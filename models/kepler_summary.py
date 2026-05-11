"""
models/kepler_summary.py — LLM prompt builder for the Kepler Summary report.

The Kepler Summary is primarily a data-driven report; the LLM's role is narrow:
  - Generate a 12-month price target
  - State a BUY / HOLD / SELL recommendation
  - Provide a one-sentence thesis and valuation method note

Everything else (all tables) is built directly from CompanyData.
"""
from __future__ import annotations
from data_sources.base import CompanyData

SYSTEM_PROMPT = (
    "You are a senior equity research analyst at a top-tier European investment bank. "
    "You produce concise, data-driven investment recommendations with clear price targets. "
    "Your output is consumed by a structured PDF report — be precise, factual, and brief. "
    "Base your target price on observable valuation multiples (P/E, EV/EBIT, EV/EBITDA) "
    "relative to sector peers and the company's own history. "
    "State BUY if the stock trades at a material discount to fair value, "
    "HOLD if fairly valued, SELL if overvalued or structurally deteriorating. "
    "Reply ONLY with valid JSON — no markdown, no prose outside the JSON."
)


def _kepler_prompt_parts(
    company: CompanyData,
    news_block: str = "",
    macro_country_block: str = "",
) -> tuple[str, str]:
    """
    Return (cacheable_prefix, dynamic_prompt).

    The cacheable_prefix contains the static financial data (doesn't change
    run-to-run for the same ticker). The dynamic_prompt contains time-sensitive
    data (current price, news) and the output schema.
    """
    cur = company.currency or company.currency_price or ""
    la  = company.latest_annual()
    fe  = company.forward_estimates

    # ── Build historical snapshot ────────────────────────────────────────────
    years = list(reversed(company.sorted_years()[:5]))  # last 5 years, chrono
    hist_lines = []
    for yr in years:
        af = company.annual_financials.get(yr)
        if not af:
            continue
        parts = [f"FY{yr}:"]
        if af.revenue:
            parts.append(f"Sales={af.revenue:.0f}M")
        if af.ebit:
            parts.append(f"EBIT={af.ebit:.0f}M")
        if af.net_income:
            parts.append(f"NetProfit={af.net_income:.0f}M")
        if af.eps_diluted:
            parts.append(f"EPS={af.eps_diluted:.2f}")
        if af.pe_ratio:
            parts.append(f"P/E={af.pe_ratio:.1f}x")
        if af.ev_ebit:
            parts.append(f"EV/EBIT={af.ev_ebit:.1f}x")
        if af.roe:
            parts.append(f"ROE={af.roe*100:.1f}%")
        hist_lines.append("  " + "  ".join(parts))

    hist_block = "\n".join(hist_lines) if hist_lines else "  No annual history available."

    # Forward estimates
    fwd_block = ""
    if fe:
        fwd_parts = [f"FY{fe.year}E (consensus):"]
        if fe.revenue:
            fwd_parts.append(f"Sales={fe.revenue:.0f}M")
        if fe.eps_diluted:
            fwd_parts.append(f"EPS={fe.eps_diluted:.2f}")
        if fe.pe_ratio:
            fwd_parts.append(f"FwdP/E={fe.pe_ratio:.1f}x")
        fwd_block = "FORWARD ESTIMATES:\n  " + "  ".join(fwd_parts)

    # Current valuation
    cur_val = []
    if company.current_price:
        cur_val.append(f"Current price: {company.current_price:.2f} {cur}")
    if company.market_cap:
        mc = company.market_cap
        cur_val.append(f"Mkt cap: {mc/1000:.1f}B {cur}" if mc >= 1000 else f"Mkt cap: {mc:.0f}M {cur}")
    if company.pe_ratio:
        cur_val.append(f"Trailing P/E: {company.pe_ratio:.1f}x")
    if company.ev_ebit:
        cur_val.append(f"EV/EBIT: {company.ev_ebit:.1f}x")
    if company.roe:
        cur_val.append(f"ROE: {company.roe*100:.1f}%")
    cur_val_block = "  " + "  ".join(cur_val) if cur_val else "  n/a"

    # ── Cacheable prefix (static per ticker + data vintage) ─────────────────
    cacheable_prefix = f"""COMPANY: {company.name or 'Unknown'} | {company.ticker or ''}
SECTOR: {company.sector or 'n/a'} | COUNTRY: {company.country or 'n/a'}
CURRENCY: {cur}

HISTORICAL FINANCIALS (last 5 fiscal years, values in {cur}M):
{hist_block}

{fwd_block}

CURRENT MARKET DATA:
{cur_val_block}

{f'MACRO CONTEXT ({company.country}):{chr(10)}{macro_country_block}' if macro_country_block else ''}"""

    # ── Dynamic prompt (time-sensitive) ────────────────────────────────────
    news_section = f"RECENT NEWS:\n{news_block}\n" if news_block else ""

    dynamic_prompt = f"""{news_section}
Based on the above financial data, generate a structured equity research summary.

Return ONLY this JSON (no other text):
{{
  "target_price": <number — 12-month price target in {cur}>,
  "recommendation": "<BUY|HOLD|SELL>",
  "key_thesis": "<one sentence, max 25 words, primary investment case or main risk>",
  "valuation_method": "<brief note on methodology, e.g. '14x fwd EV/EBIT + sector peer avg'>"
}}

Rules:
- target_price must be a plain number (no currency symbol)
- recommendation must be exactly BUY, HOLD, or SELL
- key_thesis: factual, direct — no filler words
- valuation_method: 10 words max
"""

    return cacheable_prefix, dynamic_prompt
