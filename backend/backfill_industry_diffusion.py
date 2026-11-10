"""
backfill_industry_diffusion.py

使用 yfinance 批次下載歷史股價，計算每個交易日的産業擴散度，
並回補至 market_snapshot.industry_diffusion。

用法：
    python backfill_industry_diffusion.py [start_date] [end_date]
    python backfill_industry_diffusion.py 2026-06-12 2026-09-21
"""

from __future__ import annotations

import sys
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")
sys.path.insert(0, str(Path(__file__).parent))

import warnings
warnings.filterwarnings("ignore")

import yfinance as yf
import pandas as pd
from sqlalchemy import func, text
from database import SessionLocal
from models import MarketSnapshot

BATCH_SIZE = 100   # yfinance 每批下載股票數


# ── yfinance 批次下載 ─────────────────────────────────────────────────────────

def _download_batch(
    tickers: list[str],
    start: str,
    end: str,
) -> pd.DataFrame | None:
    """
    下載一批股票在 [start, end) 區間的日線資料。
    end 為 yfinance 的排除端點，需多傳一天。
    """
    try:
        df = yf.download(
            tickers,
            start=start,
            end=end,
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        return df if not df.empty else None
    except Exception as e:
        print(f"  [warn] 批次下載失敗: {e}")
        return None


def _extract_daily_changes(
    df: pd.DataFrame,
    tickers: list[str],
) -> dict[str, dict[str, float]]:
    """
    從 DataFrame 算出每支股票每個日期的漲跌幅。

    Returns
    -------
    {ticker: {date_str: pct_change}}
    """
    results: dict[str, dict[str, float]] = {}
    is_multi = isinstance(df.columns, pd.MultiIndex)

    for ticker in tickers:
        try:
            if is_multi:
                if ticker not in df.columns.get_level_values(1):
                    continue
                closes = df["Close"][ticker].dropna()
            else:
                closes = df["Close"].dropna()

            if len(closes) < 2:
                continue

            pct = closes.pct_change() * 100   # Series，index 為 Timestamp
            changes = {
                ts.strftime("%Y-%m-%d"): round(float(v), 2)
                for ts, v in pct.items()
                if pd.notna(v)
            }
            results[ticker] = changes
        except Exception:
            continue

    return results


# ── 産業擴散度計算 ────────────────────────────────────────────────────────────

def compute_diffusion_by_date(
    change_by_ticker: dict[str, dict[str, float]],
    industry_by_ticker: dict[str, str],
    target_dates: list[str],
) -> dict[str, float]:
    """
    各交易日的産業擴散度 = 平均漲幅 > 0 的産業數 / 全部産業數。

    Returns
    -------
    {date_str: diffusion (0–1)}
    """
    diffusion_by_date: dict[str, float] = {}

    for day_str in target_dates:
        industry_changes: dict[str, list[float]] = defaultdict(list)

        for ticker, changes in change_by_ticker.items():
            pct = changes.get(day_str)
            if pct is None:
                continue
            industry = industry_by_ticker.get(ticker)
            if not industry:
                continue
            industry_changes[industry].append(pct)

        if not industry_changes:
            continue

        up_count = sum(
            1 for chgs in industry_changes.values()
            if sum(chgs) / len(chgs) > 0
        )
        diffusion_by_date[day_str] = round(up_count / len(industry_changes), 3)

    return diffusion_by_date


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main(start_date: str = "2026-06-12", end_date: str = None) -> None:
    if end_date is None:
        end_date = date.today().isoformat()

    # yfinance end 是排除端點，多加一天
    yf_end = (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()

    print(f"回補區間：{start_date} → {end_date}")

    db = SessionLocal()
    try:
        # 1. 載入有效股票清單（含産業）
        rows = db.execute(text(
            "SELECT stock_id, industry, market FROM stock_master "
            "WHERE is_active = 1 AND industry IS NOT NULL"
        )).fetchall()
        # 釋放連線：後續 yfinance 下載需數分鐘，閒置連線會被 DB 端切斷
        db.commit()

        industry_by_ticker: dict[str, str] = {}
        all_tickers: list[str] = []
        for r in rows:
            stock_id, industry, market = r[0], r[1], r[2]
            suffix = ".TW" if market == "sii" else ".TWO"
            ticker = f"{stock_id}{suffix}"
            industry_by_ticker[ticker] = industry
            all_tickers.append(ticker)

        total = len(all_tickers)
        print(f"股票數：{total} 支，分 {(total // BATCH_SIZE) + 1} 批下載\n")

        # 2. 分批下載歷史股價
        change_by_ticker: dict[str, dict[str, float]] = {}

        for i in range(0, total, BATCH_SIZE):
            batch = all_tickers[i : i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            print(f"  批次 {batch_num}（{i+1}–{min(i+BATCH_SIZE, total)}）下載中...", end=" ", flush=True)

            df = _download_batch(batch, start=start_date, end=yf_end)
            if df is not None:
                changes = _extract_daily_changes(df, batch)
                change_by_ticker.update(changes)
                print(f"OK（{len(changes)} 支有效）")
            else:
                print("失敗，跳過")

            time.sleep(0.5)

        print(f"\n下載完成：共 {len(change_by_ticker)} 支有效資料")

        # 3. 彙整所有有資料的交易日
        all_dates = sorted({
            d for changes in change_by_ticker.values()
            for d in changes
            if start_date <= d <= end_date
        })
        print(f"交易日數：{len(all_dates)} 天\n")

        # 4. 計算各日擴散度
        diffusion_by_date = compute_diffusion_by_date(
            change_by_ticker, industry_by_ticker, all_dates
        )

        # 5. 寫回 DB
        print("回寫 DB：")
        updated = skipped = 0

        for day_str, diffusion in sorted(diffusion_by_date.items()):
            snapshot = (
                db.query(MarketSnapshot)
                .filter(func.date(MarketSnapshot.snapshot_time) == day_str)
                .first()
            )
            if snapshot:
                snapshot.industry_diffusion = diffusion
                updated += 1
                print(f"  {day_str}  diffusion={diffusion:.3f}")
            else:
                skipped += 1
                print(f"  {day_str}  無 market_snapshot，跳過")

        db.commit()
        print(f"\n完成。更新 {updated} 筆，跳過 {skipped} 筆")

    finally:
        db.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    s = args[0] if len(args) > 0 else "2026-06-12"
    e = args[1] if len(args) > 1 else None
    main(s, e)
