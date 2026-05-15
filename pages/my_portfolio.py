"""
my_portfolio.py — Personal watchlist / portfolio tracker (EODHD-only).

Stores a user-chosen list of tickers in `data/portfolio.json` so the list
persists across app sessions.

Compact rendering — one card = one row by default:
  Name · Price · Mkt Cap · P/E · ROE · EBIT Margin · YTD% · ▼ Expand

Clicking the expand toggle reveals:
  • Recommendation badge (Buy / Hold / Sell, rule-based)
  • Period-selectable price chart (1d / 1m / 6m / YTD / 5y / All)
  • Latest news from EODHD /news

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

import altair as alt
import pandas as pd
import requests
import streamlit as st
from streamlit_searchbox import st_searchbox

from config import EODHD_API_KEY, REQUEST_HEADERS
from data_sources.eodhd_adapter import _YF_TO_EODHD

# Reverse mapping: EODHD exchange code → Yahoo Finance suffix.
# Built once from _YF_TO_EODHD. Some collisions are inevitable (e.g. both
# ".VX" and ".SW" map to EODHD ".SW") — the last entry wins, which for our
# use case (showing results to the user) is fine.
_EODHD_TO_YF = {v: k for k, v in _YF_TO_EODHD.items()}

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
    t = yf_ticker.strip().upper()
    if t.endswith("=X"):
        return t.replace("=X", "") + ".FOREX"
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


# ── Low-level EODHD GET ───────────────────────────────────────────────────────
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


# ── Helpers ───────────────────────────────────────────────────────────────────
def _to_float(v) -> Optional[float]:
    if v is None or v == "" or v == "NA":
        return None
    try:
        return float(v)
    except Exception:
        return None


# ── Snapshot (cached) ─────────────────────────────────────────────────────────
@st.cache_data(ttl=900, show_spinner=False)   # 15-minute cache
def _fetch_snapshot(yf_ticker: str) -> dict:
    """
    All snapshot metrics for one ticker from EODHD, including YTD%.
    """
    eodhd_ticker = _convert_ticker(yf_ticker)

    # ── Real-time price ──────────────────────────────────────────────────────
    rt = _eodhd_get(f"/real-time/{eodhd_ticker}") or {}
    price = _to_float(rt.get("close"))
    if price is None or price < 0:
        price = _to_float(rt.get("previousClose"))

    # ── Fundamentals (may be missing for indices/forex) ──────────────────────
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

    mc_mln = _to_float(highlights.get("MarketCapitalizationMln"))
    market_cap = mc_mln * 1e6 if mc_mln is not None else _to_float(highlights.get("MarketCapitalization"))

    pe          = _to_float(highlights.get("PERatio"))
    roe         = _to_float(highlights.get("ReturnOnEquityTTM"))
    ebit_margin = _to_float(highlights.get("OperatingMarginTTM"))

    # ── YTD: pull the first trading day of the current year close ────────────
    ytd_pct: Optional[float] = None
    if price is not None:
        today = datetime.utcnow().date()
        year_start = datetime(today.year, 1, 1).date()
        eod = _eodhd_get(
            f"/eod/{eodhd_ticker}",
            params={"from": year_start.isoformat(),
                    "to":   today.isoformat(),
                    "period": "d", "order": "a"},
            timeout=30,
        )
        if isinstance(eod, list) and eod:
            for row in eod:
                if not isinstance(row, dict):
                    continue
                # Prefer split/dividend-adjusted close so a YoY split
                # (e.g. AAPL Aug 2020) doesn't break the YTD%.
                c = (_to_float(row.get("adjusted_close"))
                     or _to_float(row.get("adjusted"))
                     or _to_float(row.get("close")))
                if c and c > 0:
                    try:
                        ytd_pct = (float(price) / c - 1)
                    except Exception:
                        pass
                    break

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
        "ytd_pct":      ytd_pct,
    }


# ── History (cached, period-aware) ────────────────────────────────────────────
PERIODS = ["1d", "1m", "6m", "YTD", "5y", "All"]
DEFAULT_PERIOD = "5y"


def _period_range(period: str) -> tuple[Optional[datetime.date], datetime.date]:
    """Return (from_date, to_date). from_date=None means 'as far back as possible'."""
    today = datetime.utcnow().date()
    if period == "1d":
        # Last 5 trading days — we'll filter to 1 day's worth in fetch
        return (today - timedelta(days=7), today)
    if period == "1m":
        return (today - timedelta(days=35), today)
    if period == "6m":
        return (today - timedelta(days=185), today)
    if period == "YTD":
        return (datetime(today.year, 1, 1).date(), today)
    if period == "5y":
        return (today - timedelta(days=5 * 365 + 7), today)
    if period == "All":
        return (None, today)
    # Fallback
    return (today - timedelta(days=365), today)


@st.cache_data(ttl=1800, show_spinner=False)   # 30-min cache
def _fetch_history(yf_ticker: str, period: str) -> Optional[pd.DataFrame]:
    """
    Price history for the requested period.

    For "1d" we use the EODHD intraday endpoint with 5-minute bars so the
    chart actually shows the day's price action — daily-OHLC would give
    only 1-2 points for that range. All other periods use the daily /eod
    endpoint.
    """
    eodhd_ticker = _convert_ticker(yf_ticker)

    # ── 1-day chart: intraday 5-minute bars ──────────────────────────────────
    if period == "1d":
        # EODHD intraday: timestamp params are Unix seconds
        now_utc = datetime.utcnow()
        # Fetch the last 24h (covers extended-hours bars on US tickers)
        from_ts = int((now_utc - timedelta(hours=24)).timestamp())
        to_ts   = int(now_utc.timestamp())
        data = _eodhd_get(
            f"/intraday/{eodhd_ticker}",
            params={"interval": "5m", "from": from_ts, "to": to_ts},
            timeout=30,
        )
        if not isinstance(data, list) or not data:
            return None
        try:
            df = pd.DataFrame(data)
            ts_col = "datetime" if "datetime" in df.columns else "timestamp"
            if ts_col not in df.columns or "close" not in df.columns:
                return None
            if ts_col == "timestamp":
                df[ts_col] = pd.to_datetime(df[ts_col], unit="s")
            else:
                df[ts_col] = pd.to_datetime(df[ts_col])
            df = df.set_index(ts_col)[["close"]].rename(columns={"close": "Close"})
            df.index.name = "Time"
            # Keep only the most recent trading session
            return df
        except Exception:
            return None

    # ── All other periods: daily OHLC ────────────────────────────────────────
    # We prefer EODHD's `adjusted_close` (split- and dividend-adjusted) so
    # multi-year charts (especially "All") don't show a phantom dip on the
    # day of a stock split — e.g. AAPL's 4-for-1 in Aug 2020.
    start, end = _period_range(period)
    params = {"period": "d", "order": "a", "to": end.isoformat()}
    if start is not None:
        params["from"] = start.isoformat()
    data = _eodhd_get(f"/eod/{eodhd_ticker}", params=params, timeout=45)
    if not isinstance(data, list) or not data:
        return None
    try:
        df = pd.DataFrame(data)
        if "date" not in df.columns:
            return None
        # Choose the best price column: adjusted_close → adjusted → close
        price_col = None
        for cand in ("adjusted_close", "adjusted", "close"):
            if cand in df.columns:
                price_col = cand
                break
        if price_col is None:
            return None
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")[[price_col]].rename(columns={price_col: "Close"})
        df.index.name = "Date"
        # Drop any rows where the price came back as None / NaN
        df = df.dropna(subset=["Close"])
        return df if not df.empty else None
    except Exception:
        return None


# ── Upcoming earnings date (cached) ───────────────────────────────────────────
@st.cache_data(ttl=6 * 3600, show_spinner=False)   # 6-hour cache
def _fetch_next_earnings(yf_ticker: str) -> Optional[str]:
    """
    Return the next upcoming earnings report date (YYYY-MM-DD) for the
    ticker, or None if EODHD doesn't have one scheduled in the next 180 days.

    Uses /calendar/earnings — works for most US + EU listings. Indices,
    forex and ETFs return None.
    """
    eodhd_ticker = _convert_ticker(yf_ticker)
    today = datetime.utcnow().date()
    end   = today + timedelta(days=180)
    data = _eodhd_get(
        "/calendar/earnings",
        params={
            "symbols": eodhd_ticker,
            "from":    today.isoformat(),
            "to":      end.isoformat(),
        },
        timeout=30,
    )
    if not isinstance(data, dict):
        return None
    rows = data.get("earnings")
    if not isinstance(rows, list) or not rows:
        return None
    upcoming: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        d = row.get("report_date") or row.get("date")
        if not d:
            continue
        try:
            dt = datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        if dt >= today:
            upcoming.append(str(d)[:10])
    if not upcoming:
        return None
    return min(upcoming)


# ── News (cached) ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=1800, show_spinner=False)
def _fetch_news(yf_ticker: str, limit: int = 15) -> list[dict]:
    eodhd_ticker = _convert_ticker(yf_ticker)
    data = _eodhd_get(
        "/news",
        params={"s": eodhd_ticker, "limit": limit, "offset": 0},
        timeout=30,
    )
    if not isinstance(data, list):
        return []
    return data


# ── Recommendation heuristic ──────────────────────────────────────────────────
def _recommendation(snap: dict) -> tuple[str, str]:
    pe   = snap.get("pe")
    roe  = snap.get("roe")
    ebit = snap.get("ebit_margin")
    if pe is None and roe is None and ebit is None:
        return "—", "#888888"
    score = 0
    used = 0
    if pe is not None:
        used += 1
        if pe <= 0:      score -= 1
        elif pe < 15:    score += 2
        elif pe < 25:    score += 1
        elif pe < 35:    score += 0
        else:            score -= 1
    if roe is not None:
        used += 1
        if roe >= 0.20:   score += 2
        elif roe >= 0.12: score += 1
        elif roe >= 0.05: score += 0
        elif roe >= 0:    score -= 1
        else:             score -= 2
    if ebit is not None:
        used += 1
        if ebit >= 0.20:   score += 2
        elif ebit >= 0.10: score += 1
        elif ebit >= 0.05: score += 0
        elif ebit >= 0:    score -= 1
        else:              score -= 2
    if used == 0:
        return "—", "#888888"
    avg = score / used
    if avg >= 1.0:  return "BUY",  "#1A7E3D"
    if avg <= -0.5: return "SELL", "#B83227"
    return "HOLD", "#C49102"


# ── Formatters ────────────────────────────────────────────────────────────────
def _fmt_money(v) -> str:
    if v is None: return "—"
    try: v = float(v)
    except Exception: return "—"
    if abs(v) >= 1e12: return f"{v/1e12:.2f}T"
    if abs(v) >= 1e9:  return f"{v/1e9:.2f}B"
    if abs(v) >= 1e6:  return f"{v/1e6:.2f}M"
    if abs(v) >= 1e3:  return f"{v/1e3:.2f}K"
    return f"{v:.2f}"


def _fmt_price(v, ccy: str = "") -> str:
    if v is None: return "—"
    try: return f"{float(v):,.2f} {ccy}".strip()
    except Exception: return "—"


def _fmt_ratio(v) -> str:
    if v is None: return "—"
    try: return f"{float(v):.2f}×"
    except Exception: return "—"


def _fmt_pct(v) -> str:
    if v is None: return "—"
    try: return f"{float(v)*100:.1f}%"
    except Exception: return "—"


def _fmt_signed_pct(v) -> tuple[str, str]:
    """Return (text, color) — green/red based on sign."""
    if v is None:
        return "—", "#888888"
    try:
        pct = float(v) * 100
    except Exception:
        return "—", "#888888"
    color = "#1A7E3D" if pct >= 0 else "#B83227"
    return f"{pct:+.2f}%", color


def _normalize_ticker(raw: str) -> str:
    return raw.strip().upper().replace(" ", "")


# ── Reverse ticker conversion (EODHD → Yahoo Finance) ────────────────────────
def _eodhd_to_yf(code: str, exchange: str) -> str:
    """
    Convert EODHD (Code, Exchange) → Yahoo Finance ticker so it can be
    stored in the portfolio the same way users normally enter it.

    Examples:
      ("AAPL",   "US")    → "AAPL"
      ("RHM",    "XETRA") → "RHM.DE"
      ("005930", "KO")    → "005930.KS"
      ("GSPC",   "INDX")  → "^GSPC"
      ("EURUSD", "FOREX") → "EURUSD=X"
    """
    code = (code or "").strip().upper()
    exch = (exchange or "").strip().upper()
    if not code:
        return ""
    if exch in ("", "US"):
        return code
    if exch == "INDX":
        return "^" + code
    if exch == "FOREX":
        return code + "=X"
    eodhd_suffix = f".{exch}"
    yf_suffix = _EODHD_TO_YF.get(eodhd_suffix, eodhd_suffix)
    return f"{code}{yf_suffix}"


# ── EODHD search (cached) ────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)   # 5-minute cache per query
def _search_eodhd_raw(query: str) -> list[dict]:
    """Hit EODHD /search/{query} — returns the raw list of matches."""
    q = (query or "").strip()
    if len(q) < 1:
        return []
    data = _eodhd_get(f"/search/{q}", params={"limit": 15})
    return data if isinstance(data, list) else []


def _ticker_search(query: str) -> list[tuple[str, str]]:
    """
    Callback for st_searchbox. Returns a list of (display_label, yf_ticker)
    tuples. yf_ticker is the value stored in the portfolio on selection.

    Empty / very short queries return [] — searchbox simply shows nothing.
    """
    if not query or len(query.strip()) < 1:
        return []
    rows = _search_eodhd_raw(query)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        code = item.get("Code", "")
        exch = item.get("Exchange", "")
        name = item.get("Name", "") or ""
        ttype = (item.get("Type") or "").strip()
        country = (item.get("Country") or "").strip()
        if not code:
            continue
        yf_ticker = _eodhd_to_yf(code, exch)
        if not yf_ticker or yf_ticker in seen:
            continue
        seen.add(yf_ticker)
        # Build a compact, readable label
        meta_bits = [b for b in (exch, country, ttype) if b]
        meta = " · ".join(meta_bits)
        label = f"{yf_ticker:<14}  {name[:48]}"
        if meta:
            label += f"  ({meta})"
        out.append((label, yf_ticker))
    return out


# ── Page header ───────────────────────────────────────────────────────────────
st.title("📁 My Portfolio")
st.caption(
    "Personal watchlist powered by **EODHD only**. Add any ticker — stocks "
    "(AAPL, RHM.DE), indices (^GSPC, ^DJI), ETFs (SPY) or forex "
    "(EURUSD=X). Cards are collapsed by default — click ▾ to expand."
)

if not EODHD_API_KEY:
    st.error(
        "❌ `EODHD_API_KEY` is not configured. This page requires an EODHD "
        "subscription (All-In-One plan). Set it in `.env` locally or in "
        "Streamlit Cloud secrets."
    )
    st.stop()

# ── Session state ─────────────────────────────────────────────────────────────
if "portfolio_tickers" not in st.session_state:
    st.session_state.portfolio_tickers = _load_portfolio()
if "portfolio_expanded" not in st.session_state:
    st.session_state.portfolio_expanded = set()        # tickers currently expanded
if "portfolio_periods" not in st.session_state:
    st.session_state.portfolio_periods = {}            # ticker -> selected period

# ── Add-ticker searchbox (type-as-you-go, EODHD /search) ──────────────────────
# st_searchbox calls _ticker_search() on every keystroke (debounced) and
# shows the returned suggestions in a dropdown beneath the input. Picking
# one adds it to the portfolio immediately — no extra confirm click.
selected_ticker = st_searchbox(
    search_function=_ticker_search,
    placeholder="🔍 Type a ticker or company name (e.g. AAPL, Rheinmetall, S&P 500)",
    label=None,
    clear_on_submit=True,
    key="ticker_searchbox",
)

if selected_ticker:
    norm = _normalize_ticker(selected_ticker)
    if norm and norm not in st.session_state.portfolio_tickers:
        st.session_state.portfolio_tickers.append(norm)
        _save_portfolio(st.session_state.portfolio_tickers)
        st.success(f"Added **{norm}** to portfolio.")
        st.rerun()
    elif norm in st.session_state.portfolio_tickers:
        st.info(f"**{norm}** is already in your portfolio.")

# ── Top bar ───────────────────────────────────────────────────────────────────
top_l, top_m, top_r = st.columns([5, 1, 1])
with top_l:
    if st.session_state.portfolio_tickers:
        st.markdown(
            f"**{len(st.session_state.portfolio_tickers)}** ticker"
            f"{'s' if len(st.session_state.portfolio_tickers) != 1 else ''} tracked"
        )
with top_m:
    if st.button("⏷ Expand all", use_container_width=True,
                 disabled=not st.session_state.portfolio_tickers):
        st.session_state.portfolio_expanded = set(st.session_state.portfolio_tickers)
        st.rerun()
with top_r:
    if st.button("🔄 Refresh", use_container_width=True):
        _fetch_snapshot.clear()
        _fetch_history.clear()
        _fetch_news.clear()
        _fetch_next_earnings.clear()
        st.rerun()

# Tight CSS for the rows — pulls Streamlit's default column padding tighter
# so an entire ticker fits on a single visual line.
st.markdown(
    """
    <style>
      .pf-cell {
        font-size: 13px;
        line-height: 1.2;
        padding: 4px 0 4px 0;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .pf-name    { font-weight: 600; color: #1B3F6E; }
      .pf-tk      { color: #888; font-size: 11px; }
      .pf-val     { font-size: 13px; color: #222; }
      .pf-hdr     { color: #888; font-size: 11px; padding: 2px 0;
                    text-align: center; }
      .pf-earn    { color: #1B3F6E; font-size: 11px; }
      .pf-earn-na { color: #888; font-size: 11px; font-style: italic; }

      /* Tight gutter — pull all columns hard together. */
      div[data-testid="column"] {
        padding-left: 1px !important;
        padding-right: 1px !important;
      }

      /* All buttons: rounded corners, centred symbol, subtle border, hover. */
      div[data-testid="stButton"] > button {
        border-radius: 8px !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
        text-align: center !important;
        padding: 0 8px !important;
        min-height: 30px !important;
        line-height: 1 !important;
      }

      /* Action buttons in the last two columns of a row become small
         rounded squares with a centred glyph. */
      div[data-testid="stHorizontalBlock"]
        > div[data-testid="column"]:nth-last-child(-n+2)
        > div[data-testid="stVerticalBlock"]
        div[data-testid="stButton"] > button {
        width: 30px !important;
        height: 30px !important;
        min-width: 30px !important;
        min-height: 30px !important;
        padding: 0 !important;
        border-radius: 8px !important;
        border: 1px solid #C8D2DD !important;
        background: #FFFFFF !important;
        color: #1B3F6E !important;
        font-size: 14px !important;
        margin: 0 auto !important;
      }
      div[data-testid="stHorizontalBlock"]
        > div[data-testid="column"]:nth-last-child(-n+2)
        > div[data-testid="stVerticalBlock"]
        div[data-testid="stButton"] > button:hover {
        background: #EEF5FB !important;
        border-color: #1B3F6E !important;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

# Column widths — must match between header and rows.
#   Name  Tk   Earn  Price MCap  P/E   ROE   EBIT  YTD   ▾    ✕
_COL_W = [1.9, 0.8, 1.3, 1.3, 1.2, 1.0, 1.1, 1.0, 1.1, 0.35, 0.35]

# Header row labels (only when there's at least one card)
if st.session_state.portfolio_tickers:
    h_cols = st.columns(_COL_W)
    labels = ["Name", "Ticker", "Earnings", "Price", "Mkt Cap",
              "P/E", "ROE", "EBIT M.", "YTD", "", ""]
    for i, lab in enumerate(labels):
        # All header labels centred (pf-hdr already has text-align:center).
        h_cols[i].markdown(
            f"<div class='pf-hdr'>{lab}</div>",
            unsafe_allow_html=True,
        )
    st.markdown("<hr style='margin:0 0 4px 0; border-color:#E0E5EC;'>",
                unsafe_allow_html=True)

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
        is_expanded = ticker in st.session_state.portfolio_expanded
        ytd_text, ytd_color = _fmt_signed_pct(snap.get("ytd_pct"))
        next_earnings = _fetch_next_earnings(ticker)

        # ── Single-line compact row ──────────────────────────────────────────
        cols = st.columns(_COL_W)

        # 0. Name (truncated hard so it stays on one line — full name in tooltip)
        name_disp = (snap["name"] or ticker)[:22]
        cols[0].markdown(
            f"<div class='pf-cell pf-name' title='{snap['name']}'>"
            f"{name_disp}</div>",
            unsafe_allow_html=True,
        )

        # 1. Ticker symbol (small grey, centred)
        cols[1].markdown(
            f"<div class='pf-cell pf-tk' style='text-align:center;'>"
            f"{ticker}</div>",
            unsafe_allow_html=True,
        )

        # 2. Earnings date (or 'no date'), centred
        if next_earnings:
            earn = f"📅 {next_earnings}"
            earn_cls = "pf-earn"
        else:
            earn = "📅 no date"
            earn_cls = "pf-earn-na"
        cols[2].markdown(
            f"<div class='pf-cell {earn_cls}' style='text-align:center;'>"
            f"{earn}</div>",
            unsafe_allow_html=True,
        )

        # 3-7. Numeric metrics — centred to line up with centred headers.
        def _num_cell(text: str, color: str = "#222",
                      bold: bool = False) -> str:
            wt = "font-weight:600;" if bold else ""
            return (f"<div class='pf-cell pf-val' "
                    f"style='text-align:center;color:{color};{wt}'>"
                    f"{text}</div>")

        cols[3].markdown(_num_cell(_fmt_price(snap['price'], snap['currency'])),
                          unsafe_allow_html=True)
        cols[4].markdown(_num_cell(_fmt_money(snap['market_cap'])),
                          unsafe_allow_html=True)
        cols[5].markdown(_num_cell(_fmt_ratio(snap['pe'])),
                          unsafe_allow_html=True)
        cols[6].markdown(_num_cell(_fmt_pct(snap['roe'])),
                          unsafe_allow_html=True)
        cols[7].markdown(_num_cell(_fmt_pct(snap['ebit_margin'])),
                          unsafe_allow_html=True)
        # 8. YTD (coloured + bold)
        cols[8].markdown(_num_cell(ytd_text, color=ytd_color, bold=True),
                          unsafe_allow_html=True)

        # 9. Expand / collapse toggle
        with cols[9]:
            arrow = "▴" if is_expanded else "▾"
            if st.button(arrow, key=f"toggle_{ticker}", help="Expand / collapse",
                         use_container_width=True):
                if is_expanded:
                    st.session_state.portfolio_expanded.discard(ticker)
                else:
                    st.session_state.portfolio_expanded.add(ticker)
                st.rerun()

        # 10. Remove
        with cols[10]:
            if st.button("✕", key=f"del_{ticker}", help="Remove from portfolio",
                         use_container_width=True):
                st.session_state.portfolio_tickers.remove(ticker)
                st.session_state.portfolio_expanded.discard(ticker)
                _save_portfolio(st.session_state.portfolio_tickers)
                st.rerun()

        # ── Expanded detail section ──────────────────────────────────────────
        if is_expanded:
            with st.container(border=True):
                # Top strip — sector + recommendation badge
                strip_l, strip_r = st.columns([5, 1])
                with strip_l:
                    sector_label = snap.get("sector") or ""
                    extra_meta = f"  ·  {sector_label}" if sector_label else ""
                    st.markdown(
                        f"<div style='color:#888;font-size:13px;'>{ticker}{extra_meta}</div>",
                        unsafe_allow_html=True,
                    )
                with strip_r:
                    st.markdown(
                        f"<div style='text-align:right;'>"
                        f"<span style='color:#888;font-size:12px;'>Rec:</span>"
                        f"&nbsp;<span style='color:{rec_color};font-weight:700;font-size:16px;'>"
                        f"{rec_label}</span></div>",
                        unsafe_allow_html=True,
                    )

                # Period selector
                current_period = st.session_state.portfolio_periods.get(ticker, DEFAULT_PERIOD)
                if current_period not in PERIODS:
                    current_period = DEFAULT_PERIOD
                sel_period = st.radio(
                    "Chart period",
                    options=PERIODS,
                    index=PERIODS.index(current_period),
                    horizontal=True,
                    label_visibility="collapsed",
                    key=f"period_{ticker}",
                )
                if sel_period != current_period:
                    st.session_state.portfolio_periods[ticker] = sel_period

                # Chart — use Altair so the Y axis fits the price range
                # (st.line_chart anchors Y at 0, which makes intraday charts
                # look like a flat line).
                hist = _fetch_history(ticker, sel_period)
                if hist is None or hist.empty:
                    st.warning(f"No EODHD price history available for **{sel_period}**.")
                else:
                    try:
                        df_chart = hist.reset_index()
                        time_col = df_chart.columns[0]   # "Date" or "Time"
                        low_p  = float(df_chart["Close"].min())
                        high_p = float(df_chart["Close"].max())
                        first_px = float(df_chart["Close"].iloc[0])
                        last_px  = float(df_chart["Close"].iloc[-1])
                        chg_pct  = (last_px / first_px - 1) * 100 if first_px else 0
                        abs_chg  = last_px - first_px

                        # Add a small padding around the range so the line
                        # doesn't hug the chart edges.
                        span = max(high_p - low_p, abs(low_p) * 0.001)
                        pad  = span * 0.08
                        y_min = low_p - pad
                        y_max = high_p + pad

                        # Line + heading colour: green if up, red if down
                        line_color = "#1A7E3D" if chg_pct >= 0 else "#B83227"
                        arrow      = "▲" if chg_pct >= 0 else "▼"

                        # ── Prominent period-change banner above the chart ──
                        ccy = snap.get("currency") or ""
                        st.markdown(
                            f"<div style='display:flex;align-items:baseline;"
                            f"justify-content:space-between;margin:8px 0 4px 0;"
                            f"padding:6px 10px;background:#F4F8FC;"
                            f"border-left:4px solid {line_color};"
                            f"border-radius:4px;'>"
                            f"<div>"
                            f"<span style='color:#666;font-size:13px;'>"
                            f"{sel_period} change</span>&nbsp;&nbsp;"
                            f"<span style='color:{line_color};font-weight:700;"
                            f"font-size:22px;'>{arrow} {chg_pct:+.2f}%</span>"
                            f"&nbsp;&nbsp;"
                            f"<span style='color:{line_color};font-size:15px;"
                            f"font-weight:600;'>"
                            f"({abs_chg:+,.2f} {ccy})</span>"
                            f"</div>"
                            f"<div style='color:#888;font-size:12px;'>"
                            f"{first_px:,.2f} → {last_px:,.2f}"
                            f"</div>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )

                        x_type = "T"   # temporal works for both Date + Time
                        chart = (
                            alt.Chart(df_chart)
                               .mark_line(strokeWidth=2)
                               .encode(
                                   x=alt.X(f"{time_col}:{x_type}", title=""),
                                   y=alt.Y(
                                       "Close:Q",
                                       title="",
                                       scale=alt.Scale(
                                           domain=[y_min, y_max],
                                           zero=False,
                                           nice=False,
                                       ),
                                   ),
                                   tooltip=[
                                       alt.Tooltip(f"{time_col}:{x_type}",
                                                   title="Time"),
                                       alt.Tooltip("Close:Q",
                                                   title="Price",
                                                   format=",.2f"),
                                   ],
                                   color=alt.value(line_color),
                               )
                               .properties(height=280)
                               .configure_view(strokeWidth=0)
                        )
                        st.altair_chart(chart, use_container_width=True)

                        sc1, sc2 = st.columns(2)
                        sc1.caption(f"**{sel_period} low:** {low_p:,.2f}")
                        sc2.caption(f"**{sel_period} high:** {high_p:,.2f}")
                    except Exception as e:
                        st.warning(f"Chart rendering failed: {e}")

                # News
                with st.expander("📰 Latest news", expanded=False):
                    news_items = _fetch_news(ticker, limit=15)
                    if not news_items:
                        st.warning("No EODHD news available for this ticker.")
                    else:
                        for n in news_items:
                            title   = n.get("title")   or "(no title)"
                            link    = n.get("link")    or ""
                            date    = n.get("date")    or ""
                            content = n.get("content") or ""
                            date_short = str(date)[:16].replace("T", " ")

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

        # Thin inter-card separator
        st.markdown(
            "<hr style='margin:2px 0; border:none; "
            "border-top:1px solid #EDF1F5;'>",
            unsafe_allow_html=True,
        )

st.markdown("&nbsp;", unsafe_allow_html=True)
st.caption(
    "All data: **EODHD All-In-One** · Snapshot cached 15 min · "
    "History 30 min · News 30 min · Earnings dates 6 h · "
    "Recommendation is a rule-based heuristic on P/E + ROE + EBIT margin "
    "— not investment advice."
)
