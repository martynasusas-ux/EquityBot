"""
worldbank_adapter.py — World Bank + OECD free macro data.

No API key required. Data available for all countries.
World Bank API: https://api.worldbank.org/v2/
OECD SDMX API: https://sdmx.oecd.org/public/rest/

Provides country-level macro indicators relevant to equity analysis:
- GDP (current USD, growth rate)
- Inflation (CPI)
- Military spending % of GDP
- Government debt % of GDP
- Population
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.worldbank.org/v2/country/{code}/indicator/{indicator}"

# World Bank indicator codes mapped to CountryMacro field names
_INDICATORS: list[tuple[str, str, str]] = [
    ("NY.GDP.MKTP.CD",   "gdp_usd_bn",               "GDP current USD"),
    ("NY.GDP.MKTP.KD.ZG","gdp_growth_pct",            "GDP growth (annual %)"),
    ("FP.CPI.TOTL.ZG",   "inflation_pct",             "Inflation CPI (annual %)"),
    ("MS.MIL.XPND.GD.ZS","military_spending_pct_gdp", "Military expenditure (% of GDP)"),
    ("GC.DOD.TOTL.GD.ZS","gov_debt_pct_gdp",          "Central gov debt (% of GDP)"),
    ("SP.POP.TOTL",       "population_m",              "Population total"),
]


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class CountryMacro:
    """Country-level macroeconomic indicators from the World Bank."""

    country_code: str                            # ISO2: "DE", "FI", "US"
    country_name: str = ""
    gdp_usd_bn: Optional[float] = None          # GDP in billions USD, latest year
    gdp_growth_pct: Optional[float] = None      # Real GDP growth %, latest
    inflation_pct: Optional[float] = None       # CPI inflation %, latest
    military_spending_pct_gdp: Optional[float] = None  # Military % of GDP
    gov_debt_pct_gdp: Optional[float] = None    # Government debt % of GDP
    population_m: Optional[float] = None        # Population in millions
    data_year: Optional[int] = None             # Year the data refers to

    # ── Prompt block ──────────────────────────────────────────────────────────

    def format_for_prompt(self) -> str:
        """
        Returns a compact, LLM-readable country macro context block.
        Returns "" if no meaningful data is available.

        Example output
        --------------
        COUNTRY MACRO — Germany (DE), 2023:
        • GDP: $4,456B | Growth: +1.8% | Inflation: 5.9%
        • Military spending: 1.6% of GDP | Gov debt: 66.3% of GDP
        • Population: 84.4M
        """
        # Consider the block empty if we have no GDP figure
        if self.gdp_usd_bn is None and self.gdp_growth_pct is None and self.inflation_pct is None:
            return ""

        name_str = self.country_name or self.country_code
        year_str = f", {self.data_year}" if self.data_year else ""
        header = f"COUNTRY MACRO — {name_str} ({self.country_code}){year_str}:"

        # Line 1: GDP, growth, inflation
        gdp_parts: list[str] = []
        if self.gdp_usd_bn is not None:
            gdp_parts.append(f"GDP: ${self.gdp_usd_bn:,.1f}B")
        if self.gdp_growth_pct is not None:
            sign = "+" if self.gdp_growth_pct >= 0 else ""
            gdp_parts.append(f"Growth: {sign}{self.gdp_growth_pct:.1f}%")
        if self.inflation_pct is not None:
            gdp_parts.append(f"Inflation: {self.inflation_pct:.1f}%")
        line1 = "• " + " | ".join(gdp_parts) if gdp_parts else ""

        # Line 2: military, debt
        fiscal_parts: list[str] = []
        if self.military_spending_pct_gdp is not None:
            fiscal_parts.append(f"Military spending: {self.military_spending_pct_gdp:.1f}% of GDP")
        if self.gov_debt_pct_gdp is not None:
            fiscal_parts.append(f"Gov debt: {self.gov_debt_pct_gdp:.1f}% of GDP")
        line2 = "• " + " | ".join(fiscal_parts) if fiscal_parts else ""

        # Line 3: population
        line3 = f"• Population: {self.population_m:.1f}M" if self.population_m is not None else ""

        body_lines = [ln for ln in [line1, line2, line3] if ln]
        if not body_lines:
            return ""

        return "\n".join([header] + body_lines)


# ── Adapter ───────────────────────────────────────────────────────────────────

class WorldBankAdapter:
    """
    Fetches country-level macro indicators from the World Bank API.
    No API key required.

    Usage:
        adapter = WorldBankAdapter()
        macro = adapter.fetch_country("DE")
        print(macro.format_for_prompt())
    """

    def fetch_country(self, country_code: str) -> CountryMacro:
        """
        Fetch macro indicators for the given ISO2 country code from World Bank.

        Requests the last 5 years of data (mrv=5) for each indicator and takes
        the most recent non-null value.  Returns a CountryMacro with all
        available fields populated; missing fields remain None.

        Parameters
        ----------
        country_code : str
            ISO 3166-1 alpha-2 country code (e.g. "DE", "US", "FI").

        Returns
        -------
        CountryMacro
            Populated where data is available.  Always returns an object,
            never raises.
        """
        try:
            import requests
        except ImportError:
            logger.warning("[worldbank] requests not installed — cannot fetch country macro")
            return CountryMacro(country_code=country_code.upper())

        code = country_code.strip().upper()
        macro = CountryMacro(country_code=code)

        session = requests.Session()
        session.headers.update({"User-Agent": "EquityBot/1.0"})

        gdp_year: Optional[int] = None

        for indicator, field_name, label in _INDICATORS:
            url = _BASE_URL.format(code=code, indicator=indicator)
            params = {
                "format": "json",
                "mrv":    5,      # most recent 5 values (covers data lag)
            }
            try:
                resp = session.get(url, params=params, timeout=10)
                resp.raise_for_status()
                payload = resp.json()

                # World Bank returns [metadata_dict, [observations_list]]
                if not isinstance(payload, list) or len(payload) < 2:
                    logger.debug(f"[worldbank] Unexpected response format for {indicator}")
                    continue

                # Populate country name from metadata on the first call
                if not macro.country_name and isinstance(payload[0], dict):
                    # Country name sometimes lives in the page metadata
                    pass  # populated below from observation records

                observations = payload[1] or []
                value: Optional[float] = None
                obs_year: Optional[int] = None

                for obs in observations:
                    raw = obs.get("value")
                    if raw is None:
                        continue
                    try:
                        value = float(raw)
                        date_str = obs.get("date", "")
                        obs_year = int(date_str[:4]) if date_str else None
                        # Grab country name from obs record
                        if not macro.country_name:
                            country_info = obs.get("country") or {}
                            macro.country_name = country_info.get("value", "")
                        break
                    except (ValueError, TypeError):
                        continue

                if value is None:
                    logger.debug(f"[worldbank] {code} {indicator}: no valid data")
                    continue

                # GDP: convert from USD to billions
                if field_name == "gdp_usd_bn":
                    value = value / 1e9
                    gdp_year = obs_year

                # Population: convert to millions
                elif field_name == "population_m":
                    value = value / 1e6

                setattr(macro, field_name, round(value, 4))
                logger.debug(f"[worldbank] {code} {indicator}: {value} ({obs_year})")

            except Exception as exc:
                logger.debug(f"[worldbank] {code} {indicator} failed: {exc}")

        macro.data_year = gdp_year
        logger.info(
            f"[worldbank] {code} ({macro.country_name}): "
            f"GDP=${macro.gdp_usd_bn}B, growth={macro.gdp_growth_pct}%, "
            f"inflation={macro.inflation_pct}%, year={macro.data_year}"
        )
        return macro

    def format_for_prompt(self, macro: "CountryMacro") -> str:
        """
        Convenience wrapper — delegates to CountryMacro.format_for_prompt().
        Returns "" if macro is None or empty.
        """
        if macro is None:
            return ""
        return macro.format_for_prompt()
