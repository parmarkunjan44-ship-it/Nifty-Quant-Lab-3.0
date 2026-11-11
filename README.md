# Nifty Quant Lab v3.0 — Research Workbench

This release is a staged upgrade of the supplied v2.3 codebase. It retains the existing Kite Connect data center, live-signal/Telegram components, NIFTY optimizer/checkpoint code, BTC EMA/Stoch RSI module, and BTC PDHL module while adding setup-isolated NIFTY spot backtests and interactive trade visualization.

## v3.0 research workflow

1. Connect Kite and download NIFTY 50 spot 5-minute candles, or upload a CSV.
2. Select the Kite-downloaded candles or upload a NIFTY spot CSV. The chart is intentionally hidden before the backtest.
3. Choose combined setup testing or one individual entry setup.
4. Run the backtest. The NIFTY backtest enforces **one trade per trading day**.
5. Review R-multiple statistics, trade log, and the post-backtest chart.

## Entry setup catalog

- PDH momentum / PDL momentum
- PDH two-close / PDL two-close
- PDH trap / PDL trap
- Small-gap PDH support / Small-gap PDL resistance
- Gap-up reversal / Gap-down reversal

Individual tests filter the signal engine to the selected setup. The current signal engine has a deterministic within-candle precedence: momentum, two-close, trap, then retest; gap reversal is evaluated if no standard setup has qualified. A setup that is not selected is discarded and the engine continues scanning later candles. If multiple definitions would qualify on the same candle, the first rule in this precedence is the one classified. Review the signal audit before treating setup labels as final research definitions.

## Chart behavior

The post-backtest Plotly candlestick chart uses the original spot OHLC data. It shows the latest candle OHLC in the chart corner and the hovered candle OHLC in its tooltip. It has no volume panel and no VWAP. Each backtested trade is drawn from its actual `entry_time` through `exit_time` using trade-log prices:

- Red rectangle: entry-to-initial-stop risk zone.
- Green rectangle: entry-to-target reward zone.
- Blue line: entry.
- Red dotted line: stop.
- Green dotted line: target.
- Annotation: LONG/SHORT, trade number, and realized R multiple.
- PDH/PDL: continuous solid session lines.
- Chart navigation: scroll zoom, pan, zoom controls, reset, and independent right-side price and bottom time axes.

The boxes use the initial stop and target recorded by the current fixed-SL/target engine. They do not yet replay trailing-stop levels on the main trade chart; trailing-stop analysis remains a separate comparison module.

## R-multiple definition

`R_multiple = realized spot points / initial stop distance in spot points`.

This is a spot-signal research result, **not options premium P&L**, not rupee P&L, and not net of brokerage, taxes, slippage, or market impact. No options execution is modeled in this workflow.

## Data requirements

CSV must contain: `datetime, open, high, low, close`. `volume` is optional. Timestamps should represent the exchange-local candle time consistently (for NIFTY, Asia/Kolkata). Duplicate timestamps are removed; invalid OHLC rows are rejected or dropped during normalization. Use 5-minute candles for the intended NIFTY setup definitions.

## Deploy

- Keep the original v2.3 deployment unchanged until v3.0 has been tested.
- Upload this staged release to a separate GitHub branch or repository first.
- Configure secrets only in Streamlit app settings, never in source files:
  - `KITE_API_KEY`
  - `KITE_API_SECRET`
  - Optional Telegram/Supabase secrets used by retained modules.
- Install dependencies from `requirements.txt`.

## Validation status and known limitations

- Python syntax compilation is part of the staged build checks.
- Runtime behavior still requires validation in the target Streamlit environment with representative NIFTY and BTC datasets and valid user-owned API credentials.
- Streamlit Community Cloud local files are ephemeral. Verify Supabase checkpoint configuration for durable optimizer recovery after a full app restart.
- The retained BTC modules continue to use their existing independent workflow and have not been re-engineered into the new NIFTY setup-isolation selector.
- The existing NIFTY optimizer uses the combined PDHL signal engine and does not yet optimize each setup independently. Use individual setup backtest mode for setup-level baseline comparisons.
- Existing legacy options-premium module `pdhl.py` is retained but is not used in this spot-signal v3.0 research workflow.
