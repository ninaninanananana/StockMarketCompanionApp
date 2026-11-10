"""Phase 2 共用：狀態特徵、預測目標、RNR 模型（含找不到鄰居時的 KNN 備援）。"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, RegressorMixin
from sklearn.neighbors import KNeighborsRegressor, RadiusNeighborsRegressor
from sklearn.preprocessing import StandardScaler

from config import CLASSES, DIMENSIONS, KNN_K, RNR_RADIUS_QUANTILES, WINDOW

FEATURE_KINDS = ["level", "mean5", "mean20", "trend60", "vol20", "momentum"]
FEATURE_LABELS = {
    "level": "水準", "mean5": "5 日均", "mean20": "20 日均",
    "trend60": "60 日趨勢", "vol20": "波動度", "momentum": "動能",
}


def _slope(values: np.ndarray) -> float:
    m = ~np.isnan(values)
    if m.sum() < WINDOW * 2 // 3:
        return np.nan
    x = np.arange(len(values))[m]
    return float(np.polyfit(x, values[m], 1)[0])


def build_features(dims: pd.DataFrame) -> pd.DataFrame:
    """每個構面近 60 日 → 6 個狀態特徵（只用當日以前與當日資料）。"""
    feats = {}
    for d in DIMENSIONS:
        s = dims[f"D_{d}"]
        feats[f"{d}__level"] = s
        feats[f"{d}__mean5"] = s.rolling(5, min_periods=4).mean()
        feats[f"{d}__mean20"] = s.rolling(20, min_periods=15).mean()
        feats[f"{d}__trend60"] = s.rolling(WINDOW, min_periods=WINDOW * 2 // 3).apply(_slope, raw=True)
        feats[f"{d}__vol20"] = s.rolling(20, min_periods=15).std()
        feats[f"{d}__momentum"] = feats[f"{d}__mean5"] - feats[f"{d}__mean20"]
    return pd.DataFrame(feats, index=dims.index)


def feature_columns() -> list[str]:
    return [f"{d}__{k}" for d in DIMENSIONS for k in FEATURE_KINDS]


def dimension_of(feature: str) -> str:
    return feature.split("__")[0]


def tertile_thresholds(dt_train: pd.Series) -> tuple[float, float]:
    return float(dt_train.quantile(1 / 3)), float(dt_train.quantile(2 / 3))


def to_class(dt: np.ndarray | pd.Series, q1: float, q2: float) -> np.ndarray:
    dt = np.asarray(dt, dtype=float)
    return np.where(dt < q1, CLASSES[0], np.where(dt > q2, CLASSES[2], CLASSES[1]))


class NaiveZero(BaseEstimator, RegressorMixin):
    """Persistence：明天＝今天 → ΔT = 0。"""

    def fit(self, X, y):
        return self

    def predict(self, X):
        return np.zeros(len(X))


class RNRWithFallback(BaseEstimator, RegressorMixin):
    """
    Radius Neighbors Regressor（特徵先標準化）。
    - radius=None：用訓練期內部驗證（後 20%）從候選半徑中挑誤差最小者
    - 半徑內找不到鄰居時，改用 KNN（k=10），並記錄 neighbor_count=0
    """

    def __init__(self, radius: float | None = None, weights: str = "distance"):
        self.radius = radius
        self.weights = weights

    def _candidate_radii(self, Xs: np.ndarray) -> list[float]:
        from sklearn.metrics import pairwise_distances
        dist = pairwise_distances(Xs)
        upper = dist[np.triu_indices_from(dist, k=1)]
        return sorted({float(np.quantile(upper, q)) for q in RNR_RADIUS_QUANTILES})

    def _fit_core(self, Xs, y, radius):
        rnr = RadiusNeighborsRegressor(radius=radius, weights=self.weights)
        rnr.fit(Xs, y)
        knn = KNeighborsRegressor(n_neighbors=min(KNN_K, len(y)), weights=self.weights)
        knn.fit(Xs, y)
        return rnr, knn

    def _predict_core(self, rnr, knn, radius, Xs):
        dist, _ = rnr.radius_neighbors(Xs, radius=radius)
        counts = np.array([len(d) for d in dist])
        pred = np.empty(len(Xs))
        has = counts > 0
        if has.any():
            pred[has] = rnr.predict(Xs[has])
        if (~has).any():
            pred[~has] = knn.predict(Xs[~has])
        return pred, counts

    def fit(self, X, y):
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        self.scaler_ = StandardScaler().fit(X)
        Xs = self.scaler_.transform(X)
        radius = self.radius
        if radius is None:
            cut = int(len(y) * 0.8)
            best, best_err = None, np.inf
            for r in self._candidate_radii(Xs[:cut]):
                rnr, knn = self._fit_core(Xs[:cut], y[:cut], r)
                pred, _ = self._predict_core(rnr, knn, r, Xs[cut:])
                err = np.mean(np.abs(pred - y[cut:]))
                if err < best_err:
                    best, best_err = r, err
            radius = best
        self.radius_ = radius
        self.rnr_, self.knn_ = self._fit_core(Xs, y, radius)
        self.y_train_ = y
        return self

    def predict(self, X):
        Xs = self.scaler_.transform(np.asarray(X, dtype=float))
        pred, self.last_counts_ = self._predict_core(self.rnr_, self.knn_, self.radius_, Xs)
        return pred

    def neighbors(self, x_row) -> tuple[np.ndarray, np.ndarray]:
        """回傳 (訓練樣本索引, 距離)，依距離由近到遠。"""
        xs = self.scaler_.transform(np.asarray(x_row, dtype=float).reshape(1, -1))
        dist, ind = self.rnr_.radius_neighbors(xs, radius=self.radius_, sort_results=True)
        return ind[0], dist[0]
