import os
import sys
import json
from datetime import datetime, date
from typing import Tuple
from pathlib import Path

# 讓直接執行時也能載入 .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import requests
from sqlalchemy.orm import Session

from models import MarketSnapshot

FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "")
FINMIND_API = "https://api.finmindtrade.com/api/v4/data"
TWSE_OPENAPI_URL = "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX_GA"


def _get(dataset: str, params: dict) -> list:
    resp = requests.get(
        FINMIND_API,
        params={"dataset": dataset, "token": FINMIND_TOKEN, **params},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def _fetch_total_volume(today: str) -> int:
    """
    TaiwanStockStatisticsOfOrderBookAndTrade — 盤中每 ~13 秒一筆。
    取當日最後一筆的累計成交量。
    """
    rows = _get(
        "TaiwanStockStatisticsOfOrderBookAndTrade",
        {"start_date": today, "end_date": today},
    )
    if rows:
        return int(rows[-1].get("TotalDealVolume") or 0)
    return 0


def _fetch_institutional(today: str) -> Tuple[int, int, int]:
    """
    TaiwanStockTotalInstitutionalInvestors — 三大法人全市場合計（收盤後才有當日資料）。
    """
    rows = _get(
        "TaiwanStockTotalInstitutionalInvestors",
        {"start_date": today, "end_date": today},
    )

    foreign = investment = dealer_self = dealer_hedging = 0

    for row in rows:
        name = row.get("name", "")
        net = int(row.get("buy") or 0) - int(row.get("sell") or 0)
        if name == "Foreign_Investor":
            foreign = net
        elif name == "Investment_Trust":
            investment = net
        elif name == "Dealer_self":
            dealer_self = net
        elif name == "Dealer_Hedging":
            dealer_hedging = net

    return foreign, investment, dealer_self + dealer_hedging


def _fetch_twse_openapi_counts() -> Tuple[int, int, int, int]:
    """
    1. 新增：從台灣證交所 OpenAPI 獲取免費的漲跌停與上漲下跌家數
    回傳: (上漲家數, 下跌家數, 漲停家數, 跌停家數)
    """
    try:
        resp = requests.get(TWSE_OPENAPI_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        
        if data and isinstance(data, list):
            # 證交所 OpenAPI 回傳的是一個陣列，通常第一筆就是當日統計
            row = data[0]
            up_count = int(row.get("UpNum", 0))     # 上漲家數 (不含漲停)
            down_count = int(row.get("DnNum", 0))   # 下跌家數 (不含跌停)
            limit_up = int(row.get("UpLmtNum", 0))   # 漲停家數
            limit_down = int(row.get("DNLmtNum", 0)) # 跌停家術
            
            # 實務上通常把「漲停」也算進「總上漲家數」中，視覺化比較好看
            total_up = up_count + limit_up
            total_down = down_count + limit_down
            
            return total_up, total_down, limit_up, limit_down
    except Exception as e:
        print(f"警告：無法從證交所 OpenAPI 取得家數資料: {e}", file=sys.stderr)
    
    return 0, 0, 0, 0


def _fetch_speculation_index(today: str, total_volume: int) -> float:
    """
    2. 新增：市場投機度（當沖成交量 / 大盤總成交量）
    """
    if total_volume == 0:
        return 0.0
    try:
        # 抓取當日當沖合計資料
        rows = _get("TaiwanStockDayTrading", {"start_date": today, "end_date": today})
        if rows:
            # 欄位 "BuyVolume" 代表當沖買進股數，乘以 2 通常代表當沖總成交量
            # 或是直接取其中一邊的股數（因為當沖是買賣兩邊），對比大盤總量
            day_trade_vol = int(rows[-1].get("BuyVolume", 0))
            # 計算當沖量佔大盤比例 (百分比)
            speculation_ratio = round((day_trade_vol / total_volume) * 100, 2)
            return speculation_ratio
    except Exception as e:
        print(f"警告：無法取得當沖投機度資料: {e}", file=sys.stderr)
    return 0.0


def _fetch_retail_confidence(today: str) -> float:
    """
    3. 新增：散戶信心指數（使用整體市場融資增減金額）
    註：此為盤後資料（約下午 9 點更新），盤中會維持 0 或取到上一日的增減
    """
    try:
        rows = _get("TaiwanStockTotalMarginPurchaseShortSale", {"start_date": today, "end_date": today})
        if rows:
            # MarginPurchaseTodayBalance: 今日融資餘額
            # MarginPurchaseYesterdayBalance: 昨日融資餘額
            row = rows[-1]
            today_bal = int(row.get("MarginPurchaseTodayBalance", 0))
            yesterday_bal = int(row.get("MarginPurchaseYesterdayBalance", 0))
            
            # 融資增減金額（單位通常是元，換算成「億元」比較好讀）
            margin_diff_in_yi = (today_bal - yesterday_bal) / 100_000_000
            return round(margin_diff_in_yi, 2)
    except Exception as e:
        print(f"警告：無法取得融資散戶信心資料: {e}", file=sys.stderr)
    return 0.0


def fetch_and_import(db: Session) -> dict:
    today = date.today().isoformat()
    now = datetime.now()

    # 1. 抓取原本的基礎大盤與法人資料
    total_volume = _fetch_total_volume(today)
    foreign_buy, investment_buy, dealer_buy = _fetch_institutional(today)

    # 2. 抓取新追加的 OpenAPI 家數與 FinMind 情緒指標
    up_count, down_count, limit_up, limit_down = _fetch_twse_openapi_counts()
    speculation_index = _fetch_speculation_index(today, total_volume)
    retail_confidence = _fetch_retail_confidence(today)

    # 3. 寫入資料庫
    snapshot = MarketSnapshot(
        snapshot_time=now,
        total_volume=total_volume,
        foreign_buy=foreign_buy,
        investment_buy=investment_buy,
        dealer_buy=dealer_buy,
        up_count=up_count,          # 補上真實數據
        down_count=down_count,      # 補上真實數據
        limit_up=limit_up,          # 補上真實數據
        limit_down=limit_down,      # 補上真實數據
        market_score=0,
        trend="",
        source_status="SUCCESS",
    )

    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)

    return {
        "id": snapshot.id,
        "snapshot_time": now.isoformat(),
        "total_volume": total_volume,
        "foreign_buy": foreign_buy,
        "investment_buy": investment_buy,
        "dealer_buy": dealer_buy,
        "up_count": up_count,
        "down_count": down_count,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "speculation_index": f"{speculation_index}%",
        "retail_confidence": f"{retail_confidence} 億",
        "message": "市場快照已成功更新並寫入資料庫",
    }


if __name__ == "__main__":
    from database import SessionLocal, Base, engine

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        result = fetch_and_import(db)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()