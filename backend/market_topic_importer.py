import os
import sys
import json
import time
from datetime import datetime
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Optional

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import warnings
warnings.filterwarnings("ignore")

import yfinance as yf
import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from models import MarketTopicSnapshot
from database import SessionLocal, Base, engine

# ── 常數 ────────────────────────────────────────────
TAIPEI_TZ = ZoneInfo("Asia/Taipei")
BATCH_SIZE = 100
MIN_CHANGE_PCT = 3.0          # 漲幅門檻 %
MIN_VOLUME_ZHANG = 2000       # 條件 A：成交量 > 2,000 張
MIN_TRADING_VALUE = 100_000_000  # 條件 B：成交金額 > 1 億 NTD
MIN_INDUSTRY_COUNT = 3        # 同產業至少 N 家
TOP_N = 3                     # 每個主題取漲幅前 N 名


# ── 交易時段判斷 ──────────────────────────────────────
def is_trading_hours() -> bool:
    """週一至週五 09:00–13:30 台北時間。"""
    now = datetime.now(TAIPEI_TZ)
    if now.weekday() >= 5:
        return False
    open_t  = now.replace(hour=9,  minute=0,  second=0, microsecond=0)
    close_t = now.replace(hour=13, minute=30, second=0, microsecond=0)
    return open_t <= now <= close_t


# ── 從 DB 讀取股票清單 ────────────────────────────────
def _load_stocks(db: Session) -> list[dict]:
    rows = db.execute(
        text("""
            SELECT stock_id, stock_name, industry, market
            FROM stock_master
            WHERE is_active = 1
        """)
    ).fetchall()
    return [
        {"stock_id": r[0], "stock_name": r[1], "industry": r[2], "market": r[3]}
        for r in rows
    ]


def _build_ticker(stock_id: str, market: str) -> str:
    """sii → .TW，otc → .TWO"""
    suffix = ".TW" if market == "sii" else ".TWO"
    return f"{stock_id}{suffix}"


# ── yfinance 批次下載 ──────────────────────────────────
def _download_batch(tickers: list[str]) -> Optional[pd.DataFrame]:
    try:
        df = yf.download(
            tickers,
            period="10d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        return df if not df.empty else None
    except Exception as e:
        print(f"  [warn] 批次下載失敗: {e}")
        return None


def _extract_stats(df: pd.DataFrame, tickers: list[str]) -> dict[str, dict]:
    """從 DataFrame 計算每支股票的漲幅、成交量、成交金額。"""
    results = {}
    is_multi = isinstance(df.columns, pd.MultiIndex)

    for ticker in tickers:
        try:
            if is_multi:
                if ticker not in df.columns.get_level_values(1):
                    continue
                closes  = df["Close"][ticker].dropna()
                volumes = df["Volume"][ticker].dropna()
            else:
                # 單一 ticker 時為 flat columns
                closes  = df["Close"].dropna()
                volumes = df["Volume"].dropna()

            if len(closes) < 2 or len(volumes) < 1:
                continue

            today_close     = float(closes.iloc[-1])
            yesterday_close = float(closes.iloc[-2])
            today_volume    = float(volumes.iloc[-1])   # 單位：股

            if yesterday_close <= 0 or today_close <= 0 or today_volume <= 0:
                continue

            change_pct = (today_close - yesterday_close) / yesterday_close * 100

            # 5 日均量（排除今日）
            hist_vol   = volumes.iloc[:-1]
            avg_5d     = float(hist_vol.tail(5).mean()) if len(hist_vol) >= 1 else 0.0

            today_zhang = today_volume / 1000   # 股 → 張
            avg_5d_zhang = avg_5d / 1000
            trading_value = today_close * today_volume  # NTD

            results[ticker] = {
                "change_pct":    round(change_pct, 2),
                "volume_zhang":  round(today_zhang, 0),
                "avg_5d_zhang":  round(avg_5d_zhang, 0),
                "trading_value": round(trading_value, 0),
            }
        except Exception:
            continue

    return results


def _meets_volume_condition(stats: dict) -> bool:
    """條件 A（成交量） OR 條件 B（成交金額）。"""
    vol   = stats["volume_zhang"]
    avg5d = stats["avg_5d_zhang"]
    value = stats["trading_value"]

    cond_a = (vol > MIN_VOLUME_ZHANG) or (avg5d > 0 and vol > avg5d * 2)
    cond_b = (value > MIN_TRADING_VALUE)
    return cond_a or cond_b


# ── 主流程 ────────────────────────────────────────────
def fetch_and_import(db: Session, force: bool = False) -> dict:
    """
    掃描全市場，找出熱門主題寫入 market_topic_snapshot。
    force=True 可略過交易時段限制（測試用）。
    """
    if not force and not is_trading_hours():
        return {"skipped": True, "reason": "非交易時段"}

    now = datetime.now(TAIPEI_TZ).replace(tzinfo=None)

    # 1. 讀取股票清單
    stocks = _load_stocks(db)
    if not stocks:
        return {"skipped": True, "reason": "stock_master 無資料"}

    ticker_meta: dict[str, dict] = {
        _build_ticker(s["stock_id"], s["market"]): s
        for s in stocks
    }
    all_tickers = list(ticker_meta.keys())
    print(f"[TopicImporter] 共 {len(all_tickers)} 支，分 {len(all_tickers)//BATCH_SIZE + 1} 批下載...")

    # 2. 分批下載
    all_stats: dict[str, dict] = {}
    for i in range(0, len(all_tickers), BATCH_SIZE):
        batch = all_tickers[i : i + BATCH_SIZE]
        df = _download_batch(batch)
        if df is not None:
            all_stats.update(_extract_stats(df, batch))
        time.sleep(0.5)

    print(f"[TopicImporter] 取得 {len(all_stats)} 支股票資料")

    # 3. 篩選：漲幅 >= 3% AND (條件A OR 條件B)
    qualified: list[dict] = []
    for ticker, stats in all_stats.items():
        if stats["change_pct"] < MIN_CHANGE_PCT:
            continue
        if not _meets_volume_condition(stats):
            continue
        meta = ticker_meta[ticker]
        if not meta.get("industry"):
            continue
        qualified.append({
            "stock_id":   meta["stock_id"],
            "stock_name": meta["stock_name"],
            "industry":   meta["industry"],
            "change_pct": stats["change_pct"],
        })

    print(f"[TopicImporter] 符合條件: {len(qualified)} 支")

    # 4. 依產業分組，>= 3 家才成立主題
    groups: dict[str, list] = defaultdict(list)
    for s in qualified:
        groups[s["industry"]].append(s)

    # 依平均漲幅大到小排序後的主題列表
    hot_topics = sorted(
        [
            (ind, members)
            for ind, members in groups.items()
            if len(members) >= MIN_INDUSTRY_COUNT
        ],
        key=lambda x: sum(s["change_pct"] for s in x[1]) / len(x[1]),
        reverse=True,
    )

    print(f"[TopicImporter] 熱門主題: {len(hot_topics)} 個")

    # 5. 寫入新資料（依平均漲幅大到小順序寫入，id 即代表排名）
    next_id = (db.query(func.max(MarketTopicSnapshot.id)).scalar() or 0) + 1
    inserted = []
    for industry, members in hot_topics:
        top3 = sorted(members, key=lambda x: x["change_pct"], reverse=True)[:TOP_N]
        stock_list = [
            {"id": s["stock_id"], "name": s["stock_name"]}
            for s in top3
        ]

        db.add(MarketTopicSnapshot(
            id=next_id,
            snapshot_time=now,
            topic_name=industry,
            trend="",
            topic_score=None,
            stock_list=stock_list,
            reason=None,
        ))
        next_id += 1
        avg_chg = round(sum(s["change_pct"] for s in members) / len(members), 2)
        inserted.append({
            "topic_name":  industry,
            "stock_count": len(members),
            "avg_change":  avg_chg,
            "top3":        [f"{s['id']} {s['name']}" for s in stock_list],
        })

    db.commit()
    print(f"[TopicImporter] 寫入完成，共 {len(inserted)} 筆主題")

    return {
        "snapshot_time":    now.isoformat(),
        "stocks_fetched":   len(all_stats),
        "qualified_stocks": len(qualified),
        "hot_topics_count": len(hot_topics),
        "topics":           inserted,
    }


# ── 直接執行（測試用）────────────────────────────────
if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        force = "--force" in sys.argv
        result = fetch_and_import(db, force=force)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        import traceback
        print(f"ERROR: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()
