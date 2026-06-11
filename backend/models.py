from datetime import datetime
from sqlalchemy import VARCHAR, TINYINT, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class StockMaster(Base):
    __tablename__ = "stock_master"

    stock_id: Mapped[str] = mapped_column(VARCHAR(10), primary_key=True)
    stock_name: Mapped[str] = mapped_column(VARCHAR(50), nullable=False)
    industry: Mapped[str | None] = mapped_column(VARCHAR(50), nullable=True)
    market: Mapped[str] = mapped_column(VARCHAR(20), nullable=False)
    is_active: Mapped[int] = mapped_column(TINYINT, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
