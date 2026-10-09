import json
import logging

from django.conf import settings
from django.core.cache import cache
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from .services import analytics, market, ml, pretrained

log = logging.getLogger(__name__)

RANGES = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "3Y": 1095, "5Y": 1825}


def _days(request, default=90) -> int:
    raw = request.GET.get("days", default)
    try:
        return max(7, min(int(raw), 1900))
    except (TypeError, ValueError):
        return default


def _error(exc: Exception, status: int = 503):
    log.exception("API error")
    return JsonResponse({"error": str(exc) or exc.__class__.__name__}, status=status)


def index(request):
    return render(
        request,
        "dashboard/index.html",
        {
            "coins_json": {k: {"name": v["name"], "color": v["color"]} for k, v in market.COINS.items()},
            "ranges": RANGES,
            "refresh_ms": 15000,
        }
    )


@require_GET
def health(request):
    return JsonResponse({"status": "ok", "time": timezone.now().isoformat()})


@require_GET
def api_live(request):
    try:
        return JsonResponse({"updated": timezone.now().isoformat(), "coins": market.get_live_quotes()})
    except market.DataUnavailable as exc:
        return _error(exc)


@require_GET
def api_intraday(request, symbol):
    symbol = symbol.upper()
    if symbol not in market.COINS:
        raise Http404("Unknown coin")
    try:
        df = market.get_intraday(symbol)
    except market.DataUnavailable as exc:
        return _error(exc)
    return JsonResponse(
        {
            "symbol": symbol,
            "times": [t.strftime("%H:%M") for t in df.index],
            "close": analytics.clean(df["Close"], 6),
            "volume": analytics.clean(df["Volume"], 2),
        }
    )


@require_GET
def api_indicators(request, symbol):
    symbol = symbol.upper()
    if symbol not in market.COINS:
        raise Http404("Unknown coin")
    try:
        frames = market.get_history()
        if symbol not in frames:
            raise market.DataUnavailable(f"No data for {symbol}")
        return JsonResponse({"symbol": symbol, **analytics.coin_indicators(frames[symbol], _days(request))})
    except market.DataUnavailable as exc:
        return _error(exc)


@require_GET
def api_compare(request):
    try:
        return JsonResponse(analytics.compare(market.get_history(), _days(request)))
    except market.DataUnavailable as exc:
        return _error(exc)


@require_GET
def api_predict(request):
    key = "ml:btc:v1"
    result = cache.get(key)
    try:
        if result is None:
            result = ml.run(market.get_history())
            cache.set(key, result, settings.HISTORY_CACHE_TTL)
        return JsonResponse(result)
    except (market.DataUnavailable, ValueError) as exc:
        return _error(exc)


@require_GET
def api_pretrained_live(request):
    """Live nowcast from the notebook's pickled Random Forest."""
    try:
        return JsonResponse(pretrained.live_nowcast())
    except (market.DataUnavailable, pretrained.ModelError) as exc:
        return _error(exc)


@require_GET
def api_pretrained_predict(request):
    """What-if prediction (same inputs as the old Streamlit app)."""
    params = {
        "USDT_Close": request.GET.get("usdt_close"),
        "USDT_Volume": request.GET.get("usdt_volume"),
        "BNB_Close": request.GET.get("bnb_close"),
        "BNB_Volume": request.GET.get("bnb_volume"),
    }
    try:
        return JsonResponse(pretrained.predict_one(params))
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except pretrained.ModelError as exc:
        return _error(exc)


@require_GET
def api_pretrained_backtest(request):
    try:
        return JsonResponse(pretrained.backtest(market.get_history(), _days(request, 365)))
    except (market.DataUnavailable, pretrained.ModelError) as exc:
        return _error(exc)
