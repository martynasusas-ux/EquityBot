"""
pdf_kepler.py — Kepler Cheuvreux-style analyst summary sheet.

Pages:
  1  Summary         — target price · key KPI table · market data
  2  Valuation       — per share data · share price · EV · multiples
  3  Income Statement — full P&L with margins and consensus
  4  Cash Flow        — operating CF · capex · FCF and ratios
  5  Balance Sheet    — assets · liabilities · equity · leverage ratios

Design: clean white background, orange section headers, fine gray rules,
data-dense tables with 7 historical years + 1-2 forward estimate columns.
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
W, H  = A4                        # 595.3 × 841.9 pts
ML = MR = 15 * mm                 # left / right margin
MT = 28 * mm                      # top margin (header drawn on canvas)
MB = 12 * mm                      # bottom margin
CW = W - ML - MR                  # content width ≈ 565 pts

# ── Colour palette ────────────────────────────────────────────────────────────
NAVY    = HexColor("#1B3F6E")
ORANGE  = HexColor("#C9843E")      # Kepler section-title orange
GOLD    = HexColor("#D4A35A")      # lighter gold for alt use
LGRAY   = HexColor("#F5F5F5")      # alt row fill
DGRAY   = HexColor("#333333")      # body text
MGRAY   = HexColor("#666666")
CGRAY   = HexColor("#999999")      # column-header grey
RULE    = HexColor("#DDDDDD")      # thin horizontal rules
BORDER  = HexColor("#CCCCCC")
GREEN   = HexColor("#1A7E3D")
RED     = HexColor("#C0392B")
ESTCOL  = HexColor("#2E4A8A")      # estimate column label colour (blue-ish)
HDRFILL = HexColor("#E8EFF8")      # column header background

# Plain hex strings for use inside Paragraph XML markup (ReportLab's
# HexColor.hexval() returns "0xRRGGBB" not "#RRGGBB", so we keep these
# separately rather than calling .hexval() at runtime).
GREEN_HEX = "#1A7E3D"
RED_HEX   = "#C0392B"
NAVY_HEX  = "#1B3F6E"
MGRAY_HEX = "#666666"
ESTCOL_HEX = "#2E4A8A"

BASE_FONT = "Helvetica"
BOLD_FONT = "Helvetica-Bold"

# ── Label column width and data column width ──────────────────────────────────
LABEL_W  = 148          # pts — row-label column
MAX_DCOLS = 8           # max data columns (historical + forward)
DATA_W   = (CW - LABEL_W) / MAX_DCOLS   # ~52 pts each


# ── Formatters ────────────────────────────────────────────────────────────────

def _m(v: Optional[float], decimals: int = 1) -> str:
    """Format a millions value: '9,935.0' or '-423.0' or 'n/a'."""
    if v is None:
        return "n/a"
    fmt = f"{{:,.{decimals}f}}"
    return fmt.format(v)


def _pct(v: Optional[float], decimals: int = 1) -> str:
    """Format decimal fraction as percentage: 0.154 → '15.4%'."""
    if v is None:
        return "n/a"
    return f"{v * 100:.{decimals}f}%"


def _x(v: Optional[float], decimals: int = 1) -> str:
    """Format a multiple: '25.8'."""
    if v is None:
        return "n/a"
    return f"{v:.{decimals}f}"


def _ps(v: Optional[float], decimals: int = 2) -> str:
    """Format per-share value: '24.65'."""
    if v is None:
        return "n/a"
    return f"{v:.{decimals}f}"


def _chg(curr: Optional[float], prev: Optional[float]) -> str:
    """YoY % change between two values."""
    if curr is None or prev is None or prev == 0:
        return "n/a"
    pct = (curr - prev) / abs(prev) * 100
    return f"{pct:+.1f}%"


def _ratio(num: Optional[float], den: Optional[float], decimals: int = 1) -> str:
    """Safe division for derived ratios like P/BV."""
    if num is None or den is None or den == 0:
        return "n/a"
    return f"{num / den:.{decimals}f}"


def _bvps(af: AnnualFinancials) -> Optional[float]:
    """Book value per share = total_equity / shares_outstanding (millions)."""
    if af.total_equity is None or af.shares_outstanding is None or af.shares_outstanding <= 0:
        return None
    return af.total_equity / af.shares_outstanding


def _cfps(af: AnnualFinancials) -> Optional[float]:
    """Cash flow per share = OCF / shares."""
    if af.operating_cash_flow is None or af.shares_outstanding is None or af.shares_outstanding <= 0:
        return None
    return af.operating_cash_flow / af.shares_outstanding


def _fcf_ps(af: AnnualFinancials) -> Optional[float]:
    """FCF per share."""
    if af.fcf is None or af.shares_outstanding is None or af.shares_outstanding <= 0:
        return None
    return af.fcf / af.shares_outstanding


def _pbv(af: AnnualFinancials) -> Optional[float]:
    bv = _bvps(af)
    if bv is None or bv <= 0 or af.price_year_end is None:
        return None
    return af.price_year_end / bv


def _ev_ebitda(af: AnnualFinancials) -> Optional[float]:
    if af.enterprise_value is None or af.ebitda is None or af.ebitda <= 0:
        return None
    return af.enterprise_value / af.ebitda


def _nd_ebitda(af: AnnualFinancials) -> Optional[float]:
    if af.net_debt is None or af.ebitda is None or af.ebitda <= 0:
        return None
    return af.net_debt / af.ebitda


def _gearing(af: AnnualFinancials) -> Optional[float]:
    """Net debt / total equity."""
    if af.net_debt is None or af.total_equity is None or af.total_equity <= 0:
        return None
    return af.net_debt / af.total_equity


def _ev_ic(af: AnnualFinancials) -> Optional[float]:
    """EV / Invested Capital (equity + net debt)."""
    if af.enterprise_value is None:
        return None
    ic = (af.total_equity or 0) + (af.net_debt or 0)
    if ic <= 0:
        return None
    return af.enterprise_value / ic


def _capex_sales(af: AnnualFinancials) -> Optional[float]:
    if af.capex is None or af.revenue is None or af.revenue <= 0:
        return None
    return af.capex / af.revenue


def _fcf_sales(af: AnnualFinancials) -> Optional[float]:
    if af.fcf is None or af.revenue is None or af.revenue <= 0:
        return None
    return af.fcf / af.revenue


# ── Style helpers ─────────────────────────────────────────────────────────────

def _S(name, **kw) -> ParagraphStyle:
    return ParagraphStyle(name, **kw)


def _styles() -> dict:
    return {
        # Section title  (orange, like Kepler)
        "section": _S("sec",
            fontName=BOLD_FONT, fontSize=9.5, textColor=ORANGE,
            spaceBefore=10, spaceAfter=3, leading=13),
        # Column header (gray, small)
        "col_hdr": _S("ch",
            fontName=BASE_FONT, fontSize=7, textColor=CGRAY,
            alignment=TA_RIGHT, leading=9),
        "col_hdr_lbl": _S("chl",
            fontName=BASE_FONT, fontSize=7, textColor=CGRAY,
            alignment=TA_LEFT, leading=9),
        # Sub-section bold label (e.g. "Per share data")
        "subsec": _S("ss",
            fontName=BOLD_FONT, fontSize=8, textColor=DGRAY,
            spaceBefore=4, spaceAfter=1, leading=10),
        # Row label
        "lbl": _S("lbl",
            fontName=BASE_FONT, fontSize=7.5, textColor=DGRAY,
            alignment=TA_LEFT, leading=10),
        "lbl_bold": _S("lblb",
            fontName=BOLD_FONT, fontSize=7.5, textColor=DGRAY,
            alignment=TA_LEFT, leading=10),
        "lbl_indent": _S("lbind",
            fontName=BASE_FONT, fontSize=7, textColor=MGRAY,
            alignment=TA_LEFT, leading=9, leftIndent=8),
        # Data cell
        "cell": _S("cel",
            fontName=BASE_FONT, fontSize=7.5, textColor=DGRAY,
            alignment=TA_RIGHT, leading=10),
        "cell_est": _S("est",
            fontName="Helvetica-Oblique", fontSize=7.5,
            textColor=ESTCOL, alignment=TA_RIGHT, leading=10),
        "cell_chg": _S("chg",
            fontName=BASE_FONT, fontSize=6.5, textColor=MGRAY,
            alignment=TA_RIGHT, leading=9),
        # Header bar text
        "hdr_name": _S("hn",
            fontName=BOLD_FONT, fontSize=13, textColor=black, leading=16),
        "hdr_sub": _S("hs",
            fontName=BASE_FONT, fontSize=8.5, textColor=MGRAY, leading=11),
        # Market data
        "mkt_lbl": _S("ml",
            fontName=BASE_FONT, fontSize=8, textColor=DGRAY,
            alignment=TA_LEFT, leading=11),
        "mkt_val": _S("mv",
            fontName=BOLD_FONT, fontSize=8, textColor=DGRAY,
            alignment=TA_RIGHT, leading=11),
        # Target price block
        "tp_key": _S("tpk",
            fontName=BOLD_FONT, fontSize=8.5, textColor=DGRAY, leading=12),
        "tp_val": _S("tpv",
            fontName=BOLD_FONT, fontSize=11, textColor=NAVY, leading=14),
        "tp_sub": _S("tps",
            fontName=BASE_FONT, fontSize=8, textColor=MGRAY, leading=11),
    }


# ── Page-header canvas callback ───────────────────────────────────────────────

def _draw_page_header(canvas, doc, company: CompanyData, report_date: str):
    """Light page header matching Kepler style: name + sub-line + right info + rule."""
    canvas.saveState()
    cur = company.currency_price or company.currency or ""
    name = company.name or (company.ticker or "")
    sub  = " | ".join(filter(None, [company.country, company.sector]))

    # Company name — left
    canvas.setFont(BOLD_FONT, 13)
    canvas.setFillColor(black)
    canvas.drawString(ML, H - 12 * mm, name)

    # Sub-line
    canvas.setFont(BASE_FONT, 8)
    canvas.setFillColor(MGRAY)
    canvas.drawString(ML, H - 17 * mm, sub)

    # Right: price + date
    price_str = (
        f"{company.current_price:,.2f} {cur}" if company.current_price else ""
    )
    canvas.setFont(BOLD_FONT, 8.5)
    canvas.setFillColor(NAVY)
    canvas.drawRightString(W - MR, H - 11 * mm, price_str)
    canvas.setFont(BASE_FONT, 7.5)
    canvas.setFillColor(CGRAY)
    canvas.drawRightString(W - MR, H - 16 * mm, report_date)

    # Thin horizontal rule
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.6)
    canvas.line(ML, H - 20 * mm, W - MR, H - 20 * mm)

    # Footer
    canvas.setFont(BASE_FONT, 6.5)
    canvas.setFillColor(CGRAY)
    canvas.drawString(ML, 7 * mm,
        "Your Humble EquityBot  |  For internal use only. Not investment advice.")
    canvas.drawRightString(W - MR, 7 * mm,
        f"Page {doc.page}  |  {report_date}")

    canvas.restoreState()


# ── Column plan ───────────────────────────────────────────────────────────────

def _build_col_plan(company: CompanyData) -> tuple[list[int], list[int]]:
    """
    Return (hist_years_chrono, est_years).

    hist_years_chrono: up to 7 historical years, oldest first.
    est_years: forward estimate year(s), oldest first (usually 1).
    """
    all_hist = list(reversed(company.sorted_years()))   # most recent first
    hist = list(reversed(all_hist[:7]))                 # up to 7, chrono order
    est  = []
    fe = company.forward_estimates
    if fe and fe.year:
        est = [fe.year]
    return hist, est


# ── Table builders ────────────────────────────────────────────────────────────

def _col_widths(n_data: int) -> list[float]:
    """Return [label_width, d1, d2, ...] for n_data data columns."""
    if n_data == 0:
        return [CW]
    dw = (CW - LABEL_W) / max(n_data, 1)
    return [LABEL_W] + [dw] * n_data


def _tbl_style(rows: list, shade_freq: int = 2, section_rows: set = None) -> TableStyle:
    """Generic table style with alternating light fill and thin rules."""
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
        ("LINEBELOW",   (0, 0), (-1, 0),  0.5, RULE),   # after header row
    ]
    for i, _ in enumerate(rows):
        if i % shade_freq == 0 and i > 0:
            cmds.append(("BACKGROUND", (0, i), (-1, i), LGRAY))
        cmds.append(("LINEBELOW", (0, i), (-1, i), 0.3, RULE))
    if section_rows:
        for sr in section_rows:
            cmds.append(("FONTNAME",    (0, sr), (-1, sr), BOLD_FONT))
            cmds.append(("BACKGROUND",  (0, sr), (-1, sr), white))
            cmds.append(("LINEABOVE",   (0, sr), (-1, sr), 0.5, BORDER))
    return TableStyle(cmds)


def _hdr_row(
    styles: dict,
    hist_years: list[int],
    est_years: list[int],
    label: str = "FY",
    label_suffix: str = "",
) -> list:
    """Build a column-header row: [label, y1, y2, ..., E1, ...]."""
    row = [Paragraph(f"{label}{label_suffix}", styles["col_hdr_lbl"])]
    for y in hist_years:
        row.append(Paragraph(f"12/{y}", styles["col_hdr"]))
    for y in est_years:
        row.append(Paragraph(f"<b>12/{y}E</b>", styles["col_hdr"]))
    return row


def _val(v: Optional[float], fmt: str, styles: dict, is_est: bool = False) -> Paragraph:
    """Format a data value into a styled Paragraph."""
    if fmt == "M":
        txt = _m(v)
    elif fmt == "%":
        txt = _pct(v)
    elif fmt == "x":
        txt = _x(v)
    elif fmt == "ps":
        txt = _ps(v)
    elif fmt == "ps1":
        txt = _ps(v, 1)
    elif fmt == "B":   # billions
        if v is None:
            txt = "n/a"
        else:
            txt = f"{v/1000:.1f}"
    elif fmt == "int":
        txt = str(int(round(v))) if v is not None else "n/a"
    else:
        txt = str(v) if v is not None else "n/a"

    st = styles["cell_est"] if is_est else styles["cell"]
    return Paragraph(txt, st)


def _row(
    label: str,
    hist_years: list[int],
    est_years: list[int],
    afs: dict,
    getter,
    fmt: str,
    styles: dict,
    est_getter=None,
    fe=None,
    bold_label: bool = False,
    indent: bool = False,
) -> list:
    """Build one data row."""
    lbl_style = (
        styles["lbl_indent"] if indent else
        (styles["lbl_bold"] if bold_label else styles["lbl"])
    )
    cells = [Paragraph(label, lbl_style)]
    for y in hist_years:
        af = afs.get(y)
        v  = getter(af) if af else None
        cells.append(_val(v, fmt, styles, is_est=False))
    for y in est_years:
        v = est_getter(fe) if (est_getter and fe) else None
        cells.append(_val(v, fmt, styles, is_est=True))
    return cells


def _chg_row(
    label: str,
    hist_years: list[int],
    est_years: list[int],
    afs: dict,
    getter,
    styles: dict,
    est_getter=None,
    fe=None,
) -> list:
    """Build a '% Change' sub-row."""
    cells = [Paragraph(label, styles["lbl_indent"])]
    vals = []
    for y in hist_years:
        af = afs.get(y)
        vals.append(getter(af) if af else None)
    for yi, (cur_v, prev_v) in enumerate(zip(vals[1:], vals[:-1]), start=1):
        pass  # just for logic clarity

    # First cell: no prev
    cells.append(Paragraph("", styles["cell_chg"]))
    for i in range(1, len(hist_years)):
        txt = _chg(vals[i], vals[i - 1])
        col_hex = GREEN_HEX if (vals[i] is not None and vals[i - 1] is not None
                                and vals[i] > vals[i - 1]) else MGRAY_HEX
        cells.append(Paragraph(f'<font color="{col_hex}">{txt}</font>',
                                styles["cell_chg"]))
    for y in est_years:
        cells.append(Paragraph("", styles["cell_chg"]))
    return cells


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 1: SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

def _build_summary_page(
    company: CompanyData,
    analysis: dict,
    hist_years: list[int],
    est_years: list[int],
    styles: dict,
) -> list:
    """Build flowables for the Summary page."""
    story = []
    fe   = company.forward_estimates
    afs  = company.annual_financials
    cur  = company.currency or company.currency_price or ""

    # ── "Summary" section title ───────────────────────────────────────────────
    story.append(Paragraph("Summary", styles["section"]))
    story.append(HRFlowable(width=CW, thickness=0.5, color=ORANGE, spaceAfter=6))

    # ── Target Price / Latest Price / Upside block ────────────────────────────
    tp    = analysis.get("target_price")
    rec   = analysis.get("recommendation", "n/a")
    thesis = analysis.get("key_thesis", "")
    val_method = analysis.get("valuation_method", "")
    cp    = company.current_price

    upside = None
    if tp and cp and cp > 0:
        upside = (tp - cp) / cp * 100

    rec_color     = {"BUY": GREEN, "SELL": RED}.get(rec, NAVY)
    rec_color_hex = {"BUY": GREEN_HEX, "SELL": RED_HEX}.get(rec, NAVY_HEX)

    tp_data = [
        [
            Paragraph("<b>Target Price</b>", styles["tp_key"]),
            Paragraph(f"<font size='9'>{cur}</font>", styles["tp_sub"]),
            Paragraph(f"<b>{tp:,.2f}</b>" if tp else "n/a", styles["tp_val"]),
            Paragraph("Reuters", styles["tp_sub"]),
            Paragraph(company.ticker or "", styles["tp_key"]),
            Paragraph(f'<b><font color="{rec_color_hex}">{rec}</font></b>',
                      _S("rec", fontName=BOLD_FONT, fontSize=14, textColor=rec_color,
                         alignment=TA_CENTER, leading=16)),
        ],
        [
            Paragraph("<b>Latest Price</b>", styles["tp_key"]),
            Paragraph(f"<font size='9'>{cur}</font>", styles["tp_sub"]),
            Paragraph(f"<b>{cp:,.2f}</b>" if cp else "n/a", styles["tp_val"]),
            Paragraph("Bloomberg", styles["tp_sub"]),
            Paragraph(
                (company.ticker or "").replace(".", " ").replace("-", " "),
                styles["tp_key"]
            ),
            Paragraph(val_method or "", styles["tp_sub"]),
        ],
        [
            Paragraph("<b>Upside</b>", styles["tp_key"]),
            Paragraph("", styles["tp_sub"]),
            Paragraph(f"<b>{upside:+.2f}%</b>" if upside is not None else "n/a",
                      styles["tp_val"]),
            Paragraph("", styles["tp_sub"]),
            Paragraph("", styles["tp_key"]),
            Paragraph("", styles["tp_sub"]),
        ],
    ]
    tp_w = [90, 22, 80, 50, 100, CW - 90 - 22 - 80 - 50 - 100]
    tp_table = Table(tp_data, colWidths=tp_w, hAlign="LEFT")
    tp_table.setStyle(TableStyle([
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",  (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(tp_table)

    if thesis:
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f'<i><font color="{MGRAY_HEX}">{thesis}</font></i>',
            _S("th", fontName="Helvetica-Oblique", fontSize=7.5,
               textColor=MGRAY, leading=11)
        ))

    story.append(Spacer(1, 8))
    story.append(HRFlowable(width=CW, thickness=0.4, color=RULE, spaceAfter=4))

    # ── Main KPI table (two-panel: left = P&L, right = ratios) ───────────────
    # Show: last 3 historical years + 1 forward year  (e.g. 2023 2024 2025 2026E)
    sum_hist = hist_years[-3:]      # last 3 historical years, chronological
    sum_est  = est_years[:1]        # at most 1 forward year
    sum_years = sum_hist + sum_est
    n_sum_cols = len(sum_years)     # 3 or 4

    # Full-year header labels: "12/2024", "12/2026E"
    yr_labels = [f"12/{y}" for y in sum_hist] + [f"12/{y}E" for y in sum_est]

    # Value helpers
    def _lv(af, fn, fmt):
        if af is None:
            return "n/a"
        v = fn(af)
        if fmt == "M":   return _m(v)
        if fmt == "%":   return _pct(v)
        if fmt == "x":   return _x(v)
        if fmt == "ps":  return _ps(v)
        return "n/a"

    def _ev(fn_fe, fmt):
        if not fe or fn_fe is None:
            return "n/a"
        v = fn_fe(fe)
        if fmt == "M":   return _m(v)
        if fmt == "%":   return _pct(v)
        if fmt == "x":   return _x(v)
        if fmt == "ps":  return _ps(v)
        return "n/a"

    # Row definitions — labels match reference exactly
    L_ROWS = [
        ("Sales (m)",              lambda af: af.revenue,              "M",  lambda fe: fe.revenue),
        ("EBITDA adj (m)",         lambda af: af.ebitda,               "M",  lambda fe: fe.ebitda),
        ("EBIT adj (m)",           lambda af: af.ebit,                 "M",  None),
        ("Net profit adj (m)",     lambda af: af.net_income,           "M",  lambda fe: fe.net_income),
        ("Net debt",               lambda af: af.net_debt,             "M",  None),
        ("FCF (m)",                lambda af: af.fcf,                  "M",  None),
        ("EPS adj. and fully dil.",lambda af: af.eps_diluted,          "ps", lambda fe: fe.eps_diluted),
        ("Consensus EPS",          None,                               "ps", lambda fe: fe.eps_diluted),
        ("Net dividend",           lambda af: af.dividends_per_share,  "ps", None),
    ]
    R_ROWS = [
        ("P/E (x) adj and ful. dil.", lambda af: af.pe_ratio,   "x",  lambda fe: fe.pe_ratio),
        ("EV/EBITDA (x)",             _ev_ebitda,               "x",  None),
        ("EV/EBIT (x)",               lambda af: af.ev_ebit,    "x",  None),
        ("FCF yield (%)",             lambda af: af.fcf_yield,  "%",  None),
        ("Dividend yield (%)",        lambda af: af.div_yield,  "%",  None),
        ("Net Debt / EBITDA adj",     _nd_ebitda,               "x",  None),
        ("Gearing (%)",               _gearing,                 "%",  None),
        ("ROE (%)",                   lambda af: af.roe,        "%",  None),
        ("EV/IC (x)",                 _ev_ic,                   "x",  None),
    ]

    def _summary_panel(row_defs):
        panel = []
        for (lbl, hist_fn, fmt, est_fn) in row_defs:
            cells = [lbl]
            for y in sum_hist:
                af = afs.get(y)
                cells.append(_lv(af, hist_fn, fmt) if hist_fn is not None else "—")
            for _ in sum_est:
                cells.append(_ev(est_fn, fmt) if est_fn is not None else "—")
            panel.append(cells)
        return panel

    l_data = _summary_panel(L_ROWS)
    r_data = _summary_panel(R_ROWS)

    # Column widths: 4 data cols each side, total must fit CW (~510 pts)
    # l_lbl + n*l_yr + gap + r_lbl + n*r_yr = CW
    l_lbl_w = 97
    r_lbl_w = 97
    gap_w   = 10
    yr_w    = int((CW - l_lbl_w - r_lbl_w - gap_w) / (n_sum_cols * 2))

    # Build combined side-by-side table
    combo_hdr = (
        [Paragraph(f"FY ({cur})", styles["col_hdr_lbl"])]
        + [Paragraph(y, styles["col_hdr"]) for y in yr_labels]
        + [Paragraph("", styles["col_hdr"])]   # gap col
        + [Paragraph(f"FY ({cur})", styles["col_hdr_lbl"])]
        + [Paragraph(y, styles["col_hdr"]) for y in yr_labels]
    )

    def _p(txt, est=False):
        st = styles["cell_est"] if est else styles["cell"]
        return Paragraph(txt, st)

    combo_rows = [combo_hdr]
    for i in range(len(L_ROWS)):
        lr = l_data[i]
        rr = r_data[i]
        row = (
            [Paragraph(lr[0], styles["lbl"])]
            + [_p(v, est=(j >= len(sum_hist))) for j, v in enumerate(lr[1:])]
            + [Paragraph("", styles["cell"])]   # gap
            + [Paragraph(rr[0], styles["lbl"])]
            + [_p(v, est=(j >= len(sum_hist))) for j, v in enumerate(rr[1:])]
        )
        combo_rows.append(row)

    combo_widths = (
        [l_lbl_w] + [yr_w] * n_sum_cols
        + [gap_w]
        + [r_lbl_w] + [yr_w] * n_sum_cols
    )

    combo_tbl = Table(combo_rows, colWidths=combo_widths, hAlign="LEFT")
    combo_style_cmds = [
        ("FONTNAME",    (0, 0), (-1, -1), BASE_FONT),
        ("FONTSIZE",    (0, 0), (-1, -1), 7.5),
        ("ALIGN",       (0, 0), (0, -1),  "LEFT"),
        ("ALIGN",       (1, 0), (-1, -1), "RIGHT"),
        # Right-panel label col: left-align
        ("ALIGN",
         (1 + n_sum_cols + 1, 0),
         (1 + n_sum_cols + 1, -1), "LEFT"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",  (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW",   (0, 0), (-1, 0),  0.6, BORDER),
    ]
    for i in range(1, len(combo_rows)):
        if i % 2 == 0:
            combo_style_cmds.append(("BACKGROUND", (0, i), (-1, i), LGRAY))
        combo_style_cmds.append(("LINEBELOW", (0, i), (-1, i), 0.25, RULE))
    combo_tbl.setStyle(TableStyle(combo_style_cmds))
    story.append(combo_tbl)

    story.append(Spacer(1, 10))
    story.append(HRFlowable(width=CW, thickness=0.4, color=RULE, spaceAfter=6))

    # ── Market Data block ─────────────────────────────────────────────────────
    story.append(Paragraph("Market data", styles["section"]))
    story.append(HRFlowable(width=CW, thickness=0.5, color=ORANGE, spaceAfter=6))

    mc = company.market_cap
    mc_str = f"{mc/1000:.1f}" if mc else "n/a"
    sh = company.shares_outstanding
    sh_str = f"{sh:.1f}" if sh else "n/a"

    # Derive 52-week high / low from the most recent year's price_year_end
    # and prior year (proxy only — yfinance doesn't store intra-year range)
    la = company.latest_annual()
    prev_yr = (la.year - 1) if la else None
    prev_af = company.annual_financials.get(prev_yr) if prev_yr else None
    prices = [
        p for p in [
            la.price_year_end if la else None,
            prev_af.price_year_end if prev_af else None,
        ] if p is not None
    ]
    yr_high_str = f"{max(prices):,.2f}" if prices else "n/a"
    yr_low_str  = f"{min(prices):,.2f}" if prices else "n/a"

    mkt_rows = [
        (f"Market cap ({cur}bn)", mc_str),
        ("No. of shares outstanding (m)", sh_str),
        ("Current price",
         f"{company.current_price:,.2f} {cur}" if company.current_price else "n/a"),
        ("P/E (trailing)",
         f"{company.pe_ratio:.1f}x" if company.pe_ratio else "n/a"),
        ("EV/EBIT",
         f"{company.ev_ebit:.1f}x" if company.ev_ebit else "n/a"),
        ("Year-high (proxy)", yr_high_str),
        ("Year-low (proxy)",  yr_low_str),
    ]

    mkt_data = [
        [Paragraph(k, styles["mkt_lbl"]), Paragraph(v, styles["mkt_val"])]
        for k, v in mkt_rows
    ]
    mkt_tbl = Table(mkt_data, colWidths=[CW * 0.55, CW * 0.45], hAlign="LEFT")
    mkt_style = [
        ("FONTNAME",    (0, 0), (-1, -1), BASE_FONT),
        ("FONTSIZE",    (0, 0), (-1, -1), 8),
        ("TOPPADDING",  (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("ALIGN",       (0, 0), (0, -1),  "LEFT"),
        ("ALIGN",       (1, 0), (1, -1),  "RIGHT"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(len(mkt_data)):
        mkt_style.append(("LINEBELOW", (0, i), (-1, i), 0.3, RULE))
        if i % 2 == 0:
            mkt_style.append(("BACKGROUND", (0, i), (-1, i), LGRAY))
    mkt_tbl.setStyle(TableStyle(mkt_style))
    story.append(mkt_tbl)

    return story


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 2: VALUATION
# ═══════════════════════════════════════════════════════════════════════════════

def _build_valuation_page(
    company: CompanyData,
    hist_years: list[int],
    est_years: list[int],
    styles: dict,
) -> list:
    story = []
    fe  = company.forward_estimates
    afs = company.annual_financials

    story.append(Paragraph("Valuation", styles["section"]))
    story.append(HRFlowable(width=CW, thickness=0.5, color=ORANGE, spaceAfter=4))

    hdr = _hdr_row(styles, hist_years, est_years, label="FY", label_suffix=f" ({company.currency or ''})")
    rows = [hdr]
    section_rows = set()

    def R(lbl, fn, fmt, est_fn=None, bold=False, indent=False):
        return _row(lbl, hist_years, est_years, afs, fn, fmt, styles,
                    est_getter=est_fn, fe=fe, bold_label=bold, indent=indent)

    def CHG(lbl, fn, est_fn=None):
        return _chg_row(lbl, hist_years, est_years, afs, fn, styles,
                        est_getter=est_fn, fe=fe)

    # ── Per share data ────────────────────────────────────────────────────────
    section_rows.add(len(rows))
    rows.append(R("Per share data", lambda a: None, "M", bold=True))
    rows.append(R("EPS adj. and fully diluted",
                  lambda a: a.eps_diluted, "ps",
                  est_fn=lambda fe: fe.eps_diluted))
    rows.append(CHG("  % Change", lambda a: a.eps_diluted))
    rows.append(R("EPS reported",
                  lambda a: a.eps_diluted, "ps"))   # same field — no adjustment
    rows.append(R("Consensus EPS", lambda a: None, "ps",
                  est_fn=lambda fe: fe.eps_diluted))
    rows.append(R("Cash flow per share",
                  _cfps, "ps"))
    rows.append(R("Book value per share",
                  _bvps, "ps"))
    rows.append(R("DPS",
                  lambda a: a.dividends_per_share, "ps"))
    rows.append(R("No. of shares, YE (m)",
                  lambda a: a.shares_outstanding, "ps1"))

    # ── Share price ───────────────────────────────────────────────────────────
    section_rows.add(len(rows))
    rows.append(R("Share price", lambda a: None, "M", bold=True))
    rows.append(R("Year-end price",
                  lambda a: a.price_year_end, "ps"))

    # ── Enterprise value ──────────────────────────────────────────────────────
    section_rows.add(len(rows))
    rows.append(R("Enterprise value (EURm)", lambda a: None, "M", bold=True))
    rows.append(R("Market capitalisation",
                  lambda a: a.market_cap, "M"))
    rows.append(R("Net debt (financial)",
                  lambda a: a.net_debt, "M"))
    rows.append(R("Enterprise value",
                  lambda a: a.enterprise_value, "M"))

    # ── Valuation multiples ───────────────────────────────────────────────────
    section_rows.add(len(rows))
    rows.append(R("Valuation", lambda a: None, "M", bold=True))
    rows.append(R("P/E adjusted and fully diluted",
                  lambda a: a.pe_ratio, "x",
                  est_fn=lambda fe: fe.pe_ratio))
    rows.append(R("P/BV",         _pbv,        "x"))
    rows.append(R("Dividend yield (%)",
                  lambda a: a.div_yield,  "%"))
    rows.append(R("FCF yield (%)",
                  lambda a: a.fcf_yield,  "%"))
    rows.append(R("ROE (%)",      lambda a: a.roe,  "%"))
    rows.append(R("EV/Sales",     lambda a: a.ev_sales, "x",
                  est_fn=lambda fe: fe.ev_sales))
    rows.append(R("EV/EBITDA",    _ev_ebitda,  "x"))
    rows.append(R("EV/EBIT",      lambda a: a.ev_ebit,  "x"))
    rows.append(R("Net Debt / EBITDA", _nd_ebitda, "x"))
    rows.append(R("Gearing (%)",  _gearing,    "%"))

    cw = _col_widths(len(hist_years) + len(est_years))
    tbl = Table(rows, colWidths=cw, hAlign="LEFT", repeatRows=1)
    tbl.setStyle(_tbl_style(rows, section_rows=section_rows))
    story.append(tbl)
    return story


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 3: INCOME STATEMENT
# ═══════════════════════════════════════════════════════════════════════════════

def _build_income_page(
    company: CompanyData,
    hist_years: list[int],
    est_years: list[int],
    styles: dict,
) -> list:
    story = []
    fe  = company.forward_estimates
    afs = company.annual_financials
    cur = company.currency or ""

    story.append(Paragraph("Income Statement", styles["section"]))
    story.append(HRFlowable(width=CW, thickness=0.5, color=ORANGE, spaceAfter=4))

    hdr = _hdr_row(styles, hist_years, est_years, label=f"FY ({cur}m)")
    rows = [hdr]
    section_rows = set()

    def R(lbl, fn, fmt, est_fn=None, bold=False, indent=False):
        return _row(lbl, hist_years, est_years, afs, fn, fmt, styles,
                    est_getter=est_fn, fe=fe, bold_label=bold, indent=indent)

    def CHG(fn):
        return _chg_row("  % Change", hist_years, est_years, afs, fn, styles, fe=fe)

    rows.append(R("Sales",         lambda a: a.revenue,    "M", est_fn=lambda fe: fe.revenue, bold=True))
    rows.append(CHG(lambda a: a.revenue))
    rows.append(R("Gross profit",  lambda a: a.gross_profit, "M"))
    rows.append(R("EBITDA",        lambda a: a.ebitda,     "M", est_fn=lambda fe: fe.ebitda,  bold=True))
    rows.append(CHG(lambda a: a.ebitda))
    rows.append(R("Depreciation & amortisation",
                  lambda a: (-(a.ebitda - a.ebit) if a.ebitda and a.ebit else None), "M", indent=True))
    rows.append(R("EBIT",          lambda a: a.ebit,       "M", bold=True))
    rows.append(CHG(lambda a: a.ebit))
    rows.append(R("Net profit",    lambda a: a.net_income, "M", est_fn=lambda fe: fe.net_income, bold=True))
    rows.append(CHG(lambda a: a.net_income))

    section_rows.add(len(rows))
    rows.append(R("Margins", lambda a: None, "M", bold=True))
    rows.append(R("Gross margin (%)",   lambda a: a.gross_margin,  "%"))
    rows.append(R("EBITDA margin (%)",  lambda a: a.ebitda_margin, "%",
                  est_fn=lambda fe: (fe.ebitda / fe.revenue) if fe and fe.ebitda and fe.revenue else None))
    rows.append(R("EBIT margin (%)",    lambda a: a.ebit_margin,   "%"))
    rows.append(R("Net profit margin (%)", lambda a: a.net_margin, "%",
                  est_fn=lambda fe: fe.net_margin))

    section_rows.add(len(rows))
    rows.append(R("Per share", lambda a: None, "M", bold=True))
    rows.append(R("EPS (diluted)",
                  lambda a: a.eps_diluted, "ps", est_fn=lambda fe: fe.eps_diluted))
    rows.append(CHG(lambda a: a.eps_diluted))
    rows.append(R("DPS",
                  lambda a: a.dividends_per_share, "ps"))

    section_rows.add(len(rows))
    rows.append(R("Consensus estimates", lambda a: None, "M", bold=True))
    rows.append(R("  Consensus Sales",   lambda a: None, "M", est_fn=lambda fe: fe.revenue))
    rows.append(R("  Consensus EBITDA",  lambda a: None, "M", est_fn=lambda fe: fe.ebitda))
    rows.append(R("  Consensus EPS",     lambda a: None, "ps", est_fn=lambda fe: fe.eps_diluted))

    cw = _col_widths(len(hist_years) + len(est_years))
    tbl = Table(rows, colWidths=cw, hAlign="LEFT", repeatRows=1)
    tbl.setStyle(_tbl_style(rows, section_rows=section_rows))
    story.append(tbl)
    return story


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 4: CASH FLOW
# ═══════════════════════════════════════════════════════════════════════════════

def _build_cashflow_page(
    company: CompanyData,
    hist_years: list[int],
    est_years: list[int],
    styles: dict,
) -> list:
    story = []
    fe  = company.forward_estimates
    afs = company.annual_financials
    cur = company.currency or ""

    story.append(Paragraph("Cash Flow", styles["section"]))
    story.append(HRFlowable(width=CW, thickness=0.5, color=ORANGE, spaceAfter=4))

    hdr = _hdr_row(styles, hist_years, est_years, label=f"FY ({cur}m)")
    rows = [hdr]
    section_rows = set()

    def R(lbl, fn, fmt, est_fn=None, bold=False, indent=False):
        return _row(lbl, hist_years, est_years, afs, fn, fmt, styles,
                    est_getter=est_fn, fe=fe, bold_label=bold, indent=indent)

    def CHG(fn):
        return _chg_row("  % Change", hist_years, est_years, afs, fn, styles, fe=fe)

    rows.append(R("Operating cash flow",
                  lambda a: a.operating_cash_flow, "M", bold=True))
    rows.append(CHG(lambda a: a.operating_cash_flow))
    rows.append(R("Capital expenditure (Capex)",
                  lambda a: -a.capex if a.capex else None, "M", indent=True))
    rows.append(R("Free cash flow (FCF)",
                  lambda a: a.fcf,  "M", bold=True))
    rows.append(CHG(lambda a: a.fcf))

    section_rows.add(len(rows))
    rows.append(R("Ratios", lambda a: None, "M", bold=True))
    rows.append(R("Capex / Sales (%)",    _capex_sales, "%"))
    rows.append(R("FCF / Sales (%)",      _fcf_sales,   "%"))
    rows.append(R("FCF yield (%)",        lambda a: a.fcf_yield, "%"))
    rows.append(R("FCF per share",        _fcf_ps,      "ps"))

    cw = _col_widths(len(hist_years) + len(est_years))
    tbl = Table(rows, colWidths=cw, hAlign="LEFT", repeatRows=1)
    tbl.setStyle(_tbl_style(rows, section_rows=section_rows))
    story.append(tbl)
    return story


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 5: BALANCE SHEET
# ═══════════════════════════════════════════════════════════════════════════════

def _build_balance_page(
    company: CompanyData,
    hist_years: list[int],
    est_years: list[int],
    styles: dict,
) -> list:
    story = []
    fe  = company.forward_estimates
    afs = company.annual_financials
    cur = company.currency or ""

    story.append(Paragraph("Balance Sheet", styles["section"]))
    story.append(HRFlowable(width=CW, thickness=0.5, color=ORANGE, spaceAfter=4))

    hdr = _hdr_row(styles, hist_years, est_years, label=f"FY ({cur}m)")
    rows = [hdr]
    section_rows = set()

    def R(lbl, fn, fmt, est_fn=None, bold=False, indent=False):
        return _row(lbl, hist_years, est_years, afs, fn, fmt, styles,
                    est_getter=est_fn, fe=fe, bold_label=bold, indent=indent)

    # Assets
    section_rows.add(len(rows))
    rows.append(R("Assets", lambda a: None, "M", bold=True))
    rows.append(R("Cash & equivalents",  lambda a: a.cash,         "M"))
    rows.append(R("Total assets",        lambda a: a.total_assets,  "M", bold=True))

    # Liabilities & equity
    section_rows.add(len(rows))
    rows.append(R("Liabilities & Equity", lambda a: None, "M", bold=True))
    rows.append(R("Total debt",          lambda a: a.total_debt,   "M"))
    rows.append(R("Net debt",            lambda a: a.net_debt,     "M", bold=True))
    rows.append(R("Shareholders' equity",lambda a: a.total_equity, "M", bold=True))
    rows.append(R("Book value per share", _bvps, "ps"))
    rows.append(_chg_row("  % Change BVPS", hist_years, est_years, afs,
                          _bvps, styles, fe=fe))

    # Leverage & returns
    section_rows.add(len(rows))
    rows.append(R("Leverage & Returns", lambda a: None, "M", bold=True))
    rows.append(R("Net Debt / EBITDA (x)", _nd_ebitda, "x"))
    rows.append(R("Net Debt / FCF (x)",
                  lambda a: (a.net_debt / a.fcf) if a.net_debt and a.fcf and a.fcf != 0 else None, "x"))
    rows.append(R("Gearing (%) — net debt / equity", _gearing, "%"))
    rows.append(R("ROE (%)",    lambda a: a.roe,    "%"))
    rows.append(R("ROA (%)",    lambda a: a.roa,    "%"))
    rows.append(R("Invested capital",
                  lambda a: ((a.total_equity or 0) + (a.net_debt or 0)) or None, "M"))

    cw = _col_widths(len(hist_years) + len(est_years))
    tbl = Table(rows, colWidths=cw, hAlign="LEFT", repeatRows=1)
    tbl.setStyle(_tbl_style(rows, section_rows=section_rows))
    story.append(tbl)
    return story


# ═══════════════════════════════════════════════════════════════════════════════
# Main renderer
# ═══════════════════════════════════════════════════════════════════════════════

class KeplerPDFGenerator:
    """Generate a Kepler-style analyst summary PDF."""

    def render(
        self,
        company: CompanyData,
        analysis: dict,
        output_path: str,
    ) -> str:
        """Build and save the PDF. Returns output_path."""
        report_date = datetime.now().strftime("%d %b %Y")
        styles = _styles()
        hist_years, est_years = _build_col_plan(company)

        # Canvas callback bound to current context
        def _on_page(canvas, doc):
            _draw_page_header(canvas, doc, company, report_date)

        doc = SimpleDocTemplate(
            output_path,
            pagesize=A4,
            leftMargin=ML, rightMargin=MR,
            topMargin=MT, bottomMargin=MB,
            title=f"{company.name or company.ticker} — Kepler Summary",
            author="Your Humble EquityBot",
        )

        story = []

        # ── Page 1: Summary ───────────────────────────────────────────────────
        story += _build_summary_page(company, analysis, hist_years, est_years, styles)
        story.append(PageBreak())

        # ── Page 2: Valuation ─────────────────────────────────────────────────
        story += _build_valuation_page(company, hist_years, est_years, styles)
        story.append(PageBreak())

        # ── Page 3: Income Statement ──────────────────────────────────────────
        story += _build_income_page(company, hist_years, est_years, styles)
        story.append(PageBreak())

        # ── Page 4: Cash Flow ─────────────────────────────────────────────────
        story += _build_cashflow_page(company, hist_years, est_years, styles)
        story.append(PageBreak())

        # ── Page 5: Balance Sheet ─────────────────────────────────────────────
        story += _build_balance_page(company, hist_years, est_years, styles)

        doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
        logger.info(f"[KeplerPDF] Saved to {output_path}")
        return output_path
