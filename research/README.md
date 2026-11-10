# research/：碩論研究實驗程式

依《Market Lens 研究計畫 Proposal》簡報與 [discussRnnForecast.md](../discussRnnForecast.md) 的決議實作。
與 App 分開（Q9）：只**讀取** App 的 `market_snapshot`，唯一會寫入的是外部變數表 `market_index_daily`；
研究的中間結果全部存成 `outputs/` 下的檔案，不影響每天的排程與首頁。

## 流程

| 步驟 | 程式 | 內容 | 輸出 |
|------|------|------|------|
| 資料 | `backend/backfill_market_snapshot.py`、`backend/backfill_industry_diffusion.py` | 原始資料回補到 2023-07 | DB `market_snapshot` |
| 資料 | `fetch_market_index.py` | 加權指數（FinMind）、台指 VIX（期交所） | DB `market_index_daily` |
| Phase 0 | `dimensions.py` | point-in-time 四大構面 D_k ∈ [−1, 1] | `outputs/dimensions.csv` |
| Phase 1 | `phase1_temperature.py` | 三種權重（A/B/C）、敏感度、construct validity、已知事件 | `phase1_temperature.csv`、`phase1_weights.json`、`phase1_report.md` |
| Phase 2 | `phase2_forecast.py` | 24 個狀態特徵、ΔT 三分類、RNR vs 5 種對照模型、walk-forward | `phase2_predictions_*.csv`、`phase2_metrics.csv`、`phase2_report.md` |
| Phase 3 | `phase3_explain.py` | SHAP 構面貢獻＋相似歷史日、方向一致率 | `phase3_explanations_*.csv`、`phase3_report_*.md` |

一次跑完：

```bash
./run_all.sh A
```

`A` 是 Phase 3 要解釋的權重方案，看完 Phase 1 報告後再決定。

## 與 App v2 公式的差異（研究版）

| 項目 | App（v2） | 研究版 |
|------|-----------|--------|
| 百分位比較基準 | DB 最新 120 筆（回補歷史時會比到未來） | **只用當日以前** 120 個交易日 |
| 當沖比方向 | ＋ | **−**（短線躁動、風險升高） |
| 融券餘額方向 | − | **＋**（可能引發軋空） |
| 成交量 | 未納入 | **納入投機活躍度，方向 −** |
| 權重 | 只有 A | A／B／C 三種方案 |

所有參數集中在 `config.py`。

## 已知限制

- **台指 VIX**：期交所只免費開放近 3 個月（目前 2026-08 起），更早需另外申請；Phase 1 另以 20 日實現波動度作為替代指標
- **XGBoost**：需要系統層級的 OpenMP，改用 scikit-learn `HistGradientBoostingRegressor`（同為梯度提升樹）
- **產業擴散度**：以現有股票清單回補，有存活者偏差
