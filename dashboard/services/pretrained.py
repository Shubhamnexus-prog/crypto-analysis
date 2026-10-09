"""Serve the notebook's pre-trained Random Forest (random_forest_model.pkl).

The model maps 4 same-day features -> BTC close:
    USDT close, USDT volume, BNB close, BNB volume       (chosen by SelectKBest in the notebook)

It is a *nowcast* (what BTC "should" trade at given USDT/BNB), not a next-day forecast.

Scaling note
------------
The model was trained on inputs scaled to [0, 1]. The saved ``scaler.pkl`` was accidentally fit on
already-scaled data (min=0, max=1 -> identity), so the original scaling is rebuilt from the
dataset statistics stored in ``ml_models/feature_ranges.json``.

Security note: pickle can execute code on load. Only load model files you created yourself.
"""
from __future__ import annotations

import json
import logging
import math
import pickle
import warnings
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.exceptions import InconsistentVersionWarning
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from . import analytics, market

log = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).resolve().parent.parent / "ml_models"
FEATURES = ["USDT_Close", "USDT_Volume", "BNB_Close", "BNB_Volume"]
LABELS = {
    "USDT_Close": "USDT close ($)",
    "USDT_Volume": "USDT volume ($)",
    "BNB_Close": "BNB close ($)",
    "BNB_Volume": "BNB volume ($)",
}


class ModelError(Exception):
    """Model file missing or unusable."""


@lru_cache(maxsize=1)
def _load():
    path = MODEL_DIR / "random_forest_model.pkl"
    ranges_path = MODEL_DIR / "feature_ranges.json"
    if not path.exists() or not ranges_path.exists():
        raise ModelError("Model files not found in dashboard/ml_models/")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with open(path, "rb") as fh:
            model = pickle.load(fh)  # trusted, user-supplied file
    mismatch = any(issubclass(w.category, InconsistentVersionWarning) for w in caught)
    if mismatch:
        log.warning("random_forest_model.pkl was saved with a different scikit-learn version.")
    ranges = json.loads(ranges_path.read_text())
    lo = np.array([ranges["features"][f]["min"] for f in FEATURES], dtype=float)
    hi = np.array([ranges["features"][f]["max"] for f in FEATURES], dtype=float)
    if getattr(model, "n_features_in_", 4) != len(FEATURES):
        raise ModelError("Model feature count does not match expected 4 features")
    return model, lo, hi, ranges, mismatch


def meta() -> dict:
    model, lo, hi, ranges, mismatch = _load()
    return {
        "type": type(model).__name__,
        "n_trees": len(getattr(model, "estimators_", [])),
        "features": FEATURES,
        "labels": LABELS,
        "ranges": {f: {"min": float(a), "max": float(b)} for f, a, b in zip(FEATURES, lo, hi)},
        "range_source": ranges.get("source"),
        "version_mismatch": mismatch,
    }


def scale(X: np.ndarray) -> np.ndarray:
    _, lo, hi, _, _ = _load()
    return (X - lo) / (hi - lo)


def predict_array(X: np.ndarray) -> np.ndarray:
    model = _load()[0]
    return model.predict(scale(np.asarray(X, dtype=float)))


def predict_one(values: dict) -> dict:
    """values: {USDT_Close, USDT_Volume, BNB_Close, BNB_Volume} (raw units)."""
    row = []
    for f in FEATURES:
        try:
            v = float(values[f])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Missing or invalid value for {f}")
        if not math.isfinite(v) or v < 0:
            raise ValueError(f"{f} must be a non-negative number")
        row.append(v)
    X = np.array([row])
    scaled = scale(X)[0]
    pred = float(predict_array(X)[0])
    in_range = {f: bool(0 <= s <= 1) for f, s in zip(FEATURES, scaled)}
    rng = meta()["ranges"]
    warns = []
    for f, ok, v in zip(FEATURES, in_range.values(), row):
        if not ok:
            side = "above" if v > rng[f]["max"] else "below"
            warns.append(
                f"{LABELS[f]} is {side} the range the model was trained on "
                f"({rng[f]['min']:,.4g} to {rng[f]['max']:,.4g}). Random forests cannot extrapolate, "
                "so the prediction is capped at the nearest training value."
            )
    return {
        "inputs": dict(zip(FEATURES, row)),
        "scaled": dict(zip(FEATURES, [round(float(s), 4) for s in scaled])),
        "in_range": in_range,
        "predicted_btc": round(pred, 2),
        "warnings": warns,
    }


# --------------------------------------------------------------------------- live
def _volume_24h(symbol: str, quote: dict, frames: dict | None) -> tuple[float, str]:
    """24h volume from live candles; falls back to the last full day if the feed looks broken."""
    vol = float(quote["volume"])
    df = None if frames is None else frames.get(symbol)
    if df is None or len(df) < 9:
        return vol, "live 24h"
    ref = float(df["Volume"].iloc[-8:-1].median())
    if quote.get("source") == "intraday" and ref > 0 and vol >= 0.2 * ref:
        return vol, "live 24h"
    return float(df["Volume"].iloc[-2]), "last full day"


def live_nowcast() -> dict:
    quotes = {q["symbol"]: q for q in market.get_live_quotes()}
    for need in ("BTC", "USDT", "BNB"):
        if need not in quotes:
            raise market.DataUnavailable(f"Live {need} quote unavailable")
    try:
        frames = market.get_history()
    except market.DataUnavailable:
        frames = None
    usdt_v, usdt_src = _volume_24h("USDT", quotes["USDT"], frames)
    bnb_v, bnb_src = _volume_24h("BNB", quotes["BNB"], frames)
    values = {
        "USDT_Close": quotes["USDT"]["price"],
        "USDT_Volume": usdt_v,
        "BNB_Close": quotes["BNB"]["price"],
        "BNB_Volume": bnb_v,
    }
    res = predict_one(values)
    actual = float(quotes["BTC"]["price"])
    pred = res["predicted_btc"]
    res.update(
        actual_btc=round(actual, 2),
        diff=round(pred - actual, 2),
        diff_pct=round((pred / actual - 1) * 100, 2),
        volume_source={"USDT": usdt_src, "BNB": bnb_src},
        as_of=quotes["BTC"]["as_of"],
        model=meta(),
    )
    return res


# --------------------------------------------------------------------------- backtest
def _metrics(actual: pd.Series, pred: np.ndarray) -> dict:
    return {
        "rmse": round(float(np.sqrt(mean_squared_error(actual, pred))), 2),
        "mae": round(float(mean_absolute_error(actual, pred)), 2),
        "mape": round(float(np.mean(np.abs((actual.values - pred) / actual.values)) * 100), 2),
        "r2": round(float(r2_score(actual, pred)), 4),
    }


def backtest(frames: dict[str, pd.DataFrame], days: int) -> dict:
    for need in ("BTC", "USDT", "BNB"):
        if need not in frames:
            raise market.DataUnavailable(f"{need} history unavailable")
    close, vol = analytics.close_frame(frames), analytics.volume_frame(frames)
    X = pd.DataFrame(
        {
            "USDT_Close": close["USDT"],
            "USDT_Volume": vol["USDT"],
            "BNB_Close": close["BNB"],
            "BNB_Volume": vol["BNB"],
        }
    )
    df = pd.concat([X, close["BTC"].rename("actual")], axis=1).dropna()
    df["pred"] = predict_array(df[FEATURES].values)
    view = df.tail(days)
    scaled = scale(view[FEATURES].values)
    in_range = float(np.mean(((scaled >= 0) & (scaled <= 1)).all(axis=1)) * 100)
    recent = df.tail(90)
    return {
        "dates": analytics.dates(view.index),
        "actual": analytics.clean(view["actual"], 2),
        "predicted": analytics.clean(view["pred"], 2),
        "metrics_window": _metrics(view["actual"], view["pred"].values),
        "metrics_recent_90d": _metrics(recent["actual"], recent["pred"].values),
        "in_range_pct": round(in_range, 1),
        "note": (
            "The notebook trained this model with a random split, so part of this history was seen "
            "during training. Treat the fit as optimistic; the live card is the honest test."
        ),
    }
