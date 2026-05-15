"""
_eodhd_context.py — Shared EODHD-only LLM context builder.

Used by Fisher Alternatives and Gravity Taxers to assemble a rich
context block from EODHD data only:

  - Identity, full description (untruncated)
  - Current market data + valuation multiples + TTM profitability
  - 10 years of annual financials (parameterised rows per framework)
  - Forward estimates from EODHD Earnings.Trend
  - Officers (top 6)
  - Insider transactions summary (last 12 months)
  - Analyst ratings + recent rating changes
  - Sentiment time series (last 30 days)
  - News headlines (top 10 from EODHD /news)
  - Peer comparison table (provided externally — already EODHD-only)
  - Country macro from EODHD /macro-indicator

All inputs are passed in pre-fetched. This module only formats — no I/O.
"""
from __future__ import annotations

import logging
from typing import Optional, Callable

from data_sources.base import CompanyData, AnnualFinancials

logger = logging.getLogger(__name__)


# ── Formatters (shared with fisher.py / gravity.py originals) ────────────────

def _b(v) -> str:
    if v is None: return "n/a"
    if abs(v) >= 1000: return f"{v/1000:,.1f}B"
    return f"{v:,.0f}"


def _x(v, d=1) -> str:
    if v is None: return "n/a"
    return f"{v:.{d}f}x"


def _pct(v) -> str:
    if v is None: return "n/a"
    return f"{v*100:.1f}%"


def _ps(v) -> str:
    """Per-share value — 2 decimals."""
    if v is None: return "n/a"
    return f"{v:.2f}"


def _date(v) -> str:
    if not v: return ""
    return str(v)[:10]


# ── Annual row definitions ───────────────────────────────────────────────────
# Per-framework: each row = (label, getter(AnnualFinancials), fmt)
# fmt: "M" (currency millions), "%" (decimal pct), "ps" (per share), "x" (multiple)

def _row(label: str, getter: Callable[[AnnualFinancials], Optional[float]],
         fmt: str = "M") -> tuple[str, Callable, str]:
    return (label, getter, fmt)


FISHER_ROWS: list = [
    _row("Revenue",        lambda a: a.revenue),
    _row("EBITDA",         lambda a: a.ebitda),
    _row("EBIT",           lambda a: a.ebit),
    _row("Net Income",     lambda a: a.net_income),
    _row("EPS (diluted)",  lambda a: a.eps_diluted,   "ps"),
    _row("FCF",            lambda a: a.fcf),
    _row("Net Debt",       lambda a: a.net_debt),
    _row("ROE",            lambda a: a.roe,           "%"),
    _row("EBIT Margin",    lambda a: a.ebit_margin,   "%"),
    _row("Net Margin",     lambda a: a.net_margin,    "%"),
    _row("Div/Share",      lambda a: a.dividends_per_share, "ps"),
]

GRAVITY_ROWS: list = [
    _row("Revenue",        lambda a: a.revenue),
    _row("Gross Profit",   lambda a: a.gross_profit),
    _row("EBITDA",         lambda a: a.ebitda),
    _row("EBIT",           lambda a: a.ebit),
    _row("Net Income",     lambda a: a.net_income),
    _row("FCF",            lambda a: a.fcf),
    _row("CapEx",          lambda a: a.capex),
    _row("Net Debt",       lambda a: a.net_debt),
    _row("Gross Margin",   lambda a: a.gross_margin,  "%"),
    _row("EBIT Margin",    lambda a: a.ebit_margin,   "%"),
    _row("Net Margin",     lambda a: a.net_margin,    "%"),
    _row("ROE",            lambda a: a.roe,           "%"),
]


def _format_cell(v: Optional[float], fmt: str) -> str:
    if v is None:
        return "      n/a"
    if fmt == "M":
        if abs(v) >= 1000:
            return f"{v/1000:>9.1f}B"
        return f"{v:>9.0f}M"
    if fmt == "%":
        return f"{v*100:>9.1f}%"
    if fmt == "x":
        return f"{v:>9.1f}x"
    if fmt == "ps":
        return f"{v:>9.2f}"
    return f"{v:>9}"


def _annual_table(company: CompanyData, rows: list, years: list[int]) -> str:
    """Render a fixed-width annual financials table for the given rows."""
    if not years:
        return ""
    lines = []
    cur = company.currency or "USD"
    lines.append(f"\nANNUAL FINANCIALS ({cur}M, most recent first):")
    lines.append(f"  {'':24} " + " ".join(f"{y:>10}" for y in years))
    lines.append("  " + "-" * (24 + 11 * len(years)))
    for label, getter, fmt in rows:
        vals = []
        for y in years:
            af = company.annual_financials.get(y)
            v  = getter(af) if af else None
            vals.append(_format_cell(v, fmt))
        lines.append(f"  {label:<24} " + " ".join(vals))
    return "\n".join(lines)


# ── Section builders ─────────────────────────────────────────────────────────

def _identity_block(company: CompanyData) -> str:
    cur = company.currency or "USD"
    lines = []
    lines.append(f"COMPANY:  {company.name or company.ticker} ({company.ticker})")
    lines.append(f"SECTOR:   {company.sector or 'n/a'} — {company.industry or 'n/a'}")
    lines.append(f"COUNTRY:  {company.country or 'n/a'} | CURRENCY: {cur}")
    lines.append(f"EXCHANGE: {company.exchange or 'n/a'}")
    if company.employees:
        lines.append(f"EMPLOYEES: {company.employees:,}")
    if company.website:
        lines.append(f"WEBSITE:  {company.website}")
    if company.ipo_date:
        lines.append(f"IPO:      {_date(company.ipo_date)}")
    if company.fiscal_year_end:
        lines.append(f"FYE:      {company.fiscal_year_end}")
    return "\n".join(lines)


def _description_block(company: CompanyData) -> str:
    """Full description — no 800-char truncation."""
    if not company.description:
        return ""
    # Cap at 3000 chars to keep prompt size sane; EODHD descriptions rarely
    # exceed this anyway.
    desc = company.description[:3000]
    suffix = "…" if len(company.description) > 3000 else ""
    return f"\nBUSINESS DESCRIPTION:\n{desc}{suffix}"


def _market_block(company: CompanyData) -> str:
    cur = company.currency or "USD"
    lines = []
    lines.append(f"\nMARKET DATA (as of {company.as_of_date or 'n/a'}):")
    lines.append(f"  Price:         {company.current_price} {company.currency_price or cur}")
    lines.append(f"  Market Cap:    {_b(company.market_cap)} {cur}M")
    lines.append(f"  EV:            {_b(company.enterprise_value)} {cur}M")
    if company.week_52_high or company.week_52_low:
        lines.append(f"  52-wk range:   {company.week_52_low or 'n/a'} – {company.week_52_high or 'n/a'}")
    if company.beta is not None:
        lines.append(f"  Beta:          {company.beta:.2f}")
    return "\n".join(lines)


def _valuation_block(company: CompanyData) -> str:
    lines = []
    lines.append("\nVALUATION:")
    lines.append(f"  P/E:        {_x(company.pe_ratio)}  | Fwd P/E: {_x(company.forward_pe)}")
    lines.append(f"  EV/EBIT:    {_x(company.ev_ebit)}  | EV/EBITDA: {_x(company.ev_ebitda)}")
    lines.append(f"  EV/Sales:   {_x(company.ev_sales)} | P/Book: {_x(company.price_to_book)}")
    lines.append(f"  Div Yield:  {_pct(company.dividend_yield)} | FCF Yield: {_pct(company.fcf_yield)}")
    return "\n".join(lines)


def _profitability_block(company: CompanyData) -> str:
    cur = company.currency or "USD"
    lines = []
    lines.append("\nPROFITABILITY (TTM):")
    lines.append(f"  Gross Margin: {_pct(company.gross_margin)} | EBIT Margin: {_pct(company.ebit_margin)}")
    lines.append(f"  Net Margin:   {_pct(company.net_margin)} | EBITDA Margin: {_pct(company.ebitda_margin)}")
    lines.append(f"  ROE:          {_pct(company.roe)} | ROA: {_pct(company.roa)}")
    lines.append(f"  Net Debt:     {_b(company.net_debt)} {cur}M | Gearing: {_x(company.gearing)} x Net Debt/EBITDA")
    return "\n".join(lines)


def _ownership_block(company: CompanyData) -> str:
    parts = []
    if company.pct_institutions is not None:
        parts.append(f"Institutional: {_pct(company.pct_institutions)}")
    if company.pct_insiders is not None:
        parts.append(f"Insider: {_pct(company.pct_insiders)}")
    if company.shares_float:
        parts.append(f"Float: {_b(company.shares_float)} sh")
    if not parts:
        return ""
    return "\nOWNERSHIP:  " + " | ".join(parts)


def _officers_block(company: CompanyData, limit: int = 6) -> str:
    if not company.officers:
        return ""
    rows = ["\nKEY OFFICERS:"]
    for off in company.officers[:limit]:
        if not isinstance(off, dict):
            continue
        name  = off.get("Name") or off.get("name") or ""
        title = off.get("Title") or off.get("title") or ""
        year  = off.get("YearBorn") or off.get("year_born") or ""
        line = f"  • {name:<30} — {title}"
        if year:
            line += f"  (born {year})"
        rows.append(line)
    return "\n".join(rows) if len(rows) > 1 else ""


def _insider_block(bundle: dict, months: int = 12) -> str:
    """Summarise recent insider transactions (count + net direction)."""
    insider = bundle.get("insider") or []
    if not isinstance(insider, list) or not insider:
        return ""
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=months * 31)
    bought = 0
    sold = 0
    net_value = 0.0
    recent: list[tuple[str, str, str, str]] = []
    for tx in insider:
        if not isinstance(tx, dict):
            continue
        date_s = tx.get("transactionDate") or tx.get("date") or ""
        try:
            dt = datetime.strptime(str(date_s)[:10], "%Y-%m-%d")
        except Exception:
            continue
        if dt < cutoff:
            continue
        ttype = (tx.get("transactionCode") or tx.get("type") or "").upper()
        name = tx.get("ownerName") or tx.get("name") or ""
        try:
            val = float(tx.get("transactionAmount") or tx.get("value") or 0)
        except Exception:
            val = 0.0
        # P = open-market purchase, S = open-market sale (EODHD codes)
        if ttype.startswith("P") or "BUY" in ttype:
            bought += 1
            net_value += val
        elif ttype.startswith("S") or "SELL" in ttype:
            sold += 1
            net_value -= val
        if len(recent) < 5:
            recent.append((str(date_s)[:10], name[:24], ttype[:6], _b(val/1e6) if val else "n/a"))
    if bought + sold == 0:
        return ""
    direction = "net BUYING" if net_value > 0 else ("net SELLING" if net_value < 0 else "balanced")
    lines = [f"\nINSIDER ACTIVITY (last {months} months): "
             f"{bought} buys, {sold} sells — {direction}"]
    for d, n, t, v in recent:
        lines.append(f"  {d}  {n:<24}  {t:<6}  ~{v}")
    return "\n".join(lines)


def _ratings_block(bundle: dict) -> str:
    """Analyst ratings + recent upgrades/downgrades."""
    fund = bundle.get("fundamentals") or {}
    ar = (fund.get("AnalystRatings") if isinstance(fund, dict) else None) or {}
    upgrades = bundle.get("upgrades") or []

    lines = []
    if ar:
        rating       = ar.get("Rating")
        target_price = ar.get("TargetPrice")
        strong_buy   = ar.get("StrongBuy")
        buy          = ar.get("Buy")
        hold         = ar.get("Hold")
        sell         = ar.get("Sell")
        strong_sell  = ar.get("StrongSell")
        line_pieces = []
        if rating       is not None: line_pieces.append(f"score: {rating}")
        if target_price is not None: line_pieces.append(f"target: {target_price}")
        counts = []
        if strong_buy  is not None: counts.append(f"SB:{strong_buy}")
        if buy         is not None: counts.append(f"B:{buy}")
        if hold        is not None: counts.append(f"H:{hold}")
        if sell        is not None: counts.append(f"S:{sell}")
        if strong_sell is not None: counts.append(f"SS:{strong_sell}")
        if counts:
            line_pieces.append("breakdown: " + " ".join(counts))
        if line_pieces:
            lines.append("\nANALYST CONSENSUS:  " + " | ".join(line_pieces))

    if isinstance(upgrades, list) and upgrades:
        lines.append("\nRECENT RATING CHANGES (last 12 months):")
        for u in upgrades[:8]:
            if not isinstance(u, dict):
                continue
            d = (u.get("date") or u.get("date_added") or "")[:10]
            firm = u.get("firm") or u.get("analyst") or ""
            old  = u.get("fromGrade") or ""
            new  = u.get("toGrade")   or ""
            act  = u.get("action")    or ""
            line = f"  {d}  {firm:<22}  "
            if old and new:
                line += f"{old} → {new}"
            elif new:
                line += new
            if act:
                line += f"  [{act}]"
            lines.append(line)
    return "\n".join(lines)


def _sentiment_block(bundle: dict, days: int = 30) -> str:
    """Latest sentiment polarity averaged over the past N days."""
    sents = bundle.get("sentiments") or {}
    if not isinstance(sents, dict) or not sents:
        return ""
    # EODHD returns {ticker: [{date, count, normalized}, ...]}
    for _key, series in sents.items():
        if not isinstance(series, list) or not series:
            continue
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)
        recent = []
        for pt in series:
            if not isinstance(pt, dict):
                continue
            try:
                dt = datetime.strptime(str(pt.get("date"))[:10], "%Y-%m-%d")
            except Exception:
                continue
            if dt < cutoff:
                continue
            val = pt.get("normalized")
            if val is None:
                continue
            try:
                recent.append(float(val))
            except Exception:
                continue
        if not recent:
            continue
        avg = sum(recent) / len(recent)
        label = "positive" if avg >= 0.15 else ("negative" if avg <= -0.15 else "neutral")
        return (f"\nNEWS SENTIMENT (EODHD, last {days} days): "
                f"avg polarity = {avg:+.2f} ({label}, n={len(recent)} days)")
    return ""


def _news_block(bundle: dict, limit: int = 10) -> str:
    """Top N news headlines from EODHD /news."""
    news = bundle.get("news") or []
    if not isinstance(news, list) or not news:
        return ""
    lines = ["\nRECENT NEWS (EODHD, newest first):"]
    for n in news[:limit]:
        if not isinstance(n, dict):
            continue
        date  = (n.get("date") or "")[:10]
        title = (n.get("title") or "").strip()
        if not title:
            continue
        lines.append(f"  {date}  {title[:120]}")
    return "\n".join(lines) if len(lines) > 1 else ""


def _forward_estimates_block(company: CompanyData) -> str:
    fe = company.forward_estimates
    if fe is None:
        return ""
    cur = company.currency or "USD"
    parts = []
    if fe.revenue is not None:
        g = f" (YoY {_pct(fe.revenue_growth_yoy)})" if fe.revenue_growth_yoy is not None else ""
        parts.append(f"Revenue: {_b(fe.revenue)} {cur}M{g}")
    if fe.eps_diluted is not None:
        g = f" (YoY {_pct(fe.eps_growth_yoy)})" if fe.eps_growth_yoy is not None else ""
        parts.append(f"EPS: {fe.eps_diluted:.2f}{g}")
    if fe.net_income is not None:
        parts.append(f"NI: {_b(fe.net_income)} {cur}M")
    if fe.ebitda is not None:
        parts.append(f"EBITDA: {_b(fe.ebitda)} {cur}M")
    if fe.pe_ratio is not None:
        parts.append(f"Fwd P/E: {_x(fe.pe_ratio)}")
    if fe.ev_sales is not None:
        parts.append(f"Fwd EV/Sales: {_x(fe.ev_sales)}")
    if not parts:
        return ""
    head = f"\nFORWARD ESTIMATES (FY{fe.year}"
    if fe.analyst_count:
        head += f", n={fe.analyst_count}"
    head += "):"
    body = "  " + " | ".join(parts)
    return f"{head}\n{body}"


def _peers_block(peers: dict, cur_company: CompanyData) -> str:
    """Compact peer comparison table — EODHD-only fields per peer."""
    if not peers:
        return ""
    lines = ["\nPEER COMPARISON (all EODHD-sourced):"]
    header = (f"  {'Ticker':<12} {'Name':<26} {'Sector':<22} "
              f"{'MCap':>10} {'P/E':>7} {'EV/EBIT':>8} {'ROE':>7} "
              f"{'EBIT Mg':>8} {'Net Mg':>8}")
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    # The subject first
    def row(tk, c):
        la = c.latest_annual()
        return (f"  {tk:<12} {(c.name or '')[:25]:<26} "
                f"{(c.sector or '')[:21]:<22} "
                f"{_b(c.market_cap):>10} "
                f"{_x(c.pe_ratio):>7} "
                f"{_x(c.ev_ebit):>8} "
                f"{_pct(c.roe):>7} "
                f"{_pct(la.ebit_margin if la else None):>8} "
                f"{_pct(la.net_margin if la else None):>8}")
    lines.append(row(cur_company.ticker or "self", cur_company))
    for tk, c in peers.items():
        lines.append(row(tk, c))
    return "\n".join(lines)


# ── Public entry point ───────────────────────────────────────────────────────

def build_eodhd_context(
    company: CompanyData,
    bundle: dict,
    rows: list,
    *,
    peers: Optional[dict] = None,
    country_macro_block: str = "",
    n_years: int = 10,
) -> str:
    """
    Build the full EODHD-only context block for the LLM prompt.

    Args:
        company             — main subject CompanyData (built from EODHD bundle)
        bundle              — raw EODHD endpoints dict (news, sentiments, etc.)
        rows                — annual-financials row spec (FISHER_ROWS or GRAVITY_ROWS)
        peers               — optional dict[ticker → CompanyData] of EODHD-only peers
        country_macro_block — pre-fetched country macro text (eodhd_macro)
        n_years             — how many historical years to include (default 10)
    """
    cur = company.currency or "USD"

    # Identity + description
    sections = [
        _identity_block(company),
        _description_block(company),
        _market_block(company),
        _valuation_block(company),
        _profitability_block(company),
        _ownership_block(company),
        _officers_block(company, limit=6),
    ]

    # 10-year history
    years = company.sorted_years()[:n_years]
    sections.append(_annual_table(company, rows, years))

    # Growth
    c3 = company.revenue_cagr(3)
    c5 = company.revenue_cagr(5)
    c10 = company.revenue_cagr(10) if hasattr(company, "revenue_cagr") else None
    cagr_parts = [f"3yr={_pct(c3)}", f"5yr={_pct(c5)}"]
    if c10 is not None:
        cagr_parts.append(f"10yr={_pct(c10)}")
    sections.append(f"\nREVENUE CAGR: " + " · ".join(cagr_parts))

    # Forward / analyst / sentiment / news / insider
    sections.append(_forward_estimates_block(company))
    sections.append(_ratings_block(bundle))
    sections.append(_insider_block(bundle, months=12))
    sections.append(_sentiment_block(bundle, days=30))
    sections.append(_news_block(bundle, limit=10))

    # Peers
    if peers:
        sections.append(_peers_block(peers, company))

    # Country macro
    if country_macro_block:
        sections.append("\n" + country_macro_block)

    # Final reminder
    sections.append(
        f"\nCurrency is {cur}. Every value above is sourced from EODHD. "
        f"Do not invent data not in the blocks above."
    )

    # Drop any empty sections
    return "\n".join(s for s in sections if s)
