"""
eodhd_adapter.py — Tier 1b: EODHD (End of Day Historical Data) API.

High-quality annual financial data for European and global exchanges.
Requires a paid plan for fundamentals access.
Plans: https://eodhistoricaldata.com/pricing

EODHD provides:
- 30+ years of annual fundamentals for 70,000+ companies worldwide
- All major EU exchanges: XETRA, LSE, Euronext, Helsinki, Stockholm, Oslo, etc.
- Asia, LatAm, Middle East coverage
- Balance sheet, income statement, cash flow in one endpoint
- Real-time and delayed price quotes

Usage in waterfall:
- Runs for non-US tickers after yfinance (Tier 1a)
- Overrides yfinance annual financials with EODHD's more accurate data
- Silently skips if free tier (returns 403) or key missing
"""

from __future__ import annotations
import time
import logging
from datetime import datetime
from typing import Optional

import requests

from .base import CompanyData, AnnualFinancials, DataSourceResult
from config import EODHD_API_KEY, REQUEST_HEADERS

logger = logging.getLogger(__name__)

EODHD_BASE = "https://eodhistoricaldata.com/api"
EODHD_DELAY = 0.5  # seconds between calls

# Yahoo Finance exchange suffix → EODHD exchange suffix
_YF_TO_EODHD = {
    ".DE": ".XETRA",   # Germany (Xetra)
    ".F":  ".F",       # Frankfurt (alternative)
    ".L":  ".LSE",     # London
    ".PA": ".PA",      # Paris (Euronext)
    ".AS": ".AS",      # Amsterdam (Euronext)
    ".BR": ".BR",      # Brussels
    ".MI": ".MI",      # Milan
    ".MC": ".MC",      # Madrid
    ".HE": ".HE",      # Helsinki
    ".ST": ".ST",      # Stockholm
    ".OL": ".OL",      # Oslo
    ".CO": ".CO",      # Copenhagen
    ".VX": ".VX",      # Swiss Exchange
    ".VI": ".VI",      # Vienna
    ".WA": ".WA",      # Warsaw
    ".LS": ".LS",      # Lisbon
    ".AT": ".AT",      # Athens
    ".KS": ".KS",      # South Korea
    ".T":  ".TSE",     # Tokyo
    ".HK": ".HK",      # Hong Kong
    ".AX": ".AU",      # Australia (ASX)
}


class EODHDAdapter:
    """
    EODHD fundamentals adapter — accurate annual financial data for global stocks.

    Data returned (in millions where monetary):
    - Company profile: name, exchange, currency, sector, industry, country, ISIN
    - Current highlights: market cap, P/E, EPS, dividend yield, ROE, profit margin
    - Annual history: up to 20+ years of income stmt, balance sheet, cash flow
    """

    SOURCE_NAME = "eodhd"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or EODHD_API_KEY
        if not self.api_key:
            logger.warning("[eodhd] No API key configured. EODHD adapter disabled.")

    def _is_available(self) -> bool:
        return bool(self.api_key)

    def _convert_ticker(self, yf_ticker: str) -> str:
        """
        Convert Yahoo Finance ticker format to EODHD format.

        Examples:
            RHM.DE   → RHM.XETRA
            NOKIA.HE → NOKIA.HE
            AAPL     → AAPL.US
            BA.L     → BA.LSE
            SAP.DE   → SAP.XETRA
        """
        dot_pos = yf_ticker.rfind(".")
        if dot_pos == -1:
            # No suffix — US stock
            return f"{yf_ticker}.US"

        suffix = yf_ticker[dot_pos:]          # e.g. ".DE"
        base   = yf_ticker[:dot_pos]          # e.g. "RHM"

        eodhd_suffix = _YF_TO_EODHD.get(suffix)
        if eodhd_suffix:
            return f"{base}{eodhd_suffix}"
        # Suffix not in mapping — might already be EODHD format, keep as-is
        return yf_ticker

    def fetch(self, ticker: str) -> DataSourceResult:
        """
        Fetch company fundamentals from EODHD.

        Converts the Yahoo Finance ticker to EODHD format, calls the
        /fundamentals endpoint, then maps the JSON to CompanyData.
        Returns DataSourceResult with success=False on any error.
        """
        start = time.time()

        if not self._is_available():
            return DataSourceResult(
                success=False,
                source_name=self.SOURCE_NAME,
                error="EODHD API key not configured. Add EODHD_API_KEY to .env.",
                duration_seconds=time.time() - start,
            )

        eodhd_ticker = self._convert_ticker(ticker)
        logger.info(f"[eodhd] Fetching {ticker} (as {eodhd_ticker})…")

        try:
            time.sleep(EODHD_DELAY)
            url    = f"{EODHD_BASE}/fundamentals/{eodhd_ticker}"
            params = {"api_token": self.api_key, "fmt": "json"}
            resp   = requests.get(url, params=params, headers=REQUEST_HEADERS, timeout=30)

            # Free-tier accounts get HTTP 403 on /fundamentals
            if resp.status_code == 403:
                return DataSourceResult(
                    success=False,
                    source_name=self.SOURCE_NAME,
                    error=(
                        "EODHD fundamentals require a paid plan. "
                        "Visit eodhistoricaldata.com to upgrade."
                    ),
                    duration_seconds=time.time() - start,
                )

            resp.raise_for_status()
            raw = resp.json()

        except requests.RequestException as e:
            logger.error(f"[eodhd] Request failed for {eodhd_ticker}: {e}")
            return DataSourceResult(
                success=False,
                source_name=self.SOURCE_NAME,
                error=str(e),
                duration_seconds=time.time() - start,
            )

        # Validate we got real data (EODHD returns {} for unknown tickers)
        if not raw or not isinstance(raw, dict):
            return DataSourceResult(
                success=False,
                source_name=self.SOURCE_NAME,
                error=f"EODHD returned empty response for '{eodhd_ticker}'. Check ticker format.",
                duration_seconds=time.time() - start,
            )

        general = raw.get("General") or {}
        if not general.get("Name"):
            return DataSourceResult(
                success=False,
                source_name=self.SOURCE_NAME,
                error=f"EODHD: No company found for '{eodhd_ticker}'.",
                duration_seconds=time.time() - start,
            )

        fields_filled: list[str] = []

        company = CompanyData(
            ticker=ticker,
            input_ticker=ticker,
            fetch_timestamp=datetime.utcnow().isoformat(),
            as_of_date=datetime.utcnow().strftime("%Y-%m-%d"),
            data_sources=[self.SOURCE_NAME],
        )

        # ── General / Identity ────────────────────────────────────────────────
        company.name        = general.get("Name")
        company.exchange    = general.get("Exchange")
        company.currency    = general.get("CurrencyCode")
        company.currency_price = general.get("CurrencyCode")
        company.sector      = general.get("Sector") or None
        company.industry    = general.get("Industry") or None
        company.country     = general.get("CountryName") or general.get("Country")
        company.isin        = general.get("ISIN") or None
        company.description = general.get("Description") or None
        company.website     = general.get("WebURL") or None

        emp_raw = general.get("FullTimeEmployees")
        if emp_raw:
            try:
                company.employees = int(str(emp_raw).replace(",", ""))
            except (ValueError, TypeError):
                pass

        for f in ["name", "sector", "industry", "country", "description", "isin"]:
            if getattr(company, f):
                fields_filled.append(f)

        # ── Highlights (current-period market & ratio data) ───────────────────
        highlights = raw.get("Highlights") or {}

        company.market_cap = self._to_m(highlights.get("MarketCapitalization"))
        company.pe_ratio   = self._parse_float(highlights.get("PERatio"))
        company.roe        = self._parse_float(highlights.get("ReturnOnEquityTTM"))
        company.net_margin = self._parse_float(highlights.get("ProfitMargin"))

        # Dividend yield: EODHD returns as a decimal already (0.02 = 2%)
        company.dividend_yield = self._parse_float(highlights.get("DividendYield"))

        for f in ["market_cap", "pe_ratio", "roe", "net_margin", "dividend_yield"]:
            if getattr(company, f) is not None:
                fields_filled.append(f)

        # ── Technicals ────────────────────────────────────────────────────────
        technicals = raw.get("Technicals") or {}
        company.beta = self._parse_float(technicals.get("Beta"))

        # ── Shares outstanding ────────────────────────────────────────────────
        shares_stats = raw.get("SharesStats") or {}
        shares_raw = shares_stats.get("SharesOutstanding")
        if shares_raw is not None:
            company.shares_outstanding = self._to_m(shares_raw)

        # ── Annual Financial History ──────────────────────────────────────────
        try:
            financials_block = raw.get("Financials") or {}
            self._parse_annual_history(company, financials_block, fields_filled)
        except Exception as e:
            logger.warning(f"[eodhd] Could not parse annual financials for {ticker}: {e}")

        # ── Derived Calculations ──────────────────────────────────────────────
        company.calculate_current_ratios()

        logger.info(
            f"[eodhd] {ticker} done. "
            f"Fields: {len(fields_filled)}, "
            f"Years: {company.year_range()}, "
            f"Completeness: {company.completeness_pct()}%"
        )

        return DataSourceResult(
            success=True,
            source_name=self.SOURCE_NAME,
            data=company,
            fields_filled=fields_filled,
            duration_seconds=time.time() - start,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Annual history parser
    # ──────────────────────────────────────────────────────────────────────────

    def _parse_annual_history(
        self,
        company: CompanyData,
        financials_block: dict,
        fields_filled: list,
    ) -> None:
        """
        Parse EODHD Financials block into AnnualFinancials objects.

        EODHD structure:
            Financials.Income_Statement.annual  → dict keyed by "YYYY-MM-DD"
            Financials.Balance_Sheet.annual     → dict keyed by "YYYY-MM-DD"
            Financials.Cash_Flow.annual         → dict keyed by "YYYY-MM-DD"

        All monetary values arrive as strings (or None); raw units (not millions).
        We convert to millions via _to_m().
        """
        income_annual   = (financials_block.get("Income_Statement")  or {}).get("annual") or {}
        balance_annual  = (financials_block.get("Balance_Sheet")     or {}).get("annual") or {}
        cashflow_annual = (financials_block.get("Cash_Flow")         or {}).get("annual") or {}

        if not income_annual:
            logger.debug(f"[eodhd] No annual income statement data available.")
            return

        # Index balance sheet and cash flow by year for O(1) lookup
        balance_by_year:  dict[int, dict] = {}
        cashflow_by_year: dict[int, dict] = {}

        for date_str, row in balance_annual.items():
            yr = _year_from_date(date_str)
            if yr:
                balance_by_year[yr] = row

        for date_str, row in cashflow_annual.items():
            yr = _year_from_date(date_str)
            if yr:
                cashflow_by_year[yr] = row

        for date_str, inc in income_annual.items():
            yr = _year_from_date(date_str)
            if not yr:
                continue

            af = company.annual_financials.get(yr, AnnualFinancials(year=yr))

            # ── Income Statement ──────────────────────────────────────────────
            af.revenue      = self._to_m(inc.get("totalRevenue"))
            af.gross_profit = self._to_m(inc.get("grossProfit"))
            af.ebit         = self._to_m(inc.get("ebit"))
            af.ebitda       = self._to_m(inc.get("ebitda"))
            af.net_income   = self._to_m(inc.get("netIncome"))

            # EPS is per-share — parse_float only (no /1M conversion)
            af.eps_diluted  = self._parse_float(inc.get("eps") or inc.get("epsDiluted"))

            # ── Balance Sheet ─────────────────────────────────────────────────
            b = balance_by_year.get(yr, {})
            if b:
                af.total_assets = self._to_m(b.get("totalAssets"))
                af.total_equity = self._to_m(
                    b.get("totalStockholderEquity")
                    or b.get("totalEquity")
                )
                # Debt: prefer combined short+long, fall back to long-term only
                short_debt = self._to_m(b.get("shortLongTermDebt"))
                long_debt  = self._to_m(b.get("longTermDebt"))
                if short_debt is not None:
                    af.total_debt = short_debt
                elif long_debt is not None:
                    af.total_debt = long_debt

                af.cash = self._to_m(
                    b.get("cashAndCashEquivalentsAtCarryingValue")
                    or b.get("cash")
                )

                # Shares outstanding (raw count → millions)
                shares_raw = b.get("commonStock")
                if shares_raw is not None:
                    shares_val = self._parse_float(shares_raw)
                    if shares_val is not None:
                        af.shares_outstanding = shares_val / 1_000_000

            # ── Cash Flow ─────────────────────────────────────────────────────
            cf = cashflow_by_year.get(yr, {})
            if cf:
                af.operating_cash_flow = self._to_m(
                    cf.get("totalCashFromOperatingActivities")
                )
                # FCF: use reported value if available
                fcf_raw = cf.get("freeCashFlow")
                if fcf_raw is not None:
                    af.fcf = self._to_m(fcf_raw)

                # CapEx: EODHD typically reports as a negative number
                capex_raw = self._to_m(cf.get("capitalExpenditures"))
                if capex_raw is not None:
                    af.capex = abs(capex_raw)

            # Derive margins, net debt, ROE, etc.
            af.calculate_derived()
            company.annual_financials[yr] = af

        if company.annual_financials:
            fields_filled.append("annual_financials")
            logger.debug(
                f"[eodhd] Annual data years: {list(company.annual_financials.keys())}"
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _parse_float(self, val) -> Optional[float]:
        """Parse EODHD string values safely. Returns None for None/empty/invalid."""
        if val is None or val in ("", "None", "null"):
            return None
        try:
            return float(str(val).replace(",", ""))
        except (ValueError, TypeError):
            return None

    def _to_m(self, val) -> Optional[float]:
        """Convert raw (full-unit) value to millions."""
        v = self._parse_float(val)
        return v / 1_000_000 if v is not None else None


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _year_from_date(val) -> Optional[int]:
    """Extract 4-digit year from a date string like '2024-12-31'."""
    if not val:
        return None
    try:
        return int(str(val)[:4])
    except (ValueError, TypeError):
        return None


# ── Quick sanity test (run this file directly) ────────────────────────────────
if __name__ == "__main__":
    import os
    import sys
    logging.basicConfig(level=logging.INFO)

    # Ticker conversion tests
    adapter = EODHDAdapter(api_key="demo")
    test_conversions = [
        ("RHM.DE",   "RHM.XETRA"),
        ("NOKIA.HE", "NOKIA.HE"),
        ("AAPL",     "AAPL.US"),
        ("BA.L",     "BA.LSE"),
        ("SAP.DE",   "SAP.XETRA"),
    ]
    print("Ticker conversion tests:")
    all_ok = True
    for yf_ticker, expected in test_conversions:
        result = adapter._convert_ticker(yf_ticker)
        status = "OK" if result == expected else "FAIL"
        if status == "FAIL":
            all_ok = False
        print(f"  {status}  {yf_ticker:15s} → {result:15s}  (expected {expected})")
    print(f"\nAll conversions correct: {all_ok}")

    # Live API test (only if real key provided)
    key = os.getenv("EODHD_API_KEY", "")
    if not key or key == "demo":
        print("\nSet EODHD_API_KEY env variable to test live API calls.")
        sys.exit(0)

    adapter = EODHDAdapter(api_key=key)
    for ticker, label in [
        ("RHM.DE",   "Rheinmetall (XETRA)"),
        ("NOKIA.HE", "Nokia (Helsinki)"),
        ("BA.L",     "BAE Systems (LSE)"),
    ]:
        print(f"\n{'='*60}")
        print(f"  {label} ({ticker})")
        res = adapter.fetch(ticker)
        if res.success and res.data:
            c = res.data
            print(f"  {c.summary()}")
            for yr in c.sorted_years()[:3]:
                af = c.annual_financials[yr]
                print(f"  {yr}: Rev={af.revenue}M EBIT={af.ebit}M NI={af.net_income}M")
        else:
            print(f"  FAILED: {res.error}")
