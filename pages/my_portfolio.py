"""
my_portfolio.py — Personal watchlist / portfolio tracker (EODHD-only).

Stores a user-chosen list of tickers in `data/portfolio.json` so the list
persists across app sessions.

For each saved ticker the page shows (all from EODHD):
  • Price            — /real-time/{ticker}
  • Market cap       — /fundamentals/{ticker}  → Highlights.MarketCapitalization*
  • P/E              — /fundamentals            → Highlights.PERatio
  • ROE              — /fundamentals            → Highlights.ReturnOnEquityTTM
  • EBIT margin      — /fundamentals            → Highlights.OperatingMarginTTM
  • Recommendation   — heuristic on the above 3 ratios
  • 5-year chart     — /eod/{ticker}            (collapsible)

Tickers are entered in Yahoo Finance format (RHM.DE, AAPL, ^GSPC, ...) and
converted to EODHD format via _convert_ticker(). Indices/forex without
fundamentals fall back to real-time + EOD only.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Any

import pandas as pd
import requests
import streamlit as st

from config import EODHD_API_KEY, REQUEST_HEADERS
from data_sources.eodhd_adapter import _YF_TO_EODHD

# ── Storage ───────────────────────────────────────────────────────────────────
_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
_PORTFOLIO_FILE = _DATA_DIR / "portfolio.json"

EODHD_BASE = "https://eodhistoricaldata.com/api"


def _load_portfolio() -> list[str]:
    if not _PORTFOLIO_FILE.exists():
        return []
    try:
        raw = json.loads(_PORTFOLIO_FILE.read_text(encoding="utf-8"))
        return list(raw.get("tickers", []))
    except Exception:
        return []


def _save_portfolio(tickers: list[str]) -> None:
    _PORTFOLIO_FILE.write_text(
        json.dumps({"tickers": tickers}, indent=2),
        encoding="utf-8",
    )


# ── Ticker conversion (Yahoo → EODHD) ─────────────────────────────────────────
def _convert_ticker(yf_ticker: str) -> str:
    """
    Convert a Yahoo Finance ticker to EODHD format.

    Examples:
      RHM.DE    → RHM.XETRA
      AAPL      → AAPL.US
      005930.KS → 005930.KO
      ^GSPC     → GSPC.INDX
      EURUSD=X  → EURUSD.FOREX
    """
    t = yf_ticker.strip().upper()

    # Forex (Yahoo: EURUSD=X)
    if t.endswith("=X"):
        return t.replace("=X", "") + ".FOREX"

    # Indices (Yahoo: ^GSPC, ^DJI)
    if t.startswith("^"):
        return t[1:] + ".INDX"

    dot = t.rfind(".")
    if dot == -1:
        return f"{t}.US"
    suffix = t[dot:]
    base = t[:dot]
    eodhd_suffix = _YF_TO_EODHD.get(suffix, suffix)
    if eodhd_suffix == ".HK" and base.isdigit():
        base = base.zfill(4)
    if eodhd_suffix in (".KO", ".KQ") and base.isdigit():
        base = base.zfill(6)
    return f"{base}{eodhd_suffix}"


# ── Low-level EODHD GET helper ────────────────────────────────────────────────
def _eodhd_get(path: str, params: dict | None = None, timeout: int = 30) -> Optional[Any]:
    if not EODHD_API_KEY:
        return None
    p = {"api_token": EODHD_API_KEY, "fmt": "json"}
    if params:
        p.update(params)
    try:
        url = f"{EODHD_BASE}{path}"
        r = requests.get(url, params=p, headers=REQUEST_HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


# ── Data fetching (cached) ────────────────────────────────────────────────────
def _to_float(v) -> Optional[float]:
    if v is None or v == "" or v == "NA":
        return None
    try:
        return float(v)
    except Exception:
        return None


@st.cache_data(ttl=900, show_spinner=False)   # 15-minute cache
def _fetch_snapshot(yf_ticker: str) -> dict:
    """
    Fetch all snapshot metrics for one ticker from EODHD.
    Returns a dict — missing fields come back as None.
    """
    eodhd_ticker = _convert_ticker(yf_ticker)

    # ── Real-time price ───────────────────────────────────────────────────────
    rt = _eodhd_get(f"/real-time/{eodhd_ticker}") or {}
    price = _to_float(rt.get("close"))
    if price is None or price < 0:
        price = _to_float(rt.get("previousClose"))

    # ── Fundamentals (may be absent for indices/forex/some exchanges) ─────────
    time.sleep(0.2)
    fund = _eodhd_get(f"/fundamentals/{eodhd_ticker}") or {}
    general    = fund.get("General")    or {}
    highlights = fund.get("Highlights") or {}

    name = general.get("Name") or yf_ticker
    sector = general.get("Sector") or general.get("Type") or ""
    currency = (
        general.get("CurrencyCode")
        or general.get("CurrencySymbol")
        or rt.get("currency")
        or ""
    )

    # Market cap (prefer Mln, fallback to raw)
    mc_mln = _to_float(highlights.get("MarketCapitalizationMln"))
    if mc_mln is not None:
        market_cap = mc_mln * 1e6
    else:
        market_cap = _to_float(highlights.get("MarketCapitalization"))

    pe          = _to_float(highlights.get("PERatio"))
    roe         = _to_float(highlights.get("ReturnOnEquityTTM"))
    ebit_margin = _to_float(highlights.get("OperatingMarginTTM"))

    return {
        "eodhd_ticker": eodhd_ticker,
        "name":         name,
        "currency":     currency,
        "sector":       sector,
        "price":        price,
        "market_cap":   market_cap,
        "pe":           pe,
        "roe":          roe,
        "ebit_margin":  ebit_margin,
    }


@st.cache_data(ttl=1800, show_spinner=False)   # 30-minute cache for news
def _fetch_news(yf_ticker: str, limit: int = 15) -> list[dict]:
    """
    Latest news for a ticker from EODHD /news endpoint.
    Returns a list of dicts (newest first) — empty list on failure.
    """
    eodhd_ticker = _convert_ticker(yf_ticker)
    data = _eodhd_get(
        "/news",
        params={"s": eodhd_ticker, "limit": limit, "offset": 0},
        timeout=30,
    )
    if not isinstance(data, list):
        return []
    return data


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_history(yf_ticker: str, years: int = 5) -> Optional[pd.DataFrame]:
    """5-year daily close prices from EODHD /eod endpoint."""
    eodhd_ticker = _convert_ticker(yf_ticker)
    end = datetime.utcnow().date()
    start = end - timedelta(days=years * 365 + 30)
    data = _eodhd_get(
        f"/eod/{eodhd_ticker}",
        params={"from": start.isoformat(), "to": end.isoformat(),
                "period": "d", "order": "a"},
        timeout=45,
    )
    if not isinstance(data, list) or not data:
        return None
    try:
        df = pd.DataFrame(data)
        if "date" not in df.columns or "close" not in df.columns:
            return None
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")[["close"]].rename(columns={"close": "Close"})
        df.index.name = "Date"
        return df
    except Exception:
        return None


# ── Recommendation logic (rule-based, no LLM) ─────────────────────────────────
def _recommendation(snap: dict) -> tuple[str, str]:
    """
    Heuristic Buy/Hold/Sell based on P/E, ROE and EBIT margin.
    Returns (label, color_hex).
    """
    pe   = snap.get("pe")
    roe  = snap.get("roe")
    ebit = snap.get("ebit_margin")

    if pe is None and roe is None and ebit is None:
        return "—", "#888888"

    score = 0
    used = 0

    if pe is not None:
        used += 1
        if pe <= 0:           score -= 1     # losses
        elif pe < 15:         score += 2
        elif pe < 25:         score += 1
        elif pe < 35:         score += 0
        else:                 score -= 1     # very expensive

    if roe is not None:
        used += 1
        if roe >= 0.20:       score += 2
        elif roe >= 0.12:     score += 1
        elif roe >= 0.05:     score += 0
        elif roe >= 0:        score -= 1
        else:                 score -= 2

    if ebit is not None:
        used += 1
        if ebit >= 0.20:      score += 2
        elif ebit >= 0.10:    score += 1
        elif ebit >= 0.05:    score += 0
        elif ebit >= 0:       score -= 1
        else:                 score -= 2

    if used == 0:
        return "—", "#888888"

    avg = score / used
    if avg >= 1.0:
        return "BUY",  "#1A7E3D"   # green
    if avg <= -0.5:
        return "SELL", "#B83227"   # red
    return "HOLD", "#C49102"       # amber


# ── Formatting helpers ────────────────────────────────────────────────────────
def _fmt_money(v) -> str:
    if v is None:
        return "—"
    try:
        v = float(v)
    except Exception:
        return "—"
    if abs(v) >= 1e12: return f"{v/1e12:.2f}T"
    if abs(v) >= 1e9:  return f"{v/1e9:.2f}B"
    if abs(v) >= 1e6:  return f"{v/1e6:.2f}M"
    if abs(v) >= 1e3:  return f"{v/1e3:.2f}K"
    return f"{v:.2f}"


def _fmt_price(v, ccy: str = "") -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.2f} {ccy}".strip()
    except Exception:
        return "—"


def _fmt_ratio(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.2f}×"
    except Exception:
        return "—"


def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v)*100:.1f}%"
    except Exception:
        return "—"


# ── UI ────────────────────────────────────────────────────────────────────────
def _normalize_ticker(raw: str) -> str:
    return raw.strip().upper().replace(" ", "")


st.title("📁 My Portfolio")
st.caption(
    "Personal watchlist powered by **EODHD only**. Add any ticker — stocks "
    "(AAPL, RHM.DE), indices (^GSPC, ^DJI), ETFs (SPY) or forex "
    "(EURUSD=X). The list is saved automatically and survives between sessions."
)

if not EODHD_API_KEY:
    st.error(
        "❌ `EODHD_API_KEY` is not configured. This page requires an EODHD "
        "subscription (All-In-One plan). Set it in `.env` locally or in "
        "Streamlit Cloud secrets."
    )
    st.stop()

# ── Initialise session state from disk ────────────────────────────────────────
if "portfolio_tickers" not in st.session_state:
    st.session_state.portfolio_tickers = _load_portfolio()

# ── Add ticker form ───────────────────────────────────────────────────────────
with st.form("add_ticker_form", clear_on_submit=True):
    col1, col2 = st.columns([4, 1])
    with col1:
        new_ticker = st.text_input(
            "Add ticker",
            placeholder="e.g. AAPL, RHM.DE, ^GSPC, EURUSD=X",
            label_visibility="collapsed",
        )
    with col2:
        add_btn = st.form_submit_button("➕ Add", use_container_width=True)

if add_btn and new_ticker:
    norm = _normalize_ticker(new_ticker)
    if norm and norm not in st.session_state.portfolio_tickers:
        with st.spinner(f"Verifying {norm} via EODHD..."):
            test = _fetch_snapshot(norm)
        if test["price"] is None and not test.get("market_cap"):
            st.error(
                f"Couldn't find EODHD data for **{norm}** "
                f"(tried `{test['eodhd_ticker']}`). Check the ticker format."
            )
        else:
            st.session_state.portfolio_tickers.append(norm)
            _save_portfolio(st.session_state.portfolio_tickers)
            st.success(f"Added **{norm}** to portfolio.")
            st.rerun()
    elif norm in st.session_state.portfolio_tickers:
        st.info(f"**{norm}** is already in your portfolio.")

# ── Top bar ───────────────────────────────────────────────────────────────────
top_l, top_r = st.columns([6, 1])
with top_l:
    if st.session_state.portfolio_tickers:
        st.markdown(
            f"**{len(st.session_state.portfolio_tickers)}** ticker"
            f"{'s' if len(st.session_state.portfolio_tickers) != 1 else ''} tracked"
        )
with top_r:
    if st.button("🔄 Refresh", use_container_width=True):
        _fetch_snapshot.clear()
        _fetch_history.clear()
        _fetch_news.clear()
        st.rerun()

st.markdown("---")

# ── Portfolio rendering ───────────────────────────────────────────────────────
if not st.session_state.portfolio_tickers:
    st.info(
        "Your portfolio is empty. Add a ticker above to get started.\n\n"
        "Examples: `AAPL`, `MSFT`, `RHM.DE`, `^GSPC` (S&P 500), "
        "`SPY` (ETF), `EURUSD=X` (forex)."
    )
else:
    for ticker in list(st.session_state.portfolio_tickers):
        snap = _fetch_snapshot(ticker)
        rec_label, rec_color = _recommendation(snap)

        # Card container
        with st.container(border=True):
            # Header row: name + ticker + remove button
            hcol1, hcol2 = st.columns([8, 1])
            with hcol1:
                meta = f"({ticker}"
                if snap["sector"]:
                    meta += f" · {snap['sector']}"
                meta += ")"
                st.markdown(
                    f"### {snap['name']}  "
                    f"<span style='color:#888;font-size:14px;'>{meta}</span>",
                    unsafe_allow_html=True,
                )
            with hcol2:
                if st.button("🗑️", key=f"del_{ticker}", help="Remove from portfolio"):
                    st.session_state.portfolio_tickers.remove(ticker)
                    _save_portfolio(st.session_state.portfolio_tickers)
                    st.rerun()

            # Metrics row
            m1, m2, m3, m4, m5, m6 = st.columns(6)
            m1.metric("Price",       _fmt_price(snap["price"], snap["currency"]))
            m2.metric("Market Cap",  _fmt_money(snap["market_cap"]))
            m3.metric("P/E",         _fmt_ratio(snap["pe"]))
            m4.metric("ROE",         _fmt_pct(snap["roe"]))
            m5.metric("EBIT Margin", _fmt_pct(snap["ebit_margin"]))
            m6.markdown(
                f"<div style='text-align:center;padding-top:8px;'>"
                f"<div style='color:#888;font-size:13px;'>Recommendation</div>"
                f"<div style='color:{rec_color};font-weight:700;font-size:22px;"
                f"margin-top:2px;'>{rec_label}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

            # Collapsible 5-year chart
            with st.expander("📈 Show 5-year price chart", expanded=False):
                hist = _fetch_history(ticker, years=5)
                if hist is None or hist.empty:
                    st.warning("No EODHD price history available for this ticker.")
                else:
                    st.line_chart(hist, height=300, use_container_width=True)
                    try:
                        first_px = float(hist["Close"].iloc[0])
                        last_px  = float(hist["Close"].iloc[-1])
                        chg_pct  = (last_px / first_px - 1) * 100 if first_px else 0
                        low5y    = float(hist["Close"].min())
                        high5y   = float(hist["Close"].max())
                        cs1, cs2, cs3 = st.columns(3)
                        cs1.caption(f"**5Y change:** {chg_pct:+.1f}%")
                        cs2.caption(f"**5Y low:** {low5y:,.2f}")
                        cs3.caption(f"**5Y high:** {high5y:,.2f}")
                    except Exception:
                        pass

            # Collapsible news feed
            with st.expander("📰 Show latest news", expanded=False):
                news_items = _fetch_news(ticker, limit=15)
                if not news_items:
                    st.warning("No EODHD news available for this ticker.")
                else:
                    for n in news_items:
                        title   = n.get("title")   or "(no title)"
                        link    = n.get("link")    or ""
                        date    = n.get("date")    or ""
                        content = n.get("content") or ""

                        # Date shown as YYYY-MM-DD HH:MM (EODHD format)
                        date_short = str(date)[:16].replace("T", " ")

                        # Sentiment polarity (if present): EODHD returns a dict
                        # like {"polarity": 0.42, "neg": 0.1, "neu": 0.5, "pos": 0.4}
                        sent = n.get("sentiment") or {}
                        polarity = sent.get("polarity") if isinstance(sent, dict) else None
                        if polarity is None:
                            sent_badge = ""
                        elif polarity >= 0.15:
                            sent_badge = (
                                f" <span style='background:#E0F2E5;color:#1A7E3D;"
                                f"padding:1px 6px;border-radius:8px;font-size:11px;"
                                f"font-weight:600;'>+ {polarity:.2f}</span>"
                            )
                        elif polarity <= -0.15:
                            sent_badge = (
                                f" <span style='background:#FBE5E2;color:#B83227;"
                                f"padding:1px 6px;border-radius:8px;font-size:11px;"
                                f"font-weight:600;'>− {polarity:.2f}</span>"
                            )
                        else:
                            sent_badge = (
                                f" <span style='background:#F0F0F0;color:#666;"
                                f"padding:1px 6px;border-radius:8px;font-size:11px;'>"
                                f"~ {polarity:.2f}</span>"
                            )

                        # Title as link, date + sentiment on the side
                        title_html = (
                            f"<a href='{link}' target='_blank' "
                            f"style='color:#1B3F6E;font-weight:600;text-decoration:none;'>"
                            f"{title}</a>"
                            if link else
                            f"<span style='color:#1B3F6E;font-weight:600;'>{title}</span>"
                        )
                        st.markdown(
                            f"<div style='margin-bottom:4px;'>"
                            f"<span style='color:#888;font-size:12px;'>{date_short}</span>"
                            f"{sent_badge}<br>{title_html}"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                        # Short snippet of body (first ~300 chars)
                        if content:
                            snippet = content.strip().replace("\n", " ")
                            if len(snippet) > 300:
                                snippet = snippet[:300].rstrip() + "…"
                            st.markdown(
                                f"<div style='color:#444;font-size:13px;"
                                f"margin-bottom:12px;line-height:1.4;'>{snippet}</div>",
                                unsafe_allow_html=True,
                            )
                        else:
                            st.markdown("<div style='margin-bottom:12px;'></div>",
                                        unsafe_allow_html=True)

st.markdown("---")
st.caption(
    "All data: **EODHD All-In-One** · Snapshot cached 15 min · "
    "News cached 30 min · 5Y history cached 1 h · "
    "Recommendation is a rule-based heuristic on P/E + ROE + EBIT margin "
    "— not investment advice."
)
