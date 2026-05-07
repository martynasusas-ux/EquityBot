"""
adversarial.py — Adversarial dual-model engine for Your Humble EquityBot.

When ADVERSARIAL_MODE=true:
  1. Claude (primary) and GPT-4o (secondary) independently generate the full analysis.
  2. Each model critiques the other's output.
  3. Fields are compared — consensus = high confidence, contested = flag to investor.
  4. A merged "best-of-both" analysis is produced for the PDF.
  5. An extra Adversarial Review page is appended to the report.

The merged analysis is a drop-in replacement for the single-model analysis dict —
existing PDF generators work unchanged, with the adversarial page added on top.
"""

from __future__ import annotations
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from agents.llm_client import LLMClient

logger = logging.getLogger(__name__)


# ── Result container ──────────────────────────────────────────────────────────

@dataclass
class AdversarialResult:
    """
    Full output of a dual-model adversarial analysis run.
    Passed to pdf_adversarial.py to render the extra review page.
    """
    primary:               dict = field(default_factory=dict)   # Claude's analysis
    secondary:             dict = field(default_factory=dict)   # GPT-4o's analysis
    merged:                dict = field(default_factory=dict)   # Best-of-both for PDF
    critique_of_primary:   str  = ""   # GPT-4o's critique of Claude
    critique_of_secondary: str  = ""   # Claude's critique of GPT-4o
    consensus_fields:      list = field(default_factory=list)   # Fields that agree
    contested_fields:      list = field(default_factory=list)   # Fields that disagree
    primary_rec:           str  = "HOLD"
    secondary_rec:         str  = "HOLD"
    recs_agree:            bool = False


# ── Engine ────────────────────────────────────────────────────────────────────

class AdversarialEngine:
    """
    Orchestrates independent dual-model analysis, cross-critique, and merge.

    Designed to be a drop-in replacement for a single LLMClient.generate_json() call:
        result = engine.run(prompt, system_prompt, max_tokens, report_type)
        analysis = result.merged   # use this for PDF generation
    """

    CRITIQUE_SYSTEM = (
        "You are a senior risk analyst and Devil's Advocate. "
        "Your job: identify where the analysis you are reviewing is overconfident, "
        "misses key risks, or draws conclusions not supported by the data. "
        "Be specific. Reference numbers from the financial data. "
        "Flag where the other analyst is more rigorous. Under 250 words."
    )

    def __init__(self):
        self.claude  = LLMClient(provider="claude", model="claude-sonnet-4-5")
        self.gpt4o   = LLMClient(provider="openai", model="gpt-4o")

    def run(
        self,
        user_prompt:   str,
        system_prompt: str,
        max_tokens:    int  = 5000,
        report_type:   str  = "overview",   # "overview" | "fisher" | "gravity"
    ) -> AdversarialResult:
        """
        Full adversarial pipeline. Returns AdversarialResult.
        Primary = Claude, Secondary = GPT-4o.
        """
        logger.info("[Adversarial] Step 1/4 — Claude independent analysis...")
        primary = self.claude.generate_json(user_prompt, system_prompt, max_tokens)

        logger.info("[Adversarial] Step 2/4 — GPT-4o independent analysis...")
        secondary = self.gpt4o.generate_json(user_prompt, system_prompt, max_tokens)

        logger.info("[Adversarial] Step 3/4 — Cross-critique...")
        critique_of_primary   = self._critique(
            analyst_label="Claude",
            subject=primary,
            comparator=secondary,
            critic=self.gpt4o,
        )
        critique_of_secondary = self._critique(
            analyst_label="GPT-4o",
            subject=secondary,
            comparator=primary,
            critic=self.claude,
        )

        logger.info("[Adversarial] Step 4/4 — Comparing and merging...")
        consensus, contested = _deep_compare(primary, secondary, report_type)
        merged = _merge(primary, secondary, contested, report_type)

        p_rec = primary.get("recommendation", "HOLD")
        s_rec = secondary.get("recommendation", "HOLD")

        return AdversarialResult(
            primary=primary,
            secondary=secondary,
            merged=merged,
            critique_of_primary=critique_of_primary,
            critique_of_secondary=critique_of_secondary,
            consensus_fields=consensus,
            contested_fields=contested,
            primary_rec=p_rec,
            secondary_rec=s_rec,
            recs_agree=(p_rec.strip().upper() == s_rec.strip().upper()),
        )

    # ── Private ───────────────────────────────────────────────────────────────

    def _critique(
        self,
        analyst_label: str,
        subject:       dict,
        comparator:    dict,
        critic:        LLMClient,
    ) -> str:
        prompt = (
            f"You are reviewing the investment analysis produced by {analyst_label}.\n\n"
            f"=== {analyst_label}'s Analysis ===\n"
            f"{json.dumps(subject, indent=2)[:3000]}\n\n"
            f"=== The Other Analyst's Analysis (for comparison) ===\n"
            f"{json.dumps(comparator, indent=2)[:3000]}\n\n"
            f"Critique {analyst_label}'s analysis in under 250 words. "
            f"What did they miss, overstate, or get wrong? "
            f"Where is the other analyst more rigorous? "
            f"Be specific — reference numbers and logic, not just opinions."
        )
        try:
            return critic.generate(prompt, self.CRITIQUE_SYSTEM, max_tokens=512)
        except Exception as e:
            logger.warning(f"[Adversarial] Critique failed: {e}")
            return f"Critique unavailable: {e}"


# ── Deep field comparison ─────────────────────────────────────────────────────

def _deep_compare(
    a: dict, b: dict, report_type: str
) -> tuple[list[dict], list[dict]]:
    """
    Compare the two analyses field by field.
    Returns (consensus_list, contested_list).
    Each consensus item: {field, value}
    Each contested item: {field, primary, secondary, severity}
    """
    consensus: list[dict] = []
    contested: list[dict] = []

    def _str_match(va, vb) -> bool:
        return str(va).strip().lower() == str(vb).strip().lower()

    def _numeric_close(va, vb, pct_threshold=15) -> bool:
        try:
            fa, fb = float(va), float(vb)
            avg = (fa + fb) / 2
            return avg == 0 or abs(fa - fb) / abs(avg) * 100 <= pct_threshold
        except Exception:
            return False

    # ── Fields compared for all report types ─────────────────────────────────
    common_fields = [
        ("recommendation",   "string"),
        ("moat_width",       "string"),
    ]

    # ── Report-type specific fields ───────────────────────────────────────────
    type_fields = {
        "overview": [
            ("recommendation_rationale", "text_sentiment"),
        ],
        "fisher": [
            ("fisher_grade",       "string"),
            ("fisher_total_score", "numeric_15pct"),
        ],
        "gravity": [
            ("gravity_grade",       "string"),
            ("total_gravity_score", "numeric_15pct"),
            ("revenue_model.pricing_power", "string"),
        ],
    }

    fields_to_check = common_fields + type_fields.get(report_type, [])

    for field_path, compare_type in fields_to_check:
        # Support dot-notation for nested fields
        va = _get_nested(a, field_path)
        vb = _get_nested(b, field_path)

        if va is None or vb is None:
            continue

        if compare_type == "string":
            agrees = _str_match(va, vb)
        elif compare_type == "numeric_15pct":
            agrees = _numeric_close(va, vb, 15)
        elif compare_type == "text_sentiment":
            # Simple heuristic: check if both lean same direction
            agrees = _same_sentiment(str(va), str(vb))
        else:
            agrees = _str_match(va, vb)

        label = field_path.replace("_", " ").replace(".", " → ").title()

        if agrees:
            consensus.append({"field": label, "value": str(va)})
        else:
            # Severity: recommendation disagreement is most serious
            severity = "high" if field_path == "recommendation" else "medium"
            contested.append({
                "field":    label,
                "primary":  str(va)[:200],
                "secondary":str(vb)[:200],
                "severity": severity,
            })

    # ── Fisher point-level comparison (bonus: flag big score divergences) ────
    if report_type == "fisher":
        pts_a = {p["number"]: p.get("score", 3) for p in a.get("fisher_points", [])
                 if isinstance(p, dict)}
        pts_b = {p["number"]: p.get("score", 3) for p in b.get("fisher_points", [])
                 if isinstance(p, dict)}
        for n in range(1, 16):
            sa, sb = pts_a.get(n, 3), pts_b.get(n, 3)
            if abs(sa - sb) >= 2:  # ≥2 point spread on a 5-point scale = flag
                contested.append({
                    "field":    f"Fisher Point {n:02d}",
                    "primary":  f"{sa}/5",
                    "secondary":f"{sb}/5",
                    "severity": "medium",
                })

    # ── Gravity dimension divergences ─────────────────────────────────────────
    if report_type == "gravity":
        dims_a = {d["number"]: d.get("score", 3) for d in a.get("gravity_dimensions", [])
                  if isinstance(d, dict)}
        dims_b = {d["number"]: d.get("score", 3) for d in b.get("gravity_dimensions", [])
                  if isinstance(d, dict)}
        for n in range(1, 11):
            sa, sb = dims_a.get(n, 3), dims_b.get(n, 3)
            if abs(sa - sb) >= 2:
                contested.append({
                    "field":    f"Gravity Dimension {n:02d}",
                    "primary":  f"{sa}/5",
                    "secondary":f"{sb}/5",
                    "severity": "medium",
                })

    return consensus, contested


# ── Merge logic ───────────────────────────────────────────────────────────────

def _merge(
    primary:   dict,
    secondary: dict,
    contested: list[dict],
    report_type: str,
) -> dict:
    """
    Produce a single merged analysis dict for the PDF generator.

    Strategy:
    - Start with primary (Claude) as base.
    - On contested recommendation: take the more conservative view.
    - On contested scores: average them.
    - Add a merge_note if recommendation is contested.
    - Preserve all primary fields so existing PDF generators work unchanged.
    """
    import copy
    merged = copy.deepcopy(primary)

    contested_fields = {c["field"].lower() for c in contested}

    # ── Recommendation merge ──────────────────────────────────────────────────
    p_rec = primary.get("recommendation", "HOLD").strip().upper()
    s_rec = secondary.get("recommendation", "HOLD").strip().upper()

    if p_rec != s_rec:
        # Conservative merge: BUY+HOLD→HOLD, HOLD+SELL→HOLD, BUY+SELL→HOLD
        rank = {"BUY": 1, "HOLD": 2, "SELL": 3}
        rp, rs = rank.get(p_rec, 2), rank.get(s_rec, 2)
        merged["recommendation"] = "HOLD" if rp != rs else p_rec
        merged["recommendation_rationale"] = (
            f"[ADVERSARIAL — CONTESTED] Claude: {p_rec}. GPT-4o: {s_rec}. "
            f"Conservative merged view: HOLD pending resolution. "
            f"Claude: {primary.get('recommendation_rationale','')[:200]} "
            f"GPT-4o: {secondary.get('recommendation_rationale','')[:200]}"
        )
    else:
        merged["recommendation"] = p_rec

    # ── Score merges (average) ────────────────────────────────────────────────
    if report_type == "fisher":
        ps = primary.get("fisher_total_score")
        ss = secondary.get("fisher_total_score")
        if ps is not None and ss is not None:
            merged["fisher_total_score"] = round((ps + ss) / 2)
            t = merged["fisher_total_score"]
            merged["fisher_grade"] = ("A" if t >= 65 else "B" if t >= 55 else
                                       "C" if t >= 45 else "D" if t >= 35 else "F")

    if report_type == "gravity":
        ps = primary.get("total_gravity_score")
        ss = secondary.get("total_gravity_score")
        if ps is not None and ss is not None:
            merged["total_gravity_score"] = round((ps + ss) / 2)
            t = merged["total_gravity_score"]
            merged["gravity_grade"] = ("A" if t >= 45 else "B" if t >= 38 else
                                        "C" if t >= 30 else "D" if t >= 22 else "F")

    # Mark that this is a merged adversarial result
    merged["_adversarial"] = True
    merged["_primary_rec"]   = p_rec
    merged["_secondary_rec"] = s_rec

    return merged


# ── Utilities ─────────────────────────────────────────────────────────────────

def _get_nested(d: dict, path: str):
    """Get a value from a dict using dot-notation path (e.g. 'revenue_model.pricing_power')."""
    keys = path.split(".")
    val  = d
    for k in keys:
        if not isinstance(val, dict):
            return None
        val = val.get(k)
    return val


def _same_sentiment(text_a: str, text_b: str) -> bool:
    """
    Very rough heuristic: both texts lean the same direction if they share
    the same predominant signal word (bullish / bearish / neutral).
    """
    bull = {"attractive", "undervalued", "compelling", "buy", "strong", "upside"}
    bear = {"overvalued", "expensive", "risky", "sell", "weak", "decline", "concern"}

    def score(txt):
        t = txt.lower()
        b = sum(1 for w in bull if w in t)
        br = sum(1 for w in bear if w in t)
        if b > br: return "bull"
        if br > b: return "bear"
        return "neutral"

    return score(text_a) == score(text_b)
