NIFTY QUANT LAB v1.9 — BTC + NIFTY

WHAT'S NEW
- Separate BTC Quant Lab module alongside the existing Nifty PDH/PDL module.
- Delta Exchange India public BTCUSD historical candle download, 15-minute timeframe.
- User's BTC direction rules:
    LONG: EMA fast > EMA slow AND Stoch RSI %K > %D
    SHORT: EMA fast < EMA slow AND Stoch RSI %K < %D
- Signals are evaluated after a completed candle. Backtest entry is next candle open.
- Stochastic RSI lengths/smoothing, EMA lengths, ATR stop multiple and reward:risk are configurable.
- Randomized optimizer evaluates EMA, Stoch RSI, ATR stop and target combinations.
- Chronological 60% training / 20% validation / 20% untouched final test split. Candidate ranking is based on validation; final test is reported separately.
- Estimated fees and slippage are included in BTC backtest P&L (configurable bps per side).
- BTC dashboard, trade log, optimizer ranking and CSV exports are separate from Nifty.
- Optional BTC live polling every 30 seconds checks the latest completed 15-minute Delta candle and sends Telegram alerts. No orders are placed.

BTC STRATEGY ASSUMPTIONS
- To avoid repeated alerts while a condition remains true, the system emits a signal only when the LONG or SHORT condition changes from false to true.
- Entry is next candle open after the signal candle closes.
- Initial stop is ATR(14) multiplied by the selected SL ATR factor; target is selected R:R.
- If stop and target are both touched within one candle, stop is assumed first (conservative).
- Opposite signal closes an open trade at that candle close. Otherwise position exits at end of available data.
- Backtest reports BTCUSD price-point P&L, not INR P&L and not option premium P&L.
- Fee and slippage assumptions must be changed to match the user's actual Delta product, order type and fee tier. These are estimates, not a live fee quote.

DELTA DATA
- Public endpoint: https://api.india.delta.exchange/v2/history/candles
- Symbol: BTCUSD; resolution: 15m.
- API supports up to 2,000 candles per request; downloader batches the selected date range.
- Public historical data does not require a Delta trading API key.
- Live mode currently uses periodic REST polling of recent candles (30-second app fragment), not a persistent WebSocket daemon. The Streamlit app must remain running/open for live alerts. It is alert-only.

TELEGRAM
Add the existing secrets in Streamlit Cloud → App settings → Secrets:
TELEGRAM_BOT_TOKEN = "your-existing-bot-token"
TELEGRAM_CHAT_ID = "your-chat-id"
The same configured Telegram bot/chat can receive Nifty and BTC alerts. Keep secrets out of GitHub and source files.

DEPLOY
1. Download and extract this ZIP.
2. Upload the files to the same GitHub repository used by the current Nifty Quant Lab (replace app.py and add btc_module.py; keep requirements.txt).
3. Commit and push.
4. Streamlit Community Cloud should redeploy from the repository.
5. Open BTC Quant Lab, fetch BTC history, run the default backtest, then run optimizer.
6. Review training, validation and final-test results. Do not assume the optimizer's top candidate will remain profitable.
7. Enable BTC live polling only after confirming the Telegram test setup and chosen parameters.

IMPORTANT RESEARCH CAUTIONS
- Optimization can overfit. Prefer positive validation and untouched final-test expectancy, adequate trade count, manageable drawdown, and stability across nearby parameter sets.
- The 20% final test should remain untouched while tuning. If you change parameters based on final-test results, it is no longer an untouched test.
- The app does not place orders or need Delta trading permissions.
