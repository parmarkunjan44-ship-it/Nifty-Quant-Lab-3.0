import pandas as pd
import numpy as np

SPOT_REQUIRED = {"datetime", "open", "high", "low", "close"}
OPTION_REQUIRED = {"datetime", "expiry", "strike", "option_type", "open", "high", "low", "close"}

def validate_spot(df):
    missing = SPOT_REQUIRED - set(df.columns)
    if missing:
        raise ValueError(f"Spot CSV is missing columns: {', '.join(sorted(missing))}")
    if df.empty:
        raise ValueError("Spot CSV is empty.")

def validate_options(df):
    missing = OPTION_REQUIRED - set(df.columns)
    if missing:
        raise ValueError(f"Options CSV is missing columns: {', '.join(sorted(missing))}")
    if df.empty:
        raise ValueError("Options CSV is empty.")
    vals = set(df["option_type"].astype(str).str.upper().unique())
    if not vals.issubset({"CE", "PE"}):
        raise ValueError("option_type values must be CE or PE.")

def _prepare(df):
    out = df.copy()
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    if out["datetime"].isna().any():
        raise ValueError("One or more datetime values could not be parsed.")
    for col in ["open", "high", "low", "close"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["open", "high", "low", "close"])
    out = out.sort_values("datetime").drop_duplicates("datetime", keep="last").reset_index(drop=True)
    out["trade_date"] = out["datetime"].dt.date
    return out

def _is_strong(row, level, direction, min_body_ratio, min_close_location):
    candle_range = float(row["high"] - row["low"])
    if candle_range <= 0:
        return False
    body_ratio = abs(float(row["close"] - row["open"])) / candle_range
    if direction == "CE":
        close_loc = (float(row["close"]) - float(row["low"])) / candle_range
        return row["close"] > level and row["close"] > row["open"] and body_ratio >= min_body_ratio and close_loc >= min_close_location
    close_loc = (float(row["high"]) - float(row["close"])) / candle_range
    return row["close"] < level and row["close"] < row["open"] and body_ratio >= min_body_ratio and close_loc >= min_close_location

def _signals(spot, confirmation, min_body_ratio, min_close_location):
    s = spot.copy()
    # Previous completed trading day's high/low; no current-day look-ahead.
    daily = s.groupby("trade_date").agg(day_high=("high", "max"), day_low=("low", "min"))
    dates = list(daily.index)
    prev = {}
    for i in range(1, len(dates)):
        prev[dates[i]] = (daily.iloc[i-1]["day_high"], daily.iloc[i-1]["day_low"])
    s["pdh"] = s["trade_date"].map(lambda d: prev.get(d, (np.nan, np.nan))[0])
    s["pdl"] = s["trade_date"].map(lambda d: prev.get(d, (np.nan, np.nan))[1])
    s["signal"] = ""
    for i in range(1, len(s)):
        row = s.iloc[i]
        if pd.isna(row["pdh"]) or pd.isna(row["pdl"]):
            continue
        prevrow = s.iloc[i-1]
        same_day = prevrow["trade_date"] == row["trade_date"]
        above_two = same_day and prevrow["close"] > row["pdh"] and row["close"] > row["pdh"]
        below_two = same_day and prevrow["close"] < row["pdl"] and row["close"] < row["pdl"]
        strong_up = _is_strong(row, row["pdh"], "CE", min_body_ratio, min_close_location)
        strong_down = _is_strong(row, row["pdl"], "PE", min_body_ratio, min_close_location)
        if confirmation == "Two consecutive closes":
            if above_two:
                s.at[i, "signal"] = "CE"
            elif below_two:
                s.at[i, "signal"] = "PE"
        else:
            if above_two or strong_up:
                s.at[i, "signal"] = "CE"
            elif below_two or strong_down:
                s.at[i, "signal"] = "PE"
    return s

def _select_contract(options, timestamp, spot_price, option_type, strike_step, strike_offset):
    available = options[(options["datetime"] == timestamp) & (options["option_type"] == option_type)]
    if available.empty:
        return None
    # CE ITM = below spot; PE ITM = above spot.
    atm = round(float(spot_price) / strike_step) * strike_step
    strike = atm - strike_offset * strike_step if option_type == "CE" else atm + strike_offset * strike_step
    candidates = available[available["strike"] == strike]
    if candidates.empty:
        # Do not silently substitute a different strike.
        return None
    # If multiple expiries exist, choose the nearest expiry on/after trade date.
    candidates = candidates.copy()
    candidates["expiry_dt"] = pd.to_datetime(candidates["expiry"], errors="coerce")
    candidates = candidates[candidates["expiry_dt"].dt.date >= timestamp.date()]
    if candidates.empty:
        return None
    return candidates.sort_values("expiry_dt").iloc[0]

def run_pdhl_backtest(spot_df, option_df, confirmation="Strong candle OR two closes",
                      min_body_ratio=0.65, min_close_location=0.75,
                      strike_step=50, strike_offset=1, quantity=75,
                      sl_pct=0.25, target_pct=0.50, brokerage=15.0,
                      slippage=0.5, max_trades_per_day=1):
    spot = _prepare(spot_df)
    options = option_df.copy()
    options["datetime"] = pd.to_datetime(options["datetime"], errors="coerce")
    options["expiry"] = options["expiry"].astype(str)
    options["strike"] = pd.to_numeric(options["strike"], errors="coerce")
    options["option_type"] = options["option_type"].astype(str).str.upper()
    for col in ["open", "high", "low", "close"]:
        options[col] = pd.to_numeric(options[col], errors="coerce")
    options = options.dropna(subset=["datetime", "strike", "open", "high", "low", "close"])
    options = options.sort_values("datetime").reset_index(drop=True)

    signals = _signals(spot, confirmation, min_body_ratio, min_close_location)
    trades = []
    daily_count = {}

    for i in range(len(signals) - 1):
        row = signals.iloc[i]
        direction = row["signal"]
        if direction not in ("CE", "PE"):
            continue
        day = row["trade_date"]
        if daily_count.get(day, 0) >= max_trades_per_day:
            continue

        # Entry on next spot candle timestamp, avoiding same-candle look-ahead.
        next_row = signals.iloc[i + 1]
        if next_row["trade_date"] != day:
            continue
        entry_time = next_row["datetime"]
        contract = _select_contract(options, entry_time, next_row["open"], direction, strike_step, strike_offset)
        if contract is None or contract["open"] <= 0:
            continue

        entry_price = float(contract["open"]) + slippage
        stop = entry_price * (1 - sl_pct)
        target = entry_price * (1 + target_pct)
        contract_key = (contract["expiry"], float(contract["strike"]), direction)
        path = options[
            (options["datetime"] >= entry_time) &
            (options["datetime"].dt.date == day) &
            (options["expiry"] == contract_key[0]) &
            (options["strike"] == contract_key[1]) &
            (options["option_type"] == direction)
        ].sort_values("datetime")

        exit_price, exit_time, reason = None, None, None
        for _, candle in path.iterrows():
            # Conservative assumption if both SL and target are touched in one bar.
            if candle["low"] <= stop:
                exit_price, exit_time, reason = stop - slippage, candle["datetime"], "SL"
                break
            if candle["high"] >= target:
                exit_price, exit_time, reason = target - slippage, candle["datetime"], "Target"
                break
        if exit_price is None:
            last = path.iloc[-1] if not path.empty else None
            if last is None:
                continue
            exit_price, exit_time, reason = float(last["close"]) - slippage, last["datetime"], "EOD"

        gross = (exit_price - entry_price) * quantity
        charges = 2 * brokerage
        net = gross - charges
        trades.append({
            "signal_time": row["datetime"], "entry_time": entry_time, "exit_time": exit_time,
            "direction": direction, "expiry": contract_key[0], "strike": contract_key[1],
            "quantity": quantity, "entry_premium": round(entry_price, 2),
            "exit_premium": round(exit_price, 2), "exit_reason": reason,
            "gross_pnl": round(gross, 2), "charges": round(charges, 2), "net_pnl": round(net, 2)
        })
        daily_count[day] = daily_count.get(day, 0) + 1

    trade_df = pd.DataFrame(trades)
    if trade_df.empty:
        return trade_df, pd.DataFrame(columns=["exit_time", "net_pnl", "cumulative_pnl"])
    trade_df = trade_df.sort_values("exit_time").reset_index(drop=True)
    equity = trade_df[["exit_time", "net_pnl"]].copy()
    equity["cumulative_pnl"] = equity["net_pnl"].cumsum()
    return trade_df, equity
