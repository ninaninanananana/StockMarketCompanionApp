from __future__ import annotations
from datetime import date, datetime
from typing import Optional
from typing import Any
from sqlalchemy import BigInteger, Date, Float, Integer, VARCHAR, DateTime, JSON, func
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class StockMaster(Base):
    __tablename__ = "stock_master"

    stock_id: Mapped[str] = mapped_column(VARCHAR(10), primary_key=True)
    stock_name: Mapped[str] = mapped_column(VARCHAR(50), nullable=False)
    industry: Mapped[Optional[str]] = mapped_column(VARCHAR(50), nullable=True)
    market: Mapped[str] = mapped_column(VARCHAR(20), nullable=False)
    is_active: Mapped[int] = mapped_column(TINYINT, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )


class MarketTopicSnapshot(Base):
    """對應 DB 的 market_topic_snapshot 資料表。"""

    __tablename__ = "market_topic_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    topic_name: Mapped[str] = mapped_column(VARCHAR(50), nullable=False)
    trend: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default="")
    topic_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stock_list: Mapped[Any] = mapped_column(JSON, nullable=False)
    reason: Mapped[Optional[Any]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, server_default=func.now()
    )


class MarketSnapshot(Base):
    """對應 DB 現有的 market_snapshot 資料表。"""

    __tablename__ = "market_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, unique=True)

    # AI 分析結果（由其他模組填寫，importer 留預設值）
    market_score: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    trend: Mapped[str] = mapped_column(VARCHAR(100), nullable=False, default="")

    # 漲跌家數（TODO: FinMind register 帳號無法一次取全部個股，需升級或改換資料源）
    up_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    down_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    limit_up: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    limit_down: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # 三大法人買超（來源：TaiwanStockTotalInstitutionalInvestors，收盤後才有當日資料）
    foreign_buy: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    investment_buy: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    dealer_buy: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # 大盤成交量（來源：TaiwanStockStatisticsOfOrderBookAndTrade）
    total_volume: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    analysis_version: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default="v1")
    source_status: Mapped[str] = mapped_column(VARCHAR(20), nullable=False, default="SUCCESS")
    speculation_index: Mapped[str] = mapped_column(Float, nullable=False,default=0)
    retail_confidence: Mapped[str] = mapped_column(Float, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
