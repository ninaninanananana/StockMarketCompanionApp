"""
Phase 1：校準市場溫度計（RQ1）

1. 三種權重方案：A（實務經驗）、B（等權）、C（訓練期資料校準）
2. 敏感度分析：單一權重 ±10%、±20%，其餘依比例調整
3. 溫度計驗證：
   - Construct validity：與當日報酬、未來 5 日報酬、台指 VIX、20 日實現波動度的相關
   - 已知市場事件：2024-08-05、2025-04-07
   - 穩定性：敏感度分析的標籤改變比例

輸出：
  outputs/phase1_temperature.csv  每日四構面與三種方案的溫度
  outputs/phase1_weights.json     三種方案的權重（含校準結果）
  outputs/phase1_report.md        驗證報告
"""
from __future__ import annotations

import itertools
import json

import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr

from config import (
    CALIBRATION_STEP, DIMENSION_LABELS, DIMENSIONS, KNOWN_EVENTS, OUTPUT_DIR,
    REALIZED_VOL_WINDOW, SAMPLE_START, SCHEMES, SENSITIVITY_DELTAS,
    STABLE_LABEL_CHANGE, TRAIN_RATIO, label_of,
)
from common import df_to_markdown, ensure_output_dir, load_market_index
from dimensions import load_dimensions

D_COLS = [f"D_{d}" for d in DIMENSIONS]


def temperature(dims: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    w = np.array([weights[d] for d in DIMENSIONS])
    return 50 + 50 * dims[D_COLS].to_numpy() @ w


def simplex_grid(step: float):
    n = round(1 / step)
    for a, b, c in itertools.product(range(n + 1), repeat=3):
        d = n - a - b - c
        if d >= 0:
            yield dict(zip(DIMENSIONS, np.array([a, b, c, d]) / n))


def calibrate(train: pd.DataFrame) -> tuple[dict[str, float], float]:
    """方案 C：在訓練期找與『當日大盤漲跌幅』相關係數最大的權重（Q3）。"""
    best_w, best_r = None, -np.inf
    ret = train["taiex_change_pct"].to_numpy()
    for w in simplex_grid(CALIBRATION_STEP):
        r = np.corrcoef(temperature(train, w), ret)[0, 1]
        if r > best_r:
            best_w, best_r = w, r
    return {k: round(float(v), 2) for k, v in best_w.items()}, float(best_r)


def perturb(weights: dict[str, float], dim: str, delta: float) -> dict[str, float]:
    """調整單一構面權重 ×(1+delta)，其餘依原比例分配，維持加總 = 1。"""
    new = dict(weights)
    new[dim] = weights[dim] * (1 + delta)
    rest = 1 - weights[dim]
    for k in weights:
        if k != dim:
            new[k] = weights[k] * (1 - new[dim]) / rest if rest > 0 else (1 - new[dim]) / 3
    return new


def sensitivity(df: pd.DataFrame, schemes: dict[str, dict[str, float]]) -> pd.DataFrame:
    rows = []
    for name, w in schemes.items():
        base = pd.Series(temperature(df, w), index=df.index)
        base_label = base.map(label_of)
        for dim in DIMENSIONS:
            for delta in SENSITIVITY_DELTAS:
                t = pd.Series(temperature(df, perturb(w, dim, delta)), index=df.index)
                rows.append({
                    "方案": name, "構面": DIMENSION_LABELS[dim], "調整": f"{delta:+.0%}",
                    "spearman": spearmanr(base, t).statistic,
                    "標籤改變比例": float((t.map(label_of) != base_label).mean()),
                })
    return pd.DataFrame(rows)


def corr(x: pd.Series, y: pd.Series) -> tuple[float, int]:
    m = x.notna() & y.notna()
    if m.sum() < 10:
        return float("nan"), int(m.sum())
    return float(pearsonr(x[m], y[m]).statistic), int(m.sum())


def validity_table(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    targets = {
        "當日報酬": df["taiex_change_pct"],
        "未來5日報酬": df["fwd_5d_return"],
        "台指VIX": df["tw_vix"],
        "20日實現波動度": df["realized_vol_20"],
    }
    rows = {}
    for c in cols:
        row = {}
        for name, y in targets.items():
            r, n = corr(df[c], y)
            row[name] = r
            row[f"{name} n"] = float(n)
        rows[c] = row
    out = pd.DataFrame(rows).T
    out.index.name = "指標"
    return out


def event_table(df: pd.DataFrame, t_cols: list[str]) -> pd.DataFrame:
    rows = {}
    for day, desc in KNOWN_EVENTS.items():
        ts = pd.Timestamp(day)
        if ts not in df.index:
            rows[f"{day} {desc}"] = {c: float("nan") for c in t_cols}
            continue
        row = {}
        pos = df.index.get_loc(ts)
        for c in t_cols:
            prev20 = df[c].iloc[max(0, pos - 20):pos].mean()
            row[f"{c} 當日"] = float(df[c].iloc[pos])
            row[f"{c} 較前20日均"] = float(df[c].iloc[pos] - prev20)
            row[f"{c} 全期百分位"] = float((df[c] <= df[c].iloc[pos]).mean())
        rows[f"{day} {desc}"] = row
    out = pd.DataFrame(rows).T
    out.index.name = "事件"
    return out


def build_frame() -> pd.DataFrame:
    dims = load_dimensions()
    idx = load_market_index()
    idx["fwd_5d_return"] = (idx["taiex_close"].shift(-5) / idx["taiex_close"] - 1) * 100
    idx["realized_vol_20"] = idx["taiex_change_pct"].rolling(REALIZED_VOL_WINDOW).std() * np.sqrt(252)
    df = dims.join(idx, how="left")
    df = df[df.index >= SAMPLE_START]
    return df.dropna(subset=D_COLS + ["taiex_change_pct"])


def main() -> None:
    ensure_output_dir()
    df = build_frame()
    n = len(df)
    if n < 30:
        raise SystemExit(f"樣本只有 {n} 天，不足以分析（請先完成資料回補）")
    split = int(n * TRAIN_RATIO)
    train, test = df.iloc[:split], df.iloc[split:]

    w_c, r_c = calibrate(train)
    schemes = {**SCHEMES, "C": w_c}
    for name, w in schemes.items():
        df[f"T_{name}"] = temperature(df, w)
    t_cols = [f"T_{k}" for k in schemes]

    # ── 輸出資料 ───────────────────────────────────────────────────────────────
    out_cols = D_COLS + t_cols + ["taiex_change_pct", "tw_vix", "realized_vol_20", "app_market_score"]
    df[out_cols].to_csv(OUTPUT_DIR / "phase1_temperature.csv", float_format="%.6f")
    meta = {
        "sample": [str(df.index.min().date()), str(df.index.max().date()), n],
        "train_end": str(train.index.max().date()),
        "schemes": schemes,
        "calibration_train_corr": r_c,
    }
    (OUTPUT_DIR / "phase1_weights.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    # ── 報告 ───────────────────────────────────────────────────────────────────
    test_df = df.loc[test.index]
    sens = sensitivity(df, schemes)
    sens_summary = sens.groupby("方案").agg(
        平均spearman=("spearman", "mean"),
        最低spearman=("spearman", "min"),
        平均標籤改變=("標籤改變比例", "mean"),
        最大標籤改變=("標籤改變比例", "max"),
    )
    sens_summary["穩定"] = np.where(sens_summary["最大標籤改變"] < STABLE_LABEL_CHANGE, "是", "否")
    validity_test = validity_table(test_df, t_cols + D_COLS)
    events = event_table(df, t_cols)

    weights_df = pd.DataFrame(schemes).T[DIMENSIONS].rename(columns=DIMENSION_LABELS)
    weights_df.index.name = "方案"
    vix_days = int(df["tw_vix"].notna().sum())

    lines = [
        "# Phase 1 報告：市場溫度計校準（RQ1）",
        "",
        f"- 樣本：{df.index.min().date()} ～ {df.index.max().date()}，共 {n} 個交易日（四構面與加權指數齊全）",
        f"- 訓練期：前 {TRAIN_RATIO:.0%}（至 {train.index.max().date()}，{len(train)} 天）；測試期 {len(test)} 天",
        f"- 台指 VIX 有值天數：{vix_days}（期交所僅免費開放近 3 個月），因此另以 20 日實現波動度作為替代指標",
        "",
        "## 1. 三種權重方案",
        "",
        df_to_markdown(weights_df, ".2f"),
        "",
        f"方案 C 在訓練期與當日報酬的相關係數：{r_c:.3f}",
        "",
        "## 2. Construct validity（測試期）",
        "",
        "期望方向：溫度與當日報酬、未來報酬正相關；與台指 VIX、實現波動度負相關。",
        "",
        df_to_markdown(validity_test),
        "",
        "## 3. 已知市場事件",
        "",
        "「較前20日均」為負、「全期百分位」越低，代表溫度在事件當日明顯下降。",
        "",
        df_to_markdown(events),
        "",
        "## 4. 敏感度分析（全樣本）",
        "",
        f"判定標準：最大標籤改變比例 < {STABLE_LABEL_CHANGE:.0%} 視為穩定。",
        "",
        df_to_markdown(sens_summary),
        "",
        "### 明細",
        "",
        df_to_markdown(sens.set_index("方案")),
        "",
    ]
    (OUTPUT_DIR / "phase1_report.md").write_text("\n".join(lines), encoding="utf-8")
    sens.to_csv(OUTPUT_DIR / "phase1_sensitivity.csv", index=False, float_format="%.4f")
    print(f"樣本 {n} 天；方案 C 權重 {w_c}（訓練期 r={r_c:.3f}）")
    print(sens_summary.to_string())
    print("報告 →", OUTPUT_DIR / "phase1_report.md")


if __name__ == "__main__":
    main()
