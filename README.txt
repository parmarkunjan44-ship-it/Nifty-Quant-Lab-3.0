Nifty Quant Lab v3.0 — PDHL rules revision

Replace only the v3.0 repository's app.py. Keep v2.3 separate and untouched.

Confirmed rules in this revision:
- PDH/PDL are previous-session maximum/minimum candle BODY levels.
- Gap-up = first 5-minute candle BODY LOW minus PDH.
- Gap-down = PDL minus first 5-minute candle BODY HIGH.
- Large gap threshold is inclusive (>= configured threshold).
- Gap-up reversal requires a later candle CLOSE below first candle body low.
- Gap-down reversal requires a later candle CLOSE above first candle body high.
- Momentum is evaluated only on the first candle closing beyond PDH/PDL.
- If that candle is not a qualifying momentum candle, only the immediately following candle can provide the second consecutive close.
- Momentum body minimum applies to the first breakout candle only.
- Directional wick filter applies to each actual setup entry-condition candle.
- PDH trap: prior candle close > PDH, current candle close < PDH, provided PDH breakout entry has not activated.
- PDL trap is mirrored.
- Signal/entry-condition candle is the candle that confirms the setup.
- Execution is at the NEXT 5-minute candle OPEN.
- For two-close breakouts, SL uses the second confirmation (entry) candle LOW for LONG or HIGH for SHORT, plus/minus buffer.
- At most one combined strategy trade per day.
- Live PDHL parameters are aligned with backtest defaults.
- Pre-backtest chart removed; chart is price-only and PDH/PDL lines are solid.

Checks:
- Python syntax compilation passed.
- Synthetic tests passed for two-close SL reference, next-candle-open execution, PDH trap, body-based gap classification, and close-only gap reversal.

Important limitations:
- The six setups are still selected by the existing combined signal flow; this revision does not yet produce six fully independent hypothetical setup ledgers/reports.
- Small-gap retest trigger definition remains the existing coded interpretation and should be separately confirmed.
- A full Streamlit runtime test requires deployment in the app environment with its dependencies.
