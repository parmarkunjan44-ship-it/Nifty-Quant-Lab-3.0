"""BTC PDHL baseline backtest using UTC calendar days and Delta 5m candles."""
import time
import requests
import pandas as pd
import numpy as np

DELTA_BASE = "https://api.india.delta.exchange"

def fetch_btc_5m_utc(start_dt, end_dt, symbol="BTCUSD"):
    start = pd.Timestamp(start_dt)
    end = pd.Timestamp(end_dt)
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
    cursor, end_ts = int(start.timestamp()), int(end.timestamp())
    step = 300
    rows = []
    while cursor < end_ts:
        chunk_end = min(end_ts, cursor + step * 1900)
        response = requests.get(
            f"{DELTA_BASE}/v2/history/candles",
            params={"resolution": "5m", "symbol": symbol, "start": cursor, "end": chunk_end},
            headers={"Accept": "application/json"}, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            raise RuntimeError(f"Delta API error: {payload}")
        rows.extend(payload.get("result", []))
        cursor = chunk_end + step
        if rows:
            time.sleep(0.12)
    if not rows:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
    x = pd.DataFrame(rows)
    x["datetime"] = pd.to_datetime(pd.to_numeric(x["time"]), unit="s", utc=True)
    for c in ["open", "high", "low", "close", "volume"]:
        if c in x:
            x[c] = pd.to_numeric(x[c], errors="coerce")
    return x.dropna(subset=["datetime", "open", "high", "low", "close"]).sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)

def prepare_btc_utc(df):
    required = {"datetime", "open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError("BTC data missing columns: " + ", ".join(sorted(missing)))
    x = df.copy()
    dt = pd.to_datetime(x["datetime"], errors="coerce")
    if dt.isna().any():
        raise ValueError("Invalid BTC candle timestamps.")
    if dt.dt.tz is None:
        dt = dt.dt.tz_localize("UTC")
    else:
        dt = dt.dt.tz_convert("UTC")
    x["datetime"] = dt
    for c in ["open", "high", "low", "close"]:
        x[c] = pd.to_numeric(x[c], errors="coerce")
    x = x.dropna(subset=["open", "high", "low", "close"]).sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
    if x.empty:
        raise ValueError("No valid BTC candles found.")
    if ((x.high < x[["open", "close", "low"]].max(axis=1)) | (x.low > x[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("Invalid OHLC candle found.")
    x["utc_date"] = x.datetime.dt.strftime("%Y-%m-%d")
    x["body_high"] = x[["open", "close"]].max(axis=1)
    x["body_low"] = x[["open", "close"]].min(axis=1)
    daily = x.groupby("utc_date").agg(pdh=("body_high", "max"), pdl=("body_low", "min"))
    dates = list(daily.index)
    prior = {dates[i]: (float(daily.iloc[i-1].pdh), float(daily.iloc[i-1].pdl)) for i in range(1, len(dates))}
    x["pdh"] = x.utc_date.map(lambda d: prior.get(d, (np.nan, np.nan))[0])
    x["pdl"] = x.utc_date.map(lambda d: prior.get(d, (np.nan, np.nan))[1])
    return x

def backtest_btc_pdhl(df, rr=3.5, max_trades_per_day=1, fee_bps_per_side=0.0, slippage_bps_per_side=0.0, confirmation="Strong candle OR two closes"):
    """Baseline: body-based prior UTC-day levels; close breakout; next-bar-open entry;
    signal candle extreme is stop; fixed R target. If stop/target same bar, stop first.
    No gap/trap/retest rules are inferred in this baseline.
    """
    x = prepare_btc_utc(df)
    trades, daily_count = [], {}
    cost_rate = (float(fee_bps_per_side) + float(slippage_bps_per_side)) / 10000.0
    for i in range(1, len(x)-1):
        r, prev = x.iloc[i], x.iloc[i-1]
        if pd.isna(r.pdh) or pd.isna(r.pdl):
            continue
        day = r.utc_date
        if daily_count.get(day, 0) >= int(max_trades_per_day):
            continue
        body = abs(float(r.close-r.open))
        up_wick = float(r.high-max(r.open,r.close))
        low_wick = float(min(r.open,r.close)-r.low)
        long_momentum = r.close > r.pdh and r.close > r.open and up_wick <= body
        short_momentum = r.close < r.pdl and r.close < r.open and low_wick <= body
        long_two = prev.utc_date == day and prev.close > r.pdh and r.close > r.pdh and r.close > r.open and up_wick <= body
        short_two = prev.utc_date == day and prev.close < r.pdl and r.close < r.pdl and r.close < r.open and low_wick <= body
        side = None
        if confirmation == "Strong candle only":
            if long_momentum: side = "LONG"
            elif short_momentum: side = "SHORT"
        elif confirmation == "Two consecutive closes only":
            if long_two: side = "LONG"
            elif short_two: side = "SHORT"
        else:
            if long_momentum or long_two: side = "LONG"
            elif short_momentum or short_two: side = "SHORT"
        if side is None:
            continue
        entry_i = i+1
        entry = float(x.iloc[entry_i].open)
        stop = float(r.low) if side == "LONG" else float(r.high)
        risk = entry-stop if side == "LONG" else stop-entry
        if risk <= 0:
            continue
        target = entry + risk*float(rr) if side == "LONG" else entry-risk*float(rr)
        exit_price, exit_i, reason = float(x.iloc[-1].close), len(x)-1, "End of data"
        for j in range(entry_i, len(x)):
            bar = x.iloc[j]
            # Do not carry a position beyond its UTC day in this baseline.
            if bar.utc_date != day:
                exit_price, exit_i, reason = float(x.iloc[j-1].close), j-1, "UTC day close"
                break
            hit_sl = bar.low <= stop if side == "LONG" else bar.high >= stop
            hit_tp = bar.high >= target if side == "LONG" else bar.low <= target
            if hit_sl:
                exit_price, exit_i, reason = stop, j, "Stop loss"
                break
            if hit_tp:
                exit_price, exit_i, reason = target, j, "Target"
                break
        gross = exit_price-entry if side == "LONG" else entry-exit_price
        costs = (abs(entry)+abs(exit_price))*cost_rate
        net = gross-costs
        trades.append({"utc_date": day, "signal_time": r.datetime, "entry_time": x.iloc[entry_i].datetime,
                       "exit_time": x.iloc[exit_i].datetime, "side": side, "setup": "Momentum" if (long_momentum or short_momentum) else "Two-close",
                       "pdh": float(r.pdh), "pdl": float(r.pdl), "entry": entry, "stop": stop, "target": target,
                       "exit": exit_price, "risk_points": risk, "gross_pnl_points": gross,
                       "estimated_cost_points": costs, "net_pnl_points": net, "R_multiple": net/risk,
                       "exit_reason": reason})
        daily_count[day] = daily_count.get(day, 0)+1
    return pd.DataFrame(trades)

def summarize_btc_pdhl(trades):
    if trades is None or trades.empty:
        return {"Trades": 0, "Win rate %": 0.0, "Net points": 0.0, "Expectancy R": 0.0, "Profit factor": 0.0, "Max drawdown R": 0.0, "Total R": 0.0}
    r = trades.R_multiple.astype(float)
    wins, losses = r[r > 0].sum(), abs(r[r < 0].sum())
    eq = r.cumsum(); dd = eq-eq.cummax().clip(lower=0)
    return {"Trades": len(r), "Win rate %": float((r > 0).mean()*100), "Net points": float(trades.net_pnl_points.sum()),
            "Expectancy R": float(r.mean()), "Profit factor": float(wins/losses) if losses else float("inf"),
            "Max drawdown R": float(abs(dd.min())), "Total R": float(r.sum())}
