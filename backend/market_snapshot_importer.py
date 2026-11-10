import os
import re
import sys
import json
from datetime import datetime, date
from typing import Optional, Tuple
from pathlib import Path
from zoneinfo import ZoneInfo

# 讓直接執行時也能載入 .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

import requests
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import MarketSnapshot

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "")
FINMIND_API = "https://api.finmindtrade.com/api/v4/data"
TWSE_OPENAPI_BASE = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
TWSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://www.twse.com.tw/zh/trading/exchange/MI_INDEX.html",
}

ANALYSIS_VERSION = "v2"

# 狀態：所有欄位皆取得 → SUCCESS；部分欄位缺值（API 失敗或資料尚未更新）→ PARTIAL
STATUS_SUCCESS = "SUCCESS"
STATUS_PARTIAL = "PARTIAL"

# 這些欄位在真實交易日幾乎不可能剛好為 0，
# 因此 DB 中的 0 一律視為「無資料」（DB 欄位 NOT NULL，缺值只能存 0）。
ZERO_MEANS_MISSING = (
    "total_volume",
    "foreign_buy",
    "investment_buy",
    "dealer_buy",
    "speculation_index",
    "retail_confidence",
    "margin_balance",
    "short_sell_balance",
)

# 可缺值的欄位（漲跌家數為必要欄位，缺少則視為非交易日不寫入）
OPTIONAL_FIELDS = ZERO_MEANS_MISSING + ("industry_diffusion",)


def taipei_now() -> datetime:
    """台北時間（naive），避免主機時區不同造成日期錯誤。"""
    return datetime.now(TAIPEI_TZ).replace(tzinfo=None)


def _get(dataset: str, params: dict) -> list:
    resp = requests.get(
        FINMIND_API,
        params={"dataset": dataset, "token": FINMIND_TOKEN, **params},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def _fetch_total_volume(today: str) -> Optional[int]:
    """
    TaiwanStockStatisticsOfOrderBookAndTrade — 盤中每 ~13 秒一筆。
    取當日最後一筆的累計成交量；無資料或失敗回傳 None。
    """
    try:
        rows = _get(
            "TaiwanStockStatisticsOfOrderBookAndTrade",
            {"start_date": today, "end_date": today},
        )
        if rows:
            return int(rows[-1].get("TotalDealVolume") or 0) or None
    except Exception as e:
        print(f"警告：無法取得成交量資料: {e}", file=sys.stderr)
    return None


def _fetch_institutional(today: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """
    TaiwanStockTotalInstitutionalInvestors — 三大法人全市場合計（收盤後才有當日資料）。
    無資料或失敗時回傳 (None, None, None)。
    """
    try:
        rows = _get(
            "TaiwanStockTotalInstitutionalInvestors",
            {"start_date": today, "end_date": today},
        )
    except Exception as e:
        print(f"警告：無法取得三大法人資料: {e}", file=sys.stderr)
        return None, None, None

    if not rows:
        return None, None, None

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


def _fetch_twse_openapi_data(target_date: str = None) -> Tuple[int, int, int, int]:
    """
    從 TWSE MI_INDEX 取得漲跌家數（僅「股票」欄，排除 ETF/權證）。

      fields: ['類型', '整體市場', '股票']
      data:   [['上漲(漲停)', '9,333(454)', '808(42)'], ...]

    産業擴散度統一改由 market_topic_importer（yfinance 個股平均漲幅）計算，
    此處不再解析（type=MS 不回傳類股表，過去一律得到 0）。

    target_date : 格式 'YYYYMMDD'，預設今日（台北時間）
    回傳        : (up_count, down_count, limit_up, limit_down)；非交易日全為 0
    """
    if target_date is None:
        target_date = taipei_now().strftime("%Y%m%d")

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

    total_up = limit_up = total_down = limit_down = 0

    try:
        response = requests.get(url, headers=TWSE_HEADERS, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("stat") != "OK":
            print(f"[TWSE] stat={data.get('stat')}，{target_date} 非交易日或資料尚未更新。")
            return 0, 0, 0, 0

        tables = data.get("tables", [])

        for t in tables:
            title = t.get("title", "")
            if "漲跌" in title and "類股" not in title:
                stock_col = 2  # ['類型', '整體市場', '股票']
                for row in t.get("data", []):
                    if not row or len(row) <= stock_col:
                        continue
                    category = str(row[0]).strip()
                    if "上漲" in category:
                        total_up, limit_up = parse_count_pair(row[stock_col])
                    elif "下跌" in category:
                        total_down, limit_down = parse_count_pair(row[stock_col])

        if total_up == 0 and total_down == 0:
            print("[TWSE] 找不到漲跌證券數表格。")
            for t in tables:
                if t.get("title"):
                    print(f"  title={t['title']!r}, fields={t.get('fields')}")

    except Exception as e:
        print(f"[TWSE] 抓取失敗: {e}")

    return total_up, total_down, limit_up, limit_down


def _fetch_speculation_index(today: str, total_volume: int = 0) -> Optional[float]:
    """
    市場投機度（當日沖銷交易總成交股數占市場比重%）
    資料來源：TWSE TWTB4U — 當日沖銷交易統計資訊（盤後更新，17:30 時通常尚未更新）
    無資料或失敗回傳 None。
    """
    twse_date = today.replace("-", "")
    url = f"https://www.twse.com.tw/exchangeReport/TWTB4U?response=json&date={twse_date}"
    try:
        resp = requests.get(url, headers=TWSE_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        if data.get("stat") != "OK":
            print(f"[TWTB4U] stat={data.get('stat')}，{twse_date} 非交易日或資料尚未更新。")
            return None

        tables = data.get("tables", [])
        if not tables:
            return None

        # table[0]：當日沖銷交易統計資訊
        # fields: ['當日沖銷交易總成交股數', '當日沖銷交易總成交股數占市場比重%', ...]
        stat_table = tables[0]
        fields = stat_table.get("fields", [])
        rows = stat_table.get("data", [])

        if not rows:
            print(f"[TWTB4U] {twse_date} 無統計資料（可能尚未更新）。")
            return None

        ratio_idx = next(
            (i for i, f in enumerate(fields) if "成交股數" in f and "比重" in f),
            None,
        )
        if ratio_idx is None:
            print(f"[TWTB4U] 找不到比重欄位，fields={fields}")
            return None

        ratio_str = str(rows[0][ratio_idx]).replace(",", "").strip()
        return round(float(ratio_str), 2) or None

    except Exception as e:
        print(f"警告：無法取得當沖投機度資料: {e}", file=sys.stderr)
    return None


def _fetch_margin_data(today: str) -> Tuple[Optional[float], Optional[int], Optional[int]]:
    """
    一次取得融資與融券全部指標。
    此為盤後資料（17:30 時通常尚未更新，由 21:30 的排程補齊），未取得的欄位回傳 None。

    FinMind TaiwanStockTotalMarginPurchaseShortSale 回傳三筆 row：
      name=MarginPurchase      → 融資餘額（股數）
      name=ShortSale           → 融券餘額
      name=MarginPurchaseMoney → 融資餘額（元）
    欄位：TodayBalance（今日餘額）/ YesBalance（昨日餘額）

    Returns
    -------
    (retail_confidence, margin_balance, short_sell_balance)
    - retail_confidence  : 融資增減金額（億元），今日 − 昨日，正值代表加槓桿
    - margin_balance     : 融資餘額絕對值（元），反映整體散戶槓桿水位
    - short_sell_balance : 融券餘額，反映放空力道
    """
    retail_confidence: Optional[float] = None
    margin_balance: Optional[int] = None
    short_sell_balance: Optional[int] = None

    try:
        rows = _get("TaiwanStockTotalMarginPurchaseShortSale", {"start_date": today, "end_date": today})
        for row in rows:
            name = row.get("name", "")
            if name == "MarginPurchaseMoney":
                today_bal = int(row.get("TodayBalance", 0))
                yesterday_bal = int(row.get("YesBalance", 0))
                if today_bal > 0:
                    retail_confidence = round((today_bal - yesterday_bal) / 100_000_000, 2)
                    margin_balance = today_bal
            elif name == "ShortSale":
                short_sell_balance = int(row.get("TodayBalance", 0)) or None
    except Exception as e:
        print(f"警告：無法取得融資融券資料: {e}", file=sys.stderr)

    return retail_confidence, margin_balance, short_sell_balance


_YUAN_TO_YI = 100_000_000  # FinMind 法人資料單位為元，除以此值換算為億元

# ── v2 權重（法人優先） ────────────────────────────────────────────────────────
_W_INSTITUTIONAL = 0.40   # 法人動向
_W_BREADTH       = 0.25   # 市場廣度
_W_SPECULATION   = 0.15   # 投機活躍度
_W_LEVERAGE      = 0.20   # 散戶槓桿（偏空，高槓桿→警告）


def _pct_rank(value: float, history: list[float]) -> float:
    """
    百分位排名，回傳 [-1, 1]。
    history 需已排除缺值（0）。
    資料不足 5 筆時回傳 0（中性）。
    """
    if not history or len(history) < 5:
        return 0.0
    rank = sum(1 for v in history if v <= value) / len(history)
    return round(2 * rank - 1, 4)


def _nonzero(values) -> list:
    """過濾掉代表「無資料」的 0。"""
    return [v for v in values if v]


def _calculate_market_analysis(
    db: Session,
    foreign_buy: Optional[int],
    investment_buy: Optional[int],
    dealer_buy: Optional[int],
    up_count: int,
    down_count: int,
    limit_up: int,
    limit_down: int,
    speculation_index: Optional[float],
    retail_confidence: Optional[float],
    margin_balance: Optional[int],
    short_sell_balance: Optional[int],
    industry_diffusion: Optional[float],
) -> tuple[float, str]:
    """
    v2：四構面加性可分解模型（analysis_version = "v2"）

    Score_t = 50 + 50 × (w1·D_法人 + w2·D_廣度 + w3·D_投機 + w4·D_槓桿)
      w1=0.40  法人動向（外資/投信/自營，等權平均）
      w2=0.25  市場廣度（漲跌比 + 漲跌停淨值百分位 + 産業擴散度，等權平均）
      w3=0.15  投機活躍度（當沖比百分位，方向偏多）
      w4=0.20  散戶槓桿（融資餘額/融券/融資增減，全部倒轉→偏空警告）

    各構面 D_{k,t} 均在 [-1, 1]，百分位排名對歷史資料計算。
    參數為 None 代表缺值，該信號不計入（構面內其餘信號照常平均，全缺則為 0 中性）。
    """
    # ── 取歷史資料（最近 120 個交易日，排除假日） ──────────────────────────────
    try:
        history = (
            db.query(MarketSnapshot)
            .filter(
                MarketSnapshot.up_count > 0,        # 確保是真實交易日
                MarketSnapshot.foreign_buy != 0,    # 排除法人資料缺失日
            )
            .order_by(MarketSnapshot.snapshot_time.desc())
            .limit(120)
            .all()
        )
    except Exception as e:
        print(f"警告：查詢歷史快照失敗: {e}", file=sys.stderr)
        history = []

    # ── 構面 1：法人動向 ───────────────────────────────────────────────────────
    d_signals: list[float] = []
    if foreign_buy is not None:
        d_signals.append(_pct_rank(foreign_buy, _nonzero(r.foreign_buy for r in history)))
    if investment_buy is not None:
        d_signals.append(_pct_rank(investment_buy, _nonzero(r.investment_buy for r in history)))
    if dealer_buy is not None:
        d_signals.append(_pct_rank(dealer_buy, _nonzero(r.dealer_buy for r in history)))

    d_institutional = sum(d_signals) / len(d_signals) if d_signals else 0.0

    # ── 構面 2：市場廣度 ───────────────────────────────────────────────────────
    total_stocks = up_count + down_count
    breadth_ratio = (up_count - down_count) / total_stocks if total_stocks > 0 else 0.0

    h_limit_net = [r.limit_up - r.limit_down for r in history]
    limit_net_pct = _pct_rank(limit_up - limit_down, h_limit_net)

    breadth_signals = [breadth_ratio, limit_net_pct]
    if industry_diffusion is not None:
        breadth_signals.append(2 * industry_diffusion - 1)

    d_breadth = sum(breadth_signals) / len(breadth_signals)

    # ── 構面 3：投機活躍度 ─────────────────────────────────────────────────────
    d_speculation = (
        _pct_rank(speculation_index, _nonzero(r.speculation_index for r in history))
        if speculation_index is not None else 0.0
    )

    # ── 構面 4：散戶槓桿（偏空，全部倒轉） ────────────────────────────────────
    lev_signals: list[float] = []

    if margin_balance is not None:   # 高餘額 → 負值
        lev_signals.append(-_pct_rank(margin_balance, _nonzero(r.margin_balance for r in history)))
    if short_sell_balance is not None:   # 高融券 → 負值
        lev_signals.append(-_pct_rank(short_sell_balance, _nonzero(r.short_sell_balance for r in history)))
    if retail_confidence is not None:   # 融資增加 → 負值
        lev_signals.append(-_pct_rank(retail_confidence, _nonzero(r.retail_confidence for r in history)))

    d_leverage = sum(lev_signals) / len(lev_signals) if lev_signals else 0.0

    # ── 加權合成 ───────────────────────────────────────────────────────────────
    composite = (
        _W_INSTITUTIONAL * d_institutional
        + _W_BREADTH     * d_breadth
        + _W_SPECULATION * d_speculation
        + _W_LEVERAGE    * d_leverage
    )
    score = round(max(0.0, min(100.0, 50 + 50 * composite)), 1)

    # ── 信號描述 ───────────────────────────────────────────────────────────────
    signals: list[str] = []

    # 法人
    if foreign_buy is not None:
        foreign_yi = foreign_buy / _YUAN_TO_YI
        invest_yi = (investment_buy or 0) / _YUAN_TO_YI
        if d_institutional > 0.3:
            signals.append(f"法人合力買超（外資{foreign_yi:+.0f}億，投信{invest_yi:+.0f}億）")
        elif d_institutional < -0.3:
            signals.append(f"法人合力賣超（外資{foreign_yi:+.0f}億，投信{invest_yi:+.0f}億）")
        else:
            signals.append(f"外資{foreign_yi:+.0f}億，投信{invest_yi:+.0f}億")

    # 廣度
    diffusion_text = f"，擴散度{industry_diffusion:.0%}" if industry_diffusion is not None else ""
    if d_breadth > 0.3:
        signals.append(f"市場廣度良好（漲{up_count}/跌{down_count}{diffusion_text}）")
    elif d_breadth < -0.3:
        signals.append(f"市場廣度疲弱（漲{up_count}/跌{down_count}{diffusion_text}）")

    # 槓桿警告
    if margin_balance is not None:
        margin_yi = margin_balance / _YUAN_TO_YI
        if d_leverage < -0.4:
            signals.append(f"⚠️ 散戶槓桿偏高（融資餘額{margin_yi:.0f}億），留意反轉風險")
        elif d_leverage > 0.3:
            signals.append(f"散戶槓桿溫和（融資餘額{margin_yi:.0f}億），風險可控")

    # 投機
    if speculation_index is not None:
        if d_speculation > 0.5:
            signals.append(f"當沖比{speculation_index:.1f}%，市場交投活躍")
        elif d_speculation < -0.5:
            signals.append(f"當沖比{speculation_index:.1f}%，市場觀望")

    # ── 情緒標籤 ───────────────────────────────────────────────────────────────
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

    trend_text = f"市場{label}。" + "，".join(signals[:3])
    if len(trend_text) > 100:
        trend_text = trend_text[:97] + "..."

    return score, trend_text


def _existing_value(snapshot: Optional[MarketSnapshot], field: str):
    """取 DB 既有值；不存在或屬於「0 代表缺值」的欄位且為 0 時回傳 None。"""
    if snapshot is None:
        return None
    value = getattr(snapshot, field)
    if field in ZERO_MEANS_MISSING and not value:
        return None
    return value


def fetch_and_import(
    db: Session,
    target_date: str = None,
    industry_diffusion: Optional[float] = None,
) -> dict:
    """
    target_date        : 'YYYY-MM-DD' 格式，預設今日（台北時間）。
    industry_diffusion : 由 market_topic_importer 算好傳入；None 則沿用 DB 既有值。

    同一天可重複執行（17:30 首次寫入、21:30 補齊盤後資料）：
    本次未取得的欄位會沿用 DB 既有值，不會被 0 覆寫。
    """
    if target_date:
        today = target_date
        now = datetime.strptime(target_date, "%Y-%m-%d")
    else:
        now = taipei_now()
        today = now.date().isoformat()
    twse_date = today.replace("-", "")

    # 1. 漲跌家數（必要欄位）；非交易日 → 跳過，不寫入 DB
    up_count, down_count, limit_up, limit_down = _fetch_twse_openapi_data(twse_date)
    if up_count == 0 and down_count == 0:
        return {"skipped": True, "reason": f"{today} 非交易日或資料尚未更新，略過寫入"}

    # 2. 其餘欄位（取不到為 None）
    total_volume = _fetch_total_volume(today)
    foreign_buy, investment_buy, dealer_buy = _fetch_institutional(today)
    speculation_index = _fetch_speculation_index(today)
    retail_confidence, margin_balance, short_sell_balance = _fetch_margin_data(today)

    fetched = {
        "total_volume": total_volume,
        "foreign_buy": foreign_buy,
        "investment_buy": investment_buy,
        "dealer_buy": dealer_buy,
        "speculation_index": speculation_index,
        "retail_confidence": retail_confidence,
        "margin_balance": margin_balance,
        "short_sell_balance": short_sell_balance,
        "industry_diffusion": industry_diffusion,
    }

    # 3. 與 DB 既有值合併：本次缺值的欄位沿用既有值
    existing = db.query(MarketSnapshot).filter(
        func.date(MarketSnapshot.snapshot_time) == date.fromisoformat(today),
    ).first()

    values = {
        field: (v if v is not None else _existing_value(existing, field))
        for field, v in fetched.items()
    }
    missing = [f for f in OPTIONAL_FIELDS if values[f] is None]
    source_status = STATUS_PARTIAL if missing else STATUS_SUCCESS

    # 4. v2 四維度加法模型
    market_score, trend = _calculate_market_analysis(
        db,
        values["foreign_buy"], values["investment_buy"], values["dealer_buy"],
        up_count, down_count, limit_up, limit_down,
        values["speculation_index"], values["retail_confidence"], values["margin_balance"],
        values["short_sell_balance"], values["industry_diffusion"],
    )

    # 5. 寫入資料庫（同一天只保留一筆）；DB 欄位 NOT NULL，缺值存 0
    row = {
        "snapshot_time": now,
        "up_count": up_count,
        "down_count": down_count,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "market_score": market_score,
        "trend": trend,
        "source_status": source_status,
        "analysis_version": ANALYSIS_VERSION,
        **{f: (v if v is not None else 0) for f, v in values.items()},
    }

    if existing:
        for field, v in row.items():
            setattr(existing, field, v)
        snapshot = existing
    else:
        snapshot = MarketSnapshot(**row)   # id 交由 DB AUTO_INCREMENT 產生
        db.add(snapshot)

    db.commit()
    db.refresh(snapshot)

    return {
        "id": snapshot.id,
        "snapshot_time": now.isoformat(),
        **values,
        "up_count": up_count,
        "down_count": down_count,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "market_score": market_score,
        "trend": trend,
        "source_status": source_status,
        "missing_fields": missing,
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
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()
