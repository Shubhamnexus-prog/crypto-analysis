# CryptoPulse: Real-Time Crypto Analytics Dashboard (Django + ML)

A full-stack capstone that turns the `Bitcoin_Price_Predication.ipynb` notebook into a production-style web app:
live prices, interactive charts, risk analytics and a 10-model Bitcoin forecasting engine.

## Features
- **Live ticker** for BTC, ETH, USDT, BNB, SOL, XRP: price, 24h change, sparkline, auto-refresh every 15s with flash animation
- **12+ interactive charts**: 24h intraday, price with SMA20/50 and Bollinger bands, volume, RSI(14), multi-coin performance comparison, rolling volatility, drawdown, return histogram, correlation heatmap (returns or prices)
- **Risk table**: total return, annualised volatility, Sharpe, max drawdown, best/worst day
- **Your pretrained model, live**: the notebook's `random_forest_model.pkl` is served by Django. Every 15s it predicts BTC from live USDT/BNB price and volume and compares with the real BTC price (rolling chart), plus a what-if form and a history backtest
- **ML forecast**: 10 regressors (Linear, Ridge, Lasso, ElasticNet, SVR, Decision Tree, Random Forest, Gradient Boosting, KNN, MLP) benchmarked against a naive baseline, with next-day BTC forecast, actual vs predicted plot and feature importances
- **JSON REST API**, caching layer, graceful fallbacks, offline unit tests, Docker support

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py runserver
```
Open http://127.0.0.1:8000 (internet needed for Yahoo Finance data).

```bash
python manage.py test                                  # runs offline with synthetic data
docker build -t cryptopulse . && docker run -p 8000:8000 cryptopulse
```

## How it works (end to end)
```
 Yahoo Finance --(yfinance)--> services/market.py --cache 15s / 1h--> services/analytics.py  (RSI, SMA, volatility, correlation)
                                      |                                services/ml.py         (10 models, next-day forecast)
                                      |                                services/pretrained.py (your .pkl model)
                                      v                                         |
                               Django views (JSON API)  <-------------------------+
                                      ^
 Browser: Chart.js polls /api/live/ and /api/pretrained/live/ every 15s and redraws charts
```
1. **Data:** `market.py` downloads 5 years of daily candles (history) and 24h of 5-minute candles (live) for 6 coins, in parallel, and caches them so the dashboard stays fast.
2. **Charts:** the browser calls the JSON APIs, and Chart.js draws the graphs. The ticker and live charts refresh themselves every 15 seconds.
3. **Your pickle model:** `pretrained.py` loads `dashboard/ml_models/random_forest_model.pkl` once. For each request it takes USDT close/volume and BNB close/volume, scales them to 0-1, and the 100-tree forest returns a BTC price.
4. **Next-day forecast:** `ml.py` builds lag features, trains 10 models on the first 80% of days, tests on the last 20% and compares them with a naive baseline.

### About the pickle files
| File | What it is |
|---|---|
| `random_forest_model.pkl` | RandomForestRegressor (100 trees), 4 inputs, trained on data scaled to 0-1 |
| `scaler.pkl` | Was fit on already-scaled data (min 0, max 1), so it does nothing. **Not used.** |
| `feature_ranges.json` | The original training min/max rebuilt from the notebook's `data.describe()`. This is the scaling the model really needs |

The original Streamlit `app.py` passed raw values straight to the model and returned the same ~114,571 for every input. The Django version scales inputs correctly (unit tests cover this).

**Limits to know:** it is a nowcast (BTC from same-day USDT/BNB), not a forecast. Random forests cannot predict outside prices seen in training, so the dashboard warns when an input leaves the training range. The notebook used a random split, so the backtest is optimistic.

To retrain with your own data, save a fitted `MinMaxScaler` and update `feature_ranges.json` (or save a scikit-learn `Pipeline` in one pickle). Only load pickle files you created yourself.

## Architecture
```
Browser (Chart.js, polls every 15s)
   |  JSON
Django views  ->  services/market.py     yfinance + cache (15s live, 1h history, parallel fetch, fallbacks)
              ->  services/analytics.py  RSI, SMA, Bollinger, volatility, drawdown, correlation
              ->  services/ml.py         feature engineering, 10 models, time-series evaluation
```
| Endpoint | Purpose |
|---|---|
| `/api/live/` | latest quote + sparkline for all coins |
| `/api/intraday/<SYM>/` | last 24h of 5-minute candles |
| `/api/indicators/<SYM>/?days=90` | price, SMAs, Bollinger, RSI, volume, drawdown, volatility, histogram, stats |
| `/api/compare/?days=90` | normalised performance, volatility, correlations, risk table |
| `/api/predict/` | model metrics, best model, next-day forecast |
| `/api/pretrained/live/` | live BTC nowcast from the pickled Random Forest vs real price |
| `/api/pretrained/predict/?usdt_close=&usdt_volume=&bnb_close=&bnb_volume=` | what-if prediction |
| `/api/pretrained/backtest/?days=365` | pickled model predictions vs actual BTC over history |
| `/health/` | health check |

## What I improved over the original notebook
| Notebook | This project |
|---|---|
| Random `train_test_split` on time series (look-ahead leakage) | Chronological 80/20 split |
| Predicted BTC close from same-day other-coin prices | Predicts next-day return from lagged features (no leakage; unit test enforces it) |
| Static matplotlib plots | Live interactive web dashboard |
| No benchmark | Compared with naive persistence baseline, plus MAE/MAPE/R²/directional accuracy |

Honest finding worth discussing in interviews: next-day crypto returns are close to a random walk, so most models land near the naive baseline. The value is in the rigorous evaluation, not in a magic predictor.

## Resume bullets (copy/edit)
- Built **CryptoPulse**, a real-time cryptocurrency analytics platform using **Django, pandas, scikit-learn and Chart.js**, serving 5 JSON APIs and 12+ interactive visualisations (live ticker, RSI, Bollinger bands, volatility, drawdown, correlation heatmap).
- Engineered a time-series forecasting pipeline benchmarking **10 ML models** against a naive baseline with chronological validation; removed look-ahead leakage present in the original notebook and enforced it with unit tests.
- Productionised a pickled scikit-learn Random Forest behind a Django REST API with live inference; diagnosed that the saved scaler was an identity transform (original Streamlit app returned a constant prediction), rebuilt the scaling from training statistics and added regression tests.
- Designed a caching and fault-tolerant data layer (parallel Yahoo Finance fetch, TTL cache, intraday-to-daily fallback), cutting upstream calls and keeping the UI responsive; containerised with **Docker + Gunicorn + WhiteNoise**.

## Ideas to extend
Binance WebSocket + Django Channels for tick-level streaming, user watchlists/auth, price alerts by email, LSTM/Prophet models, PostgreSQL + Celery for scheduled data snapshots, deploy on Render/Railway.

*Educational project. Not financial advice.*
