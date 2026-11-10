"""
Phase 0：point-in-time 四大構面計算

與 App 的 v2 公式差異：
1. 百分位只與「當日以前」最近 120 個交易日比較（App 回補時會比到未來資料）
2. 指標方向依研究決議：當沖比 −、成交量 −（新增）、融券 +
3. DB 中的 0 一律視為缺值（含產業擴散度）

輸出：outputs/dimensions.csv（每日各指標分數與四構面分數 D_k ∈ [-1, 1]）
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import (
    DIMENSION_SIGNALS, DIMENSIONS, OUTPUT_DIR, PCT_LOOKBACK, PCT_MIN_HISTORY,
    RAW_START, ZERO_MEANS_MISSING,
)
from common import ensure_output_dir, load_snapshots


def pct_rank_point_in_time(series: pd.Series,
                           lookback: int = PCT_LOOKBACK,
                           min_history: int = PCT_MIN_HISTORY) -> pd.Series:
    """每一天只跟「前」lookback 個交易日（不含當日）比較，回傳 2×rank−1 ∈ [-1, 1]。"""
    values = series.to_numpy(dtype=float)
    out = np.full(len(values), np.nan)
    for i, v in enumerate(values):
        if np.isnan(v):
            continue
        hist = values[max(0, i - lookback):i]
        hist = hist[~np.isnan(hist)]
        if len(hist) < min_history:
            continue
        out[i] = 2 * (hist <= v).mean() - 1
    return pd.Series(out, index=series.index)


def prepare_raw(snap: pd.DataFrame) -> pd.DataFrame:
    raw = snap.copy()
    for col in ZERO_MEANS_MISSING:
        raw.loc[raw[col] == 0, col] = np.nan
    total = raw["up_count"] + raw["down_count"]
    raw["breadth_ratio"] = (raw["up_count"] - raw["down_count"]) / total.replace(0, np.nan)
    raw["limit_net"] = raw["limit_up"] - raw["limit_down"]
    return raw


def build_dimensions(snap: pd.DataFrame) -> pd.DataFrame:
    raw = prepare_raw(snap)
    out = pd.DataFrame(index=raw.index)
    for dim, signals in DIMENSION_SIGNALS.items():
        cols = []
        for col, direction, kind in signals:
            if kind == "pct":
                score = pct_rank_point_in_time(raw[col])
            elif kind == "raw":
                score = raw[col].clip(-1, 1)
            elif kind == "diffusion":
                score = 2 * raw[col] - 1
            else:
                raise ValueError(kind)
            name = f"s_{col}"
            out[name] = direction * score
            cols.append(name)
        out[f"D_{dim}"] = out[cols].mean(axis=1, skipna=True)
    out["app_market_score"] = snap["market_score"]
    return out


def load_dimensions(start: str = RAW_START) -> pd.DataFrame:
    return build_dimensions(load_snapshots(start))


def main() -> None:
    ensure_output_dir()
    dims = load_dimensions()
    path = OUTPUT_DIR / "dimensions.csv"
    dims.to_csv(path, float_format="%.6f")
    d_cols = [f"D_{d}" for d in DIMENSIONS]
    complete = dims[d_cols].notna().all(axis=1)
    print(f"交易日 {len(dims)} 天（{dims.index.min().date()} ～ {dims.index.max().date()}），"
          f"四構面齊全 {int(complete.sum())} 天 → {path}")
    print("各構面缺值天數：", dims[d_cols].isna().sum().to_dict())


if __name__ == "__main__":
    main()
