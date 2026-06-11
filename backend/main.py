from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from database import get_db
from stock_importer import fetch_and_import

app = FastAPI(title="Stock Market Companion API")


@app.get("/")
def root():
    return {"message": "Stock Market Companion API is running"}


@app.post("/stocks/import")
def import_stocks(db: Session = Depends(get_db)):
    """從 TwStock 抓取台股基本資料並寫入 stock_master"""
    result = fetch_and_import(db)
    return result
