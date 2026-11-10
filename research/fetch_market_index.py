"""
補抓外部市場變數 → market_index_daily（Phase 1 溫度計驗證用）

- 加權指數：FinMind TaiwanStockPrice（data_id=TAIEX），可回補全部期間
- 台指 VIX：期交所每月檔 log2data/YYYYMMnew.txt
    ⚠️ 期交所只免費開放「最近約 3 個月」；更早的歷史需向期交所申請。
    取不到的月份 tw_vix 留 NULL、source_status = PARTIAL，不會覆寫既有值。

用法：
    python fetch_market_index.py [start_date] [end_date]
    python fetch_market_index.py 2023-06-01
"""
from __future__ import annotations

import os
import sys
from datetime import date

import pandas as pd
import requests
from dotenv import load_dotenv
from sqlalchemy import text

from config import BACKEND_DIR
from common import engine

load_dotenv(BACKEND_DIR / ".env")
FINMIND_API = "https://api.finmindtrade.com/api/v4/data"
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "")
TAIFEX_VIX_URL = "https://www.taifex.com.tw/file/taifex/Dailydownload/vix/log2data/{ym}new.txt"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_taiex(start: str, end: str) -> pd.DataFrame:
    resp = requests.get(
        FINMIND_API,
        params={"dataset": "TaiwanStockPrice", "data_id": "TAIEX",
                "start_date": start, "end_date": end, "token": FINMIND_TOKEN},
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json().get("data", [])
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["taiex_close", "taiex_change_pct"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df["taiex_close"] = df["close"].astype(float)
    prev_close = df["close"].astype(float) - df["spread"].astype(float)
    df["taiex_change_pct"] = (df["spread"].astype(float) / prev_close * 100).round(4)
    return df[["taiex_close", "taiex_change_pct"]]


def fetch_vix_month(ym: str) -> dict[pd.Timestamp, float]:
    """回傳 {日期: 收盤 VIX}；該月檔案不存在（超過 3 個月）時回傳空 dict。"""
    resp = requests.get(TAIFEX_VIX_URL.format(ym=ym), headers=HEADERS, timeout=20)
    content = resp.content.decode("big5", errors="replace")
    if resp.status_code != 200 or "<title>404" in content:
        return {}
    result = {}
    for line in content.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit() and len(parts[0]) == 8:
            try:
                result[pd.Timestamp(parts[0])] = float(parts[2])
            except ValueError:
                continue
    return result


def fetch_vix(start: str, end: str) -> pd.Series:
    months = pd.period_range(start, end, freq="M")
    values: dict[pd.Timestamp, float] = {}
    missing = []
    for p in months:
        got = fetch_vix_month(p.strftime("%Y%m"))
        if got:
            values.update(got)
        else:
            missing.append(p.strftime("%Y-%m"))
    if missing:
        print(f"[VIX] 期交所未開放的月份（需另外申請）：{missing[0]} ～ {missing[-1]}，共 {len(missing)} 個月")
    return pd.Series(values, name="tw_vix", dtype=float)


def upsert(df: pd.DataFrame) -> int:
    sql = text("""
        INSERT INTO market_index_daily (trade_date, taiex_close, taiex_change_pct, tw_vix, source_status)
        VALUES (:d, :close, :chg, :vix, :status)
        ON DUPLICATE KEY UPDATE
          taiex_close = COALESCE(VALUES(taiex_close), taiex_close),
          taiex_change_pct = COALESCE(VALUES(taiex_change_pct), taiex_change_pct),
          tw_vix = COALESCE(VALUES(tw_vix), tw_vix),
          source_status = IF(COALESCE(VALUES(tw_vix), tw_vix) IS NULL, 'PARTIAL', 'SUCCESS')
    """)
    rows = [
        {
            "d": idx.date(),
            "close": None if pd.isna(r.taiex_close) else float(r.taiex_close),
            "chg": None if pd.isna(r.taiex_change_pct) else float(r.taiex_change_pct),
            "vix": None if pd.isna(r.tw_vix) else float(r.tw_vix),
            "status": "PARTIAL" if pd.isna(r.tw_vix) else "SUCCESS",
        }
        for idx, r in df.iterrows()
    ]
    with engine.begin() as conn:
        for row in rows:
            conn.execute(sql, row)
    return len(rows)


def main(start: str = "2023-06-01", end: str | None = None) -> None:
    end = end or date.today().isoformat()
    taiex = fetch_taiex(start, end)
    vix = fetch_vix(start, end)
    df = taiex.join(vix, how="outer")
    if "tw_vix" not in df:
        df["tw_vix"] = float("nan")
    n = upsert(df)
    print(f"寫入 market_index_daily {n} 筆；加權指數 {taiex.shape[0]} 天，VIX {int(df['tw_vix'].notna().sum())} 天")


if __name__ == "__main__":
    args = sys.argv[1:]
    main(*(args[:2]))
