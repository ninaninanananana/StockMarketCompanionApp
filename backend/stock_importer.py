import twstock
from sqlalchemy.orm import Session
from models import StockMaster

# TwStock market 中文對應英文代碼
MARKET_MAP = {"上市": "sii", "上櫃": "otc"}

# 只匯入一般股票，排除認購權證、ETF、TDR 等
VALID_TYPES = {"股票"}


def fetch_and_import(db: Session) -> dict:
    stocks = []

    for code, info in twstock.codes.items():
        # 只處理上市(sii)與上櫃(otc)，跳過興櫃、指數等
        market_zh = getattr(info, "market", None)
        if market_zh not in MARKET_MAP:
            continue

        # 只取一般股票，排除認購權證、ETF 等
        if getattr(info, "type", None) not in VALID_TYPES:
            continue

        stock_id = code
        stock_name = getattr(info, "name", "") or ""
        industry = getattr(info, "group", None) or None
        market = MARKET_MAP[market_zh]  # 轉為英文代碼 sii / otc

        stocks.append(
            StockMaster(
                stock_id=stock_id,
                stock_name=stock_name,
                industry=industry,
                market=market,
                is_active=1,
            )
        )

    if not stocks:
        return {"imported": 0, "message": "No stocks found"}

    # 使用 UPSERT：已存在則更新，不存在則新增
    from sqlalchemy.dialects.mysql import insert as mysql_insert

    for stock in stocks:
        stmt = mysql_insert(StockMaster).values(
            stock_id=stock.stock_id,
            stock_name=stock.stock_name,
            industry=stock.industry,
            market=stock.market,
            is_active=stock.is_active,
        )
        stmt = stmt.on_duplicate_key_update(
            stock_name=stmt.inserted.stock_name,
            industry=stmt.inserted.industry,
            market=stmt.inserted.market,
            is_active=stmt.inserted.is_active,
        )
        db.execute(stmt)

    db.commit()
    return {"imported": len(stocks), "message": f"成功匯入 {len(stocks)} 筆股票資料"}
