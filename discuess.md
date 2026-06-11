請先跟我討論

DB連線資料：
mysql://2JQEAcWdXr8KhA3.root:bTnP6GYnv5bUvVYQ@gateway01.ap-northeast-1.prod.aws.tidbcloud.com:4000/sys

寫入資料表：
stock_master

資料來源：
python 的 yf 


需求：
幫我分兩個資料夾 frontend/backend
frontend 語言：angular
backend:python
根據以上幫我寫一個API 抓取資料並放到對應的資料表 

---

## 問題清單

### 🔴 嚴重問題

1. **DB 連線資訊明文暴露**
   - 帳號密碼直接寫在文件裡，不應出現在版控或共享文件中。
   - 應改用 `.env` 環境變數管理，並將 `.env` 加入 `.gitignore`。

答： 請幫我產.env 並將 `.env` 加入 `.gitignore`
---

### 🟡 需求不完整（動工前需釐清）

2. **`stock_master` 資料表 schema 未定義**
   - 欄位名稱、資料型別、主鍵為何？
   - 例如是否包含 `symbol`、`name`、`price`、`volume`、`date` 等欄位？
    答：
    
USE test;


CREATE TABLE stock_master (
    stock_id VARCHAR(10) NOT NULL,
    stock_name VARCHAR(50) NOT NULL,
    industry VARCHAR(50) NULL,
    market VARCHAR(20) NOT NULL,
    is_active TINYINT NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT PK_stock_master PRIMARY KEY (stock_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


3. **yfinance 抓取範圍未指定**
   - 要抓哪些股票代碼（台股？美股？清單從哪來）？
   - 要抓哪些欄位？頻率（排程？即時？一次性）？歷史資料範圍？
答：台股 ,欄位依據資料表 
4. **API 規格未定義**
   - 有哪些端點？是前端觸發抓取還是後端排程？回傳格式？
答：目前先存入資料表就可
5. **Frontend 功能未說明**
   - Angular 要顯示什麼？是否有圖表、搜尋、篩選？
答：目前先存入資資料表就可
---

### 🟢 技術細節待確認

6. **Python 框架未指定**（建議 FastAPI 或 Flask）答：FastAPI
7. **是否需要 CORS 設定？部署環境為何？** 答： 目前先不用考慮

---

## 補充問題（根據回答後衍生）

8. **台股代碼清單來源未說明**
   - yfinance 本身沒有內建台股完整列表，需要知道股票代碼從哪裡取得。
   - 選項：
     - A) 手動提供 CSV 或代碼清單
     - B) 從 TWSE（證交所）或 TPEX（櫃買中心）官網爬取
     - C) 其他方式
   - 答：使用 TwStock 套件 取得代碼後 再用yfinance 撈取相對應資料

9. **`stock_name` 欄位的語言問題**
   - yfinance 回傳的 `longName` 通常是英文（例如 "Taiwan Semiconductor Manufacturing"），不是中文股票名稱。
   - 是否可以接受英文名稱？還是需要中文？
   - 答：請問 TwStock 的名稱

10. **`market` 欄位要存什麼值**
    - 台股分為 TWSE（上市）和 TPEX（上櫃），欄位要存哪種格式？
    - 例如："TWSE" / "TPEX"，或 "上市" / "上櫃"？
    - 答：存 TWSE" / "TPEX"

11. **Frontend 是否仍需要建立**
    - 目前目標是「存入資料表」，Angular frontend 是否先略過，僅建立 backend？
    - 答：frontend 部分僅建立資料夾就好  backend照上面溝通的

---

## 補充問題 2（根據回答後衍生）

12. **DB 連線字串的 database name 與建表 SQL 不一致**
    - 連線字串指定的是 `sys` 這個 database
    - 但建表 SQL 寫的是 `USE test`
    - 請問資料表要建在哪個 database？
    - 答：USE test

13. **yfinance 在 stock_master 的角色不明確**
    - `stock_master` 的欄位（stock_id、stock_name、industry、market）TwStock 本身全部能提供，不需要 yfinance。
    - yfinance 適合抓股價、成交量等行情資料，但 stock_master 沒有這類欄位。
    - 請問 yfinance 在這個需求裡具體要做什麼？
    - 答：需求是 透過yfinance.info 將台股個股資料 寫進去stock_master

14. **TwStock 的 market 值需要轉換**
    - TwStock 回傳的市場代碼是 `sii`（上市）和 `otc`（上櫃），需要程式轉換成 `TWSE` / `TPEX`。
    - 確認這樣處理是否符合預期？
    - 答：直接寫 `sii`（上市）和 `otc`（上櫃）

15. **`is_active` 判斷邏輯**
    - TwStock 包含部分已停止交易的股票，`is_active` 要如何設定？全部預設 1，還是有過濾邏輯？
    - 答：若停止交易/下市 就不用存了. 資料表先存入還能交易的股票就好

---

## 補充問題 3（最後一個矛盾）

16. **yfinance.info 與 TwStock 的欄位來源矛盾**
    - 問題 9 回答：stock_name 要用 TwStock 的中文名稱
    - 問題 13 回答：用 yfinance.info 寫入 stock_master
    - 但 yfinance.info 實際上對台股回傳的資料如下：
      - `longName` → 英文名（例如 "Taiwan Semiconductor Manufacturing Company Limited"）
      - `industry` → 英文（例如 "Semiconductors"）
      - `market` → "tw_market"（不是 sii / otc）
    - 這表示如果全用 yfinance.info，stock_name 會是英文、market 也對不上。
    - 建議做法：TwStock 提供代碼清單 + 中文名稱 + market(sii/otc)，yfinance.info 補充 industry。
    - 請確認各欄位的資料來源：
      | 欄位 | 資料來源 |
      |------|---------|
      | stock_id | TwStock 代碼 |
      | stock_name | TwStock（中文） |
      | industry | TwStock（中文產業別） |
      | market | TwStock（sii/otc）|
    - 答：都用TwStock 好了

---

## 補充問題 4

17. **yfinance 已不需要使用**
    - 原始需求說資料來源為 yfinance，但確認後所有欄位（stock_id、stock_name、industry、market）都由 TwStock 提供。
    - 這表示 yfinance 在此需求中完全不需要使用。
    - 確認移除 yfinance，僅使用 TwStock？
    - 答：對,確認移除 yfinance，僅使用 TwStock

18. **DB 連線字串需更新**
    - 原本連線字串結尾是 `/sys`，但資料表要建在 `test` database。
    - 實際連線字串應改為：
      `mysql://...@gateway01.ap-northeast-1.prod.aws.tidbcloud.com:4000/test`
    - 確認這樣修正是否正確？
    - 答：對