"""
eodhd_macro.py — Country-level macroeconomic snapshot from EODHD.

Uses EODHD's `/macro-indicator/{country}` endpoint to pull the most
relevant macro time series for a country and project the latest value
of each into a compact, LLM-friendly text block.

This replaces the FRED-based US macro block previously injected into
Fisher / Gravity prompts. FRED only covers the United States — using
EODHD instead means a European or Asian company gets local-economy
context (GDP growth, inflation, unemployment, etc.) instead of US data.

Country code mapping:
  EODHD uses ISO 3166-1 alpha-3 country codes (DEU, USA, GBR, FRA, …).
  We map CompanyData.country (full English name) → alpha-3 here.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Optional, Any

import requests

from config import EODHD_API_KEY, REQUEST_HEADERS

logger = logging.getLogger(__name__)

EODHD_BASE = "https://eodhistoricaldata.com/api"
_DELAY = 0.2

# Indicators we surface in the LLM prompt — ordered by usefulness.
# (eodhd_code, human_label, format)
_INDICATORS: list[tuple[str, str, str]] = [
    ("gdp_growth_annual",                "GDP growth (real)",          "%"),
    ("inflation_consumer_prices_annual", "CPI inflation",              "%"),
    ("unemployment_total_percent",       "Unemployment rate",          "%"),
    ("real_interest_rate",               "Real interest rate",         "%"),
    ("gdp_current_usd",                  "GDP (USD)",                  "money_usd"),
    ("gdp_per_capita_usd",               "GDP per capita (USD)",       "money_usd"),
    ("debt_percent_gdp",                 "Public debt / GDP",          "%"),
    ("exports_of_goods_services_percent_gdp", "Exports / GDP",          "%"),
    ("population_total",                 "Population",                 "people"),
]

# Country name → ISO alpha-3 mapping (most common ones EODHD covers).
# Falls back to a 3-letter slice if a country isn't mapped (works for
# many cases by coincidence; the worst-case is the endpoint returns
# nothing and we silently skip the macro block).
_COUNTRY_ALPHA3: dict[str, str] = {
    "United States":         "USA",
    "USA":                   "USA",
    "United Kingdom":        "GBR",
    "UK":                    "GBR",
    "Germany":               "DEU",
    "France":                "FRA",
    "Italy":                 "ITA",
    "Spain":                 "ESP",
    "Netherlands":           "NLD",
    "Belgium":               "BEL",
    "Switzerland":           "CHE",
    "Sweden":                "SWE",
    "Norway":                "NOR",
    "Denmark":               "DNK",
    "Finland":               "FIN",
    "Poland":                "POL",
    "Czech Republic":        "CZE",
    "Hungary":               "HUN",
    "Austria":               "AUT",
    "Portugal":              "PRT",
    "Ireland":               "IRL",
    "Greece":                "GRC",
    "Turkey":                "TUR",
    "Russia":                "RUS",
    "Israel":                "ISR",
    "Japan":                 "JPN",
    "China":                 "CHN",
    "Hong Kong":             "HKG",
    "Taiwan":                "TWN",
    "South Korea":           "KOR",
    "Korea, Republic of":    "KOR",
    "Singapore":             "SGP",
    "India":                 "IND",
    "Indonesia":             "IDN",
    "Malaysia":              "MYS",
    "Thailand":              "THA",
    "Vietnam":               "VNM",
    "Philippines":           "PHL",
    "Australia":             "AUS",
    "New Zealand":           "NZL",
    "Canada":                "CAN",
    "Mexico":                "MEX",
    "Brazil":                "BRA",
    "Argentina":             "ARG",
    "Chile":                 "CHL",
    "South Africa":          "ZAF",
    "Saudi Arabia":          "SAU",
    "United Arab Emirates":  "ARE",
    "Egypt":                 "EGY",
}


def country_to_alpha3(country: Optional[str]) -> Optional[str]:
    """Map a country display name to its ISO alpha-3 code."""
    if not country:
        return None
    return _COUNTRY_ALPHA3.get(country.strip())


# ── Low-level GET ─────────────────────────────────────────────────────────────

def _eodhd_get(path: str, params: dict | None = None,
               timeout: int = 30) -> Optional[Any]:
    if not EODHD_API_KEY:
        return None
    p = {"api_token": EODHD_API_KEY, "fmt": "json"}
    if params:
        p.update(params)
    try:
        time.sleep(_DELAY)
        url = f"{EODHD_BASE}{path}"
        r = requests.get(url, params=p, headers=REQUEST_HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        logger.warning(f"[eodhd-macro] {path} HTTP {r.status_code}")
        return None
    except Exception as e:
        logger.warning(f"[eodhd-macro] {path} failed: {e}")
        return None


# ── Single-indicator fetch ────────────────────────────────────────────────────

def _fetch_indicator(alpha3: str, indicator: str) -> Optional[list]:
    """Fetch a single macro indicator time series for a country."""
    result = _eodhd_get(
        f"/macro-indicator/{alpha3}",
        params={"indicator": indicator},
    )
    if isinstance(result, list):
        return result
    return None


def _latest_point(series: list | None) -> tuple[Optional[str], Optional[float]]:
    """Return (date, value) of the most recent non-null point."""
    if not isinstance(series, list) or not series:
        return None, None
    # Series is usually sorted ascending; pick the last non-null Value.
    for entry in reversed(series):
        if not isinstance(entry, dict):
            continue
        val = entry.get("Value")
        if val is None:
            continue
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        return str(entry.get("Date") or ""), v
    return None, None


# ── Formatting ────────────────────────────────────────────────────────────────

def _fmt_value(val: Optional[float], fmt: str) -> str:
    if val is None:
        return "n/a"
    try:
        v = float(val)
    except Exception:
        return "n/a"
    if fmt == "%":
        return f"{v:.2f}%"
    if fmt == "money_usd":
        if abs(v) >= 1e12: return f"${v/1e12:.2f}T"
        if abs(v) >= 1e9:  return f"${v/1e9:.2f}B"
        if abs(v) >= 1e6:  return f"${v/1e6:.2f}M"
        return f"${v:,.0f}"
    if fmt == "people":
        if abs(v) >= 1e9:  return f"{v/1e9:.2f}B"
        if abs(v) >= 1e6:  return f"{v/1e6:.1f}M"
        if abs(v) >= 1e3:  return f"{v/1e3:.0f}K"
        return f"{v:,.0f}"
    return f"{v:.2f}"


def fetch_country_macro_block(country: Optional[str]) -> str:
    """
    Build a one-block macro snapshot string for the given country.
    Returns "" if the country can't be mapped or no data comes back.
    """
    alpha3 = country_to_alpha3(country)
    if not alpha3:
        if country:
            logger.info(f"[eodhd-macro] No alpha-3 mapping for '{country}' — skipping macro.")
        return ""

    rows: list[str] = []
    rows.append(f"=== MACRO CONTEXT (EODHD, country = {country} / {alpha3}) ===")

    fetched_any = False
    for code, label, fmt in _INDICATORS:
        series = _fetch_indicator(alpha3, code)
        date, val = _latest_point(series)
        if val is None:
            continue
        fetched_any = True
        date_short = (date or "")[:7] if date else ""   # YYYY-MM
        rows.append(f"  {label:<28} {_fmt_value(val, fmt):>12}   ({date_short})")

    if not fetched_any:
        return ""
    return "\n".join(rows)
