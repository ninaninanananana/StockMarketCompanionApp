import os
import re
import sys
import json
from datetime import datetime, date, timedelta
from typing import Tuple
from pathlib import Path

# 讓直接執行時也能載入 .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import requests
from sqlalchemy import func
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
        # Foreign_Investor_Dealer（外資自營商）依台灣證交所定義須合併計入外資
        if name in ("Foreign_Investor", "Foreign_Investor_Dealer"):
            foreign += net
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


def _fetch_speculation_index(today: str, total_volume: int = 0) -> float:
    """
    市場投機度（當日沖銷交易總成交股數占市場比重%）
    資料來源：TWSE TWTB4U — 當日沖銷交易統計資訊（盤後更新）
    """
    twse_date = today.replace("-", "")
    url = f"https://www.twse.com.tw/exchangeReport/TWTB4U?response=json&date={twse_date}"
    try:
        resp = requests.get(url, headers=TWSE_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("stat") != "OK":
            print(f"[TWTB4U] stat={data.get('stat')}，{twse_date} 非交易日或資料尚未更新。")
            return 0.0

        tables = data.get("tables", [])
        if not tables:
            return 0.0

        # table[0]：當日沖銷交易統計資訊
        # fields: ['當日沖銷交易總成交股數', '當日沖銷交易總成交股數占市場比重%', ...]
        stat_table = tables[0]
        fields = stat_table.get("fields", [])
        rows = stat_table.get("data", [])

        if not rows:
            print(f"[TWTB4U] {twse_date} 無統計資料（可能尚未更新）。")
            return 0.0

        # 找「成交股數占市場比重」欄位索引
        ratio_idx = next(
            (i for i, f in enumerate(fields) if "成交股數" in f and "比重" in f),
            None,
        )
        if ratio_idx is None:
            print(f"[TWTB4U] 找不到比重欄位，fields={fields}")
            return 0.0

        ratio_str = str(rows[0][ratio_idx]).replace(",", "").strip()
        return round(float(ratio_str), 2)

    except Exception as e:
        print(f"警告：無法取得當沖投機度資料: {e}", file=sys.stderr)
    return 0.0


def _fetch_retail_confidence(today: str) -> float:
    """
    散戶信心指數（整體市場融資增減金額，換算億元）
    此為盤後資料（約下午 9 點更新），盤中呼叫會回傳 0.0
    """
    try:
        rows = _get("TaiwanStockTotalMarginPurchaseShortSale", {"start_date": today, "end_date": today})
        if rows:
            row = rows[-1]
            today_bal = int(row.get("MarginPurchaseTodayBalance", 0))
            yesterday_bal = int(row.get("MarginPurchaseYesterdayBalance", 0))
            margin_diff_in_yi = (today_bal - yesterday_bal) / 100_000_000
            return round(margin_diff_in_yi, 2)
    except Exception as e:
        print(f"警告：無法取得融資散戶信心資料: {e}", file=sys.stderr)
    return 0.0


_YUAN_TO_YI = 100_000_000  # FinMind 法人資料單位為元，除以此值換算為億元


def _calculate_market_analysis(
    db: Session,
    foreign_buy: int,    # 外資買賣超（元）
    investment_buy: int, # 投信買賣超（元）
    dealer_buy: int,     # 自營商買賣超（元）
    limit_up: int,       # 漲停家數
    limit_down: int,     # 跌停家數
) -> tuple[float, str]:
    """
    XAI Rule-based 市場評分（冷啟動階段，待資料累積後切換為 SHAP 模型）。
    五個核心輸入：外資、投信、自營、漲停、跌停。
    歷史快照提供近期趨勢脈絡。
    回傳 (market_score: 0–100, trend: 純文字描述)

    分數配置（基礎 50 分）：
      外資 ±25、投信 ±15、自營 ±10、漲停 +15、跌停 -15
    """
    score = 50.0
    signals: list[str] = []

    # --- 1. 外資買賣超（±25 分，最高權重）---
    # foreign_buy = 0 視為無資料（例如週一前日為週末），跳過不計分
    if foreign_buy != 0:
        foreign_yi = foreign_buy / _YUAN_TO_YI
        if foreign_yi >= 300:
            score += 25
            signals.append(f"外資大買+{foreign_yi:.0f}億")
        elif foreign_yi >= 50:
            score += 15
            signals.append(f"外資買超+{foreign_yi:.0f}億")
        elif foreign_yi > 0:
            score += 5
            signals.append(f"外資小幅買超+{foreign_yi:.0f}億")
        elif foreign_yi <= -300:
            score -= 25
            signals.append(f"外資大賣{foreign_yi:.0f}億")
        elif foreign_yi <= -50:
            score -= 15
            signals.append(f"外資賣超{foreign_yi:.0f}億")
        else:
            score -= 5
            signals.append(f"外資小幅賣超{foreign_yi:.0f}億")

    # --- 2. 投信買賣超（±15 分）---
    if investment_buy != 0:
        invest_yi = investment_buy / _YUAN_TO_YI
        if invest_yi > 10:
            score += 15
            signals.append(f"投信買超+{invest_yi:.0f}億")
        elif invest_yi > 0:
            score += 8
            signals.append(f"投信小幅買超+{invest_yi:.1f}億")
        elif invest_yi < -10:
            score -= 15
            signals.append(f"投信賣超{invest_yi:.0f}億")
        else:
            score -= 8
            signals.append(f"投信小幅賣超{invest_yi:.1f}億")

    # --- 3. 自營商買賣超（±10 分）---
    if dealer_buy != 0:
        dealer_yi = dealer_buy / _YUAN_TO_YI
        if dealer_yi > 0:
            score += 10
            signals.append(f"自營買超+{dealer_yi:.0f}億")
        else:
            score -= 10
            signals.append(f"自營賣超{dealer_yi:.0f}億")

    # --- 4. 漲停家數（絕對數量判斷，+15 分）---
    if limit_up >= 50:
        score += 15
        signals.append(f"漲停{limit_up}家，買氣極旺")
    elif limit_up >= 20:
        score += 8
        signals.append(f"漲停{limit_up}家，買氣偏強")

    # --- 5. 跌停家數（絕對數量判斷，-15 分）---
    if limit_down >= 20:
        score -= 15
        signals.append(f"跌停{limit_down}家，市場恐慌")
    elif limit_down >= 10:
        score -= 8
        signals.append(f"跌停{limit_down}家，市場偏弱")

    # --- 近期趨勢脈絡（歷史 5 筆，不計入 5 個核心 input）---
    try:
        recent = (
            db.query(MarketSnapshot)
            .filter(MarketSnapshot.market_score > 0)
            .order_by(MarketSnapshot.snapshot_time.desc())
            .limit(5)
            .all()
        )
        if len(recent) >= 3:
            avg_score = sum(r.market_score for r in recent) / len(recent)
            # 外資連續方向（排除 0 值的無資料日）
            recent3_foreign = [r.foreign_buy for r in recent[:3] if r.foreign_buy != 0]
            if len(recent3_foreign) == 3 and all(v > 0 for v in recent3_foreign):
                signals.append("外資近期連續買超，信心回升")
            elif len(recent3_foreign) == 3 and all(v < 0 for v in recent3_foreign):
                signals.append("外資近期連續賣超，資金流出")
            # 市場溫度趨勢
            if score > avg_score + 10:
                signals.append(f"市場溫度高於近期均值（均{avg_score:.0f}分），氣氛轉熱")
            elif score < avg_score - 10:
                signals.append(f"市場溫度低於近期均值（均{avg_score:.0f}分），氣氛轉冷")
    except Exception as e:
        print(f"警告：查詢歷史快照失敗: {e}", file=sys.stderr)

    score = round(max(0.0, min(100.0, score)), 1)

    # --- 情緒標籤 ---
    if score >= 70:
        label = "偏多"
    elif score >= 57:
        label = "中性偏多"
    elif score >= 43:
        label = "中性"
    elif score >= 30:
        label = "中性偏空"
    else:
        label = "偏空"

    trend_text = f"市場{label}。" + "，".join(signals[:4])
    if len(trend_text) > 100:
        trend_text = trend_text[:97] + "..."

    return score, trend_text


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
    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    foreign_buy, investment_buy, dealer_buy = _fetch_institutional(yesterday)

    # 2. 抓取新追加的 OpenAPI 家數與 FinMind 情緒指標
    up_count, down_count, limit_up, limit_down = _fetch_twse_openapi_counts(twse_date)
    speculation_index = _fetch_speculation_index(yesterday, total_volume)
    retail_confidence = _fetch_retail_confidence(yesterday)

    # 3. Rule-based 分析（五個核心 input + 近期歷史趨勢脈絡）
    market_score, trend = _calculate_market_analysis(
        db,
        foreign_buy, investment_buy, dealer_buy,
        limit_up, limit_down,
    )

    # 4. 寫入資料庫（同天同小時則覆蓋；否則新增，id 從 1 開始遞增）
    existing = db.query(MarketSnapshot).filter(
        func.date(MarketSnapshot.snapshot_time) == date.fromisoformat(today),
        func.hour(MarketSnapshot.snapshot_time) == now.hour,
    ).first()

    if existing:
        existing.snapshot_time    = now
        existing.total_volume     = total_volume
        existing.foreign_buy      = foreign_buy
        existing.investment_buy   = investment_buy
        existing.dealer_buy       = dealer_buy
        existing.up_count         = up_count
        existing.down_count       = down_count
        existing.limit_up         = limit_up
        existing.limit_down       = limit_down
        existing.market_score     = market_score
        existing.trend            = trend
        existing.source_status    = "SUCCESS"
        existing.speculation_index    = speculation_index
        existing.retail_confidence    = retail_confidence
        snapshot = existing
    else:
        max_id = db.query(func.max(MarketSnapshot.id)).scalar() or 0
        snapshot = MarketSnapshot(
            id=max_id + 1,
            snapshot_time=now,
            total_volume=total_volume,
            foreign_buy=foreign_buy,
            investment_buy=investment_buy,
            dealer_buy=dealer_buy,
            up_count=up_count,
            down_count=down_count,
            limit_up=limit_up,
            limit_down=limit_down,
            market_score=market_score,
            trend=trend,
            source_status="SUCCESS",
            speculation_index=speculation_index,
            retail_confidence=retail_confidence
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
        "market_score": market_score,
        "trend": trend,
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