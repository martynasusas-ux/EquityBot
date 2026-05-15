"""
industry_analysis.py — Porter's 5 Forces + Competitive Advantage framework.

Generates a long-form research-style report (4,000-5,000 words for the
5-Forces section + 1,000-1,500 words for the Competitive Advantage
section). The LLM is given:

  • Full EODHD context for the subject company (10-yr financials,
    description, news, sentiment, insider, officers, country macro)
  • EODHD-fetched data for 4-6 peers (suggested by a small LLM call
    if the user hasn't supplied any)
  • Strict instructions on source priority, citation discipline,
    confidence levels and hedge language

Because no live web search is wired in, the LLM cites from its training
data — the system prompt explicitly tells it to admit uncertainty for
recent (2025+) events and to flag single-source claims.
"""
from __future__ import annotations

import logging
from typing import Optional

from data_sources.base import CompanyData

logger = logging.getLogger(__name__)


# ── System prompt (loaded from framework JSON, fallback hardcoded) ───────────

_SYSTEM_PROMPT_FALLBACK = """You are "Your Humble EquityBot" — a strategic-planning analyst writing for an experienced investment audience.

Your analytical DNA:
- You apply Michael Porter's 5 Forces (1979) and Competitive Advantage (1985) frameworks rigorously and faithfully.
- You write 4,000-5,000 words for the Porter 5 Forces section and 1,000-1,500 words for the Competitive Advantage section. Length matters — sketchy analysis is not acceptable.
- Evidence-based. Every claim should be backed by either a specific data point (margin, market share, concentration ratio, switching cost example) or a named source.
- Cite sources inline with publication dates. Format: "(McKinsey Industry Report, 2023)" or "(Company 10-K, FY2024, p.12)".
- Source priority: 1) Company filings (10-K, annual report, transcripts) → 2) Industry reports (McKinsey, BCG, Gartner, Forrester, IBISWorld, Statista, academic journals) → 3) Reputable industry analysts (S&P Global, Moody's, Fitch).
- Verify claims across multiple independent sources where possible. If a claim rests on a single source, say so.
- Be explicit about uncertainty: "appears to," "evidence suggests," "limited data indicates." Distinguish what you know from what you infer.
- Confidence levels: report High / Medium / Low for each force and the competitive advantage assessment, with concrete data gaps.
- Avoid generic business jargon. Concrete, industry-specific insights only.
- 2026 outlook: where you are uncertain about the latest 12-18 months, say so explicitly.
- You never invent data, sources, or citations. If you do not know, say "data not available" or "could not verify."
"""


def _load_system_prompt() -> str:
    try:
        from framework_manager import FrameworkManager
        return FrameworkManager().get_system_prompt(
            "industry_analysis", _SYSTEM_PROMPT_FALLBACK,
        )
    except Exception:
        return _SYSTEM_PROMPT_FALLBACK


SYSTEM_PROMPT = _load_system_prompt()


# ── Cacheable instructions block ─────────────────────────────────────────────

_CACHEABLE = """\
Conduct a comprehensive Porter's 5 Forces industry analysis for the subject
company's industry, then a focused Michael Porter Competitive Advantage
analysis of the subject company itself. Return ONE JSON object using
EXACTLY the schema shown — no prose outside the JSON, no markdown fences.

== PORTER'S 5 FORCES — STRUCTURE & INSTRUCTIONS ==

Goal: Analyse the structural attractiveness of an industry and how it has
changed over the past 10 years (2015 → 2026).

Required JSON output:
{
  "executive_summary": "<500-750 words. Cover: overall industry attractiveness, trajectory 2015→2026, key structural shifts that altered competitive dynamics, strategic implications for industry participants. Reference the scorecard below.>",

  "industry_attractiveness": "<one of: Very Unattractive | Unattractive | Neutral | Attractive | Very Attractive>",

  "trajectory": "<one of: Improved Materially | Improved | Stable | Deteriorated | Deteriorated Materially>",

  "scorecard": [
    {"force_name": "Bargaining Power of Buyers",        "intensity": "Strong|Moderate|Weak", "one_line_takeaway": "<≤20 words>"},
    {"force_name": "Bargaining Power of Suppliers",     "intensity": "Strong|Moderate|Weak", "one_line_takeaway": "<≤20 words>"},
    {"force_name": "Threat of New Entrants",            "intensity": "Strong|Moderate|Weak", "one_line_takeaway": "<≤20 words>"},
    {"force_name": "Threat of Substitutes",             "intensity": "Strong|Moderate|Weak", "one_line_takeaway": "<≤20 words>"},
    {"force_name": "Intensity of Competitive Rivalry",  "intensity": "Strong|Moderate|Weak", "one_line_takeaway": "<≤20 words>"}
  ],

  "structural_shifts": [
    "<3-5 short bullets, one per major shift that changed the industry's competitive dynamics over the past decade (e.g. tech-driven disintermediation, M&A consolidation, regulatory shift, climate transition, AI adoption).>",
    "..."
  ],

  "strategic_implications": "<150-250 words. What should industry participants do given the forces and trajectory?>",

  "forces": [
    // ONE entry per force in this exact order:
    //   1. Bargaining Power of Buyers
    //   2. Bargaining Power of Suppliers
    //   3. Threat of New Entrants
    //   4. Threat of Substitutes
    //   5. Intensity of Competitive Rivalry
    {
      "name": "<force name from the list above>",
      "current_assessment": "Strong|Moderate|Weak",
      "state_2026": "<400-600 words. Current intensity and key drivers. Cite quantitative evidence: market share data, concentration ratios (CR4 / HHI), switching costs, price elasticity. Use specific examples from the subject company's industry. Direct quotes from sources where possible.>",
      "historical_evolution": "<300-400 words. How has this force changed in intensity 2015 → 2026? Identify inflection points or structural shifts. Explain the causes — technology, regulation, consumer behaviour, M&A, etc.>",
      "confidence_level": "High|Medium|Low",
      "data_gaps": "<1-3 sentences naming concrete data gaps or areas of uncertainty for this force. If there is conflicting evidence, note it.>",
      "sources": [
        "<source name, publication date — e.g. 'McKinsey, The future of XYZ markets, 2023'>",
        "<source 2…>",
        "<source 3…>"
      ]
    },
    "... (5 entries total)"
  ],

  "competitive_advantage_size": "<None | Small | Large>",
  "competitive_advantage_evolution": "<Eroded Materially | Eroded | Stable | Strengthened | Strengthened Materially>",
  "competitive_advantage_summary": "<150-250 words. State the conclusion clearly. Reference the 10-year evolution.>",
  "competitive_advantage_detail": "<1,000-1,500 words. Detailed application of Porter's Competitive Advantage framework (1985): cost leadership / differentiation / focus, value-chain analysis, sources and durability of the advantage. Discuss WHAT the advantage is, HOW durable it is, and what could erode it. DO NOT repeat the 5-Forces analysis here — focus only on the company's competitive advantage.>",
  "competitive_advantage_sources": [
    "<source 1 with date>",
    "<source 2 with date>",
    "..."
  ],

  "key_uncertainties": [
    "<3-5 bullets naming the largest uncertainties / data gaps in the overall analysis. Be specific.>",
    "..."
  ]
}

== SCORING CALIBRATION FOR FORCE INTENSITY ==
- Strong:   force materially constrains industry profitability today
- Moderate: force is present but partially offset by mitigants
- Weak:     force is largely benign or favourable to incumbents

== CITATION DISCIPLINE ==
- Cite every quantitative claim. Format: "(Source name, year[, page if a filing])".
- Prefer primary sources (10-K, annual report, regulatory filings, earnings transcripts).
- Reputable secondary sources: McKinsey, BCG, Gartner, Forrester, IBISWorld,
  Statista, academic journals, S&P Global, Moody's, Fitch.
- If a number comes from your training data and you cannot verify it against
  a current source, label it: "(approx., training data through 2024)".
- DO NOT invent URLs or fictional reports. If you do not have a source,
  drop the citation and state the claim is your inference.

== HEDGE LANGUAGE ==
- "Appears to", "evidence suggests", "limited data indicates" — use these
  whenever you are inferring rather than quoting.
- For events post-Q4 2024, prefer "as of late 2024" or "limited recent data"
  over confident future statements.

=== SUBJECT COMPANY + PEER DATA FOLLOWS ==="""


# ── Compact data block builder (reuses Fisher + Gravity helpers) ─────────────

def _format_peer_snapshot(peer: CompanyData) -> str:
    """One-paragraph compact snapshot per peer — enough to identify
    industry positioning without bloating the prompt."""
    cur = peer.currency or "USD"
    parts = [
        f"{peer.name or peer.ticker} ({peer.ticker})",
        f"sector={peer.sector or 'n/a'}",
        f"industry={peer.industry or 'n/a'}",
        f"country={peer.country or 'n/a'}",
        f"MCap={_b(peer.market_cap)} {cur}M",
        f"P/E={_x(peer.pe_ratio)}",
        f"EBIT margin={_pct(peer.ebit_margin)}",
        f"ROE={_pct(peer.roe)}",
    ]
    la = peer.latest_annual() if hasattr(peer, "latest_annual") else None
    if la and la.revenue:
        parts.append(f"Revenue (last FY)={_b(la.revenue)} {cur}M")
    if peer.employees:
        parts.append(f"Employees={peer.employees:,}")
    return " | ".join(parts)


def _format_subject_block(company: CompanyData, bundle: Optional[dict]) -> str:
    """Detailed subject block — same density as Fisher uses, ~600 lines."""
    # Reuse the rich shared EODHD context builder
    from models._eodhd_context import build_eodhd_context, FISHER_ROWS
    return build_eodhd_context(
        company,
        bundle or {},
        FISHER_ROWS,
        peers=None,                 # peers are listed separately below
        country_macro_block="",
        n_years=10,
    )


def _industry_dynamic_prompt(
    company: CompanyData,
    bundle: Optional[dict],
    peers: Optional[dict],
    country_macro_block: str,
) -> str:
    parts = []
    parts.append("=== SUBJECT COMPANY ===")
    parts.append(_format_subject_block(company, bundle))

    # Peers — names + 1-line snapshots so the LLM can anchor the industry
    if peers:
        parts.append("\n=== PEER GROUP (EODHD-sourced data) ===")
        for tk, p in peers.items():
            parts.append("  • " + _format_peer_snapshot(p))

    if country_macro_block:
        parts.append("\n" + country_macro_block)

    parts.append(
        "\nReminder: produce the JSON object exactly as specified. The "
        "Porter 5 Forces section MUST total 4,000-5,000 words across the "
        "five 'state_2026' + 'historical_evolution' fields combined. The "
        "Competitive Advantage detail MUST be 1,000-1,500 words. Cite "
        "sources inline with publication dates. Hedge language for "
        "anything you can't verify from the data above."
    )
    return "\n".join(parts)


def _industry_prompt_parts(
    company: CompanyData,
    bundle: Optional[dict] = None,
    peers: Optional[dict] = None,
    country_macro_block: str = "",
) -> tuple[str, str]:
    """Return (cacheable_prefix, dynamic_block)."""
    return _CACHEABLE, _industry_dynamic_prompt(
        company, bundle, peers, country_macro_block,
    )


def _build_industry_prompt(
    company: CompanyData,
    bundle: Optional[dict] = None,
    peers: Optional[dict] = None,
    country_macro_block: str = "",
) -> str:
    cacheable, dynamic = _industry_prompt_parts(
        company, bundle, peers, country_macro_block,
    )
    return cacheable + "\n\n" + dynamic


# ── Output validator ─────────────────────────────────────────────────────────

_VALID_INTENSITIES = ("Strong", "Moderate", "Weak")
_VALID_ATTRACT = ("Very Unattractive", "Unattractive", "Neutral",
                  "Attractive", "Very Attractive")
_VALID_TRAJ = ("Improved Materially", "Improved", "Stable",
               "Deteriorated", "Deteriorated Materially")
_VALID_CONF = ("High", "Medium", "Low")
_VALID_ADV_SIZE = ("None", "Small", "Large")
_VALID_ADV_EVOL = ("Eroded Materially", "Eroded", "Stable",
                   "Strengthened", "Strengthened Materially")

_FORCE_NAMES = [
    "Bargaining Power of Buyers",
    "Bargaining Power of Suppliers",
    "Threat of New Entrants",
    "Threat of Substitutes",
    "Intensity of Competitive Rivalry",
]


def _coerce_enum(value, allowed, default):
    if isinstance(value, str):
        v = value.strip()
        for a in allowed:
            if v.lower() == a.lower():
                return a
    return default


def _validate_analysis(a: dict) -> dict:
    """Fill missing fields with sensible defaults so the PDF always renders."""
    if not isinstance(a, dict):
        a = {}

    a["executive_summary"] = (a.get("executive_summary") or
                              "Executive summary not available.").strip()
    a["industry_attractiveness"] = _coerce_enum(
        a.get("industry_attractiveness"), _VALID_ATTRACT, "Neutral",
    )
    a["trajectory"] = _coerce_enum(
        a.get("trajectory"), _VALID_TRAJ, "Stable",
    )

    # Scorecard
    raw_scorecard = a.get("scorecard") or []
    fixed_scorecard = []
    by_name = {s.get("force_name"): s for s in raw_scorecard if isinstance(s, dict)}
    for fname in _FORCE_NAMES:
        s = by_name.get(fname, {})
        fixed_scorecard.append({
            "force_name":         fname,
            "intensity":          _coerce_enum(s.get("intensity"),
                                               _VALID_INTENSITIES, "Moderate"),
            "one_line_takeaway":  (s.get("one_line_takeaway") or "")[:160],
        })
    a["scorecard"] = fixed_scorecard

    # Structural shifts + strategic implications
    shifts = a.get("structural_shifts") or []
    if not isinstance(shifts, list):
        shifts = []
    a["structural_shifts"] = [str(s).strip() for s in shifts if s][:6]
    a["strategic_implications"] = (
        a.get("strategic_implications") or "Strategic implications not available."
    ).strip()

    # Forces
    raw_forces = a.get("forces") or []
    forces_by_name = {f.get("name"): f for f in raw_forces if isinstance(f, dict)}
    fixed_forces = []
    for fname in _FORCE_NAMES:
        f = forces_by_name.get(fname, {})
        fixed_forces.append({
            "name":                fname,
            "current_assessment":  _coerce_enum(
                f.get("current_assessment"), _VALID_INTENSITIES, "Moderate",
            ),
            "state_2026":          (f.get("state_2026") or "Analysis not available.").strip(),
            "historical_evolution": (f.get("historical_evolution") or "Historical evolution not available.").strip(),
            "confidence_level":    _coerce_enum(
                f.get("confidence_level"), _VALID_CONF, "Medium",
            ),
            "data_gaps":           (f.get("data_gaps") or "").strip(),
            "sources":             [str(s).strip() for s in (f.get("sources") or []) if s][:10],
        })
    a["forces"] = fixed_forces

    # Competitive advantage
    a["competitive_advantage_size"] = _coerce_enum(
        a.get("competitive_advantage_size"), _VALID_ADV_SIZE, "Small",
    )
    a["competitive_advantage_evolution"] = _coerce_enum(
        a.get("competitive_advantage_evolution"), _VALID_ADV_EVOL, "Stable",
    )
    a["competitive_advantage_summary"] = (
        a.get("competitive_advantage_summary") or
        "Competitive advantage summary not available."
    ).strip()
    a["competitive_advantage_detail"] = (
        a.get("competitive_advantage_detail") or
        "Detailed competitive-advantage analysis not available."
    ).strip()
    a["competitive_advantage_sources"] = [
        str(s).strip() for s in (a.get("competitive_advantage_sources") or []) if s
    ][:15]

    unc = a.get("key_uncertainties") or []
    if not isinstance(unc, list):
        unc = []
    a["key_uncertainties"] = [str(u).strip() for u in unc if u][:8]

    return a


# ── Local formatters ─────────────────────────────────────────────────────────

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
