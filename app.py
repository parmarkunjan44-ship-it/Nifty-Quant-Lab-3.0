import streamlit as st
import pandas as pd
import numpy as np
import requests, hashlib, hmac, time, json, sqlite3, threading, traceback, os, base64
from io import StringIO
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo


def _data_fingerprint(frame):
    """Stable SHA-256 fingerprint for a dataframe's columns, index and values."""
    if frame is None:
        return ""
    digest = hashlib.sha256()
    digest.update("|".join(map(str, frame.columns)).encode("utf-8"))
    digest.update(str(frame.shape).encode("utf-8"))
    try:
        row_hashes = pd.util.hash_pandas_object(frame, index=True, categorize=True)
        digest.update(row_hashes.to_numpy(dtype="uint64").tobytes())
    except Exception:
        digest.update(frame.to_csv(index=True).encode("utf-8"))
    return digest.hexdigest()


st.set_page_config(page_title="Nifty Quant Lab v3.0", page_icon="📈", layout="wide")
st.title("📈 Nifty Quant Lab v3.0")
st.caption("Unified NIFTY 50 + BTC research workspace | Existing Kite and Telegram live-alert features retained")
st.caption("Nifty PDHL research + BTC EMA/Stoch RSI research • Independent markets and statistics")

KITE_API_ROOT = "https://api.kite.trade"
KITE_LOGIN_ROOT = "https://kite.zerodha.com/connect/login"


def _google_drive_service():
    """Build Drive API client with personal OAuth first; never log credentials."""
    try:
        from googleapiclient.discovery import build
        oauth = dict(st.secrets.get("google_oauth", {}))
        if oauth:
            required = ("client_id", "client_secret", "refresh_token")
            missing = [key for key in required if not str(oauth.get(key, "")).strip()]
            if missing:
                return None, "Google OAuth setup incomplete. Add: " + ", ".join(missing)
            from google.oauth2.credentials import Credentials
            credentials = Credentials(
                token=None,
                refresh_token=str(oauth["refresh_token"]).strip(),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=str(oauth["client_id"]).strip(),
                client_secret=str(oauth["client_secret"]).strip(),
                scopes=["https://www.googleapis.com/auth/drive"],
            )
            return build("drive", "v3", credentials=credentials, cache_discovery=False), None

        # Backward-compatible fallback; personal OAuth takes precedence when configured.
        cfg = dict(st.secrets.get("gcp_service_account", {}))
        if not cfg or not st.secrets.get("google_drive", {}).get("folder_id"):
            return None, "Google Drive secrets are not configured. Add [google_oauth] credentials."
        from google.oauth2 import service_account
        credentials = service_account.Credentials.from_service_account_info(
            cfg, scopes=["https://www.googleapis.com/auth/drive"]
        )
        return build("drive", "v3", credentials=credentials, cache_discovery=False), None
    except Exception as exc:
        return None, f"Google Drive setup error: {type(exc).__name__}: {exc}"


def render_google_oauth_setup():
    """Offer a one-time personal Google OAuth consent flow and show refresh token."""
    import urllib.parse
    import secrets as _secrets
    import hmac as _hmac
    oauth = dict(st.secrets.get("google_oauth", {}))
    if not str(oauth.get("client_id", "")).strip() or not str(oauth.get("client_secret", "")).strip():
        return
    has_saved_refresh_token = bool(str(oauth.get("refresh_token", "")).strip())
    if has_saved_refresh_token:
        st.warning("A Google Drive refresh token is already configured. If it has expired or been revoked, use the button below to authorize again and replace the token in Streamlit Secrets.")
    if st.session_state.get("google_oauth_refresh_token"):
        st.success("Google Drive authorization successful. Copy this refresh token into Streamlit Secrets.")
        st.code(st.session_state["google_oauth_refresh_token"])
        st.warning("Treat this token like a password. Do not share a screenshot of it.")
        return
    redirect_uri = "https://nifty-quant-lab-30git-3plam2pvfgndrgqp6xav4e.streamlit.app"
    code = st.query_params.get("code")
    state = st.query_params.get("state")

    # Signed, time-limited OAuth state: unlike st.session_state, this survives
    # Google's round-trip even if Streamlit creates a fresh browser session.
    def _make_oauth_state(secret):
        payload = f"{int(time.time())}:{_secrets.token_urlsafe(24)}"
        signature = _hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=") + "." + signature

    def _valid_oauth_state(candidate, secret):
        try:
            encoded, supplied_sig = candidate.split(".", 1)
            payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
            timestamp_text, _nonce = payload.split(":", 1)
            timestamp = int(timestamp_text)
            expected_sig = _hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
            return abs(time.time() - timestamp) <= 600 and _hmac.compare_digest(supplied_sig, expected_sig)
        except Exception:
            return False

    state_secret = str(oauth["client_secret"]).strip()
    if code:
        if not state or not _valid_oauth_state(state, state_secret):
            st.error("Google OAuth state mismatch or expired authorization. Start Reauthorize Google Drive again in this same app tab.")
            return
        try:
            response = requests.post("https://oauth2.googleapis.com/token", data={
                "code": code,
                "client_id": str(oauth["client_id"]).strip(),
                "client_secret": str(oauth["client_secret"]).strip(),
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }, timeout=25)
            response.raise_for_status()
            token_data = response.json()
            refresh_token = token_data.get("refresh_token")
            if not refresh_token:
                st.error("Google did not return a refresh token. Revoke this app's access in your Google Account and try Connect again.")
                return
            st.session_state["google_oauth_refresh_token"] = refresh_token
            st.query_params.clear()
        except Exception as exc:
            st.error(f"Google OAuth exchange failed: {type(exc).__name__}: {exc}")
        return
    oauth_state = _make_oauth_state(state_secret)
    params = {
        "client_id": str(oauth["client_id"]).strip(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/drive",
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": oauth_state,
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    if has_saved_refresh_token:
        st.info("Reauthorize the same Google account. After Google consent, copy the newly generated refresh token into Streamlit Secrets, replacing the old value.")
        st.link_button("Reauthorize Google Drive", auth_url, use_container_width=True)
    else:
        st.info("Connect your personal Google Drive. After Google consent, copy the refresh token into Streamlit Secrets.")
        st.link_button("Connect personal Google Drive", auth_url, use_container_width=True)


with st.expander("Personal Google Drive connection", expanded=False):
    render_google_oauth_setup()


def _drive_folder_id(service, parent_id, folder_name):
    safe_name = folder_name.replace("'", "\\'")
    query = f"'{parent_id}' in parents and name='{safe_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    found = service.files().list(q=query, fields="files(id,name)", pageSize=10).execute().get("files", [])
    if found:
        return found[0]["id"]
    created = service.files().create(body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}, fields="id").execute()
    return created["id"]


def _drive_upload_bytes(service, parent_id, name, payload, mime_type):
    from googleapiclient.http import MediaIoBaseUpload
    from io import BytesIO
    query = f"'{parent_id}' in parents and name='{name.replace(chr(39), chr(92)+chr(39))}' and trashed=false"
    found = service.files().list(q=query, fields="files(id)", pageSize=10).execute().get("files", [])
    media = MediaIoBaseUpload(BytesIO(payload), mimetype=mime_type, resumable=False)
    if found:
        return service.files().update(fileId=found[0]["id"], media_body=media).execute()["id"]
    return service.files().create(body={"name": name, "parents": [parent_id]}, media_body=media, fields="id").execute()["id"]


def archive_pdhl_backtest_to_drive(trades, signals, params, stats, created_at):
    """Archive one immutable run and update the cumulative comparison workbook."""
    service, error = _google_drive_service()
    if service is None:
        return False, error
    try:
        from io import BytesIO
        from openpyxl import load_workbook
        import json as _json
        root_id = str(st.secrets["google_drive"]["folder_id"]).strip()
        pdhl_id = _drive_folder_id(service, root_id, "PDHL")
        run_id = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("PDHL_%Y%m%d_%H%M%S_%f")
        run_folder = _drive_folder_id(service, pdhl_id, run_id)
        trades_df = pd.DataFrame(trades).copy()
        signals_df = pd.DataFrame(signals).copy()
        params_row = {**params, "Run ID": run_id, "Created at IST": created_at, "Data fingerprint": st.session_state.get("nql_backtest_results", {}).get("data_fingerprint", "")}
        summary_row = {"Run ID": run_id, "Created at IST": created_at, **params, **stats, "Data period start": str(pd.to_datetime(trades_df.get("trade_date"), errors="coerce").min()), "Data period end": str(pd.to_datetime(trades_df.get("trade_date"), errors="coerce").max()), "Sample designation": "Full sample — not classified as in-sample/OOS"}
        monthly = pd.DataFrame()
        if "trade_date" in trades_df.columns:
            d = trades_df.copy(); d["trade_date"] = pd.to_datetime(d["trade_date"], errors="coerce"); d = d.dropna(subset=["trade_date"]); d["Month"] = d.trade_date.dt.strftime("%Y-%m")
            if not d.empty:
                monthly = d.groupby("Month").agg(Trades=("pnl_points", "size"), **{"Net points": ("pnl_points", "sum"), "Total R": ("R_multiple", "sum"), "Win rate %": ("pnl_points", lambda x: (x > 0).mean()*100)}).reset_index()
                monthly.insert(0, "Run ID", run_id)
        # Per-run immutable raw files
        _drive_upload_bytes(service, run_folder, "trades.csv", trades_df.to_csv(index=False).encode("utf-8"), "text/csv")
        _drive_upload_bytes(service, run_folder, "signals.csv", signals_df.to_csv(index=False).encode("utf-8"), "text/csv")
        _drive_upload_bytes(service, run_folder, "parameters.json", _json.dumps(params_row, indent=2, default=str).encode(), "application/json")
        _drive_upload_bytes(service, run_folder, "summary.json", _json.dumps(summary_row, indent=2, default=str, allow_nan=True).encode(), "application/json")
        if "cumulative_R" in trades_df.columns:
            eq = trades_df[[c for c in ["trade_date", "exit_time", "cumulative_R", "cumulative_points"] if c in trades_df.columns]]
            _drive_upload_bytes(service, run_folder, "equity_curve.csv", eq.to_csv(index=False).encode("utf-8"), "text/csv")

        # Read existing master workbook, append this run, then upload the updated workbook.
        q = f"'{pdhl_id}' in parents and name='PDHL_Master_Comparison.xlsx' and trashed=false"
        existing = service.files().list(q=q, fields="files(id)", pageSize=1).execute().get("files", [])
        sheets = {"Backtest Master": pd.DataFrame(), "Trade History": pd.DataFrame(), "Parameter Comparison": pd.DataFrame(), "Monthly Comparison": pd.DataFrame(), "Dashboard": pd.DataFrame()}
        if existing:
            from googleapiclient.http import MediaIoBaseDownload
            request = service.files().get_media(fileId=existing[0]["id"])
            buff = BytesIO(); downloader = MediaIoBaseDownload(buff, request)
            done = False
            while not done: _, done = downloader.next_chunk()
            buff.seek(0)
            try:
                old = pd.read_excel(buff, sheet_name=None)
                for key in sheets: sheets[key] = old.get(key, pd.DataFrame())
            except Exception:
                pass
        sheets["Backtest Master"] = pd.concat([sheets["Backtest Master"], pd.DataFrame([summary_row])], ignore_index=True)
        history = trades_df.copy(); history.insert(0, "Run ID", run_id)
        sheets["Trade History"] = pd.concat([sheets["Trade History"], history], ignore_index=True)
        sheets["Parameter Comparison"] = pd.concat([sheets["Parameter Comparison"], pd.DataFrame([params_row])], ignore_index=True)
        if not monthly.empty: sheets["Monthly Comparison"] = pd.concat([sheets["Monthly Comparison"], monthly], ignore_index=True)
        master = sheets["Backtest Master"]
        if not master.empty:
            dashboard = master.copy()
            dashboard["Win rate %"] = pd.to_numeric(dashboard.get("Win rate", 0), errors="coerce") * 100
            for col in ["Total points", "Total R", "Profit factor", "Max drawdown (R)"]:
                if col in dashboard: dashboard[col] = pd.to_numeric(dashboard[col], errors="coerce")
            sheets["Dashboard"] = dashboard
        # Excel cannot store timezone-aware datetime values. Strip timezone
        # metadata while preserving the displayed local clock time, across all
        # sheets and both native datetime columns and object columns.
        def _excel_safe_frame(frame):
            safe = frame.copy()
            for col in safe.columns:
                series = safe[col]
                if isinstance(series.dtype, pd.DatetimeTZDtype):
                    safe[col] = series.dt.tz_localize(None)
                elif pd.api.types.is_object_dtype(series.dtype):
                    def _strip_tz(value):
                        if isinstance(value, (pd.Timestamp, datetime)) and value.tzinfo is not None:
                            return value.replace(tzinfo=None)
                        return value
                    safe[col] = series.map(_strip_tz)
            return safe

        sheets = {name: _excel_safe_frame(frame) for name, frame in sheets.items()}
        out = BytesIO()
        with pd.ExcelWriter(out, engine="openpyxl") as writer:
            for name, frame in sheets.items():
                frame.to_excel(writer, sheet_name=name, index=False)
        # Add readable formatting, best-run highlighting, and comparison charts.
        from openpyxl import load_workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.chart import BarChart, Reference
        out.seek(0)
        wb = load_workbook(out)
        for ws in wb.worksheets:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="243B53")
                cell.alignment = Alignment(wrap_text=True)
            for col_cells in ws.columns:
                letter = col_cells[0].column_letter
                width = min(32, max(12, max(len(str(c.value or "")) for c in col_cells[:100]) + 2))
                ws.column_dimensions[letter].width = width
        dash = wb["Dashboard"]
        if dash.max_row > 1:
            headers = {c.value: c.column for c in dash[1]}
            total_r_col = headers.get("Total R")
            if total_r_col:
                best_row = max(range(2, dash.max_row + 1), key=lambda r: float(dash.cell(r, total_r_col).value or -1e99))
                for cell in dash[best_row]:
                    cell.fill = PatternFill("solid", fgColor="D9EAD3")
                    cell.font = Font(bold=True)
                chart = BarChart(); chart.title = "Total R by backtest"; chart.y_axis.title = "Total R"; chart.x_axis.title = "Run"
                chart.add_data(Reference(dash, min_col=total_r_col, min_row=1, max_row=dash.max_row), titles_from_data=True)
                label_col = headers.get("Run ID", 1)
                chart.set_categories(Reference(dash, min_col=label_col, min_row=2, max_row=dash.max_row))
                chart.height = 8; chart.width = 18; dash.add_chart(chart, "A" + str(dash.max_row + 3))
        final = BytesIO(); wb.save(final)
        _drive_upload_bytes(service, pdhl_id, "PDHL_Master_Comparison.xlsx", final.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        return True, f"Saved {run_id} and updated PDHL_Master_Comparison.xlsx"
    except Exception as exc:
        return False, f"Google Drive archive failed: {type(exc).__name__}: {exc}"





def _drive_list_pdhl_runs(service, pdhl_folder_id, page_size=100):
    """Return archived PDHL run folders, newest first."""
    q = f"'{pdhl_folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
    response = service.files().list(
        q=q, orderBy="name desc", pageSize=page_size,
        fields="files(id,name,modifiedTime)"
    ).execute()
    return response.get("files", [])


def _drive_read_file_bytes(service, folder_id, filename):
    """Download one named file from a saved run folder."""
    q = f"'{folder_id}' in parents and name='{filename}' and trashed=false"
    found = service.files().list(q=q, fields="files(id,name)", pageSize=5).execute().get("files", [])
    if not found:
        return None
    request = service.files().get_media(fileId=found[0]["id"])
    from io import BytesIO
    buff = BytesIO()
    from googleapiclient.http import MediaIoBaseDownload
    downloader = MediaIoBaseDownload(buff, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buff.getvalue()


def _load_pdhl_run_from_drive(service, run_folder):
    """Load the complete saved backtest package needed to reproduce its report/chart."""
    from io import BytesIO
    folder_id = run_folder["id"]
    trades_raw = _drive_read_file_bytes(service, folder_id, "trades.csv")
    signals_raw = _drive_read_file_bytes(service, folder_id, "signals.csv")
    if trades_raw is None or signals_raw is None:
        raise FileNotFoundError("This archive is missing trades.csv or signals.csv.")
    trades = pd.read_csv(BytesIO(trades_raw))
    signals = pd.read_csv(BytesIO(signals_raw))
    for col in ("entry_time", "exit_time", "signal_time", "signal_candle_time",
                "entry_candle_time", "sl_reference_candle_time"):
        if col in trades.columns:
            trades[col] = pd.to_datetime(trades[col], errors="coerce")
    if "datetime" in signals.columns:
        signals["datetime"] = pd.to_datetime(signals["datetime"], errors="coerce")
    summary_raw = _drive_read_file_bytes(service, folder_id, "summary.json")
    params_raw = _drive_read_file_bytes(service, folder_id, "parameters.json")
    summary = json.loads(summary_raw.decode("utf-8")) if summary_raw else {}
    params = json.loads(params_raw.decode("utf-8")) if params_raw else {}
    return {"trades": trades, "signals": signals, "summary": summary,
            "params": params, "run_id": run_folder["name"],
            "modified": run_folder.get("modifiedTime", "")}


def render_saved_backtest_report(saved, key_prefix="saved_bt"):
    """Render archived results using the same report sections and chart data as a fresh run."""
    trades = saved["trades"].copy()
    signals = saved["signals"].copy()
    if trades.empty:
        st.warning("This saved run contains no trades.")
        return

    stats = stats_for(trades)
    st.caption(f"Run: {saved['run_id']} • Drive modified: {saved.get('modified','')}")
    params = saved.get("params", {})
    if params:
        with st.expander("Saved strategy parameters", expanded=True):
            st.json(params)

    st.subheader("TradingView-style chart with actual trade drawings")
    render_tradingview_chart(signals, trades=trades, chart_key=f"{key_prefix}_{saved['run_id'][-10:]}")

    cols = st.columns(4)
    for col, (k, v) in zip(cols, list(stats.items())[:4]):
        col.metric(k, f"{v:.2f}" if isinstance(v, (float, np.floating)) and np.isfinite(v) else str(v))
    cols = st.columns(4)
    for col, (k, v) in zip(cols, list(stats.items())[4:8]):
        col.metric(k, f"{v:.2f}" if isinstance(v, (float, np.floating)) and np.isfinite(v) else str(v))

    st.subheader("Risk-normalized performance — R multiples")
    rcols = st.columns(3)
    rcols[0].metric("Total R", f"{stats['Total R']:+.2f}R")
    rcols[1].metric("Average R / trade", f"{stats['Average R / trade']:+.3f}R")
    rcols[2].metric("Maximum drawdown", f"{stats['Max drawdown (R)']:.2f}R")
    st.caption("1R = initial entry-to-stop risk for that individual trade. R excludes brokerage, slippage and other costs.")

    st.subheader("Equity curve — cumulative R")
    if "cumulative_R" in trades.columns:
        st.line_chart(trades.set_index("exit_time")["cumulative_R"])
    st.subheader("Equity curve — cumulative Nifty points")
    if "cumulative_points" in trades.columns:
        st.line_chart(trades.set_index("exit_time")["cumulative_points"])

    st.subheader("Compounding capital simulation")
    st.caption("Risk is recalculated as a percentage of current capital before every trade. This is a hypothetical spot-R account simulation, not actual options P&L.")
    cap1, cap2 = st.columns(2)
    with cap1:
        initial_capital = st.number_input("Initial capital (₹)", min_value=1000.0, value=100000.0,
                                          step=5000.0, key=f"{key_prefix}_capital")
    with cap2:
        risk_pct = st.number_input("Risk per trade (% of current capital)", min_value=0.1, max_value=10.0,
                                   value=1.0, step=0.1, key=f"{key_prefix}_risk")
    compound_curve, final_capital = compound_capital_path(trades, initial_capital, risk_pct)
    if not compound_curve.empty:
        net_profit = final_capital - float(initial_capital)
        peak = compound_curve["Capital after"].cummax().clip(lower=float(initial_capital))
        mdd_cash = abs(float((compound_curve["Capital after"] - peak).min()))
        mc = st.columns(4)
        mc[0].metric("Final capital", f"₹{final_capital:,.2f}")
        mc[1].metric("Final profit / loss", f"₹{net_profit:,.2f}",
                     delta=f"{net_profit / float(initial_capital) * 100:.2f}%")
        mc[2].metric("Maximum drawdown (₹)", f"₹{mdd_cash:,.2f}")
        mc[3].metric("First trade risk", f"₹{float(initial_capital) * float(risk_pct) / 100:,.2f}")
        st.line_chart(compound_curve.set_index("Trade")["Capital after"])

    st.subheader("Entry-type statistics")
    def entry_type_summary(group):
        pnl = group["pnl_points"].astype(float)
        rvals = group["R_multiple"].astype(float)
        gross_profit = pnl[pnl > 0].sum()
        gross_loss = abs(pnl[pnl < 0].sum())
        rcurve = rvals.cumsum()
        rdraw = rcurve - rcurve.cummax().clip(lower=0)
        return pd.Series({
            "Trades": len(group), "Wins": int((pnl > 0).sum()), "Losses": int((pnl < 0).sum()),
            "Win rate %": (pnl > 0).mean() * 100, "Net points": pnl.sum(),
            "Expectancy points": pnl.mean(),
            "Profit factor": gross_profit / gross_loss if gross_loss else np.inf,
            "Total R": rvals.sum(), "Average R": rvals.mean(),
            "Max drawdown R": abs(rdraw.min()) if len(rdraw) else 0
        })
    setup_stats = pd.DataFrame([
        {"setup": name, **entry_type_summary(group).to_dict()}
        for name, group in trades.groupby("setup", sort=False)
    ])
    st.dataframe(setup_stats, use_container_width=True, hide_index=True)
    st.bar_chart(setup_stats.set_index("setup")[["Total R", "Average R"]])

    st.subheader("Monthly performance — including total R")
    monthly_source = trades.copy()
    monthly_source["trade_date"] = pd.to_datetime(monthly_source["trade_date"], errors="coerce")
    monthly_source = monthly_source.dropna(subset=["trade_date"])
    monthly_source["Year"] = monthly_source["trade_date"].dt.year
    monthly_source["Month number"] = monthly_source["trade_date"].dt.month
    monthly_source["Month"] = monthly_source["trade_date"].dt.strftime("%b")

    def period_summary(group):
        pnl = group["pnl_points"].astype(float)
        rvals = group["R_multiple"].astype(float)
        gp, gl = pnl[pnl > 0].sum(), abs(pnl[pnl < 0].sum())
        return {"Trades": len(group), "Win rate %": (pnl > 0).mean() * 100,
                "Net points": pnl.sum(), "Expectancy points": pnl.mean(),
                "Profit factor": gp / gl if gl else np.inf,
                "Total R": rvals.sum(), "Average R": rvals.mean()}

    monthly_rows = []
    for (year, month), group in monthly_source.groupby(["Year", "Month number"], sort=True):
        monthly_rows.append({"Year": int(year), "Month number": int(month),
                             "Period": group["Month"].iloc[0], **period_summary(group)})
    monthly_stats = pd.DataFrame(monthly_rows)
    year_end_table = pd.DataFrame()
    if not monthly_stats.empty:
        st.dataframe(monthly_stats, use_container_width=True, hide_index=True)
        st.bar_chart(monthly_stats.assign(
            Year_Month=monthly_stats.apply(lambda z: f"{int(z['Year'])}-{int(z['Month number']):02d}", axis=1)
        ).set_index("Year_Month")[["Total R"]])
        annual_rows, combined_rows = [], []
        for year, year_data in monthly_source.groupby("Year", sort=True):
            for month, group in year_data.groupby("Month number", sort=True):
                combined_rows.append({"Year": int(year), "Period": group["Month"].iloc[0], **period_summary(group)})
            annual = period_summary(year_data)
            annual_rows.append({"Year": int(year), **annual})
            combined_rows.append({"Year": int(year), "Period": "YEAR TOTAL", **annual})
        st.subheader("Year-wise totals")
        st.dataframe(pd.DataFrame(annual_rows), use_container_width=True, hide_index=True)
        year_end_table = pd.DataFrame(combined_rows)
        st.subheader("Monthly breakdown with annual total")
        st.dataframe(year_end_table, use_container_width=True, hide_index=True)

    st.divider()
    st.header("Trailing stop-loss effectiveness")
    st.caption("Same saved entries and initial risk are replayed with alternative exits. Original fixed target remains active.")
    trail_a, trail_s = st.columns(2)
    with trail_a:
        atr_mult = st.number_input("ATR trailing multiplier", 0.5, 5.0, 2.0, 0.25, key=f"{key_prefix}_atr")
    with trail_s:
        swing_lookback = st.number_input("Swing trailing lookback (bars)", 2, 10, 3, 1, key=f"{key_prefix}_swing")
    if st.button("Analyze trailing stop methods", key=f"{key_prefix}_run_trailing", use_container_width=True):
        detail, summary = trailing_stop_analysis(signals, trades, atr_mult, int(swing_lookback))
        st.session_state[f"{key_prefix}_trail"] = {"detail": detail, "summary": summary}
    trail_saved = st.session_state.get(f"{key_prefix}_trail")
    if trail_saved and not trail_saved["summary"].empty:
        st.subheader("Exit-method comparison")
        st.dataframe(trail_saved["summary"], use_container_width=True, hide_index=True)
        st.bar_chart(trail_saved["summary"].set_index("Exit method")[["Total R", "Expectancy (R)", "Max drawdown (R)"]])
        best = trail_saved["summary"].sort_values(["Expectancy (R)", "Max drawdown (R)"], ascending=[False, True]).iloc[0]
        st.info(f"Highest expectancy in this sample: {best['Exit method']} ({best['Expectancy (R)']:+.3f}R/trade).")

    st.subheader("Trade log")
    st.dataframe(trades, use_container_width=True, hide_index=True)
    d1, d2, d3 = st.columns(3)
    with d1:
        st.download_button("Download saved trades CSV", trades.to_csv(index=False).encode("utf-8"),
                           f"{saved['run_id']}_trades.csv", "text/csv", key=f"{key_prefix}_dl_trades")
    with d2:
        st.download_button("Download saved signals CSV", signals.to_csv(index=False).encode("utf-8"),
                           f"{saved['run_id']}_signals.csv", "text/csv", key=f"{key_prefix}_dl_signals")
    with d3:
        st.download_button("Download monthly/yearly CSV", year_end_table.to_csv(index=False).encode("utf-8"),
                           f"{saved['run_id']}_monthly.csv", "text/csv", key=f"{key_prefix}_dl_monthly")
    st.download_button("Download compounding CSV", compound_curve.to_csv(index=False).encode("utf-8"),
                       f"{saved['run_id']}_compounding.csv", "text/csv", key=f"{key_prefix}_dl_compound")


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
            target_raw = tr.get("target", np.nan)
            target = float(target_raw) if pd.notna(target_raw) and np.isfinite(float(target_raw)) else None
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
    # Draw prior-session PDH and PDL independently for every visible session.
    # Also mark the first candle's body edge on gap sessions: gap-up = body low;
    # gap-down = body high. These are visual references only; signal rules are unchanged.
    pdh_sessions, pdl_sessions, gap_opening_levels = [], [], []
    for session_day, session in d.groupby(d["datetime"].dt.date, sort=True):
        start_ts = to_epoch_ist(session["datetime"].iloc[0])
        end_ts = to_epoch_ist(session["datetime"].iloc[-1])
        if "pdh" in session.columns:
            vals = pd.to_numeric(session["pdh"], errors="coerce").dropna()
            if not vals.empty:
                pdh_sessions.append({"day": str(session_day), "start": start_ts,
                                     "end": end_ts, "value": float(vals.iloc[0])})
        if "pdl" in session.columns:
            vals = pd.to_numeric(session["pdl"], errors="coerce").dropna()
            if not vals.empty:
                pdl_sessions.append({"day": str(session_day), "start": start_ts,
                                     "end": end_ts, "value": float(vals.iloc[0])})
        first = session.iloc[0]
        if ("opening_body_low" in session.columns and "opening_body_high" in session.columns
                and pd.notna(first.get("opening_body_low")) and pd.notna(first.get("opening_body_high"))
                and pd.notna(first.get("pdh")) and pd.notna(first.get("pdl"))):
            ob_low, ob_high = float(first.opening_body_low), float(first.opening_body_high)
            prior_high, prior_low = float(first.pdh), float(first.pdl)
            if ob_low > prior_high:
                gap_opening_levels.append({"day": str(session_day), "start": start_ts,
                    "end": end_ts, "value": ob_low, "label": "Gap-up first candle body low", "kind": "gap_up"})
            elif ob_high < prior_low:
                gap_opening_levels.append({"day": str(session_day), "start": start_ts,
                    "end": end_ts, "value": ob_high, "label": "Gap-down first candle body high", "kind": "gap_down"})
    levels_json = json.dumps({"pdh_sessions": pdh_sessions, "pdl_sessions": pdl_sessions,
                              "gap_opening_levels": gap_opening_levels}, separators=(",", ":"))

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
      // Session-specific PDH: draw one horizontal segment per visible trading day.
      (levels.pdh_sessions||[]).forEach(level=>{
        const pdhLine=chart.addLineSeries({color:'#f0b90b',lineWidth:2,lineStyle:0,
          priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,
          });
        pdhLine.setData([{time:level.start,value:level.value},{time:level.end,value:level.value}]);
      });
      // Session-specific PDL, matching the PDH treatment.
      (levels.pdl_sessions||[]).forEach(level=>{
        const pdlLine=chart.addLineSeries({color:'#42a5f5',lineWidth:2,lineStyle:0,
          priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,
          });
        pdlLine.setData([{time:level.start,value:level.value},{time:level.end,value:level.value}]);
      });
      // Gap reference: first candle BODY low for gap-up, BODY high for gap-down.
      (levels.gap_opening_levels||[]).forEach(level=>{
        const gapLine=chart.addLineSeries({color:level.kind==='gap_up'?'#ff9800':'#ab8cff',
          lineWidth:2,lineStyle:2,priceLineVisible:false,lastValueVisible:false,
          crosshairMarkerVisible:false,title:level.label+' '+level.day});
        gapLine.setData([{time:level.start,value:level.value},{time:level.end,value:level.value}]);
      });
      // Draw each trade's horizontal levels ONLY from its actual entry timestamp
      // through its actual exit timestamp. Never extend levels to the chart edge.
      trades.forEach(t=>{
        const addTradeSegment=(price,color,style,title)=>{
          if(price===null || !Number.isFinite(price) || !Number.isFinite(t.entry_time) || !Number.isFinite(t.exit_time)) return;
          const line=chart.addLineSeries({
            color,lineWidth:1,lineStyle:style,title,
            priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false
          });
          line.setData([
            {time:t.entry_time,value:price},
            {time:t.exit_time,value:price}
          ]);
        };
        addTradeSegment(t.entry,'#e5e7eb',2,'Entry');
        addTradeSegment(t.stop,'#ef5350',0,'Initial SL');
        if(t.target!==null) addTradeSegment(t.target,'#26a69a',0,'Target');
        // Mark the exact candle/price where the trade was actually closed.
        if(Number.isFinite(t.exit) && Number.isFinite(t.exit_time)){
          markers.push({
            time:t.exit_time,
            position:t.direction==='LONG'?'aboveBar':'belowBar',
            color:'#f5c542',shape:'circle',text:'EXIT '+t.n,size:2
          });
        }
      });
      if(markers.length) series.setMarkers(markers.sort((a,b)=>a.time-b.time));
      // Keep the price chart clean: no large shaded risk/reward rectangles.
      // Entry, initial SL, target (if any), and trade markers remain visible.
      const drawRiskReward=()=>{
        const w=root.clientWidth,h=root.clientHeight,dpr=window.devicePixelRatio||1;
        overlay.width=Math.round(w*dpr); overlay.height=Math.round(h*dpr);
        overlay.style.width=w+'px'; overlay.style.height=h+'px';
        const ctx=overlay.getContext('2d');
        ctx.setTransform(dpr,0,0,dpr,0,0);
        ctx.clearRect(0,0,w,h);
      };
      chart.timeScale().fitContent();
      drawRiskReward();
      chart.timeScale().subscribeVisibleTimeRangeChange(drawRiskReward);
      series.subscribeDataChanged?.(drawRiskReward);
      chart.subscribeCrosshairMove(param=>{
        if(!param || !param.time || !param.seriesData || !param.seriesData.get(series)){return;}
        const c=param.seriesData.get(series);
        const dt=new Date(param.time*1000);
        const bodySize=Math.abs(c.close-c.open);
        tip.textContent=istDateTime.format(dt)+
          '\nO '+c.open.toFixed(2)+'  H '+c.high.toFixed(2)+'\nL '+c.low.toFixed(2)+'  C '+c.close.toFixed(2)+
          '\nBody Size: '+bodySize.toFixed(2)+' points';
      });
      // Tap/click inside a trade's green reward or red risk box to reveal exact values.
      chart.subscribeClick(param=>{
        if(!param || !param.point) return;
        const px=param.point.x, py=param.point.y;
        let picked=null;
        for(let i=trades.length-1;i>=0;i--){
          const t=trades[i];
          const x1=chart.timeScale().timeToCoordinate(t.entry_time);
          const x2=chart.timeScale().timeToCoordinate(t.exit_time);
          const ye=series.priceToCoordinate(t.entry), ys=series.priceToCoordinate(t.stop);
          const yt=t.target===null?null:series.priceToCoordinate(t.target);
          if([x1,x2,ye,ys].some(v=>v===null||!Number.isFinite(v)) || (t.target!==null && (yt===null||!Number.isFinite(yt)))) continue;
          const left=Math.min(x1,x2), right=Math.max(x1,x2);
          const inReward=yt!==null&&px>=left&&px<=right&&py>=Math.min(ye,yt)&&py<=Math.max(ye,yt);
          const inRisk=px>=left&&px<=right&&py>=Math.min(ye,ys)&&py<=Math.max(ye,ys);
          if(inReward||inRisk){picked=t;break;}
        }
        if(picked){
          const risk=Math.abs(picked.entry-picked.stop);
          const reward=picked.target===null?null:Math.abs(picked.target-picked.entry);
          const rr=(reward!==null&&risk>0)?reward/risk:null;
          tip.textContent='TRADE '+picked.n+' • '+picked.setup+' '+picked.direction+
            '\nEntry: '+picked.entry.toFixed(2)+'  Initial SL: '+picked.stop.toFixed(2)+
            (picked.target===null?'\nNo fixed target — trailing exit':'\nTarget: '+picked.target.toFixed(2))+
            '\nExit: '+picked.exit.toFixed(2)+'  |  Risk: '+risk.toFixed(2)+' pts'+
            (rr===null?'':'  |  Target R:R 1 : '+rr.toFixed(2))+
            '\nResult: '+picked.r.toFixed(2)+'R';
        }
      });
      const resize=()=>{chart.applyOptions({width:root.clientWidth,height:root.clientHeight});drawRiskReward();};
      if(window.ResizeObserver){new ResizeObserver(resize).observe(root);}else{window.addEventListener('resize',resize);}
    }
    </script></body></html>'''
    html = html.replace("__CANDLES__", candles_json).replace("__TRADES__", trades_json).replace("__MARKERS__", markers_json).replace("__LEVELS__", levels_json)
    components.html(html, height=620, scrolling=False)
    if "pdh" in d.columns or "pdl" in d.columns:
        st.caption("Mobile controls: drag chart to pan • drag price axis to vertical-zoom • drag time axis to horizontal-zoom • two-finger pinch to zoom. Yellow PDH and blue PDL are marked separately for each visible session. Gap references are shown where available. Tap/click inside a trade box to inspect its Entry, SL, Target/exit and realized R. All chart times use IST.")
    else:
        st.caption("Mobile controls: drag chart to pan • drag price axis to vertical-zoom • drag time axis to horizontal-zoom • two-finger pinch to zoom. Trade markers and stop/exit regions are shown where available. All chart times use IST.")


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
    # Optimizer split frames are already cleaned and sorted. Reuse their parsed
    # columns instead of reparsing every timestamp and OHLC value per candidate.
    already_prepared=("trade_date" in df.columns and pd.api.types.is_datetime64_any_dtype(df["datetime"])
                      and all(pd.api.types.is_numeric_dtype(df[c]) for c in ["open","high","low","close"]))
    if already_prepared:
        x=df.copy()
    else:
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


def build_signals(df, gap_threshold=50.0, cutoff="11:00", min_entry_body=20.0, min_momentum_body=50.0, progress_callback=None, breakout_confirm_candles=1, level_mode="Previous session body high/low"):

    """Build one earliest valid PDHL signal per session using confirmed rules.

    Signal candle is the candle that satisfies the setup. Execution is selected
    in run_backtest. PDH/PDL default to prior-session bodies; wick levels are optional.
    """
    x = prepare_spot(df)
    x["body_high"] = x[["open", "close"]].max(axis=1)
    x["body_low"] = x[["open", "close"]].min(axis=1)
    if str(level_mode).lower().startswith("previous session wick"):
        daily = x.groupby("trade_date").agg(pdh=("high", "max"), pdl=("low", "min"))
    else:
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

        # Breakout state machine: momentum and confirmation are separate setups.
        # The first close beyond PDH/PDL fixes the reference even if its candle
        # fails the confirmation body/wick filters. Momentum may still trigger
        # immediately on that first candle when its own full conditions pass.
        selected = None
        breakout_activated = False
        if gap_mode in ("gap_up", "gap_down"):
            # Large-gap reversal: later candle CLOSE, not wick, crosses opening body edge.
            for pos in range(1, len(ids)):
                i = ids[pos]
                r = x.loc[i]
                tm = r.datetime.strftime("%H:%M")
                if tm >= cutoff:
                    break
                if gap_mode == "gap_up" and float(r.close) < opening_body_low:
                    body, _, _ = candle_parts(r)
                    if body >= float(min_entry_body) and _entry_wick_pass(r, "SHORT"):
                        selected = (i, "SHORT", "Gap-up reversal", "Close below first 5-minute candle body low; minimum body and directional wick filters passed", "signal")
                        break
                if gap_mode == "gap_down" and float(r.close) > opening_body_high:
                    body, _, _ = candle_parts(r)
                    if body >= float(min_entry_body) and _entry_wick_pass(r, "LONG"):
                        selected = (i, "LONG", "Gap-down reversal", "Close above first 5-minute candle body high; minimum body and directional wick filters passed", "signal")
                        break
        else:
            required = max(1, int(breakout_confirm_candles))
            anchor_side = None
            anchor_pos = None
            confirmations = 0
            for pos, i in enumerate(ids):
                r = x.loc[i]
                close = float(r.close)

                if anchor_side is None:
                    side = "LONG" if close > pdh else ("SHORT" if close < pdl else None)
                    if side is None:
                        continue
                    anchor_side, anchor_pos = side, pos
                    breakout_activated = True
                    body, _, _ = candle_parts(r)
                    directional = ((side == "LONG" and close > float(r.open)) or
                                   (side == "SHORT" and close < float(r.open)))
                    # Independent immediate momentum setup on the first breakout candle.
                    if (body >= float(min_momentum_body) and body >= float(min_entry_body)
                            and directional and _entry_wick_pass(r, side)):
                        selected = (i, side,
                                    "PDH momentum" if side == "LONG" else "PDL momentum",
                                    "First breakout candle independently meets momentum body, minimum body and directional wick filters",
                                    "signal")
                        break
                    # The anchor itself does not need to pass body/wick filters.
                    continue

                # For an active PDH breakout, inspect each later candle. A candle
                # is skipped unless it passes its own directional body/wick filter
                # AND gives either a valid continuation close or a valid trap close.
                if anchor_side == "LONG":
                    if close > pdh and close > float(r.open):
                        side = "LONG"
                    elif close < pdh and close < float(r.open):
                        side = "SHORT"
                    else:
                        continue
                else:  # PDL breakout
                    if close < pdl and close < float(r.open):
                        side = "SHORT"
                    elif close > pdl and close > float(r.open):
                        side = "LONG"
                    else:
                        continue

                body, _, _ = candle_parts(r)
                if body < float(min_entry_body) or not _entry_wick_pass(r, side):
                    continue

                # First qualifying candle is classified as continuation or trap.
                if anchor_side == "LONG" and side == "LONG":
                    confirmations += 1
                    if confirmations >= required:
                        setup_name = f"PDH {required}-candle confirmation"
                        selected = (i, "LONG", setup_name,
                                    f"First breakout reference above PDH followed by {required} qualifying green continuation candle(s); failed-filter candles skipped",
                                    "entry")
                        break
                elif anchor_side == "SHORT" and side == "SHORT":
                    confirmations += 1
                    if confirmations >= required:
                        setup_name = f"PDL {required}-candle confirmation"
                        selected = (i, "SHORT", setup_name,
                                    f"First breakout reference below PDL followed by {required} qualifying red continuation candle(s); failed-filter candles skipped",
                                    "entry")
                        break
                elif anchor_side == "LONG" and side == "SHORT":
                    selected = (i, "SHORT", "PDH trap",
                                "After PDH breakout reference, first qualifying red candle closed below PDH; body and SHORT wick filters passed",
                                "signal")
                    break
                elif anchor_side == "SHORT" and side == "LONG":
                    selected = (i, "LONG", "PDL trap",
                                "After PDL breakout reference, first qualifying green candle closed above PDL; body and LONG wick filters passed",
                                "signal")
                    break

            # Traps without an active breakout attempt (no qualifying first
            # breakout candle) are still checked independently.
            if selected is None and not breakout_activated:
                for pos in range(1, len(ids)):
                    i, pi = ids[pos], ids[pos-1]
                    r, pr = x.loc[i], x.loc[pi]
                    body, _, _ = candle_parts(r)
                    if pr.close > pdh and r.close < pdh and body >= float(min_entry_body) and _entry_wick_pass(r, "SHORT"):
                        selected = (i, "SHORT", "PDH trap", "Previous candle closed above PDH; current candle closed below PDH; minimum body and directional wick filters passed", "signal")
                        break
                    if pr.close < pdl and r.close > pdl and body >= float(min_entry_body) and _entry_wick_pass(r, "LONG"):
                        selected = (i, "LONG", "PDL trap", "Previous candle closed below PDL; current candle closed above PDL; minimum body and directional wick filters passed", "signal")
                        break

            # Small-gap / inside-range retests: touch and close back beyond level.
            if selected is None:
                for pos in range(1, len(ids)):
                    i, pi = ids[pos], ids[pos-1]
                    r, pr = x.loc[i], x.loc[pi]
                    body, _, _ = candle_parts(r)
                    if pr.close > pdh and r.low <= pdh and r.close > pdh and body >= float(min_entry_body) and _entry_wick_pass(r, "LONG"):
                        selected = (i, "LONG", "Small-gap PDH support", "Retest touched PDH and closed above; minimum body and directional wick filters passed", "signal")
                        break
                    if pr.close < pdl and r.high >= pdl and r.close < pdl and body >= float(min_entry_body) and _entry_wick_pass(r, "SHORT"):
                        selected = (i, "SHORT", "Small-gap PDL resistance", "Retest touched PDL and closed below; minimum body and directional wick filters passed", "signal")
                        break
        if selected:
            i, side, setup, reason, sl_ref = selected
            # General minimum body filter applies to the actual qualifying candle
            # for every setup, including two-close confirmation, traps, retests,
            # and gap reversals. Momentum has its additional dedicated threshold.
            selected_body, _, _ = candle_parts(x.loc[i])
            if selected_body < float(min_entry_body):
                continue
            x.at[i, "signal"] = side
            x.at[i, "setup"] = setup
            x.at[i, "signal_reason"] = reason
            x.at[i, "signal_candle_time"] = str(x.at[i, "datetime"])
            x.at[i, "entry_candle_time"] = str(x.at[i, "datetime"])
            x.at[i, "sl_reference"] = sl_ref
    return x


def run_backtest(df, max_trades_day=1, entry_mode="Next candle open", sl_buffer=10.0, rr=2.0, min_entry_body=20.0, gap_threshold=50.0, cutoff="11:00", progress_callback=None, min_momentum_body=50.0, breakout_confirm_candles=1, profit_booking_mode="Fixed target", stop_loss_mode="Candle low/high", prebuilt_signals=None, min_sl_points=15.0, max_sl_points=55.0, adaptive_sl_threshold=55.0, adaptive_sl_range_fraction=0.50, level_mode="Previous session body high/low"):

    """Execute confirmed signals at the next 5-minute candle open; one/day default.

    The execution engine uses NumPy arrays and session boundaries instead of
    repeatedly iterating pandas rows. Trade-selection and fill priority are
    intentionally identical to the original engine.
    """
    sig = prebuilt_signals.copy() if prebuilt_signals is not None else build_signals(
        df, min_entry_body=min_entry_body, min_momentum_body=min_momentum_body,
        gap_threshold=gap_threshold, cutoff=cutoff, progress_callback=progress_callback,
        breakout_confirm_candles=breakout_confirm_candles, level_mode=level_mode)
    if sig.empty:
        if progress_callback: progress_callback(1.0, "Backtest complete — no qualifying trades")
        return pd.DataFrame(), pd.DataFrame(), sig

    # Convert the candle columns once. The optimizer calls this function many
    # times, so avoiding DataFrame iterrows/iloc inside the trade loop matters.
    opens = sig["open"].to_numpy(dtype=np.float64, copy=False)
    highs = sig["high"].to_numpy(dtype=np.float64, copy=False)
    lows = sig["low"].to_numpy(dtype=np.float64, copy=False)
    closes = sig["close"].to_numpy(dtype=np.float64, copy=False)
    dates = sig["trade_date"].to_numpy(copy=False)
    datetimes = sig["datetime"].to_numpy(copy=False)
    sides = sig["signal"].to_numpy(dtype=object, copy=False)
    setups = sig["setup"].to_numpy(dtype=object, copy=False)
    reasons = sig["signal_reason"].to_numpy(dtype=object, copy=False)
    pdhs = sig["pdh"].to_numpy(dtype=np.float64, copy=False)
    pdls = sig["pdl"].to_numpy(dtype=np.float64, copy=False)

    # Session end indices, preserving the original no-overnight execution rule.
    session_ends = {}
    for i, day in enumerate(dates):
        session_ends[day] = i

    out = []
    counts = {}
    signal_indices = np.flatnonzero(sides != "")
    total_signals = max(1, len(signal_indices))
    for row_no, i in enumerate(signal_indices, 1):
        if progress_callback and (row_no == 1 or row_no % 3000 == 0 or row_no == total_signals):
            progress_callback(0.25 + 0.74 * row_no / total_signals,
                              f"Simulating trades: signal {row_no:,} of {total_signals:,}")
        day, side = dates[i], str(sides[i])
        if counts.get(day, 0) >= max(1, int(max_trades_day)):
            continue
        nxt = i + 1
        if nxt >= len(sig) or dates[nxt] != day:
            continue
        if str(entry_mode) == "Signal candle close":
            entry = float(closes[i])
            path_start = nxt  # never simulate a fill against the already-completed signal candle
            entry_time = datetimes[i]
            entry_candle_time = datetimes[i]
        else:
            entry = float(opens[nxt])
            path_start = nxt
            entry_time = datetimes[nxt]
            entry_candle_time = datetimes[nxt]
        setup = str(setups[i])
        candle_range = max(0.0, float(highs[i]) - float(lows[i]))
        if str(stop_loss_mode).startswith("Adaptive"):
            raw_extreme_distance = (entry - float(lows[i])) if side == "LONG" else (float(highs[i]) - entry)
            if raw_extreme_distance < float(adaptive_sl_threshold):
                risk = raw_extreme_distance + float(sl_buffer)
                sl_method = "Candle extreme + buffer"
            else:
                risk = candle_range * float(adaptive_sl_range_fraction) + float(sl_buffer)
                sl_method = "Range fraction + buffer"
            if risk < float(min_sl_points) or risk > float(max_sl_points):
                continue
            stop = entry - risk if side == "LONG" else entry + risk
        else:
            if side == "LONG":
                stop = float(lows[i]) - float(sl_buffer)
                risk = entry - stop
            else:
                stop = float(highs[i]) + float(sl_buffer)
                risk = stop - entry
            sl_method = "Candle low/high + buffer"
            if str(stop_loss_mode).startswith("50-point cap") and risk >= 50.0:
                risk = 50.0
                stop = entry - risk if side == "LONG" else entry + risk
        if risk <= 0:
            continue
        target = entry + float(rr) * risk if side == "LONG" else entry - float(rr) * risk

        end = session_ends[day]
        # Exit modes: fixed full-position target, or 50% at +1R then step-trail remainder.
        use_partial_trailing = str(profit_booking_mode).startswith("50% at 1R")
        active_stop = stop
        partial_done = False
        partial_pnl = 0.0
        remaining_exit = None
        remaining_reason = None
        remaining_exit_time = None
        for k in range(path_start, end + 1):
            bar_open, bar_high, bar_low, bar_close = opens[k], highs[k], lows[k], closes[k]
            # Conservative priority: an already-active stop is checked before target/partial fills.
            stop_hit = (bar_open <= active_stop or bar_low <= active_stop) if side == "LONG" else (bar_open >= active_stop or bar_high >= active_stop)
            if stop_hit:
                fill = bar_open if ((side == "LONG" and bar_open <= active_stop) or (side == "SHORT" and bar_open >= active_stop)) else active_stop
                remaining_exit, remaining_reason, remaining_exit_time = float(fill), ("SL gap" if fill == bar_open and bar_open != active_stop else ("Trailing SL" if partial_done else "SL")), datetimes[k]
                break

            favorable_price = bar_high if side == "LONG" else bar_low
            favorable = (favorable_price - entry) if side == "LONG" else (entry - favorable_price)
            if not use_partial_trailing:
                target_hit = bar_high >= target if side == "LONG" else bar_low <= target
                if target_hit:
                    fill = bar_open if ((side == "LONG" and bar_open >= target) or (side == "SHORT" and bar_open <= target)) else target
                    remaining_exit, remaining_reason, remaining_exit_time = float(fill), "Target", datetimes[k]
                    break
            else:
                if not partial_done and favorable >= risk:
                    # Book half at exactly +1R; trail remaining half to breakeven.
                    partial_done = True
                    partial_pnl = 0.5 * risk
                    active_stop = entry
                if partial_done:
                    steps = int(max(0.0, favorable) // risk)
                    if steps >= 1:
                        locked_price = entry + (steps - 1) * risk if side == "LONG" else entry - (steps - 1) * risk
                        active_stop = max(active_stop, locked_price) if side == "LONG" else min(active_stop, locked_price)
                    # If the candle reaches a new trailing level, it becomes active next candle.
        if remaining_exit is None:
            remaining_exit = float(closes[end])
            remaining_reason = "EOD"
            remaining_exit_time = datetimes[end]
        if partial_done:
            pnl = partial_pnl + (0.5 * ((remaining_exit - entry) if side == "LONG" else (entry - remaining_exit)))
            exit_price = remaining_exit
            exit_reason = "50% at 1R + " + str(remaining_reason)
        else:
            pnl = (remaining_exit - entry) if side == "LONG" else (entry - remaining_exit)
            exit_price = remaining_exit
            exit_reason = remaining_reason
        exit_time = remaining_exit_time
        out.append({"trade_date": str(day), "setup": setup, "direction": side,
                    "signal_time": datetimes[i], "entry_time": entry_time,
                    "entry": entry, "stop": stop, "target": target,
                    "exit_time": exit_time, "exit": exit_price, "exit_reason": exit_reason,
                    "risk_points": risk, "pnl_points": pnl, "R_multiple": pnl/risk,
                    "partial_exit_at_1R": bool(partial_done), "partial_exit_fraction": 0.5 if partial_done else 0.0,
                    "remaining_exit": float(remaining_exit), "signal_reason": reasons[i], "PDH": pdhs[i], "PDL": pdls[i],
                    "signal_candle_time": datetimes[i], "entry_candle_time": entry_candle_time,
                    "sl_reference_candle_time": datetimes[i], "sl_method": sl_method,
                    "raw_extreme_distance_points": ((entry - float(lows[i])) if side == "LONG" else (float(highs[i]) - entry)),
                    "signal_candle_range_points": candle_range})
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


def save_job_checkpoint(job_id, payload, sync_remote=True):
    payload={**payload,"job_id":job_id,"updated_at":datetime.utcnow().isoformat()+"Z"}
    raw=json.dumps(payload,default=str)
    con=_checkpoint_store()
    con.execute("INSERT INTO jobs(job_id,updated_at,payload) VALUES(?,?,?) ON CONFLICT(job_id) DO UPDATE SET updated_at=excluded.updated_at,payload=excluded.payload",(job_id,payload["updated_at"],raw)); con.commit(); con.close()
    if sync_remote and _remote_checkpoint_enabled():
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
    """Stability-oriented score; avoids ranking solely by raw net R."""
    if trades is None or trades.empty or len(trades) < int(min_trades):
        return -1e9
    r = pd.to_numeric(trades["R_multiple"], errors="coerce").dropna()
    if len(r) < int(min_trades):
        return -1e9
    equity = r.cumsum()
    drawdown = equity - equity.cummax().clip(lower=0)
    max_dd = abs(float(drawdown.min())) if len(drawdown) else 0.0
    expectancy = float(r.mean())
    gross_profit = float(r[r > 0].sum())
    gross_loss = abs(float(r[r < 0].sum()))
    pf = gross_profit / gross_loss if gross_loss > 0 else (5.0 if gross_profit > 0 else 0.0)
    monthly = pd.DataFrame({"r": r.values})
    if "exit_time" in trades:
        dates = pd.to_datetime(trades.loc[r.index, "exit_time"], errors="coerce")
        monthly["month"] = dates.dt.to_period("M").astype(str).values
    elif "trade_date" in trades:
        dates = pd.to_datetime(trades.loc[r.index, "trade_date"], errors="coerce")
        monthly["month"] = dates.dt.to_period("M").astype(str).values
    else:
        monthly["month"] = "all"
    monthly_r = monthly.groupby("month")["r"].sum()
    positive_month_ratio = float((monthly_r > 0).mean()) if len(monthly_r) else 0.0
    monthly_volatility = float(monthly_r.std(ddof=0)) if len(monthly_r) > 1 else 0.0
    # Reward repeatable expectancy and profitable months; penalize drawdown and instability.
    sample_factor = min(1.0, (len(r) / max(1.0, float(min_trades))) ** 0.5)
    return float((expectancy * 10.0 + min(pf, 5.0) * 0.5 + positive_month_ratio * 2.0
                  - (max_dd / max(1.0, len(r) ** 0.5)) * 0.5 - monthly_volatility * 0.10)
                 * sample_factor)


def _optimizer_candidate_sets(seed=20261117, signal_count=40, exits_per_signal=4):
    """Deterministic coarse-to-fine candidate design with balanced exit modes."""
    import random
    rng = random.Random(seed)
    grids = {
        "body": [20, 30, 40, 50, 60, 80, 100],
        "momentum_body": [40, 50, 60, 75, 100],
        "breakout_confirm_candles": [1, 2, 3],
        "gap": [30, 40, 50, 60, 75, 100],
        "cutoff": ["10:00", "10:30", "11:00", "11:30", "12:00"],
    }
    seen = set(); signal_sets = []
    # Include current defaults as a benchmark, then deterministic random coverage.
    defaults = (20, 50, 1, 50, "11:00")
    signal_sets.append(dict(zip(grids, defaults)))
    while len(signal_sets) < signal_count:
        vals = tuple(rng.choice(grids[k]) for k in grids)
        if vals in seen or vals == defaults:
            continue
        seen.add(vals)
        signal_sets.append(dict(zip(grids, vals)))
    exits = [
        {"sl": 5.0, "rr": 1.5, "profit_booking_mode": "Fixed target", "stop_loss_mode": "Candle low/high"},
        {"sl": 10.0, "rr": 2.0, "profit_booking_mode": "Fixed target", "stop_loss_mode": "50-point cap"},
        {"sl": 15.0, "rr": 2.5, "profit_booking_mode": "50% at 1R + step trailing", "stop_loss_mode": "Candle low/high"},
        {"sl": 10.0, "rr": 3.0, "profit_booking_mode": "50% at 1R + step trailing", "stop_loss_mode": "50-point cap"},
        {"sl": 0.0, "rr": 2.0, "profit_booking_mode": "Fixed target", "stop_loss_mode": "Candle low/high"},
        {"sl": 20.0, "rr": 2.5, "profit_booking_mode": "Fixed target", "stop_loss_mode": "50-point cap"},
        {"sl": 5.0, "rr": 2.0, "profit_booking_mode": "50% at 1R + step trailing", "stop_loss_mode": "50-point cap"},
        {"sl": 15.0, "rr": 3.0, "profit_booking_mode": "Fixed target", "stop_loss_mode": "Candle low/high"},
    ]
    candidates = []
    for sig in signal_sets:
        # Deterministic balanced coverage across fixed/partial and both SL modes.
        chosen = rng.sample(exits, k=min(exits_per_signal, len(exits)))
        for ex in chosen:
            candidates.append({**sig, **ex})
    return candidates


def optimize_parameters(df, min_trades=30, progress_callback=None, checkpoint_callback=None, resume_state=None):
    """Fast deterministic optimizer: cache entry signals, rank train/validation, test once."""
    clean = prepare_spot(df)
    dates = sorted(clean.trade_date.unique())
    if len(dates) < 250:
        raise ValueError(f"Optimizer needs at least 250 trading sessions; this dataset has {len(dates)}. Use at least about one year of 5-minute data for a meaningful result.")
    n = len(dates)
    train_end = dates[int(n * .60) - 1]
    valid_end = dates[int(n * .80) - 1]
    train_dates = set(d for d in dates if d <= train_end)
    valid_dates = set(d for d in dates if train_end < d <= valid_end)
    test_dates = set(d for d in dates if d > valid_end)
    if len(valid_dates) < 20 or len(test_dates) < 20:
        raise ValueError("Not enough sessions in validation/test windows; use a longer historical dataset.")

    candidates = _optimizer_candidate_sets()
    state = resume_state or {"phase": "screen", "train_done": {}, "valid_done": {}, "progress": 0.0}
    state.setdefault("train_done", {}); state.setdefault("valid_done", {})
    state["phase"] = "screen"
    total_work = len(candidates) + 25 + 1
    completed = len(state["train_done"]) + len(state["valid_done"])

    def save(message):
        state["progress"] = min(.99, completed / max(1, total_work))
        state["message"] = message
        if checkpoint_callback:
            checkpoint_callback(state)
        if progress_callback:
            progress_callback(state["progress"], message)

    def signal_key(p):
        return (float(p["body"]), float(p["momentum_body"]), int(p["breakout_confirm_candles"]), float(p["gap"]), str(p["cutoff"]))

    signal_cache = {}
    def get_signals(p):
        key = signal_key(p)
        if key not in signal_cache:
            signal_cache[key] = build_signals(
                clean, min_entry_body=key[0], min_momentum_body=key[1],
                breakout_confirm_candles=key[2], gap_threshold=key[3], cutoff=key[4]
            )
        return signal_cache[key]

    def evaluate(p, date_set, signals):
        part = signals[signals["trade_date"].isin(date_set)].copy()
        trades, _, _ = run_backtest(
            part, 1, "Next candle open", float(p["sl"]), float(p["rr"]),
            float(p["body"]), float(p["gap"]), str(p["cutoff"]),
            min_momentum_body=float(p["momentum_body"]),
            breakout_confirm_candles=int(p["breakout_confirm_candles"]),
            profit_booking_mode=str(p["profit_booking_mode"]),
            stop_loss_mode=str(p["stop_loss_mode"]), prebuilt_signals=part
        )
        return trades

    # Stage 1: screen 160 balanced candidates on training data. Signals are built
    # once per entry-rule combination and reused across every SL/exit variant.
    for i, p in enumerate(candidates, 1):
        cid = str(i - 1)
        if cid in state["train_done"]:
            continue
        sig = get_signals(p)
        tr = evaluate(p, train_dates, sig)
        score = optimizer_score(tr, int(min_trades))
        state["train_done"][cid] = {
            "params": p, "score": score, "trades": len(tr),
            "total_r": float(tr.R_multiple.sum()) if len(tr) else 0.0,
            "avg_r": float(tr.R_multiple.mean()) if len(tr) else np.nan,
        }
        completed += 1
        save(f"Training screen: {i:,}/{len(candidates):,} candidates")

    eligible = [v for v in state["train_done"].values() if float(v.get("score", -1e9)) > -1e8]
    eligible.sort(key=lambda v: float(v["score"]), reverse=True)
    if not eligible:
        raise ValueError("No candidates met the training minimum-trade requirement. Reduce the minimum trade filter or upload more history.")
    finalists = eligible[:25]
    state["phase"] = "validation"
    # Stage 2: evaluate top 25 on the chronological validation window.
    for j, item in enumerate(finalists, 1):
        p = item["params"]
        cid = hashlib.sha1(json.dumps(p, sort_keys=True).encode()).hexdigest()
        if cid in state["valid_done"]:
            continue
        sig = get_signals(p)
        vt = evaluate(p, valid_dates, sig)
        vmin = max(8, min(20, int(min_trades) // 5))
        vscore = optimizer_score(vt, vmin)
        state["valid_done"][cid] = {
            "params": p, "score": vscore, "trades": len(vt),
            "total_r": float(vt.R_multiple.sum()) if len(vt) else 0.0,
            "avg_r": float(vt.R_multiple.mean()) if len(vt) else np.nan,
            "max_dd": abs(float((vt.R_multiple.cumsum() - vt.R_multiple.cumsum().cummax().clip(lower=0)).min())) if len(vt) else 0.0,
        }
        completed += 1
        save(f"Validation: {j:,}/{len(finalists):,} finalists")

    ranked = []
    for v in state["valid_done"].values():
        p = v["params"]
        ranked.append({**p, "Validation score": v["score"], "Validation trades": v["trades"],
                       "Validation total R": v["total_r"], "Validation average R": v["avg_r"],
                       "Validation max DD R": v["max_dd"]})
    ranked_df = pd.DataFrame(ranked)
    if ranked_df.empty or not (pd.to_numeric(ranked_df["Validation score"], errors="coerce") > -1e8).any():
        raise ValueError("No candidate passed the validation trade-count filter. Use more data or lower the minimum trade requirement.")
    ranked_df = ranked_df.sort_values(["Validation score", "Validation average R", "Validation max DD R"],
                                      ascending=[False, False, True]).reset_index(drop=True)
    best_row = ranked_df.iloc[0]
    best = {k: best_row[k] for k in candidates[0].keys()}
    best["body"] = float(best["body"]); best["momentum_body"] = float(best["momentum_body"])
    best["breakout_confirm_candles"] = int(best["breakout_confirm_candles"])
    best["sl"] = float(best["sl"]); best["rr"] = float(best["rr"]); best["gap"] = float(best["gap"])
    best["cutoff"] = str(best["cutoff"])
    # Stage 3: untouched final 20% is run exactly once for the selected candidate.
    final_signals = get_signals(best)
    test_trades = evaluate(best, test_dates, final_signals)
    test_stats = stats_for(test_trades) if not test_trades.empty else {}
    test_stats["Test sessions"] = len(test_dates)
    test_stats["Validation score"] = float(best_row["Validation score"])
    completed += 1
    final_state = {**state, "phase": "completed", "progress": 1.0,
                   "message": "Optimization completed; final test period evaluated once",
                   "ranked": ranked_df.to_dict("records"), "best": best,
                   "test_stats": test_stats, "test_trades": test_trades.to_dict("records"),
                   "splits": [len(train_dates), len(valid_dates), len(test_dates)]}
    if checkpoint_callback:
        checkpoint_callback(final_state)
    if progress_callback:
        progress_callback(1.0, "Optimization completed")
    return ranked_df, best, test_stats, len(train_dates), len(valid_dates), len(test_dates), test_trades


# In-process worker registry. The durable checkpoint is written outside Streamlit session state.
_NQL_WORKERS={}; _NQL_LOCK=threading.Lock()
def _run_optimizer_worker(job_id, frame, min_trades, fingerprint, resume_state=None):
    try:
        checkpoint_count = 0
        def persist(state):
            nonlocal checkpoint_count
            checkpoint_count += 1
            # Keep a local SQLite checkpoint after every completed evaluation,
            # but avoid a slow remote HTTP upload for every single parameter.
            # Remote durability is synced every 10 evaluations and at completion.
            save_job_checkpoint(job_id,{"status":"running","fingerprint":fingerprint,"min_trades":min_trades,**state},
                                sync_remote=(checkpoint_count % 10 == 0))
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


def kite_fetch_nfo_option_instruments(api_key, access_token):
    """Fetch the live NFO instrument master and retain NIFTY option contracts."""
    response = requests.get(
        f"{KITE_API_ROOT}/instruments/NFO",
        headers=kite_headers(api_key, access_token),
        timeout=60,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Could not download NFO instrument list: HTTP {response.status_code}")
    frame = pd.read_csv(StringIO(response.text))
    required = {"instrument_token", "tradingsymbol", "name", "expiry",
                "strike", "instrument_type", "exchange", "segment", "lot_size"}
    missing = required - set(frame.columns)
    if missing:
        raise RuntimeError("NFO instrument master is missing columns: " + ", ".join(sorted(missing)))
    frame["expiry"] = pd.to_datetime(frame["expiry"], errors="coerce").dt.date
    frame["strike"] = pd.to_numeric(frame["strike"], errors="coerce")
    frame["instrument_token"] = pd.to_numeric(frame["instrument_token"], errors="coerce")
    frame = frame[
        frame["name"].astype(str).str.upper().eq("NIFTY")
        & frame["instrument_type"].astype(str).str.upper().isin(["CE", "PE"])
        & frame["expiry"].notna()
        & frame["strike"].notna()
        & frame["instrument_token"].notna()
    ].copy()
    if frame.empty:
        raise RuntimeError("No active NIFTY CE/PE contracts were found in the current NFO instrument master.")
    frame["instrument_token"] = frame["instrument_token"].astype("int64")
    return frame.sort_values(["expiry", "strike", "instrument_type"]).reset_index(drop=True)


def kite_get_nifty_spot_ltp(api_key, access_token):
    """Return the latest NIFTY 50 index LTP, or None if quote is unavailable."""
    data = kite_get(api_key, access_token, "/quote/ltp", params={"i": "NSE:NIFTY 50"})
    item = data.get("NSE:NIFTY 50", {}) if isinstance(data, dict) else {}
    value = item.get("last_price")
    return float(value) if value is not None else None


def kite_fetch_option_history(api_key, access_token, instrument, start_date, end_date, interval):
    """Fetch a single currently-live option contract with OI."""
    token = int(instrument["instrument_token"])
    candles = kite_fetch_historical(api_key, access_token, token, start_date, end_date, interval)
    if candles.empty:
        return candles
    candles["datetime"] = pd.to_datetime(candles["datetime"], errors="coerce")
    candles = candles.dropna(subset=["datetime"]).copy()
    candles["tradingsymbol"] = str(instrument["tradingsymbol"])
    candles["instrument_token"] = token
    candles["expiry"] = str(instrument["expiry"])
    candles["strike"] = float(instrument["strike"])
    candles["option_type"] = str(instrument["instrument_type"])
    candles["underlying"] = "NIFTY"
    return candles


def render_nifty_option_downloader(api_key, access_token):
    """Render the NIFTY options contract selector, historical downloader and exports."""
    st.divider()
    st.subheader("Download NIFTY option historical data")
    st.caption(
        "Downloads currently active NIFTY CE/PE contracts from Kite Connect. "
        "Each contract includes timestamp, OHLC, volume and OI where supplied by Kite."
    )

    cache_key = "kite_nfo_option_master"
    if st.button("Refresh NIFTY option contracts", key="kite_refresh_option_master",
                 use_container_width=True):
        try:
            with st.spinner("Downloading current NFO instrument master..."):
                st.session_state[cache_key] = kite_fetch_nfo_option_instruments(api_key, access_token)
                st.session_state["kite_nfo_option_master_loaded_at"] = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
            st.success(f"Loaded {len(st.session_state[cache_key]):,} active NIFTY option contracts.")
        except Exception as exc:
            st.error(f"Could not load option contracts: {exc}")

    instruments = st.session_state.get(cache_key)
    if instruments is None or instruments.empty:
        st.info("Refresh NIFTY option contracts to load the current expiry and strike list.")
        return

    loaded_at = st.session_state.get("kite_nfo_option_master_loaded_at", "Current session")
    st.caption(f"Instrument master last refreshed: {loaded_at}")

    today = datetime.now(IST).date()
    available_expiries = sorted(x for x in instruments["expiry"].unique() if x >= today)
    if not available_expiries:
        st.warning("The current instrument master contains no unexpired NIFTY options.")
        return

    c1, c2, c3 = st.columns(3)
    with c1:
        expiry = st.selectbox("Expiry", available_expiries, format_func=lambda x: x.strftime("%d %b %Y"),
                              key="kite_option_expiry")
    with c2:
        option_side = st.selectbox("Option type", ["Both CE & PE", "CE only", "PE only"],
                                   key="kite_option_side")
    with c3:
        interval = st.selectbox("Candle interval", ["5minute", "minute", "15minute", "30minute", "day"],
                                index=0, key="kite_option_interval")

    expiry_contracts = instruments[instruments["expiry"] == expiry].copy()
    if option_side == "CE only":
        expiry_contracts = expiry_contracts[expiry_contracts["instrument_type"] == "CE"]
    elif option_side == "PE only":
        expiry_contracts = expiry_contracts[expiry_contracts["instrument_type"] == "PE"]

    spot = None
    spot_error = None
    try:
        spot = kite_get_nifty_spot_ltp(api_key, access_token)
    except Exception as exc:
        spot_error = str(exc)
    if spot is not None:
        st.metric("NIFTY spot LTP (Kite)", f"{spot:,.2f}")
        strikes = sorted(expiry_contracts["strike"].dropna().unique().tolist())
        atm_strike = min(strikes, key=lambda x: abs(float(x) - spot)) if strikes else None
    else:
        st.warning(f"Could not fetch live NIFTY spot LTP ({spot_error}). Choose strikes manually below.")
        atm_strike = None

    mode = st.radio("Strike selection", ["ATM range", "Select strikes manually"],
                    horizontal=True, key="kite_option_strike_mode")
    if mode == "ATM range" and atm_strike is not None:
        steps = st.number_input("Number of strikes either side of ATM", min_value=0, max_value=30,
                                value=2, step=1, key="kite_option_atm_steps")
        strike_values = sorted(expiry_contracts["strike"].dropna().unique().tolist())
        nearest_idx = min(range(len(strike_values)), key=lambda i: abs(float(strike_values[i]) - float(atm_strike)))
        chosen_strikes = strike_values[max(0, nearest_idx-int(steps)):min(len(strike_values), nearest_idx+int(steps)+1)]
        selected_contracts = expiry_contracts[expiry_contracts["strike"].isin(chosen_strikes)].copy()
        st.caption(f"ATM reference: {atm_strike:g}; selected strike range: {min(chosen_strikes):g}–{max(chosen_strikes):g}" if chosen_strikes else "No strikes selected.")
    else:
        contract_labels = expiry_contracts.apply(
            lambda r: f"{r['tradingsymbol']} | {float(r['strike']):g} | {r['instrument_type']}", axis=1
        )
        selected_labels = st.multiselect(
            "Choose option contracts", contract_labels.tolist(),
            default=contract_labels.tolist()[:min(4, len(contract_labels))],
            key="kite_option_manual_contracts",
        )
        selected_contracts = expiry_contracts.loc[contract_labels.isin(selected_labels)].copy()

    if selected_contracts.empty:
        st.warning("No contracts selected.")
        return
    if len(selected_contracts) > 40:
        st.warning(f"{len(selected_contracts)} contracts selected. Limit one batch to 40 contracts to avoid excessive API requests.")
        return

    st.write(f"**Selected contracts: {len(selected_contracts)}**")
    st.dataframe(
        selected_contracts[["tradingsymbol", "expiry", "strike", "instrument_type", "lot_size", "instrument_token"]],
        use_container_width=True, hide_index=True
    )

    d1, d2 = st.columns(2)
    with d1:
        start_date = st.date_input("Options data start date", value=max(today - timedelta(days=5), date(2015, 1, 1)),
                                   min_value=date(2015, 1, 1), max_value=today, key="kite_option_start")
    with d2:
        end_date = st.date_input("Options data end date", value=today, min_value=date(2015, 1, 1),
                                 max_value=today, key="kite_option_end")
    if start_date > end_date:
        st.error("Start date must be on or before end date.")
        return

    st.caption(
        "Kite's current instrument master only contains live contracts. Download and archive a contract's "
        "intraday candles before it expires; the API does not provide expired-option intraday history later."
    )
    if st.button("Download selected NIFTY options", type="primary",
                 use_container_width=True, key="kite_download_options"):
        cache = st.session_state.setdefault("kite_option_history_cache", {})
        downloaded, failed, empty = [], [], []
        progress = st.progress(0, text="Preparing option downloads...")
        status = st.empty()
        total = len(selected_contracts)
        for idx, (_, instrument) in enumerate(selected_contracts.iterrows(), start=1):
            token = int(instrument["instrument_token"])
            cache_id = f"{token}|{start_date}|{end_date}|{interval}"
            if cache_id in cache:
                status.write(f"{idx}/{total}: Already downloaded — {instrument['tradingsymbol']}")
                downloaded.append(cache[cache_id])
                progress.progress(idx / total, text=f"Reusing saved session data: {idx}/{total}")
                continue
            try:
                status.write(f"{idx}/{total}: Downloading {instrument['tradingsymbol']}...")
                frame = kite_fetch_option_history(api_key, access_token, instrument, start_date, end_date, interval)
                if frame.empty:
                    empty.append(str(instrument["tradingsymbol"]))
                else:
                    cache[cache_id] = frame
                    downloaded.append(frame)
            except Exception as exc:
                failed.append(f"{instrument['tradingsymbol']}: {exc}")
            progress.progress(idx / total, text=f"Processed {idx}/{total} contracts")
            time.sleep(0.35)
        if downloaded:
            result = pd.concat(downloaded, ignore_index=True)
            result = result.drop_duplicates(["instrument_token", "datetime"]).sort_values(
                ["expiry", "strike", "option_type", "datetime"]
            ).reset_index(drop=True)
            st.session_state["kite_nifty_option_result"] = result
            st.session_state["kite_nifty_option_contracts_result"] = selected_contracts.copy()
            st.session_state["kite_nifty_option_meta"] = {
                "start": str(start_date), "end": str(end_date), "interval": interval,
                "contracts": sorted(result["tradingsymbol"].dropna().unique().tolist())
            }
            st.success(f"Downloaded/reused {len(result):,} candles across {result['tradingsymbol'].nunique()} contracts.")
        if empty:
            st.warning("Kite returned no candles for: " + ", ".join(empty))
        if failed:
            st.error("Some contracts failed:\n" + "\n".join(failed))

    result = st.session_state.get("kite_nifty_option_result")
    if result is not None and not result.empty:
        meta = st.session_state.get("kite_nifty_option_meta", {})
        st.subheader("Downloaded options data")
        st.write(
            f"{len(result):,} candles • {result['tradingsymbol'].nunique()} contracts • "
            f"{meta.get('start', '')} to {meta.get('end', '')} • {meta.get('interval', '')}"
        )
        st.dataframe(result.head(30), use_container_width=True, hide_index=True)
        st.download_button(
            "Download combined options CSV",
            result.to_csv(index=False).encode("utf-8"),
            file_name="nifty_options_historical.csv", mime="text/csv",
            use_container_width=True, key="kite_options_combined_csv"
        )
        from io import BytesIO
        import zipfile
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("nifty_options_combined.csv", result.to_csv(index=False).encode("utf-8"))
            saved_contracts = st.session_state.get("kite_nifty_option_contracts_result", selected_contracts)
            archive.writestr("selected_contracts.csv", saved_contracts.to_csv(index=False).encode("utf-8"))
            for symbol, group in result.groupby("tradingsymbol", sort=True):
                archive.writestr(f"contracts/{symbol}.csv", group.to_csv(index=False).encode("utf-8"))
        st.download_button(
            "Download ZIP (combined + individual contracts)",
            zip_buffer.getvalue(), file_name="nifty_options_historical_bundle.zip",
            mime="application/zip", use_container_width=True, key="kite_options_zip"
        )


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
        prepared = prepare_spot(history).copy()
        today = datetime.now(IST).date()
        # Historical data fetched at monitor startup may already contain today's
        # completed 5-minute candles. Keep those separately for first-candle replay.
        today_rows = prepared[prepared["datetime"].dt.date == today].copy()
        self.history = prepared[prepared["datetime"].dt.date < today].copy()
        now_ist = datetime.now(IST)
        current_bucket = now_ist.replace(minute=(now_ist.minute // 5) * 5, second=0, microsecond=0)
        if not today_rows.empty:
            # Do not evaluate the still-forming candle returned by Kite history.
            today_rows = today_rows[today_rows["datetime"].apply(lambda value: pd.Timestamp(value).to_pydatetime().replace(tzinfo=IST) if pd.Timestamp(value).tzinfo is None else pd.Timestamp(value).tz_convert(IST).to_pydatetime()) < current_bucket].copy()
        self.today_candles = today_rows.drop(columns=["trade_date"], errors="ignore").to_dict("records")
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
        self.pending_signal = None
        self.last_signal = None
        self.last_alert_key = None
        self.last_tick_time = None
        self.last_price = None
        self.last_message = ""
        self.trade_date = today if self.today_candles else None
        # Replay today's completed candles from the first 5-minute candle.
        # If a valid setup already happened before monitoring began, mark it missed
        # locally and suppress a late Telegram entry alert.
        if self.today_candles and not self.history.empty:
            replay_df = pd.concat([self.history, pd.DataFrame(self.today_candles)], ignore_index=True, sort=False)
            replay = build_signals(replay_df, gap_threshold=self.params["gap"], cutoff=self.params["cutoff"], min_entry_body=self.params["entry_body"], min_momentum_body=self.params["momentum_body"], breakout_confirm_candles=self.params.get("breakout_confirm_candles", 1))
            replay_today = replay[replay["trade_date"] == today]
            replay_hits = replay_today[replay_today["signal"].astype(str).str.len() > 0]
            if not replay_hits.empty:
                missed = replay_hits.iloc[0]
                self.last_alert_key = f"{today}|{missed.datetime}|{missed.signal}|{missed.setup}"
                self.last_message = f"{missed.signal} {missed.setup} already triggered at {missed.datetime}; entry missed. No late entry alert sent."

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
        if str(self.params.get("stop_loss_mode", "Candle low/high")).startswith("50-point cap") and risk >= 50.0:
            risk = 50.0
            stop = entry_price-risk if side == "LONG" else entry_price+risk
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
            f"Parameters: entry body {self.params['entry_body']:g}, momentum body {self.params['momentum_body']:g}, SL buffer {buffer:g}, gap {self.params['gap']:g}, cutoff {self.params['cutoff']}\n"
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
            sig = build_signals(combined, gap_threshold=self.params["gap"], cutoff=self.params["cutoff"], min_entry_body=self.params["entry_body"], min_momentum_body=self.params["momentum_body"], breakout_confirm_candles=self.params.get("breakout_confirm_candles", 1))
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
                # KiteTicker builds these naive datetimes with datetime.fromtimestamp(),
                # so their wall time follows the Streamlit host timezone (UTC on Cloud),
                # not necessarily IST. Reinterpret through the host-local epoch, then
                # convert that instant to IST to avoid a 5h30 live-candle offset.
                ts = datetime.fromtimestamp(ts.timestamp(), IST)
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


def render_live_signal_center(api_key, access_token, live_params):
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
                if monitor is None or monitor.status == "Stopped" or monitor.params != live_params:
                    if monitor is not None:
                        monitor.stop()
                    instrument_token, symbol = kite_find_nifty_token(api_key, access_token)
                    hist = kite_fetch_historical(api_key, access_token, instrument_token, date.today()-timedelta(days=10), date.today(), "5minute")
                    if hist.empty:
                        raise RuntimeError("Kite returned no recent 5-minute candles. Check market-data access.")
                    hist["datetime"] = pd.to_datetime(hist["datetime"], errors="coerce")
                    monitor = NiftyLiveMonitor(api_key, access_token, instrument_token, hist, dict(live_params), tg_token, tg_chat)
                    with registry["lock"]:
                        registry["monitors"][token_key] = monitor
                monitor.start()
                try:
                    monitor._send_telegram("Nifty Quant Lab live monitor started.\nFeed: Nifty 50 spot / Kite Connect\nParameters: " + str(monitor.params) + "\nMode: alert-only; no orders will be placed.")
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
            st.caption(f"Active live parameters: body {active_monitor.params['entry_body']:g} points • SL buffer {active_monitor.params['sl_buffer']:g} points • SL method {active_monitor.params.get('stop_loss_mode', 'Candle low/high')} • target {active_monitor.params['rr']:g}R • gap {active_monitor.params['gap']:g} points • cutoff {active_monitor.params['cutoff']}. These match the PDHL Strategy Settings above.")
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
    st.header("Market Data Center")
    with st.expander("Historical NIFTY Options — Hugging Face", expanded=True):
        render_hf_nifty_options_downloader()
    with st.expander("Kite Connect — Live / active contracts", expanded=False):
        render_kite_connection_panel()


def render_kite_connection_panel():
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
        st.caption("Download NIFTY spot/index candles here; use the options downloader below for active CE/PE contracts.")
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
        render_nifty_option_downloader(api_key, access_token)

    else:
        login_url = f"{KITE_LOGIN_ROOT}?v=3&api_key={api_key}"
        st.info("Credentials found. Sign in to Zerodha to connect this browser session.")
        st.link_button("Connect to Zerodha", login_url, use_container_width=True)
        st.caption("After login, Zerodha redirects to the registered Streamlit URL with a short-lived request_token. The app exchanges it server-side; the API secret is never sent to the browser.")



# ---------------- Hugging Face historical NIFTY options data ----------------
HF_NQL_DATASET_ID = "thetrademarkk/india-index-options-1m"
HF_NQL_API_ROOT = "https://huggingface.co/api/datasets/" + HF_NQL_DATASET_ID
HF_NQL_RESOLVE_ROOT = "https://huggingface.co/datasets/" + HF_NQL_DATASET_ID + "/resolve/main/"

@st.cache_data(ttl=3600, show_spinner=False)
def hf_nql_list_nifty_option_shards():
    """List public NIFTY option Parquet shards from the Hugging Face dataset."""
    import re
    url = HF_NQL_API_ROOT + "/tree/main/options/NIFTY?recursive=false&expand=false&limit=1000"
    session = requests.Session()
    shards = []
    seen = set()
    while url:
        response = session.get(url, timeout=45)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("Unexpected Hugging Face file-list response.")
        for item in payload:
            path = str(item.get("path", ""))
            if item.get("type") != "file" or not path.lower().endswith(".parquet"):
                continue
            name = path.rsplit("/", 1)[-1]
            match = re.search(r"(20\d{2}-\d{2}-\d{2})\.parquet$", name)
            if not match or path in seen:
                continue
            seen.add(path)
            shards.append({"path": path, "name": name, "partition_date": match.group(1),
                           "size": int(item.get("size", 0) or 0)})
        # Follow server pagination when present; many repos fit in one response.
        link = response.headers.get("Link", "")
        next_url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                left, right = part.find("<"), part.find(">")
                if left >= 0 and right > left:
                    next_url = part[left + 1:right]
                    break
        url = next_url
    if not shards:
        raise RuntimeError("No NIFTY option Parquet shards were listed by Hugging Face.")
    return sorted(shards, key=lambda x: x["partition_date"])


def hf_nql_read_option_shard(shard_path):
    """Download one public Parquet shard and return its rows as a DataFrame."""
    from io import BytesIO
    url = HF_NQL_RESOLVE_ROOT + shard_path
    response = requests.get(url, timeout=180)
    response.raise_for_status()
    try:
        return pd.read_parquet(BytesIO(response.content))
    except ImportError as exc:
        raise RuntimeError("Parquet reader is missing. Add pyarrow to requirements.txt and redeploy.") from exc


def hf_nql_normalize_option_frame(frame):
    """Normalize the published dataset schema to the NQL option-data schema."""
    d = frame.copy()
    aliases = {"timestamp": "datetime", "option_type": "option_type", "open_interest": "oi"}
    for source, target in aliases.items():
        if source in d.columns and target not in d.columns:
            d[target] = d[source]
    required = {"datetime", "open", "high", "low", "close", "volume", "oi",
                "strike", "option_type", "expiry"}
    missing = required - set(d.columns)
    if missing:
        raise RuntimeError("Dataset shard is missing required fields: " + ", ".join(sorted(missing)))
    d["datetime"] = pd.to_datetime(d["datetime"], errors="coerce")
    # Keep timestamps in IST for consistent charting and backtesting.
    if getattr(d["datetime"].dt, "tz", None) is None:
        d["datetime"] = d["datetime"].dt.tz_localize("Asia/Kolkata")
    else:
        d["datetime"] = d["datetime"].dt.tz_convert("Asia/Kolkata")
    for col in ["open", "high", "low", "close", "volume", "oi", "strike"]:
        d[col] = pd.to_numeric(d[col], errors="coerce")
    d["option_type"] = d["option_type"].astype(str).str.upper().str.strip()
    d["expiry"] = pd.to_datetime(d["expiry"], errors="coerce").dt.date
    d["underlying"] = "NIFTY"
    return d.dropna(subset=["datetime", "open", "high", "low", "close", "strike", "expiry"])


def render_hf_nifty_options_downloader():
    """Historical NIFTY options importer, independent of Kite authentication."""
    st.subheader("Historical NIFTY options — Hugging Face dataset")
    st.caption("Source: thetrademarkk/india-index-options-1m · 1-minute Parquet · expired contracts included where present")
    st.warning("Dataset licence: CC-BY-NC-4.0 (non-commercial use only). Coverage is partial; far-OTM and illiquid contracts may be missing. Verify data before relying on backtest results.")
    st.markdown("[Open source dataset](https://huggingface.co/datasets/thetrademarkk/india-index-options-1m)")

    if st.button("Refresh Hugging Face NIFTY file catalogue", key="hf_nql_refresh_catalogue", use_container_width=True):
        hf_nql_list_nifty_option_shards.clear()
        st.session_state.pop("hf_nql_shards", None)
    shards = st.session_state.get("hf_nql_shards")
    if shards is None:
        try:
            with st.spinner("Reading the public NIFTY options file catalogue..."):
                shards = hf_nql_list_nifty_option_shards()
            st.session_state["hf_nql_shards"] = shards
        except Exception as exc:
            st.error(f"Could not read Hugging Face file catalogue: {type(exc).__name__}: {exc}")
            return

    shard_dates = [pd.Timestamp(x["partition_date"]).date() for x in shards]
    first_available, last_available = min(shard_dates), max(shard_dates)
    total_gb = sum(x["size"] for x in shards) / (1024 ** 3)
    st.caption(f"Catalogue: {len(shards):,} Parquet files · {total_gb:.2f} GB total · shard labels {first_available} to {last_available}")

    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input("Historical options start date", value=max(first_available, last_available - timedelta(days=30)),
                                   min_value=first_available, max_value=last_available, key="hf_nql_start")
    with c2:
        end_date = st.date_input("Historical options end date", value=last_available,
                                 min_value=first_available, max_value=last_available, key="hf_nql_end")
    if start_date > end_date:
        st.error("Start date must be on or before end date.")
        return

    # File names are shard/partition labels, not guaranteed expiry dates. Use a
    # conservative 35-day margin, then filter every downloaded row by timestamp.
    margin_start = start_date - timedelta(days=35)
    margin_end = end_date + timedelta(days=35)
    selected_shards = [x for x in shards if margin_start <= pd.Timestamp(x["partition_date"]).date() <= margin_end]
    if not selected_shards:
        st.warning("No NIFTY Parquet shards fall near the selected date range.")
        return
    estimated_mb = sum(x["size"] for x in selected_shards) / (1024 ** 2)
    st.info(f"This date range will inspect {len(selected_shards):,} candidate shard(s), approximately {estimated_mb:.1f} MB before row filtering. The app filters the actual candle timestamps after download.")

    # Persist per-shard results in the session so reruns do not download them again.
    if st.button("Download historical NIFTY option data", type="primary", use_container_width=True, key="hf_nql_download"):
        cache = st.session_state.setdefault("hf_nql_shard_cache", {})
        frames, errors = [], []
        progress = st.progress(0, text="Starting Hugging Face download...")
        status = st.empty()
        for i, shard in enumerate(selected_shards, 1):
            path = shard["path"]
            try:
                if path not in cache:
                    status.write(f"{i}/{len(selected_shards)} · Downloading {shard['name']} ({shard['size'] / 1048576:.1f} MB)")
                    cache[path] = hf_nql_read_option_shard(path)
                else:
                    status.write(f"{i}/{len(selected_shards)} · Reusing cached {shard['name']}")
                frames.append(cache[path])
            except Exception as exc:
                errors.append(f"{shard['name']}: {type(exc).__name__}: {exc}")
            progress.progress(i / len(selected_shards), text=f"Processed {i}/{len(selected_shards)} shards")
        if frames:
            try:
                raw = pd.concat(frames, ignore_index=True)
                data = hf_nql_normalize_option_frame(raw)
                data = data[(data["datetime"].dt.date >= start_date) & (data["datetime"].dt.date <= end_date)].copy()
                if "symbol" in data.columns:
                    data = data[data["symbol"].astype(str).str.upper().eq("NIFTY")].copy()
                data = data.drop_duplicates(["datetime", "expiry", "strike", "option_type"]).sort_values(
                    ["datetime", "expiry", "strike", "option_type"]
                ).reset_index(drop=True)
                st.session_state["hf_nql_options_raw"] = data
                st.session_state["hf_nql_options_range"] = (start_date, end_date)
                st.success(f"Loaded {len(data):,} NIFTY option candles from {len(frames):,} Parquet shard(s).")
            except Exception as exc:
                st.error(f"Could not normalize downloaded option data: {type(exc).__name__}: {exc}")
        else:
            st.error("No shards could be downloaded. Check the error list below.")
        if errors:
            with st.expander(f"Download errors ({len(errors)})"):
                for message in errors:
                    st.write(message)

    data = st.session_state.get("hf_nql_options_raw")
    if data is None or data.empty:
        st.info("Choose a date range and download the historical option shards. The selected data will be cached in this app session.")
        return

    # Contract filters operate on the downloaded data, not on an assumed contract list.
    expiries = sorted(data["expiry"].dropna().unique().tolist())
    f1, f2, f3 = st.columns(3)
    with f1:
        expiry_choice = st.selectbox("Expiry filter", ["All expiries"] + [str(x) for x in expiries], key="hf_nql_expiry_filter")
    with f2:
        side_choice = st.selectbox("Option side", ["Both CE & PE", "CE only", "PE only"], key="hf_nql_side_filter")
    with f3:
        strike_values = sorted(data["strike"].dropna().unique().tolist())
        strike_choice = st.selectbox("Strike filter", ["All strikes", "Manual strikes"], key="hf_nql_strike_mode")

    filtered = data.copy()
    if expiry_choice != "All expiries":
        filtered = filtered[filtered["expiry"].astype(str) == expiry_choice]
    if side_choice == "CE only":
        filtered = filtered[filtered["option_type"] == "CE"]
    elif side_choice == "PE only":
        filtered = filtered[filtered["option_type"] == "PE"]
    if strike_choice == "Manual strikes":
        choices = st.multiselect("Select strikes", strike_values, key="hf_nql_manual_strikes")
        if choices:
            filtered = filtered[filtered["strike"].isin(choices)]
        else:
            filtered = filtered.iloc[0:0]

    st.write(f"**Filtered candles:** {len(filtered):,} · **Contracts:** {filtered[['expiry','strike','option_type']].drop_duplicates().shape[0]:,}")
    if not filtered.empty:
        st.dataframe(filtered.head(100), use_container_width=True, hide_index=True)
        st.download_button("Download filtered options CSV", filtered.to_csv(index=False).encode("utf-8"),
                           file_name="nifty_options_huggingface_1m.csv", mime="text/csv",
                           use_container_width=True, key="hf_nql_csv_download")
        try:
            from io import BytesIO
            buf = BytesIO()
            filtered.to_parquet(buf, index=False)
            st.download_button("Download filtered options Parquet", buf.getvalue(),
                               file_name="nifty_options_huggingface_1m.parquet", mime="application/octet-stream",
                               use_container_width=True, key="hf_nql_parquet_download")
        except Exception:
            st.caption("Parquet export unavailable in this environment; CSV export is available.")
    else:
        st.warning("No rows match the selected filters.")

# ---------------- End Hugging Face historical NIFTY options data ----------------

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
    strategy_options = (["NIFTY PDHL", "NIFTY Stoch RSI V5", "NIFTY EMA 9/20 ATR Partial"] if selected_market == "NIFTY 50"
                        else ["BTC EMA + Stoch RSI", "BTC PDHL"])
    selected_strategy = st.selectbox("2. Select strategy", strategy_options, key="v30_strategy")
st.caption("Choose the market and strategy here. Each strategy retains its existing data controls and parameters below.")

# Shared data access is independent of the selected market/strategy. Historical
# NIFTY downloads remain available even when another strategy is selected.
with st.expander("Market Data Center — Kite Connect / historical downloads", expanded=False):
    render_kite_data_center()
# ---------------------------------------------------------------------------

if selected_market == "BTC" and selected_strategy == "BTC EMA + Stoch RSI":
    render_btc_quant_module()
elif selected_market == "BTC" and selected_strategy == "BTC PDHL":
    render_btc_pdhl_module()


if selected_market == "NIFTY 50" and selected_strategy == "NIFTY PDHL":
    with st.expander("Saved Backtests — Google Drive archive", expanded=False):
        st.caption("Open a previous PDHL run with its original candles, trade drawings, full statistics, monthly reports and downloads.")
        if st.button("Refresh saved backtests", key="refresh_saved_pdhl_runs"):
            st.session_state.pop("saved_pdhl_runs_cache", None)
            st.session_state.pop("selected_saved_pdhl_run", None)
        service, drive_error = _google_drive_service()
        if service is None:
            st.warning("Google Drive is not connected: " + str(drive_error))
        else:
            try:
                root_id = str(st.secrets["google_drive"]["folder_id"]).strip()
                pdhl_id = _drive_folder_id(service, root_id, "PDHL")
                runs = _drive_list_pdhl_runs(service, pdhl_id)
                if not runs:
                    st.info("No archived PDHL runs were found in the configured Google Drive folder.")
                else:
                    run_labels = [f"{r['name']} • {r.get('modifiedTime', '')}" for r in runs]
                    selected_label = st.selectbox("Select saved backtest", run_labels, key="saved_pdhl_run_select")
                    selected_run = runs[run_labels.index(selected_label)]
                    if st.button("Load selected backtest", type="primary", use_container_width=True,
                                 key="load_saved_pdhl_run"):
                        with st.spinner("Fetching saved trades, signals and report data from Google Drive…"):
                            loaded = _load_pdhl_run_from_drive(service, selected_run)
                            st.session_state["selected_saved_pdhl_run"] = loaded
                    loaded = st.session_state.get("selected_saved_pdhl_run")
                    if loaded and loaded.get("run_id") == selected_run["name"]:
                        st.divider()
                        render_saved_backtest_report(loaded, key_prefix="saved_pdhl")
            except Exception as archive_error:
                st.error(f"Could not retrieve saved backtests: {type(archive_error).__name__}: {archive_error}")

    api_key_live, _ = kite_credentials()

    st.header("PDHL Strategy Settings")
    st.caption("All key backtest variables are editable. Defaults preserve the existing PDHL setup: body-based PDH/PDL, one trade per day, 50-point momentum, 10-point SL buffer and 2R target.")
    with st.expander("Backtest date range", expanded=True):
        d1,d2=st.columns(2)
        with d1: bt_start=st.date_input("Start date", value=date(2025,1,1), key="pdhl_bt_start")
        with d2: bt_end=st.date_input("End date", value=date.today(), key="pdhl_bt_end")
    with st.expander("Entry and signal settings", expanded=True):
        c1,c2,c3=st.columns(3)
        with c1: max_trades=st.number_input("Maximum trades per day",1,10,int(st.session_state.get("pdhl_max_trades",1)),1,key="pdhl_max_trades")
        with c2: rr=st.number_input("Reward-to-risk (R)",0.5,10.0,float(st.session_state.get("rr",2.0)),0.25,key="rr")
        with c3: entry_mode=st.selectbox("Entry execution",["Next candle open","Signal candle close"],index=0,key="pdhl_entry_mode")
        min_entry_body=st.number_input("Minimum entry candle body (points)",0.0,300.0,float(st.session_state.get("min_entry_body",20.0)),1.0,key="min_entry_body")
        min_momentum_body=st.number_input("Momentum candle body threshold (points)",0.0,500.0,float(st.session_state.get("min_momentum_body",50.0)),1.0,key="min_momentum_body")
        breakout_confirm_candles=st.number_input("Breakout confirmation candles",1,5,int(st.session_state.get("breakout_confirm_candles_v2",1)),1,key="breakout_confirm_candles_v2")
        level_mode=st.selectbox("PDH / PDL calculation",["Previous session body high/low","Previous session wick high/low"],index=0,key="pdhl_level_mode",help="Body mode preserves the existing baseline. Wick mode uses the prior session's actual high and low.")
    with st.expander("Stop-loss and profit management", expanded=True):
        s1,s2,s3=st.columns(3)
        with s1: sl_buffer=st.number_input("SL buffer (points)",0.0,200.0,float(st.session_state.get("sl_buffer",10.0)),1.0,key="sl_buffer")
        with s2: stop_loss_mode=st.selectbox("Stop-loss method",["Candle low/high","50-point cap","Adaptive SL (15–55 point filter)"],index=0,key="stop_loss_mode")
        with s3: profit_booking_mode=st.selectbox("Profit booking",["Fixed target","50% at 1R + step trailing"],index=0,key="profit_booking_mode")
        a1,a2,a3,a4=st.columns(4)
        with a1: min_sl_points=st.number_input("Minimum accepted SL",1.0,200.0,float(st.session_state.get("min_sl_points",15.0)),1.0,key="min_sl_points")
        with a2: max_sl_points=st.number_input("Maximum accepted SL",1.0,300.0,float(st.session_state.get("max_sl_points",55.0)),1.0,key="max_sl_points")
        with a3: adaptive_sl_threshold=st.number_input("Adaptive extreme-distance threshold",1.0,300.0,float(st.session_state.get("adaptive_sl_threshold",55.0)),1.0,key="adaptive_sl_threshold")
        with a4: adaptive_sl_range_fraction=st.number_input("Full-range fraction",0.05,1.0,float(st.session_state.get("adaptive_sl_range_fraction",0.50)),0.05,key="adaptive_sl_range_fraction")
        if min_sl_points > max_sl_points: st.error("Minimum accepted SL cannot exceed maximum accepted SL.")
    with st.expander("Gap and session filters", expanded=False):
        g1,g2=st.columns(2)
        with g1: gap_threshold=st.number_input("Large-gap threshold (points)",0.0,500.0,float(st.session_state.get("gap_threshold",50.0)),1.0,key="gap_threshold")
        with g2: cutoff=st.selectbox("Large-gap reversal cutoff",["09:30","10:00","10:30","11:00","11:30","12:00","12:30","13:00","13:30","14:00","15:00"],index=3,key="gap_cutoff")
    if bt_start > bt_end: st.error("Start date must be on or before end date.")
    live_params={"entry_body":float(min_entry_body),"momentum_body":float(min_momentum_body),"breakout_confirm_candles":int(breakout_confirm_candles),"sl_buffer":float(sl_buffer),"stop_loss_mode":str(stop_loss_mode),"rr":float(rr),"gap":float(gap_threshold),"cutoff":str(cutoff)}
    render_live_signal_center(api_key_live, st.session_state.get("kite_access_token"), live_params)
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
            data = prepare_spot(data)
            data = data[(data["trade_date"] >= bt_start) & (data["trade_date"] <= bt_end)].copy().reset_index(drop=True)
            if data.empty:
                st.warning("No candles found inside the selected date range.")
            else:
                st.caption(f"Selected data: {data.datetime.min()} to {data.datetime.max()} • {len(data):,} candles")
            st.subheader("Automatic parameter research")
            st.caption("Fast staged search: 160 balanced candidates, cached PDHL signals, top 25 validation finalists, then one untouched final test. It searches entry body, momentum body, confirmation count, SL buffer/method, target R:R, profit booking, gap threshold and cutoff. Requires 250+ sessions for a meaningful 60/20/20 split.")
            optimizer_min_trades=st.number_input("Minimum trades required per candidate", min_value=10, max_value=500, value=50, step=10, key="optimizer_min_trades")
            # Render the normal backtest control before optimizer fingerprint/checkpoint work.
            run_pdhl_requested = st.button("Run PDHL backtest", type="primary", use_container_width=True)
            with st.spinner("Preparing dataset fingerprint…"):
                fp=_data_fingerprint(data)
            job_id="nql_fast2_"+fp[:24]
            remote_ok=_remote_checkpoint_enabled()
            if remote_ok:
                st.info("Supabase checkpoint credentials are configured. Remote bucket access is verified when the first checkpoint is saved.")
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
                    bcols=st.columns(4)
                    for col,(label,key,suffix) in zip(bcols,[("Entry body","body"," pts"),("Momentum body","momentum_body"," pts"),("Confirm candles","breakout_confirm_candles",""),("SL buffer","sl"," pts")]): col.metric(label,f"{best[key]}{suffix}")
                    bcols2=st.columns(4)
                    for col,(label,key,suffix) in zip(bcols2,[("Target","rr","R"),("Gap threshold","gap"," pts"),("Entry cutoff","cutoff",""),("SL method","stop_loss_mode","")]): col.metric(label,f"{best[key]}{suffix}")
                    st.caption("Profit booking: " + str(best["profit_booking_mode"]))
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

            if run_pdhl_requested:
                bt_progress=st.progress(0, text="Backtest progress: 0%")
                bt_status=st.empty()
                def backtest_progress(fraction, message):
                    pct=int(max(0,min(100,round(fraction*100))))
                    bt_progress.progress(pct, text=f"Backtest progress: {pct}%")
                    bt_status.caption(message)
                trades,equity,signals=run_backtest(data,int(max_trades),str(entry_mode),float(sl_buffer),float(rr),float(min_entry_body),float(gap_threshold),str(cutoff),progress_callback=backtest_progress,min_momentum_body=float(min_momentum_body),breakout_confirm_candles=int(breakout_confirm_candles),profit_booking_mode=str(profit_booking_mode),stop_loss_mode=str(stop_loss_mode),min_sl_points=float(min_sl_points),max_sl_points=float(max_sl_points),adaptive_sl_threshold=float(adaptive_sl_threshold),adaptive_sl_range_fraction=float(adaptive_sl_range_fraction),level_mode=str(level_mode))
                bt_progress.progress(100, text="Backtest progress: 100% — complete")
                bt_status.success("Backtest finished. Preparing performance report…")
                if trades.empty:
                    st.warning("No trades generated. The zero-trade run will still be archived with its settings and signal data.")
                    created_at = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
                    params = {
                        "Market": "NIFTY 50", "Strategy": "NIFTY PDHL",
                        "Backtest start date": str(bt_start), "Backtest end date": str(bt_end),
                        "Maximum trades per day": int(max_trades), "Entry execution": str(entry_mode),
                        "PDH/PDL level mode": str(level_mode), "Reward-to-risk": float(rr),
                        "Stop-loss buffer points": float(sl_buffer), "Stop-loss method": str(stop_loss_mode),
                        "Minimum accepted SL points": float(min_sl_points), "Maximum accepted SL points": float(max_sl_points),
                        "Adaptive SL extreme-distance threshold": float(adaptive_sl_threshold),
                        "Adaptive SL full-range fraction": float(adaptive_sl_range_fraction),
                        "Minimum entry body points": float(min_entry_body),
                        "Minimum momentum body points": float(min_momentum_body),
                        "Breakout confirmation candles": int(breakout_confirm_candles),
                        "Profit booking method": str(profit_booking_mode),
                        "Large-gap threshold points": float(gap_threshold),
                        "Large-gap reversal cutoff": str(cutoff),
                        "Data fingerprint": fp
                    }
                    drive_ok, drive_message = archive_pdhl_backtest_to_drive(trades, signals, params, {}, created_at)
                    if drive_ok: st.success("Zero-trade run archived to Google Drive: " + drive_message)
                    else: st.warning("Google Drive archive did not complete: " + drive_message)
                    st.session_state.pop("nql_backtest_results", None)
                else:
                    # Persist results across Streamlit reruns, including CSV download clicks.
                    created_at = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
                    st.session_state["nql_backtest_results"] = {
                        "trades": trades.copy(), "signals": signals.copy(),
                        "data_fingerprint": fp, "created_at": created_at
                    }
                    params = {
                        "Market": "NIFTY 50", "Strategy": "NIFTY PDHL",
                        "Backtest start date": str(bt_start), "Backtest end date": str(bt_end),
                        "Maximum trades per day": int(max_trades), "Entry execution": str(entry_mode),
                        "PDH/PDL level mode": str(level_mode),
                        "Reward-to-risk": float(rr), "Stop-loss buffer points": float(sl_buffer), "Stop-loss method": str(stop_loss_mode),
                        "Minimum accepted SL points": float(min_sl_points), "Maximum accepted SL points": float(max_sl_points),
                        "Adaptive SL extreme-distance threshold": float(adaptive_sl_threshold),
                        "Adaptive SL full-range fraction": float(adaptive_sl_range_fraction),
                        "Minimum entry body points": float(min_entry_body),
                        "Minimum momentum body points": float(min_momentum_body),
                        "Breakout confirmation candles": int(breakout_confirm_candles),
                        "Profit booking method": str(profit_booking_mode),
                        "Profit booking details": ("50% at 1R; remaining 50% step-trailing (1R=BE, 2R=1R lock, 3R=2R lock)" if str(profit_booking_mode).startswith("50% at 1R") else f"100% exit at {float(rr):g}R fixed target"),
                        "Large-gap threshold points": float(gap_threshold),
                        "Large-gap reversal cutoff": str(cutoff),
                        "Data fingerprint": fp
                    }
                    drive_ok, drive_message = archive_pdhl_backtest_to_drive(trades, signals, params, stats_for(trades), created_at)
                    st.session_state["nql_backtest_results"]["drive_archived"] = bool(drive_ok)
                    st.session_state["nql_backtest_results"]["drive_message"] = drive_message
                    st.success(f"Backtest complete: {len(trades):,} trades")
                    if drive_ok:
                        st.success("Google Drive archive: " + drive_message)
                    else:
                        st.warning("Backtest is available in this session, but Drive archive did not complete. " + drive_message)

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
                    if saved_bt.get("drive_message"):
                        if saved_bt.get("drive_archived"):
                            st.caption("Google Drive: " + saved_bt["drive_message"])
                        else:
                            st.warning("Google Drive archive status: " + saved_bt["drive_message"])
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
    - Profit booking: 50% at +1R; remaining 50% trails by steps (+1R = breakeven, +2R = +1R locked, +3R = +2R locked). Trailing updates apply from the next candle.
    - Remaining open trades exit at the final candle close of that session.
    - All performance is in Nifty spot points, not options P&L or rupees. No brokerage/charges are applied.
    """)


# ---------------- NIFTY Stochastic RSI V5 research module ----------------
def _nql_normalize_nifty_ohlc(frame):
    """Normalize uploaded/Kite NIFTY spot candles without changing price values."""
    d = frame.copy()
    d.columns = [str(c).strip().lower() for c in d.columns]
    if "datetime" not in d.columns:
        for candidate in ("date", "timestamp", "time"):
            if candidate in d.columns:
                d = d.rename(columns={candidate: "datetime"})
                break
    required = ["datetime", "open", "high", "low", "close"]
    missing = [c for c in required if c not in d.columns]
    if missing:
        raise ValueError("CSV must contain datetime/date, open, high, low and close columns. Missing: " + ", ".join(missing))
    parsed = pd.to_datetime(d["datetime"], errors="coerce")
    if parsed.isna().any():
        bad = int(parsed.isna().sum())
        raise ValueError(f"{bad} rows have invalid timestamps.")
    if parsed.dt.tz is None:
        parsed = parsed.dt.tz_localize("Asia/Kolkata")
    else:
        parsed = parsed.dt.tz_convert("Asia/Kolkata")
    d["datetime"] = parsed
    for c in ["open", "high", "low", "close"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=required).sort_values("datetime").drop_duplicates("datetime", keep="last").reset_index(drop=True)
    if d.empty:
        raise ValueError("No valid OHLC candles remain after cleaning.")
    if (d[["high", "low", "open", "close"]] <= 0).any().any():
        raise ValueError("OHLC prices must be positive.")
    if (d["high"] < d[["open", "close", "low"]].max(axis=1)).any():
        raise ValueError("Invalid OHLC rows found: high is below open, close or low.")
    if (d["low"] > d[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("Invalid OHLC rows found: low is above open, close or high.")
    d["session_date"] = d["datetime"].dt.date
    return d


def _nql_wilder(series, period):
    return series.ewm(alpha=1.0 / float(period), adjust=False, min_periods=int(period)).mean()


def _nql_add_stoch_indicators(frame, rsi_period=14, stoch_period=14, k_smooth=3, d_smooth=3,
                              ema_period=20, adx_period=14, atr_period=14):
    d = frame.copy()
    close, high, low = d["close"], d["high"], d["low"]

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = _nql_wilder(gain, rsi_period)
    avg_loss = _nql_wilder(loss, rsi_period)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.where(avg_loss.ne(0), 100.0)
    rsi = rsi.where(~(avg_gain.eq(0) & avg_loss.eq(0)), 50.0)

    rsi_min = rsi.rolling(stoch_period, min_periods=stoch_period).min()
    rsi_max = rsi.rolling(stoch_period, min_periods=stoch_period).max()
    stoch_raw = 100.0 * (rsi - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan)
    d["rsi"] = rsi
    d["stoch_raw"] = stoch_raw
    d["stoch_k"] = stoch_raw.rolling(k_smooth, min_periods=k_smooth).mean()
    d["stoch_d"] = d["stoch_k"].rolling(d_smooth, min_periods=d_smooth).mean()
    d["ema"] = close.ewm(span=int(ema_period), adjust=False, min_periods=int(ema_period)).mean()

    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    d["atr"] = _nql_wilder(tr, atr_period)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=d.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=d.index)
    plus_di = 100.0 * _nql_wilder(plus_dm, adx_period) / d["atr"].replace(0, np.nan)
    minus_di = 100.0 * _nql_wilder(minus_dm, adx_period) / d["atr"].replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    d["plus_di"] = plus_di
    d["minus_di"] = minus_di
    d["adx"] = _nql_wilder(dx, adx_period)
    return d


def nql_stoch_rsi_v5_backtest(frame, params):
    """
    NIFTY spot Stoch RSI V5.
    Signal is confirmed at candle close; entry is next same-session candle open.
    Initial risk is fixed from signal-candle ATR; trailing stop updates after each
    completed candle and becomes active from the next candle.
    """
    d = _nql_normalize_nifty_ohlc(frame)
    d = _nql_add_stoch_indicators(
        d, params["rsi_period"], params["stoch_period"], params["k_smooth"], params["d_smooth"],
        params["ema_period"], params["adx_period"], params["atr_period"]
    )
    d["signal"] = ""
    d["signal_reason"] = ""
    d["atr_percentile"] = np.nan

    # V5 as discussed: EMA20, ADX14 >= 18, long K < 55, short K > 50.
    # The ATR percentile filter is intentionally OFF by default because it was not
    # part of the final V5 parameter set; it can be enabled explicitly for comparison.
    atr_pct_lookback = int(params.get("atr_pct_lookback", 100))
    atr_pct = (d["atr"] / d["close"]).rolling(atr_pct_lookback, min_periods=atr_pct_lookback).rank(pct=True)
    # Current ATR/close is ranked against its trailing window, all known at signal close.
    d["atr_percentile"] = atr_pct * 100.0

    k_prev, d_prev = d["stoch_k"].shift(1), d["stoch_d"].shift(1)
    cross_up = (k_prev <= d_prev) & (d["stoch_k"] > d["stoch_d"])
    cross_down = (k_prev >= d_prev) & (d["stoch_k"] < d["stoch_d"])
    signal_start = params["start_time"]
    signal_end = params["end_time"]
    hhmm = d["datetime"].dt.strftime("%H:%M")
    in_window = (hhmm >= signal_start) & (hhmm <= signal_end)
    long_ok = (
        cross_up & (d["close"] > d["ema"]) & (d["stoch_k"] < float(params["long_threshold"]))
        & (d["adx"] >= float(params["adx_min"]))
    )
    short_ok = (
        cross_down & (d["close"] < d["ema"]) & (d["stoch_k"] > float(params["short_threshold"]))
        & (d["adx"] >= float(params["adx_min"]))
    )
    if params.get("use_atr_percentile_filter", False):
        lo_pct, hi_pct = float(params["atr_pct_min"]), float(params["atr_pct_max"])
        vol_ok = d["atr_percentile"].between(lo_pct, hi_pct, inclusive="both")
        long_ok &= vol_ok
        short_ok &= vol_ok
    long_ok &= in_window
    short_ok &= in_window
    d.loc[long_ok.fillna(False), "signal"] = "LONG"
    d.loc[short_ok.fillna(False), "signal"] = "SHORT"
    d.loc[long_ok.fillna(False), "signal_reason"] = "K crossed above D; close > EMA; K below long threshold; ADX filter passed"
    d.loc[short_ok.fillna(False), "signal_reason"] = "K crossed below D; close < EMA; K above short threshold; ADX filter passed"

    trades = []
    used_sessions = set()
    dates = d["session_date"].to_numpy()
    for i in range(len(d) - 1):
        side = str(d.at[i, "signal"])
        if side not in ("LONG", "SHORT"):
            continue
        session = dates[i]
        if session in used_sessions or dates[i + 1] != session:
            continue
        signal_atr = float(d.at[i, "atr"]) if pd.notna(d.at[i, "atr"]) else np.nan
        if not np.isfinite(signal_atr) or signal_atr <= 0:
            continue
        entry_time = d.at[i + 1, "datetime"]
        entry_time_str = entry_time.strftime("%H:%M")
        if entry_time_str > params.get("last_entry_time", "13:05"):
            continue
        entry = float(d.at[i + 1, "open"])
        risk_points = float(params["initial_sl_atr"]) * signal_atr
        if not np.isfinite(risk_points) or risk_points <= 0:
            continue
        direction = 1.0 if side == "LONG" else -1.0
        initial_stop = entry - direction * risk_points
        active_stop = initial_stop
        best_close = entry
        entry_index = i + 1
        exit_index = None
        exit_price = None
        exit_reason = ""
        exit_time = None

        for j in range(entry_index, len(d)):
            if dates[j] != session:
                break
            bar = d.iloc[j]
            op, hi, lo, cl = float(bar.open), float(bar.high), float(bar.low), float(bar.close)

            # Stop active before this candle. If price gaps through it, use the open.
            if side == "LONG":
                if op <= active_stop:
                    exit_price, exit_reason = op, "Gap through stop"
                elif lo <= active_stop:
                    exit_price, exit_reason = active_stop, "Stop loss / trailing stop"
            else:
                if op >= active_stop:
                    exit_price, exit_reason = op, "Gap through stop"
                elif hi >= active_stop:
                    exit_price, exit_reason = active_stop, "Stop loss / trailing stop"

            if exit_reason:
                exit_index, exit_time = j, bar.datetime
                break

            best_close = max(best_close, cl) if side == "LONG" else min(best_close, cl)
            current_atr = float(bar.atr) if pd.notna(bar.atr) else signal_atr
            trail_distance = float(params["trailing_sl_atr"]) * current_atr
            if side == "LONG":
                candidate = best_close - trail_distance
                active_stop = max(active_stop, candidate)
            else:
                candidate = best_close + trail_distance
                active_stop = min(active_stop, candidate)

            # Force-close at the final available candle of this session.
            if j == len(d) - 1 or dates[j + 1] != session:
                exit_price, exit_reason = cl, "Session close"
                exit_index, exit_time = j, bar.datetime
                break

        if exit_index is None:
            # Defensive fallback if malformed/incomplete session boundaries occur.
            same_day = np.flatnonzero(dates == session)
            exit_index = int(same_day[-1])
            exit_time = d.at[exit_index, "datetime"]
            exit_price = float(d.at[exit_index, "close"])
            exit_reason = "Session close"

        pnl_points = direction * (float(exit_price) - entry)
        r_multiple = pnl_points / risk_points
        exit_bar = d.iloc[exit_index]
        trades.append({
            "trade_date": pd.Timestamp(session),
            "direction": side,
            "setup": "Stoch RSI V5",
            "signal_time": d.at[i, "datetime"],
            "entry_time": entry_time,
            "exit_time": exit_time,
            "entry": entry,
            "stop": initial_stop,
            "active_stop_at_exit": active_stop,
            "target": np.nan,
            "exit": float(exit_price),
            "exit_reason": exit_reason,
            "initial_risk_points": risk_points,
            "pnl_points": pnl_points,
            "R_multiple": r_multiple,
            "signal_atr": signal_atr,
            "signal_stoch_k": float(d.at[i, "stoch_k"]),
            "signal_stoch_d": float(d.at[i, "stoch_d"]),
            "signal_adx": float(d.at[i, "adx"]),
            "signal_ema": float(d.at[i, "ema"]),
        })
        used_sessions.add(session)

    return d, pd.DataFrame(trades)


def _nql_stoch_stats(trades):
    if trades is None or trades.empty:
        return {}
    r = pd.to_numeric(trades["R_multiple"], errors="coerce").dropna()
    wins, losses = r[r > 0], r[r < 0]
    equity = r.cumsum()
    peak = equity.cummax().clip(lower=0)
    dd = equity - peak
    loss_streak = max_streak = 0
    for value in r:
        if value < 0:
            loss_streak += 1
            max_streak = max(max_streak, loss_streak)
        else:
            loss_streak = 0
    gross_loss = abs(float(losses.sum()))
    return {
        "Trades": int(len(r)),
        "Wins": int((r > 0).sum()),
        "Losses": int((r < 0).sum()),
        "Win rate %": float((r > 0).mean() * 100),
        "Net R": float(r.sum()),
        "Profit factor": float(wins.sum() / gross_loss) if gross_loss else np.inf,
        "Expectancy (R)": float(r.mean()),
        "Average win (R)": float(wins.mean()) if len(wins) else 0.0,
        "Average loss (R)": float(losses.mean()) if len(losses) else 0.0,
        "Largest win (R)": float(r.max()),
        "Largest loss (R)": float(r.min()),
        "Max drawdown (R)": float(abs(dd.min())) if len(dd) else 0.0,
        "Max consecutive losses": int(max_streak),
    }


def _nql_stoch_period_table(trades, period):
    if trades.empty:
        return pd.DataFrame()
    t = trades.copy()
    t["trade_date"] = pd.to_datetime(t["trade_date"], errors="coerce")
    t = t.dropna(subset=["trade_date"])
    t["Year"] = t["trade_date"].dt.year
    if period == "year":
        groups = t.groupby("Year", sort=True)
        rows = [{"Year": int(y), **_nql_stoch_stats(g)} for y, g in groups]
    else:
        t["Month"] = t["trade_date"].dt.to_period("M").astype(str)
        groups = t.groupby("Month", sort=True)
        rows = [{"Month": m, **_nql_stoch_stats(g)} for m, g in groups]
    return pd.DataFrame(rows)


def render_nifty_stoch_v5_module():
    st.header("NIFTY 50 — Stochastic RSI V5")
    st.caption("Spot/index backtest only. All performance is measured in R; this module does not model option premiums or charges.")
    source = st.radio("Data source", ["Use downloaded Kite candles", "Upload Nifty spot CSV"],
                      horizontal=True, key="stoch_v5_data_source")
    data = None
    if source == "Use downloaded Kite candles":
        if "kite_nifty_candles" in st.session_state:
            data = st.session_state["kite_nifty_candles"].copy()
            st.success(f"Using {len(data):,} downloaded candles.")
        else:
            st.info("Download 5-minute NIFTY spot candles in the Kite Connect data section, or select CSV upload.")
    else:
        uploaded = st.file_uploader("Upload NIFTY spot 5-minute CSV", type=["csv"], key="stoch_v5_csv")
        if uploaded is not None:
            try:
                data = pd.read_csv(uploaded)
                st.success(f"Loaded {len(data):,} rows.")
            except Exception as exc:
                st.error(f"CSV read error: {exc}")

    st.subheader("Strategy parameters")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        rsi_period = st.number_input("RSI period", 2, 50, 14, 1, key="stoch_v5_rsi")
        stoch_period = st.number_input("Stochastic period", 2, 50, 14, 1, key="stoch_v5_stoch")
    with c2:
        k_smooth = st.number_input("%K smoothing", 1, 10, 3, 1, key="stoch_v5_k")
        d_smooth = st.number_input("%D smoothing", 1, 10, 3, 1, key="stoch_v5_d")
    with c3:
        ema_period = st.number_input("EMA period", 2, 200, 20, 1, key="stoch_v5_ema")
        adx_min = st.number_input("Minimum ADX", 0.0, 60.0, 18.0, 0.5, key="stoch_v5_adxmin")
    with c4:
        initial_sl_atr = st.number_input("Initial SL (ATR)", 0.25, 6.0, 2.0, 0.25, key="stoch_v5_isl")
        trailing_sl_atr = st.number_input("Trailing SL (ATR)", 0.5, 10.0, 3.0, 0.25, key="stoch_v5_tsl")
    c5, c6, c7, c8 = st.columns(4)
    with c5:
        long_threshold = st.number_input("Long K threshold (<)", 0.0, 100.0, 55.0, 1.0, key="stoch_v5_long")
    with c6:
        short_threshold = st.number_input("Short K threshold (>)", 0.0, 100.0, 50.0, 1.0, key="stoch_v5_short")
    with c7:
        start_time = st.text_input("Signal start (HH:MM)", "10:15", key="stoch_v5_start")
        end_time = st.text_input("Signal end (HH:MM)", "13:00", key="stoch_v5_end")
    with c8:
        last_entry_time = st.text_input("Latest entry time (HH:MM)", "13:05", key="stoch_v5_lastentry")

    use_atr_filter = st.checkbox("Enable ATR percentile filter (optional comparison)", value=False, key="stoch_v5_use_atr_pct")
    atrf1, atrf2, atrf3 = st.columns(3)
    with atrf1:
        atr_pct_lookback = st.number_input("ATR percentile lookback", 20, 500, 100, 10, key="stoch_v5_pct_lb")
    with atrf2:
        atr_pct_min = st.number_input("ATR percentile minimum", 0.0, 100.0, 20.0, 5.0, key="stoch_v5_pct_min")
    with atrf3:
        atr_pct_max = st.number_input("ATR percentile maximum", 0.0, 100.0, 80.0, 5.0, key="stoch_v5_pct_max")

    params = {
        "rsi_period": int(rsi_period), "stoch_period": int(stoch_period),
        "k_smooth": int(k_smooth), "d_smooth": int(d_smooth),
        "ema_period": int(ema_period), "adx_period": 14, "adx_min": float(adx_min),
        "atr_period": 14, "initial_sl_atr": float(initial_sl_atr),
        "trailing_sl_atr": float(trailing_sl_atr), "long_threshold": float(long_threshold),
        "short_threshold": float(short_threshold), "start_time": start_time.strip(),
        "end_time": end_time.strip(), "last_entry_time": last_entry_time.strip(),
        "use_atr_percentile_filter": bool(use_atr_filter), "atr_pct_lookback": int(atr_pct_lookback),
        "atr_pct_min": float(atr_pct_min), "atr_pct_max": float(atr_pct_max),
    }
    run = st.button("Run Stochastic RSI V5 backtest", type="primary", use_container_width=True,
                    key="run_nifty_stoch_v5")
    if data is not None and run:
        try:
            with st.spinner("Calculating indicators and replaying trades…"):
                indicator_data, trades = nql_stoch_rsi_v5_backtest(data, params)
            st.session_state["nql_stoch_v5_result"] = {
                "trades": trades, "indicators": indicator_data,
                "params": params, "data_fingerprint": _data_fingerprint(data),
            }
        except Exception as exc:
            st.exception(exc)

    saved = st.session_state.get("nql_stoch_v5_result")
    if saved:
        current_fp = _data_fingerprint(data) if data is not None else None
        if current_fp and current_fp != saved.get("data_fingerprint"):
            st.warning("The uploaded/downloaded dataset changed. Run the backtest again to refresh these results.")
        else:
            trades = saved["trades"].copy()
            indicators = saved["indicators"].copy()
            stats = _nql_stoch_stats(trades)
            if trades.empty:
                st.warning("No trades were generated. Check the date range, candle interval and signal conditions.")
            else:
                st.subheader("Backtest performance — R multiples")
                m = st.columns(4)
                m[0].metric("Net profit", f"{stats['Net R']:+.2f}R")
                m[1].metric("Profit factor", f"{stats['Profit factor']:.2f}")
                m[2].metric("Win rate", f"{stats['Win rate %']:.2f}%")
                m[3].metric("Maximum drawdown", f"{stats['Max drawdown (R)']:.2f}R")
                m2 = st.columns(4)
                m2[0].metric("Trades", f"{stats['Trades']:,}")
                m2[1].metric("Expectancy", f"{stats['Expectancy (R)']:+.3f}R/trade")
                m2[2].metric("Average win", f"{stats['Average win (R)']:+.3f}R")
                m2[3].metric("Average loss", f"{stats['Average loss (R)']:+.3f}R")
                st.dataframe(pd.DataFrame([stats]), use_container_width=True, hide_index=True)

                st.subheader("Yearly performance")
                yearly = _nql_stoch_period_table(trades, "year")
                st.dataframe(yearly, use_container_width=True, hide_index=True)
                if not yearly.empty:
                    st.bar_chart(yearly.set_index("Year")["Net R"])
                st.subheader("Monthly performance")
                monthly = _nql_stoch_period_table(trades, "month")
                st.dataframe(monthly, use_container_width=True, hide_index=True)
                st.subheader("Trade chart")
                render_tradingview_chart(indicators, trades=trades, chart_key="nifty_stoch_v5_chart")
                st.subheader("Trade log")
                st.dataframe(trades, use_container_width=True, hide_index=True)
                st.download_button("Download Stoch RSI V5 trades CSV",
                                   trades.to_csv(index=False).encode("utf-8"),
                                   "nql_stoch_rsi_v5_trades.csv", "text/csv", key="stoch_v5_dl_trades")
                st.download_button("Download Stoch RSI V5 indicator/signal audit CSV",
                                   indicators.to_csv(index=False).encode("utf-8"),
                                   "nql_stoch_rsi_v5_signal_audit.csv", "text/csv", key="stoch_v5_dl_signals")
                st.download_button("Download Stoch RSI V5 yearly CSV",
                                   yearly.to_csv(index=False).encode("utf-8"),
                                   "nql_stoch_rsi_v5_yearly.csv", "text/csv", key="stoch_v5_dl_yearly")

    with st.expander("V5 exact execution rules", expanded=False):
        st.markdown("""
        - Long: completed candle closes above EMA 20; %K crosses above %D; %K is below 55; ADX(14) is at least 18.
        - Short: completed candle closes below EMA 20; %K crosses below %D; %K is above 50; ADX(14) is at least 18.
        - Stochastic RSI: RSI(14), stochastic lookback 14, %K smoothing 3, %D smoothing 3.
        - Signal candle must close between 10:15 and 13:00 IST; entry is next same-session 5-minute candle open and must be no later than 13:05.
        - Initial stop distance is 2 × ATR(14) measured on the signal candle; this distance defines 1R.
        - No fixed target. After each candle closes, trail from best close since entry by 3 × current ATR(14); the new stop applies from the next candle.
        - A stop already active before a candle is checked against that candle. If the candle opens beyond the stop, exit at its open; otherwise exit at the stop price if its range touches it.
        - Maximum one trade per session. Any open position exits at the final candle close of that session.
        - Indicator calculations carry across sessions; no future candles are used for signals or stop updates.
        - Spot/index points only. No options premium, brokerage, taxes or slippage are modelled.
        """)



# ---------------- NIFTY EMA 9/20 ATR Partial strategy ----------------
def nql_ema920_backtest(frame, params):
    """NIFTY spot EMA 9/20 crossover. Signal close -> next bar open entry.
    25% is booked at +1R; remaining 75% retains original stop and exits on
    opposite EMA cross close or session close. Stops are checked before targets.
    """
    d = _nql_normalize_nifty_ohlc(frame)
    d["ema9"] = d["close"].ewm(span=int(params["fast_ema"]), adjust=False, min_periods=int(params["fast_ema"])).mean()
    d["ema20"] = d["close"].ewm(span=int(params["slow_ema"]), adjust=False, min_periods=int(params["slow_ema"])).mean()
    prev_fast, prev_slow = d["ema9"].shift(1), d["ema20"].shift(1)
    d["cross_up"] = (prev_fast <= prev_slow) & (d["ema9"] > d["ema20"])
    d["cross_down"] = (prev_fast >= prev_slow) & (d["ema9"] < d["ema20"])
    rng = (d["high"] - d["low"]).replace(0, np.nan)
    d["body_pct"] = ((d["close"] - d["open"]).abs() / rng * 100).fillna(0)
    prev_close = d["close"].shift(1)
    tr = pd.concat([(d["high"]-d["low"]), (d["high"]-prev_close).abs(), (d["low"]-prev_close).abs()], axis=1).max(axis=1)
    d["atr14"] = _nql_wilder(tr, int(params["atr_period"]))
    hhmm = d["datetime"].dt.strftime("%H:%M")
    blocked = (hhmm >= params["blocked_start"]) & (hhmm < params["blocked_end"])
    body_ok = d["body_pct"] >= float(params["body_min_pct"])
    d["signal"] = ""
    d.loc[d["cross_up"] & body_ok & ~blocked, "signal"] = "LONG"
    d.loc[d["cross_down"] & body_ok & ~blocked, "signal"] = "SHORT"
    d["signal_reason"] = np.where(d["signal"] != "", "EMA crossover + body filter", "")

    trades=[]
    i=0
    n=len(d)
    while i < n-1:
        side=str(d.at[i,"signal"])
        if side not in ("LONG","SHORT"):
            i+=1; continue
        if d.at[i+1,"session_date"] != d.at[i,"session_date"]:
            i+=1; continue
        atr=float(d.at[i,"atr14"]) if pd.notna(d.at[i,"atr14"]) else np.nan
        if not np.isfinite(atr) or atr <= 0:
            i+=1; continue
        entry_i=i+1
        direction=1.0 if side=="LONG" else -1.0
        entry=float(d.at[entry_i,"open"])
        risk=float(params["atr_mult"])*atr
        if risk <= 0 or not np.isfinite(risk):
            i+=1; continue
        stop=entry-direction*risk
        target=entry+direction*risk
        partial=False
        partial_r=0.0
        exit_i=None; exit_px=None; reason=""
        session=d.at[i,"session_date"]
        for j in range(entry_i,n):
            if d.at[j,"session_date"] != session:
                break
            op=float(d.at[j,"open"]); hi=float(d.at[j,"high"]); lo=float(d.at[j,"low"]); cl=float(d.at[j,"close"])
            # Original stop has priority over target if both occur in one candle.
            stop_hit = (lo <= stop) if direction>0 else (hi >= stop)
            if stop_hit:
                px=op if ((direction>0 and op<=stop) or (direction<0 and op>=stop)) else stop
                exit_i=j; exit_px=px; reason="PARTIAL+SL" if partial else "SL"
                break
            if not partial:
                target_hit=(hi>=target) if direction>0 else (lo<=target)
                if target_hit:
                    partial=True
                    partial_r=0.25*1.0
            # Opposite crossover is confirmed at close; remainder exits at that close.
            opposite = bool(d.at[j,"cross_down"] if direction>0 else d.at[j,"cross_up"])
            if opposite:
                exit_i=j; exit_px=cl; reason="PARTIAL+CROSS" if partial else "CROSS"
                break
            # End of session: close remaining quantity at final candle close.
            is_last = (j==n-1 or d.at[j+1,"session_date"] != session)
            if is_last:
                exit_i=j; exit_px=cl; reason="PARTIAL+EOD" if partial else "EOD"
                break
        if exit_i is None:
            # Dataset ended unexpectedly; close at last available candle.
            exit_i=n-1; exit_px=float(d.at[exit_i,"close"]); reason="DATA_END"
        remaining_r=direction*(float(exit_px)-entry)/risk
        total_r=partial_r + (0.75*remaining_r if partial else remaining_r)
        trades.append({
            "entry_time":d.at[entry_i,"datetime"], "exit_time":d.at[exit_i,"datetime"],
            "direction":side, "entry":entry, "stop":stop, "target":target,
            "exit":float(exit_px), "initial_risk_points":risk,
            "partial_booked":partial, "partial_fraction":0.25 if partial else 0.0,
            "R_multiple":float(total_r), "exit_reason":reason,
            "signal_time":d.at[i,"datetime"], "signal_body_pct":float(d.at[i,"body_pct"]),
            "signal_atr14":atr
        })
        # No overlapping positions: resume scanning after the exit candle.
        i=max(i+1,exit_i+1)
    return d, pd.DataFrame(trades)


def _nql_ema920_stats(trades):
    if trades.empty:
        return {"Trades":0,"Net R":0.0,"Expectancy (R)":0.0,"Profit factor":0.0,"Win rate %":0.0,"Max drawdown (R)":0.0,"Average win (R)":0.0,"Average loss (R)":0.0}
    r=pd.to_numeric(trades["R_multiple"],errors="coerce").fillna(0.0)
    wins=r[r>0]; losses=r[r<0]
    eq=r.cumsum(); dd=eq-eq.cummax()
    gross_loss=abs(float(losses.sum()))
    return {"Trades":int(len(r)),"Net R":float(r.sum()),"Expectancy (R)":float(r.mean()),
            "Profit factor":float(wins.sum()/gross_loss) if gross_loss else float("inf"),
            "Win rate %":float((r>0).mean()*100),"Max drawdown (R)":float(dd.min()),
            "Average win (R)":float(wins.mean()) if len(wins) else 0.0,
            "Average loss (R)":float(losses.mean()) if len(losses) else 0.0}


def render_nifty_ema920_module():
    st.header("NIFTY 50 — EMA 9/20 + ATR Partial Exit")
    st.caption("Spot/index backtest. R = directional NIFTY spot P&L divided by initial entry-to-stop distance. No options premium or charges are modelled.")
    source=st.radio("Data source",["Use downloaded Kite candles","Upload Nifty spot CSV"],horizontal=True,key="ema920_data_source")
    data=None
    if source=="Use downloaded Kite candles":
        if "kite_nifty_candles" in st.session_state:
            data=st.session_state["kite_nifty_candles"].copy(); st.success(f"Using {len(data):,} downloaded candles.")
        else: st.info("Download NIFTY spot candles in Kite Connect data integration, or choose CSV upload.")
    else:
        uploaded=st.file_uploader("Upload NIFTY spot OHLC CSV",type=["csv"],key="ema920_csv")
        if uploaded is not None:
            try:
                data=pd.read_csv(uploaded); st.success(f"Loaded {len(data):,} rows.")
            except Exception as exc: st.error(f"CSV read error: {exc}")
    st.subheader("Strategy parameters")
    a,b,c,dcol=st.columns(4)
    with a: fast=st.number_input("Fast EMA",2,100,9,1,key="ema920_fast")
    with b: slow=st.number_input("Slow EMA",3,200,20,1,key="ema920_slow")
    with c: body=st.number_input("Minimum signal candle body (%)",0.0,100.0,30.0,1.0,key="ema920_body")
    with dcol: atr_mult=st.number_input("Stop distance (ATR multiple)",0.1,5.0,0.5,0.05,key="ema920_atr_mult")
    e,f,g=st.columns(3)
    with e: atr_period=st.number_input("ATR period",2,100,14,1,key="ema920_atr_period")
    with f: blocked_start=st.text_input("Blocked start (IST)","13:00",key="ema920_block_start")
    with g: blocked_end=st.text_input("Blocked end (IST)","14:00",key="ema920_block_end")
    st.info("Fixed exit rules: book 25% at +1R; remaining 75% keeps original SL; exit remainder on opposite EMA crossover close or session close. No BE movement.")
    params={"fast_ema":int(fast),"slow_ema":int(slow),"body_min_pct":float(body),"atr_mult":float(atr_mult),"atr_period":int(atr_period),"blocked_start":blocked_start.strip(),"blocked_end":blocked_end.strip()}
    run=st.button("Run EMA 9/20 backtest",type="primary",use_container_width=True,key="run_nifty_ema920")
    if data is not None and run:
        try:
            with st.spinner("Calculating EMA/ATR indicators and replaying trades…"):
                indicators,trades=nql_ema920_backtest(data,params)
            st.session_state["nql_ema920_result"]={"indicators":indicators,"trades":trades,"params":params,"data_fingerprint":_data_fingerprint(data)}
        except Exception as exc: st.exception(exc)
    saved=st.session_state.get("nql_ema920_result")
    if saved:
        current_fp=_data_fingerprint(data) if data is not None else None
        if current_fp and current_fp!=saved.get("data_fingerprint"):
            st.warning("The dataset changed. Run the backtest again to refresh results.")
        else:
            trades=saved["trades"].copy(); indicators=saved["indicators"].copy(); stats=_nql_ema920_stats(trades)
            if trades.empty: st.warning("No trades generated. Check your data interval and signal filters.")
            else:
                st.subheader("Backtest performance — R multiples")
                m=st.columns(4); m[0].metric("Total R",f"{stats['Net R']:+.2f}R"); m[1].metric("Expectancy",f"{stats['Expectancy (R)']:+.3f}R"); m[2].metric("Profit factor",f"{stats['Profit factor']:.2f}"); m[3].metric("Max drawdown",f"{stats['Max drawdown (R)']:.2f}R")
                m2=st.columns(4); m2[0].metric("Trades",f"{stats['Trades']:,}"); m2[1].metric("Win rate",f"{stats['Win rate %']:.2f}%"); m2[2].metric("Average win",f"{stats['Average win (R)']:+.3f}R"); m2[3].metric("Average loss",f"{stats['Average loss (R)']:+.3f}R")
                st.dataframe(pd.DataFrame([stats]),use_container_width=True,hide_index=True)
                trades["year"]=pd.to_datetime(trades["exit_time"]).dt.year
                trades["month"]=pd.to_datetime(trades["exit_time"]).dt.to_period("M").astype(str)
                st.subheader("Yearly performance")
                yearly=trades.groupby("year",as_index=False).agg(Trades=("R_multiple","size"),**{"Net R":("R_multiple","sum"),"Expectancy R":("R_multiple","mean")})
                st.dataframe(yearly,use_container_width=True,hide_index=True)
                st.subheader("Monthly performance")
                monthly=trades.groupby("month",as_index=False).agg(Trades=("R_multiple","size"),**{"Net R":("R_multiple","sum"),"Expectancy R":("R_multiple","mean")})
                st.dataframe(monthly,use_container_width=True,hide_index=True)
                st.subheader("Trade-by-trade chart explorer")
                trade_view = trades.reset_index(drop=True).copy()
                trade_view["trade_no"] = np.arange(1, len(trade_view) + 1)
                jump_key = "ema920_trade_jump"
                if jump_key not in st.session_state:
                    st.session_state[jump_key] = 1
                st.session_state[jump_key] = min(max(int(st.session_state[jump_key]), 1), len(trade_view))
                nav1, nav2, nav3, nav4 = st.columns([1, 1, 2, 2])
                with nav1:
                    if st.button("← Previous", key="ema920_prev_trade",
                                 disabled=st.session_state[jump_key] <= 1,
                                 use_container_width=True):
                        st.session_state[jump_key] -= 1
                with nav2:
                    if st.button("Next →", key="ema920_next_trade",
                                 disabled=st.session_state[jump_key] >= len(trade_view),
                                 use_container_width=True):
                        st.session_state[jump_key] += 1
                with nav3:
                    st.selectbox(
                        "Jump to trade", options=list(range(1, len(trade_view) + 1)),
                        format_func=lambda n: f"Trade {n}", key=jump_key
                    )
                selected_trade = trade_view.iloc[st.session_state[jump_key] - 1]
                with nav4:
                    st.caption(
                        f"{str(selected_trade.get('direction','')).upper()} · "
                        f"{pd.Timestamp(selected_trade['entry_time']).strftime('%d %b %Y %H:%M')} → "
                        f"{pd.Timestamp(selected_trade['exit_time']).strftime('%d %b %Y %H:%M')} · "
                        f"{float(selected_trade['R_multiple']):+.2f}R"
                    )
                st.caption("Use Previous/Next or Jump to trade. The chart opens around the selected trade's exit date; tap its trade marker/risk-reward area for details.")
                render_tradingview_chart(
                    indicators, trades=trades,
                    chart_key=f"nifty_ema920_chart_trade_{st.session_state['ema920_trade_jump']}"
                )
                st.subheader("Trade log")
                st.dataframe(trades,use_container_width=True,hide_index=True)
                st.download_button("Download EMA 9/20 trades CSV",trades.to_csv(index=False).encode("utf-8"),"nql_ema920_trades.csv","text/csv",key="ema920_dl_trades")
                st.download_button("Download EMA/ATR signal audit CSV",indicators.to_csv(index=False).encode("utf-8"),"nql_ema920_signal_audit.csv","text/csv",key="ema920_dl_signals")
    with st.expander("Exact execution rules",expanded=False):
        st.markdown("""
        - Long signal: EMA 9 crosses above EMA 20 on a completed candle; short signal is the reverse.
        - Signal candle body/range must be at least the configured percentage (default 30%).
        - No new signal is accepted from 13:00 through 13:59 IST (default blocked interval 13:00–14:00).
        - Entry is the next same-session candle open.
        - Initial stop distance is 0.5 × ATR(14) from entry, using ATR known at signal close; this distance defines 1R.
        - If stop and +1R target both occur in one candle, stop is assumed first. At +1R, book 25%; remaining 75% retains original stop.
        - Remaining quantity exits at opposite EMA crossover candle close or final candle close of the session.
        - No overlapping trades; a new signal can be taken after the previous trade has exited.
        - Spot/index points only; no options premium, brokerage, taxes or slippage.
        """)


if selected_market == "NIFTY 50" and selected_strategy == "NIFTY EMA 9/20 ATR Partial":
    render_nifty_ema920_module()

if selected_market == "NIFTY 50" and selected_strategy == "NIFTY Stoch RSI V5":
    render_nifty_stoch_v5_module()
# ---------------- end NIFTY Stochastic RSI V5 module ----------------
