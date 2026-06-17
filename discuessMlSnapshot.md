請先跟我討論
請先閱讀 SPEC.md

需求： 首頁｜今日驅動因素
請根據MarketSnapshot 這張表中的欄位，使用XAI去分析 今日盤中狀況,並寫進MarketSnapshot.trend欄位跟market_score欄位

請問有什麼需要跟我討論的？

---

## Claude 的討論問題

**Q1. XAI 是指哪個服務？**
是 xAI 的 Grok API，還是 Explainable AI（可解釋人工智慧），或是其他？
如果是 Grok API，是否已有 API Key？
答：Explainable AI（可解釋人工智慧）
**Q2. `trend` 欄位目前是 `VARCHAR(20)`，只能存 20 字**
SPEC 上「今日驅動因素」有多條理由（如「↑ 外資買超 影響 +32」），遠超 20 字。請確認：
- `trend` 只存簡短標籤（例如「偏多」），驅動因素另開新欄位？
- 還是把 `trend` 改成 `TEXT` 或 `JSON` 來存完整分析結果？
答： 已將trend改為100字
**Q3. 分析是盤中跑，還是盤後跑？**
目前 `_fetch_institutional`（法人）和 `speculation_index`（當沖）都是盤後才有資料。
若做「盤中分析」，實際可用的只有 `total_volume`、`up_count`/`down_count`、`limit_up`/`limit_down`。
這樣的資料量是否足夠？
答：分析為每小時跑,`_fetch_institutional`（法人）和 `speculation_index`是昨日資料,看是否對於今日分析有幫助,若無幫助則可不理會
**Q4. SPEC 上「影響 +32」這類分數怎麼來？**
是讓 XAI 自行判斷給分，還是有預設的計算公式？
答：XAI 自行判斷給分
**Q5. MarketSnapshot 現有欄位無法涵蓋 SPEC 所有驅動因素**
SPEC 裡有「AI 類股轉強」、「匯率波動」等因素，但這些資料目前表裡都沒有。
XAI 分析時，資料來源只限 MarketSnapshot 現有欄位，
還是可以額外帶入其他資料（例如 `market_topic_snapshot`、外部 API）？
答：先以MarketSnapshot為主

---

## Claude 追加問題

**Q6. XAI 實作方式確認**
「Explainable AI」在這裡是指：
- (A) 我們自己寫加權計算公式（rule-based），每個欄位對應固定分數貢獻？
- (B) 呼叫 AI 模型（如 Claude API），讓模型根據欄位數值自行推理並給出可解釋的結論？
答：預期採用的 XAI 實作方式是透過機器學習模型結合 SHAP 套件,先訓練一個量化預測模型（如 XGBoost、LightGBM），接著使用 SHAP 套件來計算每個特徵對『該次預測結果』的邊際貢獻度（Shapley Value）。這能讓我們針對每一次的預測，精確給出類似：『因為當沖比率偏高，讓投機風險得分增加了 15 分』 的數據化、可解釋結論。如果需要，我們也可以在最後將 SHAP 計算出的特徵貢獻度，丟給 Claude API 來轉譯成更人性化、好讀的分析報告。
**Q7. `trend` 的輸出格式**
VARCHAR(100) 內存的是什麼形式？
- 純文字描述，例如：「外資買超、成交量放大、漲多跌少，市場偏多」
- 還是固定 enum 標籤，例如：「偏多 / 偏空 / 中性 / 偏熱 / 偏弱」
答：純文字描述,已投資新手也能懂的方式敘述
**Q8. `market_score` 範圍與計算**
需求已追加要寫入 `market_score`。SPEC 上顯示範圍是 0–100（例如 68/100），確認嗎？
分數是 XAI 自行給分，還是有一個公式（例如各欄位加權後正規化到 0–100）？
答：顯示範圍是 0–100,此分數功能為 讓投資人了解今日市場熱度,分數是 XAI 自行給分,根據表中的欄位 法人購買數量等等去決定

**Q9. DB Schema 遷移**
`trend` 改為 VARCHAR(100)，這個 migration 是你來跑，還是要我一起產生 Alembic migration script？
答：我已經改完DB跟MarketSnapshot.models.py 可以再看看哪裡有需要調整
**Q10. 每小時排程的觸發方式**
每小時跑分析，是要：
- 加進現有的 `market_snapshot_importer.py`（匯入完資料後接著分析）？
- 還是另開一支獨立的分析腳本，由外部 cron 觸發？
答：請評估 若直接加進現有的 是否會執行超過３分鐘 ,若不會 則直接寫入market_snapshot_importer,若會 則另開一支獨立的分析腳本，由外部 cron 觸發

---

## Claude 追加問題（第二輪）

**Q11. XGBoost 訓練資料與標籤（最關鍵）**
XGBoost / LightGBM 是監督式學習，需要 **歷史資料 + 標籤（label）** 才能訓練。
- 訓練資料：MarketSnapshot 目前有多少歷史筆數？ 答：３筆
- 標籤定義：`market_score` 的 ground truth 是什麼？例如「隔日大盤漲跌幅」、「當日成交量百分位」，還是另有定義？
答：Ground Truth 定義應該是「未來 N 日的大盤表現」。定義為：「隔日大盤漲跌幅」（T+1 報酬率）或「未來 3 日大盤最大震盪幅度」。
**Q12. 冷啟動問題**
若目前資料筆數不足以訓練模型（通常 XGBoost 至少需要數十至數百筆才穩定），
第一階段是否先用 **rule-based 加權公式** 頂替，等資料累積到一定量後再切換為 SHAP 模型？
答：是的，第一階段必須且強烈建議先採用 Rule-based（加權公式）頂替
**Q13. Claude API 轉譯是否要加入？**
Q6 提到可以用 Claude API 將 SHAP 結果轉成人性化文字寫入 `trend`。
這個要加進本次實作嗎？若要，`.env` 裡有 `ANTHROPIC_API_KEY` 嗎？

**Q14. 模型檔案的存放與更新策略**
訓練好的模型（`.pkl` / `.joblib`）要放在哪裡？
- 每次 importer 執行都重新 train（資料少時快，但每小時 re-train 效率差）？
- 還是 train 一次存檔，之後只做 inference，另外安排定期 re-train？

**Q10 時間評估（回覆）**
純 inference（XGBoost predict + SHAP）：< 1 秒。
若加 Claude API 轉譯 `trend` 文字：約 10–30 秒。
整體預估遠低於 3 分鐘，**可以直接整合進 `market_snapshot_importer.py`**。



