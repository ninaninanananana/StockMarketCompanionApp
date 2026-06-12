請先跟我討論
請先閱讀 SPEC.md

需求： 首頁｜市場總覽
請使用 FinMind 套件
請塞入 資料表 market_snapshot
大盤與市場整體情绪
包含數據：大盤總成交量、上漲/下跌家數、漲停/跌停家數、三大法人。

請問有哪裡需要討論嗎

---

## 討論事項

### 1. FinMind API Token
FinMind 免費版每日限制約 600 requests。
請問是否已申請 token？若沒有，需先至 FinMind 官網申請，否則資料抓取會受限。
答： 已申請,token 為eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiYjQxMDIwMzAzOUBnbWFpbC5jb20iLCJlbWFpbCI6ImI0MTAyMDMwMzlAZ21haWwuY29tIiwidG9rZW5fdmVyc2lvbiI6MH0.XPLpTsNSaUE9wmTB6j_rifRoEx6l1h8BBYpdlUBQrC8
### 2. market_snapshot 的時間維度
SPEC 上顯示「更新時間 09:03」，這代表快照是盤中即時還是每日收盤後一次？

答：- **盤中多次更新**：同一天多筆

### 3. 三大法人的對應欄位
FinMind 三大法人資料（`TaiwanStockInstitutionalInvestorsBuySell`）是「個股」維度的，
要彙總成全市場合計（如：外資買超 +328 億），需要做加總。
請確認這樣的設計是你要的？
答：對 請幫我做加總
### 4. 觸發方式
沿用 stock_importer 模式（獨立 script + API endpoint 觸發），
還是要做定時排程（cron job 每天收盤後自動跑）？

答：沿用 stock_importer 模式（獨立 script + API endpoint 觸發）