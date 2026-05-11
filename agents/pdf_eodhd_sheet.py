"""
pdf_eodhd_sheet.py — Comprehensive EODHD Fundamental Data Sheet.

Uses every field EODHD provides — no price-history dependency for ratios.
All current-period metrics come directly from EODHD's pre-computed values.

Pages:
  1  Company Profile  — identity · business description · market snapshot
                        technical levels · ownership · dividends & splits
  2  Income Statement — 7+ year annual P&L with margins
  3  Balance Sheet    — assets · liabilities · equity · leverage
  4  Cash Flow        — operating · capex · FCF · ratios
"""

from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional

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
W, H  = A4
ML = MR = 15 * mm
MT = 28 * mm
MB = 12 * mm
CW = W - ML - MR          # ~565 pts

# ── Colours ───────────────────────────────────────────────────────────────────
NAVY    = HexColor("#1B3F6E")
ORANGE  = HexColor("#C9843E")
LGRAY   = HexColor("#F5F5F5")
DGRAY   = HexColor("#333333")
MGRAY   = HexColor("#666666")
CGRAY   = HexColor("#999999")
RULE    = HexColor("#DDDDDD")
BORDER  = HexColor("#CCCCCC")
GREEN   = HexColor("#1A7E3D")
RED     = HexColor("#C0392B")
ESTCOL  = HexColor("#2E4A8A")
HDRFILL = HexColor("#EBF0F8")
SECFILL = HexColor("#FDF4EC")   # light orange tint for section headers

GREEN_HEX  = "#1A7E3D"
RED_HEX    = "#C0392B"
NAVY_HEX   = "#1B3F6E"
MGRAY_HEX  = "#666666"
ORANGE_HEX = "#C9843E"

BASE_FONT = "Helvetica"
BOLD_FONT = "Helvetica-Bold"

LABEL_W  = 160
MAX_DCOLS = 8
DATA_W   = (CW - LABEL_W) / MAX_DCOLS


# ── Formatters ────────────────────────────────────────────────────────────────

def _n(v: Optional[float], decimals: int = 1) -> str:
    if v is None:
        return "—"
    fmt = f"{{:,.{decimals}f}}"
    return fmt.format(v)

def _pct(v: Optional[float], decimals: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v * 100:.{decimals}f}%"

def _pct_raw(v: Optional[float], decimals: int = 1) -> str:
    """v is already a percentage (e.g. 5.3 = 5.3%), not a decimal."""
    if v is None:
        return "—"
    return f"{v:.{decimals}f}%"

def _x(v: Optional[float], decimals: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:.{decimals}f}x"

def _ps(v: Optional[float], decimals: int = 2) -> str:
    if v is None:
        return "—"
    return f"{v:.{decimals}f}"

def _m(v: Optional[float], decimals: int = 1) -> str:
    if v is None:
        return "—"
    return f"{v:,.{decimals}f}"

def _chg(curr: Optional[float], prev: Optional[float]) -> str:
    if curr is None or prev is None or prev == 0:
        return "—"
    pct = (curr - prev) / abs(prev) * 100
    return f"{pct:+.1f}%"

def _str(v) -> str:
    return str(v) if v else "—"


# ── Style factory ─────────────────────────────────────────────────────────────

def _S(name, **kw) -> ParagraphStyle:
    return ParagraphStyle(name, **kw)


def _styles() -> dict:
    return {
        "section": _S("sec",
            fontName=BOLD_FONT, fontSize=9, textColor=ORANGE,
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
        "title": _S("title",
            fontName=BOLD_FONT, fontSize=13, textColor=black, leading=16),
        "subtitle": _S("subtitle",
            fontName=BASE_FONT, fontSize=8.5, textColor=MGRAY, leading=11),
        "officer": _S("off",
            fontName=BASE_FONT, fontSize=7, textColor=DGRAY, leading=10),
    }


# ── Page header ───────────────────────────────────────────────────────────────

def _draw_header(canvas, doc, company: CompanyData, report_date: str):
    canvas.saveState()
    cur  = company.currency_price or company.currency or ""
    name = company.name or company.ticker or ""
    sub  = " | ".join(filter(None, [
        company.country, company.sector,
        company.industry,
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
    canvas.drawRightString(W - MR, 7 * mm,
        f"Page {doc.page}  |  {report_date}")
    canvas.restoreState()


# ── KV table helper ───────────────────────────────────────────────────────────

def _kv_table(rows: list[tuple], styles: dict,
              col_split: float = 0.52) -> Table:
    """
    Two-column key/value table.
    rows = [(label, value), ...]  — value may be str or Paragraph.
    """
    data = []
    for lbl, val in rows:
        p_lbl = Paragraph(str(lbl), styles["kv_lbl"])
        if isinstance(val, Paragraph):
            p_val = val
        else:
            p_val = Paragraph(str(val) if val else "—", styles["kv_val"])
        data.append([p_lbl, p_val])

    tbl = Table(data, colWidths=[CW * col_split, CW * (1 - col_split)],
                hAlign="LEFT")
    cmds = [
        ("FONTNAME",    (0, 0), (-1, -1), BASE_FONT),
        ("FONTSIZE",    (0, 0), (-1, -1), 8),
        ("ALIGN",       (0, 0), (0, -1),  "LEFT"),
        ("ALIGN",       (1, 0), (1, -1),  "RIGHT"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",  (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    for i in range(len(data)):
        cmds.append(("LINEBELOW", (0, i), (-1, i), 0.3, RULE))
        if i % 2 == 0:
            cmds.append(("BACKGROUND", (0, i), (-1, i), LGRAY))
    tbl.setStyle(TableStyle(cmds))
    return tbl


def _kv2_table(left_rows: list, right_rows: list, styles: dict) -> Table:
    """
    Side-by-side two-panel KV table (left and right panels).
    Useful for fitting more metrics on one row.
    """
    gap_w  = 14
    panel_w = (CW - gap_w) / 2
    lbl_w  = panel_w * 0.58
    val_w  = panel_w * 0.42

    n = max(len(left_rows), len(right_rows))
    data = []
    for i in range(n):
        ll, lv = left_rows[i] if i < len(left_rows) else ("", "")
        rl, rv = right_rows[i] if i < len(right_rows) else ("", "")
        data.append([
            Paragraph(str(ll), styles["kv_lbl"]),
            Paragraph(str(lv) if lv else "—", styles["kv_val"]),
            Paragraph("", styles["kv_lbl"]),           # gap
            Paragraph(str(rl), styles["kv_lbl"]),
            Paragraph(str(rv) if rv else "—", styles["kv_val"]),
        ])

    tbl = Table(data, colWidths=[lbl_w, val_w, gap_w, lbl_w, val_w],
                hAlign="LEFT")
    cmds = [
        ("FONTNAME",    (0, 0), (-1, -1), BASE_FONT),
        ("FONTSIZE",    (0, 0), (-1, -1), 8),
        ("ALIGN",       (0, 0), (0, -1),  "LEFT"),
        ("ALIGN",       (1, 0), (1, -1),  "RIGHT"),
        ("ALIGN",       (3, 0), (3, -1),  "LEFT"),
        ("ALIGN",       (4, 0), (4, -1),  "RIGHT"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",  (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    for i in range(len(data)):
        cmds.append(("LINEBELOW", (0, i), (1, i), 0.3, RULE))
        cmds.append(("LINEBELOW", (3, i), (4, i), 0.3, RULE))
        if i % 2 == 0:
            cmds.append(("BACKGROUND", (0, i), (1, i), LGRAY))
            cmds.append(("BACKGROUND", (3, i), (4, i), LGRAY))
    tbl.setStyle(TableStyle(cmds))
    return tbl


# ── Financial history helpers ─────────────────────────────────────────────────

def _build_col_plan(company: CompanyData) -> tuple[list[int], list[int]]:
    all_hist = list(reversed(company.sorted_years()))
    hist = list(reversed(all_hist[:8]))   # up to 8 years, chrono
    est  = []
    fe = company.forward_estimates
    if fe and fe.year:
        est = [fe.year]
    return hist, est


def _col_widths(n_data: int) -> list[float]:
    if n_data == 0:
        return [CW]
    dw = (CW - LABEL_W) / max(n_data, 1)
    return [LABEL_W] + [dw] * n_data


def _hdr_row(styles: dict, hist: list[int], est: list[int],
             label: str = "FY") -> list:
    row = [Paragraph(label, styles["col_hdr_lbl"])]
    for y in hist:
        row.append(Paragraph(f"12/{y}", styles["col_hdr"]))
    for y in est:
        row.append(Paragraph(f"<b>12/{y}E</b>", styles["col_hdr"]))
    return row


def _val_p(v: Optional[float], fmt: str, styles: dict,
           is_est: bool = False) -> Paragraph:
    if fmt == "M":    txt = _m(v)
    elif fmt == "%":  txt = _pct(v)
    elif fmt == "x":  txt = _x(v)
    elif fmt == "ps": txt = _ps(v)
    elif fmt == "ps1":txt = _ps(v, 1)
    elif fmt == "B":  txt = ("—" if v is None else f"{v/1000:.1f}")
    else:             txt = str(v) if v is not None else "—"
    st = styles["cell_est"] if is_est else styles["cell"]
    return Paragraph(txt, st)


def _data_row(label: str, hist: list, est: list, afs: dict,
              getter, fmt: str, styles: dict,
              est_getter=None, fe=None,
              bold: bool = False, indent: bool = False) -> list:
    lbl_st = (styles["lbl_indent"] if indent else
              (styles["lbl_bold"] if bold else styles["lbl"]))
    cells = [Paragraph(label, lbl_st)]
    for y in hist:
        af = afs.get(y)
        v  = getter(af) if af else None
        cells.append(_val_p(v, fmt, styles))
    for _ in est:
        v = est_getter(fe) if (est_getter and fe) else None
        cells.append(_val_p(v, fmt, styles, is_est=True))
    return cells


def _chg_row(label: str, hist: list, est: list, afs: dict,
             getter, styles: dict) -> list:
    cells = [Paragraph(label, styles["lbl_indent"])]
    vals = [getter(afs.get(y)) if afs.get(y) else None for y in hist]
    cells.append(Paragraph("", styles["cell_chg"]))
    for i in range(1, len(hist)):
        txt  = _chg(vals[i], vals[i - 1])
        col  = (GREEN_HEX if (vals[i] is not None and vals[i - 1] is not None
                              and vals[i] > vals[i - 1]) else MGRAY_HEX)
        cells.append(Paragraph(f'<font color="{col}">{txt}</font>',
                                styles["cell_chg"]))
    for _ in est:
        cells.append(Paragraph("", styles["cell_chg"]))
    return cells


def _tbl_style(rows: list, section_rows: set = None) -> TableStyle:
    cmds = [
        ("FONTNAME",    (0, 0), (-1, -1), BASE_FONT),
        ("FONTSIZE",    (0, 0), (-1, -1), 7.5),
        ("ALIGN",       (0, 0), (0, -1),  "LEFT"),
        ("ALIGN",       (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",  (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW",   (0, 0), (-1, 0),  0.5, RULE),
    ]
    for i, _ in enumerate(rows):
        if i % 2 == 0 and i > 0:
            cmds.append(("BACKGROUND", (0, i), (-1, i), LGRAY))
        cmds.append(("LINEBELOW", (0, i), (-1, i), 0.3, RULE))
    if section_rows:
        for sr in section_rows:
            cmds.extend([
                ("FONTNAME",   (0, sr), (-1, sr), BOLD_FONT),
                ("BACKGROUND", (0, sr), (-1, sr), HDRFILL),
                ("LINEABOVE",  (0, sr), (-1, sr), 0.5, BORDER),
            ])
    return TableStyle(cmds)


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 1: COMPANY PROFILE
# ═══════════════════════════════════════════════════════════════════════════════

def _build_profile_page(company: CompanyData, styles: dict) -> list:
    story = []
    cur = company.currency_price or company.currency or ""
    la  = company.latest_annual()

    # ── Company description ───────────────────────────────────────────────────
    story.append(Paragraph("Company Profile", styles["section"]))
    story.append(HRFlowable(width=CW, thickness=0.5, color=ORANGE, spaceAfter=5))

    if company.description:
        # Trim to ~400 chars for page fit
        desc = company.description[:500].strip()
        if len(company.description) > 500:
            desc += "…"
        story.append(Paragraph(desc, styles["desc"]))

    story.append(Spacer(1, 4))

    # ── Identity grid (two panels side-by-side) ───────────────────────────────
    left_id = [
        ("Ticker",          company.ticker or "—"),
        ("ISIN",            company.isin or "—"),
        ("Exchange",        company.exchange or "—"),
        ("Reporting currency", company.currency or "—"),
        ("Country",         company.country or "—"),
        ("Sector",          company.sector or "—"),
        ("Industry",        company.industry or "—"),
    ]
    right_id = [
        ("IPO date",        company.ipo_date or "—"),
        ("Fiscal year end", company.fiscal_year_end or "—"),
        ("Employees",       f"{company.employees:,}" if company.employees else "—"),
        ("Website",         company.website or "—"),
        ("Address",         company.address or "—"),
        ("Phone",           company.phone or "—"),
        ("Data sources",    ", ".join(company.data_sources) or "—"),
    ]
    story.append(_kv2_table(left_id, right_id, styles))

    story.append(Spacer(1, 8))

    # ── Current market snapshot ───────────────────────────────────────────────
    story.append(Paragraph("Market Snapshot", styles["section"]))
    story.append(HRFlowable(width=CW, thickness=0.5, color=ORANGE, spaceAfter=5))

    mc = company.market_cap
    mc_str = (f"{mc/1000:.2f}bn {cur}" if mc and mc >= 1000
              else (f"{mc:.0f}m {cur}" if mc else "—"))

    # Distance from 52w levels
    def _dist_52(price, level, label):
        if price and level and level > 0:
            pct = (price - level) / level * 100
            return f"{level:,.2f} ({pct:+.1f}%)"
        return f"{level:,.2f}" if level else "—"

    cp = company.current_price

    left_mkt = [
        ("Current price",        f"{cp:,.2f} {cur}" if cp else "—"),
        ("52-week high",         _dist_52(cp, company.week_52_high, "high")),
        ("52-week low",          _dist_52(cp, company.week_52_low, "low")),
        ("50-day moving avg",    f"{company.ma_50:,.2f}" if company.ma_50 else "—"),
        ("200-day moving avg",   f"{company.ma_200:,.2f}" if company.ma_200 else "—"),
        ("Beta",                 f"{company.beta:.2f}" if company.beta else "—"),
    ]
    right_mkt = [
        ("Market cap",           mc_str),
        ("Enterprise value",
         (f"{company.enterprise_value/1000:.2f}bn {cur}"
          if company.enterprise_value and company.enterprise_value >= 1000
          else (f"{company.enterprise_value:.0f}m {cur}"
                if company.enterprise_value else "—"))),
        ("Shares outstanding",   f"{company.shares_outstanding:.1f}m" if company.shares_outstanding else "—"),
        ("Float shares",         f"{company.shares_float:.1f}m" if company.shares_float else "—"),
        ("% held by insiders",   _pct_raw(company.pct_insiders) if company.pct_insiders else "—"),
        ("% held by institutions", _pct_raw(company.pct_institutions) if company.pct_institutions else "—"),
    ]
    story.append(_kv2_table(left_mkt, right_mkt, styles))

    story.append(Spacer(1, 8))

    # ── Valuation multiples (EODHD pre-computed) ─────────────────────────────
    story.append(Paragraph("Valuation Multiples  (current / TTM — EODHD)", styles["section"]))
    story.append(HRFlowable(width=CW, thickness=0.5, color=ORANGE, spaceAfter=5))

    left_val = [
        ("Trailing P/E",         _x(company.pe_ratio) if company.pe_ratio else "—"),
        ("Forward P/E",          _x(company.forward_pe) if company.forward_pe else "—"),
        ("PEG ratio",            _x(company.peg_ratio) if company.peg_ratio else "—"),
        ("Price / Book (MRQ)",   _x(company.price_to_book) if company.price_to_book else "—"),
        ("Price / Sales (TTM)",  _x(company.price_to_sales) if company.price_to_sales else "—"),
    ]
    right_val = [
        ("EV / Revenue",         _x(company.ev_sales) if company.ev_sales else "—"),
        ("EV / EBITDA",          _x(company.ev_ebitda) if company.ev_ebitda else "—"),
        ("EV / EBIT",            _x(company.ev_ebit) if company.ev_ebit else "—"),
        ("EPS (TTM)",            f"{company.eps_ttm:.2f} {cur}" if company.eps_ttm else "—"),
        ("Book value / share",   f"{company.book_value_per_share:.2f} {cur}" if company.book_value_per_share else "—"),
    ]
    story.append(_kv2_table(left_val, right_val, styles))

    story.append(Spacer(1, 8))

    # ── Profitability & returns ───────────────────────────────────────────────
    story.append(Paragraph("Profitability & Returns  (TTM)", styles["section"]))
    story.append(HRFlowable(width=CW, thickness=0.5, color=ORANGE, spaceAfter=5))

    rev_ps = (f"{company.revenue_per_share:.2f} {cur}"
              if company.revenue_per_share else "—")

    left_prof = [
        ("Gross margin",         _pct(company.gross_margin) if company.gross_margin else "—"),
        ("EBITDA margin",        _pct(company.ebitda_margin) if company.ebitda_margin else "—"),
        ("EBIT / Operating margin", _pct(company.ebit_margin) if company.ebit_margin else "—"),
        ("Net profit margin",    _pct(company.net_margin) if company.net_margin else "—"),
        ("Revenue / share (TTM)", rev_ps),
    ]
    right_prof = [
        ("Return on equity (ROE)", _pct(company.roe) if company.roe else "—"),
        ("Return on assets (ROA)", _pct(company.roa) if company.roa else "—"),
        ("Qtrly revenue growth YoY", _pct(company.quarterly_revenue_growth_yoy) if company.quarterly_revenue_growth_yoy else "—"),
        ("Qtrly earnings growth YoY", _pct(company.quarterly_earnings_growth_yoy) if company.quarterly_earnings_growth_yoy else "—"),
        ("FCF yield",            _pct(company.fcf_yield) if company.fcf_yield else "—"),
    ]
    story.append(_kv2_table(left_prof, right_prof, styles))

    story.append(Spacer(1, 8))

    # ── Dividends & splits ────────────────────────────────────────────────────
    story.append(Paragraph("Dividends & Corporate Actions", styles["section"]))
    story.append(HRFlowable(width=CW, thickness=0.5, color=ORANGE, spaceAfter=5))

    left_div = [
        ("Dividend yield (trailing)",
         _pct(company.dividend_yield) if company.dividend_yield else "—"),
        ("Fwd annual dividend rate",
         f"{company.forward_annual_dividend_rate:.4f} {cur}"
         if company.forward_annual_dividend_rate else "—"),
        ("Fwd annual dividend yield",
         _pct(company.forward_annual_dividend_yield)
         if company.forward_annual_dividend_yield else "—"),
        ("Payout ratio",
         _pct(company.payout_ratio) if company.payout_ratio else "—"),
    ]
    right_div = [
        ("Ex-dividend date",     company.ex_dividend_date or "—"),
        ("Dividend date",        company.dividend_date or "—"),
        ("Last split factor",    company.last_split_factor or "—"),
        ("Last split date",      company.last_split_date or "—"),
    ]
    story.append(_kv2_table(left_div, right_div, styles))

    # ── Officers ──────────────────────────────────────────────────────────────
    if company.officers:
        story.append(Spacer(1, 8))
        story.append(Paragraph("Key Officers", styles["section"]))
        story.append(HRFlowable(width=CW, thickness=0.5, color=ORANGE, spaceAfter=5))

        off_data = []
        for off in company.officers[:8]:
            name  = off.get("name", "")
            title = off.get("title", "")
            off_data.append([
                Paragraph(name,  styles["lbl_bold"]),
                Paragraph(title, styles["lbl"]),
            ])
        off_tbl = Table(off_data, colWidths=[CW * 0.40, CW * 0.60], hAlign="LEFT")
        off_tbl.setStyle(TableStyle([
            ("FONTNAME",   (0, 0), (-1, -1), BASE_FONT),
            ("FONTSIZE",   (0, 0), (-1, -1), 7.5),
            ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING",   (0, 0), (-1, -1), 3),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
            *[("BACKGROUND", (0, i), (-1, i), LGRAY)
              for i in range(len(off_data)) if i % 2 == 0],
            *[("LINEBELOW", (0, i), (-1, i), 0.3, RULE)
              for i in range(len(off_data))],
        ]))
        story.append(off_tbl)

    return story


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 2: INCOME STATEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def _build_income_page(company: CompanyData, hist: list, est: list,
                       styles: dict) -> list:
    story = []
    fe  = company.forward_estimates
    afs = company.annual_financials
    cur = company.currency or ""

    story.append(Paragraph("Income Statement", styles["section"]))
    story.append(HRFlowable(width=CW, thickness=0.5, color=ORANGE, spaceAfter=4))

    hdr  = _hdr_row(styles, hist, est, label=f"FY ({cur}m)")
    rows = [hdr]
    sec  = set()

    def R(lbl, fn, fmt, efn=None, bold=False, indent=False):
        return _data_row(lbl, hist, est, afs, fn, fmt, styles,
                         est_getter=efn, fe=fe, bold=bold, indent=indent)

    def C(fn):
        return _chg_row("  % Change", hist, est, afs, fn, styles)

    rows.append(R("Sales",              lambda a: a.revenue,       "M",
                  efn=lambda f: f.revenue, bold=True))
    rows.append(C(lambda a: a.revenue))
    rows.append(R("Gross profit",       lambda a: a.gross_profit,  "M"))
    rows.append(R("Gross margin (%)",   lambda a: a.gross_margin,  "%", indent=True))
    rows.append(R("EBITDA",             lambda a: a.ebitda,        "M",
                  efn=lambda f: f.ebitda, bold=True))
    rows.append(C(lambda a: a.ebitda))
    rows.append(R("EBITDA margin (%)",  lambda a: a.ebitda_margin, "%", indent=True))
    rows.append(R("D&A",
                  lambda a: (-(a.ebitda - a.ebit)
                             if a.ebitda and a.ebit else None), "M", indent=True))
    rows.append(R("EBIT",               lambda a: a.ebit,          "M", bold=True))
    rows.append(C(lambda a: a.ebit))
    rows.append(R("EBIT margin (%)",    lambda a: a.ebit_margin,   "%", indent=True))
    rows.append(R("Net profit",         lambda a: a.net_income,    "M",
                  efn=lambda f: f.net_income, bold=True))
    rows.append(C(lambda a: a.net_income))
    rows.append(R("Net margin (%)",     lambda a: a.net_margin,    "%", indent=True))

    sec.add(len(rows))
    rows.append(R("Per Share", lambda a: None, "M", bold=True))
    rows.append(R("EPS (diluted)",
                  lambda a: a.eps_diluted,          "ps",
                  efn=lambda f: f.eps_diluted))
    rows.append(C(lambda a: a.eps_diluted))
    rows.append(R("DPS",
                  lambda a: a.dividends_per_share, "ps"))

    sec.add(len(rows))
    rows.append(R("Consensus Estimates", lambda a: None, "M", bold=True))
    rows.append(R("  Consensus Sales",   lambda a: None, "M",
                  efn=lambda f: f.revenue))
    rows.append(R("  Consensus EBITDA",  lambda a: None, "M",
                  efn=lambda f: f.ebitda))
    rows.append(R("  Consensus EPS",     lambda a: None, "ps",
                  efn=lambda f: f.eps_diluted))

    cw = _col_widths(len(hist) + len(est))
    tbl = Table(rows, colWidths=cw, hAlign="LEFT", repeatRows=1)
    tbl.setStyle(_tbl_style(rows, section_rows=sec))
    story.append(tbl)
    return story


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 3: BALANCE SHEET
# ═══════════════════════════════════════════════════════════════════════════════

def _build_balance_page(company: CompanyData, hist: list, est: list,
                        styles: dict) -> list:
    story = []
    fe  = company.forward_estimates
    afs = company.annual_financials
    cur = company.currency or ""

    story.append(Paragraph("Balance Sheet", styles["section"]))
    story.append(HRFlowable(width=CW, thickness=0.5, color=ORANGE, spaceAfter=4))

    hdr  = _hdr_row(styles, hist, est, label=f"FY ({cur}m)")
    rows = [hdr]
    sec  = set()

    def R(lbl, fn, fmt, bold=False, indent=False):
        return _data_row(lbl, hist, est, afs, fn, fmt, styles,
                         bold=bold, indent=indent)

    def bvps(af):
        if af.total_equity and af.shares_outstanding and af.shares_outstanding > 0:
            return af.total_equity / af.shares_outstanding
        return None

    # Assets
    sec.add(len(rows))
    rows.append(R("Assets", lambda a: None, "M", bold=True))
    rows.append(R("Cash & equivalents",      lambda a: a.cash,         "M"))
    rows.append(R("Total assets",            lambda a: a.total_assets, "M", bold=True))

    # Liabilities
    sec.add(len(rows))
    rows.append(R("Liabilities & Equity", lambda a: None, "M", bold=True))
    rows.append(R("Total debt",            lambda a: a.total_debt,   "M"))
    rows.append(R("Net debt",              lambda a: a.net_debt,     "M", bold=True))
    rows.append(R("Shareholders' equity",  lambda a: a.total_equity, "M", bold=True))
    rows.append(R("Shares outstanding (m)",lambda a: a.shares_outstanding, "ps1"))
    rows.append(R("Book value per share",  bvps, "ps"))

    # Leverage
    sec.add(len(rows))
    rows.append(R("Leverage & Returns", lambda a: None, "M", bold=True))
    rows.append(R("Net debt / EBITDA (x)",
                  lambda a: (a.net_debt / a.ebitda
                             if a.net_debt is not None and a.ebitda and a.ebitda > 0
                             else None), "x"))
    rows.append(R("Net debt / FCF (x)",
                  lambda a: (a.net_debt / a.fcf
                             if a.net_debt is not None and a.fcf and a.fcf != 0
                             else None), "x"))
    rows.append(R("Gearing — net debt / equity (%)",
                  lambda a: (a.net_debt / a.total_equity
                             if a.net_debt is not None and a.total_equity and a.total_equity > 0
                             else None), "%"))
    rows.append(R("ROE (%)",  lambda a: a.roe, "%"))
    rows.append(R("ROA (%)",  lambda a: a.roa, "%"))
    rows.append(R("Invested capital (equity + net debt)",
                  lambda a: (((a.total_equity or 0) + (a.net_debt or 0)) or None), "M"))

    cw = _col_widths(len(hist) + len(est))
    tbl = Table(rows, colWidths=cw, hAlign="LEFT", repeatRows=1)
    tbl.setStyle(_tbl_style(rows, section_rows=sec))
    story.append(tbl)
    return story


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 4: CASH FLOW
# ═══════════════════════════════════════════════════════════════════════════════

def _build_cashflow_page(company: CompanyData, hist: list, est: list,
                         styles: dict) -> list:
    story = []
    fe  = company.forward_estimates
    afs = company.annual_financials
    cur = company.currency or ""

    story.append(Paragraph("Cash Flow Statement", styles["section"]))
    story.append(HRFlowable(width=CW, thickness=0.5, color=ORANGE, spaceAfter=4))

    hdr  = _hdr_row(styles, hist, est, label=f"FY ({cur}m)")
    rows = [hdr]
    sec  = set()

    def R(lbl, fn, fmt, bold=False, indent=False):
        return _data_row(lbl, hist, est, afs, fn, fmt, styles,
                         bold=bold, indent=indent)

    def C(fn):
        return _chg_row("  % Change", hist, est, afs, fn, styles)

    rows.append(R("Operating cash flow",  lambda a: a.operating_cash_flow, "M", bold=True))
    rows.append(C(lambda a: a.operating_cash_flow))
    rows.append(R("Capital expenditure (Capex)",
                  lambda a: -a.capex if a.capex else None, "M", indent=True))
    rows.append(R("Capex / Sales (%)",
                  lambda a: (a.capex / a.revenue
                             if a.capex and a.revenue and a.revenue > 0
                             else None), "%", indent=True))
    rows.append(R("Free cash flow (FCF)", lambda a: a.fcf, "M", bold=True))
    rows.append(C(lambda a: a.fcf))

    sec.add(len(rows))
    rows.append(R("FCF Ratios", lambda a: None, "M", bold=True))
    rows.append(R("FCF / Sales (%)",
                  lambda a: (a.fcf / a.revenue
                             if a.fcf is not None and a.revenue and a.revenue > 0
                             else None), "%"))
    rows.append(R("FCF yield (%)",  lambda a: a.fcf_yield,  "%"))
    rows.append(R("FCF per share",
                  lambda a: (a.fcf / a.shares_outstanding
                             if a.fcf is not None and a.shares_outstanding
                             and a.shares_outstanding > 0 else None), "ps"))
    rows.append(R("Dividend per share",  lambda a: a.dividends_per_share, "ps"))

    cw = _col_widths(len(hist) + len(est))
    tbl = Table(rows, colWidths=cw, hAlign="LEFT", repeatRows=1)
    tbl.setStyle(_tbl_style(rows, section_rows=sec))
    story.append(tbl)
    return story


# ═══════════════════════════════════════════════════════════════════════════════
# Main renderer
# ═══════════════════════════════════════════════════════════════════════════════

class EODHDSheetGenerator:
    """Generate a comprehensive EODHD Fundamental Data Sheet PDF."""

    def render(self, company: CompanyData, output_path: str) -> str:
        report_date = datetime.now().strftime("%d %b %Y")
        styles = _styles()
        hist, est = _build_col_plan(company)

        def _on_page(canvas, doc):
            _draw_header(canvas, doc, company, report_date)

        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            leftMargin=ML, rightMargin=MR,
            topMargin=MT, bottomMargin=MB,
            title=f"{company.name or company.ticker} — EODHD Data Sheet",
            author="Your Humble EquityBot",
        )

        story = []
        story += _build_profile_page(company, styles)
        story.append(PageBreak())
        story += _build_income_page(company, hist, est, styles)
        story.append(PageBreak())
        story += _build_balance_page(company, hist, est, styles)
        story.append(PageBreak())
        story += _build_cashflow_page(company, hist, est, styles)

        doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
        logger.info(f"[EODHDSheet] Saved to {output_path}")
        return output_path
