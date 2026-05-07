"""
report_generic.py — Generic HTML report renderer for any FrameworkConfig.

Turns the LLM analysis dict + optional company data into a fully styled
self-contained HTML document.  Used for:
  • User-created / forked frameworks (primary use case)
  • Previewing framework output in the Framework Studio

Built-in frameworks (overview, fisher, gravity) continue to use their
hand-crafted PDF generators.  This renderer is the HTML equivalent for
everything else.

Supported section types (framework.report_sections[].type):
  text_block          — Heading + paragraph text
  bullet_list         — Heading + <ul> list
  recommendation_banner — Coloured BUY/HOLD/SELL strip
  score_table         — Two-column label/value table
  key_value           — Inline key: value pairs
  fisher_scorecard    — 15-point Fisher table (reads analysis["fisher_points"])
  helmer_powers       — 7-power table (reads analysis["powers"])
  gravity_scorecard   — 10-dimension table (reads analysis["gravity_dimensions"])
  financial_table     — Simplified historical financials (needs company kwarg)
  checklist           — Investment checklist table (needs checklist kwarg)
  peer_table          — Peer comparison table (needs peers kwarg)

Public API:
    html = render_html(framework, analysis, company=..., peers=..., checklist=...)
    # Returns a self-contained HTML string ready for st.html() or file download.
"""

from __future__ import annotations

import html as html_lib
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from framework_manager import FrameworkConfig
    from data_sources.base import CompanyData


# ── Colour constants (match EquityBot visual identity) ────────────────────────
_NAV   = "#1B3F6E"   # navy — headers
_LIGHT = "#EEF5FB"   # light blue — backgrounds
_MID   = "#D6E8F7"   # mid blue — alternate rows
_GREY  = "#F5F5F5"   # light grey
_GREEN = "#D4EDDA"   # BUY
_AMBER = "#FFF3CD"   # HOLD
_RED   = "#FADBD8"   # SELL
_TEXT  = "#222222"
_MUTED = "#555555"


# ── Public entry point ────────────────────────────────────────────────────────

def render_html(
    framework: "FrameworkConfig",
    analysis: dict,
    company: Optional["CompanyData"] = None,
    peers: Optional[dict] = None,
    checklist: Optional[list] = None,
    index_data=None,
) -> str:
    """
    Generate a self-contained HTML report.

    Args:
        framework:   FrameworkConfig (drives section order and types)
        analysis:    LLM output dict
        company:     CompanyData object — required for financial_table sections
        peers:       {ticker: CompanyData} — required for peer_table sections
        checklist:   list of {criterion, actual, pass} — required for checklist
        index_data:  IndexData object — used when rendering index/ETF reports

    Returns:
        Full HTML string (includes <html>, <head>, <body>).
    """
    rec = analysis.get("recommendation", "")
    if company:
        title = f"{framework.icon} {company.name} — {framework.name}"
    elif index_data:
        title = f"{framework.icon} {index_data.name or index_data.ticker} — {framework.name}"
    else:
        title = f"{framework.icon} {framework.name}"

    sections_html = []
    for section in sorted(framework.report_sections, key=lambda s: s.get("order", 99)):
        stype = section.get("type", "text_block")
        stitle = section.get("title", "")
        sfield = section.get("field", "")
        value  = analysis.get(sfield, "")

        rendered = _dispatch(
            stype, stitle, sfield, value,
            analysis=analysis,
            company=company,
            peers=peers,
            checklist=checklist,
        )
        if rendered:
            sections_html.append(rendered)

    body = "\n".join(sections_html)
    if company:
        cur    = company.currency or ""
        ticker = company.ticker or ""
    elif index_data:
        cur    = index_data.currency or ""
        ticker = index_data.ticker or ""
    else:
        cur    = ""
        ticker = ""

    return _wrap_html(title, body, framework, company, ticker, cur, rec, index_data=index_data)


# ── Section dispatcher ────────────────────────────────────────────────────────

def _dispatch(
    stype: str,
    title: str,
    field: str,
    value,
    *,
    analysis: dict,
    company,
    peers,
    checklist,
) -> str:
    if stype == "text_block":
        return _section_text(title, value)
    elif stype == "bullet_list":
        return _section_bullets(title, value)
    elif stype == "recommendation_banner":
        rationale = analysis.get("recommendation_rationale", "")
        return _section_recommendation(value, rationale)
    elif stype == "score_table":
        return _section_score_table(title, value)
    elif stype == "key_value":
        return _section_key_value(title, value)
    elif stype == "fisher_scorecard":
        points = analysis.get("fisher_points", [])
        total  = analysis.get("fisher_total_score", "?")
        grade  = analysis.get("fisher_grade", "?")
        return _section_fisher_scorecard(title, points, total, grade)
    elif stype == "helmer_powers":
        powers = analysis.get("powers", [])
        count  = analysis.get("active_powers_count", "?")
        moat   = analysis.get("moat_width", "?")
        return _section_helmer_powers(title, powers, count, moat)
    elif stype == "gravity_scorecard":
        dims   = analysis.get("gravity_dimensions", [])
        total  = analysis.get("total_gravity_score", "?")
        grade  = analysis.get("gravity_grade", "?")
        rm     = analysis.get("revenue_model", {})
        return _section_gravity_scorecard(title, dims, total, grade, rm)
    elif stype == "financial_table":
        return _section_financial_table(company) if company else ""
    elif stype == "checklist":
        return _section_checklist(checklist) if checklist else ""
    elif stype == "peer_table":
        return _section_peer_table(peers) if peers else ""
    else:
        # Unknown type — render as text block if value is a string
        if isinstance(value, str) and value:
            return _section_text(title or field, value)
        return ""


# ── Section renderers ─────────────────────────────────────────────────────────

def _section_text(title: str, text) -> str:
    if not text or not isinstance(text, str):
        return ""
    escaped = _p(text)
    return f"""
<div class="section">
  <h2 class="section-title">{_e(title)}</h2>
  <p class="body-text">{escaped}</p>
</div>"""


def _section_bullets(title: str, items) -> str:
    if not items:
        return ""
    if isinstance(items, str):
        items = [items]
    lis = "\n".join(f"  <li>{_e(str(i))}</li>" for i in items if i)
    if not lis:
        return ""
    return f"""
<div class="section">
  <h2 class="section-title">{_e(title)}</h2>
  <ul class="bullet-list">
{lis}
  </ul>
</div>"""


def _section_recommendation(rec: str, rationale: str) -> str:
    rec = (rec or "HOLD").upper().strip()
    colours = {"BUY": (_GREEN, "#1A7E3D"), "SELL": (_RED, "#C0392B")}
    bg, fg = colours.get(rec, (_AMBER, "#D68910"))
    rat_html = f'<p class="body-text" style="margin-top:10px">{_p(rationale)}</p>' if rationale else ""
    return f"""
<div class="section">
  <div class="rec-banner" style="background:{bg};color:{fg}">
    {_e(rec)}
  </div>
  {rat_html}
</div>"""


def _section_score_table(title: str, data) -> str:
    """Render a dict or list-of-dicts as a two-column table."""
    if not data:
        return ""
    rows = ""
    if isinstance(data, dict):
        for k, v in data.items():
            rows += f"<tr><td class='label-cell'>{_e(str(k))}</td><td>{_e(str(v))}</td></tr>"
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                label = item.get("label") or item.get("name") or item.get("key", "")
                value = item.get("value") or item.get("score") or item.get("rating", "")
                note  = item.get("rationale") or item.get("description", "")
                rows += (
                    f"<tr><td class='label-cell'>{_e(str(label))}</td>"
                    f"<td>{_e(str(value))}"
                    + (f"<br><small style='color:{_MUTED}'>{_e(str(note))}</small>" if note else "")
                    + "</td></tr>"
                )
    if not rows:
        return ""
    return f"""
<div class="section">
  <h2 class="section-title">{_e(title)}</h2>
  <table class="data-table">
    <tbody>{rows}</tbody>
  </table>
</div>"""


def _section_key_value(title: str, data) -> str:
    """Render a flat dict as a row of key: value chips."""
    if not isinstance(data, dict) or not data:
        return ""
    chips = " ".join(
        f'<span class="kv-chip"><strong>{_e(str(k))}:</strong> {_e(str(v))}</span>'
        for k, v in data.items()
    )
    return f"""
<div class="section">
  <h2 class="section-title">{_e(title)}</h2>
  <div style="margin-top:8px">{chips}</div>
</div>"""


def _section_fisher_scorecard(title: str, points: list, total, grade: str) -> str:
    if not points:
        return ""

    grade_colours = {"A": _GREEN, "B": "#D1ECF1", "C": _AMBER, "D": "#FDEBD0", "F": _RED}
    grade_bg = grade_colours.get(str(grade).upper(), _GREY)

    rows = ""
    for p in points:
        n    = p.get("number", "")
        ttl  = p.get("title", "")
        sc   = p.get("score", "")
        asmt = p.get("assessment", "")
        rat  = p.get("rationale", "")
        asmt_colour = {"PASS": "#1A7E3D", "FAIL": "#C0392B"}.get(asmt, "#D68910")
        score_bg = _score_bg(sc, 5)
        rows += (
            f"<tr>"
            f"<td style='text-align:center;font-weight:600;width:36px'>{_e(str(n))}</td>"
            f"<td style='font-weight:600'>{_e(str(ttl))}</td>"
            f"<td style='text-align:center;background:{score_bg};font-weight:700;width:44px'>{_e(str(sc))}/5</td>"
            f"<td style='text-align:center;color:{asmt_colour};font-weight:600;width:80px'>{_e(str(asmt))}</td>"
            f"<td style='font-size:12px;color:{_MUTED}'>{_e(str(rat))}</td>"
            f"</tr>"
        )

    summary_row = (
        f"<tr style='background:{grade_bg}'>"
        f"<td colspan='2' style='font-weight:700;text-align:right'>Total Score</td>"
        f"<td style='text-align:center;font-weight:700'>{_e(str(total))}/75</td>"
        f"<td style='text-align:center;font-weight:700'>Grade {_e(str(grade))}</td>"
        f"<td></td></tr>"
    )

    return f"""
<div class="section">
  <h2 class="section-title">{_e(title)}</h2>
  <table class="data-table">
    <thead>
      <tr>
        <th>#</th><th>Point</th><th>Score</th><th>Assessment</th><th>Rationale</th>
      </tr>
    </thead>
    <tbody>{rows}{summary_row}</tbody>
  </table>
</div>"""


def _section_helmer_powers(title: str, powers: list, count, moat: str) -> str:
    if not powers:
        return ""

    strength_colours = {
        "Strong":   ("#D4EDDA", "#1A7E3D"),
        "Moderate": ("#D1ECF1", "#0C5460"),
        "Weak":     (_AMBER,   "#856404"),
        "None":     (_RED,     "#721C24"),
    }

    rows = ""
    for p in powers:
        name = p.get("name", "")
        str_ = p.get("strength", "Weak")
        rat  = p.get("rationale", "")
        bg, fg = strength_colours.get(str_, (_GREY, _TEXT))
        rows += (
            f"<tr>"
            f"<td style='font-weight:600'>{_e(str(name))}</td>"
            f"<td style='background:{bg};color:{fg};font-weight:600;text-align:center;width:100px'>"
            f"{_e(str(str_))}</td>"
            f"<td style='font-size:12px;color:{_MUTED}'>{_e(str(rat))}</td>"
            f"</tr>"
        )

    moat_bg = {"Wide": _GREEN, "Narrow": _AMBER, "None": _RED}.get(moat, _GREY)

    return f"""
<div class="section">
  <h2 class="section-title">{_e(title)}</h2>
  <table class="data-table">
    <thead>
      <tr><th>Power</th><th>Strength</th><th>Rationale</th></tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  <p style="margin-top:10px">
    <strong>Active powers: {_e(str(count))}/7</strong> &nbsp;·&nbsp;
    <span style="background:{moat_bg};padding:2px 10px;border-radius:4px;font-weight:700">
      Moat: {_e(str(moat))}
    </span>
  </p>
</div>"""


def _section_gravity_scorecard(title: str, dims: list, total, grade: str, revenue_model: dict) -> str:
    if not dims:
        return ""

    rows = ""
    for d in dims:
        n   = d.get("number", "")
        ttl = d.get("title", "")
        sc  = d.get("score", "")
        rat = d.get("rationale", "")
        score_bg = _score_bg(sc, 5)
        rows += (
            f"<tr>"
            f"<td style='text-align:center;font-weight:600;width:36px'>{_e(str(n))}</td>"
            f"<td style='font-weight:600'>{_e(str(ttl))}</td>"
            f"<td style='text-align:center;background:{score_bg};font-weight:700;width:44px'>{_e(str(sc))}/5</td>"
            f"<td style='font-size:12px;color:{_MUTED}'>{_e(str(rat))}</td>"
            f"</tr>"
        )

    grade_colours = {"A": _GREEN, "B": "#D1ECF1", "C": _AMBER, "D": "#FDEBD0", "F": _RED}
    grade_bg = grade_colours.get(str(grade).upper(), _GREY)
    summary_row = (
        f"<tr style='background:{grade_bg}'>"
        f"<td colspan='2' style='font-weight:700;text-align:right'>Total Score</td>"
        f"<td style='text-align:center;font-weight:700'>{_e(str(total))}/50</td>"
        f"<td style='font-weight:700'>Grade {_e(str(grade))}</td></tr>"
    )

    rm_html = ""
    if revenue_model:
        rec_pct = revenue_model.get("recurring_pct_estimate", "?")
        vis     = revenue_model.get("revenue_visibility", "?")
        pp      = revenue_model.get("pricing_power", "?")
        cap     = revenue_model.get("capex_intensity", "?")
        rm_html = (
            f'<div style="margin-top:12px;display:flex;gap:10px;flex-wrap:wrap">'
            f'<span class="kv-chip"><strong>Recurring:</strong> ~{_e(str(rec_pct))}%</span>'
            f'<span class="kv-chip"><strong>Visibility:</strong> {_e(str(vis))}</span>'
            f'<span class="kv-chip"><strong>Pricing Power:</strong> {_e(str(pp))}</span>'
            f'<span class="kv-chip"><strong>CapEx:</strong> {_e(str(cap))}</span>'
            f'</div>'
        )

    return f"""
<div class="section">
  <h2 class="section-title">{_e(title)}</h2>
  <table class="data-table">
    <thead>
      <tr><th>#</th><th>Dimension</th><th>Score</th><th>Rationale</th></tr>
    </thead>
    <tbody>{rows}{summary_row}</tbody>
  </table>
  {rm_html}
</div>"""


def _section_financial_table(company: "CompanyData") -> str:
    """Simplified financial history table."""
    if not company:
        return ""
    years = company.sorted_years()[:6]
    if not years:
        return ""

    cur = company.currency or ""

    def _v(val, fmt="M") -> str:
        if val is None:
            return "<span style='color:#999'>n/a</span>"
        if fmt == "M":
            if abs(val) >= 1000:
                return f"{val/1000:,.1f}B"
            return f"{val:,.0f}M"
        elif fmt == "%":
            return f"{val*100:.1f}%"
        elif fmt == "ps":
            return f"{val:.2f}"
        return str(val)

    header_cells = "".join(
        f"<th style='text-align:right'>{y}</th>" for y in years
    )

    def data_row(label, getter, fmt="M") -> str:
        cells = "".join(
            f"<td style='text-align:right'>{_v(getter(company.annual_financials.get(y)), fmt)}</td>"
            for y in years
        )
        return f"<tr><td class='label-cell'>{_e(label)}</td>{cells}</tr>"

    rows = (
        data_row("Revenue",      lambda a: a.revenue if a else None)
        + data_row("EBITDA",     lambda a: a.ebitda if a else None)
        + data_row("EBIT",       lambda a: a.ebit if a else None)
        + data_row("Net Income", lambda a: a.net_income if a else None)
        + data_row("EPS (dil.)", lambda a: a.eps_diluted if a else None, "ps")
        + data_row("FCF",        lambda a: a.fcf if a else None)
        + data_row("Net Margin", lambda a: a.net_margin if a else None, "%")
        + data_row("ROE",        lambda a: a.roe if a else None, "%")
    )

    fe_note = ""
    fe = company.forward_estimates
    if fe:
        fe_note = (
            f'<p style="font-size:11px;color:{_MUTED};margin-top:6px">'
            f'Analyst consensus estimates ({fe.analyst_count or "?"} analysts · source: {fe.source})'
            f'</p>'
        )

    return f"""
<div class="section">
  <h2 class="section-title">Financial Summary ({cur}M)</h2>
  <table class="data-table">
    <thead>
      <tr><th>Metric</th>{header_cells}</tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  {fe_note}
</div>"""


def _section_checklist(checklist: list) -> str:
    if not checklist:
        return ""
    passed = sum(1 for c in checklist if c.get("pass"))
    total  = len(checklist)
    rows   = ""
    for c in checklist:
        ok    = c.get("pass", False)
        icon  = "✓" if ok else "✗"
        color = "#1A7E3D" if ok else "#C0392B"
        rows += (
            f"<tr>"
            f"<td style='color:{color};font-weight:700;text-align:center;width:32px'>{icon}</td>"
            f"<td>{_e(c.get('criterion', ''))}</td>"
            f"<td style='color:{_MUTED};font-size:12px'>{_e(str(c.get('actual', 'n/a')))}</td>"
            f"</tr>"
        )
    return f"""
<div class="section">
  <h2 class="section-title">Investment Checklist — {passed}/{total} criteria met</h2>
  <table class="data-table">
    <thead><tr><th></th><th>Criterion</th><th>Actual</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""


def _section_peer_table(peers: dict) -> str:
    if not peers:
        return ""
    rows = ""
    for ticker, p in peers.items():
        if not p or not p.name:
            continue
        rows += (
            f"<tr>"
            f"<td style='font-weight:600'>{_e(ticker)}</td>"
            f"<td>{_e(p.name or '')}</td>"
            f"<td style='text-align:right'>{_fmt_b(p.market_cap)}</td>"
            f"<td style='text-align:right'>{_fmt_x(p.pe_ratio)}</td>"
            f"<td style='text-align:right'>{_fmt_x(p.ev_ebitda)}</td>"
            f"<td style='text-align:right'>{_fmt_pct(p.ebit_margin)}</td>"
            f"<td style='text-align:right'>{_fmt_pct(p.roe)}</td>"
            f"</tr>"
        )
    if not rows:
        return ""
    return f"""
<div class="section">
  <h2 class="section-title">Peer Comparison</h2>
  <table class="data-table">
    <thead>
      <tr>
        <th>Ticker</th><th>Name</th>
        <th style='text-align:right'>Mkt Cap</th>
        <th style='text-align:right'>P/E</th>
        <th style='text-align:right'>EV/EBITDA</th>
        <th style='text-align:right'>EBIT%</th>
        <th style='text-align:right'>ROE</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""


# ── HTML wrapper ──────────────────────────────────────────────────────────────

def _wrap_html(title, body, framework, company, ticker, cur, rec, index_data=None) -> str:
    rec_upper = (rec or "").upper().strip()
    # Index reports use ACCUMULATE/REDUCE; map to colour buckets
    _rec_colour_map = {
        "BUY": (_GREEN, "#1A7E3D"), "ACCUMULATE": (_GREEN, "#1A7E3D"),
        "SELL": (_RED, "#C0392B"),  "REDUCE":     (_RED, "#C0392B"),
    }
    rec_bg, rec_fg = _rec_colour_map.get(rec_upper, (_AMBER, "#D68910"))

    # Header meta line
    meta_parts = []
    if ticker:
        meta_parts.append(_e(ticker))
    if company and company.sector:
        meta_parts.append(_e(company.sector))
    if company and company.country:
        meta_parts.append(_e(company.country))
    elif index_data:
        meta_parts.append(_e(index_data.index_type))
        if index_data.as_of_date:
            meta_parts.append(f"As of {_e(index_data.as_of_date)}")
    if cur:
        meta_parts.append(f"Currency: {_e(cur)}")
    meta_line = "  ·  ".join(meta_parts)

    rec_badge = ""
    if rec_upper:
        rec_badge = (
            f'<span style="background:{rec_bg};color:{rec_fg};'
            f'padding:3px 14px;border-radius:4px;font-weight:700;font-size:14px">'
            f'{_e(rec_upper)}</span>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_e(title)}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 14px; color: {_TEXT}; background: #fff; padding: 24px 32px;
    max-width: 1100px; margin: 0 auto;
  }}
  .report-header {{
    background: {_NAV}; color: white; padding: 20px 24px;
    border-radius: 6px; margin-bottom: 24px;
  }}
  .report-header h1 {{ font-size: 20px; font-weight: 700; margin-bottom: 4px; }}
  .report-header .meta {{ font-size: 12px; opacity: 0.8; margin-top: 6px; }}
  .report-header .sub {{
    font-size: 13px; opacity: 0.9; margin-top: 4px;
    display: flex; align-items: center; gap: 12px;
  }}
  .section {{
    margin-bottom: 28px; padding-bottom: 20px;
    border-bottom: 1px solid #DDDDDD;
  }}
  .section:last-child {{ border-bottom: none; }}
  .section-title {{
    font-size: 15px; font-weight: 700; color: {_NAV};
    text-transform: uppercase; letter-spacing: 0.5px;
    margin-bottom: 10px; padding-bottom: 5px;
    border-bottom: 2px solid {_MID};
  }}
  .body-text {{
    line-height: 1.7; color: {_TEXT};
    white-space: pre-wrap; word-wrap: break-word;
  }}
  .bullet-list {{
    margin: 6px 0 0 20px; line-height: 1.8;
  }}
  .bullet-list li {{ margin-bottom: 4px; }}
  .rec-banner {{
    font-size: 26px; font-weight: 900; letter-spacing: 2px;
    padding: 14px 24px; border-radius: 6px;
    text-align: center; display: inline-block;
    min-width: 180px; margin-bottom: 10px;
  }}
  .data-table {{
    width: 100%; border-collapse: collapse;
    font-size: 13px; margin-top: 6px;
  }}
  .data-table th {{
    background: {_NAV}; color: white;
    padding: 7px 10px; text-align: left;
    font-size: 12px; font-weight: 600;
  }}
  .data-table td {{
    padding: 6px 10px; border-bottom: 1px solid {_MID};
    vertical-align: top;
  }}
  .data-table tr:nth-child(even) td {{ background: {_LIGHT}; }}
  .data-table tr:hover td {{ background: {_MID}; }}
  .label-cell {{
    font-weight: 600; width: 200px; background: {_LIGHT} !important;
    color: {_NAV};
  }}
  .kv-chip {{
    display: inline-block; background: {_MID};
    color: {_NAV}; border-radius: 4px;
    padding: 3px 10px; font-size: 12px; margin: 3px 4px 3px 0;
  }}
  .footer {{
    margin-top: 32px; padding-top: 12px;
    border-top: 1px solid #DDD;
    font-size: 11px; color: {_MUTED};
    text-align: center;
  }}
</style>
</head>
<body>

<div class="report-header">
  <h1>{_e(title)}</h1>
  <div class="sub">
    {rec_badge}
    <span style="font-size:13px;opacity:0.9">{_e(framework.description)}</span>
  </div>
  {f'<div class="meta">{meta_line}</div>' if meta_line else ''}
</div>

{body}

<div class="footer">
  Generated by Your Humble EquityBot &nbsp;·&nbsp; Framework: {_e(framework.name)}
  {f'&nbsp;·&nbsp; {_e(ticker)}' if ticker else ''}
</div>

</body>
</html>"""


# ── Utilities ─────────────────────────────────────────────────────────────────

def _e(text: str) -> str:
    """HTML-escape a string."""
    return html_lib.escape(str(text)) if text is not None else ""


def _p(text: str) -> str:
    """HTML-escape preserving newlines as <br> for display."""
    return html_lib.escape(str(text)).replace("\n", "<br>") if text else ""


def _score_bg(score, max_score: int) -> str:
    """Return a background colour for a numeric score (green → amber → red)."""
    try:
        ratio = float(score) / float(max_score)
    except (TypeError, ValueError, ZeroDivisionError):
        return _GREY
    if ratio >= 0.7:
        return _GREEN
    elif ratio >= 0.5:
        return _AMBER
    else:
        return _RED


def _fmt_b(v) -> str:
    if v is None:
        return "<span style='color:#999'>n/a</span>"
    if abs(v) >= 1000:
        return f"{v/1000:,.1f}B"
    return f"{v:,.0f}M"


def _fmt_x(v) -> str:
    return f"{v:.1f}x" if v is not None else "<span style='color:#999'>n/a</span>"


def _fmt_pct(v) -> str:
    return f"{v*100:.1f}%" if v is not None else "<span style='color:#999'>n/a</span>"
