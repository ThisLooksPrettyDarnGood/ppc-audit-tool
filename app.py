import streamlit as st
import json
import sys
import os

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="PPC Team Audit Tool",
    page_icon="📊",
    layout="centered",
)

# ── Tool directory (works locally and on Streamlit Cloud) ─────────────────────
TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
if TOOL_DIR not in sys.path:
    sys.path.insert(0, TOOL_DIR)

# ── Secrets helper ────────────────────────────────────────────────────────────
def get_secret(key: str) -> str:
    """Pull from st.secrets (Streamlit Cloud) or env vars (local dev)."""
    if key in st.secrets:
        return st.secrets[key]
    val = os.environ.get(key)
    if val:
        return val
    raise KeyError(f"Missing secret / env var: {key}")


# ── Write credential files from secrets (Streamlit Cloud only) ───────────────
def prepare_credentials():
    """
    On Streamlit Cloud there are no local token files.
    Reconstruct credentials.json, token.json, and token_ads.json from secrets.
    Skips any file that already exists (so local dev works unchanged).
    """
    creds_path     = os.path.join(TOOL_DIR, "credentials.json")
    token_path     = os.path.join(TOOL_DIR, "token.json")
    token_ads_path = os.path.join(TOOL_DIR, "token_ads.json")

    if not os.path.exists(creds_path):
        data = {
            "installed": {
                "client_id":     get_secret("GOOGLE_CLIENT_ID"),
                "client_secret": get_secret("GOOGLE_CLIENT_SECRET"),
                "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
                "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
                "token_uri":     "https://oauth2.googleapis.com/token",
            }
        }
        with open(creds_path, "w") as f:
            json.dump(data, f)

    if not os.path.exists(token_path):
        data = {
            "token":         None,
            "refresh_token": get_secret("GOOGLE_REFRESH_TOKEN_SLIDES"),
            "token_uri":     "https://oauth2.googleapis.com/token",
            "client_id":     get_secret("GOOGLE_CLIENT_ID"),
            "client_secret": get_secret("GOOGLE_CLIENT_SECRET"),
            "scopes": [
                "https://www.googleapis.com/auth/presentations",
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/gmail.send",
            ],
        }
        with open(token_path, "w") as f:
            json.dump(data, f)

    if not os.path.exists(token_ads_path):
        data = {
            "token":         None,
            "refresh_token": get_secret("GOOGLE_REFRESH_TOKEN_ADS"),
            "token_uri":     "https://oauth2.googleapis.com/token",
            "client_id":     get_secret("GOOGLE_CLIENT_ID"),
            "client_secret": get_secret("GOOGLE_CLIENT_SECRET"),
            "scopes": ["https://www.googleapis.com/auth/adwords"],
        }
        with open(token_ads_path, "w") as f:
            json.dump(data, f)


# ── Guardrails: config + helpers ──────────────────────────────────────────────
def _daily_limit() -> int:
    """
    Max audits per day across the whole team.
    0 (or unset) = NO LIMIT. To re-enable, add a line to Streamlit secrets, e.g.
        DAILY_AUDIT_LIMIT = "10"
    No code change needed to turn the cap on or off.
    """
    try:
        raw = st.secrets.get("DAILY_AUDIT_LIMIT", os.environ.get("DAILY_AUDIT_LIMIT", "0"))
        return int(raw)
    except (ValueError, TypeError):
        return 0


def _log_creds():
    """Spreadsheets-scope credentials for reading the audit log (count, stats)."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    c = Credentials(
        token=None,
        refresh_token=get_secret("GOOGLE_REFRESH_TOKEN_SLIDES"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=get_secret("GOOGLE_CLIENT_ID"),
        client_secret=get_secret("GOOGLE_CLIENT_SECRET"),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    c.refresh(Request())
    return c


def _audits_today() -> int:
    """How many audits have run today (whole team). Returns 0 if unreadable."""
    try:
        import audit_log as _al
        _sid = st.secrets.get("AUDIT_LOG_SHEET_ID", "") or os.environ.get("AUDIT_LOG_SHEET_ID", "")
        if not _sid:
            return 0
        os.environ["AUDIT_LOG_SHEET_ID"] = _sid
        return _al.get_stats(_log_creds()).get("audits_today", 0)
    except Exception:
        return 0


def _password_gate():
    """
    Require a shared team password before the tool can be used.
    Only active if APP_PASSWORD is set in secrets  -  so the app is never
    bricked if the password hasn't been configured yet.
    """
    try:
        expected = get_secret("APP_PASSWORD")
    except KeyError:
        return  # no password configured → gate off

    if st.session_state.get("_authed"):
        return

    st.markdown("#### 🔒 Team access")
    st.caption("Enter the team password to use the audit tool.")
    pw = st.text_input("Password", type="password", key="_pw_input")
    if st.button("Unlock"):
        if pw == expected:
            st.session_state["_authed"] = True
            st.rerun()
        else:
            st.error("Incorrect password. Ask Dan if you need it.")
    st.stop()


# ── UI ────────────────────────────────────────────────────────────────────────
st.title("📊 PPC Team  -  Audit Generator")
_password_gate()
st.markdown("Fill in the details below and click **Run Audit** to generate the Google Slides deck.")

# ── Dashboard stats ───────────────────────────────────────────────────────────
try:
    from google.oauth2.credentials import Credentials as _StatsCreds
    from google.auth.transport.requests import Request as _StatsRequest
    import audit_log as _al

    _sheet_id = st.secrets.get("AUDIT_LOG_SHEET_ID", "") or os.environ.get("AUDIT_LOG_SHEET_ID", "")
    if _sheet_id:
        os.environ["AUDIT_LOG_SHEET_ID"] = _sheet_id
        # Build creds directly from secrets  -  works before any audit has run
        _stats_creds = _StatsCreds(
            token=None,
            refresh_token=get_secret("GOOGLE_REFRESH_TOKEN_SLIDES"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=get_secret("GOOGLE_CLIENT_ID"),
            client_secret=get_secret("GOOGLE_CLIENT_SECRET"),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        _stats_creds.refresh(_StatsRequest())
        _stats = _al.get_stats(_stats_creds)
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Audits today",           _stats["audits_today"])
        col2.metric("Total audits",           _stats["total_audits"])
        col3.metric("Hours saved this month", f"{_stats['hours_saved_month']}h")
        col4.metric("Hours saved total",      f"{_stats['hours_saved_total']}h")
        st.markdown("---")
except Exception:
    pass

with st.form("audit_form"):
    # Three fields share one row (thirds) to keep the form compact
    fcol1, fcol2, fcol3 = st.columns(3)
    client_name = fcol1.text_input(
        "Client Name",
        placeholder="e.g. Kents Premier Coins & Bullion",
    )
    client_cid = fcol2.text_input(
        "Client CID",
        placeholder="e.g. 539-263-1535",
    )
    runner_email = fcol3.text_input(
        "Your email",
        placeholder="e.g. you@ppcgeeks.co.uk",
        help="We'll send the completed audit summary to this address.",
    )

    st.markdown("**Slide 3  -  Client context**")
    st.caption("Paste the completed market analysis questionnaire below. The AI will extract the key details automatically.")

    raw_questionnaire = st.text_area(
        "Market Analysis Questionnaire",
        placeholder="Paste the full questionnaire response here  -  objectives, spend, success metric, pain points, etc.",
        height=140,
    )

    submitted = st.form_submit_button("🚀 Run Audit", use_container_width=True)


# ── Pipeline execution ────────────────────────────────────────────────────────
if submitted:
    # Validation
    missing = []
    if not client_name.strip():
        missing.append("Client Name")
    if not client_cid.strip():
        missing.append("Client CID")
    if missing:
        st.error(f"Please fill in: {', '.join(missing)}")
        st.stop()

    # ── Guardrail: daily cap (whole team)  -  only enforced when a limit is set ──
    _limit = _daily_limit()
    if _limit > 0:
        _today_count = _audits_today()
        if _today_count >= _limit:
            st.error(
                f"🛑 Daily limit reached  -  the team has run {_today_count} audits today "
                f"(cap is {_limit}). This protects our OpenAI credit. "
                f"Please try again tomorrow, or ask Dan if you need the cap raised."
            )
            st.stop()

    # Normalise CID  -  strip hyphens for the API
    cid_clean = client_cid.strip().replace("-", "")

    st.markdown("---")
    progress_bar = st.progress(0)
    status_box   = st.empty()

    def update(msg: str, pct: int):
        status_box.info(f"**{msg}**")
        progress_bar.progress(pct)

    def _is_transient(err: Exception) -> bool:
        """True for temporary Google-side failures worth retrying."""
        msg = str(err).lower()
        return any(x in msg for x in ["500", "503", "internalservererror",
                                       "internal error", "unavailable",
                                       "deadline exceeded", "try again"])

    def _run_pipeline():
        """Full audit pipeline. Raises on failure."""
        import time as _time
        _audit_start = _time.time()

        # ── Step 0: credentials ───────────────────────────────────────────
        update("Preparing credentials…", 5)
        prepare_credentials()

        # Expose secrets as env vars so existing modules can read them
        os.environ["OPENAI_API_KEY"]              = get_secret("OPENAI_API_KEY")
        os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"]  = get_secret("GOOGLE_ADS_DEVELOPER_TOKEN")
        # Audit log sheet ID  -  set here so the end-of-run log write can find it
        _log_sheet_id = st.secrets.get("AUDIT_LOG_SHEET_ID", "") or os.environ.get("AUDIT_LOG_SHEET_ID", "")
        if _log_sheet_id:
            os.environ["AUDIT_LOG_SHEET_ID"] = _log_sheet_id

        # ── Step 1: fetch Google Ads data ─────────────────────────────────
        update("Fetching Google Ads data…", 15)
        import fetch_account_data as fad_module

        # Patch the hardcoded paths to use TOOL_DIR (works on Streamlit Cloud)
        fad_module.CREDENTIALS_PATH = os.path.join(TOOL_DIR, "client_secret.json")
        fad_module.TOKEN_PATH       = os.path.join(TOOL_DIR, "token_ads.json")

        account_data = fad_module.fetch_account_data(cid_clean)

        # ── Step 2: analyse ───────────────────────────────────────────────
        update("Analysing account…", 35)
        from analyse_account import analyse_account
        findings = analyse_account(account_data, raw_questionnaire=raw_questionnaire.strip())
        findings["account_cid"] = cid_clean

        # ── Step 3: generate narrative ────────────────────────────────────
        update("Writing audit narrative with GPT-5.5…", 55)
        from generate_narrative import generate_narrative
        narrative = generate_narrative(
            findings,
            get_secret("OPENAI_API_KEY"),
            client_name.strip(),
            raw_questionnaire=raw_questionnaire.strip(),
        )

        # Save narrative (populate_slides reads this file)
        narrative_path = os.path.join(TOOL_DIR, "narrative_output.json")
        with open(narrative_path, "w") as f:
            json.dump(narrative, f, indent=2)

        # ── Step 4: populate slides ───────────────────────────────────────
        update("Building Google Slides deck…", 75)
        import populate_slides as ps_module

        # Patch module-level path constants to use TOOL_DIR
        ps_module.CREDENTIALS_FILE = os.path.join(TOOL_DIR, "credentials.json")
        ps_module.TOKEN_FILE       = os.path.join(TOOL_DIR, "token.json")
        ps_module.NARRATIVE_FILE   = narrative_path

        slides_url = ps_module.main()

        # ── Log + email the completed audit ──────────────────────────────
        try:
            import audit_log as _al
            import send_email as _se
            from google.oauth2.credentials import Credentials as _Creds
            from google.auth.transport.requests import Request as _Req

            _duration = _time.time() - _audit_start
            _tokens   = narrative.get("_tokens_used", 0)

            # Full ranked findings list (incl. those below the 6-slide cut) for the
            # internal email, so the auditor can reference everything on the call.
            try:
                from analyse_account import select_top_issues as _sti
                _all_findings = _sti(findings, max_issues=50, apply_floor=False)
                _findings_lines = [
                    f"[{i.get('category','')}] " + i.get("detail", "").split(". ")[0].strip()
                    for i in _all_findings
                ]
            except Exception:
                _findings_lines = []

            # Build credentials directly from secrets  -  reliable on cloud
            _lc = _Creds(
                token=None,
                refresh_token=get_secret("GOOGLE_REFRESH_TOKEN_SLIDES"),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=get_secret("GOOGLE_CLIENT_ID"),
                client_secret=get_secret("GOOGLE_CLIENT_SECRET"),
                scopes=[
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/gmail.send",
                ],
            )
            _lc.refresh(_Req())

            _log_err = _al.log_audit(_lc, client_name.strip(), client_cid.strip(),
                                     _duration, slides_url, _tokens)
            _email_err = _se.send_audit_summary(_lc, client_name.strip(), client_cid.strip(),
                                                _duration, slides_url, _tokens,
                                                recipient=runner_email.strip(),
                                                findings_lines=_findings_lines)

            # Surface results so we are never flying blind again
            if _log_err:
                st.warning(f"📋 Audit log: {_log_err}")
            else:
                st.caption("📋 Audit logged to Google Sheet ✓")
            if _email_err:
                st.warning(f"✉️ Email: {_email_err}")
            else:
                st.caption("✉️ Notification email sent ✓")
        except Exception as _le:
            st.warning(f"⚠️ Log/email setup error (credentials): {_le}")

        # ── Done ──────────────────────────────────────────────────────────
        progress_bar.progress(100)
        status_box.success("✅ Audit complete!")

        st.markdown("### 🎉 Your audit deck is ready")
        st.markdown(
            f"[**Open in Google Slides ↗**]({slides_url})",
            unsafe_allow_html=True,
        )
        st.caption(f"Client: {client_name.strip()} · CID: {client_cid.strip()}")

    # ── Retry logic ───────────────────────────────────────────────────────────
    try:
        _run_pipeline()
    except Exception as e:
        if _is_transient(e):
            # Friendly countdown, then one automatic retry
            progress_bar.empty()
            countdown_box = st.empty()
            for secs_left in range(30, 0, -1):
                countdown_box.warning(
                    f"⏳ Google's API returned a temporary error. "
                    f"Retrying automatically in **{secs_left}s**…"
                )
                import time as _t; _t.sleep(1)
            countdown_box.info("🔄 Retrying now…")
            try:
                _run_pipeline()
                countdown_box.empty()
            except Exception as e2:
                countdown_box.empty()
                progress_bar.empty()
                status_box.empty()
                st.error(
                    "⚠️ Google's servers are having a moment. "
                    "Please wait a minute and click **Run Audit** again. "
                    "If it keeps happening, let Dan know."
                )
                st.stop()
        else:
            # Non-transient  -  something we should actually fix
            progress_bar.empty()
            status_box.empty()
            st.error(
                "❌ Something went wrong. Please check the details below "
                "and contact your system administrator if this keeps happening."
            )
            with st.expander("Technical details (for support)"):
                st.exception(e)
            st.stop()
