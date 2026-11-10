"""
Phase 3：雙重解釋（RQ3）

對 walk-forward 測試期每一天的 RNR 預測：
- 鏡頭 1｜SHAP（KernelExplainer）：24 個特徵的貢獻，依構面加總 → 哪個構面把預測推高或拉低
- 鏡頭 2｜Historical Analogue：半徑內的相似歷史日、相似來源（最接近的兩個構面）、之後的實際走勢
- 一致性：SHAP 判斷的方向（RNR 預測類別）vs 相似日之後的多數走勢 → 方向一致率

報告兩種一致率：
  (1) 預測類別 vs 相似日多數走勢（簡報 RQ3 的定義）
  (2) SHAP 主導構面方向 vs 相似日多數走勢（只計升溫／降溫）
RNR 預測是鄰居 ΔT 的加權平均，極端值互相抵銷時會落在「持平」，因此 (1) 可能偏低，兩者需一起解讀。

用法：python phase3_explain.py [--scheme A] [--limit 0]
輸出：outputs/phase3_explanations_{scheme}.csv、phase3_report_{scheme}.md
"""
from __future__ import annotations

import argparse
import json
import warnings

import numpy as np
import pandas as pd
import shap

from config import (
    CLASSES, DIMENSION_LABELS, DIMENSIONS, OUTPUT_DIR, REFIT_EVERY,
    SHAP_BACKGROUND, SHAP_NSAMPLES, TOP_ANALOGUES, TRAIN_RATIO,
)
from common import df_to_markdown, ensure_output_dir
from features import RNRWithFallback, dimension_of, feature_columns, tertile_thresholds, to_class
from phase2_forecast import load_dataset

warnings.filterwarnings("ignore")


def similarity_sources(x_today: np.ndarray, x_neighbor: np.ndarray, scaler) -> list[str]:
    """標準化後，各構面 6 個特徵的平均差距最小的兩個構面。"""
    diff = np.abs(scaler.transform(x_today.reshape(1, -1)) - scaler.transform(x_neighbor.reshape(1, -1)))[0]
    cols = feature_columns()
    by_dim = {d: diff[[j for j, c in enumerate(cols) if dimension_of(c) == d]].mean() for d in DIMENSIONS}
    return [DIMENSION_LABELS[d] for d in sorted(by_dim, key=by_dim.get)[:2]]


def summary_text(pred_class: str, dim_shap: dict[str, float], n_nb: int, nb_classes: list[str]) -> str:
    top = sorted(dim_shap.items(), key=lambda kv: abs(kv[1]), reverse=True)[:2]
    reasons = "、".join(f"{DIMENSION_LABELS[d]} {v:+.1f}" for d, v in top)
    if n_nb:
        same = sum(c == pred_class for c in nb_classes)
        analog = f"過去 {n_nb} 個相似情境中，{same} 次之後{pred_class}"
    else:
        analog = "歷史相似情境不足，可信度較低"
    return f"預測明天{pred_class}。主要原因：{reasons}；{analog}。"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scheme", default="A")
    parser.add_argument("--limit", type=int, default=0, help="只解釋最後 N 天（0 = 全部測試期）")
    args = parser.parse_args()
    ensure_output_dir()

    df = load_dataset(args.scheme)
    cols = feature_columns()
    X = df[cols].to_numpy()
    y = df["dT"].to_numpy()
    dates = df.index
    n = len(df)
    start = int(n * TRAIN_RATIO)
    test_idx = list(range(start, n))
    if args.limit:
        test_idx = test_idx[-args.limit:]

    rows = []
    model = explainer = None
    fit_at = None
    for i in test_idx:
        if model is None or (i - start) % REFIT_EVERY == 0 or fit_at is None:
            model = RNRWithFallback().fit(X[:i], y[:i])
            q1, q2 = tertile_thresholds(pd.Series(y[:i]))
            background = shap.kmeans(X[:i], min(SHAP_BACKGROUND, i))
            explainer = shap.KernelExplainer(model.predict, background)
            fit_at = i

        pred = float(model.predict(X[i:i + 1])[0])
        pred_class = to_class([pred], q1, q2)[0]
        sv = explainer.shap_values(X[i:i + 1], nsamples=SHAP_NSAMPLES, silent=True)[0]
        dim_shap = {d: float(sum(v for c, v in zip(cols, sv) if dimension_of(c) == d)) for d in DIMENSIONS}
        lead_dim = max(dim_shap, key=lambda d: abs(dim_shap[d]))
        lead_dir = "升溫" if dim_shap[lead_dim] > 0 else "降溫"

        nb_idx, nb_dist = model.neighbors(X[i])
        nb_classes = list(to_class(y[nb_idx], q1, q2))
        majority = pd.Series(nb_classes).mode().iloc[0] if len(nb_classes) else None
        analogues = [
            {
                "date": str(dates[j].date()),
                "distance": round(float(dd), 3),
                "相似來源": similarity_sources(X[i], X[j], model.scaler_),
                "之後走勢": c,
            }
            for j, dd, c in list(zip(nb_idx, nb_dist, nb_classes))[:TOP_ANALOGUES]
        ]
        actual_class = to_class([y[i]], q1, q2)[0]
        rows.append({
            "date": dates[i].date(),
            "pred_dT": pred,
            "pred_class": pred_class,
            "actual_class": actual_class,
            **{f"shap_{d}": dim_shap[d] for d in DIMENSIONS},
            "shap_base": float(explainer.expected_value),
            "lead_dimension": DIMENSION_LABELS[lead_dim],
            "lead_direction": lead_dir,
            "neighbor_count": len(nb_idx),
            "neighbor_majority": majority,
            "agree_pred_vs_analogue": (majority == pred_class) if majority else np.nan,
            "agree_lead_vs_analogue": (majority == lead_dir) if majority in ("升溫", "降溫") else np.nan,
            "analogues": json.dumps(analogues, ensure_ascii=False),
            "summary": summary_text(pred_class, dim_shap, len(nb_idx), nb_classes),
        })
        print(f"{dates[i].date()} {rows[-1]['summary']}")

    out = pd.DataFrame(rows).set_index("date")
    out.to_csv(OUTPUT_DIR / f"phase3_explanations_{args.scheme}.csv", float_format="%.4f")

    has_nb = out["neighbor_count"] > 0
    agree1 = out["agree_pred_vs_analogue"].dropna()
    agree2 = out["agree_lead_vs_analogue"].dropna()
    lead_counts = out["lead_dimension"].value_counts().to_frame("主導次數")
    lead_counts.index.name = "構面"
    mean_abs = out[[f"shap_{d}" for d in DIMENSIONS]].abs().mean().rename(
        index={f"shap_{d}": DIMENSION_LABELS[d] for d in DIMENSIONS}).to_frame("平均 |SHAP|")
    mean_abs.index.name = "構面"
    examples = out[["pred_class", "actual_class", "summary"]].tail(5)
    examples.index.name = "日期"

    lines = [
        f"# Phase 3 報告：雙重解釋（RQ3，方案 {args.scheme}）",
        "",
        f"- 解釋天數：{len(out)}；有相似歷史日（半徑內有鄰居）的比例：{has_nb.mean():.1%}",
        f"- **方向一致率（預測類別 vs 相似日多數走勢）**：{agree1.mean():.1%}（n={len(agree1)}）",
        f"- 輔助指標（SHAP 主導構面方向 vs 相似日多數走勢，僅計升溫／降溫）：{agree2.mean():.1%}（n={len(agree2)}）",
        "",
        "> RNR 的預測是鄰居 ΔT 的加權平均；鄰居走勢分歧時，正負互相抵銷會使預測落在「持平」，而鄰居多數可能是升溫或降溫，因此第一個一致率可能偏低，兩個指標需一起解讀。",
        "",
        "## 各構面的整體影響力",
        "",
        df_to_markdown(mean_abs),
        "",
        df_to_markdown(lead_counts, ".0f"),
        "",
        "## 最近 5 天的解釋範例",
        "",
        df_to_markdown(examples),
        "",
    ]
    (OUTPUT_DIR / f"phase3_report_{args.scheme}.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"方向一致率 {agree1.mean():.1%}；輔助指標 {agree2.mean():.1%} → phase3_report_{args.scheme}.md")


if __name__ == "__main__":
    main()
