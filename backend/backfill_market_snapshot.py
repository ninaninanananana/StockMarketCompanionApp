"""
backfill_market_snapshot.py

回補 market_snapshot 歷史資料。

策略：
  - FinMind 三大法人、融資融券 → 月為單位批次下載，存入記憶體快取
  - FinMind 成交量            → 逐日查詢（tick 資料量大，不適合批次）
  - TWSE MI_INDEX             → 逐日查詢（漲跌家數 + 産業擴散度）
  - TWSE TWTB4U               → 逐日查詢（當沖投機度）

速率限制：FinMind 免費帳號 600 次/小時（≈10 次/分鐘）
每日睡眠 7 秒，確保不觸碰上限。

用法：
    python backfill_market_snapshot.py [start_date] [end_date]
    python backfill_market_snapshot.py 2025-06-01 2026-06-11
"""

from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import requests
from sqlalchemy import func

from database import SessionLocal, Base, engine
from models import MarketSnapshot
from market_snapshot_importer import (
    _fetch_twse_openapi_data,
    _fetch_speculation_index,
    _calculate_market_analysis,
    ANALYSIS_VERSION,
    STATUS_PARTIAL,
    STATUS_SUCCESS,
    FINMIND_TOKEN,
    FINMIND_API,
)

SLEEP_PER_DAY   = 7    # 秒，避免觸碰 FinMind 速率上限
SLEEP_PER_BATCH = 2    # 秒，月批次之間的間隔


# ── 工具 ─────────────────────────────────────────────────────────────────────

def _get(dataset: str, params: dict) -> list:
    resp = requests.get(
        FINMIND_API,
        params={"dataset": dataset, "token": FINMIND_TOKEN, **params},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def _date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _month_ranges(start: date, end: date) -> list[tuple[str, str]]:
    """回傳 [(month_start, month_end), ...] 覆蓋 [start, end]。"""
    ranges = []
    current = start.replace(day=1)
    while current <= end:
        next_month = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_end = next_month - timedelta(days=1)
        ranges.append((
            max(current, start).isoformat(),
            min(month_end, end).isoformat(),
        ))
        current = next_month
    return ranges


# ── Phase 1：FinMind 批次下載 ─────────────────────────────────────────────────

def prefetch_institutional(start: str, end: str) -> dict[str, tuple[int, int, int]]:
    """
    回傳 {date_str: (foreign_buy, investment_buy, dealer_buy)}
    """
    print(f"[法人] 批次下載 {start} → {end}")
    result: dict[str, tuple[int, int, int]] = {}

    for ms, me in _month_ranges(date.fromisoformat(start), date.fromisoformat(end)):
        try:
            rows = _get("TaiwanStockTotalInstitutionalInvestors", {
                "start_date": ms, "end_date": me,
            })
            by_date: dict[str, dict[str, dict]] = {}
            for row in rows:
                d = row["date"]
                by_date.setdefault(d, {})[row["name"]] = row

            for d, names in by_date.items():
                foreign = investment = dealer = 0
                for name, row in names.items():
                    net = int(row.get("buy", 0)) - int(row.get("sell", 0))
                    if name in ("Foreign_Investor", "Foreign_Dealer_Self"):
                        foreign += net
                    elif name == "Investment_Trust":
                        investment = net
                    elif name in ("Dealer_self", "Dealer_Hedging"):
                        dealer += net
                result[d] = (foreign, investment, dealer)

            print(f"  {ms} ~ {me}：{len(by_date)} 個交易日")
        except Exception as e:
            print(f"  [warn] {ms}~{me} 失敗: {e}")
        time.sleep(SLEEP_PER_BATCH)

    return result


def prefetch_margin(start: str, end: str) -> dict[str, tuple[float, int, int]]:
    """
    回傳 {date_str: (retail_confidence, margin_balance, short_sell_balance)}
    """
    print(f"\n[融資融券] 批次下載 {start} → {end}")
    result: dict[str, tuple[float, int, int]] = {}

    for ms, me in _month_ranges(date.fromisoformat(start), date.fromisoformat(end)):
        try:
            rows = _get("TaiwanStockTotalMarginPurchaseShortSale", {
                "start_date": ms, "end_date": me,
            })
            by_date: dict[str, dict[str, dict]] = {}
            for row in rows:
                d = row["date"]
                by_date.setdefault(d, {})[row["name"]] = row

            for d, names in by_date.items():
                margin_balance = short_sell_balance = 0
                retail_confidence = 0.0

                if "MarginPurchaseMoney" in names:
                    r = names["MarginPurchaseMoney"]
                    today_bal = int(r.get("TodayBalance", 0))
                    yes_bal   = int(r.get("YesBalance",   0))
                    margin_balance    = today_bal
                    retail_confidence = round((today_bal - yes_bal) / 1e8, 2)

                if "ShortSale" in names:
                    short_sell_balance = int(names["ShortSale"].get("TodayBalance", 0))

                result[d] = (retail_confidence, margin_balance, short_sell_balance)

            print(f"  {ms} ~ {me}：{len(by_date)} 個交易日")
        except Exception as e:
            print(f"  [warn] {ms}~{me} 失敗: {e}")
        time.sleep(SLEEP_PER_BATCH)

    return result


def _fetch_volume(date_str: str) -> Optional[int]:
    """取當日最後一筆累計成交量。"""
    try:
        rows = _get("TaiwanStockStatisticsOfOrderBookAndTrade", {
            "start_date": date_str, "end_date": date_str,
        })
        if rows:
            return int(rows[-1].get("TotalDealVolume", 0)) or None
    except Exception as e:
        print(f"  [warn] 成交量 {date_str} 失敗: {e}")
    return None


# ── Phase 2：逐日寫入 ─────────────────────────────────────────────────────────

def main(start_date: str = "2025-06-01", end_date: str = "2026-06-11") -> None:
    Base.metadata.create_all(bind=engine)

    start_d = date.fromisoformat(start_date)
    end_d   = date.fromisoformat(end_date)

    print(f"回補區間：{start_date} → {end_date}\n")

    # ── 批次預取 ────────────────────────────────────────────────────────────
    institutional = prefetch_institutional(start_date, end_date)
    margin_data   = prefetch_margin(start_date, end_date)
    print(f"\n法人：{len(institutional)} 天，融資融券：{len(margin_data)} 天")

    total_days     = sum(1 for _ in _date_range(start_d, end_d))
    trading_days   = len([d for d in _date_range(start_d, end_d)
                          if d.isoformat() in institutional])
    estimated_secs = trading_days * SLEEP_PER_DAY
    print(f"預計交易日：{trading_days}，估計耗時 {estimated_secs//60} 分鐘\n")
    print("=" * 60)

    # ── 逐日處理 ────────────────────────────────────────────────────────────
    inserted = skipped = errors = 0

    for d in _date_range(start_d, end_d):
        day_str   = d.isoformat()
        twse_date = d.strftime("%Y%m%d")

        # 先取 TWSE 漲跌家數（非交易日直接跳過，不呼叫 FinMind）
        up_count, down_count, limit_up, limit_down = _fetch_twse_openapi_data(twse_date)

        if up_count == 0 and down_count == 0:
            skipped += 1
            time.sleep(1)
            continue

        db = SessionLocal()
        try:
            # FinMind 逐日資料
            total_volume      = _fetch_volume(day_str)
            speculation_index = _fetch_speculation_index(day_str)

            # 從快取取（取不到為 None = 缺值）
            foreign_buy, investment_buy, dealer_buy = \
                institutional.get(day_str, (None, None, None))
            retail_confidence, margin_balance, short_sell_balance = (
                v or None for v in margin_data.get(day_str, (None, None, None))
            )

            # 同一天的既有資料（産業擴散度由 backfill_industry_diffusion.py 回補，沿用既有值）
            existing = db.query(MarketSnapshot).filter(
                func.date(MarketSnapshot.snapshot_time) == d,
            ).first()
            industry_diffusion = existing.industry_diffusion if existing else None

            fields = {
                "total_volume": total_volume,
                "foreign_buy": foreign_buy,
                "investment_buy": investment_buy,
                "dealer_buy": dealer_buy,
                "speculation_index": speculation_index,
                "retail_confidence": retail_confidence,
                "margin_balance": margin_balance,
                "short_sell_balance": short_sell_balance,
                "industry_diffusion": industry_diffusion,
            }
            source_status = (
                STATUS_PARTIAL if any(v is None for v in fields.values()) else STATUS_SUCCESS
            )

            # v2 評分
            market_score, trend = _calculate_market_analysis(
                db,
                foreign_buy, investment_buy, dealer_buy,
                up_count, down_count, limit_up, limit_down,
                speculation_index, retail_confidence, margin_balance,
                short_sell_balance, industry_diffusion,
            )

            row = {
                "snapshot_time": datetime.strptime(day_str, "%Y-%m-%d"),
                "up_count": up_count,
                "down_count": down_count,
                "limit_up": limit_up,
                "limit_down": limit_down,
                "market_score": market_score,
                "trend": trend,
                "source_status": source_status,
                "analysis_version": ANALYSIS_VERSION,
                **{f: (v if v is not None else 0) for f, v in fields.items()},
            }

            # Upsert（同一天只保留一筆；id 交由 DB AUTO_INCREMENT）
            if existing:
                for field, v in row.items():
                    setattr(existing, field, v)
            else:
                db.add(MarketSnapshot(**row))

            db.commit()
            inserted += 1
            print(f"{day_str}  score={market_score:5.1f}  {trend[:35]}")

        except Exception as e:
            print(f"{day_str}  ERROR: {e}")
            errors += 1
        finally:
            db.close()

        time.sleep(SLEEP_PER_DAY)

    print("\n" + "=" * 60)
    print(f"完成。寫入 {inserted} 筆，跳過 {skipped} 個非交易日，錯誤 {errors} 筆")


if __name__ == "__main__":
    args = sys.argv[1:]
    s = args[0] if len(args) > 0 else "2025-06-01"
    e = args[1] if len(args) > 1 else "2026-06-11"
    main(s, e)
