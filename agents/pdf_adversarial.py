"""
pdf_adversarial.py — Adversarial Review extra page for any EquityBot report.

Appended after the main report pages when ADVERSARIAL_MODE=true.
Renders:
  • Dual-model header (Claude vs GPT-4o)
  • Recommendation comparison banner
  • Consensus fields (green)
  • Contested fields (amber/red) with both models' values
  • GPT-4o's critique of Claude
  • Claude's critique of GPT-4o
  • Merge methodology note

Usage:
    from agents.pdf_adversarial import build_adversarial_page
    from agents.adversarial import AdversarialResult

    extra_flowables = build_adversarial_page(adv_result)
    # Prepend with PageBreak() and append to your doc story.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether,
)
from reportlab.platypus.flowables import Flowable

if TYPE_CHECKING:
    from agents.adversarial import AdversarialResult

# ── Palette (matches pdf_overview.py) ────────────────────────────────────────
NAVY   = HexColor('#003F54')   # Pantone 303 — ink-saving brand colour
BLUE   = HexColor('#2E75B6')
LBLUE  = HexColor('#D6E8F7')
LLBLUE = HexColor('#FFFFFF')   # alt row fill — kept white to save ink
GREEN  = HexColor('#1A7E3D')
LGREEN = HexColor('#D4EDDA')
RED    = HexColor('#C0392B')
LRED   = HexColor('#FADBD8')
AMBER  = HexColor('#D68910')
LAMBER = HexColor('#FDEBD0')
MGRAY  = HexColor('#555555')
LGRAY  = HexColor('#F0F0F0')
BORDER = HexColor('#BBCCDD')

BASE_FONT = 'Helvetica'
BOLD_FONT = 'Helvetica-Bold'

W, _H = A4
ML = MR = 18 * mm
CW = W - ML - MR   # ~481 pts


# ── Severity colour helpers ───────────────────────────────────────────────────

def _sev_bg(severity: str):
    return LRED if severity == "high" else LAMBER

def _sev_fg(severity: str):
    return RED if severity == "high" else AMBER


# ── Style builder ─────────────────────────────────────────────────────────────

def _styles() -> dict:
    ss = getSampleStyleSheet()

    def S(name, **kw) -> ParagraphStyle:
        kw.setdefault("fontName", BASE_FONT)
        kw.setdefault("fontSize", 9)
        kw.setdefault("leading",  13)
        return ParagraphStyle(name, parent=ss["Normal"], **kw)

    return {
        "page_title":  S("adv_page_title",  fontName=BOLD_FONT, fontSize=14,
                         textColor=NAVY, leading=18, spaceAfter=4),
        "page_sub":    S("adv_page_sub",    fontSize=9,  textColor=MGRAY),
        "section":     S("adv_section",     fontName=BOLD_FONT, fontSize=10,
                         textColor=NAVY, spaceBefore=10, spaceAfter=4),
        "body":        S("adv_body",        fontSize=8.5, leading=13,
                         textColor=HexColor('#222222')),
        "small":       S("adv_small",       fontSize=7.5, textColor=MGRAY),
        "field_name":  S("adv_field",       fontName=BOLD_FONT, fontSize=8.5,
                         textColor=HexColor('#222222')),
        "value_green": S("adv_vgreen",      fontSize=8.5, textColor=GREEN),
        "value_amber": S("adv_vamber",      fontSize=8.5, textColor=AMBER),
        "value_red":   S("adv_vred",        fontSize=8.5, textColor=RED),
        "critique":    S("adv_critique",    fontSize=8, leading=12,
                         textColor=HexColor('#333333')),
        "model_label": S("adv_model_label", fontName=BOLD_FONT, fontSize=9,
                         textColor=white),
    }


# ── Section header helper ─────────────────────────────────────────────────────

def _section_header(title: str, st: dict) -> list:
    return [
        Spacer(1, 4),
        HRFlowable(width=CW, thickness=1, color=BLUE, spaceAfter=4),
        Paragraph(title, st["section"]),
    ]


# ── Recommendation banner ─────────────────────────────────────────────────────

def _rec_banner(result: "AdversarialResult", st: dict) -> list:
    """Two-cell banner showing primary vs secondary recommendation."""
    p_rec = result.primary_rec.strip().upper()
    s_rec = result.secondary_rec.strip().upper()

    def _cell_color(rec: str):
        if rec == "BUY":  return HexColor('#1A7E3D')
        if rec == "SELL": return HexColor('#C0392B')
        return HexColor('#D68910')

    agree_text = (
        "✓  Both models agree" if result.recs_agree
        else "⚠  Models disagree — conservative HOLD applied"
    )
    agree_color = GREEN if result.recs_agree else RED

    banner_data = [
        [
            Paragraph(f"<b>Claude</b><br/>{p_rec}", st["model_label"]),
            Paragraph(f"<b>GPT-4o</b><br/>{s_rec}",  st["model_label"]),
        ]
    ]
    banner_style = TableStyle([
        ("BACKGROUND",  (0, 0), (0, 0), _cell_color(p_rec)),
        ("BACKGROUND",  (1, 0), (1, 0), _cell_color(s_rec)),
        ("ALIGN",       (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
        ("FONTNAME",    (0, 0), (-1, -1), BOLD_FONT),
        ("FONTSIZE",    (0, 0), (-1, -1), 12),
        ("TEXTCOLOR",   (0, 0), (-1, -1), white),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [None]),
        ("TOPPADDING",  (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("GRID",        (0, 0), (-1, -1), 0.5, BORDER),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
    ])

    banner_tbl = Table(banner_data, colWidths=[CW * 0.5, CW * 0.5])
    banner_tbl.setStyle(banner_style)

    agree_para = Paragraph(
        f'<font color="#{agree_color.hexval()[2:] if hasattr(agree_color, "hexval") else "555555"}">'
        f'{agree_text}</font>',
        st["small"],
    )

    # Simpler agree note using direct colour
    agree_col_hex = "1A7E3D" if result.recs_agree else "C0392B"
    agree_para2 = Paragraph(
        f'<font color="#{agree_col_hex}"><b>{agree_text}</b></font>',
        ParagraphStyle("agree", parent=getSampleStyleSheet()["Normal"],
                       fontSize=9, leading=13, spaceAfter=6),
    )

    return [banner_tbl, Spacer(1, 4), agree_para2]


# ── Consensus table ───────────────────────────────────────────────────────────

def _consensus_table(consensus: list, st: dict) -> list:
    if not consensus:
        return [Paragraph("No consensus fields identified.", st["small"])]

    rows = [
        [
            Paragraph("<b>Field</b>", st["field_name"]),
            Paragraph("<b>Agreed Value</b>", st["field_name"]),
        ]
    ]
    for item in consensus:
        rows.append([
            Paragraph(item.get("field", ""), st["body"]),
            Paragraph(
                f'<font color="#1A7E3D"><b>{item.get("value", "")}</b></font>',
                st["body"],
            ),
        ])

    tbl = Table(rows, colWidths=[CW * 0.42, CW * 0.58])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), LBLUE),
        ("FONTNAME",      (0, 0), (-1, 0), BOLD_FONT),
        ("TEXTCOLOR",     (0, 0), (-1, 0), NAVY),
        ("FONTSIZE",      (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, LLBLUE]),
        ("GRID",          (0, 0), (-1, -1), 0.4, BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]))
    return [tbl]


# ── Contested table ───────────────────────────────────────────────────────────

def _contested_table(contested: list, st: dict) -> list:
    if not contested:
        return [Paragraph("No contested fields — full model agreement.", st["small"])]

    rows = [
        [
            Paragraph("<b>Field</b>", st["field_name"]),
            Paragraph("<b>Claude (Primary)</b>", st["field_name"]),
            Paragraph("<b>GPT-4o (Secondary)</b>", st["field_name"]),
            Paragraph("<b>Severity</b>", st["field_name"]),
        ]
    ]
    for item in contested:
        sev = item.get("severity", "medium")
        sev_label = "HIGH" if sev == "high" else "MED"
        sev_col   = "C0392B" if sev == "high" else "D68910"

        rows.append([
            Paragraph(item.get("field", ""),         st["body"]),
            Paragraph(item.get("primary", ""),        st["body"]),
            Paragraph(item.get("secondary", ""),      st["body"]),
            Paragraph(
                f'<font color="#{sev_col}"><b>{sev_label}</b></font>',
                st["body"],
            ),
        ])

    col_w = [CW * 0.25, CW * 0.30, CW * 0.30, CW * 0.15]
    tbl   = Table(rows, colWidths=col_w)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), LBLUE),
        ("FONTNAME",      (0, 0), (-1, 0), BOLD_FONT),
        ("TEXTCOLOR",     (0, 0), (-1, 0), NAVY),
        ("FONTSIZE",      (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, LAMBER]),
        ("GRID",          (0, 0), (-1, -1), 0.4, BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("WORDWRAP",      (0, 0), (-1, -1), True),
    ]))
    return [tbl]


# ── Critique box ──────────────────────────────────────────────────────────────

def _critique_box(
    title: str,
    critic_label: str,
    subject_label: str,
    critique_text: str,
    bg_color,
    label_color,
    st: dict,
) -> list:
    """Render a framed critique block with a coloured header label."""
    header_data = [[
        Paragraph(
            f'<font color="white"><b>{critic_label} critiques {subject_label}</b></font>',
            st["small"],
        ),
    ]]
    header_tbl = Table(header_data, colWidths=[CW])
    header_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), label_color),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
    ]))

    # Wrap critique text — clean up any excessively long lines
    critique_clean = (critique_text or "Critique unavailable.")[:1500]
    critique_paras = []
    for para_text in critique_clean.split("\n"):
        para_text = para_text.strip()
        if para_text:
            critique_paras.append(Paragraph(para_text, st["critique"]))
        else:
            critique_paras.append(Spacer(1, 4))

    body_data = [[critique_paras[0] if critique_paras else Paragraph("", st["critique"])]]
    if len(critique_paras) > 1:
        # Flatten to a single cell with all paras
        combined = "<br/>".join(
            p.text if hasattr(p, "text") else "" for p in critique_paras
        )
        body_data = [[Paragraph(combined, st["critique"])]]

    body_tbl = Table(body_data, colWidths=[CW])
    body_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), bg_color),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
        ("GRID",          (0, 0), (-1, -1), 0.5, BORDER),
    ]))

    return [header_tbl, body_tbl, Spacer(1, 6)]


# ── Merge methodology note ────────────────────────────────────────────────────

def _merge_note(result: "AdversarialResult", st: dict) -> list:
    lines = [
        "<b>Merge Methodology</b> — The main report uses the <i>merged</i> analysis:",
    ]
    lines.append("• Base analysis: <b>Claude</b> (primary) unless overridden below.")
    if not result.recs_agree:
        lines.append(
            f"• Recommendation contested ({result.primary_rec} vs {result.secondary_rec})"
            " → conservative <b>HOLD</b> applied to merged output."
        )
    else:
        lines.append(
            f"• Both models recommend <b>{result.primary_rec}</b> — no merge adjustment."
        )
    contested_scores = [c for c in result.contested_fields
                        if "score" in c["field"].lower() or "grade" in c["field"].lower()
                        or "fisher" in c["field"].lower() or "gravity" in c["field"].lower()]
    if contested_scores:
        lines.append("• Contested scores: <b>averaged</b> between the two models.")

    note_text = "<br/>".join(lines)
    note_data = [[Paragraph(note_text, st["body"])]]
    note_tbl  = Table(note_data, colWidths=[CW])
    note_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), LLBLUE),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
        ("BOX",           (0, 0), (-1, -1), 0.5, BLUE),
    ]))
    return [note_tbl]


# ── Main public function ──────────────────────────────────────────────────────

def build_adversarial_page(result: "AdversarialResult") -> list:
    """
    Build a list of ReportLab flowables for the Adversarial Review extra page.

    Call site (in each PDF generator):
        from reportlab.platypus import PageBreak
        from agents.pdf_adversarial import build_adversarial_page
        story += [PageBreak()] + build_adversarial_page(adv_result)

    Returns:
        List of flowables — ready to extend onto an existing story list.
        Does NOT include the preceding PageBreak (caller's responsibility).
    """
    st = _styles()
    story: list = []

    # ── Page title ────────────────────────────────────────────────────────────
    story.append(Paragraph("Adversarial Review", st["page_title"]))
    story.append(Paragraph(
        "Independent analysis by <b>Claude</b> and <b>GPT-4o</b>, "
        "followed by cross-critique and conservative merge.",
        st["page_sub"],
    ))
    story.append(Spacer(1, 8))

    # ── Recommendation comparison ─────────────────────────────────────────────
    story += _section_header("Recommendation Comparison", st)
    story += _rec_banner(result, st)
    story.append(Spacer(1, 6))

    # ── Consensus ─────────────────────────────────────────────────────────────
    if result.consensus_fields:
        story += _section_header(
            f"Consensus Fields  ({len(result.consensus_fields)} agreed)", st
        )
        story += _consensus_table(result.consensus_fields, st)
        story.append(Spacer(1, 6))

    # ── Contested ─────────────────────────────────────────────────────────────
    story += _section_header(
        f"Contested Fields  ({len(result.contested_fields)} diverged)", st
    )
    story += _contested_table(result.contested_fields, st)
    story.append(Spacer(1, 6))

    # ── Cross-critiques ───────────────────────────────────────────────────────
    story += _section_header("Cross-Critique", st)

    story += _critique_box(
        title="GPT-4o → Claude",
        critic_label="GPT-4o",
        subject_label="Claude's Analysis",
        critique_text=result.critique_of_primary,
        bg_color=HexColor('#FFF8F0'),
        label_color=HexColor('#D68910'),
        st=st,
    )
    story += _critique_box(
        title="Claude → GPT-4o",
        critic_label="Claude",
        subject_label="GPT-4o's Analysis",
        critique_text=result.critique_of_secondary,
        bg_color=HexColor('#F0F7FF'),
        label_color=BLUE,
        st=st,
    )

    # ── Merge methodology ─────────────────────────────────────────────────────
    story += _section_header("How the Main Report Was Produced", st)
    story += _merge_note(result, st)

    return story
