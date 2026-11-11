NIFTY QUANT LAB v2.3 — BTC PDHL UTC BASELINE

BASE VERSION
- Built from the uploaded Nifty Quant Lab v2.1 app.py and btc_module.py.
- Preserves the existing Nifty PDHL dashboard, trailing-stop analyzer, Kite Connect integration, BTC EMA/Stoch RSI research, BTC optimizer, and Telegram alert code.

NEW BTC PDHL MODULE
- Separate BTC PDHL baseline module using Delta Exchange India BTCUSD 5-minute candles.
- UTC calendar day boundary: 00:00 to 23:59 UTC.
- PDH = previous UTC day's maximum candle body high (max(open, close)).
- PDL = previous UTC day's minimum candle body low (min(open, close)).
- Breakout confirmation choices: strong candle OR two consecutive closes; strong candle only; two consecutive closes only.
- Signal candle close confirmation; entry at next 5-minute candle open.
- Stop = signal candle low for LONG / high for SHORT.
- Target defaults to 3.5R; maximum one trade per UTC day.
- Position is closed at UTC day end; if stop and target both hit in one candle, stop is assumed first.
- Fee and slippage are optional estimates in basis points per side; defaults are zero and must be set to match the actual BTC product/order type.
- Trade log, equity-in-R chart, summary metrics and CSV export.
- Does not place orders and does not alter the existing BTC EMA/Stoch RSI module.

IMPORTANT BASELINE LIMITATIONS
- The BTC PDHL implementation is a clean baseline, not a claim that every Nifty-specific trap, gap-reversal, retest, or India-session rule transfers to 24/7 BTC.
- BTC signal candle body-size and wick filters are not parameter-optimized in this baseline. The strong-candle definition requires directional candle body and the breakout-side wick no larger than the body.
- Historical results depend on Delta's available candle history and user-selected fee/slippage assumptions.
- The app has not been deployed to the user's Streamlit Cloud account or tested against a full live Delta download in this environment.

DEPLOY
1. Extract the ZIP.
2. Upload app.py, btc_module.py, and btc_pdhl_module.py into the same GitHub repository used by the current Streamlit app. Replace app.py and btc_module.py with these copies; add btc_pdhl_module.py.
3. Keep the existing requirements.txt and Streamlit Cloud Secrets unchanged.
4. Commit changes and wait for Streamlit Cloud redeployment.
5. Open the app and find “BTC PDHL Baseline — UTC day” below the existing BTC Quant Lab section.
6. Start with a shorter date range (e.g. 30–90 days) to confirm the data download and backtest before fetching a multi-year 5-minute dataset.

FILES
- app.py: full application, based on uploaded v2.1
- btc_module.py: existing BTC EMA/Stoch RSI module
- btc_pdhl_module.py: new BTC PDHL UTC baseline module
