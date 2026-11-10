# Market Lens 技術規格書（Technical Spec）

> 版本：依 2026-11-09 `develop` 分支程式碼（含未 commit 異動）整理
> 產品規格請見 [SPEC.md](SPEC.md)；設計討論紀錄見 [discuessMlSnapshot.md](discuessMlSnapshot.md)、[backend/架構優化.md](backend/架構優化.md)
>
> 產品定位：**幫使用者理解市場，而不是替使用者做決定**（所有分數與文字均標示「非投資建議」）

---

## 1. 系統總覽

### 1.1 架構

```
┌──────────────────── 資料來源 ────────────────────┐
│ FinMind API │ TWSE MI_INDEX │ TWSE TWTB4U │ yfinance │ twstock │
└──────┬──────────────┬─────────────┬──────────┬────────┬────┘
       │              │             │          │        │
       ▼              ▼             ▼          ▼        ▼
 ┌──────────────────────────────────────────────────────────┐
 │ Backend (Python / FastAPI)                               │
 │  ├ stock_importer.py           → stock_master            │
 │  ├ market_snapshot_importer.py → market_snapshot (+評分) │
 │  ├ market_topic_importer.py    → market_topic_snapshot   │
 │  ├ data_preprocessor.py        （ML 前處理，尚未接入）    │
 │  ├ backfill_*.py               （歷史資料回補）           │
 │  ├ scheduler.py ← run_stock.sh ← crontab                 │
 │  └ main.py  (REST API, :8000)                            │
 └──────────────────────────┬───────────────────────────────┘
                            │ MySQL 協定（TiDB，port 4000）
                            ▼
                     ┌─────────────┐
                     │  Database   │
                     └─────────────┘
                            ▲
                            │ HTTP (CORS: localhost:4200)
 ┌──────────────────────────┴───────────────────────────────┐
 │ Frontend (Angular 20 + SSR, :4200)                       │
 │  └ Home 頁：市場溫度 / 漲跌家數 / 三大法人 / 熱門主題     │
 └──────────────────────────────────────────────────────────┘
```

### 1.2 資料處理主流程（[skill.md](skill.md)）

```
資料收集 → 資料清洗 → 特徵生成 → 分析（規則）→ AI(XAI) → 儲存結果 → 前端
```

目前完成到「分析（規則）」與「儲存結果」；「AI(XAI)」為規劃中（見 §8）。

### 1.3 技術棧

| 層級 | 技術 |
|------|------|
| Backend | Python、FastAPI 0.111、Uvicorn 0.29、SQLAlchemy 2.0、PyMySQL |
| 資料處理 | pandas、numpy、scikit-learn |
| 外部資料 | requests（FinMind / TWSE）、yfinance、twstock 1.4.6 |
| Database | MySQL 相容（預設 port 4000，推測為 TiDB Cloud），SSL 不驗證憑證 |
| Frontend | Angular 20.3（standalone component、signals、zoneless）、@angular/ssr（Prerender）、SCSS |
| 排程 | macOS crontab → `run_stock.sh` → `scheduler.py` |

### 1.4 環境變數（`backend/.env`）

| 變數 | 說明 |
|------|------|
| `DB_HOST` / `DB_PORT`（預設 4000）/ `DB_USER` / `DB_PASSWORD` / `DB_NAME` | 資料庫連線 |
| `FINMIND_TOKEN` | FinMind API token（免費帳號限制 600 次/小時） |
| `ADMIN_API_KEY` | POST 管理端點的 API key（header `X-API-Key`）；未設定時 POST 端點一律回 503 |

---

## 2. 資料庫 Schema

### 2.1 `stock_master`：台股基本資料

| 欄位 | 型別 | 說明 |
|------|------|------|
| `stock_id` | VARCHAR(10) PK | 股票代號 |
| `stock_name` | VARCHAR(50) | 中文名稱 |
| `industry` | VARCHAR(50) NULL | 產業別（twstock `group`） |
| `market` | VARCHAR(20) | `sii`（上市）/ `otc`（上櫃） |
| `is_active` | TINYINT | 1 = 啟用 |
| `created_at` | DATETIME | 建立時間 |

只匯入 `type == "股票"`，排除 ETF、權證、TDR；以 `ON DUPLICATE KEY UPDATE` upsert。

### 2.2 `market_snapshot`：每日市場快照（**每日一筆**）

| 欄位 | 型別 | 構面 | 來源 / 說明 |
|------|------|------|-------------|
| `id` | BIGINT PK | | DB AUTO_INCREMENT（TiDB 會分段配發，id 不連續屬正常） |
| `snapshot_time` | DATETIME UNIQUE | | 寫入時間；以 `DATE(snapshot_time)` 判斷同日 |
| `market_score` | FLOAT | 結果 | 市場溫度 0–100（§4.1） |
| `trend` | VARCHAR(100) | 結果 | 白話市場描述，給投資新手看 |
| `up_count` / `down_count` | INT | 市場廣度 | TWSE MI_INDEX「股票」欄 |
| `limit_up` / `limit_down` | INT | 市場廣度 | 同上，括號內數字 |
| `industry_diffusion` | FLOAT | 市場廣度 | 產業擴散度 0–1，唯一定義見 §3.3（yfinance） |
| `foreign_buy` | BIGINT（元） | 法人動向 | FinMind，外資 + 外資自營商 |
| `investment_buy` | BIGINT（元） | 法人動向 | FinMind，投信 |
| `dealer_buy` | BIGINT（元） | 法人動向 | FinMind，自營商自行 + 避險 |
| `total_volume` | BIGINT | 成交量 | FinMind，當日最後一筆累計成交量 |
| `speculation_index` | FLOAT（%） | 投機活躍度 | TWSE TWTB4U 當沖成交股數占比 |
| `retail_confidence` | FLOAT（億元） | 散戶槓桿 | 融資餘額今日 − 昨日 |
| `margin_balance` | BIGINT（元） | 散戶槓桿 | 融資餘額 |
| `short_sell_balance` | BIGINT（股） | 散戶槓桿 | 融券餘額 |
| `analysis_version` | VARCHAR(20) | | 寫入時為 `v2`（舊資料仍為 `v1`） |
| `source_status` | VARCHAR(20) | | `SUCCESS` = 所有欄位皆取得；`PARTIAL` = 有欄位缺值 |
| `created_at` / `updated_at` | DATETIME | | |

**缺值約定**：DB 欄位為 NOT NULL，取不到的欄位存 0。以下欄位在交易日幾乎不可能剛好為 0，
所以讀取時 **0 一律視為缺值**：`total_volume`、三大法人、`speculation_index`、`retail_confidence`、`margin_balance`、`short_sell_balance`
（定義在 `market_snapshot_importer.ZERO_MEANS_MISSING`，`data_preprocessor` 也使用同一份清單）。

> DB 遷移（擴充欄位）：
> ```sql
> ALTER TABLE market_snapshot
>   ADD COLUMN margin_balance      BIGINT NOT NULL DEFAULT 0,
>   ADD COLUMN short_sell_balance  BIGINT NOT NULL DEFAULT 0,
>   ADD COLUMN industry_diffusion  FLOAT  NOT NULL DEFAULT 0.0;
> ```

### 2.3 `market_topic_snapshot`：熱門主題（**每次執行新增一批**）

| 欄位 | 型別 | 說明 |
|------|------|------|
| `id` | BIGINT PK | AUTO_INCREMENT；同批次逐筆 flush，依平均漲幅由大到小遞增（id 越小排名越前） |
| `snapshot_time` | DATETIME | 該批次執行時間（台北時間，naive） |
| `topic_name` | VARCHAR(50) | 主題名稱（目前 = 產業別） |
| `trend` | VARCHAR(20) | 預留，目前寫空字串 |
| `topic_score` | FLOAT NULL | 預留，目前為 NULL |
| `stock_list` | JSON | 漲幅前 3 名 `[{"id": "2330", "name": "台積電"}]` |
| `reason` | JSON NULL | 預留，目前為 NULL |
| `created_at` | DATETIME | |

---

## 3. 資料收集（Importers）

### 3.1 外部資料來源

| 來源 | Dataset / Endpoint | 取得欄位 | 更新時點 |
|------|-------------------|----------|----------|
| FinMind | `TaiwanStockStatisticsOfOrderBookAndTrade` | `total_volume` | 盤中約 13 秒一筆 |
| FinMind | `TaiwanStockTotalInstitutionalInvestors` | 三大法人 | 收盤後 |
| FinMind | `TaiwanStockTotalMarginPurchaseShortSale` | 融資融券 | 盤後，17:30 時尚未更新（已由 log 確認），由 21:30 補齊 |
| TWSE | `exchangeReport/MI_INDEX?type=MS` | 漲跌家數、漲跌停 | 收盤後 |
| TWSE | `exchangeReport/TWTB4U` | 當沖比重 | 盤後，17:30 時尚未更新，由 21:30 補齊 |
| yfinance | `yf.download(period="10d")`，`.TW` / `.TWO` | 個股收盤價、成交量 | 盤中有延遲報價 |
| twstock | `twstock.codes` | 股票清單、產業 | 套件內建 |

### 3.2 `market_snapshot_importer.fetch_and_import(db, target_date=None, industry_diffusion=None)`

1. 抓 TWSE MI_INDEX 的漲跌家數、漲跌停。**非交易日判斷**：`up_count == 0 and down_count == 0` → 回傳 `{"skipped": true}`，不寫入
2. 抓 FinMind 成交量、三大法人、融資融券，以及 TWSE TWTB4U 當沖比。取不到或失敗的欄位回傳 `None`，不會中斷流程
3. **與既有資料合併**：本次為 `None` 的欄位沿用 DB 同日既有值，不會被 0 覆寫。`industry_diffusion` 由呼叫端傳入，沒傳入時沿用既有值
4. 判定 `source_status`（SUCCESS / PARTIAL），計算 `market_score`、`trend`（§4.1），寫入 `analysis_version = "v2"`
5. 同日已有資料就 update，否則 insert（id 由 DB 產生）

同一天可以重複執行：17:30 首次寫入，21:30 補齊盤後資料並重算分數。回傳結果含 `missing_fields`。
日期一律以台北時間（`taipei_now()`）計算，不受主機時區影響。

CLI：`python market_snapshot_importer.py [YYYY-MM-DD]`

### 3.3 `market_topic_importer.fetch_and_import(db, force=False)`

1. 非交易時段（週一至五 09:00–13:40 以外；13:30 收盤後保留 10 分鐘緩衝，讓 13:30 的排程能執行）且 `force=False` 時略過
2. 讀取 `stock_master` 中 `is_active=1` 的股票（約 1,900 支）後先 commit 釋放 DB 連線，再每批 100 支用 yfinance 下載近 10 日資料，批次間隔 0.5 秒（下載需數分鐘，避免閒置連線被 DB 切斷）
3. 計算個股的漲幅、今日張數、5 日均量、成交金額
4. **產業擴散度**（全系統唯一定義）：個股平均漲幅 > 0 的產業數 / 全產業數，沒有資料時為 `None`。結果放在回傳值 `industry_diffusion`，由 scheduler 傳給 snapshot importer，本身不寫入 `market_snapshot`
5. **熱門股篩選**：漲幅 ≥ 3%，且符合以下任一條件
   - 條件 A：成交量 > 2,000 張，或 > 5 日均量 × 2
   - 條件 B：成交金額 > 1 億元
6. **主題成立**：同產業 ≥ 3 支熱門股；依平均漲幅排序，每主題取漲幅前 3 名寫入

CLI：`python market_topic_importer.py [--force]`

### 3.4 `stock_importer.fetch_and_import(db)`

由 twstock 匯入上市、上櫃一般股票到 `stock_master`（手動透過 API 觸發）。

### 3.5 歷史回補腳本

| 腳本 | 用途 | 用法 |
|------|------|------|
| `backfill_market_snapshot.py` | 回補 `market_snapshot`。法人、融資以月為單位批次預取；成交量、TWSE 資料逐日抓。考量 FinMind 速率限制，每日間隔睡眠 | `python backfill_market_snapshot.py 2025-06-01 2026-06-11` |
| `backfill_industry_diffusion.py` | 用 yfinance 歷史價回補 `industry_diffusion` | `python backfill_industry_diffusion.py 2026-06-12 2026-09-21` |

---

## 4. 分析邏輯

### 4.1 市場溫度 v2：四構面加性可分解模型

實作位置：`market_snapshot_importer._calculate_market_analysis()`

$$Score_t = \text{clamp}\big(50 + 50 \times \sum_k w_k D_{k,t},\ 0,\ 100\big),\quad D_{k,t} \in [-1, 1],\ \sum w_k = 1$$

| 構面 k | 權重 w | D 的計算方式 |
|--------|--------|--------------|
| 法人動向 | 0.40 | 外資、投信、自營三者的百分位排名平均（值為 0 的不計） |
| 市場廣度 | 0.25 | 平均：①（漲−跌）/（漲+跌）②（漲停−跌停）百分位 ③ 2 × 擴散度 − 1（擴散度缺值時不計入） |
| 投機活躍度 | 0.15 | 當沖比的百分位（方向偏多） |
| 散戶槓桿 | 0.20 | 融資餘額、融券餘額、融資增減三者的百分位**取負值**後平均（槓桿越高越偏空） |

- **缺值處理**：參數為 `None` 的信號不計入，構面內其餘信號照常平均；整個構面都缺值時為 0（中性）
- **百分位排名** `_pct_rank`：與最近 120 個有效交易日（`up_count > 0` 且 `foreign_buy != 0`）比較，歷史值會排除 0（缺值），映射到 [-1, 1]；歷史少於 5 筆時回傳 0（中性）
- **情緒標籤**：≥70 偏多、57–70 中性偏多、43–57 中性、30–43 中性偏空、<30 偏空
- **`trend` 文字**：`市場{標籤}。` 加上最多 3 條信號描述（法人、廣度、槓桿警告、投機），超過 100 字會截斷

> 舊版 v1 為固定加減分的 heuristic，閾值表記錄在 [架構優化.md §3](backend/架構優化.md)，作為 Baseline A 對照。

### 4.2 ML 前處理（`data_preprocessor.py`，待 XAI 模型建立後接入）

Pipeline：`load_raw_data → handle_missing → handle_outliers → normalize → get_ml_ready_df`

| 步驟 | 規則 |
|------|------|
| 缺值 | ① 漲跌家數皆為 0（非交易日）→ 整列標為 NaN ② `ZERO_MEANS_MISSING` 欄位值為 0 → 該欄標為 NaN（逐欄判斷，PARTIAL 列的其他欄位保留） |
| 異常值 | 對大數值欄位做 IQR（預設）或 3σ 的 winsorize |
| 正規化 | 法人、融資類用 StandardScaler；`total_volume` 先 log1p 再 StandardScaler；家數類用 MinMaxScaler；`speculation_index`、`industry_diffusion` 不處理 |

正規化結果只存在記憶體中，不寫回 DB。

---

## 5. 排程

```cron
30 9-13,17,21 * * 1-5  <backend>/run_stock.sh >> <backend>/log.txt 2>&1
```

`run_stock.sh` 會切換到 backend 目錄，並以專案 venv 的 Python（`backend/venv/bin/python`）執行 `scheduler.py`。scheduler 的每個步驟各自 try/except，一步失敗不會影響下一步。

| 時間（台北） | 動作 |
|--------------|------|
| 09:30–13:30 每小時 | `market_topic_importer`（盤中熱門主題） |
| **17:30** | ① `market_topic_importer(force=True)`：以收盤價結算主題並計算擴散度 ② `market_snapshot_importer(industry_diffusion=…)`：寫入當日快照與評分 |
| **21:30** | `market_snapshot_importer`：補齊盤後才更新的融資融券、當沖比，並重算分數 |

執行紀錄寫入 `backend/log.txt`。DB 連線開啟 `pool_pre_ping` 和 `pool_recycle=300`，避免使用到已被 TiDB Serverless 切斷的閒置連線。

---

## 6. REST API（`backend/main.py`）

Base URL：`http://localhost:8000`，CORS 只允許 `http://localhost:4200`。POST 端點需帶 header `X-API-Key: <ADMIN_API_KEY>`：未設定 key 時回 503，key 錯誤回 401。

| Method | Path | 參數 | 回應 |
|--------|------|------|------|
| GET | `/` | | 健康檢查 |
| POST | `/stocks/import` | | 匯入 `stock_master` |
| POST | `/market/snapshot` | | 執行 snapshot importer |
| GET | `/market/snapshot/latest` | | 最新一筆快照；沒有資料時回 404 |
| GET | `/market/snapshot` | `target_date=YYYY-MM-DD`（預設台北時間今日） | 該日快照物件；沒有資料時回 `[]`；日期格式錯誤回 400 |
| GET | `/market/topics/latest` | | 熱門主題只由排程更新（無手動觸發端點）。回傳最新一批（同一 `snapshot_time`）的全部主題，依排名（id）排序；沒有資料時回 404 |

**MarketSnapshot 回應欄位**：`id, snapshot_time, market_score, trend, up_count, down_count, limit_up, limit_down, foreign_buy, investment_buy, dealer_buy, total_volume, margin_balance, short_sell_balance, industry_diffusion, speculation_index, retail_confidence, source_status, analysis_version, created_at, updated_at`

**MarketTopicSnapshot 回應欄位**：`id, snapshot_time, topic_name, trend, topic_score, stock_list, reason, created_at`

---

## 7. 前端（`frontend/my-app`）

- Angular 20 standalone + signals + zoneless change detection；SSR 採 Prerender，資料只在瀏覽器端（`isPlatformBrowser`）載入
- 路由：`''`、`/home` → `Home` 元件
- API 位址設定在 `src/environments/environment.ts`（production）和 `environment.development.ts`（`ng serve`，透過 angular.json `fileReplacements` 替換）
- `MarketSnapshotService`：`getByDate()`、`getLatest()`、`getLatestTopics()`

**Home 頁區塊**

| 區塊 | 資料 | 狀態處理 |
|------|------|----------|
| 市場溫度卡 | 以 `getLatest()` 取最新一筆；標題顯示快照日期（「市場溫度（M/d 收盤）」）、`market_score` / 100（圓環，周長 213.6）、`trend`、漲跌家數、漲跌停 | 載入中 / 錯誤 / 無資料（404 視為無資料） |
| 三大法人 | `foreign_buy` 等，換算為「±x.x 億」，正值紅、負值綠 | 同上 |
| 熱門主題 | `topic_name`、時間、`stock_list` 股名標籤；可手動重新整理 | 同上 |

---

## 8. 產品規格對照（SPEC.md vs. 實作）

| SPEC 功能 | 狀態 | 說明 |
|-----------|------|------|
| 首頁｜市場溫度（0–100 + 標籤） | ✅ 已實作 | v2 規則模型 |
| 首頁｜短 / 中 / 長期（5 / 20 / 120 日）觀察 | ❌ 未實作 | |
| 首頁｜今日驅動因素（影響 ±分數 + 查看證據） | 🟡 部分 | 目前只有 `trend` 文字；規劃改由 SHAP 貢獻度產生 |
| 首頁｜熱門主題 + 強勢股 | ✅ 已實作 | 主題 = 產業別；`trend`/`topic_score`/`reason` 仍未使用 |
| 首頁｜市場快照（法人、漲跌停、漲跌家數） | ✅ 已實作 | |
| 首頁｜重新分析 | 🟡 部分 | 只有熱門主題可以重新整理 |
| 個股搜尋 | ❌ 未實作 | `stock_master` 已有資料，可直接支援 |
| 個股分析頁（評分、可信度、預估區間、權重） | ❌ 未實作 | |
| 證據頁 / 資料來源 | ❌ 未實作 | |
| 歷史驗證頁（命中率、誤差、樣本數） | ❌ 未實作 | 需先定義 ground truth（§9） |
| 問題回報 | ❌ 未實作 | |

---

## 9. 規劃中：XAI（可解釋 AI）

依 [discuessMlSnapshot.md](discuessMlSnapshot.md) 的討論結論：

| 項目 | 決議 |
|------|------|
| XAI 定義 | Explainable AI：XGBoost / LightGBM + SHAP，計算每個特徵對單次預測的貢獻 |
| 輸出 | `market_score`（0–100）與 `trend`（白話中文，給新手看）；SHAP 貢獻度可對應 SPEC 的「影響 +32」 |
| 標籤（Ground Truth） | 隔日大盤漲跌幅（T+1 報酬），或未來 3 日大盤最大震盪幅度 |
| 冷啟動 | 資料量不足時先用 rule-based（目前的 v2）頂替 |
| 模型生命週期 | 訓練一次後存檔（.pkl / .joblib），平時只做 inference，另外排程定期 re-train |
| 執行位置 | inference + SHAP 不到 1 秒，直接整合進 `market_snapshot_importer.py` |
| Claude API 轉譯 | 本階段不使用 |
| 分析資料範圍 | 以 `market_snapshot` 欄位為主 |

**評估設計**（[架構優化.md](backend/架構優化.md)）：

- Baseline A（Heuristic）：v1 固定加減分
- Baseline B（Equal-weight）：構面內標準化後等權重
- Proposed（Constrained Calibrated）：`w_k ≥ 0, Σw_k = 1`，加上單調性 / 稀疏性限制的校準模型

**待補資料指標**：週轉率、異常成交量比例（需股本與 5 日均量）、融資利用率（需在 `stock_master` 補流通股本）

---

## 10. 已知問題與待辦

| # | 類別 | 問題 | 位置 |
|---|------|------|------|
| 1 | ML | `data_preprocessor.py` 尚未接入流程，需等 XAI 模型（§9）建立後由訓練與推論流程呼叫 | `data_preprocessor.py` |
| 2 | 前端 | `ng build`（production）失敗：`home.scss` 為 8.39 kB，超過 `anyComponentStyle` 8 kB 上限 | `home.scss`、`angular.json` budgets |
| 3 | 資料 | 舊資料（2026-11-09 前）的 `analysis_version` 都是 `v1`，融資融券、當沖比都是 0（17:30 時尚未更新）；可用 `market_snapshot_importer.py <日期>` 逐日補齊 | `market_snapshot` |

---

## 11. 研究模組（`research/`）

碩論實驗程式，依研究計畫簡報與 [discussRnnForecast.md](discussRnnForecast.md) 決議實作，**與 App 分開**：只讀取 `market_snapshot`，唯一寫入的是外部變數表 `market_index_daily`，其餘結果存成 `research/outputs/` 檔案。詳見 [research/README.md](research/README.md)。

| Phase | 程式 | 內容 |
|-------|------|------|
| 資料 | `fetch_market_index.py` | 加權指數（FinMind `TaiwanStockPrice` TAIEX）、台指 VIX（期交所，僅近 3 個月免費） |
| 0 | `dimensions.py` | point-in-time 四大構面（只比較當日以前 120 個交易日） |
| 1 | `phase1_temperature.py` | 權重方案 A／B／C、敏感度分析、construct validity、已知事件 |
| 2 | `phase2_forecast.py` | 24 個狀態特徵、ΔT 三分類、RNR vs Naive／ElasticNet／KNN／RF／GradientBoosting，walk-forward |
| 3 | `phase3_explain.py` | SHAP 構面貢獻＋Historical Analogue、方向一致率 |

研究版與 App v2 的差異：百分位不偷看未來、當沖比方向 −、融券方向 ＋、成交量納入投機活躍度（−）、三種權重方案。App 首頁維持 v2，待研究選出最佳方案後再更新（Q11）。

新增套件（研究用）：`shap`。外部資料表 `market_index_daily`（trade_date PK、taiex_close、taiex_change_pct、tw_vix、source_status）。
