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


# ── UI ────────────────────────────────────────────────────────────────────────
st.title("📊 PPC Team — Audit Generator")
st.markdown("Fill in the details below and click **Run Audit** to generate the Google Slides deck.")

# ── Dashboard stats ───────────────────────────────────────────────────────────
try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as _Request
    import audit_log as _al

    _sheet_id = os.environ.get("AUDIT_LOG_SHEET_ID", "")
    if _sheet_id:
        _token_path = os.path.join(TOOL_DIR, "token.json")
        if os.path.exists(_token_path):
            _scopes = [
                "https://www.googleapis.com/auth/presentations",
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/spreadsheets",
            ]
            _creds = Credentials.from_authorized_user_file(_token_path, _scopes)
            if _creds.expired and _creds.refresh_token:
                _creds.refresh(_Request())
            _stats = _al.get_stats(_creds)
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Audits today",        _stats["audits_today"])
            col2.metric("Total audits",         _stats["total_audits"])
            col3.metric("Hours saved this month", f"{_stats['hours_saved_month']}h")
            col4.metric("Hours saved total",    f"{_stats['hours_saved_total']}h")
            st.markdown("---")
except Exception:
    pass

with st.form("audit_form"):
    client_name = st.text_input(
        "Client Name",
        placeholder="e.g. Kents Premier Coins & Bullion",
    )
    client_cid = st.text_input(
        "Client CID",
        placeholder="e.g. 539-263-1535",
    )

    st.markdown("---")
    st.markdown("**Slide 3 — Client context**")
    st.caption("Paste the completed market analysis questionnaire below. The AI will extract the key details automatically.")

    raw_questionnaire = st.text_area(
        "Market Analysis Questionnaire",
        placeholder="Paste the full questionnaire response here — objectives, spend, success metric, pain points, etc.",
        height=220,
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

    # Normalise CID — strip hyphens for the API
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
        findings = analyse_account(account_data)
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
            _post_scopes = [
                "https://www.googleapis.com/auth/presentations",
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/gmail.send",
            ]
            _lc = _Creds.from_authorized_user_file(
                os.path.join(TOOL_DIR, "token.json"), _post_scopes
            )
            if _lc.expired and _lc.refresh_token:
                _lc.refresh(_Req())

            _al.log_audit(_lc, client_name.strip(), client_cid.strip(),
                          _duration, slides_url, _tokens)
            _se.send_audit_summary(_lc, client_name.strip(), client_cid.strip(),
                                   _duration, slides_url, _tokens)
        except Exception as _le:
            print(f"Post-audit log/email error: {_le}")

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
            # Non-transient — something we should actually fix
            progress_bar.empty()
            status_box.empty()
            st.error(
                "❌ Something went wrong. Please check the details below "
                "and contact your system administrator if this keeps happening."
            )
            with st.expander("Technical details (for support)"):
                st.exception(e)
            st.stop()
