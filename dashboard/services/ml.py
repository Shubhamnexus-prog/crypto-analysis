"""Next-day BTC price forecasting.

Improvements over the original notebook
---------------------------------------
* Chronological train/test split (the notebook used a random split, which leaks future
  information in time-series data and inflates R^2).
* Features at day *t* predict the return on day *t+1* (the notebook regressed BTC close on
  same-day prices of other coins, which is not forecastable in practice).
* Models are compared against a naive "tomorrow = today" baseline.
* Metrics are reported on price level (RMSE/MAE/MAPE/R^2) plus directional accuracy.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import ElasticNet, Lasso, LinearRegression, Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.neighbors import KNeighborsRegressor
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVR
from sklearn.tree import DecisionTreeRegressor

from . import analytics

TARGET = "BTC"
TEST_FRACTION = 0.2


def _models() -> dict:
    return {
        "Linear Regression": LinearRegression(),
        "Ridge": Ridge(alpha=10.0),
        "Lasso": Lasso(alpha=0.01, max_iter=10000),
        "ElasticNet": ElasticNet(alpha=0.01, l1_ratio=0.5, max_iter=10000),
        "SVR": SVR(C=1.0, epsilon=0.1),
        "Decision Tree": DecisionTreeRegressor(max_depth=4, min_samples_leaf=10, random_state=0),
        "Random Forest": RandomForestRegressor(
            n_estimators=200, max_depth=6, min_samples_leaf=5, n_jobs=-1, random_state=0
        ),
        "Gradient Boosting": GradientBoostingRegressor(
            n_estimators=150, max_depth=2, learning_rate=0.05, subsample=0.8, random_state=0
        ),
        "KNN": KNeighborsRegressor(n_neighbors=15),
        "Neural Net (MLP)": MLPRegressor(
            hidden_layer_sizes=(32, 16), max_iter=500, early_stopping=True, random_state=0
        ),
    }


def build_dataset(frames: dict[str, pd.DataFrame], target: str = TARGET):
    close, vol = analytics.close_frame(frames), analytics.volume_frame(frames)
    X = pd.DataFrame(index=close.index)
    for s in close.columns:
        X[f"{s}_ret_1d"] = close[s].pct_change() * 100
        X[f"{s}_vol_chg"] = np.log1p(vol[s]).diff()
    t = close[target]
    ret = t.pct_change() * 100
    for lag in (2, 3, 5):
        X[f"{target}_ret_lag{lag}"] = ret.shift(lag - 1)
    X[f"{target}_ret_7d"] = t.pct_change(7) * 100
    X[f"{target}_sma7_ratio"] = t / t.rolling(7).mean() - 1
    X[f"{target}_sma21_ratio"] = t / t.rolling(21).mean() - 1
    X[f"{target}_rsi14"] = analytics.rsi(t)
    X[f"{target}_vol14"] = ret.rolling(14).std()
    X = X.replace([np.inf, -np.inf], np.nan)

    y = (t.shift(-1) / t - 1) * 100  # next-day return in %
    return X, y, t


def run(frames: dict[str, pd.DataFrame], target: str = TARGET) -> dict:
    if target not in frames:
        raise ValueError(f"{target} data missing")
    X, y, price = build_dataset(frames, target)
    valid = X.notna().all(axis=1)
    latest_X = X[valid].iloc[[-1]]  # most recent day -> forecast for tomorrow
    train_mask = valid & y.notna()
    Xd, yd, pd_price = X[train_mask], y[train_mask], price[train_mask]

    split = int(len(Xd) * (1 - TEST_FRACTION))
    if split < 100 or len(Xd) - split < 30:
        raise ValueError("Not enough data to train")
    X_tr, X_te = Xd.iloc[:split], Xd.iloc[split:]
    y_tr, y_te = yd.iloc[:split], yd.iloc[split:]
    p_te = pd_price.iloc[split:]
    actual_next = p_te * (1 + y_te / 100)

    def score(name: str, pred_ret: np.ndarray) -> dict:
        pred_price = p_te.values * (1 + pred_ret / 100)
        return {
            "model": name,
            "rmse": float(np.sqrt(mean_squared_error(actual_next, pred_price))),
            "mae": float(mean_absolute_error(actual_next, pred_price)),
            "mape": float(np.mean(np.abs((actual_next.values - pred_price) / actual_next.values)) * 100),
            "r2": float(r2_score(actual_next, pred_price)),
            "dir_acc": None if not np.any(pred_ret) else float(np.mean(np.sign(pred_ret) == np.sign(y_te.values)) * 100),
        }

    metrics = [score("Naive baseline", np.zeros(len(y_te)))]
    preds: dict[str, np.ndarray] = {}
    fitted = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for name, est in _models().items():
            pipe = make_pipeline(StandardScaler(), est)
            pipe.fit(X_tr, y_tr)
            preds[name] = pipe.predict(X_te)
            fitted[name] = pipe
            metrics.append(score(name, preds[name]))

    ranked = sorted(metrics[1:], key=lambda m: m["rmse"])
    best = ranked[0]["model"]

    # Refit best model on ALL data for tomorrow's forecast
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        final = make_pipeline(StandardScaler(), _models()[best]).fit(Xd, yd)
    pred_ret = float(final.predict(latest_X)[0])
    last_close = float(price.loc[latest_X.index[0]])

    # Feature importance from a random forest trained on the training window
    rf = fitted["Random Forest"].steps[-1][1]
    imp = sorted(zip(X.columns, rf.feature_importances_), key=lambda kv: -kv[1])[:8]

    tail = 120
    idx = X_te.index[-tail:]
    return {
        "target": target,
        "train_rows": int(len(X_tr)),
        "test_rows": int(len(X_te)),
        "split_date": X_te.index[0].strftime("%Y-%m-%d"),
        "metrics": [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()} for m in metrics],
        "best_model": best,
        "forecast": {
            "as_of": latest_X.index[0].strftime("%Y-%m-%d"),
            "last_close": round(last_close, 2),
            "predicted_return": round(pred_ret, 3),
            "predicted_close": round(last_close * (1 + pred_ret / 100), 2),
            "direction": "up" if pred_ret >= 0 else "down",
        },
        "test_series": {
            "dates": analytics.dates(idx + pd.Timedelta(days=1)),
            "actual": analytics.clean(actual_next.iloc[-tail:], 2),
            "predicted": analytics.clean(p_te.values[-tail:] * (1 + preds[best][-tail:] / 100), 2),
            "naive": analytics.clean(p_te.iloc[-tail:], 2),
        },
        "importances": [{"feature": f, "importance": round(float(v), 4)} for f, v in imp],
        "disclaimer": "Educational project. Crypto markets are highly volatile; this is not financial advice.",
    }
