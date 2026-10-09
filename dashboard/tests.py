"""Offline tests: Yahoo Finance is replaced with deterministic synthetic data."""
from unittest import mock

import numpy as np
import pandas as pd
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .services import analytics, market, ml, pretrained

START_PRICE = {"BTC": 30000, "ETH": 2000, "USDT": 1.0, "BNB": 300, "SOL": 40, "XRP": 0.5}


def fake_history(ticker, period="5y"):
    rng = np.random.default_rng(abs(hash(ticker)) % 2**32)
    sym = ticker.split("-")[0]
    n = 1500
    idx = pd.date_range(end=pd.Timestamp.utcnow().normalize(), periods=n, freq="D", tz="UTC")
    vol = 0.0005 if sym == "USDT" else 0.03
    close = START_PRICE[sym] * np.exp(np.cumsum(rng.normal(0.0004, vol, n)))
    return pd.DataFrame({"Open": close * 0.99, "High": close * 1.02, "Low": close * 0.98,
                         "Close": close, "Volume": rng.uniform(1e9, 5e9, n)}, index=idx)


def fake_intraday(ticker):
    rng = np.random.default_rng(1)
    sym = ticker.split("-")[0]
    idx = pd.date_range(end=pd.Timestamp.utcnow().floor("5min"), periods=576, freq="5min", tz="UTC")
    close = START_PRICE[sym] * np.exp(np.cumsum(rng.normal(0, 0.001, len(idx))))
    return pd.DataFrame({"Open": close, "High": close * 1.001, "Low": close * 0.999,
                         "Close": close, "Volume": rng.uniform(1e5, 1e6, len(idx))}, index=idx)


@mock.patch.object(market, "_download_history", fake_history)
@mock.patch.object(market, "_download_intraday", fake_intraday)
class DashboardTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_index_renders(self):
        r = self.client.get(reverse("index"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "CryptoPulse")

    def test_health(self):
        self.assertEqual(self.client.get(reverse("health")).json()["status"], "ok")

    def test_live(self):
        data = self.client.get(reverse("api-live")).json()
        self.assertEqual({c["symbol"] for c in data["coins"]}, set(market.COINS))
        self.assertTrue(all(c["price"] > 0 and c["spark"] for c in data["coins"]))

    def test_live_falls_back_to_daily(self):
        with mock.patch.object(market, "_download_intraday", side_effect=RuntimeError("boom")):
            data = self.client.get(reverse("api-live")).json()
        self.assertTrue(all(c["source"] == "daily" for c in data["coins"]))

    def test_intraday_and_unknown_coin(self):
        self.assertEqual(self.client.get(reverse("api-intraday", args=["btc"])).status_code, 200)
        self.assertEqual(self.client.get(reverse("api-intraday", args=["DOGE"])).status_code, 404)

    def test_indicators(self):
        d = self.client.get(reverse("api-indicators", args=["ETH"]) + "?days=90").json()
        self.assertEqual(len(d["dates"]), 90)
        self.assertEqual(len(d["close"]), len(d["rsi"]))
        valid_rsi = [v for v in d["rsi"] if v is not None]
        self.assertTrue(all(0 <= v <= 100 for v in valid_rsi))
        self.assertLessEqual(d["stats"]["max_drawdown"], 0)

    def test_bad_days_param_does_not_crash(self):
        self.assertEqual(self.client.get(reverse("api-indicators", args=["BTC"]) + "?days=abc").status_code, 200)

    def test_compare(self):
        d = self.client.get(reverse("api-compare") + "?days=180").json()
        n = len(d["symbols"])
        self.assertEqual(len(d["corr_returns"]), n)
        self.assertAlmostEqual(d["corr_returns"][0][0], 1.0, places=2)
        self.assertEqual(len(d["stats"]), n)

    def test_predict(self):
        d = self.client.get(reverse("api-predict")).json()
        self.assertEqual(len(d["metrics"]), 11)  # 10 models + naive baseline
        self.assertIn(d["best_model"], [m["model"] for m in d["metrics"]])
        self.assertGreater(d["forecast"]["predicted_close"], 0)
        self.assertGreater(d["train_rows"], d["test_rows"])

    def test_upstream_failure_returns_503_json(self):
        with mock.patch.object(market, "_download_history", side_effect=RuntimeError("down")):
            r = self.client.get(reverse("api-compare"))
        self.assertEqual(r.status_code, 503)
        self.assertIn("error", r.json())


class AnalyticsUnitTests(TestCase):
    def test_rsi_extremes(self):
        up = pd.Series(np.arange(1, 60, dtype=float))
        self.assertGreater(analytics.rsi(up).iloc[-1], 99)

    def test_ml_has_no_lookahead(self):
        frames = {s: fake_history(f"{s}-USD") for s in ("BTC", "ETH")}
        X, y, price = ml.build_dataset(frames)
        # target at t must equal the t+1 return, never information from t or earlier
        expected = (price.shift(-1) / price - 1) * 100
        pd.testing.assert_series_equal(y, expected)


@mock.patch.object(market, "_download_history", fake_history)
@mock.patch.object(market, "_download_intraday", fake_intraday)
class PretrainedModelTests(TestCase):
    """Uses the real random_forest_model.pkl shipped in dashboard/ml_models/."""

    def setUp(self):
        cache.clear()

    # rows copied from the notebook's data.tail()/data.head() output (USDT c, USDT v, BNB c, BNB v) -> BTC
    KNOWN = [
        ([0.999790, 38186771396, 795.264099, 1195825218], 86480.30),
        ([0.999200, 94872002560, 740.679993, 1955062016], 82292.57),
        ([1.000046, 67391135102, 421.549469, 1404867667], 54968.22),
    ]

    def test_rebuilt_scaling_matches_notebook_rows(self):
        for row, actual in self.KNOWN:
            pred = pretrained.predict_array(__import__("numpy").array([row]))[0]
            self.assertLess(abs(pred / actual - 1), 0.08)  # within 8%

    def test_raw_unscaled_input_would_be_wrong(self):
        """Regression test for the old Streamlit bug (raw values bypass scaling)."""
        import numpy as np
        model = pretrained._load()[0]
        raw = model.predict(np.array([self.KNOWN[0][0], self.KNOWN[2][0]]))
        self.assertEqual(raw[0], raw[1])  # constant output regardless of input

    def test_predict_endpoint_and_validation(self):
        url = reverse("api-pm-predict")
        ok = self.client.get(url, {"usdt_close": 0.9992, "usdt_volume": 9.4e10, "bnb_close": 740, "bnb_volume": 1.9e9})
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(all(ok.json()["in_range"].values()))
        self.assertEqual(self.client.get(url, {"usdt_close": "abc"}).status_code, 400)
        self.assertEqual(self.client.get(url, {"usdt_close": -1, "usdt_volume": 1, "bnb_close": 1, "bnb_volume": 1}).status_code, 400)

    def test_out_of_range_input_is_flagged(self):
        d = self.client.get(reverse("api-pm-predict"), {"usdt_close": 1, "usdt_volume": 6e10, "bnb_close": 5000, "bnb_volume": 1.5e9}).json()
        self.assertFalse(d["in_range"]["BNB_Close"])
        self.assertTrue(d["warnings"])

    def test_live_endpoint(self):
        d = self.client.get(reverse("api-pm-live")).json()
        for key in ("predicted_btc", "actual_btc", "diff_pct", "inputs", "scaled", "model"):
            self.assertIn(key, d)
        self.assertEqual(d["model"]["n_trees"], 100)

    def test_backtest_endpoint(self):
        d = self.client.get(reverse("api-pm-backtest") + "?days=200").json()
        self.assertEqual(len(d["dates"]), 200)
        self.assertIn("r2", d["metrics_window"])
        self.assertIn("metrics_recent_90d", d)
