"""Market data layer: Yahoo Finance (via yfinance) + Django cache.

Historical daily data mirrors the notebook (5y of BTC/ETH/USDT/BNB, extended with SOL & XRP).
Intraday 5-minute candles power the "real-time" ticker.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yfinance as yf
from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)

COINS = {
    "BTC": {"name": "Bitcoin", "ticker": "BTC-USD", "color": "#f7931a"},
    "ETH": {"name": "Ethereum", "ticker": "ETH-USD", "color": "#627eea"},
    "USDT": {"name": "Tether", "ticker": "USDT-USD", "color": "#26a17b"},
    "BNB": {"name": "BNB", "ticker": "BNB-USD", "color": "#f3ba2f"},
    "SOL": {"name": "Solana", "ticker": "SOL-USD", "color": "#9945ff"},
    "XRP": {"name": "XRP", "ticker": "XRP-USD", "color": "#00aae4"},
}


class DataUnavailable(Exception):
    """Raised when the upstream provider returns nothing usable."""


# --------------------------------------------------------------------------- raw downloads
def _download_history(ticker: str, period: str = "5y") -> pd.DataFrame:
    df = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
    return df


def _download_intraday(ticker: str) -> pd.DataFrame:
    return yf.Ticker(ticker).history(period="2d", interval="5m", auto_adjust=True)


def _tidy_daily(df: pd.DataFrame) -> pd.DataFrame:
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df = df[~df.index.duplicated(keep="last")].dropna(subset=["Close"])
    return df.sort_index()


# --------------------------------------------------------------------------- history
def get_history() -> dict[str, pd.DataFrame]:
    """5y daily OHLCV for every coin (cached; fetched in parallel)."""
    key = "history:v1"
    cached = cache.get(key)
    if cached is not None:
        return cached

    def work(item):
        symbol, meta = item
        try:
            df = _download_history(meta["ticker"])
            if df is None or df.empty:
                raise DataUnavailable(symbol)
            return symbol, _tidy_daily(df)
        except Exception as exc:  # one failing coin must not kill the dashboard
            log.warning("History fetch failed for %s: %s", symbol, exc)
            return symbol, None

    with ThreadPoolExecutor(max_workers=len(COINS)) as pool:
        results = dict(pool.map(work, COINS.items()))
    frames = {s: df for s, df in results.items() if df is not None}
    if not frames:
        raise DataUnavailable("No historical data could be downloaded (check internet / Yahoo Finance).")
    cache.set(key, frames, settings.HISTORY_CACHE_TTL)
    return frames


# --------------------------------------------------------------------------- intraday / live
def get_intraday(symbol: str) -> pd.DataFrame:
    """Last 24h of 5-minute candles for a coin (cached for LIVE_CACHE_TTL seconds)."""
    if symbol not in COINS:
        raise KeyError(symbol)
    key = f"intraday:{symbol}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    df = _download_intraday(COINS[symbol]["ticker"])
    if df is None or df.empty:
        raise DataUnavailable(f"No intraday data for {symbol}")
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df.index = idx.tz_convert("UTC")
    df = df.dropna(subset=["Close"])
    df = df[df.index >= df.index[-1] - pd.Timedelta(hours=24)]
    cache.set(key, df, settings.LIVE_CACHE_TTL)
    return df


def _quote_from_intraday(symbol: str) -> dict:
    df = get_intraday(symbol)
    price, first = float(df["Close"].iloc[-1]), float(df["Open"].iloc[0])
    step = max(1, len(df) // 48)
    return {
        "price": price,
        "change_24h": (price / first - 1) * 100 if first else 0.0,
        "high": float(df["High"].max()),
        "low": float(df["Low"].min()),
        "volume": float(df["Volume"].sum()),
        "spark": [round(float(v), 6) for v in df["Close"].iloc[::step]],
        "as_of": df.index[-1].isoformat(),
    }


def _quote_from_daily(symbol: str) -> dict:
    df = get_history()[symbol].tail(8)
    price, prev = float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2])
    return {
        "price": price,
        "change_24h": (price / prev - 1) * 100,
        "high": float(df["High"].iloc[-1]),
        "low": float(df["Low"].iloc[-1]),
        "volume": float(df["Volume"].iloc[-1]),
        "spark": [round(float(v), 6) for v in df["Close"]],
        "as_of": df.index[-1].isoformat(),
    }


def get_live_quotes() -> list[dict]:
    """Latest quote for every coin. Falls back to daily data if intraday is unavailable."""

    def work(item):
        symbol, meta = item
        try:
            q = _quote_from_intraday(symbol)
            q["source"] = "intraday"
        except Exception as exc:
            log.warning("Intraday failed for %s (%s); trying daily", symbol, exc)
            try:
                q = _quote_from_daily(symbol)
                q["source"] = "daily"
            except Exception:
                return None
        return {"symbol": symbol, "name": meta["name"], "color": meta["color"], **q}

    with ThreadPoolExecutor(max_workers=len(COINS)) as pool:
        quotes = [q for q in pool.map(work, COINS.items()) if q]
    if not quotes:
        raise DataUnavailable("Live quotes are unavailable right now.")
    return quotes
