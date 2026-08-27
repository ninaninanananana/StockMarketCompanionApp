from datetime import date
from typing import Optional

from fastapi import FastAPI, Depends, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models import MarketSnapshot
from stock_importer import fetch_and_import as fetch_stocks
from market_snapshot_importer import fetch_and_import as fetch_market_snapshot
from market_topic_importer import fetch_and_import as fetch_market_topics

app = FastAPI(title="Stock Market Companion API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


# ── GET /market/snapshot/latest ────────────────────────────────────────────
@app.get("/market/snapshot/latest")
def get_latest_market_snapshot(db: Session = Depends(get_db)):
    """回傳資料庫中最新一筆 market_snapshot"""
    snapshot = (
        db.query(MarketSnapshot)
        .order_by(MarketSnapshot.snapshot_time.desc())
        .first()
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="尚無市場快照資料")
    return _serialize(snapshot)


# ── GET /market/snapshot ────────────────────────────────────────────────────
@app.get("/market/snapshot")
def get_market_snapshot(
    target_date: Optional[str] = Query(
        None,
        description="查詢日期，格式 YYYY-MM-DD，預設今日",
        example="2026-08-27",
    ),
    db: Session = Depends(get_db),
):
    """
    查詢指定日期的 market_snapshot（每日僅一筆）。
    target_date 省略時預設為今日。
    """
    try:
        query_date = date.fromisoformat(target_date) if target_date else date.today()
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式錯誤，請使用 YYYY-MM-DD")

    snapshot = (
        db.query(MarketSnapshot)
        .filter(func.date(MarketSnapshot.snapshot_time) == query_date)
        .first()
    )

    if snapshot is None:
        return []

    return _serialize(snapshot)


def _serialize(s: MarketSnapshot) -> dict:
    return {
        "id": s.id,
        "snapshot_time": s.snapshot_time.isoformat(),
        "market_score": s.market_score,
        "trend": s.trend,
        "up_count": s.up_count,
        "down_count": s.down_count,
        "limit_up": s.limit_up,
        "limit_down": s.limit_down,
        "foreign_buy": s.foreign_buy,
        "investment_buy": s.investment_buy,
        "dealer_buy": s.dealer_buy,
        "total_volume": s.total_volume,
        "speculation_index": s.speculation_index,
        "retail_confidence": s.retail_confidence,
        "source_status": s.source_status,
        "analysis_version": s.analysis_version,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


@app.post("/market/topics")
def import_market_topics(
    db: Session = Depends(get_db),
    force: bool = Query(False, description="略過交易時段限制（測試用）"),
):
    """掃描全市場找出熱門主題（漲幅 >= 3%、同產業 >= 3 家），寫入 market_topic_snapshot"""
    return fetch_market_topics(db, force=force)
