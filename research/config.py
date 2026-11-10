"""
研究版參數（與 App 的 v2 公式分開，見 discussRnnForecast.md 第四輪決議 Q9、Q11）。

所有可調整的研究設定集中在這裡，論文中引用的數字都應與此檔一致。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT.parent / "backend"
OUTPUT_DIR = ROOT / "outputs"

# ── 資料期間（Q6、Q7）────────────────────────────────────────────────────────
RAW_START = "2023-07-01"      # 原始資料回補起點（120 日暖機）
SAMPLE_START = "2024-01-01"   # 研究樣本起點

# ── 正規化（point-in-time 百分位）─────────────────────────────────────────────
PCT_LOOKBACK = 120            # 只與「當日以前」最近 120 個交易日比較
PCT_MIN_HISTORY = 20          # 歷史不足 20 筆時視為缺值

# ── 四大構面與指標方向（Q6、Q10、Q11）────────────────────────────────────────
# (欄位, 方向, 轉換方式)
#   pct       : 過去 120 日百分位 → [-1, 1]
#   raw       : 本身已在 [-1, 1]
#   diffusion : 2 × 擴散度 − 1
DIMENSION_SIGNALS: dict[str, list[tuple[str, int, str]]] = {
    "institutional": [
        ("foreign_buy", +1, "pct"),
        ("investment_buy", +1, "pct"),
        ("dealer_buy", +1, "pct"),
    ],
    "breadth": [
        ("breadth_ratio", +1, "raw"),
        ("limit_net", +1, "pct"),
        ("industry_diffusion", +1, "diffusion"),
    ],
    "speculation": [
        ("speculation_index", -1, "pct"),   # 當沖比高 → 短線躁動、風險升高
        ("total_volume", -1, "pct"),        # Q10(a)：成交量放大 → 躁動
    ],
    "leverage": [
        ("margin_balance", -1, "pct"),      # 融資餘額高 → 槓桿過高
        ("retail_confidence", -1, "pct"),   # 融資增加 → 加槓桿
        ("short_sell_balance", +1, "pct"),  # 融券高 → 可能引發軋空
    ],
}
DIMENSIONS = list(DIMENSION_SIGNALS)
DIMENSION_LABELS = {
    "institutional": "法人動向",
    "breadth": "市場廣度",
    "speculation": "投機活躍度",
    "leverage": "散戶槓桿",
}

# DB 中這些欄位為 0 代表「無資料」（與 backend ZERO_MEANS_MISSING 一致，另加擴散度）
ZERO_MEANS_MISSING = [
    "total_volume", "foreign_buy", "investment_buy", "dealer_buy",
    "speculation_index", "retail_confidence", "margin_balance",
    "short_sell_balance", "industry_diffusion",
]

# ── Phase 1：權重方案與驗證 ───────────────────────────────────────────────────
SCHEMES: dict[str, dict[str, float]] = {
    # 方案 A：研究者依市場實務經驗設定，法人權重最高（參考 Hsu et al., 2024）
    "A": {"institutional": 0.40, "breadth": 0.25, "speculation": 0.15, "leverage": 0.20},
    # 方案 B：等權
    "B": {"institutional": 0.25, "breadth": 0.25, "speculation": 0.25, "leverage": 0.25},
    # 方案 C：由 phase1_temperature.py 校準（Q3：與當日大盤報酬相關最大）
}
CALIBRATION_STEP = 0.05
TRAIN_RATIO = 0.6               # 依時間先後，前 60% 為訓練期
SENSITIVITY_DELTAS = [-0.20, -0.10, 0.10, 0.20]
STABLE_LABEL_CHANGE = 0.10      # 標籤改變比例 < 10% 視為穩定（Q5）
REALIZED_VOL_WINDOW = 20        # 台指 VIX 歷史不足時的替代指標：20 日實現波動度

KNOWN_EVENTS = {
    "2024-08-05": "日圓套利平倉，台股單日重挫",
    "2025-04-07": "美國關稅衝擊，台股單日重挫",
}

# 五級情緒標籤（與 App 一致）
LABEL_BINS = [(70, "偏多"), (57, "中性偏多"), (43, "中性"), (30, "中性偏空"), (-1, "偏空")]

# ── Phase 2：預測 ──────────────────────────────────────────────────────────────
WINDOW = 60                     # Q5：60 個交易日
CLASSES = ["降溫", "持平", "升溫"]
REFIT_EVERY = 5                 # walk-forward 每 5 個交易日重新訓練一次
KNN_K = 10
RNR_RADIUS_QUANTILES = [0.02, 0.05, 0.10, 0.20, 0.30]   # 半徑候選：訓練集兩兩距離的分位數
RANDOM_STATE = 42

# ── Phase 3：解釋 ──────────────────────────────────────────────────────────────
SHAP_BACKGROUND = 20
SHAP_NSAMPLES = 200
TOP_ANALOGUES = 3


def label_of(score: float) -> str:
    for threshold, label in LABEL_BINS:
        if score >= threshold:
            return label
    return LABEL_BINS[-1][1]
