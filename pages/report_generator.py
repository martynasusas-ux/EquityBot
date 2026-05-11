"""
report_generator.py — Report Generator page for Your Humble EquityBot.
"""
from __future__ import annotations
import base64
import logging
import os
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

import streamlit as st

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Auth guard — must be first, blocks unauthenticated direct URL access ──────
from utils.auth import require_auth
require_auth()

from config import LLM_PROVIDER, LLM_MODEL, OUTPUTS_DIR, ADVERSARIAL_MODE as _CFG_ADV_MODE
from agents.llm_client import LLMClient
from data_sources.data_manager import DataManager
from data_sources.base import CompanyData
from framework_manager import FrameworkManager

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Tighten top padding */
.block-container { padding-top: 1.5rem; }

/* Report type cards */
.report-card {
    border: 1px solid #BBCCDD;
    border-radius: 8px;
    padding: 14px 16px;
    background: #EEF5FB;
    margin-bottom: 6px;
    cursor: pointer;
}
.report-card h4 { color: #1B3F6E; margin: 0 0 4px 0; font-size: 14px; }
.report-card p  { color: #555555; margin: 0; font-size: 12px; line-height: 1.4; }

/* Metric chips */
.metric-chip {
    display: inline-block;
    background: #D6E8F7;
    color: #1B3F6E;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 12px;
    font-weight: 600;
    margin: 2px;
}
.rec-buy  { background: #D4EDDA; color: #1A7E3D; }
.rec-hold { background: #FDEBD0; color: #D68910; }
.rec-sell { background: #FADBD8; color: #C0392B; }

/* Divider */
hr { margin: 12px 0; border-color: #BBCCDD; }
</style>
""", unsafe_allow_html=True)


# ── Utility formatters ───────────────────────────────────────────────────────
def _fmt_b(v) -> str:
    if v is None: return "n/a"
    return f"{v/1000:,.1f}B" if abs(v) >= 1000 else f"{v:,.0f}M"


def _show_token_usage(usage: dict) -> None:
    """Display a compact token usage chip after an LLM call (Claude only)."""
    if not usage:
        return
    inp   = usage.get("input_tokens", 0)
    out   = usage.get("output_tokens", 0)
    hit   = usage.get("cache_read_input_tokens", 0)
    wrote = usage.get("cache_creation_input_tokens", 0)

    if hit:
        # Cache was warm — show savings
        saved_pct = round(hit / max(inp + hit, 1) * 100)
        st.caption(
            f"🪙 Tokens — in: {inp:,} · out: {out:,} · "
            f"📦 cache read: {hit:,} ({saved_pct}% saved) · "
            f"cache write: {wrote:,}"
        )
    elif wrote:
        # First call — schema written to cache for next run
        st.caption(
            f"🪙 Tokens — in: {inp:,} · out: {out:,} · "
            f"📦 cache write: {wrote:,} (saved for next run)"
        )
    else:
        st.caption(f"🪙 Tokens — in: {inp:,} · out: {out:,}")


# ── Ticker search helper ──────────────────────────────────────────────────────
def _search_tickers(query: str, max_results: int = 5) -> list[dict]:
    """
    Search yfinance for tickers matching a company name or partial ticker.
    Returns list of {symbol, name, exchange} dicts. Empty list on any error.
    """
    if not query or len(query) < 2:
        return []
    try:
        import yfinance as yf
        results = yf.Search(query, max_results=max_results)
        quotes  = results.quotes if hasattr(results, "quotes") else []
        out = []
        for q in quotes:
            sym   = q.get("symbol", "")
            name  = q.get("shortname") or q.get("longname") or ""
            exch  = q.get("exchDisp") or q.get("exchange") or ""
            qtype = q.get("quoteType", "")
            if sym and qtype in ("EQUITY", "ETF", "INDEX", ""):
                out.append({"symbol": sym, "name": name, "exchange": exch})
        return out[:max_results]
    except Exception:
        return []


# ── Framework registry (loaded dynamically from frameworks/ directory) ────────
def _build_report_types() -> dict:
    """Build the REPORT_TYPES dict from the FrameworkManager."""
    fm = FrameworkManager()
    frameworks = fm.list()
    result = {}
    for fw in frameworks:
        result[fw.id] = {
            "label": f"{fw.icon} {fw.name}",
            "short": fw.name,
            "desc":  fw.description,
            "pages": "PDF report" if fw.uses_builtin_runner else "HTML report",
            "is_builtin": fw.is_builtin,
            "uses_builtin_runner": fw.uses_builtin_runner,
        }
    return result

REPORT_TYPES = _build_report_types()

# Builtin framework ids (for runner dispatch logic)
_BUILTIN_IDS = {"overview", "fisher", "gravity", "kepler_summary"}

EXCHANGE_HINTS = {
    "Amsterdam (AEX)":   ".AS  e.g. WKL.AS, ASML.AS",
    "London (LSE)":      ".L   e.g. AZN.L, SHEL.L, INF.L",
    "Stockholm (STO)":   ".ST  e.g. ATCO-A.ST, SWED-A.ST",
    "Frankfurt (XETRA)": ".DE  e.g. SAP.DE, BAYN.DE",
    "Helsinki (OMX)":    ".HE  e.g. NOKIA.HE, SAMPO.HE",
    "Paris (EPA)":       ".PA  e.g. MC.PA, SAN.PA",
    "Toronto (TSX)":     ".TO  e.g. TRI.TO, RY.TO",
    "Tokyo (TSE)":       ".T   e.g. 7203.T, 6758.T",
    "US (NYSE/NASDAQ)":  "No suffix — AAPL, MSFT, V, MCO",
}


# ── Natural-language intent parser ────────────────────────────────────────────

def _parse_intent_regex(q: str) -> dict:
    """Fast regex-only fallback: extract ticker, framework and mode."""
    import re
    q_up = q.upper()
    q_lo = q.lower()
    result: dict = {"ticker": None, "framework_id": None,
                    "mode": "equity", "force_refresh": False}

    # Ticker: index (^OMXH25) > suffix (NOKIA.HE) > bare caps
    m = re.search(r"\^[A-Z0-9]+", q_up)
    if m:
        result["ticker"] = m.group()
    else:
        m = re.search(r"\b([A-Z]{1,6}\.[A-Z]{1,3})\b", q_up)
        if m:
            result["ticker"] = m.group(1)

    # Framework keywords
    fw_hints = {
        "gravity":  ["gravity", "taxer", "choke", "toll"],
        "fisher":   ["fisher", "scuttlebutt", "philip fisher"],
        "overview": ["overview", "helmer", "7 power", "seven power"],
    }
    for fw_id, kws in fw_hints.items():
        if any(k in q_lo for k in kws):
            result["framework_id"] = fw_id
            break

    # Mode
    if result["ticker"] and result["ticker"].startswith("^"):
        screen_kws = ["screen", "constituent", "compan", "stock", "member",
                      "all ", "each ", "list of", "run "]
        result["mode"] = (
            "universe_screen"
            if any(k in q_lo for k in screen_kws)
            else "index_overview"
        )

    # Force refresh
    if any(k in q_lo for k in ["careful", "fresh data", "re-fetch",
                                "refetch", "new data"]):
        result["force_refresh"] = True

    return result


def _parse_intent(query: str) -> dict:
    """
    Parse a free-text query into structured intent via regex + LLM fallback.
    Returns dict: {ticker, framework_id, mode, force_refresh}.
    Returns {} if the input looks like a plain ticker symbol (no whitespace).
    """
    import re
    q = query.strip()
    if not q or not re.search(r"\s", q):
        return {}   # plain ticker — nothing to parse

    # Regex pass
    intent = _parse_intent_regex(q)

    # Enough info from regex alone?
    if intent.get("ticker") and intent.get("framework_id"):
        return intent

    # LLM pass for ambiguous queries
    fw_list = "\n".join(
        f"  {k}: {REPORT_TYPES[k]['short']}" for k in REPORT_TYPES
    )
    system = (
        "You extract structured financial analysis intent from natural language. "
        "Reply with valid JSON only — no markdown, no explanation."
    )
    prompt = (
        f'Query: "{q}"\n\n'
        f'Available frameworks:\n{fw_list}\n\n'
        'Return JSON:\n'
        '{\n'
        '  "ticker": "<Yahoo Finance ticker or null>",\n'
        '  "framework_id": "<exact framework id from list or null>",\n'
        '  "mode": "<equity|index_overview|universe_screen>",\n'
        '  "force_refresh": <true|false>\n'
        '}\n\n'
        'Rules:\n'
        '- Index ticker + analyse/screen its companies → mode=universe_screen\n'
        '- Index overview/performance/composition only → mode=index_overview\n'
        '- Single stock analysis → mode=equity\n'
        '- "carefully", "fresh data", "re-fetch" → force_refresh=true\n'
        '- framework_id must exactly match one of the ids listed'
    )
    try:
        llm = LLMClient()
        if not llm.check_configured()[0]:
            return intent
        result = llm.generate_json(prompt, system, max_tokens=200)
        # Merge: LLM wins, fill gaps with regex
        for k in ("ticker", "framework_id", "mode", "force_refresh"):
            if not result.get(k):
                result[k] = intent.get(k)
        if result.get("framework_id") not in REPORT_TYPES:
            result["framework_id"] = intent.get("framework_id")
        return result
    except Exception:
        return intent


# ── Session state ─────────────────────────────────────────────────────────────
if "report_result" not in st.session_state:
    st.session_state.report_result = None   # {pdf_path, company, analysis, report_type}
if "recent_reports" not in st.session_state:
    st.session_state.recent_reports = []    # list of {label, path, ts}
if "error_msg" not in st.session_state:
    st.session_state.error_msg = None


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📊 Your Humble EquityBot")
    st.caption("Value investing · Decade-scale horizon · Three frameworks")
    st.divider()

    # LLM status
    llm = LLMClient()
    ok, msg = llm.check_configured()
    if ok:
        st.success(f"✓ {msg}", icon="🤖")
    else:
        st.error(f"⚠ {msg}", icon="🔑")

    st.divider()

    # Exchange suffix reference
    with st.expander("🌍 Exchange ticker formats", expanded=False):
        for exch, hint in EXCHANGE_HINTS.items():
            st.markdown(f"**{exch}**  \n`{hint}`")

    st.divider()

    # Recent reports
    if st.session_state.recent_reports:
        st.markdown("#### Recent Reports")
        for r in reversed(st.session_state.recent_reports[-5:]):
            with open(r["path"], "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            st.download_button(
                label=r["label"],
                data=base64.b64decode(b64),
                file_name=Path(r["path"]).name,
                mime="application/pdf",
                key=f"dl_{r['ts']}",
                use_container_width=True,
            )
        st.divider()

    # ── Data waterfall ────────────────────────────────────────────────────
    with st.expander("📡 Data Sources & Waterfall", expanded=True):
        st.markdown("""
<style>
.wf-tier   { font-size:11px; font-weight:700; color:#888; letter-spacing:.06em;
             text-transform:uppercase; margin:10px 0 2px 0; }
.wf-row    { display:flex; align-items:flex-start; gap:7px; margin:3px 0; }
.wf-badge  { flex-shrink:0; font-size:10px; font-weight:700; padding:1px 6px;
             border-radius:3px; margin-top:1px; }
.wf-paid   { background:#1B3F6E; color:#fff; }
.wf-free   { background:#D6E8F7; color:#1B3F6E; }
.wf-ctx    { background:#EEF5FB; color:#555; }
.wf-body   { font-size:12px; line-height:1.4; color:#333; }
.wf-body b { color:#111; }
.wf-arrow  { color:#BBCCDD; font-size:13px; margin:1px 0 1px 10px; }
.wf-miss   { font-size:11px; color:#888; margin:4px 0 0 0; }
</style>

<div class="wf-tier">① Always — market skeleton</div>
<div class="wf-row">
  <span class="wf-badge wf-free">FREE</span>
  <div class="wf-body"><b>yfinance</b><br>Current price · shares · live ratios</div>
</div>

<div class="wf-arrow">↓</div>

<div class="wf-tier">② Primary fundamentals — all markets</div>
<div class="wf-row">
  <span class="wf-badge wf-paid">PAID</span>
  <div class="wf-body"><b>EODHD</b> Fundamentals Feed<br>
  Overrides annual income · balance sheet · cash flow<br>
  65+ exchanges · 20–40 yr history</div>
</div>

<div class="wf-arrow">↓</div>

<div class="wf-tier">③ US depth — fill-only after EODHD</div>
<div class="wf-row">
  <span class="wf-badge wf-free">FREE</span>
  <div class="wf-body"><b>SEC EDGAR</b><br>US only · direct SEC filings</div>
</div>

<div class="wf-arrow">↓</div>

<div class="wf-tier">④ Last resort — if EODHD unavailable</div>
<div class="wf-row">
  <span class="wf-badge wf-free">FREE</span>
  <div class="wf-body"><b>Alpha Vantage</b><br>25 calls/day · non-US only</div>
</div>

<hr style="margin:10px 0; border-color:#EEE;">

<div class="wf-tier">Context — injected into every report</div>
<div class="wf-row">
  <span class="wf-badge wf-ctx">FREE</span>
  <div class="wf-body"><b>FRED</b> — rates · CPI · credit spreads</div>
</div>
<div class="wf-row">
  <span class="wf-badge wf-ctx">FREE</span>
  <div class="wf-body"><b>NewsAPI</b> — 8 recent headlines</div>
</div>
<div class="wf-row">
  <span class="wf-badge wf-ctx">FREE</span>
  <div class="wf-body"><b>World Bank</b> — GDP · inflation · debt</div>
</div>

<hr style="margin:10px 0; border-color:#EEE;">

<div class="wf-tier">EODHD global coverage</div>
<div class="wf-body" style="font-size:11px; line-height:1.6;">
✅ US · EU · Korea · Taiwan · China<br>
✅ HK · Brazil · Canada · SE Asia · Africa<br>
⚠️ Japan · India · Singapore → yfinance only
</div>
""", unsafe_allow_html=True)

    # ── Frameworks & LLM ──────────────────────────────────────────────────
    with st.expander("🔍 Frameworks & AI", expanded=False):
        st.markdown("""
**Frameworks**
- Philip Fisher (Common Stocks, 1958)
- Hamilton Helmer (7 Powers, 2016)
- Gravity Taxers (choke-point businesses)

**LLM providers**
- Anthropic Claude (primary)
- OpenAI GPT-4o (fallback / adversarial)
""")


# ── Main area ─────────────────────────────────────────────────────────────────
st.markdown("# 📊 Report Generator")
st.markdown(
    "Enter a ticker, pick a framework, and generate a professional investment report. "
    "Reports use real financial data + LLM analysis calibrated to value investing principles."
)
st.divider()

# ── Input form ────────────────────────────────────────────────────────────────
col_left, col_right = st.columns([1.4, 1], gap="large")

with col_left:
    st.markdown("#### Ticker  *or describe what to analyse*")
    ticker_input = st.text_input(
        "Ticker or natural language query",
        placeholder=(
            "e.g.  NOKIA.HE  ·  ^OMXH25  ·  "
            "'Run Gravity model for OMX Helsinki 25 constituents, no market cap constraint'"
        ),
        label_visibility="collapsed",
        key="ticker_input",
    ).strip()

    # Live ticker suggestions — fires when the input looks like a single company name
    _raw_input = st.session_state.get("ticker_input", "").strip()
    _is_nl_query = " " in _raw_input and len(_raw_input) > 8
    _looks_like_name = (
        _raw_input
        and len(_raw_input) > 4
        and not _is_nl_query                          # exclude NL phrases
        and _raw_input.replace(" ", "").isalpha()
        and "." not in _raw_input
        and not _raw_input.startswith("^")
    )
    if _looks_like_name:
        _suggestions = _search_tickers(_raw_input, max_results=4)
        if _suggestions:
            st.caption("**Did you mean one of these?**")
            for _s in _suggestions:
                _lbl = f"`{_s['symbol']}`  {_s['name']}"
                if _s['exchange']:
                    _lbl += f"  ·  {_s['exchange']}"
                st.caption(_lbl)

    # Normalise for display-time checks (full upper only for plain tickers)
    ticker_input = ticker_input.upper() if not _is_nl_query else ticker_input

    # ── Index / ETF detection ─────────────────────────────────────────────────
    # Quick heuristic: ^ prefix = definitely an index.
    # For NL queries, check if a ^ ticker is embedded in the text.
    import re as _re_idx
    _is_index_ticker = (
        ticker_input.startswith("^")
        or bool(_re_idx.search(r"\^[A-Z0-9]+", ticker_input.upper()))
    )

    if _is_index_ticker:
        st.info(
            "📈 **Market index detected.**  \n"
            "Choose how to analyse it below.",
            icon="📈",
        )
        index_mode = st.radio(
            "Index analysis mode",
            options=["index_overview", "universe_screen"],
            format_func=lambda k: {
                "index_overview":  "📊 Analyse the index  (performance · valuation · composition)",
                "universe_screen": "🔍 Screen constituents through a framework",
            }[k],
            label_visibility="collapsed",
            key="index_mode",
        )
    else:
        index_mode = None

    # ── Report Framework picker ───────────────────────────────────────────────
    # Hide index_overview for equity tickers; hide it also in universe-screen
    # mode (it's selected automatically); show all stock frameworks for screening.
    if _is_index_ticker and index_mode == "index_overview":
        _fw_options = ["index_overview"]
    elif _is_index_ticker and index_mode == "universe_screen":
        _fw_options = [k for k in REPORT_TYPES if k != "index_overview"]
    else:
        _fw_options = [k for k in REPORT_TYPES if k != "index_overview"]

    st.markdown(
        "#### Report Framework"
        if not (_is_index_ticker and index_mode == "index_overview")
        else "#### Framework  *(auto-selected)*"
    )
    report_type = st.radio(
        "Report type",
        options=_fw_options,
        format_func=lambda k: REPORT_TYPES[k]["label"],
        label_visibility="collapsed",
        key="report_type",
        horizontal=False,
        disabled=(_is_index_ticker and index_mode == "index_overview"),
    )
    rt = REPORT_TYPES[report_type]
    builtin_badge = "" if rt.get("is_builtin", True) else "  ·  *Custom*"
    st.caption(f"*{rt['desc']}*  ·  {rt['pages']}{builtin_badge}")

with col_right:
    # Peer tickers — only relevant for Overview
    st.markdown("#### Peer Tickers  *(Overview only, optional)*")
    peers_input = st.text_input(
        "Peer tickers",
        placeholder="REL.L  TRI.TO  MSFT  (space-separated, up to 6)",
        label_visibility="collapsed",
        disabled=(report_type != "overview"),
        key="peers_input",
    )

    st.markdown("#### Options")
    force_refresh = st.checkbox(
        "Force refresh data cache",
        value=False,
        help="Bypass the 24-hour cache and re-fetch all data from source.",
    )

    # Adversarial mode — needs both Claude and OpenAI keys
    _adv_available = bool(
        os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("OPENAI_API_KEY")
    )
    adversarial_on = st.checkbox(
        "⚔ Adversarial Mode  (Claude + GPT-4o)",
        value=_CFG_ADV_MODE,
        disabled=not _adv_available,
        help=(
            "Run the analysis twice — once with Claude, once with GPT-4o — "
            "then cross-critique and merge. Adds an extra review page to the PDF. "
            "Requires both ANTHROPIC_API_KEY and OPENAI_API_KEY."
            if _adv_available
            else "Requires both ANTHROPIC_API_KEY and OPENAI_API_KEY in .env."
        ),
    )
    st.markdown(" ")

    # Generate button — label adapts to mode
    if _is_index_ticker and index_mode == "index_overview":
        _btn_label = f"Generate Index Overview"
    elif _is_index_ticker and index_mode == "universe_screen":
        _btn_label = f"Screen {ticker_input or 'Index'} Constituents"
    else:
        _btn_label = f"Generate {rt['short']} Report"

    generate_clicked = st.button(
        _btn_label,
        type="primary",
        use_container_width=True,
        disabled=not ticker_input.strip() or not ok,
    )
    if not ok:
        st.caption("⚠ Add your API key to .env to enable report generation.")
    if not ticker_input.strip():
        st.caption("Enter a ticker or describe what to analyse above.")
    elif _is_nl_query:
        st.caption("💡 Natural language detected — click Generate to interpret and run.")

st.divider()


# ── Report generation ─────────────────────────────────────────────────────────
if generate_clicked and ticker_input:
    st.session_state.report_result = None
    st.session_state.error_msg = None

    # ── Natural-language interpretation ───────────────────────────────────────
    _orig_query   = ticker_input
    _nl_intent: dict = {}

    if " " in _orig_query:          # has spaces → treat as NL
        with st.spinner("🔍 Interpreting query…"):
            _nl_intent = _parse_intent(_orig_query)

    # Compute effective values (parsed wins over form defaults)
    _eff_ticker  = (_nl_intent.get("ticker") or _orig_query).strip().upper()
    _eff_fw      = (
        _nl_intent["framework_id"]
        if _nl_intent.get("framework_id") in REPORT_TYPES
        else report_type
    )
    _eff_refresh = force_refresh or bool(_nl_intent.get("force_refresh"))

    # Mode: parsed intent > form radio > auto-detect from ticker
    _parsed_mode = _nl_intent.get("mode") if _nl_intent else None
    if _parsed_mode:
        _eff_mode = _parsed_mode if _eff_ticker.startswith("^") else None
    elif _eff_ticker.startswith("^"):
        _eff_mode = index_mode or "universe_screen"
    else:
        _eff_mode = None

    # Shadow outer variables — all downstream code uses these
    ticker_input  = _eff_ticker
    report_type   = _eff_fw
    index_mode    = _eff_mode
    force_refresh = _eff_refresh
    rt            = REPORT_TYPES[report_type]

    # Show interpretation summary when NL was used
    if _nl_intent and _nl_intent.get("ticker"):
        _mode_lbl = {
            "equity":          "single equity",
            "index_overview":  "index overview",
            "universe_screen": "screen all constituents",
        }.get(_eff_mode or "equity", _eff_mode or "equity")
        st.info(
            f"🔍 **Interpreted as:** `{_eff_ticker}`  ·  "
            f"**{rt['short']}**  ·  {_mode_lbl}"
            + ("  ·  🔄 force refresh" if _eff_refresh and not force_refresh else ""),
            icon="✅",
        )

    peer_list = [t.strip().upper() for t in peers_input.split() if t.strip()][:6]

    # ── INDEX OVERVIEW mode ───────────────────────────────────────────────────
    if index_mode == "index_overview":
        with st.status(
            f"Generating Index Overview for **{ticker_input}**...",
            expanded=True,
        ) as status:
            try:
                _prog = st.progress(0, text="Initializing…")
                _prog.progress(8, text="📡  Fetching index data…")
                st.write(f"📡  Fetching index/ETF data for **{ticker_input}**…")

                from models.index_runner import IndexRunner
                from data_sources.data_manager import DataManager as _DM

                _idx_data = _DM().get_index(ticker_input, force_refresh=force_refresh)
                st.write(f"✓  **{_idx_data.name}** · {_idx_data.index_type} "
                         f"· Level: {_idx_data.current_level}")
                _prog.progress(20, text="🤖  Running AI analysis — typically 20–60 s…")
                st.write("🤖  Running Index Overview analysis (Claude)…")

                safe = ticker_input.replace("^", "").replace(".", "_")
                date = datetime.now().strftime("%Y-%m-%d")
                pdf_path = str(OUTPUTS_DIR / f"{safe}_index_overview_{date}.html")

                runner = IndexRunner()
                html_path, _idx_analysis = runner.run(
                    ticker_input,
                    output_path=pdf_path,
                    force_refresh=force_refresh,
                )
                _idx_rec = _idx_analysis.get("recommendation", "n/a")
                _prog.progress(95, text="📄  Rendering report…")
                with open(html_path, "r", encoding="utf-8") as _f:
                    _html_content = _f.read()
                _prog.progress(100, text="✅  Report ready!")

                status.update(
                    label=f"✅  Index Overview ready for **{_idx_data.name}** · {_idx_rec}",
                    state="complete", expanded=False,
                )
                st.session_state.report_result = {
                    "pdf_path":    html_path,
                    "company":     None,
                    "index_data":  _idx_data,
                    "analysis":    _idx_analysis,
                    "report_type": "index_overview",
                    "rec":         _idx_rec,
                    "extra":       {"html_content": _html_content},
                    "adversarial": None,
                }
                label = f"{ticker_input} · Index Overview · {date}"
                st.session_state.recent_reports.append({
                    "label": label, "path": html_path,
                    "ts": datetime.now().timestamp(),
                })

            except Exception as e:
                st.session_state.error_msg = str(e)
                status.update(label="❌  Error", state="error", expanded=True)
                st.error(f"**Error:** {e}")

    # ── UNIVERSE SCREEN mode ──────────────────────────────────────────────────
    elif index_mode == "universe_screen":
        with st.status(
            f"Screening **{ticker_input}** constituents through {rt['short']}…",
            expanded=True,
        ) as status:
            try:
                _prog = st.progress(0, text="Initializing…")
                _step_text = st.empty()

                def _universe_progress(pct: int, msg: str) -> None:
                    _prog.progress(min(pct, 99), text=msg)
                    _step_text.write(msg)

                from models.universe_screener import UniverseScreener
                safe_idx = ticker_input.replace("^", "").replace(".", "_")
                safe_fw  = report_type.replace("_", "-")
                date     = datetime.now().strftime("%Y-%m-%d")
                pdf_path = str(OUTPUTS_DIR / f"{safe_idx}_{safe_fw}_universe_{date}.html")

                screener  = UniverseScreener()
                html_path = screener.run(
                    index_ticker=ticker_input,
                    framework_id=report_type,
                    output_path=pdf_path,
                    force_refresh=force_refresh,
                    progress_cb=_universe_progress,
                )
                _prog.progress(100, text="✅  Report ready!")
                with open(html_path, "r", encoding="utf-8") as _f:
                    _html_content = _f.read()

                status.update(
                    label=f"✅  {rt['short']} Universe Screen complete for {ticker_input}",
                    state="complete", expanded=False,
                )
                st.session_state.report_result = {
                    "pdf_path":    html_path,
                    "company":     None,
                    "index_data":  None,
                    "analysis":    {},
                    "report_type": f"universe_{report_type}",
                    "rec":         "n/a",
                    "extra":       {"html_content": _html_content},
                    "adversarial": None,
                }
                label = f"{ticker_input} · {rt['short']} Screen · {date}"
                st.session_state.recent_reports.append({
                    "label": label, "path": html_path,
                    "ts": datetime.now().timestamp(),
                })

            except Exception as e:
                st.session_state.error_msg = str(e)
                status.update(label="❌  Error", state="error", expanded=True)
                st.error(f"**Error:** {e}")

    # ── EQUITY mode (existing flow) ───────────────────────────────────────────
    else:
      with st.status(
        f"Generating {rt['short']} report for **{ticker_input}**...",
        expanded=True,
      ) as status:
        try:
            dm = DataManager()

            # ── Progress bar ──────────────────────────────────────────────────
            _prog = st.progress(0, text="Initializing…")

            # ── Step 1: Data ──────────────────────────────────────────────────
            _prog.progress(5, text="📡  Fetching financial data…")
            st.write(f"📡  Fetching financial data for **{ticker_input}**...")
            company = dm.get(ticker_input, force_refresh=force_refresh)

            # Check we got meaningful data. A missing name alone is not fatal —
            # EODHD now fills it, but if everything is empty we should error out.
            _has_data = bool(
                company.annual_financials
                or company.current_price
                or company.market_cap
            )
            if not _has_data:
                # Genuinely empty — suggest alternatives
                _suggestions = _search_tickers(ticker_input, max_results=4)
                if _suggestions:
                    _hint = ", ".join(
                        f"**{s['symbol']}** ({s['name']})" for s in _suggestions[:3]
                    )
                    raise ValueError(
                        f"No data found for **{ticker_input}**. "
                        f"Did you mean: {_hint}?"
                    )
                else:
                    raise ValueError(
                        f"No data found for ticker **{ticker_input}**. "
                        f"Check the format — e.g. AAPL, WKL.AS, NOKIA.HE. "
                        f"Use the Yahoo Finance ticker symbol, not the company name."
                    )
            # If name is still missing after all sources, fall back to the ticker
            if not company.name:
                company.name = ticker_input

            yrs   = company.year_range()
            compl = company.completeness_pct()
            st.write(f"✓  **{company.name}** · {yrs} · {compl}% complete · "
                     f"sources: {', '.join(company.data_sources)}")
            _prog.progress(18, text="✓  Data loaded")

            # ── Fetch news + country macro ────────────────────────────────────
            _prog.progress(19, text="📰  Fetching recent news…")
            _news_articles = dm.get_news(company.name or ticker_input, ticker_input, max_articles=8)
            _news_block = dm._news.format_for_prompt(_news_articles) if _news_articles else ""
            if _news_articles:
                st.write(f"📰  {len(_news_articles)} recent news articles fetched")

            _country_macro_block = ""
            if company.country:
                # Map full country name to ISO2 code
                _COUNTRY_MAP = {
                    "Germany": "DE", "Finland": "FI", "France": "FR", "Sweden": "SE",
                    "Netherlands": "NL", "United Kingdom": "GB", "Italy": "IT",
                    "Spain": "ES", "Poland": "PL", "Norway": "NO", "Denmark": "DK",
                    "Switzerland": "CH", "Austria": "AT", "Belgium": "BE",
                    "United States": "US", "Japan": "JP", "South Korea": "KR",
                    "China": "CN", "India": "IN", "Brazil": "BR", "Canada": "CA",
                    "Australia": "AU",
                }
                _iso2 = _COUNTRY_MAP.get(company.country, company.country[:2].upper() if company.country else "")
                if _iso2:
                    try:
                        _cmacro = dm.get_country_macro(_iso2)
                        _country_macro_block = dm._wb.format_for_prompt(_cmacro)
                    except Exception:
                        _country_macro_block = ""

            # ── Step 2: LLM ───────────────────────────────────────────────────
            adv_label = " ⚔ adversarial" if adversarial_on else ""
            _prog.progress(22, text=f"🤖  Running AI analysis — typically 30–90 s…")
            st.write(f"🤖  Running {rt['short']} analysis "
                     f"({LLM_PROVIDER}/{LLM_MODEL}{adv_label})...")

            # Shared adversarial engine (instantiated once if needed)
            adv_result = None
            if adversarial_on:
                from agents.adversarial import AdversarialEngine
                _adv_engine = AdversarialEngine()
                st.write("⚔  Adversarial Mode: Claude + GPT-4o will run independently, "
                         "then cross-critique and merge...")

            if report_type == "overview":
                from models.overview import (
                    _build_overview_prompt, _overview_prompt_parts,
                    _calculate_checklist, SYSTEM_PROMPT as SYS,
                )
                cacheable_pfx, dynamic_prompt = _overview_prompt_parts(
                    company, news_block=_news_block, macro_country_block=_country_macro_block
                )

                if adversarial_on:
                    _prog.progress(25, text="⚔  Step 1/4: Claude analysis…")
                    st.write("   → Step 1/4: Claude analysis...")
                    full_prompt = cacheable_pfx + "\n\n" + dynamic_prompt
                    adv_result = _adv_engine.run(full_prompt, SYS, max_tokens=5000,
                                                  report_type="overview")
                    analysis = adv_result.merged
                    rec_note = (
                        f"**{adv_result.primary_rec}** (Claude) vs "
                        f"**{adv_result.secondary_rec}** (GPT-4o) → "
                        f"merged: **{analysis.get('recommendation','?')}**"
                    )
                    st.write(f"✓  Dual-model recommendations: {rec_note}")
                    st.write(f"   Consensus: {len(adv_result.consensus_fields)} fields  ·  "
                             f"Contested: {len(adv_result.contested_fields)} fields")
                else:
                    analysis = llm.generate_json(dynamic_prompt, SYS, max_tokens=5000,
                                                 cacheable_prefix=cacheable_pfx)
                    rec = analysis.get("recommendation", "n/a")
                    st.write(f"✓  Recommendation: **{rec}**")
                    _show_token_usage(llm.last_usage)
                _prog.progress(65, text="✓  AI analysis complete")

                # ── Step 3: Peers ─────────────────────────────────────────────
                _prog.progress(68, text="🔍  Fetching peer data…")
                st.write("🔍  Fetching peer comparison data...")
                peers: dict[str, CompanyData] = {}
                raw_peers = peer_list or [
                    p.get("ticker", "")
                    for p in analysis.get("suggested_peers", [])
                ]
                raw_peers = [t.strip().upper() for t in raw_peers if t.strip()][:6]
                for pt in raw_peers:
                    try:
                        pd_ = dm.get(pt)
                        if pd_.name:
                            peers[pt] = pd_
                    except Exception:
                        pass
                st.write(f"✓  {len(peers)} peers loaded: "
                         f"{', '.join(peers.keys()) or 'none'}")
                _prog.progress(78, text=f"✓  {len(peers)} peers loaded")

                # ── Step 4: Checklist ─────────────────────────────────────────
                checklist = _calculate_checklist(company)
                passed    = sum(1 for c in checklist if c["pass"])
                st.write(f"✓  Checklist: {passed}/{len(checklist)} criteria met")
                _prog.progress(84, text=f"✓  Checklist: {passed}/{len(checklist)}")

                # ── Step 5: PDF ───────────────────────────────────────────────
                _prog.progress(88, text="📄  Rendering PDF…")
                st.write("📄  Rendering PDF...")
                from agents.pdf_overview import OverviewPDFGenerator
                safe = ticker_input.replace(".", "_").replace("-", "_")
                date = datetime.now().strftime("%Y-%m-%d")
                pdf_path = str(OUTPUTS_DIR / f"{safe}_overview_{date}.pdf")
                os.makedirs(OUTPUTS_DIR, exist_ok=True)
                OverviewPDFGenerator().render(company, analysis, peers, checklist,
                                              pdf_path, adv_result=adv_result)
                extra = {"checklist": checklist, "passed": passed}

            elif report_type == "fisher":
                from models.fisher import (
                    _build_fisher_prompt, _fisher_prompt_parts,
                    _validate_analysis, SYSTEM_PROMPT as SYS,
                )
                cacheable_pfx, dynamic_prompt = _fisher_prompt_parts(
                    company, news_block=_news_block, macro_country_block=_country_macro_block
                )

                if adversarial_on:
                    full_prompt = cacheable_pfx + "\n\n" + dynamic_prompt
                    adv_result = _adv_engine.run(full_prompt, SYS, max_tokens=6000,
                                                  report_type="fisher")
                    analysis = _validate_analysis(adv_result.merged)
                    score = analysis.get("fisher_total_score", "?")
                    grade = analysis.get("fisher_grade", "?")
                    st.write(f"✓  Merged Fisher Score: **{score}/75** (Grade {grade}) · "
                             f"Claude: {adv_result.primary_rec} / "
                             f"GPT-4o: {adv_result.secondary_rec}")
                    st.write(f"   Consensus: {len(adv_result.consensus_fields)} fields  ·  "
                             f"Contested: {len(adv_result.contested_fields)} fields")
                else:
                    analysis = llm.generate_json(dynamic_prompt, SYS, max_tokens=6000,
                                                 cacheable_prefix=cacheable_pfx)
                    analysis = _validate_analysis(analysis)
                    score = analysis.get("fisher_total_score", "?")
                    grade = analysis.get("fisher_grade", "?")
                    rec   = analysis.get("recommendation", "n/a")
                    st.write(f"✓  Fisher Score: **{score}/75** (Grade {grade}) · Rec: **{rec}**")
                    _show_token_usage(llm.last_usage)
                _prog.progress(75, text="✓  Fisher analysis complete")

                _prog.progress(88, text="📄  Rendering PDF…")
                st.write("📄  Rendering PDF...")
                from agents.pdf_fisher import FisherPDFGenerator
                safe = ticker_input.replace(".", "_").replace("-", "_")
                date = datetime.now().strftime("%Y-%m-%d")
                pdf_path = str(OUTPUTS_DIR / f"{safe}_fisher_{date}.pdf")
                os.makedirs(OUTPUTS_DIR, exist_ok=True)
                FisherPDFGenerator().render(company, analysis, pdf_path,
                                            adv_result=adv_result)
                extra = {"score": score, "grade": grade}

            elif report_type == "kepler_summary":
                # ── Kepler-style analyst summary sheet ────────────────────────
                from models.kepler_summary import (
                    _kepler_prompt_parts, SYSTEM_PROMPT as SYS,
                )
                cacheable_pfx, dynamic_prompt = _kepler_prompt_parts(
                    company, news_block=_news_block,
                    macro_country_block=_country_macro_block
                )
                _prog.progress(25, text="🤖  Generating target price & recommendation…")
                st.write("🤖  Generating target price and recommendation (Claude)…")
                analysis = llm.generate_json(
                    dynamic_prompt, SYS, max_tokens=400,
                    cacheable_prefix=cacheable_pfx,
                )
                tp  = analysis.get("target_price", "n/a")
                rec = analysis.get("recommendation", "n/a")
                st.write(f"✓  Recommendation: **{rec}** · Target: **{tp} {company.currency_price or ''}**")
                _show_token_usage(llm.last_usage)
                _prog.progress(75, text="✓  Analysis complete")

                _prog.progress(88, text="📄  Rendering Kepler Summary PDF…")
                st.write("📄  Rendering Kepler Summary PDF…")
                from agents.pdf_kepler import KeplerPDFGenerator
                safe = ticker_input.replace(".", "_").replace("-", "_")
                date = datetime.now().strftime("%Y-%m-%d")
                pdf_path = str(OUTPUTS_DIR / f"{safe}_kepler_{date}.pdf")
                os.makedirs(OUTPUTS_DIR, exist_ok=True)
                KeplerPDFGenerator().render(company, analysis, pdf_path)
                extra = {"target_price": tp, "valuation_method": analysis.get("valuation_method", "")}

            elif report_type not in _BUILTIN_IDS:
                # ── User-created / custom framework ───────────────────────────
                from models.generic_runner import GenericRunner
                fw_config = FrameworkManager().get(report_type)
                if fw_config is None:
                    raise ValueError(f"Framework '{report_type}' not found.")

                _prog.progress(25, text=f"🤖  Running '{fw_config.name}' AI analysis — typically 30–90 s…")
                st.write(f"🤖  Running '{fw_config.name}' analysis (Claude)…")
                runner = GenericRunner()
                safe   = ticker_input.replace(".", "_").replace("-", "_")
                import re as _re
                fw_slug = _re.sub(r"[^a-z0-9]+", "_", fw_config.name.lower()).strip("_")[:20]
                date   = datetime.now().strftime("%Y-%m-%d")
                pdf_path = str(OUTPUTS_DIR / f"{safe}_{fw_slug}_{date}.html")

                html_path = runner.run(
                    ticker_input, fw_config,
                    peer_tickers=peer_list or None,
                    force_refresh=force_refresh,
                    output_path=pdf_path,
                )
                _prog.progress(88, text="📄  Rendering report…")
                # Read HTML for inline display
                with open(html_path, "r", encoding="utf-8") as _f:
                    _html_content = _f.read()
                analysis = {}     # no structured analysis for custom frameworks yet
                extra    = {"html_content": _html_content}

            else:  # gravity
                from models.gravity import (
                    _build_gravity_prompt, _gravity_prompt_parts,
                    _validate_analysis, SYSTEM_PROMPT as SYS,
                )
                cacheable_pfx, dynamic_prompt = _gravity_prompt_parts(
                    company, news_block=_news_block, macro_country_block=_country_macro_block
                )

                if adversarial_on:
                    full_prompt = cacheable_pfx + "\n\n" + dynamic_prompt
                    adv_result = _adv_engine.run(full_prompt, SYS, max_tokens=6000,
                                                  report_type="gravity")
                    analysis = _validate_analysis(adv_result.merged)
                    score = analysis.get("total_gravity_score", "?")
                    grade = analysis.get("gravity_grade", "?")
                    pp    = analysis.get("revenue_model", {}).get("pricing_power", "?")
                    st.write(f"✓  Merged Gravity Score: **{score}/50** (Grade {grade}) · "
                             f"Pricing Power: {pp}")
                    st.write(f"   Claude: {adv_result.primary_rec} / "
                             f"GPT-4o: {adv_result.secondary_rec}  ·  "
                             f"Contested: {len(adv_result.contested_fields)} fields")
                else:
                    analysis = llm.generate_json(dynamic_prompt, SYS, max_tokens=6000,
                                                 cacheable_prefix=cacheable_pfx)
                    analysis = _validate_analysis(analysis)
                    score = analysis.get("total_gravity_score", "?")
                    grade = analysis.get("gravity_grade", "?")
                    rec   = analysis.get("recommendation", "n/a")
                    pp    = analysis.get("revenue_model", {}).get("pricing_power", "?")
                    st.write(f"✓  Gravity Score: **{score}/50** (Grade {grade}) · "
                             f"Pricing Power: {pp} · Rec: **{rec}**")
                    _show_token_usage(llm.last_usage)
                _prog.progress(75, text="✓  Gravity analysis complete")

                _prog.progress(88, text="📄  Rendering PDF…")
                st.write("📄  Rendering PDF...")
                from agents.pdf_gravity import GravityPDFGenerator
                safe = ticker_input.replace(".", "_").replace("-", "_")
                date = datetime.now().strftime("%Y-%m-%d")
                pdf_path = str(OUTPUTS_DIR / f"{safe}_gravity_{date}.pdf")
                os.makedirs(OUTPUTS_DIR, exist_ok=True)
                GravityPDFGenerator().render(company, analysis, pdf_path,
                                             adv_result=adv_result)
                extra = {"score": score, "grade": grade}

            # ── Done ──────────────────────────────────────────────────────────
            _prog.progress(100, text="✅  Report ready!")
            status.update(
                label=f"✅  {rt['short']} report ready for **{company.name}**",
                state="complete",
                expanded=False,
            )

            # Store result
            st.session_state.report_result = {
                "pdf_path":    pdf_path,
                "company":     company,
                "analysis":    analysis,
                "report_type": report_type,
                "rec":         analysis.get("recommendation", "HOLD"),
                "extra":       extra,
                "adversarial": adv_result,
            }

            # Add to recent reports
            label = f"{ticker_input} · {rt['short']} · {date}"
            st.session_state.recent_reports.append({
                "label": label,
                "path":  pdf_path,
                "ts":    datetime.now().timestamp(),
            })

        except Exception as e:
            st.session_state.error_msg = str(e)
            status.update(
                label=f"❌  Error generating report",
                state="error",
                expanded=True,
            )
            st.error(f"**Error:** {e}")
            logger.exception("Report generation failed")


# ── Results display ───────────────────────────────────────────────────────────
if st.session_state.report_result:
    res     = st.session_state.report_result
    company = res["company"]
    rec     = res["rec"]
    rtype   = res["report_type"]
    extra   = res["extra"]

    # ── Key metrics bar ───────────────────────────────────────────────────────
    if rtype == "index_overview":
        # Index metrics bar
        idx_data = res.get("index_data")
        if idx_data:
            col1, col2, col3, col4, col5, col6 = st.columns(6)
            col1.metric("Level",       f"{idx_data.current_level:,.2f}"        if idx_data.current_level       else "n/a")
            col2.metric("YTD",         f"{idx_data.return_ytd*100:+.1f}%"      if idx_data.return_ytd          else "n/a")
            col3.metric("1Y Return",   f"{idx_data.return_1y*100:+.1f}%"       if idx_data.return_1y           else "n/a")
            col4.metric("Volatility",  f"{idx_data.volatility_1y_ann*100:.1f}%" if idx_data.volatility_1y_ann  else "n/a")
            col5.metric("Wtd P/E",     f"{idx_data.weighted_pe:.1f}x"          if idx_data.weighted_pe         else "n/a")
            col6.metric("Div Yield",   f"{idx_data.dividend_yield*100:.2f}%"   if idx_data.dividend_yield      else "n/a")
            st.caption(
                f"**{idx_data.name or idx_data.ticker}**  ·  "
                f"{idx_data.index_type}  ·  "
                f"Currency: {idx_data.currency or 'n/a'}  ·  "
                f"As of: {idx_data.as_of_date or 'n/a'}  ·  "
                f"Rec: **{rec}**"
            )

    elif rtype.startswith("universe_"):
        # Universe screen — lightweight caption only
        fw_id    = rtype.removeprefix("universe_")
        fw_short = REPORT_TYPES.get(fw_id, {}).get("short", fw_id)
        st.caption(f"🔍 **Universe Screen** · Framework: **{fw_short}** · {Path(res['pdf_path']).name}")

    else:
        # ── Equity metrics bar ────────────────────────────────────────────────
        col1, col2, col3, col4, col5, col6 = st.columns(6)

        price_str  = (f"{company.current_price:.2f} {company.currency_price or ''}"
                      if company.current_price else "n/a")
        cap_str    = (_fmt_b(company.market_cap) + f" {company.currency or ''}"
                      if company.market_cap else "n/a")
        pe_str     = f"{company.pe_ratio:.1f}x" if company.pe_ratio else "n/a"
        roe_str    = f"{company.roe*100:.1f}%"  if company.roe else "n/a"
        margin_str = f"{company.ebit_margin*100:.1f}%" if company.ebit_margin else "n/a"

        col1.metric("Price",       price_str)
        col2.metric("Market Cap",  cap_str)
        col3.metric("P/E",         pe_str)
        col4.metric("ROE",         roe_str)
        col5.metric("EBIT Margin", margin_str)
        col6.metric("Rec.",        rec)

        # Adversarial badge
        adv = res.get("adversarial")
        if adv is not None:
            agree_icon = "✓" if adv.recs_agree else "⚠"
            st.markdown(
                f"<span style='background:#1B3F6E;color:white;padding:2px 8px;"
                f"border-radius:4px;font-size:12px;font-weight:600;'>⚔ Adversarial</span>  "
                f"Claude: **{adv.primary_rec}**  ·  GPT-4o: **{adv.secondary_rec}**  ·  "
                f"{agree_icon} {'Agree' if adv.recs_agree else 'Contested'}  ·  "
                f"Consensus: {len(adv.consensus_fields)} fields  ·  "
                f"Contested: {len(adv.contested_fields)} fields",
                unsafe_allow_html=True,
            )

        # Extra framework metrics
        if rtype == "overview":
            passed = extra.get("passed", 0)
            total  = len(extra.get("checklist", []))
            st.caption(f"Checklist: **{passed}/{total}** criteria met  ·  "
                       f"Data: {company.year_range()}  ·  "
                       f"Sources: {', '.join(company.data_sources)}")
        elif rtype == "fisher":
            st.caption(f"Fisher Score: **{extra.get('score','?')}/75**  ·  "
                       f"Grade: **{extra.get('grade','?')}**  ·  "
                       f"Moat: {res['analysis'].get('moat_width','?')}  ·  "
                       f"Active Powers: {res['analysis'].get('active_powers_count','?')}/7")
        elif rtype == "gravity":
            rm = res["analysis"].get("revenue_model", {})
            st.caption(f"Gravity Score: **{extra.get('score','?')}/50**  ·  "
                       f"Grade: **{extra.get('grade','?')}**  ·  "
                       f"Recurring: ~{rm.get('recurring_pct_estimate','?')}%  ·  "
                       f"Pricing Power: {rm.get('pricing_power','?')}")
        else:
            fw_label = REPORT_TYPES.get(rtype, {}).get("short", rtype)
            st.caption(f"Framework: **{fw_label}**  ·  "
                       f"Data: {company.year_range()}  ·  "
                       f"Sources: {', '.join(company.data_sources)}")

    st.divider()

    # ── Report viewer + download ──────────────────────────────────────────────
    pdf_path = res["pdf_path"]
    is_html_report = pdf_path.endswith(".html")

    col_view, col_dl = st.columns([5, 1])

    # Build a sensible header label for the viewer
    if company:
        _viewer_label = f"**{company.name}** — {REPORT_TYPES.get(rtype, {}).get('label', rtype)}"
    elif rtype == "index_overview":
        _idx = res.get("index_data")
        _idx_name = _idx.name if _idx else res["pdf_path"].split("\\")[-1]
        _viewer_label = f"**{_idx_name}** — Index Overview"
    else:
        _viewer_label = f"**{Path(pdf_path).stem}** — Universe Screen"

    with col_view:
        st.markdown(_viewer_label)

    with col_dl:
        st.markdown("&nbsp;")
        with open(pdf_path, "rb") as f:
            report_bytes = f.read()
        mime = "text/html" if is_html_report else "application/pdf"
        label = "⬇ Download HTML" if is_html_report else "⬇ Download PDF"
        st.download_button(
            label=label,
            data=report_bytes,
            file_name=Path(pdf_path).name,
            mime=mime,
            use_container_width=True,
            type="primary",
        )
        st.caption(f"{len(report_bytes)//1024} KB")

    if is_html_report:
        # Render HTML inline
        html_content = extra.get("html_content", report_bytes.decode("utf-8", errors="replace"))
        b64 = base64.b64encode(html_content.encode("utf-8")).decode()
        st.markdown(
            f'<iframe src="data:text/html;base64,{b64}" '
            f'width="100%" height="820px" '
            f'style="border:1px solid #BBCCDD; border-radius:4px;"></iframe>',
            unsafe_allow_html=True,
        )
    else:
        # PDF viewer
        b64 = base64.b64encode(report_bytes).decode()
        st.markdown(
            f'<iframe src="data:application/pdf;base64,{b64}" '
            f'width="100%" height="820px" '
            f'style="border:1px solid #BBCCDD; border-radius:4px;"></iframe>',
            unsafe_allow_html=True,
        )
        st.caption(
            "💡 If the PDF doesn't display inline, use the **Download PDF** button above. "
            "Firefox may require the download."
        )

elif st.session_state.error_msg:
    st.error(st.session_state.error_msg)

else:
    # Empty state — show example tickers
    st.markdown("### Example tickers to try")
    ex_cols = st.columns(3)
    examples = [
        ("WKL.AS",   "Wolters Kluwer",     "Professional information · Netherlands"),
        ("ASML.AS",  "ASML Holding",       "Semiconductor equipment · Netherlands"),
        ("V",        "Visa Inc.",          "Payment network · United States"),
        ("MCO",      "Moody's",            "Credit ratings · United States"),
        ("SAP.DE",   "SAP SE",             "Enterprise software · Germany"),
        ("NOKIA.HE", "Nokia",              "Telecom equipment · Finland"),
    ]
    for i, (ticker, name, desc) in enumerate(examples):
        with ex_cols[i % 3]:
            st.markdown(
                f"<div class='report-card'>"
                f"<h4>{name} <code>{ticker}</code></h4>"
                f"<p>{desc}</p>"
                f"</div>",
                unsafe_allow_html=True,
            )
