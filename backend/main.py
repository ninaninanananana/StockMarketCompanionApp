from fastapi import FastAPI, Depends, Query
from sqlalchemy.orm import Session
from database import get_db
from stock_importer import fetch_and_import as fetch_stocks
from market_snapshot_importer import fetch_and_import as fetch_market_snapshot
from market_topic_importer import fetch_and_import as fetch_market_topics

app = FastAPI(title="Stock Market Companion API")


@app.get("/")
def root():
    return {"message": "Stock Market Companion API is running"}


@app.post("/stocks/import")
def import_stocks(db: Session = Depends(get_db)):
    """從 TwStock 抓取台股基本資料並寫入 stock_master"""
    return fetch_stocks(db)


@app.post("/market/snapshot")
def import_market_snapshot(db: Session = Depends(get_db)):
    """從 FinMind 抓取市場總覽快照並寫入 market_snapshot（支援盤中多次呼叫）"""
    return fetch_market_snapshot(db)


@app.post("/market/topics")
def import_market_topics(
    db: Session = Depends(get_db),
    force: bool = Query(False, description="略過交易時段限制（測試用）"),
):
    """掃描全市場找出熱門主題（漲幅 >= 3%、同產業 >= 3 家），寫入 market_topic_snapshot"""
    return fetch_market_topics(db, force=force)
