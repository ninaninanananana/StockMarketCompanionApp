"""研究程式共用：DB 連線與資料載入（唯讀，不寫入 App 的資料表）。"""
from __future__ import annotations

import sys

import pandas as pd
from sqlalchemy import text

from config import BACKEND_DIR, OUTPUT_DIR

sys.path.insert(0, str(BACKEND_DIR))
from database import engine  # noqa: E402

RAW_COLUMNS = [
    "foreign_buy", "investment_buy", "dealer_buy",
    "up_count", "down_count", "limit_up", "limit_down", "industry_diffusion",
    "speculation_index", "total_volume",
    "margin_balance", "retail_confidence", "short_sell_balance",
    "market_score", "source_status", "analysis_version",
]


def load_snapshots(start: str | None = None) -> pd.DataFrame:
    """market_snapshot → 以交易日為 index 的 DataFrame（同日多筆取最後一筆）。"""
    sql = f"SELECT snapshot_time, {', '.join(RAW_COLUMNS)} FROM market_snapshot"
    params = {}
    if start:
        sql += " WHERE snapshot_time >= :start"
        params["start"] = start
    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn, params=params)
    df["date"] = pd.to_datetime(df["snapshot_time"]).dt.normalize()
    df = df.sort_values("snapshot_time").drop_duplicates("date", keep="last")
    numeric = [c for c in RAW_COLUMNS if c not in ("source_status", "analysis_version")]
    df[numeric] = df[numeric].apply(pd.to_numeric, errors="coerce")
    # 非交易日（無漲跌家數）不納入
    df = df[(df["up_count"] > 0) | (df["down_count"] > 0)]
    return df.set_index("date").drop(columns=["snapshot_time"])


def load_market_index(start: str | None = None) -> pd.DataFrame:
    """market_index_daily → 加權指數收盤、漲跌幅、台指 VIX。"""
    sql = "SELECT trade_date, taiex_close, taiex_change_pct, tw_vix FROM market_index_daily"
    params = {}
    if start:
        sql += " WHERE trade_date >= :start"
        params["start"] = start
    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn, params=params)
    df["date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index("date").drop(columns=["trade_date"]).sort_index()
    return df.apply(pd.to_numeric, errors="coerce")


def ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def df_to_markdown(df: pd.DataFrame, floatfmt: str = ".3f") -> str:
    """不依賴 tabulate 的簡易 Markdown 表格。"""
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join([df.index.name or ""] + cols) + " |",
             "|" + "---|" * (len(cols) + 1)]
    for idx, row in df.iterrows():
        cells = []
        for v in row.values:
            if isinstance(v, float):
                cells.append("—" if pd.isna(v) else format(v, floatfmt))
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join([str(idx)] + cells) + " |")
    return "\n".join(lines)
