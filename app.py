import streamlit as st
import pandas as pd
import numpy as np
import requests, hashlib, time, json, sqlite3, threading, traceback, os, base64
from io import StringIO
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

st.set_page_config(page_title="Nifty Quant Lab v3.0", page_icon="📈", layout="wide")
st.title("📈 Nifty Quant Lab v3.0")
st.caption("Unified NIFTY 50 + BTC research workspace | Existing Kite and Telegram live-alert features retained")
st.caption("Nifty PDHL research + BTC EMA/Stoch RSI research • Independent markets and statistics")
KITE_API_ROOT = "https://api.kite.trade"
KITE_LOGIN_ROOT = "https://kite.zerodha.com/connect/login"



def render_tradingview_chart(candles, trades=None, chart_key="nifty_chart", max_candles=None):
    'Mobile-first Lightweight Charts view with axis drag zoom and touch pan/pinch.'
    import streamlit.components.v1 as components

    d = candles.copy()
    d["datetime"] = pd.to_datetime(d["datetime"], errors="coerce")
    d = d.dropna(subset=["datetime", "open", "high", "low", "close"]).sort_values("datetime")
    if d.empty:
        st.warning("No valid OHLC candles are available for the chart.")
        return

    trading_dates = sorted(pd.Series(d["datetime"].dt.date.unique()).tolist())
    latest_day, earliest_day = trading_dates[-1], trading_dates[0]
    selected_day = st.date_input(
        "Go to date", value=latest_day, min_value=earliest_day, max_value=latest_day,
        key=f"{chart_key}_goto_date",
        help="Choose a trading date. Drag the chart to inspect nearby sessions."
    )
    available = [day for day in trading_dates if day <= selected_day]
    if not available:
        selected_day = earliest_day
    elif selected_day not in trading_dates:
        selected_day = available[-1]
        st.info(f"No candles on that date. Showing previous session: {selected_day}.")

    selected_index = trading_dates.index(selected_day)
    window_dates = set(trading_dates[max(0, selected_index - 4):selected_index + 1])
    d = d[d["datetime"].dt.date.isin(window_dates)].copy()
    d = d.drop_duplicates("datetime", keep="last")

    # Lightweight Charts expects Unix seconds and numeric OHLC values.
    def to_epoch_ist(value):
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("Asia/Kolkata")
        else:
            ts = ts.tz_convert("Asia/Kolkata")
        return int(ts.timestamp())

    candle_rows = []
    for row in d.itertuples(index=False):
        candle_rows.append({
            "time": to_epoch_ist(row.datetime),
            "open": float(row.open), "high": float(row.high),
            "low": float(row.low), "close": float(row.close)
        })

    trade_rows = []
    markers = []
    if trades is not None and len(trades):
        t = pd.DataFrame(trades).copy()
        t["entry_time"] = pd.to_datetime(t["entry_time"], errors="coerce")
        t["exit_time"] = pd.to_datetime(t["exit_time"], errors="coerce")
        left, right = d.datetime.min(), d.datetime.max()
        t = t[(t.entry_time >= left) & (t.entry_time <= right) & (t.exit_time >= left)]
        for n, tr in t.reset_index(drop=True).iterrows():
            if pd.isna(tr.entry_time) or pd.isna(tr.exit_time):
                continue
            entry_ts, exit_ts = to_epoch_ist(tr.entry_time), to_epoch_ist(tr.exit_time)
            entry = float(tr.get("entry", np.nan))
            stop = float(tr.get("stop", np.nan))
            target = float(tr.get("target", np.nan))
            exit_price = float(tr.get("exit", np.nan))
            direction = str(tr.get("direction", "LONG")).upper()
            setup = str(tr.get("setup", ""))
            r_mult = float(tr.get("R_multiple", 0) or 0)
            trade_no = n + 1
            trade_rows.append({"n": trade_no, "entry_time": entry_ts, "exit_time": exit_ts,
                               "entry": entry, "stop": stop, "target": target,
                               "exit": exit_price, "direction": direction,
                               "setup": setup, "r": r_mult})
            markers.append({"time": entry_ts, "position": "belowBar" if direction == "LONG" else "aboveBar",
                            "color": "#e5e7eb", "shape": "diamond", "text": f"T{trade_no}",
                            "size": 2})

    candles_json = json.dumps(candle_rows, separators=(",", ":"))
    trades_json = json.dumps(trade_rows, separators=(",", ":"))
    markers_json = json.dumps(markers, separators=(",", ":"))
    pdh = float(d["pdh"].dropna().iloc[-1]) if "pdh" in d.columns and d["pdh"].notna().any() else None
    pdl = float(d["pdl"].dropna().iloc[-1]) if "pdl" in d.columns and d["pdl"].notna().any() else None
    levels_json = json.dumps({"pdh": pdh, "pdl": pdl})

    html = r'''<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
    <script src="https://unpkg.com/lightweight-charts@4.2.2/dist/lightweight-charts.standalone.production.js"></script>
    <style>
      *{box-sizing:border-box} html,body{margin:0;padding:0;background:#131722;color:#d1d4dc;font-family:Arial,sans-serif;overflow:hidden}
      #wrap{width:100%;height:610px;position:relative;background:#131722} #chart{width:100%;height:100%;touch-action:pan-x pan-y} #rr-overlay{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:2}
      #tip{position:absolute;z-index:5;left:8px;top:8px;padding:7px 9px;border-radius:5px;background:rgba(24,28,38,.94);font-size:11px;line-height:1.5;pointer-events:none;white-space:pre-line}
      #hint{position:absolute;bottom:5px;left:8px;color:#9298a5;font-size:10px;pointer-events:none}
      @media(max-width:600px){#wrap{height:540px}#tip{font-size:10px}}
    </style></head><body><div id="wrap"><div id="chart"></div><canvas id="rr-overlay"></canvas><div id="tip">Loading chart…</div><div id="hint">Axis drag = zoom • Chart drag = pan • Two fingers = pinch zoom</div></div>
    <script>
    const candles=__CANDLES__, trades=__TRADES__, markers=__MARKERS__, levels=__LEVELS__;
    const root=document.getElementById('chart'), tip=document.getElementById('tip'), overlay=document.getElementById('rr-overlay');
    const istDateTime=new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hour12:false});
    const istAxisTime=new Intl.DateTimeFormat('en-IN',{timeZone:'Asia/Kolkata',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hour12:false});
    const asDate=(t)=>new Date((typeof t==='number'?t:Math.floor(Date.UTC(t.year,t.month-1,t.day)/1000))*1000);
    function fail(msg){tip.textContent=msg;}
    if(!window.LightweightCharts){fail('Chart library could not load. Check internet/CDN access.');}
    else if(!candles.length){fail('No candles in selected date window.');}
    else {
      const chart=LightweightCharts.createChart(root,{
        width:root.clientWidth,height:root.clientHeight,
        layout:{background:{type:'solid',color:'#131722'},textColor:'#d1d4dc',fontSize:11},
        grid:{vertLines:{color:'#252a36'},horzLines:{color:'#252a36'}},
        rightPriceScale:{borderColor:'#48515f',scaleMargins:{top:.08,bottom:.12}},
        timeScale:{borderColor:'#48515f',timeVisible:true,secondsVisible:false,rightOffset:4,tickMarkFormatter:(time)=>istAxisTime.format(asDate(time))},
        localization:{locale:'en-IN',timeFormatter:(time)=>istDateTime.format(asDate(time))},
        crosshair:{mode:LightweightCharts.CrosshairMode.Normal},
        handleScroll:{mouseWheel:false,pressedMouseMove:true,horzTouchDrag:true,vertTouchDrag:true},
        handleScale:{axisPressedMouseMove:{time:true,price:true},axisDoubleClickReset:true,mouseWheel:false,pinch:true}
      });
      const series=chart.addCandlestickSeries({upColor:'#26a69a',downColor:'#ef5350',borderUpColor:'#26a69a',borderDownColor:'#ef5350',wickUpColor:'#26a69a',wickDownColor:'#ef5350'});
      series.setData(candles);
      if(markers.length){markers.sort((a,b)=>a.time-b.time);series.setMarkers(markers);}
      if(levels.pdh!==null) series.createPriceLine({price:levels.pdh,color:'#f0b90b',lineWidth:1,lineStyle:0,axisLabelVisible:true,title:'PDH'});
      if(levels.pdl!==null) series.createPriceLine({price:levels.pdl,color:'#42a5f5',lineWidth:1,lineStyle:0,axisLabelVisible:true,title:'PDL'});
      // Entry, SL and target lines plus shaded risk/reward rectangles for every trade.
      trades.forEach(t=>{
        const seg=(price,color,style)=>{
          if(Number.isFinite(price)){
            const line=chart.addLineSeries({color,lineWidth:1,lineStyle:style,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false});
            line.setData([{time:t.entry_time,value:price},{time:t.exit_time,value:price}]);
          }
        };
        seg(t.entry,'#e5e7eb',2); seg(t.stop,'#ef5350',0); seg(t.target,'#26a69a',0);
      });
      const drawRiskReward=()=>{
        const w=root.clientWidth,h=root.clientHeight,dpr=window.devicePixelRatio||1;
        overlay.width=Math.round(w*dpr); overlay.height=Math.round(h*dpr);
        overlay.style.width=w+'px'; overlay.style.height=h+'px';
        const ctx=overlay.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,w,h);
        trades.forEach(t=>{
          const x1=chart.timeScale().timeToCoordinate(t.entry_time), x2=chart.timeScale().timeToCoordinate(t.exit_time);
          const ye=series.priceToCoordinate(t.entry), ys=series.priceToCoordinate(t.stop), yt=series.priceToCoordinate(t.target);
          if([x1,x2,ye,ys,yt].some(v=>v===null||!Number.isFinite(v))) return;
          const left=Math.min(x1,x2), width=Math.max(2,Math.abs(x2-x1));
          const rect=(yA,yB,fill,stroke)=>{const top=Math.min(yA,yB),height=Math.max(1,Math.abs(yB-yA));ctx.fillStyle=fill;ctx.strokeStyle=stroke;ctx.lineWidth=1;ctx.fillRect(left,top,width,height);ctx.strokeRect(left,top,width,height);};
          rect(ye,yt,'rgba(38,166,154,0.18)','rgba(38,166,154,0.75)');
          rect(ye,ys,'rgba(239,83,80,0.18)','rgba(239,83,80,0.75)');
        });
      };
      chart.timeScale().fitContent();
      drawRiskReward();
      chart.timeScale().subscribeVisibleTimeRangeChange(drawRiskReward);
      series.subscribeDataChanged?.(drawRiskReward);
      chart.subscribeCrosshairMove(param=>{
        if(!param || !param.time || !param.seriesData || !param.seriesData.get(series)){tip.textContent='';return;}
        const c=param.seriesData.get(series);
        const dt=new Date(param.time*1000);
        tip.textContent=istDateTime.format(dt)+
          '\nO '+c.open.toFixed(2)+'  H '+c.high.toFixed(2)+'\nL '+c.low.toFixed(2)+'  C '+c.close.toFixed(2);
      });
      const resize=()=>{chart.applyOptions({width:root.clientWidth,height:root.clientHeight});drawRiskReward();};
      if(window.ResizeObserver){new ResizeObserver(resize).observe(root);}else{window.addEventListener('resize',resize);}
    }
    </script></body></html>'''
    html = html.replace("__CANDLES__", candles_json).replace("__TRADES__", trades_json).replace("__MARKERS__", markers_json).replace("__LEVELS__", levels_json)
    components.html(html, height=620, scrolling=False)
    st.caption("Mobile controls: drag chart to pan • drag price axis to vertical-zoom • drag time axis to horizontal-zoom • two-finger pinch to zoom. Trade entry markers, shaded green reward zones and red risk zones, plus entry/SL/target lines are shown. All chart times use IST.")


def compound_capital_path(trades, initial_capital, risk_pct):
    """Apply each trade R to a fixed percentage of current equity, chronologically."""
    if trades is None or len(trades) == 0:
        return pd.DataFrame(), float(initial_capital)
    t = pd.DataFrame(trades).copy() if not isinstance(trades, pd.DataFrame) else trades.copy()
    if "R_multiple" not in t.columns:
        return pd.DataFrame(), float(initial_capital)
    if "exit_time" in t.columns:
        t["exit_time"] = pd.to_datetime(t["exit_time"], errors="coerce")
        t = t.sort_values("exit_time", kind="stable")
    equity = float(initial_capital)
    rows = []
    rate = float(risk_pct) / 100.0
    for i, (_, trade) in enumerate(t.iterrows(), start=1):
        risk_cash = equity * rate
        r_value = float(trade["R_multiple"])
        pnl_cash = risk_cash * r_value
        start_equity = equity
        equity += pnl_cash
        rows.append({"Trade": i, "Exit time": trade.get("exit_time", ""),
                     "R multiple": r_value, "Capital before": start_equity,
                     "Risk amount": risk_cash, "P&L": pnl_cash, "Capital after": equity})
    curve = pd.DataFrame(rows)
    return curve, equity

def prepare_spot(df):
    required={"datetime","open","high","low","close"}
    missing=required-set(df.columns)
    if missing: raise ValueError("CSV missing columns: "+", ".join(sorted(missing)))
    x=df.copy(); x["datetime"]=pd.to_datetime(x.datetime, errors="coerce")
    for c in ["open","high","low","close"]: x[c]=pd.to_numeric(x[c],errors="coerce")
    x=x.dropna(subset=["datetime","open","high","low","close"]).sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
    if x.empty: raise ValueError("No valid OHLC candles found")
    if ((x.high < x[["open","close","low"]].max(axis=1)) | (x.low > x[["open","close","high"]].min(axis=1))).any(): raise ValueError("Invalid OHLC candle found")
    x["trade_date"]=x.datetime.dt.date
    return x

def candle_parts(r):
    body=abs(float(r.close)-float(r.open))
    upper=float(r.high)-max(float(r.open),float(r.close))
    lower=min(float(r.open),float(r.close))-float(r.low)
    return body,max(0.0,upper),max(0.0,lower)

def _entry_wick_pass(row, side):
    """Directional wick filter used on every setup's entry candle."""
    body, upper, lower = candle_parts(row)
    return upper <= body if side == "LONG" else lower <= body


def build_signals(df, gap_threshold=50.0, cutoff="11:00", min_breakout_body=50.0, progress_callback=None):
    """Build one earliest valid PDHL signal per session using confirmed rules.

    Signal candle is the candle that satisfies the setup. Execution is on the
    next 5-minute candle open in run_backtest. PDH/PDL use prior-session bodies.
    """
    x = prepare_spot(df)
    x["body_high"] = x[["open", "close"]].max(axis=1)
    x["body_low"] = x[["open", "close"]].min(axis=1)
    daily = x.groupby("trade_date").agg(pdh=("body_high", "max"), pdl=("body_low", "min"))
    dates = list(daily.index)
    prev = {dates[j]: (float(daily.iloc[j-1].pdh), float(daily.iloc[j-1].pdl)) for j in range(1, len(dates))}
    x["pdh"] = x.trade_date.map(lambda d: prev.get(d, (np.nan, np.nan))[0])
    x["pdl"] = x.trade_date.map(lambda d: prev.get(d, (np.nan, np.nan))[1])
    for col, default in [("signal", ""), ("setup", ""), ("signal_reason", ""), ("signal_candle_time", ""), ("entry_candle_time", ""), ("sl_reference", "")]:
        x[col] = default
    x["gap_points"] = np.nan
    x["opening_body_high"] = np.nan
    x["opening_body_low"] = np.nan
    sessions = list(x.groupby("trade_date").groups.items())
    total = max(1, len(sessions))
    for session_no, (day, idx) in enumerate(sessions, 1):
        if progress_callback and (session_no == 1 or session_no % 25 == 0 or session_no == total):
            progress_callback(0.05 + 0.20 * session_no / total, f"Building PDH/PDL signals: session {session_no:,} of {total:,}")
        ids = list(idx)
        if not ids or pd.isna(x.at[ids[0], "pdh"]):
            continue
        first_i = ids[0]
        first = x.loc[first_i]
        pdh, pdl = float(first.pdh), float(first.pdl)
        opening_body_high = max(float(first.open), float(first.close))
        opening_body_low = min(float(first.open), float(first.close))
        # Gap is measured from the opening candle BODY edge to PDH/PDL.
        if opening_body_low > pdh:
            gap_points = opening_body_low - pdh
            gap_mode = "gap_up" if gap_points >= float(gap_threshold) else "small_gap"
        elif opening_body_high < pdl:
            gap_points = pdl - opening_body_high
            gap_mode = "gap_down" if gap_points >= float(gap_threshold) else "small_gap"
        else:
            gap_points = 0.0
            gap_mode = "inside_range"
        x.loc[ids, "gap_points"] = gap_points
        x.loc[ids, "opening_body_high"] = opening_body_high
        x.loc[ids, "opening_body_low"] = opening_body_low

        # Only the first close outside the range is tested for momentum.
        first_break_pos = None
        first_break_side = None
        for pos, i in enumerate(ids):
            r = x.loc[i]
            if r.close > pdh:
                first_break_pos, first_break_side = pos, "LONG"
                break
            if r.close < pdl:
                first_break_pos, first_break_side = pos, "SHORT"
                break

        selected = None
        # Large-gap reversal: later candle CLOSE, not wick, crosses opening body edge.
        if gap_mode in ("gap_up", "gap_down"):
            for pos in range(1, len(ids)):
                i = ids[pos]
                r = x.loc[i]
                tm = r.datetime.strftime("%H:%M")
                if tm >= cutoff:
                    break
                if gap_mode == "gap_up" and float(r.close) < opening_body_low and _entry_wick_pass(r, "SHORT"):
                    selected = (i, "SHORT", "Gap-up reversal", "Close below first 5-minute candle body low; directional wick filter passed", "signal")
                    break
                if gap_mode == "gap_down" and float(r.close) > opening_body_high and _entry_wick_pass(r, "LONG"):
                    selected = (i, "LONG", "Gap-down reversal", "Close above first 5-minute candle body high; directional wick filter passed", "signal")
                    break
        else:
            # First breakout candle momentum, otherwise only the immediately next
            # candle can confirm the second consecutive close.
            if first_break_pos is not None:
                i = ids[first_break_pos]
                r = x.loc[i]
                body, upper, lower = candle_parts(r)
                side = first_break_side
                momentum = (body >= float(min_breakout_body) and _entry_wick_pass(r, side)
                            and ((side == "LONG" and r.close > r.open) or (side == "SHORT" and r.close < r.open)))
                if momentum:
                    selected = (i, side, "PDH momentum" if side == "LONG" else "PDL momentum",
                                "First breakout candle qualifies as momentum and passes wick filter", "signal")
                else:
                    next_pos = first_break_pos + 1
                    if next_pos < len(ids):
                        j = ids[next_pos]
                        confirm = x.loc[j]
                        confirms = (confirm.close > pdh if side == "LONG" else confirm.close < pdl)
                        if confirms and _entry_wick_pass(confirm, side):
                            selected = (j, side, "PDH two-close" if side == "LONG" else "PDL two-close",
                                        "First breakout close followed by second consecutive close; entry candle passes wick filter", "entry")

            # Traps are allowed only when breakout entry has NOT activated.
            # PDH trap: previous close > PDH, current close < PDH.
            # PDL trap: previous close < PDL, current close > PDL.
            if selected is None:
                for pos in range(1, len(ids)):
                    i, pi = ids[pos], ids[pos-1]
                    r, pr = x.loc[i], x.loc[pi]
                    if pr.close > pdh and r.close < pdh and _entry_wick_pass(r, "SHORT"):
                        selected = (i, "SHORT", "PDH trap", "Previous candle closed above PDH; current candle closed below PDH; PDH breakout entry not activated", "signal")
                        break
                    if pr.close < pdl and r.close > pdl and _entry_wick_pass(r, "LONG"):
                        selected = (i, "LONG", "PDL trap", "Previous candle closed below PDL; current candle closed above PDL; PDL breakdown entry not activated", "signal")
                        break

            # Small-gap / inside-range retests: touch and close back beyond level.
            if selected is None:
                for pos in range(1, len(ids)):
                    i, pi = ids[pos], ids[pos-1]
                    r, pr = x.loc[i], x.loc[pi]
                    if pr.close > pdh and r.low <= pdh and r.close > pdh and _entry_wick_pass(r, "LONG"):
                        selected = (i, "LONG", "Small-gap PDH support", "Retest touched PDH and closed above; wick filter passed", "signal")
                        break
                    if pr.close < pdl and r.high >= pdl and r.close < pdl and _entry_wick_pass(r, "SHORT"):
                        selected = (i, "SHORT", "Small-gap PDL resistance", "Retest touched PDL and closed below; wick filter passed", "signal")
                        break
        if selected:
            i, side, setup, reason, sl_ref = selected
            x.at[i, "signal"] = side
            x.at[i, "setup"] = setup
            x.at[i, "signal_reason"] = reason
            x.at[i, "signal_candle_time"] = str(x.at[i, "datetime"])
            x.at[i, "entry_candle_time"] = str(x.at[i, "datetime"])
            x.at[i, "sl_reference"] = sl_ref
    return x


def run_backtest(df, max_trades_day=1, entry_mode="Next candle open", sl_buffer=10.0, rr=2.0, min_breakout_body=50.0, gap_threshold=50.0, cutoff="11:00", progress_callback=None):
    """Execute confirmed signals at the next 5-minute candle open; one/day default."""
    sig = build_signals(df, min_breakout_body=min_breakout_body, gap_threshold=gap_threshold, cutoff=cutoff, progress_callback=progress_callback)
    out, counts = [], {}
    total_rows = max(1, len(sig))
    for row_no, (i, r) in enumerate(sig.iterrows(), 1):
        if progress_callback and (row_no == 1 or row_no % 3000 == 0 or row_no == total_rows):
            progress_callback(0.25 + 0.74 * row_no / total_rows, f"Simulating trades: candle {row_no:,} of {total_rows:,}")
        day, side = r.trade_date, r.signal
        if not side or counts.get(day, 0) >= min(1, int(max_trades_day)):
            continue
        # Entry always occurs at the following 5-minute candle OPEN.
        nxt = sig.index.get_loc(i) + 1
        if nxt >= len(sig):
            continue
        entrybar = sig.iloc[nxt]
        if entrybar.trade_date != day:
            continue
        entry = float(entrybar.open)
        setup = str(r.setup)
        # For a two-close breakout, the second confirmation candle is the
        # entry candle (per the strategy terminology) and supplies the SL.
        # Actual execution still occurs at the following candle OPEN.
        sl_row = r
        if side == "LONG":
            stop = float(sl_row.low) - float(sl_buffer)
            risk = entry - stop
            target = entry + float(rr) * risk
        else:
            stop = float(sl_row.high) + float(sl_buffer)
            risk = stop - entry
            target = entry - float(rr) * risk
        if risk <= 0:
            continue
        # Begin simulation at the entry candle; never inspect pre-entry movement.
        path = sig.iloc[nxt:]
        path = path[path.trade_date == day]
        if path.empty:
            continue
        exit_price = exit_time = exit_reason = None
        for _, b in path.iterrows():
            # If market gaps through SL at candle open, use the worse opening fill.
            if side == "LONG":
                hit_sl = b.low <= stop
                hit_tp = b.high >= target
                if float(b.open) <= stop:
                    exit_price, exit_time, exit_reason = float(b.open), b.datetime, "SL gap"
                    break
            else:
                hit_sl = b.high >= stop
                hit_tp = b.low <= target
                if float(b.open) >= stop:
                    exit_price, exit_time, exit_reason = float(b.open), b.datetime, "SL gap"
                    break
            if hit_sl:
                exit_price, exit_time, exit_reason = stop, b.datetime, "SL"
                break
            if hit_tp:
                exit_price, exit_time, exit_reason = target, b.datetime, "Target"
                break
        if exit_price is None:
            last = path.iloc[-1]
            exit_price, exit_time, exit_reason = float(last.close), last.datetime, "EOD"
        pnl = (exit_price - entry) if side == "LONG" else (entry - exit_price)
        out.append({"trade_date": str(day), "setup": setup, "direction": side,
                    "signal_time": r.datetime, "entry_time": entrybar.datetime,
                    "entry": entry, "stop": stop, "target": target,
                    "exit_time": exit_time, "exit": exit_price, "exit_reason": exit_reason,
                    "risk_points": risk, "pnl_points": pnl, "R_multiple": pnl/risk,
                    "signal_reason": r.signal_reason, "PDH": r.pdh, "PDL": r.pdl,
                    "signal_candle_time": r.datetime, "entry_candle_time": entrybar.datetime,
                    "sl_reference_candle_time": sl_row.datetime})
        counts[day] = counts.get(day, 0) + 1
    trades = pd.DataFrame(out)
    if trades.empty:
        if progress_callback: progress_callback(1.0, "Backtest complete — no qualifying trades")
        return trades, pd.DataFrame(), sig
    trades = trades.sort_values("exit_time").reset_index(drop=True)
    trades["R_multiple"] = trades.pnl_points / trades.risk_points
    trades["cumulative_R"] = trades.R_multiple.cumsum()
    trades["cumulative_points"] = trades.pnl_points.cumsum()
    if progress_callback: progress_callback(1.0, "Backtest complete")
    return trades, trades[["trade_date", "pnl_points", "cumulative_points", "R_multiple", "cumulative_R"]], sig


def trailing_stop_analysis(data, trades, atr_mult=2.0, swing_lookback=3):
    """Replay the same PDHL entries with alternative exits; no entry rules are changed.
    Trailing levels are updated after each candle and become active on the next candle.
    Original fixed target remains in place for every variant.
    """
    if trades is None or trades.empty:
        return pd.DataFrame(), pd.DataFrame()
    x = prepare_spot(data).copy()
    x["true_range"] = np.maximum(x.high-x.low, np.maximum((x.high-x.close.shift()).abs(), (x.low-x.close.shift()).abs()))
    x["atr14"] = x.groupby("trade_date")["true_range"].transform(lambda z: z.rolling(14, min_periods=1).mean())
    rows=[]
    for _, tr in trades.iterrows():
        day = str(tr.trade_date)
        path = x[x.trade_date.astype(str)==day].copy()
        path = path[path.datetime >= pd.to_datetime(tr.entry_time)]
        if path.empty: continue
        side=str(tr.direction).upper(); entry=float(tr.entry); initial=float(tr.stop); target=float(tr.target); risk=float(tr.risk_points)
        if risk<=0: continue
        variants={
            "Fixed SL + Target": {"stop":initial,"exit":None,"reason":None,"active":False,"best":entry},
            "Breakeven at +1R": {"stop":initial,"exit":None,"reason":None,"active":False,"best":entry},
            "Step trailing (1R steps)": {"stop":initial,"exit":None,"reason":None,"active":False,"best":entry},
            f"ATR trailing ({atr_mult:.1f}x)": {"stop":initial,"exit":None,"reason":None,"active":False,"best":entry},
            f"Swing trailing ({swing_lookback} bars)": {"stop":initial,"exit":None,"reason":None,"active":False,"best":entry},
        }
        for j, (_, bar) in enumerate(path.iterrows()):
            hi=float(bar.high); lo=float(bar.low); close=float(bar.close)
            for name, stt in variants.items():
                if stt["exit"] is not None: continue
                stop=stt["stop"]
                hit_stop = lo<=stop if side=="LONG" else hi>=stop
                hit_target = hi>=target if side=="LONG" else lo<=target
                if hit_stop:
                    stt["exit"]=stop; stt["reason"]="SL / trailing SL"; continue
                if hit_target:
                    stt["exit"]=target; stt["reason"]="Target"; continue
                # Update stop after the candle closes; new stop applies from next candle.
                if side=="LONG": stt["best"]=max(stt["best"],hi); favorable=stt["best"]-entry
                else: stt["best"]=min(stt["best"],lo); favorable=entry-stt["best"]
                if name=="Breakeven at +1R" and favorable>=risk:
                    stt["stop"] = max(stt["stop"],entry) if side=="LONG" else min(stt["stop"],entry)
                elif name=="Step trailing (1R steps)":
                    steps=int(favorable//risk)
                    if steps>=1:
                        candidate=entry+(steps-1)*risk if side=="LONG" else entry-(steps-1)*risk
                        stt["stop"] = max(stt["stop"],candidate) if side=="LONG" else min(stt["stop"],candidate)
                elif name.startswith("ATR trailing") and favorable>=risk:
                    candidate=stt["best"]-atr_mult*float(bar.atr14) if side=="LONG" else stt["best"]+atr_mult*float(bar.atr14)
                    stt["stop"] = max(stt["stop"],candidate) if side=="LONG" else min(stt["stop"],candidate)
                elif name.startswith("Swing trailing") and favorable>=risk and j>=swing_lookback:
                    prior=path.iloc[j-swing_lookback:j]
                    candidate=float(prior.low.min()) if side=="LONG" else float(prior.high.max())
                    stt["stop"] = max(stt["stop"],candidate) if side=="LONG" else min(stt["stop"],candidate)
            if all(v["exit"] is not None for v in variants.values()): break
        for name, stt in variants.items():
            exit_price=float(stt["exit"]) if stt["exit"] is not None else float(path.iloc[-1].close)
            reason=stt["reason"] if stt["reason"] else "EOD"
            pnl=exit_price-entry if side=="LONG" else entry-exit_price
            rows.append({"Exit method":name,"trade_date":day,"direction":side,"entry":entry,"exit":exit_price,"exit_reason":reason,"pnl_points":pnl,"R_multiple":pnl/risk})
    detail=pd.DataFrame(rows)
    if detail.empty: return detail, pd.DataFrame()
    summary=[]
    for name,g in detail.groupby("Exit method",sort=False):
        r=g.R_multiple.astype(float); wins=r[r>0]; losses=r[r<0]; eq=r.cumsum(); dd=eq-eq.cummax().clip(lower=0)
        gp=wins.sum(); gl=abs(losses.sum())
        summary.append({"Exit method":name,"Trades":len(g),"Win rate %":(r>0).mean()*100,"Total R":r.sum(),"Expectancy (R)":r.mean(),"Profit factor":gp/gl if gl else np.inf,"Max drawdown (R)":abs(dd.min()) if len(dd) else 0,"Average win (R)":wins.mean() if len(wins) else np.nan,"Average loss (R)":losses.mean() if len(losses) else np.nan})
    return detail, pd.DataFrame(summary)


def stats_for(trades):
    p=trades.pnl_points.astype(float); wins=p[p>0]; losses=p[p<0]
    gp=wins.sum(); gl=abs(losses.sum())
    eq=p.cumsum(); dd=eq-eq.cummax().clip(lower=0)
    r=trades.R_multiple.astype(float)
    r_eq=r.cumsum()
    r_dd=r_eq-r_eq.cummax().clip(lower=0)
    return {"Trades":len(p),"Win rate":(p>0).mean(),"Expectancy (pts)":p.mean(),"Profit factor":gp/gl if gl else np.inf,"Average win (pts)":wins.mean() if len(wins) else np.nan,"Average loss (pts)":losses.mean() if len(losses) else np.nan,"Max drawdown (pts)":abs(dd.min()) if len(dd) else 0,"Total points":p.sum(),"Total R":r.sum(),"Average R / trade":r.mean(),"Max drawdown (R)":abs(r_dd.min()) if len(r_dd) else 0,"Lag-1 autocorrelation":p.autocorr(lag=1) if len(p)>2 else np.nan}


def optimizer_score(trades, min_trades=30):
    """Conservative score: reward OOS R while penalizing drawdown and thin samples."""
    if trades is None or trades.empty or len(trades) < min_trades:
        return -1e9
    r = trades["R_multiple"].astype(float)
    curve = r.cumsum()
    dd = curve - curve.cummax().clip(lower=0)
    total_r = float(r.sum())
    max_dd = abs(float(dd.min())) if len(dd) else 0.0
    expectancy = float(r.mean())
    pf_num = float(r[r > 0].sum())
    pf_den = abs(float(r[r < 0].sum()))
    pf = pf_num / pf_den if pf_den else 10.0
    # Score favours total R and expectancy, penalizes drawdown.
    return (total_r / max(1.0, max_dd)) + expectancy * 2.0 + min(pf, 5.0) * 0.05


def _data_fingerprint(df):
    clean=prepare_spot(df)
    cols=clean[["datetime","open","high","low","close"]].astype(str)
    return hashlib.sha256("\n".join(cols.astype(str).agg("|".join,axis=1)).encode()).hexdigest()


def _checkpoint_store():
    """SQLite fallback. Supabase Storage is the durable option on Streamlit Cloud."""
    db=os.environ.get("NQL_CHECKPOINT_DB","nql_checkpoints.sqlite3")
    con=sqlite3.connect(db,timeout=30)
    con.execute("CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, updated_at TEXT, payload TEXT)")
    con.commit(); return con


def _remote_checkpoint_enabled():
    try:
        return bool(st.secrets.get("SUPABASE_URL") and st.secrets.get("SUPABASE_SERVICE_ROLE_KEY"))
    except Exception:
        return False


def save_job_checkpoint(job_id, payload):
    payload={**payload,"job_id":job_id,"updated_at":datetime.utcnow().isoformat()+"Z"}
    raw=json.dumps(payload,default=str)
    con=_checkpoint_store()
    con.execute("INSERT INTO jobs(job_id,updated_at,payload) VALUES(?,?,?) ON CONFLICT(job_id) DO UPDATE SET updated_at=excluded.updated_at,payload=excluded.payload",(job_id,payload["updated_at"],raw)); con.commit(); con.close()
    if _remote_checkpoint_enabled():
        base=st.secrets["SUPABASE_URL"].rstrip("/")
        key=st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
        bucket=str(st.secrets.get("SUPABASE_BUCKET","nql-checkpoints"))
        url=f"{base}/storage/v1/object/{bucket}/{job_id}.json"
        r=requests.post(url,headers={"Authorization":f"Bearer {key}","apikey":key,"Content-Type":"application/json","x-upsert":"true"},data=raw.encode(),timeout=30)
        if r.status_code not in (200,201):
            raise RuntimeError(f"Supabase checkpoint save failed ({r.status_code}): {r.text[:300]}")


def load_job_checkpoint(job_id):
    if _remote_checkpoint_enabled():
        base=st.secrets["SUPABASE_URL"].rstrip("/"); key=st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
        bucket=str(st.secrets.get("SUPABASE_BUCKET","nql-checkpoints"))
        url=f"{base}/storage/v1/object/{bucket}/{job_id}.json"
        r=requests.get(url,headers={"Authorization":f"Bearer {key}","apikey":key},timeout=30)
        if r.status_code==200: return r.json()
        if r.status_code not in (404,400): raise RuntimeError(f"Supabase checkpoint load failed ({r.status_code}): {r.text[:300]}")
    con=_checkpoint_store(); row=con.execute("SELECT payload FROM jobs WHERE job_id=?",(job_id,)).fetchone(); con.close()
    return json.loads(row[0]) if row else None


def list_job_checkpoints():
    con=_checkpoint_store(); rows=con.execute("SELECT job_id,updated_at,payload FROM jobs ORDER BY updated_at DESC LIMIT 20").fetchall(); con.close()
    out=[]
    for jid,updated,raw in rows:
        try:
            d=json.loads(raw); out.append({"job_id":jid,"updated_at":updated,"status":d.get("status"),"progress":d.get("progress",0),"fingerprint":d.get("fingerprint"),"min_trades":d.get("min_trades")})
        except Exception: pass
    return out


def optimizer_score(trades, min_trades=30):
    if trades is None or trades.empty or len(trades)<min_trades: return -1e9
    r=trades["R_multiple"].astype(float); curve=r.cumsum(); dd=curve-curve.cummax().clip(lower=0)
    total_r=float(r.sum()); max_dd=abs(float(dd.min())) if len(dd) else 0.0; expectancy=float(r.mean())
    pf_num=float(r[r>0].sum()); pf_den=abs(float(r[r<0].sum())); pf=pf_num/pf_den if pf_den else 10.0
    return (total_r/max(1.0,max_dd))+expectancy*2.0+min(pf,5.0)*0.05


def optimize_parameters(df, min_trades=30, progress_callback=None, checkpoint_callback=None, resume_state=None):
    """Resumable optimizer. State is committed after every completed parameter evaluation."""
    clean=prepare_spot(df); dates=sorted(clean.trade_date.unique())
    if len(dates)<250: raise ValueError("Optimizer needs at least 250 trading sessions for meaningful chronological splits.")
    n=len(dates); train_end=dates[int(n*.60)-1]; valid_end=dates[int(n*.80)-1]
    train=clean[clean.trade_date<=train_end].copy(); valid=clean[(clean.trade_date>train_end)&(clean.trade_date<=valid_end)].copy(); test=clean[clean.trade_date>valid_end].copy()
    if valid.trade_date.nunique()<20 or test.trade_date.nunique()<20: raise ValueError("Not enough sessions in validation/test windows.")
    grids={"body":[20,30,40,50,60,75,100,125,150],"sl":[5,10,15,20,25,30],"rr":[1.,1.5,2.,2.5,3.,3.5,4.],"gap":[30,40,50,60,75,100],"cutoff":["10:00","10:30","11:00","11:30","12:00","13:00"]}
    base={"body":50.,"sl":10.,"rr":2.,"gap":50.,"cutoff":"11:00"}
    state=resume_state or {"phase":"screen","screened":{},"screen_done":{},"combo_done":{},"candidates":[],"valid_done":{},"ranked":[],"progress":0}
    total=sum(map(len,grids.values()))+3**5+20+1
    completed=0
    def save(msg):
        state["progress"]=min(.99,completed/max(1,total)); state["message"]=msg
        if checkpoint_callback: checkpoint_callback(state)
        if progress_callback: progress_callback(state["progress"],msg)
    def evaluate(frame,p):
        t,_,_=run_backtest(frame,1,"Next candle open",p["sl"],p["rr"],p["body"],p["gap"],p["cutoff"]); return t
    # Reconstruct completed work so progress remains monotonic after resume.
    completed=sum(len(v) for v in state.get("screen_done",{}).values())+len(state.get("combo_done",{}))+len(state.get("valid_done",{}))
    for key,values in grids.items():
        done=state.setdefault("screen_done",{}).setdefault(key,{})
        rows=list(state.get("screened",{}).get(key,[]))
        for value in values:
            sk=str(value)
            if sk in done: continue
            p=base.copy(); p[key]=value; tr=evaluate(train,p); score=optimizer_score(tr,min_trades)
            done[sk]=score; rows.append((score,value)); state["screened"][key]=rows; completed+=1; save(f"Screening {key}: completed {value}")
        # Checkpoint keys are strings; preserve time cutoffs as strings.
        # Only numeric optimizer dimensions should be converted to float.
        rows=sorted(
            [(float(score), value if key == "cutoff" else float(value))
             for value, score in done.items()],
            key=lambda z: z[0], reverse=True
        )
        state["screened"][key]=[v for score,v in rows[:3] if score>-1e8] or [base[key]]
    import itertools
    combos=list(itertools.product(*[state["screened"][k][:3] for k in ("body","sl","rr","gap","cutoff")]))
    for vals in combos:
        p={k:(float(v) if k!="cutoff" else v) for k,v in zip(("body","sl","rr","gap","cutoff"),vals)}
        cid="|".join(map(str,vals))
        if cid in state["combo_done"]: continue
        tr=evaluate(train,p); score=optimizer_score(tr,min_trades)
        state["combo_done"][cid]={"score":score,"params":p}; completed+=1; save(f"Testing combination {len(state['combo_done']):,} of {len(combos):,}")
    candidates=[v for v in state["combo_done"].values() if v["score"]>-1e8]
    candidates.sort(key=lambda z:z["score"],reverse=True)
    for item in candidates[:20]:
        p=item["params"]; cid="|".join(map(str,[p[k] for k in ("body","sl","rr","gap","cutoff")]))
        if cid in state["valid_done"]: continue
        vt=evaluate(valid,p); vs=optimizer_score(vt,max(10,min_trades//3))
        state["valid_done"][cid]={"params":p,"score":vs,"trades":len(vt),"total_r":float(vt.R_multiple.sum()) if len(vt) else 0.,"avg_r":float(vt.R_multiple.mean()) if len(vt) else np.nan,"max_dd":abs(float((vt.R_multiple.cumsum()-vt.R_multiple.cumsum().cummax().clip(lower=0)).min())) if len(vt) else 0.}
        completed+=1; save(f"Validating candidate {len(state['valid_done']):,} of {min(20,len(candidates)):,}")
    ranked=[]
    for v in state["valid_done"].values():
        p=v["params"]; ranked.append({**p,"Validation score":v["score"],"Validation trades":v["trades"],"Validation total R":v["total_r"],"Validation average R":v["avg_r"],"Validation max DD R":v["max_dd"]})
    if not ranked: raise ValueError("No parameter combination met the minimum trade requirement. Lower minimum trades or broaden data period.")
    ranked_df=pd.DataFrame(ranked).sort_values(["Validation score","Validation total R"],ascending=False).reset_index(drop=True)
    best={k:ranked_df.iloc[0][k] for k in ("body","sl","rr","gap","cutoff")}
    test_trades=evaluate(test,best); test_stats=stats_for(test_trades) if not test_trades.empty else {}
    if checkpoint_callback: checkpoint_callback({**state,"phase":"completed","progress":1.0,"message":"Optimization completed","ranked":ranked_df.to_dict("records"),"best":best,"test_stats":test_stats,"test_trades":test_trades.to_dict("records"),"splits":[len(train.trade_date.unique()),len(valid.trade_date.unique()),len(test.trade_date.unique())]})
    if progress_callback: progress_callback(1.0,"Optimization completed")
    return ranked_df,best,test_stats,len(train.trade_date.unique()),len(valid.trade_date.unique()),len(test.trade_date.unique()),test_trades


# In-process worker registry. The durable checkpoint is written outside Streamlit session state.
_NQL_WORKERS={}; _NQL_LOCK=threading.Lock()
def _run_optimizer_worker(job_id, frame, min_trades, fingerprint, resume_state=None):
    try:
        def persist(state):
            save_job_checkpoint(job_id,{"status":"running","fingerprint":fingerprint,"min_trades":min_trades,**state})
        def progress(frac,msg):
            # UI progress is read from the same durable checkpoint; do not overwrite optimizer state.
            return None
        result=optimize_parameters(frame,min_trades,progress_callback=progress,checkpoint_callback=persist,resume_state=resume_state)
        ranked,best,stats,nt,nv,nx,trades=result
        save_job_checkpoint(job_id,{"status":"completed","fingerprint":fingerprint,"min_trades":min_trades,"progress":1.0,"message":"Optimization completed","ranked":ranked.to_dict("records"),"best":best,"test_stats":stats,"test_trades":trades.to_dict("records"),"splits":[nt,nv,nx]})
    except Exception as e:
        old=load_job_checkpoint(job_id) or {}
        save_job_checkpoint(job_id,{**old,"status":"interrupted","error":str(e),"message":"Worker stopped. Resume is available from the last checkpoint."})


def start_optimizer_worker(job_id, frame, min_trades, fingerprint, resume_state=None):
    with _NQL_LOCK:
        th=_NQL_WORKERS.get(job_id)
        if th and th.is_alive(): return False
        th=threading.Thread(target=_run_optimizer_worker,args=(job_id,frame.copy(),min_trades,fingerprint,resume_state),daemon=True,name=f"NQL-{job_id[:8]}")
        _NQL_WORKERS[job_id]=th; th.start(); return True

KITE_API_ROOT = "https://api.kite.trade"
KITE_LOGIN_ROOT = "https://kite.zerodha.com/connect/login"

def kite_credentials():
    try:
        api_key = st.secrets["KITE_API_KEY"]
        api_secret = st.secrets["KITE_API_SECRET"]
        if api_key and api_secret:
            return str(api_key).strip(), str(api_secret).strip()
    except Exception:
        pass
    return None, None

def kite_headers(api_key, access_token=None):
    headers = {"X-Kite-Version": "3"}
    if access_token:
        headers["Authorization"] = f"token {api_key}:{access_token}"
    return headers

def kite_exchange_request_token(api_key, api_secret, request_token):
    checksum = hashlib.sha256(
        (api_key + request_token + api_secret).encode("utf-8")
    ).hexdigest()
    response = requests.post(
        f"{KITE_API_ROOT}/session/token",
        data={"api_key": api_key, "request_token": request_token, "checksum": checksum},
        headers={"X-Kite-Version": "3"},
        timeout=20,
    )
    payload = response.json()
    if response.status_code != 200 or payload.get("status") != "success":
        message = payload.get("message", response.text[:300])
        raise RuntimeError(f"Kite token exchange failed: {message}")
    return payload["data"]

def kite_get(api_key, access_token, path, params=None, timeout=30):
    response = requests.get(
        f"{KITE_API_ROOT}{path}",
        headers=kite_headers(api_key, access_token),
        params=params,
        timeout=timeout,
    )
    try:
        payload = response.json()
    except Exception:
        payload = {}
    if response.status_code != 200 or payload.get("status") == "error":
        message = payload.get("message", response.text[:300])
        raise RuntimeError(f"Kite API error ({response.status_code}): {message}")
    return payload.get("data", payload)

def kite_find_nifty_token(api_key, access_token):
    # Download the current instrument master and identify the NSE NIFTY 50 index.
    response = requests.get(
        f"{KITE_API_ROOT}/instruments/NSE",
        headers=kite_headers(api_key, access_token),
        timeout=45,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Could not download NSE instrument list: HTTP {response.status_code}")
    instruments = pd.read_csv(StringIO(response.text))
    required = {"instrument_token", "tradingsymbol", "segment", "exchange"}
    if not required.issubset(instruments.columns):
        raise RuntimeError("Unexpected Kite instrument master format.")
    matches = instruments[
        (instruments["exchange"].astype(str) == "NSE") &
        (instruments["tradingsymbol"].astype(str).str.upper().isin(["NIFTY 50", "NIFTY"]))
    ]
    if matches.empty:
        # Kite's documented NIFTY 50 index token is 256265; use only as a fallback
        # if the instrument master response omits index rows.
        return 256265, "NIFTY 50 (documented token fallback)"
    row = matches.iloc[0]
    return int(row["instrument_token"]), str(row["tradingsymbol"])

def kite_fetch_historical(api_key, access_token, instrument_token, start_date, end_date, interval="5minute"):
    if start_date > end_date:
        raise ValueError("Start date must be on or before end date.")
    all_rows = []
    cursor = start_date
    # Use small, conservative 30-day windows to avoid oversized requests.
    while cursor <= end_date:
        chunk_end = min(cursor + timedelta(days=29), end_date)
        params = {
            "from": cursor.strftime("%Y-%m-%d 09:00:00"),
            "to": chunk_end.strftime("%Y-%m-%d 16:00:00"),
            "oi": 1,
        }
        data = kite_get(
            api_key, access_token,
            f"/instruments/historical/{int(instrument_token)}/{interval}",
            params=params, timeout=45,
        )
        candles = data.get("candles", []) if isinstance(data, dict) else []
        all_rows.extend(candles)
        cursor = chunk_end + timedelta(days=1)
        if cursor <= end_date:
            time.sleep(0.35)
    columns = ["datetime", "open", "high", "low", "close", "volume", "oi"]
    if not all_rows:
        return pd.DataFrame(columns=columns)
    width = max(len(row) for row in all_rows)
    return pd.DataFrame(all_rows, columns=columns[:width])

# ---------------- Live signal engine + Telegram ----------------
@st.cache_resource
def _live_monitor_registry():
    return {"monitors": {}, "lock": threading.Lock()}

IST = ZoneInfo("Asia/Kolkata")

class NiftyLiveMonitor:
    """KiteTicker candle builder; signals are alert-only, never places orders."""
    def __init__(self, api_key, access_token, instrument_token, history, params, telegram_token, telegram_chat_id):
        self.api_key = api_key
        self.access_token = access_token
        self.instrument_token = int(instrument_token)
        self.history = prepare_spot(history).copy()
        self.history = self.history[self.history["datetime"].dt.date < datetime.now(IST).date()].copy()
        self.params = dict(params)
        self.telegram_token = telegram_token
        self.telegram_chat_id = str(telegram_chat_id)
        self.lock = threading.RLock()
        self.ticker = None
        self.thread_started = False
        self.connected = False
        self.error = ""
        self.status = "Prepared"
        self.current = None
        self.today_candles = []
        self.pending_signal = None
        self.last_signal = None
        self.last_alert_key = None
        self.last_tick_time = None
        self.last_price = None
        self.last_message = ""
        self.trade_date = None

    def _send_telegram(self, message):
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        response = requests.post(url, data={"chat_id": self.telegram_chat_id, "text": message}, timeout=15)
        payload = response.json()
        if response.status_code != 200 or not payload.get("ok"):
            raise RuntimeError(f"Telegram send failed: {payload.get('description', response.text[:200])}")

    def _notify_entry(self, pending, entry_price, entry_time):
        side = pending["signal"]
        signal_row = pending["row"]
        buffer = float(self.params["sl_buffer"])
        rr = float(self.params["rr"])
        stop = float(signal_row["low"])-buffer if side == "LONG" else float(signal_row["high"])+buffer
        risk = entry_price-stop if side == "LONG" else stop-entry_price
        if risk <= 0:
            self.last_message = "Signal skipped: next-candle entry was beyond the planned stop."
            return
        target = entry_price + rr*risk if side == "LONG" else entry_price - rr*risk
        msg = (
            f"NIFTY 50 {side} SIGNAL\n"
            f"Setup: {pending['setup']}\n"
            f"Signal candle close: {pending['signal_time']}\n"
            f"Entry (next candle first tick): {entry_price:.2f} at {entry_time.strftime('%H:%M:%S')} IST\n"
            f"Stop loss: {stop:.2f}\nTarget ({rr:g}R): {target:.2f}\n"
            f"Risk: {risk:.2f} Nifty points\n"
            f"Parameters: body {self.params['body']:g}, SL buffer {buffer:g}, gap {self.params['gap']:g}, cutoff {self.params['cutoff']}\n"
            "Alert only — no order has been placed. Verify the chart and execution before trading."
        )
        self._send_telegram(msg)
        self.last_signal = {"side":side,"setup":pending["setup"],"signal_time":pending["signal_time"],"entry_time":entry_time.isoformat(),"entry":entry_price,"stop":stop,"target":target,"risk_points":risk,"status":"Telegram alert sent"}
        self.last_message = "Telegram alert sent successfully."

    def _evaluate_closed_candle(self, candle):
        day = candle["datetime"].date()
        if self.trade_date != day:
            self.trade_date = day
            self.today_candles = []
            self.pending_signal = None
            self.last_alert_key = None
        self.today_candles.append(candle)
        if self.pending_signal is not None or self.last_alert_key is not None:
            return
        if self.history.empty:
            self.last_message = "Waiting for previous-session candles to calculate PDH/PDL."
            return
        live_df = pd.DataFrame(self.today_candles)
        combined = pd.concat([self.history, live_df], ignore_index=True, sort=False)
        try:
            sig = build_signals(combined, gap_threshold=self.params["gap"], cutoff=self.params["cutoff"], min_breakout_body=self.params["body"])
            today_rows = sig[sig["trade_date"] == day]
            if today_rows.empty:
                return
            hit = today_rows[today_rows["signal"].astype(str).str.len() > 0]
            if hit.empty:
                return
            row = hit.iloc[0]
            key = f"{day}|{row.datetime}|{row.signal}|{row.setup}"
            self.last_alert_key = key
            self.pending_signal = {"signal":row.signal,"setup":row.setup,"signal_time":str(row.datetime),"row":row.to_dict()}
            self.last_message = f"{row.signal} {row.setup} confirmed; waiting for next 5-minute candle open."
        except Exception as exc:
            self.error = f"Signal evaluation error: {exc}"

    def _on_ticks(self, ws, ticks):
        for tick in ticks:
            if int(tick.get("instrument_token", -1)) != self.instrument_token:
                continue
            ts = tick.get("exchange_timestamp") or tick.get("last_trade_time") or datetime.now(IST)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=IST)
            else:
                ts = ts.astimezone(IST)
            price = float(tick.get("last_price", 0) or 0)
            if price <= 0:
                continue
            # Nifty regular session only; ignore pre-open and post-close ticks.
            if not (ts.time() >= datetime.strptime("09:15", "%H:%M").time() and ts.time() <= datetime.strptime("15:30", "%H:%M").time()):
                continue
            bucket = ts.replace(minute=(ts.minute//5)*5, second=0, microsecond=0)
            with self.lock:
                self.last_tick_time = ts.isoformat(); self.last_price = price
                if self.current is None:
                    self.current = {"datetime":bucket,"open":price,"high":price,"low":price,"close":price,"volume":0,"oi":0}
                    continue
                if bucket > self.current["datetime"]:
                    completed = dict(self.current)
                    self._evaluate_closed_candle(completed)
                    self.current = {"datetime":bucket,"open":price,"high":price,"low":price,"close":price,"volume":0,"oi":0}
                    if self.pending_signal is not None:
                        pending = self.pending_signal
                        self.pending_signal = None
                        # Never carry a signal into the next trading session.
                        if pd.Timestamp(pending["signal_time"]).date() == ts.date():
                            try:
                                self._notify_entry(pending, price, ts)
                            except Exception as exc:
                                self.error = str(exc)
                                self.last_message = "Signal created but Telegram delivery failed; check bot settings and connection."
                        else:
                            self.last_message = "A signal at the previous session close expired; no late entry was sent."
                elif bucket == self.current["datetime"]:
                    self.current["high"] = max(self.current["high"], price)
                    self.current["low"] = min(self.current["low"], price)
                    self.current["close"] = price

    def _on_connect(self, ws, response):
        self.connected = True
        self.status = "Connected — receiving Nifty ticks"
        ws.subscribe([self.instrument_token])
        try:
            from kiteconnect import KiteTicker
            ws.set_mode(ws.MODE_FULL, [self.instrument_token])
        except Exception:
            pass

    def _on_close(self, ws, code, reason):
        self.connected = False
        self.status = f"Disconnected ({code}): {reason}"

    def _on_error(self, ws, code, reason):
        self.error = f"KiteTicker error ({code}): {reason}"

    def start(self):
        if self.thread_started:
            return
        try:
            from kiteconnect import KiteTicker
        except Exception as exc:
            raise RuntimeError("kiteconnect package is missing. Install the updated requirements.txt and reboot the app.") from exc
        self.ticker = KiteTicker(self.api_key, self.access_token)
        self.ticker.on_ticks = self._on_ticks
        self.ticker.on_connect = self._on_connect
        self.ticker.on_close = self._on_close
        self.ticker.on_error = self._on_error
        self.status = "Connecting to Kite live feed..."
        self.thread_started = True
        self.ticker.connect(threaded=True)

    def stop(self):
        try:
            if self.ticker:
                self.ticker.close()
        finally:
            self.connected = False
            self.status = "Stopped"
            self.thread_started = False

    def snapshot(self):
        with self.lock:
            return {"connected":self.connected,"status":self.status,"error":self.error,"last_price":self.last_price,"last_tick_time":self.last_tick_time,"last_message":self.last_message,"last_signal":dict(self.last_signal) if self.last_signal else None,"current_candle":dict(self.current) if self.current else None}


def render_live_signal_center(api_key, access_token):
    st.header("Live Nifty signals + Telegram alerts")
    st.caption("Alert-only mode. Signals are evaluated on completed 5-minute candles; no orders are placed.")
    tg_token = str(st.secrets.get("TELEGRAM_BOT_TOKEN", "")).strip() if hasattr(st, "secrets") else ""
    tg_chat = str(st.secrets.get("TELEGRAM_CHAT_ID", "")).strip() if hasattr(st, "secrets") else ""
    if not api_key or not access_token:
        st.info("Connect to Kite in the Data Center above before starting live signals.")
        return
    if not tg_token or not tg_chat:
        st.warning("Telegram is not configured yet. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to Streamlit Cloud → Settings → Secrets.")
        st.code('TELEGRAM_BOT_TOKEN = "123456:your_bot_token"\nTELEGRAM_CHAT_ID = "your_chat_id"', language="toml")
        st.caption("Create a bot with @BotFather, open the bot in Telegram and press Start, then obtain your chat ID from the bot updates. Never share the token in chat or commit it to GitHub.")
        return
    token_key = f"{api_key}:{access_token[-8:]}:256265"
    registry = _live_monitor_registry()
    with registry["lock"]:
        monitor = registry["monitors"].get(token_key)
    c1,c2=st.columns(2)
    with c1:
        if st.button("Start live signals", type="primary", use_container_width=True, key="start_live_signals"):
            try:
                if monitor is None or monitor.status == "Stopped":
                    instrument_token, symbol = kite_find_nifty_token(api_key, access_token)
                    hist = kite_fetch_historical(api_key, access_token, instrument_token, date.today()-timedelta(days=10), date.today(), "5minute")
                    if hist.empty:
                        raise RuntimeError("Kite returned no recent 5-minute candles. Check market-data access.")
                    hist["datetime"] = pd.to_datetime(hist["datetime"], errors="coerce")
                    monitor = NiftyLiveMonitor(api_key, access_token, instrument_token, hist, {"body":50.0,"sl_buffer":10.0,"rr":2.0,"gap":50.0,"cutoff":"11:00"}, tg_token, tg_chat)
                    with registry["lock"]:
                        registry["monitors"][token_key] = monitor
                monitor.start()
                try:
                    monitor._send_telegram("Nifty Quant Lab live monitor started.\nFeed: Nifty 50 spot / Kite Connect\nMode: alert-only; no orders will be placed.")
                except Exception as tg_exc:
                    st.warning(f"Kite feed started, but Telegram test message failed: {tg_exc}")
                st.success("Live signal monitor started.")
            except Exception as exc:
                st.error(f"Could not start live signals: {exc}")
    with c2:
        if st.button("Stop live signals", use_container_width=True, key="stop_live_signals"):
            if monitor:
                monitor.stop()
                st.success("Live signal monitor stopped.")
    @st.fragment(run_every="5s")
    def live_status_panel(key):
        with registry["lock"]:
            active_monitor = registry["monitors"].get(key)
        if active_monitor:
            snap=active_monitor.snapshot()
            st.metric("Feed status", snap["status"])
            m1,m2=st.columns(2)
            m1.metric("Latest Nifty price", f"{snap['last_price']:.2f}" if snap["last_price"] else "Waiting")
            m2.metric("Last tick (IST)", snap["last_tick_time"] or "Waiting")
            if snap["error"]: st.error(snap["error"])
            if snap["last_message"]: st.info(snap["last_message"])
            if snap["last_signal"]:
                st.subheader("Latest live signal")
                st.dataframe(pd.DataFrame([snap["last_signal"]]),use_container_width=True,hide_index=True)
            st.caption("Parameters: body 20 points • SL buffer 5 points • target 3.5R • gap 40 points • entry cutoff 10:30. These are your optimizer's leading validation settings, not a guarantee of future performance.")
        else:
            st.info("Live monitor is not running. Press Start live signals after Kite and Telegram are configured.")
    live_status_panel(token_key)

# ---------------- End live signal engine ----------------

def _supabase_kite_config():
    """Read server-only Supabase settings from Streamlit secrets."""
    try:
        url = str(st.secrets.get("SUPABASE_URL", "")).strip().rstrip("/")
        secret = str(st.secrets.get("SUPABASE_SECRET_KEY", "")).strip()
    except Exception:
        return None, None
    if not url or not secret:
        return None, None
    return url, secret


def _kite_token_cipher():
    """Derive a Fernet key from the server-only Supabase secret key."""
    try:
        from cryptography.fernet import Fernet
    except Exception as exc:
        raise RuntimeError("Missing cryptography package. Add cryptography>=42.0.0 to requirements.txt.") from exc
    _, secret = _supabase_kite_config()
    if not secret:
        raise RuntimeError("Supabase server secrets are not configured.")
    derived = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(derived)


def _supabase_kite_request(method, path, *, params=None, payload=None):
    url, secret = _supabase_kite_config()
    if not url or not secret:
        raise RuntimeError("Supabase is not configured. Add SUPABASE_URL and SUPABASE_SECRET_KEY in Streamlit Secrets.")
    headers = {"apikey": secret, "Content-Type": "application/json"}
    if method.upper() == "POST":
        headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
    response = requests.request(
        method.upper(), f"{url}/rest/v1/{path}", headers=headers,
        params=params, json=payload, timeout=15,
    )
    if response.status_code not in (200, 201, 204):
        detail = response.text[:250]
        raise RuntimeError(f"Supabase request failed ({response.status_code}): {detail}")
    if response.text.strip():
        try:
            return response.json()
        except Exception:
            return None
    return None


def _kite_token_expiry_ist():
    """Kite access tokens expire at 06:00 IST on the next applicable day."""
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(ist)
    expiry_day = now.date() if now.hour < 6 else now.date() + timedelta(days=1)
    return datetime.combine(expiry_day, datetime.min.time(), tzinfo=ist).replace(hour=6)


def save_kite_token_to_supabase(access_token):
    cipher = _kite_token_cipher()
    encrypted = cipher.encrypt(str(access_token).encode("utf-8")).decode("ascii")
    expiry = _kite_token_expiry_ist().isoformat()
    _supabase_kite_request(
        "POST", "kite_tokens", params={"on_conflict": "id"},
        payload={"id": "primary", "token_ciphertext": encrypted, "expires_at": expiry, "updated_at": datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()},
    )


def delete_kite_token_from_supabase():
    _supabase_kite_request("DELETE", "kite_tokens", params={"id": "eq.primary"})


def restore_kite_token_from_supabase(api_key):
    """Restore only a non-expired token and verify it with Kite before use."""
    rows = _supabase_kite_request(
        "GET", "kite_tokens", params={"id": "eq.primary", "select": "token_ciphertext,expires_at"}
    ) or []
    if not rows:
        return None
    row = rows[0]
    expiry_raw = row.get("expires_at")
    try:
        expiry = datetime.fromisoformat(str(expiry_raw).replace("Z", "+00:00"))
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        if datetime.now(expiry.tzinfo) >= expiry:
            delete_kite_token_from_supabase()
            return None
        cipher = _kite_token_cipher()
        token = cipher.decrypt(row["token_ciphertext"].encode("ascii")).decode("utf-8")
        profile = kite_get(api_key, token, "/user/profile", timeout=12)
        return {"access_token": token, "user_name": profile.get("user_name", "Zerodha user")}
    except Exception:
        # Expired, revoked, malformed, or undecryptable tokens are not reused.
        try:
            delete_kite_token_from_supabase()
        except Exception:
            pass
        return None


def render_kite_data_center():
    st.header("Kite Connect — Data Center")
    api_key, api_secret = kite_credentials()
    if not api_key or not api_secret:
        st.error("Kite credentials are not configured in Streamlit Secrets.")
        st.code('KITE_API_KEY = "your_api_key"\nKITE_API_SECRET = "your_api_secret"', language="toml")
        st.caption("Add these values under Streamlit Cloud → App settings → Secrets. Never put credentials in app.py or GitHub.")
        return

    # Restore the encrypted token after a browser/page reload. The token is
    # verified against Kite before it is placed back into this session.
    if not st.session_state.get("kite_access_token") and not st.session_state.get("kite_restore_attempted"):
        st.session_state["kite_restore_attempted"] = True
        try:
            restored = restore_kite_token_from_supabase(api_key)
            if restored:
                st.session_state["kite_access_token"] = restored["access_token"]
                st.session_state["kite_user_name"] = restored["user_name"]
                st.session_state["kite_login_time"] = "Restored from secure server storage"
                st.session_state["kite_token_persisted"] = True
                st.toast("Kite connection restored.", icon="✅")
        except Exception as exc:
            st.warning(f"Kite token could not be restored from Supabase: {exc}")

    # Process the one-time request_token returned to the registered redirect URL.
    request_token = st.query_params.get("request_token")
    login_status = st.query_params.get("status")
    if request_token and not st.session_state.get("kite_access_token"):
        try:
            session_data = kite_exchange_request_token(api_key, api_secret, str(request_token))
            st.session_state["kite_access_token"] = session_data["access_token"]
            st.session_state["kite_user_name"] = session_data.get("user_name", "Zerodha user")
            st.session_state["kite_login_time"] = session_data.get("login_time", "")
            try:
                save_kite_token_to_supabase(session_data["access_token"])
                st.session_state["kite_token_persisted"] = True
            except Exception as persist_exc:
                st.session_state["kite_token_persisted"] = False
                st.warning(f"Kite connected, but secure reload recovery could not be saved: {persist_exc}")
            st.query_params.clear()
            st.success("Kite authentication successful.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
            st.query_params.clear()
    elif login_status == "success" and not request_token:
        st.warning("Kite returned without a request_token. Please start login again.")

    access_token = st.session_state.get("kite_access_token")
    if access_token:
        st.success(f"Connected to Kite as {st.session_state.get('kite_user_name', 'Zerodha user')}")
        if st.session_state.get("kite_token_persisted"):
            st.caption("Secure reload recovery is enabled. Kite tokens still expire daily; a fresh Zerodha login is required after expiry.")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Test Kite connection", use_container_width=True):
                try:
                    profile = kite_get(api_key, access_token, "/user/profile")
                    st.session_state["kite_user_name"] = profile.get("user_name", "Zerodha user")
                    st.success(f"Connection verified for {profile.get('user_id', 'your account')}.")
                except Exception as exc:
                    st.error(str(exc))
        with c2:
            if st.button("Disconnect this session", use_container_width=True):
                try:
                    delete_kite_token_from_supabase()
                except Exception as exc:
                    st.warning(f"Could not remove the saved Kite token from Supabase: {exc}")
                st.session_state.pop("kite_access_token", None)
                st.session_state.pop("kite_user_name", None)
                st.session_state.pop("kite_login_time", None)
                st.session_state.pop("kite_token_persisted", None)
                st.session_state["kite_restore_attempted"] = True
                st.rerun()

        st.divider()
        st.subheader("Download Nifty 50 historical candles")
        st.caption("This first integration milestone downloads Nifty spot/index candles. Expired option contract history is a separate validation task.")
        d1, d2 = st.columns(2)
        with d1:
            start_date = st.date_input("Start date", value=date(2020, 1, 1), min_value=date(2010, 1, 1), key="kite_start")
        with d2:
            end_date = st.date_input("End date", value=date.today(), key="kite_end")
        interval = st.selectbox("Candle interval", ["5minute", "minute", "15minute", "day"], index=0, key="kite_interval")
        if st.button("Fetch Nifty historical data", type="primary", use_container_width=True):
            try:
                with st.spinner("Reading Kite instrument master and downloading candles..."):
                    token, symbol = kite_find_nifty_token(api_key, access_token)
                    candles = kite_fetch_historical(api_key, access_token, token, start_date, end_date, interval)
                if candles.empty:
                    st.warning("Kite returned no candles for this range. Check dates and API access.")
                else:
                    candles["datetime"] = pd.to_datetime(candles["datetime"], errors="coerce")
                    candles = candles.dropna(subset=["datetime"]).sort_values("datetime").drop_duplicates("datetime")
                    st.session_state["kite_nifty_candles"] = candles
                    st.session_state["kite_nifty_symbol"] = symbol
                    st.session_state["kite_nifty_token"] = token
                    st.success(f"Downloaded {len(candles):,} candles for {symbol} (instrument token {token}).")
            except Exception as exc:
                st.error(f"Historical download failed: {exc}")

        if "kite_nifty_candles" in st.session_state:
            candles = st.session_state["kite_nifty_candles"]
            st.write(f"Stored in this app session: **{len(candles):,} candles**")
            st.dataframe(candles.head(20), use_container_width=True, hide_index=True)
            st.download_button(
                "Download Nifty candles CSV",
                candles.to_csv(index=False).encode("utf-8"),
                file_name="nifty_50_kite_historical.csv",
                mime="text/csv",
                use_container_width=True,
            )
    else:
        login_url = f"{KITE_LOGIN_ROOT}?v=3&api_key={api_key}"
        st.info("Credentials found. Sign in to Zerodha to connect this browser session.")
        st.link_button("Connect to Zerodha", login_url, use_container_width=True)
        st.caption("After login, Zerodha redirects to the registered Streamlit URL with a short-lived request_token. The app exchanges it server-side; the API secret is never sent to the browser.")

# ---------------- End Kite Connect integration ----------------



# ---------------- BTC Quant module ----------------
from btc_module import fetch_delta_candles, btc_backtest, optimize_btc, summarize_btc, latest_btc_signal

def _btc_telegram_send(token, chat_id, message):
    response=requests.post(f"https://api.telegram.org/bot{token}/sendMessage",json={"chat_id":chat_id,"text":message},timeout=15)
    payload=response.json()
    if not payload.get("ok"): raise RuntimeError(payload.get("description","Telegram delivery failed"))

def render_btc_quant_module():
    st.header("₿ BTC Quant Lab — Delta Exchange")
    st.caption("15-minute BTCUSD research • EMA + Stochastic RSI • Independent BTC statistics • Alert-only; no orders")
    tg_token=str(st.secrets.get("TELEGRAM_BOT_TOKEN","")).strip() if hasattr(st,"secrets") else ""
    tg_chat=str(st.secrets.get("TELEGRAM_CHAT_ID","")).strip() if hasattr(st,"secrets") else ""
    c1,c2,c3=st.columns(3)
    with c1: btc_start=st.date_input("BTC history from",value=date(2020,1,1),key="btc_start")
    with c2: btc_end=st.date_input("BTC history to",value=date.today(),key="btc_end")
    with c3: btc_symbol=st.selectbox("Delta symbol",["BTCUSD"],key="btc_symbol")
    st.caption("Delta public historical candles are fetched in batches of at most 2,000. API market-data access is public; no order API key is used.")
    if st.button("Fetch BTC 15-minute history",type="primary",use_container_width=True,key="btc_fetch"):
        try:
            with st.spinner("Downloading BTCUSD candles from Delta Exchange..."):
                candles=fetch_delta_candles(datetime.combine(btc_start,datetime.min.time()),datetime.combine(btc_end+timedelta(days=1),datetime.min.time()),btc_symbol,"15m")
            if candles.empty: st.warning("Delta returned no candles for this range.")
            else:
                st.session_state["btc_candles"]=candles
                st.success(f"Downloaded {len(candles):,} BTC 15-minute candles.")
        except Exception as exc: st.error(f"Delta download failed: {exc}")
    uploaded=st.file_uploader("Or upload BTC OHLC CSV (datetime, open, high, low, close; optional volume)",type=["csv"],key="btc_csv")
    if uploaded is not None:
        try:
            raw=pd.read_csv(uploaded)
            if "datetime" not in raw.columns and "time" in raw.columns: raw=raw.rename(columns={"time":"datetime"})
            raw["datetime"]=pd.to_datetime(raw["datetime"],errors="coerce")
            for col in ["open","high","low","close"]: raw[col]=pd.to_numeric(raw[col],errors="coerce")
            raw=raw.dropna(subset=["datetime","open","high","low","close"]).sort_values("datetime").drop_duplicates("datetime")
            st.session_state["btc_candles"]=raw.reset_index(drop=True)
            st.success(f"Loaded {len(raw):,} BTC candles from CSV.")
        except Exception as exc: st.error(f"CSV error: {exc}")
    data=st.session_state.get("btc_candles")
    if data is None or data.empty:
        st.info("Fetch Delta history or upload a BTC OHLC CSV to begin BTC research.")
        return
    st.write(f"**BTC dataset:** {len(data):,} candles · {data.datetime.min()} to {data.datetime.max()}")
    st.download_button("Download BTC candles CSV",data.to_csv(index=False).encode(),"btc_delta_15m.csv","text/csv",key="btc_data_export")
    st.subheader("Strategy parameters")
    defaults={"ema_fast":9,"ema_slow":20,"rsi_period":14,"stoch_period":14,"smooth_k":3,"smooth_d":3,"sl_atr":1.5,"rr":2.0}
    chosen=st.session_state.get("btc_best_params",defaults.copy())
    pcols=st.columns(4)
    with pcols[0]: ema_fast=st.number_input("EMA fast",5,30,int(chosen["ema_fast"]),key="btc_ema_fast")
    with pcols[1]: ema_slow=st.number_input("EMA slow",10,60,int(chosen["ema_slow"]),key="btc_ema_slow")
    with pcols[2]: rsi_period=st.number_input("RSI period",5,30,int(chosen["rsi_period"]),key="btc_rsi")
    with pcols[3]: stoch_period=st.number_input("Stoch RSI period",5,30,int(chosen["stoch_period"]),key="btc_stoch")
    pcols=st.columns(4)
    with pcols[0]: smooth_k=st.number_input("Smooth K",1,10,int(chosen["smooth_k"]),key="btc_k")
    with pcols[1]: smooth_d=st.number_input("Smooth D",1,10,int(chosen["smooth_d"]),key="btc_d")
    with pcols[2]: sl_atr=st.number_input("Stop loss ATR ×",0.5,5.0,float(chosen["sl_atr"]),0.25,key="btc_sl_atr")
    with pcols[3]: rr=st.number_input("Target R:R",0.5,6.0,float(chosen["rr"]),0.25,key="btc_rr")
    params={"ema_fast":int(ema_fast),"ema_slow":int(ema_slow),"rsi_period":int(rsi_period),"stoch_period":int(stoch_period),"smooth_k":int(smooth_k),"smooth_d":int(smooth_d),"sl_atr":float(sl_atr),"rr":float(rr)}
    fee,slip=st.columns(2)
    with fee: fee_bps=st.number_input("Estimated fees (bps per side)",0.0,50.0,6.0,0.5,key="btc_fee")
    with slip: slip_bps=st.number_input("Estimated slippage (bps per side)",0.0,50.0,2.0,0.5,key="btc_slip")
    st.caption("LONG: EMA fast > EMA slow AND Stoch RSI %K > %D. SHORT: EMA fast < EMA slow AND %K < %D. Entry is next candle open after the condition first becomes true.")
    if st.button("Run BTC backtest",use_container_width=True,key="btc_backtest"):
        if params["ema_fast"]>=params["ema_slow"]: st.error("EMA fast must be smaller than EMA slow.")
        else:
            trades=btc_backtest(data,params,fee_bps,slip_bps)
            st.session_state["btc_trades"]=trades; st.session_state["btc_test_params"]=params
    if "btc_trades" in st.session_state:
        trades=st.session_state["btc_trades"]; stats=summarize_btc(trades)
        st.subheader("BTC backtest results")
        cols=st.columns(5)
        for col,(label,key,fmt) in zip(cols,[("Trades","Trades","{:,.0f}"),("Win rate","Win rate %","{:.1f}%"),("Net P&L","Net PnL","{:,.2f}"),("Profit factor","Profit factor","{:.2f}"),("Max drawdown","Max DD","{:,.2f}")]): col.metric(label,fmt.format(stats[key]))
        st.metric("Expectancy per trade",f"{stats['Expectancy']:.2f} USD price points",delta=f"Total R {stats['Total R']:.2f}")
        st.dataframe(trades,use_container_width=True,hide_index=True)
        st.download_button("Download BTC trade log",trades.to_csv(index=False).encode(),"btc_backtest_trades.csv","text/csv",key="btc_trade_export")
    st.divider(); st.subheader("BTC parameter optimizer")
    st.warning("Optimizer ranks candidates on the middle validation period only. The final 20% is held out and evaluated once for the selected candidate. This reduces, but does not eliminate, overfitting.")
    oc1,oc2=st.columns(2)
    with oc1: n_candidates=st.slider("Random parameter combinations",50,1000,300,50,key="btc_opt_n")
    with oc2: min_trades=st.number_input("Minimum training trades",5,500,30,5,key="btc_opt_min")
    if st.button("Optimize BTC parameters",type="primary",use_container_width=True,key="btc_optimize"):
        if params["ema_fast"]>=params["ema_slow"]: st.error("EMA fast must be smaller than EMA slow.")
        else:
            try:
                progress=st.progress(0.0,text="Preparing BTC optimization...")
                status=st.empty()
                def _btc_progress(done,total,passed):
                    pct=done/max(1,total)
                    progress.progress(pct,text=f"Optimization: {done:,} / {total:,} trials ({pct:.0%})")
                    status.caption(f"Completed: {done:,}/{total:,}  •  Candidates passing trade-count filters: {passed:,}")
                ranking,best,period_stats,final_trades=optimize_btc(
                    data,n_candidates,fee_bps,slip_bps,int(min_trades),
                    progress_callback=_btc_progress,
                    checkpoint_path="btc_optimizer_checkpoint.json")
                st.session_state["btc_optimizer_ranking"]=ranking; st.session_state["btc_best_params"]=best
                st.session_state["btc_optimizer_stats"]=period_stats; st.session_state["btc_optimizer_final_trades"]=final_trades
                progress.progress(1.0,text="Optimization complete")
                st.success("Optimization complete. Best validation candidate loaded into BTC parameters; review OOS results before using alerts.")
            except Exception as exc:
                st.error(f"Optimization paused or failed: {exc}")
                st.info("If the app restarts, run Optimize again with the same data and settings. A matching local checkpoint will resume from completed trials, if the Streamlit container retained it.")
    if "btc_optimizer_ranking" in st.session_state:
        st.subheader("Best candidate — chronological split")
        st.write("**Selected parameters:**",st.session_state["btc_best_params"])
        st.dataframe(pd.DataFrame([{"Period":k,**v} for k,v in st.session_state["btc_optimizer_stats"].items()]),use_container_width=True,hide_index=True)
        st.write("Top validation candidates")
        st.dataframe(st.session_state["btc_optimizer_ranking"].head(25),use_container_width=True,hide_index=True)
        st.download_button("Download optimizer ranking",st.session_state["btc_optimizer_ranking"].to_csv(index=False).encode(),"btc_optimizer_ranking.csv","text/csv",key="btc_rank_export")
        ft=st.session_state["btc_optimizer_final_trades"]
        st.download_button("Download untouched final-test trades",ft.to_csv(index=False).encode(),"btc_final_test_trades.csv","text/csv",key="btc_final_export")
    st.divider(); st.subheader("Live BTC alerts — Telegram")
    if not tg_token or not tg_chat:
        st.warning("Telegram secrets are missing. Existing Nifty Telegram settings can be reused: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
    else:
        st.success("Telegram credentials found. Alerts are signal-only; no Delta orders are sent.")
    live_on=st.toggle("Enable BTC live signal polling",value=False,key="btc_live_toggle")
    if live_on:
        if not tg_token or not tg_chat: st.error("Configure Telegram secrets before enabling live alerts.")
        else:
            live_params=st.session_state.get("btc_best_params",params)
            @st.fragment(run_every="30s")
            def btc_live_panel():
                try:
                    now=datetime.now(); recent=fetch_delta_candles(now-timedelta(days=5),now+timedelta(minutes=1),btc_symbol,"15m")
                    if len(recent)<50: st.warning("Waiting for sufficient recent Delta candles."); return
                    sig,calc=latest_btc_signal(recent,live_params)
                    st.metric("Latest BTCUSD close",f"{float(calc.iloc[-2].close):,.2f}")
                    st.caption(f"EMA {live_params['ema_fast']}/{live_params['ema_slow']} · Stoch RSI {live_params['rsi_period']}/{live_params['stoch_period']}/{live_params['smooth_k']}/{live_params['smooth_d']} · Last completed candle: {calc.iloc[-2].datetime}")
                    if sig:
                        alert_key=f"{sig['side']}:{sig['time']}"
                        if st.session_state.get("btc_last_alert_key")!=alert_key:
                            msg=(f"BTC Quant Lab {sig['side']} signal\nSymbol: BTCUSD / Delta India\nTimeframe: 15m\nCompleted candle: {sig['time']}\nClose: {sig['close']:.2f}\nEMA fast/slow: {sig['ema_fast']:.2f} / {sig['ema_slow']:.2f}\nStoch RSI K/D: {sig['stoch_k']:.2f} / {sig['stoch_d']:.2f}\nMode: ALERT ONLY — no order placed")
                            _btc_telegram_send(tg_token,tg_chat,msg); st.session_state["btc_last_alert_key"]=alert_key
                            st.success("New BTC signal sent to Telegram."); st.dataframe(pd.DataFrame([sig]),use_container_width=True,hide_index=True)
                    else: st.info("No new BTC signal on the last completed candle.")
                except Exception as exc: st.error(f"BTC live data/alert error: {exc}")
            btc_live_panel()
    else: st.info("Live polling is off. Enable it to check completed 15-minute candles every 30 seconds while the app is open.")


# ---------------- v3.0 unified market and strategy navigation ----------------
st.divider()
st.header("Research workspace")
market_col, strategy_col = st.columns([1, 2])
with market_col:
    selected_market = st.selectbox("1. Select market", ["NIFTY 50", "BTC"], key="v30_market")
with strategy_col:
    strategy_options = (["NIFTY PDHL"] if selected_market == "NIFTY 50"
                        else ["BTC EMA + Stoch RSI", "BTC PDHL"])
    selected_strategy = st.selectbox("2. Select strategy", strategy_options, key="v30_strategy")
st.caption("Choose the market and strategy here. Each strategy retains its existing data controls and parameters below.")
# ---------------------------------------------------------------------------

if selected_market == "BTC" and selected_strategy == "BTC EMA + Stoch RSI":
    render_btc_quant_module()
elif selected_market == "BTC" and selected_strategy == "BTC PDHL":
    render_btc_pdhl_module()


if selected_market == "NIFTY 50":
    with st.expander("Kite Connect data integration", expanded=False):
        render_kite_data_center()

    api_key_live, _ = kite_credentials()
    render_live_signal_center(api_key_live, st.session_state.get("kite_access_token"))

    st.header("PDHL Strategy Settings")
    c1,c2,c3=st.columns(3)
    with c1: st.metric("Gap reference candle", "First 5 minutes")
    with c2: max_trades=1; st.metric("Maximum trades per day", "1")
    with c3: rr=st.number_input("Reward-to-risk",1.0,5.0,float(st.session_state.get("rr",2.0)),0.5,key="rr")
    st.caption("Momentum body minimum applies only to the first breakout candle. Every entry candle must pass the directional wick filter. Two-close breakout SL uses the entry candle extreme; other setup SL uses its signal candle extreme.")
    min_breakout_body=st.number_input("Minimum breakout / confirmation candle body (points)", min_value=5.0, max_value=300.0, value=float(st.session_state.get("min_breakout_body",50.0)), step=5.0, key="min_breakout_body", help="Applied to the first breakout candle for momentum only; if it fails, the immediately following candle may confirm with a second close and must pass the wick filter. Trap, retest and gap-reversal setups are not filtered by this setting.")
    sl_buffer=st.number_input("Stop-loss buffer (points)",0.0,100.0,float(st.session_state.get("sl_buffer",10.0)),1.0,key="sl_buffer")
    gap_threshold=st.number_input("Large-gap threshold (points)",10.0,200.0,float(st.session_state.get("gap_threshold",50.0)),5.0,key="gap_threshold")
    cutoff=st.selectbox("Large-gap reversal cutoff",["10:00","10:30","11:00","11:30","12:00","12:30","13:00"],index=2,key="gap_cutoff")
    source=st.radio("Data source",["Use downloaded Kite candles","Upload Nifty spot CSV"],horizontal=True)
    data=None
    if source=="Use downloaded Kite candles":
        if "kite_nifty_candles" in st.session_state:
            data=st.session_state["kite_nifty_candles"].copy()
            st.success(f"Using {len(data):,} candles from Kite session.")
        else: st.info("Download candles in the Kite Connect section above, or choose CSV upload.")
    else:
        f=st.file_uploader("Upload Nifty spot 5-minute CSV",type="csv")
        if f is not None:
            try: data=pd.read_csv(f); st.success(f"Loaded {len(data):,} rows.")
            except Exception as e: st.error(f"CSV read error: {e}")
    if data is not None:
        try:
            st.subheader("Automatic parameter research")
            st.caption("The optimizer searches breakout body, SL buffer, target R:R, gap threshold and reversal cutoff. It selects settings on training/validation data and reports the final untouched test period separately.")
            optimizer_min_trades=st.number_input("Minimum trades required per candidate", min_value=10, max_value=500, value=50, step=10, key="optimizer_min_trades")
            fp=_data_fingerprint(data)
            job_id="nql_"+fp[:24]
            remote_ok=_remote_checkpoint_enabled()
            if remote_ok:
                st.success("Durable checkpoints: Supabase Storage connected.")
            else:
                st.warning("Local checkpoint mode: browser disconnect recovery works while this app instance stays alive. For recovery after Streamlit Cloud restart/redeploy, configure Supabase Storage in Secrets (instructions in README).")
            if st.button("Start / Resume crash-safe optimizer", type="secondary", use_container_width=True):
                try:
                    prior=load_job_checkpoint(job_id)
                    if prior and prior.get("fingerprint")!=fp: raise ValueError("Saved job data fingerprint does not match the current CSV. Upload the original data file.")
                    if prior and prior.get("min_trades")!=int(optimizer_min_trades): raise ValueError("Saved job uses a different minimum-trades setting. Keep the original setting or start a fresh job.")
                    if prior and prior.get("status")=="completed": st.info("This dataset already has a completed optimizer result. Showing the saved result below.")
                    else:
                        resume=prior if prior and prior.get("phase") not in (None,"completed") else None
                        save_job_checkpoint(job_id,{**(prior or {}),"status":"running","fingerprint":fp,"min_trades":int(optimizer_min_trades),"message":"Worker starting"})
                        start_optimizer_worker(job_id,data,int(optimizer_min_trades),fp,resume)
                        st.success("Background optimizer started. You can leave this page; the worker runs independently of the browser connection while the app process remains alive.")
                except Exception as e: st.error(str(e))
            try:
                job=load_job_checkpoint(job_id)
            except Exception as e:
                job=None; st.error(f"Could not read checkpoint: {e}")
            if job and job.get("status") in ("running","interrupted"):
                with _NQL_LOCK:
                    live=_NQL_WORKERS.get(job_id)
                    alive=bool(live and live.is_alive())
                if not alive:
                    try:
                        start_optimizer_worker(job_id,data,int(optimizer_min_trades),fp,job)
                        job=load_job_checkpoint(job_id) or job
                        st.info("Automatic recovery: restarting the saved optimizer from its last completed parameter combination.")
                    except Exception as recovery_error:
                        st.error(f"Automatic recovery could not start: {recovery_error}")
            if job:
                pct=int(max(0,min(100,round(float(job.get("progress",0))*100))))
                st.progress(pct,text=f"Optimizer: {pct}% — {job.get('message','')}")
                st.caption(f"Job: {job_id} • Status: {job.get('status','unknown')} • Last saved: {job.get('updated_at','local checkpoint')}")
                if job.get("error"): st.error(job["error"])
                if job.get("status")=="completed" and job.get("best"):
                    best=job["best"]; st.markdown("#### Recommended parameters")
                    bcols=st.columns(5)
                    for col,(label,key,suffix) in zip(bcols,[("Body minimum","body"," pts"),("SL buffer","sl"," pts"),("Target","rr","R"),("Gap threshold","gap"," pts"),("Entry cutoff","cutoff","")]): col.metric(label,f"{best[key]}{suffix}")
                    n_train,n_valid,n_test=job.get("splits",[0,0,0])
                    st.caption(f"Chronological split: training {n_train:,} sessions (60%), validation {n_valid:,} sessions (20%), final test {n_test:,} sessions (20%).")
                    st.markdown("#### Validation-ranked candidates")
                    ranked_df=pd.DataFrame(job.get("ranked",[])); st.dataframe(ranked_df,use_container_width=True,hide_index=True)
                    st.markdown("#### Untouched final test results")
                    test_trades_df=pd.DataFrame(job.get("test_trades",[]))
                    st.markdown("#### Compounding capital simulation")
                    st.caption("Risk is recalculated as a percentage of current capital before every trade. This converts spot-based R results into a hypothetical account curve; it is not actual options P&L.")
                    cap1,cap2=st.columns(2)
                    with cap1: initial_capital=st.number_input("Initial capital (₹)",min_value=1000.0,value=100000.0,step=5000.0,key="opt_initial_capital")
                    with cap2: risk_pct=st.number_input("Risk per trade (% of current capital)",min_value=0.1,max_value=10.0,value=1.0,step=0.1,key="opt_risk_pct")
                    compound_curve,final_capital=compound_capital_path(test_trades_df,initial_capital,risk_pct)
                    if not compound_curve.empty:
                        net_profit=final_capital-float(initial_capital)
                        peak=compound_curve["Capital after"].cummax().clip(lower=float(initial_capital))
                        dd_cash=compound_curve["Capital after"]-peak
                        mdd_cash=abs(float(dd_cash.min())) if len(dd_cash) else 0.0
                        mc=st.columns(4)
                        mc[0].metric("Final capital",f"₹{final_capital:,.2f}")
                        mc[1].metric("Net profit / loss",f"₹{net_profit:,.2f}",delta=f"{net_profit/float(initial_capital)*100:.2f}%")
                        mc[2].metric("Maximum drawdown (₹)",f"₹{mdd_cash:,.2f}")
                        mc[3].metric("Risk on first trade",f"₹{float(initial_capital)*float(risk_pct)/100:,.2f}")
                        st.line_chart(compound_curve.set_index("Trade")["Capital after"])
                        st.download_button("Download compounding capital trade log CSV",compound_curve.to_csv(index=False).encode(),"nql_compounding_capital.csv","text/csv")
                    ts=job.get("test_stats",{})
                    if ts:
                        tcols=st.columns(4)
                        for col,key in zip(tcols,["Trades","Total R","Average R / trade","Max drawdown (R)"]):
                            val=ts.get(key,np.nan); col.metric(key,f"{val:.2f}" if isinstance(val,(int,float)) and np.isfinite(val) else str(val))
                    st.download_button("Download optimizer candidate report CSV",ranked_df.to_csv(index=False).encode(),"nql_optimizer_candidates.csv","text/csv")
                    st.download_button("Download final test trades CSV",test_trades_df.to_csv(index=False).encode(),"nql_optimizer_test_trades.csv","text/csv")
                elif job.get("status")=="running": st.info("Optimizer is running in the background. Refresh this page to see the latest saved progress.")
                elif job.get("status")=="interrupted": st.warning("The previous worker stopped. Press Start / Resume to continue from the last completed parameter evaluation.")

            if st.button("Run PDHL backtest",type="primary",use_container_width=True):
                bt_progress=st.progress(0, text="Backtest progress: 0%")
                bt_status=st.empty()
                def backtest_progress(fraction, message):
                    pct=int(max(0,min(100,round(fraction*100))))
                    bt_progress.progress(pct, text=f"Backtest progress: {pct}%")
                    bt_status.caption(message)
                trades,equity,signals=run_backtest(data,int(max_trades),"Next candle open",float(sl_buffer),float(rr),float(min_breakout_body),float(gap_threshold),str(cutoff),progress_callback=backtest_progress)
                bt_progress.progress(100, text="Backtest progress: 100% — complete")
                bt_status.success("Backtest finished. Preparing performance report…")
                if trades.empty:
                    st.warning("No trades generated. Check data coverage and strategy conditions.")
                    st.session_state.pop("nql_backtest_results", None)
                else:
                    # Persist results across Streamlit reruns, including CSV download clicks.
                    st.session_state["nql_backtest_results"] = {
                        "trades": trades.copy(), "signals": signals.copy(),
                        "data_fingerprint": fp, "created_at": datetime.now().isoformat()
                    }
                    st.success(f"Backtest complete: {len(trades):,} trades")

            saved_bt=st.session_state.get("nql_backtest_results")
            if saved_bt is not None:
                if saved_bt.get("data_fingerprint") != fp:
                    st.info("A previous backtest result is saved for a different dataset. Run the backtest again to refresh the reports.")
                else:
                    trades=saved_bt["trades"].copy()
                    signals=saved_bt["signals"].copy()
                    stats=stats_for(trades)
                    st.divider()
                    st.header("Backtest results and downloads")
                    st.caption(f"Results retained for this dataset • Generated {saved_bt.get('created_at','')}")
                    st.subheader("TradingView-style chart with actual trade drawings")
                    render_tradingview_chart(signals, trades=trades, chart_key="nifty_backtest_chart")
                    cols=st.columns(4)
                    for col,(k,v) in zip(cols,list(stats.items())[:4]): col.metric(k, f"{v:.2f}" if isinstance(v,(float,np.floating)) and np.isfinite(v) else str(v))
                    cols=st.columns(4)
                    for col,(k,v) in zip(cols,list(stats.items())[4:8]): col.metric(k, f"{v:.2f}" if isinstance(v,(float,np.floating)) and np.isfinite(v) else str(v))
                    st.subheader("Risk-normalized performance — R multiples")
                    rcols=st.columns(3)
                    rcols[0].metric("Total R", f"{stats['Total R']:+.2f}R")
                    rcols[1].metric("Average R / trade", f"{stats['Average R / trade']:+.3f}R")
                    rcols[2].metric("Maximum drawdown", f"{stats['Max drawdown (R)']:.2f}R")
                    st.caption("1R = the initial entry-to-stop risk for that individual trade. R excludes brokerage, slippage and other costs.")
                    st.subheader("Equity curve — cumulative R")
                    st.line_chart(trades.set_index("exit_time")["cumulative_R"])
                    st.subheader("Equity curve — cumulative Nifty points")
                    st.line_chart(trades.set_index("exit_time")["cumulative_points"])

                    st.subheader("Compounding capital simulation")
                    st.caption("Risk is recalculated as a percentage of current capital before every trade. This is a hypothetical spot-R account simulation, not actual options P&L.")
                    cap1,cap2=st.columns(2)
                    with cap1: initial_capital=st.number_input("Initial capital (₹)",min_value=1000.0,value=100000.0,step=5000.0,key="bt_initial_capital")
                    with cap2: risk_pct=st.number_input("Risk per trade (% of current capital)",min_value=0.1,max_value=10.0,value=1.0,step=0.1,key="bt_risk_pct")
                    compound_curve,final_capital=compound_capital_path(trades,initial_capital,risk_pct)
                    if not compound_curve.empty:
                        net_profit=final_capital-float(initial_capital)
                        peak=compound_curve["Capital after"].cummax().clip(lower=float(initial_capital))
                        dd_cash=compound_curve["Capital after"]-peak
                        mdd_cash=abs(float(dd_cash.min())) if len(dd_cash) else 0.0
                        mc=st.columns(4)
                        mc[0].metric("Final capital",f"₹{final_capital:,.2f}")
                        mc[1].metric("Final profit / loss",f"₹{net_profit:,.2f}",delta=f"{net_profit/float(initial_capital)*100:.2f}%")
                        mc[2].metric("Maximum drawdown (₹)",f"₹{mdd_cash:,.2f}")
                        mc[3].metric("First trade risk",f"₹{float(initial_capital)*float(risk_pct)/100:,.2f}")
                        st.line_chart(compound_curve.set_index("Trade")["Capital after"])

                    st.subheader("Entry-type statistics")
                    def entry_type_summary(g):
                        pnl=g["pnl_points"].astype(float); rvals=g["R_multiple"].astype(float)
                        gross_profit=pnl[pnl>0].sum(); gross_loss=abs(pnl[pnl<0].sum())
                        rcurve=rvals.cumsum(); rdraw=rcurve-rcurve.cummax().clip(lower=0)
                        return pd.Series({"Trades":len(g),"Wins":int((pnl>0).sum()),"Losses":int((pnl<0).sum()),"Win rate %":(pnl>0).mean()*100,"Net points":pnl.sum(),"Expectancy points":pnl.mean(),"Profit factor":gross_profit/gross_loss if gross_loss else np.inf,"Total R":rvals.sum(),"Average R":rvals.mean(),"Max drawdown R":abs(rdraw.min()) if len(rdraw) else 0})
                    setup_stats=pd.DataFrame([{"setup":name, **entry_type_summary(group).to_dict()} for name,group in trades.groupby("setup",sort=False)])
                    st.dataframe(setup_stats,use_container_width=True,hide_index=True)
                    st.bar_chart(setup_stats.set_index("setup")[["Total R","Average R"]])

                    st.subheader("Monthly performance — including total R")
                    monthly_source=trades.copy()
                    monthly_source["trade_date"]=pd.to_datetime(monthly_source["trade_date"],errors="coerce")
                    monthly_source=monthly_source.dropna(subset=["trade_date"])
                    monthly_source["Year"]=monthly_source["trade_date"].dt.year
                    monthly_source["Month number"]=monthly_source["trade_date"].dt.month
                    monthly_source["Month"]=monthly_source["trade_date"].dt.strftime("%b")
                    def period_summary(g):
                        pnl=g["pnl_points"].astype(float); rvals=g["R_multiple"].astype(float)
                        gp=pnl[pnl>0].sum(); gl=abs(pnl[pnl<0].sum())
                        return {"Trades":len(g),"Win rate %":(pnl>0).mean()*100,"Net points":pnl.sum(),"Expectancy points":pnl.mean(),"Profit factor":gp/gl if gl else np.inf,"Total R":rvals.sum(),"Average R":rvals.mean()}
                    monthly_rows=[]
                    for (yr,mon),grp in monthly_source.groupby(["Year","Month number"],sort=True):
                        monthly_rows.append({"Year":int(yr),"Month number":int(mon),"Period":grp["Month"].iloc[0],**period_summary(grp)})
                    monthly_stats=pd.DataFrame(monthly_rows)
                    if not monthly_stats.empty:
                        st.dataframe(monthly_stats,use_container_width=True,hide_index=True)
                        st.bar_chart(monthly_stats.assign(Year_Month=monthly_stats.apply(lambda z:f"{int(z['Year'])}-{int(z['Month number']):02d}",axis=1)).set_index("Year_Month")[["Total R"]])
                        annual_rows=[]; combined_rows=[]
                        for yr,yr_data in monthly_source.groupby("Year",sort=True):
                            for mon,grp in yr_data.groupby("Month number",sort=True):
                                combined_rows.append({"Year":int(yr),"Period":grp["Month"].iloc[0],**period_summary(grp)})
                            year_summary=period_summary(yr_data)
                            annual_rows.append({"Year":int(yr),**year_summary})
                            combined_rows.append({"Year":int(yr),"Period":"YEAR TOTAL",**year_summary})
                        st.subheader("Year-wise totals")
                        st.dataframe(pd.DataFrame(annual_rows),use_container_width=True,hide_index=True)
                        year_end_table=pd.DataFrame(combined_rows)
                        st.subheader("Monthly breakdown with annual total")
                        st.dataframe(year_end_table,use_container_width=True,hide_index=True)
                    else:
                        year_end_table=pd.DataFrame()
                        st.warning("Trade dates could not be parsed; monthly and yearly statistics are unavailable.")

                    st.divider()
                    st.header("Trailing stop-loss effectiveness")
                    st.caption("Same PDHL entries and initial risk are replayed with alternative exits. The original fixed target remains active. New trailing levels apply from the next candle, avoiding same-candle look-ahead.")
                    atr_col, swing_col = st.columns(2)
                    with atr_col: trail_atr_mult=st.number_input("ATR trailing multiplier",min_value=0.5,max_value=5.0,value=2.0,step=0.25,key="trail_atr_mult")
                    with swing_col: trail_swing=st.number_input("Swing trailing lookback (bars)",min_value=2,max_value=10,value=3,step=1,key="trail_swing_bars")
                    if st.button("Analyze trailing stop methods",type="secondary",use_container_width=True,key="run_trailing_analysis"):
                        with st.spinner("Replaying existing PDHL trades with alternative exits…"):
                            trail_detail,trail_summary=trailing_stop_analysis(data,trades,trail_atr_mult,int(trail_swing))
                        st.session_state["nql_trailing_analysis"]={"detail":trail_detail,"summary":trail_summary,"fingerprint":fp}
                    saved_trail=st.session_state.get("nql_trailing_analysis")
                    if saved_trail and saved_trail.get("fingerprint")==fp:
                        trail_summary=saved_trail["summary"]; trail_detail=saved_trail["detail"]
                        if not trail_summary.empty:
                            st.subheader("Exit-method comparison")
                            st.dataframe(trail_summary,use_container_width=True,hide_index=True)
                            st.bar_chart(trail_summary.set_index("Exit method")[["Total R","Expectancy (R)","Max drawdown (R)"]])
                            best=trail_summary.sort_values(["Expectancy (R)","Max drawdown (R)"],ascending=[False,True]).iloc[0]
                            st.info(f"Highest expectancy in this sample: {best['Exit method']} ({best['Expectancy (R)']:+.3f}R/trade). Confirm on an untouched out-of-sample period before choosing it.")
                            tdl1,tdl2=st.columns(2)
                            with tdl1: st.download_button("Download trailing comparison CSV",trail_summary.to_csv(index=False).encode(),"nql_trailing_comparison.csv","text/csv",key="dl_trail_summary")
                            with tdl2: st.download_button("Download trailing trade details CSV",trail_detail.to_csv(index=False).encode(),"nql_trailing_trade_details.csv","text/csv",key="dl_trail_detail")
                        else: st.warning("No trailing analysis results were generated.")
                    st.subheader("Trade log")
                    st.dataframe(trades,use_container_width=True,hide_index=True)
                    # Keep all exports visible after any Streamlit rerun.
                    dl1,dl2,dl3=st.columns(3)
                    with dl1: st.download_button("Download backtest trades CSV",trades.to_csv(index=False).encode("utf-8"),"nql_backtest_trades.csv","text/csv",key="dl_backtest_trades")
                    with dl2: st.download_button("Download monthly/yearly CSV",year_end_table.to_csv(index=False).encode("utf-8"),"nql_monthly_yearly_statistics.csv","text/csv",key="dl_backtest_monthly")
                    with dl3: st.download_button("Download compounding CSV",compound_curve.to_csv(index=False).encode("utf-8"),"nql_compounding_capital.csv","text/csv",key="dl_backtest_compound")
                    st.download_button("Download signal audit CSV",signals[signals.signal!=""].to_csv(index=False).encode("utf-8"),"nql_signal_audit.csv","text/csv",key="dl_backtest_signals")
        except Exception as e: st.exception(e)
    else:
        st.info("Connect Kite and download Nifty spot candles above, or upload a spot CSV to run the backtest.")

    with st.expander("v1.0 assumptions — review before interpreting results"):
        st.markdown("""
    - PDH = highest previous-session candle body high: max(Open, Close) across its 5-minute candles.
    - PDL = lowest previous-session candle body low: min(Open, Close) across its 5-minute candles.
    - Today's open between PDH and PDL waits for a PDH/PDL breakout; no trigger means no trade.
    - Gap-up = today's open - body-based PDH; gap-down = body-based PDL - today's open.
    - A gap greater than or equal to 50 points is classified as large, using opening candle body low/high relative to PDH/PDL; reversal requires a later candle CLOSE beyond that body edge before 11:00 AM.
    - Gap-up reversal: SHORT only after a later candle closes below first candle body low. Gap-down reversal: LONG only after a later candle closes above first candle body high. Wick-only breaks do not qualify.
    - Gaps of 50 points or less, when the open is outside the PDH/PDL range, use the small-gap setups.
    - Entry is at the next 5-minute candle open after the signal candle closes.
    - PDH trap: previous candle closes above PDH, then current candle closes below PDH, only if PDH breakout entry has not activated. PDL trap is mirrored.
    - One trade per day by default; earliest qualifying signal takes priority.
    - Two-close breakout SL uses the second confirmation (entry) candle low - buffer for LONG, or high + buffer for SHORT; execution is at the following candle open. Other setups use their signal candle extreme plus/minus buffer. Default buffer 10 points; target defaults to 1:2.
    - If stop and target both touch within one candle, stop-loss is counted first (conservative).
    - Remaining open trades exit at the final candle close of that session.
    - All performance is in Nifty spot points, not options P&L or rupees. No brokerage/charges are applied.
    """)
