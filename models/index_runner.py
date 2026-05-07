"""
index_runner.py — Generates an Index Overview report for a market index or ETF.

Uses IndexData (not CompanyData) and the index_overview framework.
Output: self-contained HTML file saved to outputs/.
"""

from __future__ import annotations
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class IndexRunner:
    """
    Run an index/ETF analysis report.

    Usage:
        runner = IndexRunner()
        html_path = runner.run("^OMXH25", output_path="outputs/omxh25_2026.html")
    """

    def run(
        self,
        ticker: str,
        output_path: Optional[str] = None,
        force_refresh: bool = False,
    ) -> tuple:
        """
        Fetch index data, call Claude, render HTML.
        Returns (html_path, analysis) tuple so callers can read the
        recommendation without re-parsing the HTML.
        """
        from data_sources.data_manager import DataManager
        from framework_manager import FrameworkManager

        # ── Load framework config ──────────────────────────────────────────────
        fw = FrameworkManager().get("index_overview")
        if fw is None:
            raise ValueError("index_overview framework not found in frameworks/")

        # ── Fetch index data ───────────────────────────────────────────────────
        dm       = DataManager()
        idx_data = dm.get_index(ticker, force_refresh=force_refresh)

        if not idx_data.name or idx_data.name == ticker:
            logger.warning(f"[IndexRunner] Could not fetch data for {ticker}")

        # ── Build prompt ───────────────────────────────────────────────────────
        prompt = self._build_prompt(fw.prompt_template, idx_data)

        # ── Call Claude ────────────────────────────────────────────────────────
        logger.info(f"[IndexRunner] Calling Claude for {ticker}…")
        raw = self._call_claude(fw.system_prompt, prompt)

        # ── Parse JSON response ────────────────────────────────────────────────
        analysis = self._parse_json(raw)

        # ── Render HTML ────────────────────────────────────────────────────────
        html = self._render_html(fw, idx_data, analysis)

        # ── Save output ────────────────────────────────────────────────────────
        if output_path is None:
            from config import OUTPUTS_DIR
            safe = ticker.replace("^", "").replace(".", "_")
            date = datetime.now().strftime("%Y-%m-%d")
            output_path = str(OUTPUTS_DIR / f"{safe}_index_overview_{date}.html")

        os.makedirs(Path(output_path).parent, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

        logger.info(f"[IndexRunner] Report saved → {output_path}")
        return output_path, analysis

    # ── Prompt builder ────────────────────────────────────────────────────────

    def _build_prompt(self, template: str, idx: "IndexData") -> str:
        def _pct(v):
            if v is None: return "n/a"
            return f"{v*100:+.1f}%"

        def _pct_plain(v):
            if v is None: return "n/a"
            return f"{v*100:.2f}%"

        def _num(v, decimals=2):
            if v is None: return "n/a"
            return f"{v:,.{decimals}f}"

        def _aum(v):
            if v is None: return "n/a"
            if v >= 1000:
                return f"${v/1000:,.1f}B"
            return f"${v:,.0f}M"

        # Format top holdings table
        holdings_lines = []
        for h in idx.top_holdings[:15]:
            holdings_lines.append(
                f"  {h.get('ticker','?'):<12} {h.get('name',''):<35} {h.get('weight_pct','?')}%"
            )
        top_holdings_table = (
            "\n".join(holdings_lines) if holdings_lines
            else "  (not available for pure index tickers)"
        )

        # Sector weights table
        sec_lines = [
            f"  {k:<30} {v:.1f}%"
            for k, v in sorted(
                idx.sector_weights.items(), key=lambda x: x[1], reverse=True
            )[:12]
        ]
        sector_weights_table = "\n".join(sec_lines) if sec_lines else "  n/a"

        # Country weights table
        cty_lines = [
            f"  {k:<30} {v:.1f}%"
            for k, v in sorted(
                idx.country_weights.items(), key=lambda x: x[1], reverse=True
            )[:12]
        ]
        country_weights_table = "\n".join(cty_lines) if cty_lines else "  n/a"

        subs = {
            "{index_name}":            idx.name or idx.ticker,
            "{index_ticker}":          idx.ticker,
            "{index_type}":            idx.index_type,
            "{currency}":              idx.currency or "n/a",
            "{as_of_date}":            idx.as_of_date,
            "{current_level}":         _num(idx.current_level),
            "{low_52w}":               _num(idx.low_52w),
            "{high_52w}":              _num(idx.high_52w),
            "{change_1d_pct}":         _pct(idx.change_1d_pct / 100 if idx.change_1d_pct else None),
            "{return_ytd}":            _pct(idx.return_ytd),
            "{return_1m}":             _pct(idx.return_1m),
            "{return_3m}":             _pct(idx.return_3m),
            "{return_1y}":             _pct(idx.return_1y),
            "{return_3y_ann}":         _pct(idx.return_3y_ann),
            "{return_5y_ann}":         _pct(idx.return_5y_ann),
            "{volatility_1y_ann}":     _pct(idx.volatility_1y_ann),
            "{weighted_pe}":           _num(idx.weighted_pe, 1) + "x" if idx.weighted_pe else "n/a",
            "{dividend_yield}":        _pct_plain(idx.dividend_yield),
            "{aum}":                   _aum(idx.aum_millions),
            "{expense_ratio}":         _pct_plain(idx.expense_ratio),
            "{top_holdings_table}":    top_holdings_table,
            "{sector_weights_table}":  sector_weights_table,
            "{country_weights_table}": country_weights_table,
        }

        # Append FRED macro block unconditionally (after template substitution)
        try:
            from data_sources.fred_adapter import get_macro_block
            macro = get_macro_block()
        except Exception:
            macro = ""

        result = template
        for k, v in subs.items():
            result = result.replace(k, str(v))

        if macro:
            result += f"\n\n{macro}"
        return result

    # ── Claude call ───────────────────────────────────────────────────────────

    def _call_claude(self, system_prompt: str, prompt: str) -> str:
        from config import ANTHROPIC_API_KEY, LLM_MODEL
        import anthropic

        client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model=LLM_MODEL,
            max_tokens=4000,
            temperature=0.3,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text

    # ── JSON parser ───────────────────────────────────────────────────────────

    def _parse_json(self, raw: str) -> dict:
        text = raw.strip()
        # Strip markdown code fences
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Find outermost {} braces
            start = text.find("{")
            end   = text.rfind("}")
            if start != -1 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass
        logger.warning("[IndexRunner] Could not parse JSON response")
        return {}

    # ── HTML renderer ─────────────────────────────────────────────────────────

    def _render_html(self, fw, idx: "IndexData", analysis: dict) -> str:
        from agents.report_generic import render_html as render_generic
        return render_generic(fw, analysis, company=None, index_data=idx)
