"""
data_preprocessor.py

將 MarketSnapshot 原始資料轉換為 ML 可用的乾淨 DataFrame。

Pipeline：
    load_raw_data → handle_missing → handle_outliers → normalize → get_ml_ready_df

正規化策略（依 架構優化.md 確認）：
    - foreign_buy, investment_buy, dealer_buy, retail_confidence → StandardScaler
    - total_volume → log1p + StandardScaler
    - up_count, down_count, limit_up, limit_down → MinMaxScaler
    - speculation_index → 不處理（本已在合理 % 範圍）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler, StandardScaler
from sqlalchemy.orm import Session

from models import MarketSnapshot

# ── 欄位分組 ──────────────────────────────────────────────────────────────────

# 元單位的法人買賣超 + 融資相關大數值 → StandardScaler
_STANDARD_COLS = [
    "foreign_buy",
    "investment_buy",
    "dealer_buy",
    "retail_confidence",
    "margin_balance",       # 融資餘額絕對值（元）
    "short_sell_balance",   # 融券餘額（股數）
]

# 成交量：量級過大，先 log1p 壓縮再 StandardScaler
_LOG_STANDARD_COLS = ["total_volume"]

# 家數類：有自然上界，MinMaxScaler
_MINMAX_COLS = ["up_count", "down_count", "limit_up", "limit_down"]

# 比率類：已在合理範圍，不處理
_PASSTHROUGH_COLS = [
    "speculation_index",   # 當沖比重（%）
    "industry_diffusion",  # 産業擴散度（0–1）
]

# 所有特徵欄位
FEATURE_COLS: list[str] = (
    _STANDARD_COLS + _LOG_STANDARD_COLS + _MINMAX_COLS + _PASSTHROUGH_COLS
)

# 「0 代表無資料」的欄位（與 market_snapshot_importer.ZERO_MEANS_MISSING 一致）：
# DB 欄位 NOT NULL，importer 取不到資料時存 0，這些欄位在真實交易日幾乎不可能剛好為 0。
_ZERO_MEANS_MISSING_COLS = [
    "total_volume",
    "foreign_buy",
    "investment_buy",
    "dealer_buy",
    "speculation_index",
    "retail_confidence",
    "margin_balance",
    "short_sell_balance",
]


# ── Step 1: 載入 ──────────────────────────────────────────────────────────────

def load_raw_data(
    db: Session,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """
    從 DB 撈 MarketSnapshot，回傳原始 DataFrame。

    Parameters
    ----------
    db         : SQLAlchemy Session
    start_date : 'YYYY-MM-DD'，None 表示不限
    end_date   : 'YYYY-MM-DD'，None 表示不限

    Returns
    -------
    DataFrame，欄位包含 snapshot_time、source_status 及所有 FEATURE_COLS。
    """
    query = db.query(MarketSnapshot)

    if start_date:
        query = query.filter(MarketSnapshot.snapshot_time >= start_date)
    if end_date:
        query = query.filter(MarketSnapshot.snapshot_time <= end_date)

    rows = query.order_by(MarketSnapshot.snapshot_time).all()

    if not rows:
        return pd.DataFrame(columns=["snapshot_time", "source_status"] + FEATURE_COLS)

    records = [
        {
            "snapshot_time": r.snapshot_time,
            "source_status": r.source_status,
            "foreign_buy": r.foreign_buy,
            "investment_buy": r.investment_buy,
            "dealer_buy": r.dealer_buy,
            "total_volume": r.total_volume,
            "up_count": r.up_count,
            "down_count": r.down_count,
            "limit_up": r.limit_up,
            "limit_down": r.limit_down,
            "speculation_index": r.speculation_index,
            "retail_confidence": r.retail_confidence,
            "margin_balance": r.margin_balance,
            "short_sell_balance": r.short_sell_balance,
            "industry_diffusion": r.industry_diffusion,
        }
        for r in rows
    ]
    return pd.DataFrame(records)


# ── Step 2: 缺值處理 ──────────────────────────────────────────────────────────

def handle_missing(df: pd.DataFrame) -> pd.DataFrame:
    """
    將「應為 NaN」的 0 值標記為 NaN，並移除 source_status 欄位。

    規則：
      1. 漲跌家數皆為 0 → 非交易日，整列特徵設為 NaN
      2. _ZERO_MEANS_MISSING_COLS 中值為 0 的欄位 → 該欄設為 NaN
         （importer 對缺值欄位存 0，並將 source_status 標為 PARTIAL；
           逐欄判斷可保留同一列中其他有效欄位）
    """
    df = df.copy()

    # 規則 1：非交易日
    non_trading = (df["up_count"] == 0) & (df["down_count"] == 0)
    df.loc[non_trading, FEATURE_COLS] = np.nan

    # 規則 2：逐欄缺值
    for col in _ZERO_MEANS_MISSING_COLS:
        df.loc[df[col] == 0, col] = np.nan

    return df.drop(columns=["source_status"])


# ── Step 3: 異常值處理 ────────────────────────────────────────────────────────

def handle_outliers(df: pd.DataFrame, method: str = "iqr") -> pd.DataFrame:
    """
    對連續型大數值欄位做異常值偵測並 winsorize（截斷至上下界）。

    只處理 _STANDARD_COLS 和 _LOG_STANDARD_COLS，
    家數類（_MINMAX_COLS）不處理，避免壓縮其自然上界。

    Parameters
    ----------
    method : 'iqr'   → Q1/Q3 ± 1.5 × IQR（預設，對偏態分布較穩健）
             '3sigma' → μ ± 3σ（假設常態分布）
    """
    df = df.copy()
    target_cols = _STANDARD_COLS + _LOG_STANDARD_COLS

    for col in target_cols:
        series = df[col].dropna()
        if len(series) < 4:
            continue

        if method == "iqr":
            q1, q3 = series.quantile(0.25), series.quantile(0.75)
            spread = q3 - q1
            lo, hi = q1 - 1.5 * spread, q3 + 1.5 * spread
        else:
            mu, sigma = series.mean(), series.std()
            lo, hi = mu - 3 * sigma, mu + 3 * sigma

        df[col] = df[col].clip(lower=lo, upper=hi)

    return df


# ── Step 4: 正規化 ────────────────────────────────────────────────────────────

def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """
    依欄位類型套用不同正規化策略，只對有效（非 NaN）的列做 fit_transform。

    - _STANDARD_COLS    → StandardScaler
    - _LOG_STANDARD_COLS → log1p（下界截 0）+ StandardScaler
    - _MINMAX_COLS      → MinMaxScaler
    - _PASSTHROUGH_COLS → 不處理
    """
    df = df.copy()

    def _apply_scaler(scaler, col: str) -> None:
        valid = df[col].notna()
        if valid.sum() < 2:
            return
        df.loc[valid, col] = scaler.fit_transform(df.loc[valid, [col]])

    for col in _STANDARD_COLS:
        _apply_scaler(StandardScaler(), col)

    for col in _LOG_STANDARD_COLS:
        valid = df[col].notna()
        if valid.sum() < 2:
            continue
        df.loc[valid, col] = np.log1p(df.loc[valid, col].clip(lower=0))
        _apply_scaler(StandardScaler(), col)

    for col in _MINMAX_COLS:
        _apply_scaler(MinMaxScaler(), col)

    return df


# ── 完整 Pipeline ─────────────────────────────────────────────────────────────

def get_ml_ready_df(
    db: Session,
    start_date: str | None = None,
    end_date: str | None = None,
    outlier_method: str = "iqr",
) -> pd.DataFrame:
    """
    完整 pipeline：load → handle_missing → handle_outliers → normalize。

    Returns
    -------
    DataFrame，以 snapshot_time 為 index，欄位為 FEATURE_COLS。
    NaN 表示該日無有效資料，由下游 ML 模型決定如何處理。

    Parameters
    ----------
    db             : SQLAlchemy Session
    start_date     : 'YYYY-MM-DD'，None 表示不限
    end_date       : 'YYYY-MM-DD'，None 表示不限
    outlier_method : 'iqr'（預設）或 '3sigma'
    """
    df = load_raw_data(db, start_date, end_date)
    df = handle_missing(df)
    df = handle_outliers(df, method=outlier_method)
    df = normalize(df)
    return df.set_index("snapshot_time")
