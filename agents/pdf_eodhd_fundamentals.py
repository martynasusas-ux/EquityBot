"""
pdf_eodhd_fundamentals.py — EODHD Fundamentals Data From API report.

Four-page comprehensive fundamental analysis built entirely from CompanyData
(populated by the EODHD waterfall).  No LLM call.

Pages:
  1  Company Profile & Valuation   — identity · description · market snapshot
                                     valuation multiples · profitability TTM
                                     dividends · ownership
  2  Income Statement History      — 10-year P&L, margins, YoY growth, EPS
  3  Balance Sheet & Cash Flow     — 10-year BS + CF side-by-side
  4  EPS Trend & Scorecard         — actual/estimate EPS table · forward estimates
                                     rule-based investment scorecard
"""

from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional, List

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Table, TableStyle,
    Spacer, HRFlowable, PageBreak, KeepTogether,
)

from data_sources.base import CompanyData, AnnualFinancials

logger = logging.getLogger(__name__)

# ── Page geometry ─────────────────────────────────────────────────────────────
W, H = A4
ML = MR = 15 * mm
MT = 28 * mm
MB = 12 * mm
CW = W - ML - MR          # ~565 pts

# ── Colours ───────────────────────────────────────────────────────────────────
NAVY    = HexColor("#003F54")   # Pantone 303 — ink-saving brand colour
TEAL    = HexColor("#1A6E5A")
LGRAY   = HexColor("#F5F5F5")
DGRAY   = HexColor("#333333")
MGRAY   = HexColor("#666666")
CGRAY   = HexColor("#999999")
RULE    = HexColor("#DDDDDD")
BORDER  = HexColor("#CCCCCC")
GREEN   = HexColor("#1A7E3D")
RED     = HexColor("#C0392B")
ORANGE  = HexColor("#C9843E")
ESTCOL  = HexColor("#2E4A8A")
HDRFILL = HexColor("#FFFFFF")   # column header fill — white to save ink
SECFILL = HexColor("#F0F7F4")   # light teal tint for section headers
SCFILL  = HexColor("#FDF4EC")   # light orange for scorecard header

GREEN_HEX  = "#1A7E3D"
RED_HEX    = "#C0392B"
NAVY_HEX   = "#003F54"
MGRAY_HEX  = "#666666"
ORANGE_HEX = "#C9843E"
TEAL_HEX   = "#1A6E5A"

BASE_FONT = "Helvetica"
BOLD_FONT = "Helvetica-Bold"

# ── EODHD verified-data checkmark ─────────────────────────────────────────────
_EODHD_CHECK = ' <font name="ZapfDingbats" color="#2E7D32" size="6">4</font>'

LABEL_W   = 130            # slightly narrower to give data columns more room
MAX_DCOLS = 10
DATA_W    = (CW - LABEL_W) / MAX_DCOLS


# ── Formatters ────────────────────────────────────────────────────────────────

def _n(v: Optional[float], dec: int = 1) -> str:
    if v is None: return "—"
    return f"{v:,.{dec}f}"

def _m(v: Optional[float], dec: int = 1) -> str:
    """Value already in millions — always show at least 1 decimal."""
    if v is None: return "—"
    return f"{v:,.{dec}f}"

def _b(v: Optional[float], dec: int = 1) -> str:
    """Value in millions → display as billions."""
    if v is None: return "—"
    b = v / 1_000
    return f"{b:,.{dec}f}B"

def _pct(v: Optional[float], dec: int = 1) -> str:
    """v is a decimal (0.15 = 15%)."""
    if v is None: return "—"
    return f"{v * 100:.{dec}f}%"

def _pct_chg(curr: Optional[float], prev: Optional[float]) -> str:
    if curr is None or prev is None or prev == 0: return "—"
    return f"{(curr - prev) / abs(prev) * 100:+.1f}%"

def _x(v: Optional[float], dec: int = 1) -> str:
    if v is None: return "—"
    return f"{v:.{dec}f}x"

def _ps(v: Optional[float], dec: int = 2) -> str:
    if v is None: return "—"
    return f"{v:.{dec}f}"

def _str(v) -> str:
    return str(v) if v else "—"

def _cur(company: CompanyData) -> str:
    return company.currency_price or company.currency or ""

def _eo(text: str, field: str, company: CompanyData) -> str:
    """Append EODHD checkmark if this field was populated by EODHD."""
    if field in (getattr(company, "eodhd_fields", None) or []):
        return text + _EODHD_CHECK
    return text

def _eo_af(year: int, company: CompanyData) -> str:
    """Return checkmark string if this annual year row came from EODHD."""
    af = (getattr(company, "annual_financials", None) or {}).get(year)
    if af and getattr(af, "source", "") == "eodhd":
        return _EODHD_CHECK
    return ""


# ── Style factory ─────────────────────────────────────────────────────────────

def _S(name, **kw) -> ParagraphStyle:
    return ParagraphStyle(name, **kw)


def _styles() -> dict:
    return {
        "section": _S("sec",
            fontName=BOLD_FONT, fontSize=9, textColor=TEAL,
            spaceBefore=8, spaceAfter=2, leading=12),
        "subsec": _S("ss",
            fontName=BOLD_FONT, fontSize=7.5, textColor=NAVY,
            spaceBefore=4, spaceAfter=1, leading=10),
        "col_hdr": _S("ch",
            fontName=BASE_FONT, fontSize=6.5, textColor=CGRAY,
            alignment=TA_RIGHT, leading=9),
        "col_hdr_lbl": _S("chl",
            fontName=BASE_FONT, fontSize=6.5, textColor=CGRAY,
            alignment=TA_LEFT, leading=9),
        "lbl": _S("lbl",
            fontName=BASE_FONT, fontSize=7.5, textColor=DGRAY,
            alignment=TA_LEFT, leading=10),
        "lbl_bold": _S("lblb",
            fontName=BOLD_FONT, fontSize=7.5, textColor=DGRAY,
            alignment=TA_LEFT, leading=10),
        "lbl_indent": _S("lbind",
            fontName=BASE_FONT, fontSize=7, textColor=MGRAY,
            alignment=TA_LEFT, leading=9, leftIndent=10),
        "cell": _S("cel",
            fontName=BASE_FONT, fontSize=7.5, textColor=DGRAY,
            alignment=TA_RIGHT, leading=10),
        "cell_green": _S("celg",
            fontName=BOLD_FONT, fontSize=7.5, textColor=GREEN,
            alignment=TA_RIGHT, leading=10),
        "cell_red": _S("celr",
            fontName=BOLD_FONT, fontSize=7.5, textColor=RED,
            alignment=TA_RIGHT, leading=10),
        "cell_est": _S("est",
            fontName="Helvetica-Oblique", fontSize=7.5,
            textColor=ESTCOL, alignment=TA_RIGHT, leading=10),
        "cell_chg": _S("chg",
            fontName=BASE_FONT, fontSize=6.5, textColor=MGRAY,
            alignment=TA_RIGHT, leading=9),
        "kv_lbl": _S("kvl",
            fontName=BASE_FONT, fontSize=8, textColor=MGRAY,
            alignment=TA_LEFT, leading=11),
        "kv_val": _S("kvv",
            fontName=BOLD_FONT, fontSize=8, textColor=DGRAY,
            alignment=TA_RIGHT, leading=11),
        "kv_val_l": _S("kvvl",
            fontName=BOLD_FONT, fontSize=8, textColor=DGRAY,
            alignment=TA_LEFT, leading=11),
        "desc": _S("desc",
            fontName=BASE_FONT, fontSize=7.5, textColor=DGRAY,
            leading=11, spaceAfter=4),
        "score_hdr": _S("shdr",
            fontName=BOLD_FONT, fontSize=8, textColor=DGRAY,
            alignment=TA_LEFT, leading=11),
        "score_val": _S("sval",
            fontName=BOLD_FONT, fontSize=8, textColor=DGRAY,
            alignment=TA_CENTER, leading=11),
        "legend": _S("leg",
            fontName=BASE_FONT, fontSize=6, textColor=CGRAY,
            spaceBefore=4, leading=8),
    }


# ── Page header / footer ──────────────────────────────────────────────────────

def _draw_header(canvas, doc, company: CompanyData, report_date: str):
    canvas.saveState()
    cur  = _cur(company)
    name = company.name or company.ticker or ""
    sub  = " | ".join(filter(None, [
        company.country, company.sector, company.industry,
        f"ISIN: {company.isin}" if company.isin else None,
    ]))

    canvas.setFont(BOLD_FONT, 13)
    canvas.setFillColor(black)
    canvas.drawString(ML, H - 12 * mm, name)

    canvas.setFont(BASE_FONT, 8)
    canvas.setFillColor(MGRAY)
    canvas.drawString(ML, H - 17 * mm, sub)

    price_str = (f"{company.current_price:,.2f} {cur}"
                 if company.current_price else "")
    canvas.setFont(BOLD_FONT, 8.5)
    canvas.setFillColor(NAVY)
    canvas.drawRightString(W - MR, H - 11 * mm, price_str)

    canvas.setFont(BASE_FONT, 7.5)
    canvas.setFillColor(CGRAY)
    canvas.drawRightString(W - MR, H - 16 * mm, report_date)

    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.6)
    canvas.line(ML, H - 20 * mm, W - MR, H - 20 * mm)

    canvas.setFont(BASE_FONT, 6.5)
    canvas.setFillColor(CGRAY)
    canvas.drawString(ML, 7 * mm,
        "Your Humble EquityBot  |  For internal use only. Not investment advice.")
    canvas.drawRightString(W - MR, 7 * mm, f"Page {doc.page}  |  {report_date}")
    canvas.restoreState()


# ── Section header ────────────────────────────────────────────────────────────

def _sec(label: str, styles: dict):
    return Paragraph(label.upper(), styles["section"])


# ── Key-value grid helpers ────────────────────────────────────────────────────

def _kv_table(rows: list[tuple], col_w: float, styles: dict) -> Table:
    """Build a 4-column label/value table (pairs fill 2 columns each)."""
    data = []
    for i in range(0, len(rows), 2):
        left  = rows[i]
        right = rows[i + 1] if i + 1 < len(rows) else ("", "")
        data.append([
            Paragraph(left[0],  styles["kv_lbl"]),
            Paragraph(str(left[1]),  styles["kv_val"]),
            Paragraph(right[0], styles["kv_lbl"]),
            Paragraph(str(right[1]), styles["kv_val"]),
        ])
    lw = col_w * 0.4
    vw = col_w * 0.6
    t = Table(data, colWidths=[lw, vw, lw, vw])
    t.setStyle(TableStyle([
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LINEBELOW",  (0, 0), (-1, -2), 0.3, RULE),
    ]))
    return t


# ── Multi-year data table helpers ─────────────────────────────────────────────

def _hdr_row(styles: dict, hist: list, label: str = "FY",
             company: Optional[CompanyData] = None) -> list:
    row = [Paragraph(label, styles["col_hdr_lbl"])]
    for y in hist:
        ck = _eo_af(y, company) if company else ""
        row.append(Paragraph(f"12/{y}{ck}", styles["col_hdr"]))
    return row


def _data_row(label: str, values: list, styles: dict,
              bold: bool = False, indent: bool = False,
              style_key: str = "cell") -> list:
    lbl_style = "lbl_bold" if bold else ("lbl_indent" if indent else "lbl")
    return [Paragraph(label, styles[lbl_style])] + \
           [Paragraph(str(v), styles[style_key]) for v in values]


def _section_row(label: str, ncols: int, styles: dict) -> list:
    """Full-width section sub-header row."""
    return [Paragraph(label, styles["subsec"])] + [""] * ncols


def _table_style(nrows: int, ncols: int, alt: bool = True) -> TableStyle:
    """
    Ink-saving table style: white body, white header, thick navy underline
    under the header row, grey horizontal rules between rows.
    The `alt` flag is kept for backwards compatibility but no longer paints
    the row background — readability is preserved by the row rules.
    """
    cmds = [
        ("FONTNAME",     (0, 0), (-1, 0), BASE_FONT),
        ("FONTSIZE",     (0, 0), (-1, -1), 7.5),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2),
        # White everywhere — no alternating fill
        ("BACKGROUND",   (0, 0), (-1, -1), white),
        # Thick navy underline marks the header row instead of a fill
        ("LINEBELOW",    (0, 0), (-1, 0),  1.2, NAVY),
        # Subtle horizontal rules between body rows preserve readability
        ("LINEBELOW",    (0, 1), (-1, -2), 0.25, BORDER),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  NAVY),
    ]
    return TableStyle(cmds)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — Company Profile & Valuation
# ══════════════════════════════════════════════════════════════════════════════

def _page1(company: CompanyData, styles: dict) -> list:
    el = []
    cur = _cur(company)
    eo  = lambda text, field: _eo(text, field, company)

    # ── Description ──────────────────────────────────────────────────────────
    if company.description:
        el.append(_sec("Business Description", styles))
        desc = company.description[:600] + ("…" if len(company.description) > 600 else "")
        el.append(Paragraph(desc, styles["desc"]))
        el.append(Spacer(1, 4))

    # ── Market Snapshot ───────────────────────────────────────────────────────
    el.append(_sec("Market Snapshot", styles))
    mc  = f"{company.market_cap / 1000:,.1f}B {cur}" if company.market_cap else "—"
    ev  = f"{company.enterprise_value / 1000:,.1f}B {cur}" if company.enterprise_value else "—"
    hi  = f"{company.week_52_high:,.2f}" if company.week_52_high else "—"
    lo  = f"{company.week_52_low:,.2f}" if company.week_52_low else "—"
    ma50  = f"{company.ma_50:,.2f}" if company.ma_50 else "—"
    ma200 = f"{company.ma_200:,.2f}" if company.ma_200 else "—"

    snap_rows = [
        ("Market Cap",        eo(mc, "market_cap")),
        ("Enterprise Value",  eo(ev, "enterprise_value")),
        ("52-Week High",      eo(hi, "week_52_high")),
        ("52-Week Low",       eo(lo, "week_52_low")),
        ("50-Day MA",         eo(ma50, "ma_50")),
        ("200-Day MA",        eo(ma200, "ma_200")),
        ("Beta",              eo(_ps(company.beta), "beta")),
        ("Shares Outstanding",eo(f"{company.shares_outstanding:,.1f}M" if company.shares_outstanding else "—", "shares_outstanding")),
        ("Float",             f"{company.shares_float:,.1f}M" if company.shares_float else "—"),
        ("Book Value / Share",eo(_ps(company.book_value_per_share, 2), "book_value_per_share")),
    ]
    el.append(_kv_table(snap_rows, CW / 2, styles))
    el.append(Spacer(1, 6))

    # ── Two columns: Valuation | Profitability TTM ────────────────────────────
    half = CW / 2 - 4

    # Valuation block
    val_el = [_sec("Valuation Multiples", styles)]
    val_rows = [
        ("P/E (TTM)",       eo(_x(company.pe_ratio), "pe_ratio")),
        ("Forward P/E",     eo(_x(company.forward_pe), "forward_pe")),
        ("PEG Ratio",       eo(_ps(company.peg_ratio), "peg_ratio")),
        ("P/Book",          eo(_x(company.price_to_book), "price_to_book")),
        ("P/Sales",         eo(_x(company.price_to_sales), "price_to_sales")),
        ("EV/EBITDA",       eo(_x(company.ev_ebitda), "ev_ebitda")),
        ("EV/EBIT",         eo(_x(company.ev_ebit), "ev_ebit")),
        ("EV/Sales",        eo(_x(company.ev_sales), "ev_sales")),
        ("FCF Yield",       eo(_pct(company.fcf_yield), "fcf_yield")),
        ("Dividend Yield",  eo(_pct(company.dividend_yield), "dividend_yield")),
    ]
    for i in range(0, len(val_rows), 1):
        lbl, val = val_rows[i]
        val_el.append(Table(
            [[Paragraph(lbl, styles["kv_lbl"]), Paragraph(val, styles["kv_val"])]],
            colWidths=[half * 0.55, half * 0.45],
            style=TableStyle([
                ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
                ("TOPPADDING",    (0,0),(-1,-1), 2),
                ("BOTTOMPADDING", (0,0),(-1,-1), 2),
                ("LINEBELOW",     (0,0),(-1,-1), 0.3, RULE),
            ])
        ))

    # Profitability TTM block
    prof_el = [_sec("Profitability (TTM)", styles)]
    prof_rows = [
        ("Gross Margin",   eo(_pct(company.gross_margin), "gross_margin")),
        ("EBITDA Margin",  eo(_pct(company.ebitda_margin), "ebitda_margin")),
        ("EBIT Margin",    eo(_pct(company.ebit_margin), "ebit_margin")),
        ("Net Margin",     eo(_pct(company.net_margin), "net_margin")),
        ("ROE",            eo(_pct(company.roe), "roe")),
        ("ROA",            eo(_pct(company.roa), "roa")),
        ("ROIC",           eo(_pct(company.roic), "roic")),
        ("Rev / Share",    eo(_ps(company.revenue_per_share), "revenue_per_share")),
        ("EPS (TTM)",      eo(_ps(company.eps_ttm), "eps_ttm")),
        ("Q Rev Growth YoY", _pct(company.quarterly_revenue_growth_yoy)),
    ]
    for lbl, val in prof_rows:
        prof_el.append(Table(
            [[Paragraph(lbl, styles["kv_lbl"]), Paragraph(val, styles["kv_val"])]],
            colWidths=[half * 0.55, half * 0.45],
            style=TableStyle([
                ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
                ("TOPPADDING",    (0,0),(-1,-1), 2),
                ("BOTTOMPADDING", (0,0),(-1,-1), 2),
                ("LINEBELOW",     (0,0),(-1,-1), 0.3, RULE),
            ])
        ))

    two_col = Table(
        [[val_el, prof_el]],
        colWidths=[half, half],
        style=TableStyle([
            ("VALIGN", (0,0),(-1,-1), "TOP"),
            ("LEFTPADDING",  (0,0),(-1,-1), 0),
            ("RIGHTPADDING", (0,0),(-1,-1), 4),
        ])
    )
    el.append(two_col)
    el.append(Spacer(1, 6))

    # ── Dividends & Shareholders ──────────────────────────────────────────────
    el.append(_sec("Dividends & Ownership", styles))
    div_rows = [
        ("Fwd Annual DPS",    eo(_ps(company.forward_annual_dividend_rate, 2), "forward_annual_dividend_rate")),
        ("Fwd Dividend Yield",eo(_pct(company.forward_annual_dividend_yield), "forward_annual_dividend_yield")),
        ("Payout Ratio",      eo(_pct(company.payout_ratio), "payout_ratio")),
        ("Ex-Div Date",       eo(_str(company.ex_dividend_date), "ex_dividend_date")),
        ("% Institutions",    eo(_pct(company.pct_institutions), "pct_institutions")),
        ("% Insiders",        eo(_pct(company.pct_insiders), "pct_insiders")),
    ]
    el.append(_kv_table(div_rows, CW / 2, styles))

    # ── Legend ────────────────────────────────────────────────────────────────
    el.append(Spacer(1, 6))
    el.append(Paragraph(
        '<font name="ZapfDingbats" color="#2E7D32" size="6">4</font>'
        ' = verified EODHD data', styles["legend"]))

    return el


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — Income Statement History
# ══════════════════════════════════════════════════════════════════════════════

def _page2(company: CompanyData, styles: dict) -> list:
    el = []
    cur = _cur(company)

    # 8 most recent years in chronological order
    all_hist = company.sorted_years()           # descending
    hist = list(reversed(all_hist[:10]))        # 10 newest → chronological

    ncols = len(hist)
    col_w = [LABEL_W] + [DATA_W] * ncols
    hdr   = _hdr_row(styles, hist, f"FY ({cur}M)", company)

    def _row(lbl, vals, bold=False, indent=False):
        return _data_row(lbl, vals, styles, bold=bold, indent=indent)

    def _row_pct(lbl, vals, bold=False):
        return _data_row(lbl, vals, styles, bold=bold, style_key="cell")

    def _chg_row(lbl, vals):
        return _data_row(lbl, vals, styles, indent=True, style_key="cell_chg")

    def _get(y, attr):
        af = company.annual_financials.get(y)
        return getattr(af, attr, None) if af else None

    # ── Revenue block ─────────────────────────────────────────────────────────
    el.append(_sec("Income Statement  (millions " + cur + ")", styles))

    rev_vals   = [_m(_get(y, "revenue")) for y in hist]
    gp_vals    = [_m(_get(y, "gross_profit")) for y in hist]
    ebitda_vals= [_m(_get(y, "ebitda")) for y in hist]
    ebit_vals  = [_m(_get(y, "ebit")) for y in hist]
    ni_vals    = [_m(_get(y, "net_income")) for y in hist]
    eps_vals   = [_ps(_get(y, "eps_diluted"), 2) for y in hist]
    dps_vals   = [_ps(_get(y, "dividends_per_share"), 2) for y in hist]

    # Revenue YoY growth
    rev_yoy = []
    for i, y in enumerate(hist):
        if i == 0:
            rev_yoy.append("—")
        else:
            prev_y = hist[i - 1]
            rev_yoy.append(_pct_chg(_get(y, "revenue"), _get(prev_y, "revenue")))

    eps_yoy = []
    for i, y in enumerate(hist):
        if i == 0:
            eps_yoy.append("—")
        else:
            prev_y = hist[i - 1]
            eps_yoy.append(_pct_chg(_get(y, "eps_diluted"), _get(prev_y, "eps_diluted")))

    rows = [
        hdr,
        _row("Revenue",        rev_vals, bold=True),
        _chg_row("  YoY Growth", rev_yoy),
        _row("Gross Profit",   gp_vals),
        _row("EBITDA",         ebitda_vals, bold=True),
        _row("EBIT",           ebit_vals),
        _row("Net Income",     ni_vals, bold=True),
        _row("EPS Diluted",    eps_vals, bold=True),
        _chg_row("  EPS YoY",  eps_yoy),
        _row("DPS",            dps_vals),
    ]

    t = Table(rows, colWidths=col_w)
    ts = _table_style(len(rows), ncols)
    ts.add("BACKGROUND", (0, 0), (-1, 0), HDRFILL)
    t.setStyle(ts)
    el.append(t)
    el.append(Spacer(1, 10))

    # ── Margins block ─────────────────────────────────────────────────────────
    el.append(_sec("Margins (%)", styles))

    gm_vals  = [_pct(_get(y, "gross_margin")) for y in hist]
    em_vals  = [_pct(_get(y, "ebitda_margin")) for y in hist]
    eim_vals = [_pct(_get(y, "ebit_margin")) for y in hist]
    nm_vals  = [_pct(_get(y, "net_margin")) for y in hist]
    roe_vals = [_pct(_get(y, "roe")) for y in hist]
    roa_vals = [_pct(_get(y, "roa")) for y in hist]

    mrows = [
        _hdr_row(styles, hist, "Margin", company),
        _row("Gross Margin",   gm_vals),
        _row("EBITDA Margin",  em_vals, bold=True),
        _row("EBIT Margin",    eim_vals),
        _row("Net Margin",     nm_vals),
        _row("ROE",            roe_vals),
        _row("ROA",            roa_vals),
    ]

    mt = Table(mrows, colWidths=col_w)
    mt.setStyle(_table_style(len(mrows), ncols))
    el.append(mt)
    el.append(Spacer(1, 10))

    # ── Shares outstanding ────────────────────────────────────────────────────
    el.append(_sec("Shares Outstanding  (millions)", styles))
    # shares_outstanding is normalized to millions by DataManager; defensive
    # fallback divides only if a legacy raw value (>1M) leaks through.
    shares_vals = [
        _n(_get(y, "shares_outstanding") / 1e6
           if _get(y, "shares_outstanding") and _get(y, "shares_outstanding") > 1_000_000
           else _get(y, "shares_outstanding"), 2)
        for y in hist
    ]
    srows = [
        _hdr_row(styles, hist, "Shares", company),
        _row("Shares Outstanding", shares_vals),
    ]
    st2 = Table(srows, colWidths=col_w)
    st2.setStyle(_table_style(len(srows), ncols))
    el.append(st2)

    el.append(Spacer(1, 4))
    el.append(Paragraph(
        '<font name="ZapfDingbats" color="#2E7D32" size="6">4</font>'
        ' = verified EODHD data', styles["legend"]))

    return el


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 3 — Balance Sheet & Cash Flow
# ══════════════════════════════════════════════════════════════════════════════

def _page3(company: CompanyData, styles: dict) -> list:
    el = []
    cur = _cur(company)

    all_hist = company.sorted_years()
    hist = list(reversed(all_hist[:10]))
    ncols = len(hist)
    col_w = [LABEL_W] + [DATA_W] * ncols

    def _get(y, attr):
        af = company.annual_financials.get(y)
        return getattr(af, attr, None) if af else None

    def _row(lbl, vals, bold=False, indent=False):
        return _data_row(lbl, vals, styles, bold=bold, indent=indent)

    def _chg_row(lbl, vals):
        return _data_row(lbl, vals, styles, indent=True, style_key="cell_chg")

    # ── Balance Sheet ─────────────────────────────────────────────────────────
    el.append(_sec("Balance Sheet  (millions " + cur + ")", styles))

    ta_vals  = [_m(_get(y, "total_assets")) for y in hist]
    cash_vals= [_m(_get(y, "cash")) for y in hist]
    debt_vals= [_m(_get(y, "total_debt")) for y in hist]
    nd_vals  = [_m(_get(y, "net_debt")) for y in hist]
    eq_vals  = [_m(_get(y, "total_equity")) for y in hist]

    bs_rows = [
        _hdr_row(styles, hist, f"BS ({cur}M)", company),
        _row("Total Assets",        ta_vals, bold=True),
        _row("Cash & Equivalents",  cash_vals),
        _row("Total Debt",          debt_vals),
        _row("Net Debt / (Cash)",   nd_vals, bold=True),
        _row("Total Equity",        eq_vals),
    ]

    bs_t = Table(bs_rows, colWidths=col_w)
    bs_t.setStyle(_table_style(len(bs_rows), ncols))
    el.append(bs_t)
    el.append(Spacer(1, 10))

    # Leverage
    el.append(_sec("Leverage Ratios", styles))
    gearing_vals = []
    for y in hist:
        af = company.annual_financials.get(y)
        if af and af.ebitda and af.ebitda > 0 and af.net_debt is not None:
            gearing_vals.append(_ps(af.net_debt / af.ebitda, 2))
        else:
            gearing_vals.append("—")
    de_vals = []
    for y in hist:
        af = company.annual_financials.get(y)
        if af and af.total_equity and af.total_equity > 0 and af.total_debt is not None:
            de_vals.append(_ps(af.total_debt / af.total_equity, 2))
        else:
            de_vals.append("—")

    lev_rows = [
        _hdr_row(styles, hist, "Leverage", company),
        _row("Net Debt / EBITDA", gearing_vals, bold=True),
        _row("Debt / Equity",     de_vals),
    ]
    lev_t = Table(lev_rows, colWidths=col_w)
    lev_t.setStyle(_table_style(len(lev_rows), ncols))
    el.append(lev_t)
    el.append(Spacer(1, 10))

    # ── Cash Flow ─────────────────────────────────────────────────────────────
    el.append(_sec("Cash Flow  (millions " + cur + ")", styles))

    ocf_vals = [_m(_get(y, "operating_cash_flow")) for y in hist]
    cx_vals  = [_m(_get(y, "capex")) for y in hist]
    fcf_vals = [_m(_get(y, "fcf")) for y in hist]

    # FCF margin (FCF / Revenue)
    fcfm_vals = []
    for y in hist:
        af = company.annual_financials.get(y)
        if af and af.fcf is not None and af.revenue and af.revenue > 0:
            fcfm_vals.append(_pct(af.fcf / af.revenue))
        else:
            fcfm_vals.append("—")

    # Dividends paid (annual_financials doesn't have dividends paid; derive from DPS × shares)
    div_vals = []
    for y in hist:
        af = company.annual_financials.get(y)
        if af and af.dividends_per_share is not None and af.shares_outstanding is not None:
            # shares_outstanding is normalized to millions by DataManager
            shares_m = (af.shares_outstanding / 1e6
                        if af.shares_outstanding > 1_000_000
                        else af.shares_outstanding)
            div_vals.append(_m(af.dividends_per_share * shares_m))
        else:
            div_vals.append("—")

    cf_yoy = []
    for i, y in enumerate(hist):
        if i == 0:
            cf_yoy.append("—")
        else:
            cf_yoy.append(_pct_chg(_get(y, "fcf"), _get(hist[i-1], "fcf")))

    cf_rows = [
        _hdr_row(styles, hist, f"CF ({cur}M)", company),
        _row("Operating Cash Flow",   ocf_vals, bold=True),
        _row("Capital Expenditures",  cx_vals),
        _row("Free Cash Flow",        fcf_vals, bold=True),
        _chg_row("  FCF YoY",         cf_yoy),
        _row("FCF Margin",            fcfm_vals),
        _row("Dividends Paid (est.)", div_vals),
    ]
    cf_t = Table(cf_rows, colWidths=col_w)
    cf_t.setStyle(_table_style(len(cf_rows), ncols))
    el.append(cf_t)

    el.append(Spacer(1, 4))
    el.append(Paragraph(
        '<font name="ZapfDingbats" color="#2E7D32" size="6">4</font>'
        ' = verified EODHD data', styles["legend"]))

    return el


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 4 — EPS Trend, Forward Estimates & Investment Scorecard
# ══════════════════════════════════════════════════════════════════════════════

def _page4(company: CompanyData, styles: dict) -> list:
    el = []
    cur = _cur(company)

    all_hist = company.sorted_years()
    hist = list(reversed(all_hist[:10]))

    # ── EPS History table ─────────────────────────────────────────────────────
    el.append(_sec("EPS Diluted — Historical vs Estimates", styles))

    fe = company.forward_estimates
    est_year = fe.year if fe else None

    # Build columns: hist years + estimate year
    cols = hist + ([est_year] if est_year else [])
    ncols = len(cols)

    # Header row
    hdr_cells = [Paragraph("EPS", styles["col_hdr_lbl"])]
    for y in hist:
        ck = _eo_af(y, company)
        hdr_cells.append(Paragraph(f"12/{y}{ck}", styles["col_hdr"]))
    if est_year:
        hdr_cells.append(Paragraph(f"<i>12/{est_year}E</i>", styles["col_hdr"]))

    def _get(y, attr):
        af = company.annual_financials.get(y)
        return getattr(af, attr, None) if af else None

    eps_hist = [_ps(_get(y, "eps_diluted"), 2) for y in hist]
    eps_est  = [_ps(fe.eps_diluted, 2) if fe else "—"]
    eps_row  = [Paragraph("EPS Diluted", styles["lbl_bold"])] + \
               [Paragraph(v, styles["cell"]) for v in eps_hist] + \
               [Paragraph(eps_est[0], styles["cell_est"])]

    yoy = []
    all_eps_years = hist + ([est_year] if est_year else [])
    all_eps_vals  = [_get(y, "eps_diluted") for y in hist] + \
                    ([fe.eps_diluted if fe else None])
    for i in range(len(all_eps_years)):
        if i == 0:
            yoy.append("—")
        else:
            yoy.append(_pct_chg(all_eps_vals[i], all_eps_vals[i - 1]))
    yoy_row = [Paragraph("  EPS YoY", styles["lbl_indent"])] + \
              [Paragraph(v, styles["cell_chg"]) for v in yoy]

    ni_hist = [_m(_get(y, "net_income")) for y in hist]
    ni_est  = [_m(fe.net_income) if fe else "—"]
    ni_row  = [Paragraph("Net Income", styles["lbl"])] + \
              [Paragraph(v, styles["cell"]) for v in ni_hist] + \
              [Paragraph(ni_est[0], styles["cell_est"])]

    rev_hist = [_m(_get(y, "revenue")) for y in hist]
    rev_est  = [_m(fe.revenue) if fe else "—"]
    rev_row  = [Paragraph(f"Revenue ({cur}M)", styles["lbl"])] + \
               [Paragraph(v, styles["cell"]) for v in rev_hist] + \
               [Paragraph(rev_est[0], styles["cell_est"])]

    col_w = [LABEL_W] + [DATA_W] * ncols
    eps_t = Table([hdr_cells, eps_row, yoy_row, ni_row, rev_row],
                  colWidths=col_w)
    eps_t.setStyle(_table_style(5, ncols))
    el.append(eps_t)
    el.append(Spacer(1, 10))

    # ── Forward Estimates summary ─────────────────────────────────────────────
    if fe:
        el.append(_sec("Forward Estimates — Analyst Consensus", styles))
        fe_rows = [
            ("Est. Revenue",         f"{_m(fe.revenue)} {cur}M"),
            ("Est. EPS",             _ps(fe.eps_diluted, 2)),
            ("Est. Net Income",      f"{_m(fe.net_income)} {cur}M"),
            ("Est. EBITDA",          f"{_m(fe.ebitda)} {cur}M" if fe.ebitda else "—"),
            ("Revenue Growth YoY",   _pct(fe.revenue_growth_yoy)),
            ("EPS Growth YoY",       _pct(fe.eps_growth_yoy)),
            ("Forward P/E",          _x(fe.pe_ratio)),
            ("Analyst Count",        str(fe.analyst_count) if fe.analyst_count else "—"),
        ]
        el.append(_kv_table(fe_rows, CW / 2, styles))
        el.append(Spacer(1, 10))

    # ── Rule-based Investment Scorecard ───────────────────────────────────────
    el.append(_sec("Investment Scorecard  (Rule-Based, No LLM)", styles))

    scores = _compute_scorecard(company)

    # Scorecard header
    sc_hdr = [
        Paragraph("Dimension",   styles["score_hdr"]),
        Paragraph("Signal",      styles["score_hdr"]),
        Paragraph("Detail",      styles["score_hdr"]),
    ]
    sc_rows = [sc_hdr]
    for dim, signal, detail in scores:
        if signal == "✓ Strong":
            s_para = Paragraph(f'<font color="{GREEN_HEX}"><b>{signal}</b></font>', styles["score_val"])
        elif signal == "✓ Good":
            s_para = Paragraph(f'<font color="{TEAL_HEX}">{signal}</font>', styles["score_val"])
        elif signal == "⚠ Mixed":
            s_para = Paragraph(f'<font color="{ORANGE_HEX}">{signal}</font>', styles["score_val"])
        elif signal == "✗ Weak":
            s_para = Paragraph(f'<font color="{RED_HEX}">{signal}</font>', styles["score_val"])
        else:
            s_para = Paragraph(signal, styles["score_val"])
        sc_rows.append([
            Paragraph(dim,    styles["lbl"]),
            s_para,
            Paragraph(detail, styles["lbl_indent"]),
        ])

    sc_t = Table(sc_rows, colWidths=[120, 70, CW - 190])
    sc_t.setStyle(TableStyle([
        ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        # Ink-saving: white scorecard header with thicker green underline
        ("BACKGROUND",    (0, 0), (-1, 0),  white),
        ("BACKGROUND",    (0, 1), (-1, -1), white),
        ("LINEBELOW",     (0, 0), (-1, 0),  1.2, GREEN),
        ("LINEBELOW",     (0, 1), (-1, -1), 0.3, RULE),
    ]))
    el.append(sc_t)
    el.append(Spacer(1, 4))
    el.append(Paragraph(
        "Scorecard is rule-based using available data fields. "
        "Signals: ✓ Strong = clear positive, ✓ Good = moderate positive, "
        "⚠ Mixed = neutral/unclear, ✗ Weak = negative flag.",
        styles["legend"]))
    el.append(Spacer(1, 2))
    el.append(Paragraph(
        '<font name="ZapfDingbats" color="#2E7D32" size="6">4</font>'
        ' = verified EODHD data', styles["legend"]))

    return el


# ══════════════════════════════════════════════════════════════════════════════
# Rule-based scorecard logic
# ══════════════════════════════════════════════════════════════════════════════

def _compute_scorecard(company: CompanyData) -> list:
    """Return list of (dimension, signal, detail) tuples."""
    rows = []
    la = company.latest_annual()

    # 1. Revenue Growth (3-year CAGR)
    cagr3 = company.revenue_cagr(3)
    if cagr3 is not None:
        if cagr3 >= 0.15:
            sig, det = "✓ Strong", f"3Y CAGR {cagr3*100:.1f}% — high-growth"
        elif cagr3 >= 0.07:
            sig, det = "✓ Good",   f"3Y CAGR {cagr3*100:.1f}% — solid growth"
        elif cagr3 >= 0.0:
            sig, det = "⚠ Mixed",  f"3Y CAGR {cagr3*100:.1f}% — modest growth"
        else:
            sig, det = "✗ Weak",   f"3Y CAGR {cagr3*100:.1f}% — revenue declining"
    else:
        sig, det = "⚠ Mixed", "Insufficient history for CAGR"
    rows.append(("Revenue Growth", sig, det))

    # 2. EBITDA Margin (latest)
    em = la.ebitda_margin if la else None
    if em is not None:
        if em >= 0.20:
            sig, det = "✓ Strong", f"EBITDA margin {em*100:.1f}% — excellent"
        elif em >= 0.12:
            sig, det = "✓ Good",   f"EBITDA margin {em*100:.1f}% — healthy"
        elif em >= 0.05:
            sig, det = "⚠ Mixed",  f"EBITDA margin {em*100:.1f}% — thin"
        else:
            sig, det = "✗ Weak",   f"EBITDA margin {em*100:.1f}% — very thin"
    else:
        sig, det = "⚠ Mixed", "EBITDA margin not available"
    rows.append(("Profitability", sig, det))

    # 3. Net Debt / EBITDA (leverage)
    nd = la.net_debt if la else None
    ebitda_la = la.ebitda if la else None
    if nd is not None and ebitda_la and ebitda_la > 0:
        lev = nd / ebitda_la
        if lev < -0.5:
            sig, det = "✓ Strong", f"Net cash (ND/EBITDA {lev:.2f}x) — fortress balance sheet"
        elif lev < 1.0:
            sig, det = "✓ Good",   f"ND/EBITDA {lev:.2f}x — low leverage"
        elif lev < 2.5:
            sig, det = "⚠ Mixed",  f"ND/EBITDA {lev:.2f}x — moderate leverage"
        else:
            sig, det = "✗ Weak",   f"ND/EBITDA {lev:.2f}x — high leverage"
    else:
        sig, det = "⚠ Mixed", "Leverage ratio not computable"
    rows.append(("Balance Sheet", sig, det))

    # 4. FCF Generation (latest)
    fcf = la.fcf if la else None
    rev = la.revenue if la else None
    if fcf is not None and rev and rev > 0:
        fcfm = fcf / rev
        if fcf > 0 and fcfm >= 0.10:
            sig, det = "✓ Strong", f"FCF {fcfm*100:.1f}% of revenue — strong cash conversion"
        elif fcf > 0 and fcfm >= 0.05:
            sig, det = "✓ Good",   f"FCF {fcfm*100:.1f}% of revenue — positive"
        elif fcf > 0:
            sig, det = "⚠ Mixed",  f"FCF {fcfm*100:.1f}% of revenue — marginal"
        else:
            sig, det = "✗ Weak",   "Negative FCF — cash burn"
    else:
        sig, det = "⚠ Mixed", "FCF data not available"
    rows.append(("Cash Generation", sig, det))

    # 5. Valuation — PEG
    peg = company.peg_ratio
    fpe = company.forward_pe
    if peg is not None and peg > 0:
        if peg < 0.75:
            sig, det = "✓ Strong", f"PEG {peg:.2f} — growth at a discount"
        elif peg < 1.5:
            sig, det = "✓ Good",   f"PEG {peg:.2f} — fairly priced for growth"
        elif peg < 2.5:
            sig, det = "⚠ Mixed",  f"PEG {peg:.2f} — moderately expensive"
        else:
            sig, det = "✗ Weak",   f"PEG {peg:.2f} — expensive vs growth"
    elif fpe is not None:
        if fpe < 15:
            sig, det = "✓ Strong", f"Fwd P/E {fpe:.1f}x — attractive"
        elif fpe < 25:
            sig, det = "✓ Good",   f"Fwd P/E {fpe:.1f}x — reasonable"
        elif fpe < 35:
            sig, det = "⚠ Mixed",  f"Fwd P/E {fpe:.1f}x — full valuation"
        else:
            sig, det = "✗ Weak",   f"Fwd P/E {fpe:.1f}x — expensive"
    else:
        sig, det = "⚠ Mixed", "PEG / Fwd P/E not available"
    rows.append(("Valuation", sig, det))

    # 6. ROE
    roe = company.roe
    if roe is not None:
        if roe >= 0.20:
            sig, det = "✓ Strong", f"ROE {roe*100:.1f}% — exceptional returns"
        elif roe >= 0.12:
            sig, det = "✓ Good",   f"ROE {roe*100:.1f}% — solid"
        elif roe >= 0.06:
            sig, det = "⚠ Mixed",  f"ROE {roe*100:.1f}% — below average"
        else:
            sig, det = "✗ Weak",   f"ROE {roe*100:.1f}% — weak returns"
    else:
        sig, det = "⚠ Mixed", "ROE not available"
    rows.append(("Return on Equity", sig, det))

    # 7. EPS trend (last 3 years)
    yrs = company.sorted_years()  # descending
    if len(yrs) >= 3:
        eps_now  = (company.annual_financials[yrs[0]].eps_diluted or 0)
        eps_3yag = (company.annual_financials[yrs[2]].eps_diluted or 0)
        if eps_3yag and eps_3yag > 0 and eps_now > 0:
            eg = (eps_now / eps_3yag) ** (1/2) - 1  # 2-step CAGR
            if eg >= 0.20:
                sig, det = "✓ Strong", f"EPS 2Y CAGR {eg*100:.1f}% — rapid acceleration"
            elif eg >= 0.08:
                sig, det = "✓ Good",   f"EPS 2Y CAGR {eg*100:.1f}% — steady growth"
            elif eg >= 0.0:
                sig, det = "⚠ Mixed",  f"EPS 2Y CAGR {eg*100:.1f}% — slow growth"
            else:
                sig, det = "✗ Weak",   f"EPS declining ({eg*100:.1f}% CAGR)"
        else:
            sig, det = "⚠ Mixed", "EPS trend inconclusive (zero/negative base)"
    else:
        sig, det = "⚠ Mixed", "Insufficient EPS history"
    rows.append(("EPS Momentum", sig, det))

    # 8. Technical position vs MAs
    price = company.current_price
    ma50  = company.ma_50
    ma200 = company.ma_200
    if price and ma50 and ma200:
        above50  = price > ma50
        above200 = price > ma200
        hi52 = company.week_52_high
        lo52 = company.week_52_low
        if hi52 and lo52 and hi52 > lo52:
            pct_range = (price - lo52) / (hi52 - lo52)
        else:
            pct_range = None
        if above50 and above200:
            detail = f"Above both MAs — uptrend"
            if pct_range: detail += f" | {pct_range*100:.0f}% of 52W range"
            sig = "✓ Strong"
        elif above200:
            detail = f"Below 50D MA, above 200D — recovering"
            sig = "⚠ Mixed"
        elif above50:
            detail = f"Above 50D, below 200D — mixed"
            sig = "⚠ Mixed"
        else:
            detail = f"Below both MAs — downtrend"
            if pct_range: detail += f" | {pct_range*100:.0f}% of 52W range"
            sig = "✗ Weak"
    else:
        sig, det = "⚠ Mixed", "Technical data not available"
        detail = det
    rows.append(("Technicals", sig, detail))

    # 9. Dividend consistency
    dy = company.dividend_yield
    pr = company.payout_ratio
    ex = company.ex_dividend_date
    if dy is not None and dy > 0:
        if pr is not None and pr < 0.60:
            sig, det = "✓ Good",  f"Yield {dy*100:.2f}%, payout {pr*100:.0f}% — sustainable"
        elif pr is not None and pr < 1.0:
            sig, det = "⚠ Mixed", f"Yield {dy*100:.2f}%, payout {pr*100:.0f}% — watch ratio"
        elif pr is not None:
            sig, det = "✗ Weak",  f"Yield {dy*100:.2f}%, payout {pr*100:.0f}% — unsustainable"
        else:
            sig, det = "✓ Good",  f"Yield {dy*100:.2f}%"
        if ex:
            det += f" | Ex-div: {ex}"
    else:
        sig, det = "⚠ Mixed", "No dividend / data not available"
    rows.append(("Dividend", sig, det))

    # 10. Data quality indicator
    n_eodhd = len(getattr(company, "eodhd_fields", []))
    n_af_eodhd = sum(
        1 for af in company.annual_financials.values()
        if getattr(af, "source", "") == "eodhd"
    )
    if n_eodhd >= 15 and n_af_eodhd >= 4:
        sig = "✓ Strong"
        det = f"EODHD: {n_eodhd} scalar fields, {n_af_eodhd} annual rows verified"
    elif n_eodhd >= 8:
        sig = "✓ Good"
        det = f"EODHD: {n_eodhd} scalar fields, {n_af_eodhd} annual rows"
    else:
        sig = "⚠ Mixed"
        det = f"Partial EODHD coverage ({n_eodhd} fields)"
    rows.append(("Data Quality", sig, det))

    return rows


# ══════════════════════════════════════════════════════════════════════════════
# Main render entry point
# ══════════════════════════════════════════════════════════════════════════════

class EODHDFundamentalsGenerator:
    """Render the EODHD Fundamentals Data From API report to a PDF file."""

    def render(self, company: CompanyData, output_path: str) -> str:
        report_date = datetime.now().strftime("%d %b %Y")
        styles = _styles()

        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            leftMargin=ML, rightMargin=MR,
            topMargin=MT,  bottomMargin=MB,
            title=f"{company.name or company.ticker} — EODHD Fundamentals",
            author="Your Humble EquityBot",
        )

        def _on_page(canvas, doc):
            _draw_header(canvas, doc, company, report_date)

        story = []

        # Page 1
        story.extend(_page1(company, styles))
        story.append(PageBreak())

        # Page 2
        story.extend(_page2(company, styles))
        story.append(PageBreak())

        # Page 3
        story.extend(_page3(company, styles))
        story.append(PageBreak())

        # Page 4
        story.extend(_page4(company, styles))

        doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
        logger.info("EODHDFundamentals PDF written: %s", output_path)
        return output_path
