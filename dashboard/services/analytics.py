"""Pure-pandas analytics: indicators, risk metrics, correlations."""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 365  # crypto trades 24/7


def clean(series: pd.Series | np.ndarray, digits: int = 6) -> list:
    """JSON-safe list (NaN/inf -> None)."""
    out = []
    for v in np.asarray(series, dtype="float64"):
        out.append(None if not np.isfinite(v) else round(float(v), digits))
    return out


def dates(index: pd.Index) -> list[str]:
    return [d.strftime("%Y-%m-%d") for d in index]


def close_frame(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.DataFrame({s: f["Close"] for s, f in frames.items()}).sort_index().ffill(limit=3)


def volume_frame(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.DataFrame({s: f["Volume"] for s, f in frames.items()}).sort_index()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    out = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    out[(loss == 0) & (gain > 0)] = 100.0  # no losses in window -> RSI is 100
    return out


def drawdown(close: pd.Series) -> pd.Series:
    return (close / close.cummax() - 1) * 100


def rolling_volatility(close: pd.Series, window: int = 30) -> pd.Series:
    return close.pct_change().rolling(window).std() * np.sqrt(TRADING_DAYS) * 100


def histogram(returns_pct: pd.Series, bins: int = 30) -> dict:
    r = returns_pct.dropna()
    if r.empty:
        return {"bins": [], "counts": []}
    lo, hi = np.percentile(r, [1, 99])
    if lo == hi:
        lo, hi = lo - 0.01, hi + 0.01
    counts, edges = np.histogram(r.clip(lo, hi), bins=bins, range=(lo, hi))
    centers = (edges[:-1] + edges[1:]) / 2
    return {"bins": [round(float(c), 3) for c in centers], "counts": [int(c) for c in counts]}


def coin_indicators(df: pd.DataFrame, days: int) -> dict:
    """Indicators computed on full history (so windows are warm) and sliced to `days`."""
    c = df["Close"]
    sma20, sma50 = c.rolling(20).mean(), c.rolling(50).mean()
    std20 = c.rolling(20).std()
    ret = c.pct_change() * 100
    full = pd.DataFrame(
        {
            "close": c,
            "sma20": sma20,
            "sma50": sma50,
            "bb_upper": sma20 + 2 * std20,
            "bb_lower": sma20 - 2 * std20,
            "rsi": rsi(c),
            "volume": df["Volume"],
            "drawdown": drawdown(c),
            "volatility": rolling_volatility(c),
        }
    )
    view = full.tail(days)
    payload = {"dates": dates(view.index)}
    for col in full.columns:
        payload[col] = clean(view[col], 4 if col != "close" else 6)
    payload["histogram"] = histogram(ret.tail(days))
    payload["stats"] = risk_stats(c.tail(days))
    return payload


def risk_stats(close: pd.Series) -> dict:
    close = close.dropna()
    if len(close) < 3:
        return {}
    r = close.pct_change().dropna()
    vol = r.std() * np.sqrt(TRADING_DAYS)
    ann_ret = r.mean() * TRADING_DAYS
    return {
        "price": float(close.iloc[-1]),
        "total_return": float((close.iloc[-1] / close.iloc[0] - 1) * 100),
        "volatility": float(vol * 100),
        "sharpe": float(ann_ret / vol) if vol > 0 else None,
        "max_drawdown": float(drawdown(close).min()),
        "best_day": float(r.max() * 100),
        "worst_day": float(r.min() * 100),
    }


def compare(frames: dict[str, pd.DataFrame], days: int) -> dict:
    close = close_frame(frames)
    view = close.tail(days)
    normalized = {s: clean((view[s] / view[s].dropna().iloc[0] - 1) * 100, 3) for s in view.columns}
    vol = {s: clean(rolling_volatility(close[s]).tail(days), 3) for s in close.columns}
    returns = view.pct_change().dropna(how="all")
    corr_r = returns.corr().round(3)
    corr_p = view.corr().round(3)
    stats = []
    for s in view.columns:
        st = risk_stats(view[s])
        if st:
            stats.append({"symbol": s, **{k: (None if v is None else round(v, 3)) for k, v in st.items()}})
    return {
        "dates": dates(view.index),
        "symbols": list(view.columns),
        "normalized": normalized,
        "volatility": vol,
        "corr_returns": corr_r.where(corr_r.notna(), None).values.tolist(),
        "corr_prices": corr_p.where(corr_p.notna(), None).values.tolist(),
        "stats": stats,
    }
