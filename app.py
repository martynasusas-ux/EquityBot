"""
app.py — Your Humble EquityBot — Navigation router.

Local:
    streamlit run app.py

Cloud deployment:
    1. Push repo to GitHub
    2. Connect at share.streamlit.io
    3. Add API keys in the Streamlit Secrets manager
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

import streamlit as st

# ── Cloud secret injection ────────────────────────────────────────────────────
# Must happen before any local module imports (which trigger config.py).
def _inject_cloud_secrets() -> None:
    """Copy Streamlit secrets → os.environ so config.py picks them up."""
    try:
        secret_keys = [
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
            "ALPHA_VANTAGE_API_KEY", "FRED_API_KEY",
            "FMP_API_KEY", "EODHD_API_KEY",
            "LLM_PROVIDER", "LLM_MODEL", "ADVERSARIAL_MODE",
        ]
        for k in secret_keys:
            if k in st.secrets and not os.environ.get(k):
                os.environ[k] = str(st.secrets[k])
    except Exception:
        pass  # Running locally — .env handles it

_inject_cloud_secrets()
sys.path.insert(0, str(Path(__file__).parent))

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Your Humble EquityBot",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Navigation ────────────────────────────────────────────────────────────────
pg = st.navigation([
    st.Page("pages/report_generator.py", title="Report Generator", icon="📊"),
    st.Page("pages/model_editing.py",    title="Model Editing",    icon="⚙️"),
])
pg.run()
