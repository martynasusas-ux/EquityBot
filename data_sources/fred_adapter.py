"""
fred_adapter.py — FRED (Federal Reserve Economic Data) macro snapshot.

Fetches a curated bundle of US macro series from the St. Louis Fed API and
returns a MacroSnapshot dataclass.  A plain-text block is available via
MacroSnapshot.format_for_prompt() for direct injection into LLM prompts.

Cached at cache/macro_snapshot.json with a 6-hour TTL — roughly 4 fetches
per day, well within FRED's free-tier limit of 120 req/minute.

Series fetched
--------------
FEDFUNDS    Federal Funds Effective Rate           (monthly, %)
DGS2        2-Year Treasury Constant Maturity      (daily,   %)
DGS10       10-Year Treasury Constant Maturity     (daily,   %)
T10Y2Y      10Y–2Y Treasury Spread                 (daily,   pp)
CPIAUCSL    CPI All Urban — YoY % change           (monthly, pc1 units)
CPILFESL    Core CPI (ex food & energy) — YoY %   (monthly, pc1 units)
UNRATE      Unemployment Rate                      (monthly, %)
BAMLH0A0HYM2  ICE BofA HY OAS Spread              (daily,   %)
BAMLC0A0CM    ICE BofA IG OAS Spread               (daily,   %)
DEXUSEU     USD/EUR Spot Exchange Rate             (daily,   USD per EUR)

Usage
-----
    from data_sources.fred_adapter import FredAdapter
    snap = FredAdapter().fetch()
    print(snap.format_for_prompt())

    # Or use the convenience function (returns "" gracefully on failure):
    from data_sources.fred_adapter import get_macro_block
    block = get_macro_block()
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CACHE_TTL_HOURS = 6
_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

# Series id → (field_name, units_param, description)
_SERIES: list[tuple[str, str, str, str]] = [
    ("FEDFUNDS",     "fed_funds_rate",  "lin",  "Fed Funds Rate"),
    ("DGS2",         "t2y",             "lin",  "2Y Treasury"),
    ("DGS10",        "t10y",            "lin",  "10Y Treasury"),
    ("T10Y2Y",       "yield_spread",    "lin",  "10Y-2Y Spread"),
    ("CPIAUCSL",     "cpi_yoy",         "pc1",  "CPI YoY"),
    ("CPILFESL",     "core_cpi_yoy",    "pc1",  "Core CPI YoY"),
    ("UNRATE",       "unemployment",    "lin",  "Unemployment Rate"),
    ("BAMLH0A0HYM2", "hy_spread",       "lin",  "HY OAS Spread"),
    ("BAMLC0A0CM",   "ig_spread",       "lin",  "IG OAS Spread"),
    ("DEXUSEU",      "usd_eur",         "lin",  "USD/EUR"),
]


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class MacroSnapshot:
    as_of_date: str = ""
    # Rates
    fed_funds_rate:  Optional[float] = None
    t2y:             Optional[float] = None
    t10y:            Optional[float] = None
    yield_spread:    Optional[float] = None   # 10Y − 2Y
    # Inflation (YoY %)
    cpi_yoy:         Optional[float] = None
    core_cpi_yoy:    Optional[float] = None
    # Labour
    unemployment:    Optional[float] = None
    # Credit spreads (OAS, %)
    hy_spread:       Optional[float] = None
    ig_spread:       Optional[float] = None
    # FX
    usd_eur:         Optional[float] = None
    # Meta
    fetch_timestamp: str = ""
    data_sources:    list = field(default_factory=list)

    # ── Prompt block ──────────────────────────────────────────────────────────

    def format_for_prompt(self) -> str:
        """
        Returns a compact, LLM-readable macro context block.
        Sections with no data are omitted silently.
        """
        if not any([
            self.fed_funds_rate, self.t2y, self.t10y,
            self.cpi_yoy, self.unemployment, self.hy_spread,
        ]):
            return ""

        lines: list[str] = [
            f"--- MACRO ENVIRONMENT (FRED data, as of {self.as_of_date}) ---"
        ]

        # Interest rates
        rate_lines = []
        if self.fed_funds_rate is not None:
            rate_lines.append(f"  Fed Funds Rate:     {self.fed_funds_rate:.2f}%")
        if self.t2y is not None:
            rate_lines.append(f"  2Y Treasury:        {self.t2y:.2f}%")
        if self.t10y is not None:
            rate_lines.append(f"  10Y Treasury:       {self.t10y:.2f}%")
        if self.yield_spread is not None:
            shape = (
                "normal"   if self.yield_spread > 0.25
                else "flat" if self.yield_spread > -0.10
                else "INVERTED"
            )
            rate_lines.append(
                f"  Yield Curve 10Y-2Y: {self.yield_spread:+.2f}pp  ({shape})"
            )
        if rate_lines:
            lines.append("Interest Rates (US):")
            lines.extend(rate_lines)

        # Inflation
        infl_lines = []
        if self.cpi_yoy is not None:
            infl_lines.append(f"  CPI YoY:            {self.cpi_yoy:.1f}%")
        if self.core_cpi_yoy is not None:
            infl_lines.append(f"  Core CPI YoY:       {self.core_cpi_yoy:.1f}%")
        if infl_lines:
            lines.append("Inflation (US):")
            lines.extend(infl_lines)

        # Labour
        if self.unemployment is not None:
            lines.append("Labour Market (US):")
            lines.append(f"  Unemployment Rate:  {self.unemployment:.1f}%")

        # Credit
        credit_lines = []
        if self.hy_spread is not None:
            risk = (
                "elevated" if self.hy_spread > 5.0
                else "compressed" if self.hy_spread < 2.5
                else "moderate"
            )
            credit_lines.append(f"  HY OAS Spread:      {self.hy_spread:.2f}%  ({risk} risk appetite)")
        if self.ig_spread is not None:
            credit_lines.append(f"  IG OAS Spread:      {self.ig_spread:.2f}%")
        if credit_lines:
            lines.append("Credit Markets:")
            lines.extend(credit_lines)

        # FX
        if self.usd_eur is not None:
            lines.append("Foreign Exchange:")
            lines.append(f"  USD/EUR:            {self.usd_eur:.4f}")

        return "\n".join(lines)


# ── Adapter ───────────────────────────────────────────────────────────────────

class FredAdapter:
    """
    Fetches a MacroSnapshot from the FRED API.
    Results are cached locally for _CACHE_TTL_HOURS hours.
    """

    def __init__(self, api_key: str = ""):
        from config import FRED_API_KEY
        self._key = api_key or FRED_API_KEY

    # ── Public ────────────────────────────────────────────────────────────────

    def fetch(self, force_refresh: bool = False) -> MacroSnapshot:
        """
        Return a MacroSnapshot.  Loads from cache if fresh, otherwise hits FRED.
        Returns an empty MacroSnapshot (no error) if the key is missing or all
        fetches fail.
        """
        if not self._key:
            logger.debug("[FredAdapter] No FRED_API_KEY set — skipping macro fetch")
            return MacroSnapshot()

        if not force_refresh:
            cached = self._load_cache()
            if cached:
                logger.info(f"[FredAdapter] Cache hit (as of {cached.as_of_date})")
                return cached

        snap = self._fetch_live()
        if snap.as_of_date:
            self._save_cache(snap)

        return snap

    # ── Live fetch ────────────────────────────────────────────────────────────

    def _fetch_live(self) -> MacroSnapshot:
        try:
            import requests
        except ImportError:
            logger.warning("[FredAdapter] requests not installed")
            return MacroSnapshot()

        snap = MacroSnapshot(
            fetch_timestamp=datetime.utcnow().isoformat(),
            data_sources=["FRED"],
        )
        dates: list[str] = []

        session = requests.Session()
        session.headers.update({"User-Agent": "EquityBot/1.0"})

        for series_id, field_name, units, label in _SERIES:
            try:
                params = {
                    "series_id":   series_id,
                    "api_key":     self._key,
                    "file_type":   "json",
                    "sort_order":  "desc",
                    "limit":       5,       # grab a few to skip "." missing values
                    "units":       units,
                }
                resp = session.get(_BASE_URL, params=params, timeout=10)
                resp.raise_for_status()
                obs_list = resp.json().get("observations", [])

                # Find the first observation that isn't a missing-value placeholder
                value = None
                obs_date = ""
                for obs in obs_list:
                    raw = obs.get("value", ".")
                    if raw not in (".", "", "NA"):
                        try:
                            value = float(raw)
                            obs_date = obs.get("date", "")
                            break
                        except (ValueError, TypeError):
                            continue

                if value is not None:
                    setattr(snap, field_name, value)
                    if obs_date:
                        dates.append(obs_date)
                    logger.debug(f"[FredAdapter] {series_id}: {value} ({obs_date})")
                else:
                    logger.debug(f"[FredAdapter] {series_id}: no valid observation")

            except Exception as e:
                logger.debug(f"[FredAdapter] {series_id} failed: {e}")

        snap.as_of_date = max(dates) if dates else datetime.utcnow().strftime("%Y-%m-%d")
        return snap

    # ── Cache ─────────────────────────────────────────────────────────────────

    def _cache_path(self) -> Path:
        from config import CACHE_DIR
        return CACHE_DIR / "macro_snapshot.json"

    def _save_cache(self, snap: MacroSnapshot) -> None:
        try:
            import dataclasses
            d = dataclasses.asdict(snap)
            d["_cached_at"] = datetime.utcnow().isoformat()
            with open(self._cache_path(), "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2)
        except Exception as e:
            logger.debug(f"[FredAdapter] Cache write failed: {e}")

    def _load_cache(self) -> Optional[MacroSnapshot]:
        p = self._cache_path()
        if not p.exists():
            return None
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            cached_at = datetime.fromisoformat(d.pop("_cached_at"))
            if datetime.utcnow() - cached_at > timedelta(hours=_CACHE_TTL_HOURS):
                return None
            d.pop("data_sources", None)
            return MacroSnapshot(**{k: v for k, v in d.items()
                                    if k in MacroSnapshot.__dataclass_fields__})
        except Exception as e:
            logger.debug(f"[FredAdapter] Cache read failed: {e}")
            return None


# ── Module-level convenience ──────────────────────────────────────────────────

_cached_block: Optional[str] = None
_cached_block_ts: Optional[datetime] = None


def get_macro_block(force_refresh: bool = False) -> str:
    """
    Returns a formatted macro text block ready for LLM prompt injection.
    Uses a process-level in-memory cache (on top of the file cache) so the
    same Streamlit session doesn't hit disk on every report generation.
    Returns "" silently if FRED is unavailable.
    """
    global _cached_block, _cached_block_ts

    # In-memory cache: valid for the same TTL window
    if (
        not force_refresh
        and _cached_block is not None
        and _cached_block_ts is not None
        and datetime.utcnow() - _cached_block_ts < timedelta(hours=_CACHE_TTL_HOURS)
    ):
        return _cached_block

    try:
        snap = FredAdapter().fetch(force_refresh=force_refresh)
        block = snap.format_for_prompt()
    except Exception as e:
        logger.warning(f"[FredAdapter] get_macro_block failed: {e}")
        block = ""

    _cached_block = block
    _cached_block_ts = datetime.utcnow()
    return block
