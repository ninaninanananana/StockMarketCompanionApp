"""
Phase 2：預測市場狀態變化（RQ2）

- Input：四構面 × 6 個狀態特徵 = 24 維（另比較精簡版 12 維：水準、趨勢、動能）
- Target：ΔT = T(t+1) − T(t)
    主要：升溫／持平／降溫（門檻＝訓練期 ΔT 三等分位）
    次要：T(t+1) 絕對分數 = T(t) + 預測 ΔT
- 模型：Naive、Elastic Net、KNN、Random Forest、Gradient Boosting（HistGradientBoosting，取代 XGBoost）、RNR
- 驗證：walk-forward（expanding window），每 REFIT_EVERY 天重新訓練；
        正規化、半徑、三分類門檻都只用當時的訓練資料決定
- 穩健性：三種權重方案（A／B／C）各跑一次

用法：python phase2_forecast.py [--schemes A,B,C]
輸出：outputs/phase2_predictions_{scheme}.csv、phase2_metrics.csv、phase2_report.md
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import ElasticNet
from sklearn.metrics import accuracy_score, f1_score, mean_absolute_error, mean_squared_error
from sklearn.neighbors import KNeighborsRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from config import CLASSES, DIMENSIONS, KNN_K, OUTPUT_DIR, RANDOM_STATE, REFIT_EVERY, TRAIN_RATIO
from common import df_to_markdown, ensure_output_dir
from features import NaiveZero, RNRWithFallback, build_features, feature_columns, tertile_thresholds, to_class

D_COLS = [f"D_{d}" for d in DIMENSIONS]
COMPACT = [f"{d}__{k}" for d in DIMENSIONS for k in ("level", "trend60", "momentum")]


def make_models() -> dict:
    return {
        "Naive": NaiveZero(),
        "ElasticNet": make_pipeline(StandardScaler(), ElasticNet(alpha=0.05, l1_ratio=0.5, max_iter=5000)),
        "KNN": make_pipeline(StandardScaler(), KNeighborsRegressor(n_neighbors=KNN_K, weights="distance")),
        "RandomForest": RandomForestRegressor(n_estimators=300, min_samples_leaf=5, random_state=RANDOM_STATE, n_jobs=-1),
        "GradientBoosting": HistGradientBoostingRegressor(max_depth=3, learning_rate=0.05, max_iter=200, random_state=RANDOM_STATE),
        "RNR": RNRWithFallback(),
        "RNR_精簡12維": RNRWithFallback(),
    }


def load_dataset(scheme: str) -> pd.DataFrame:
    temp = pd.read_csv(OUTPUT_DIR / "phase1_temperature.csv", index_col=0, parse_dates=True)
    feats = build_features(temp[D_COLS])
    df = feats.join(temp[[f"T_{scheme}"]].rename(columns={f"T_{scheme}": "T"}))
    df["dT"] = df["T"].shift(-1) - df["T"]
    df["T_next"] = df["T"].shift(-1)
    return df.dropna(subset=feature_columns() + ["dT"])


def walk_forward(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    X_all = df[feature_columns()].to_numpy()
    X_compact = df[COMPACT].to_numpy()
    y_all = df["dT"].to_numpy()
    n = len(df)
    start = int(n * TRAIN_RATIO)
    preds = {name: np.full(n, np.nan) for name in make_models()}
    rnr_counts = np.full(n, np.nan)
    rnr_radius = np.full(n, np.nan)
    q1s, q2s = np.full(n, np.nan), np.full(n, np.nan)
    models = None

    for i in range(start, n):
        if models is None or (i - start) % REFIT_EVERY == 0:
            models = make_models()
            for name, m in models.items():
                X_train = X_compact[:i] if name == "RNR_精簡12維" else X_all[:i]
                m.fit(X_train, y_all[:i])
            q1, q2 = tertile_thresholds(pd.Series(y_all[:i]))
        for name, m in models.items():
            x = X_compact[i:i + 1] if name == "RNR_精簡12維" else X_all[i:i + 1]
            preds[name][i] = m.predict(x)[0]
        rnr_counts[i] = models["RNR"].last_counts_[0]
        rnr_radius[i] = models["RNR"].radius_
        q1s[i], q2s[i] = q1, q2

    out = pd.DataFrame(index=df.index)
    out["T"] = df["T"]
    out["dT_actual"] = y_all
    out["q1"], out["q2"] = q1s, q2s
    out = out.iloc[start:].copy()
    out["class_actual"] = to_class(out["dT_actual"], out["q1"], out["q2"])
    for name, p in preds.items():
        out[f"pred_{name}"] = p[start:]
        out[f"class_{name}"] = to_class(out[f"pred_{name}"], out["q1"], out["q2"])
    out["rnr_neighbor_count"] = rnr_counts[start:]
    out["rnr_radius"] = rnr_radius[start:]
    info = {"n_total": n, "n_test": n - start, "test_start": str(out.index.min().date())}
    return out, info


def metrics(out: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    rows = {}
    T_next = df.loc[out.index, "T_next"]
    for name in make_models():
        p = out[f"pred_{name}"]
        rows[name] = {
            "Accuracy": accuracy_score(out["class_actual"], out[f"class_{name}"]),
            "Macro-F1": f1_score(out["class_actual"], out[f"class_{name}"], labels=CLASSES, average="macro", zero_division=0),
            "ΔT MAE": mean_absolute_error(out["dT_actual"], p),
            "ΔT RMSE": float(np.sqrt(mean_squared_error(out["dT_actual"], p))),
            "T(t+1) MAE": mean_absolute_error(T_next, out["T"] + p),
        }
    res = pd.DataFrame(rows).T
    res.index.name = "模型"
    return res


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schemes", default="A,B,C")
    args = parser.parse_args()
    ensure_output_dir()
    weights = json.loads((OUTPUT_DIR / "phase1_weights.json").read_text())

    all_metrics, sections = [], []
    for scheme in args.schemes.split(","):
        df = load_dataset(scheme)
        out, info = walk_forward(df)
        out.to_csv(OUTPUT_DIR / f"phase2_predictions_{scheme}.csv", float_format="%.6f")
        m = metrics(out, df)
        m.insert(0, "方案", scheme)
        all_metrics.append(m)
        fallback = float((out["rnr_neighbor_count"] == 0).mean())
        dist = out["class_actual"].value_counts().reindex(CLASSES).fillna(0).astype(int).to_dict()
        sections += [
            f"## 方案 {scheme}",
            "",
            f"- 樣本 {info['n_total']} 天；測試期自 {info['test_start']} 起 {info['n_test']} 天",
            f"- 測試期實際類別分布：{dist}",
            f"- RNR 半徑內找不到鄰居、改用 KNN 的比例：{fallback:.1%}；平均鄰居數：{out['rnr_neighbor_count'].mean():.1f}",
            "",
            df_to_markdown(m.drop(columns=["方案"])),
            "",
        ]
        print(f"[方案 {scheme}] 測試 {info['n_test']} 天")
        print(m.drop(columns=["方案"]).round(3).to_string())

    pd.concat(all_metrics).to_csv(OUTPUT_DIR / "phase2_metrics.csv", float_format="%.4f")
    header = [
        "# Phase 2 報告：預測市場狀態變化（RQ2）",
        "",
        f"- 權重方案：{weights['schemes']}",
        f"- Walk-forward：前 {TRAIN_RATIO:.0%} 為初始訓練期，之後每 {REFIT_EVERY} 個交易日重新訓練",
        "- 三分類門檻：每次重新訓練時，以當時訓練資料的 ΔT 三等分位決定",
        "- Gradient Boosting 使用 scikit-learn HistGradientBoosting（與 XGBoost 同為梯度提升樹）",
        "- 判讀：Naive（明天＝今天）預測 ΔT=0，三分類一律判為「持平」，Accuracy 約 1/3 是基準線",
        "",
    ]
    (OUTPUT_DIR / "phase2_report.md").write_text("\n".join(header + sections), encoding="utf-8")
    print("報告 →", OUTPUT_DIR / "phase2_report.md")


if __name__ == "__main__":
    main()
