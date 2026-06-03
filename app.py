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

with st.form("audit_form"):
    client_name = st.text_input(
        "Client Name",
        placeholder="e.g. KPCB Ltd",
    )
    client_cid = st.text_input(
        "Client CID",
        placeholder="e.g. 981-476-6301",
    )

    st.markdown("---")
    st.markdown("**Slide 3 — Client context**")

    objectives = st.text_area(
        "Objectives",
        placeholder="e.g. Generate high-quality leads for their solar panel installation service.",
        height=100,
    )
    success_metric = st.text_input(
        "Success Metric",
        placeholder="e.g. 3 good appointments a day at £120 CPA or under",
    )
    pain_points = st.text_area(
        "Pain Points",
        placeholder="e.g. Cost per lead has risen 40% in the last 3 months.",
        height=100,
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

    try:
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
        update("Writing audit narrative with GPT-4o…", 55)
        from generate_narrative import generate_narrative
        narrative = generate_narrative(
            findings,
            get_secret("OPENAI_API_KEY"),
            client_name.strip(),
        )

        # Inject slide-3 form values into the objectives dict
        narrative["objectives"]["objectives_text"]  = objectives.strip()     or "To be confirmed."
        narrative["objectives"]["success_metric"]   = success_metric.strip() or "To be confirmed."
        narrative["objectives"]["pain_points_text"] = pain_points.strip()    or "To be confirmed."

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

        # ── Done ──────────────────────────────────────────────────────────
        progress_bar.progress(100)
        status_box.success("✅ Audit complete!")

        st.markdown("### 🎉 Your audit deck is ready")
        st.markdown(
            f"[**Open in Google Slides ↗**]({slides_url})",
            unsafe_allow_html=True,
        )
        st.caption(f"Client: {client_name.strip()} · CID: {client_cid.strip()}")

    except Exception as e:
        progress_bar.empty()
        status_box.error("❌ Something went wrong — details below.")
        st.exception(e)
        st.stop()
