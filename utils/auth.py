"""
utils/auth.py — Shared authentication guard for all pages.

Every page file calls require_auth() as its very first statement after
imports. This provides defence-in-depth: even if Streamlit serves a
page URL directly (bypassing app.py), the guard fires and blocks it.

Usage (top of any page file):
    from utils.auth import require_auth
    require_auth()
"""
from __future__ import annotations
import hashlib
import streamlit as st


def _load_users() -> dict[str, str]:
    """Return {username: password_sha256} from st.secrets['users'], or {} if absent."""
    try:
        return dict(st.secrets["users"])
    except Exception:
        return {}


def require_auth() -> None:
    """
    Block the page if the user is not authenticated.

    - If no [users] section exists in secrets → no-op (dev / local mode).
    - If authenticated → continue normally.
    - If NOT authenticated → show a locked screen and call st.stop().
    """
    users = _load_users()
    if not users:
        return  # Auth not configured — open access (dev mode)

    if st.session_state.get("authenticated"):
        return  # Already logged in — let the page render

    # ── Not logged in — block the page ───────────────────────────────────────
    st.set_page_config(page_title="EquityBot — Sign In", page_icon="🔒")

    st.markdown("""
    <style>
    .block-container { padding-top: 3rem; }
    .lock-wrap {
        max-width: 420px;
        margin: 60px auto;
        padding: 40px;
        background: #fff;
        border: 1px solid #D0DFF0;
        border-radius: 12px;
        box-shadow: 0 4px 24px rgba(27,63,110,0.10);
        text-align: center;
    }
    .lock-icon  { font-size: 48px; margin-bottom: 12px; }
    .lock-title { color: #1B3F6E; font-size: 20px; font-weight: 700; margin-bottom: 8px; }
    .lock-msg   { color: #666; font-size: 14px; line-height: 1.6; }
    </style>
    <div class="lock-wrap">
      <div class="lock-icon">🔒</div>
      <div class="lock-title">Access Restricted</div>
      <div class="lock-msg">
        This tool is private.<br>
        Please return to the home page and sign in.
      </div>
    </div>
    """, unsafe_allow_html=True)

    st.stop()
