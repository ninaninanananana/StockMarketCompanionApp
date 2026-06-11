import twstock
from sqlalchemy.orm import Session
from models import StockMaster

# TwStock market 代碼對應
VALID_MARKETS = {"sii", "otc"}


def fetch_and_import(db: Session) -> dict:
    stocks = []

    for code, info in twstock.codes.items():
        # 只處理上市(sii)與上櫃(otc)，跳過興櫃、指數等
        if getattr(info, "market", None) not in VALID_MARKETS:
            continue

        stock_id = code
        stock_name = getattr(info, "name", "") or ""
        industry = getattr(info, "group", None)
        market = info.market  # 直接存 sii / otc

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
