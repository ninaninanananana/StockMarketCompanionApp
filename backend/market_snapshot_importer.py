import os
import re
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
TWSE_OPENAPI_BASE = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
TWSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.twse.com.tw/zh/trading/exchange/MI_INDEX.html",
}


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


def _fetch_twse_openapi_counts(target_date: str = None) -> Tuple[int, int, int, int]:
    """
    從證交所 MI_INDEX OpenAPI 取得漲跌家數（僅計算「股票」，排除 ETF/權證）。

    API 回傳結構：data["tables"] 為列表，其中 title="漲跌證券數合計" 的表格：
      fields: ['類型', '整體市場', '股票']
      data:   [['上漲(漲停)', '9,333(454)', '808(42)'],
               ['下跌(跌停)', '2,154(121)', '203(4)'], ...]
    值格式 "808(42)" = 總家數808，其中漲停42。

    target_date: 格式 'YYYYMMDD'，預設今日。
    回傳: (total_up, total_down, limit_up, limit_down)
    """
    if target_date is None:
        target_date = date.today().strftime("%Y%m%d")

    url = f"{TWSE_OPENAPI_BASE}?response=json&type=MS&date={target_date}"

    def parse_count_pair(text: str):
        """解析 '808(42)' → (808, 42)；純數字 '399' → (399, 0)"""
        text = text.replace(",", "").strip()
        m = re.match(r"(\d+)\((\d+)\)", text)
        if m:
            return int(m.group(1)), int(m.group(2))
        try:
            return int(text), 0
        except ValueError:
            return 0, 0

    try:
        response = requests.get(url, headers=TWSE_HEADERS, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("stat") != "OK":
            print(f"[TWSE] stat={data.get('stat')}，{target_date} 非交易日或資料尚未更新。")
            return 0, 0, 0, 0

        # 在 tables 列表中找 title 含「漲跌」的表格
        tables = data.get("tables", [])
        target_table = None
        for t in tables:
            if "漲跌" in t.get("title", ""):
                target_table = t.get("data", [])
                break

        if not target_table:
            print("[TWSE] 找不到漲跌證券數表格。")
            for t in tables:
                if t.get("title"):
                    print(f"  title={t['title']!r}, fields={t.get('fields')}")
            return 0, 0, 0, 0

        # 欄位：['類型', '整體市場', '股票']，取 index=2（股票）
        stock_col = 2
        total_up = limit_up = total_down = limit_down = 0

        for row in target_table:
            if not row or len(row) <= stock_col:
                continue
            category = str(row[0]).strip()
            if "上漲" in category:
                total_up, limit_up = parse_count_pair(row[stock_col])
            elif "下跌" in category:
                total_down, limit_down = parse_count_pair(row[stock_col])

        return total_up, total_down, limit_up, limit_down

    except Exception as e:
        print(f"[TWSE] 抓取失敗: {e}")

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


def fetch_and_import(db: Session, target_date: str = None) -> dict:
    """
    target_date: 'YYYY-MM-DD' 格式，預設今日。
    """
    if target_date:
        today = target_date
        twse_date = target_date.replace("-", "")
        now = datetime.strptime(target_date, "%Y-%m-%d")
    else:
        today = date.today().isoformat()
        twse_date = date.today().strftime("%Y%m%d")
        now = datetime.now()

    # 1. 抓取原本的基礎大盤與法人資料
    total_volume = _fetch_total_volume(today)
    foreign_buy, investment_buy, dealer_buy = _fetch_institutional(today)

    # 2. 抓取新追加的 OpenAPI 家數與 FinMind 情緒指標
    up_count, down_count, limit_up, limit_down = _fetch_twse_openapi_counts(twse_date)
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

    # 可傳入日期參數，例如：python market_snapshot_importer.py 2026-06-12
    cli_date = sys.argv[1] if len(sys.argv) > 1 else None

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        result = fetch_and_import(db, target_date=cli_date)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()