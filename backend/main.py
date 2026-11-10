import os
import secrets
from datetime import date
from typing import Optional

from fastapi import FastAPI, Depends, Header, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from models import MarketSnapshot, MarketTopicSnapshot
from stock_importer import fetch_and_import as fetch_stocks
from market_snapshot_importer import fetch_and_import as fetch_market_snapshot, taipei_now

app = FastAPI(title="Stock Market Companion API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_api_key(x_api_key: Optional[str] = Header(None)) -> None:
    """
    保護會觸發外部 API 呼叫與寫入 DB 的 POST 端點。
    需在 .env 設定 ADMIN_API_KEY，呼叫時帶 header：X-API-Key: <ADMIN_API_KEY>
    未設定 ADMIN_API_KEY 時一律拒絕。
    """
    expected = os.getenv("ADMIN_API_KEY")
    if not expected:
        raise HTTPException(status_code=503, detail="伺服器未設定 ADMIN_API_KEY，管理端點停用")
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="API key 無效")


@app.get("/")
def root():
    return {"message": "Stock Market Companion API is running"}


@app.post("/stocks/import", dependencies=[Depends(require_api_key)])
def import_stocks(db: Session = Depends(get_db)):
    """從 TwStock 抓取台股基本資料並寫入 stock_master"""
    return fetch_stocks(db)


@app.post("/market/snapshot", dependencies=[Depends(require_api_key)])
def import_market_snapshot(db: Session = Depends(get_db)):
    """抓取市場總覽快照並寫入 market_snapshot（同日重複呼叫會更新，缺值欄位沿用既有值）"""
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
        query_date = date.fromisoformat(target_date) if target_date else taipei_now().date()
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
        "margin_balance": s.margin_balance,
        "short_sell_balance": s.short_sell_balance,
        "industry_diffusion": s.industry_diffusion,
        "speculation_index": s.speculation_index,
        "retail_confidence": s.retail_confidence,
        "source_status": s.source_status,
        "analysis_version": s.analysis_version,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


# ── GET /market/topics/latest ──────────────────────────────────────────────
@app.get("/market/topics/latest")
def get_latest_market_topics(db: Session = Depends(get_db)):
    """回傳最新一批 market_topic_snapshot（同一 snapshot_time 的全部主題，依排名排序）"""
    latest_time = db.query(func.max(MarketTopicSnapshot.snapshot_time)).scalar()
    topics = (
        db.query(MarketTopicSnapshot)
        .filter(MarketTopicSnapshot.snapshot_time == latest_time)
        .order_by(MarketTopicSnapshot.id)
        .all()
    ) if latest_time else []
    if not topics:
        raise HTTPException(status_code=404, detail="尚無主題快照資料")
    return [
        {
            "id": t.id,
            "snapshot_time": t.snapshot_time.isoformat(),
            "topic_name": t.topic_name,
            "trend": t.trend,
            "topic_score": t.topic_score,
            "stock_list": t.stock_list,
            "reason": t.reason,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        }
        for t in topics
    ]

