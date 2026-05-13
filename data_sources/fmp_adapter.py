"""
fmp_adapter.py — Tier 4: Financial Modeling Prep (FMP) API.

PAID fallback — only activated when free sources (Tiers 1-3) leave gaps.
Plans: https://site.financialmodelingprep.com/developer/docs
Starter plan ~$14/month gives 300 calls/day and global coverage.

FMP is the most comprehensive single-source API for global financial data:
- 30+ years of history for most companies
- All major exchanges: US, EU, Asia, LatAm
- Earnings call transcripts
- Insider transactions
- Analyst estimates
- Institutional holdings
- Much more

We use FMP conservatively — only for fields still missing after free tiers.
"""

from __future__ import annotations
import time
import logging
from datetime import datetime
from typing import Optional, Dict, List

import requests

from .base import CompanyData, AnnualFinancials, DataSourceResult
from config import FMP_API_KEY, REQUEST_HEADERS

logger = logging.getLogger(__name__)

FMP_BASE = "https://financialmodelingprep.com/stable"
FMP_DELAY = 0.5  # seconds between calls


def _f(val) -> Optional[float]:
    """Safe float extraction."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _to_m(val) -> Optional[float]:
    """Convert raw value to millions."""
    v = _f(val)
    return v / 1_000_000 if v is not None else None


class FMPAdapter:
    """
    Financial Modeling Prep adapter — comprehensive paid fallback.

    Provides:
    - Global financial statements (20+ years)
    - Key metrics & ratios (P/E, EV/EBIT, ROE, etc.)
    - Company profile & description
    - Earnings call transcripts
    - Insider transactions
    - Analyst estimates
    """

    SOURCE_NAME = "fmp"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or FMP_API_KEY
        if not self.api_key:
            logger.warning("[fmp] No API key configured. FMP adapter disabled.")

    def _is_available(self) -> bool:
        return bool(self.api_key)

    def _get(self, endpoint: str, params: dict = None) -> Optional[list | dict]:
        """Make a single FMP API call."""
        if not self._is_available():
            return None
        url = f"{FMP_BASE}/{endpoint}"
        p = {"apikey": self.api_key, **(params or {})}
        try:
            time.sleep(FMP_DELAY)
            resp = requests.get(url, params=p, headers=REQUEST_HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            # FMP returns an error dict when something goes wrong
            if isinstance(data, dict) and "Error Message" in data:
                logger.error(f"[fmp] API error: {data['Error Message']}")
                return None
            return data
        except requests.HTTPError as e:
            # 404 = ticker not covered by FMP (typically non-US). This is an
            # expected outcome on the free plan and shouldn't be flagged as
            # an error in the user's logs. Other HTTP errors stay loud.
            status = getattr(e.response, "status_code", None)
            if status == 404:
                logger.debug(f"[fmp] {endpoint} not covered by FMP (404)")
            else:
                logger.error(f"[fmp] Request failed for {endpoint}: {e}")
            return None
        except Exception as e:
            logger.error(f"[fmp] Request failed for {endpoint}: {e}")
            return None

    def fetch(self, ticker: str) -> DataSourceResult:
        """
        Fetch comprehensive data from FMP for any globally listed stock.
        Pulls: profile, income statements, balance sheets, cash flows, key metrics.
        """
        start = time.time()

        if not self._is_available():
            return DataSourceResult(
                success=False,
                source_name=self.SOURCE_NAME,
                error="FMP API key not configured. Add FMP_API_KEY to .env to enable paid fallback.",
                duration_seconds=time.time() - start,
            )

        # FMP uses ticker symbols without Yahoo suffixes for most exchanges,
        # but for some it uses exchange-prefixed format. Try raw first.
        fmp_ticker = ticker.upper().replace(".AS", "").replace(".ST", "").replace(
            ".L", "").replace(".DE", "").replace(".HE", "")

        logger.info(f"[fmp] Fetching {ticker} (as {fmp_ticker})…")

        # 1) Company profile
        profile_data = self._get(f"profile/{fmp_ticker}")
        if not profile_data or not isinstance(profile_data, list) or len(profile_data) == 0:
            # Try with original ticker (some EU tickers work with suffix)
            profile_data = self._get(f"profile/{ticker.upper()}")
            if not profile_data or not isinstance(profile_data, list) or len(profile_data) == 0:
                return DataSourceResult(
                    success=False,
                    source_name=self.SOURCE_NAME,
                    error=f"FMP: No profile found for '{ticker}' (tried '{fmp_ticker}'). "
                          f"Check ticker or FMP exchange support.",
                    duration_seconds=time.time() - start,
                )
            fmp_ticker = ticker.upper()  # use original if it worked

        profile = profile_data[0]
        fields_filled = []

        company = CompanyData(
            ticker=ticker,
            input_ticker=ticker,
            fetch_timestamp=datetime.utcnow().isoformat(),
            as_of_date=datetime.utcnow().strftime("%Y-%m-%d"),
            data_sources=[self.SOURCE_NAME],
        )

        # Profile fields
        company.name        = profile.get("companyName")
        company.exchange    = profile.get("exchangeShortName") or profile.get("exchange")
        company.currency    = profile.get("currency")
        company.currency_price = profile.get("currency")
        company.sector      = profile.get("sector")
        company.industry    = profile.get("industry")
        company.country     = profile.get("country")
        company.isin        = profile.get("isin")
        company.description = profile.get("description")
        company.website     = profile.get("website")
        company.employees   = int(profile["fullTimeEmployees"]) if profile.get("fullTimeEmployees") else None
        company.current_price = _f(profile.get("price"))
        company.market_cap  = _to_m(profile.get("mktCap"))
        company.beta        = _f(profile.get("beta"))

        for f in ["name", "sector", "industry", "country", "description",
                  "current_price", "market_cap"]:
            if getattr(company, f) is not None:
                fields_filled.append(f)

        # 2) Key metrics (ratios and multiples) — FMP provides these pre-calculated
        metrics_data = self._get(f"key-metrics/{fmp_ticker}", {"period": "annual", "limit": 1})
        if metrics_data and isinstance(metrics_data, list) and len(metrics_data) > 0:
            m = metrics_data[0]
            company.pe_ratio      = _f(m.get("peRatio"))
            company.price_to_book = _f(m.get("pbRatio"))
            company.ev_ebit       = _f(m.get("evToOperatingCashFlow"))  # FMP name
            company.ev_ebitda     = _f(m.get("enterpriseValueOverEBITDA"))
            company.ev_sales      = _f(m.get("evToSales"))
            company.roe           = _f(m.get("roe"))
            company.roa           = _f(m.get("roa"))
            company.roic          = _f(m.get("roic"))
            company.dividend_yield = _f(m.get("dividendYield"))
            company.net_margin    = _f(m.get("netProfitMargin"))
            company.gearing       = _f(m.get("netDebtToEBITDA"))
            company.shares_outstanding = _to_m(m.get("numberOfShares"))

            # EV/EBIT from FMP key-metrics
            ev_ebit_fmp = _f(m.get("evToEbit") or m.get("enterpriseValueMultiple"))
            if ev_ebit_fmp:
                company.ev_ebit = ev_ebit_fmp

            for f in ["pe_ratio", "ev_ebit", "ev_sales", "roe", "net_margin",
                      "dividend_yield", "gearing"]:
                if getattr(company, f) is not None:
                    fields_filled.append(f)

        # 3) Annual income statements (up to 20 years)
        income_data = self._get(f"income-statement/{fmp_ticker}",
                                {"period": "annual", "limit": 20})
        balance_data = self._get(f"balance-sheet-statement/{fmp_ticker}",
                                 {"period": "annual", "limit": 20})
        cashflow_data = self._get(f"cash-flow-statement/{fmp_ticker}",
                                  {"period": "annual", "limit": 20})

        # Index by fiscal year for merging
        balance_by_year  = {}
        cashflow_by_year = {}

        if balance_data:
            for r in balance_data:
                yr = _year_from_date(r.get("calendarYear") or r.get("date", ""))
                if yr:
                    balance_by_year[yr] = r

        if cashflow_data:
            for r in cashflow_data:
                yr = _year_from_date(r.get("calendarYear") or r.get("date", ""))
                if yr:
                    cashflow_by_year[yr] = r

        if income_data:
            fields_filled.append("annual_financials")
            for report in income_data:
                yr = _year_from_date(report.get("calendarYear") or report.get("date", ""))
                if not yr:
                    continue

                af = AnnualFinancials(year=yr)

                # Income statement
                af.revenue      = _to_m(report.get("revenue"))
                af.gross_profit = _to_m(report.get("grossProfit"))
                af.ebitda       = _to_m(report.get("ebitda"))
                af.ebit         = _to_m(report.get("operatingIncome") or report.get("ebit"))
                af.net_income   = _to_m(report.get("netIncome"))
                af.eps_diluted  = _f(report.get("epsdiluted") or report.get("eps"))
                af.dividends_per_share = _f(report.get("dividendsPerShare"))

                # Balance sheet
                b = balance_by_year.get(yr, {})
                if b:
                    af.total_assets = _to_m(b.get("totalAssets"))
                    af.total_equity = _to_m(b.get("totalStockholdersEquity")
                                            or b.get("totalEquity"))
                    af.total_debt   = _to_m(b.get("totalDebt") or b.get("longTermDebt"))
                    af.cash         = _to_m(b.get("cashAndCashEquivalents")
                                            or b.get("cashAndShortTermInvestments"))
                    af.shares_outstanding = _to_m(b.get("commonStock"))

                # Cash flow
                cf = cashflow_by_year.get(yr, {})
                if cf:
                    af.operating_cash_flow = _to_m(cf.get("operatingCashFlow"))
                    capex_raw = _f(cf.get("capitalExpenditure"))
                    if capex_raw is not None:
                        af.capex = abs(_to_m(capex_raw) or 0)

                af.calculate_derived()
                company.annual_financials[yr] = af

        # Final derived calculations
        company.calculate_current_ratios()

        logger.info(
            f"[fmp] {ticker} done. "
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

    def fetch_earnings_transcripts(self, ticker: str, limit: int = 4) -> List[dict]:
        """
        Fetch earnings call transcripts. FMP provides these on paid plans.
        Returns list of {date, quarter, year, content} dicts.
        """
        if not self._is_available():
            return []
        fmp_ticker = ticker.upper().split(".")[0]
        data = self._get(f"earning_call_transcript/{fmp_ticker}", {"limit": limit})
        if not data or not isinstance(data, list):
            return []
        return [
            {
                "date":    r.get("date"),
                "quarter": r.get("quarter"),
                "year":    r.get("year"),
                "content": r.get("content", ""),
            }
            for r in data
        ]

    def fetch_insider_transactions(self, ticker: str, limit: int = 50) -> List[dict]:
        """
        Fetch recent insider transactions (buys/sells by executives and directors).
        Returns list of transaction dicts.
        """
        if not self._is_available():
            return []
        fmp_ticker = ticker.upper().split(".")[0]
        data = self._get("insider-trading", {"symbol": fmp_ticker, "limit": limit})
        if not data or not isinstance(data, list):
            return []
        return data

    def fetch_analyst_estimates(self, ticker: str) -> dict:
        """
        Fetch forward analyst estimates (revenue, EPS for next 1-2 years).
        Returns dict with estimate fields.
        """
        if not self._is_available():
            return {}
        fmp_ticker = ticker.upper().split(".")[0]
        data = self._get(f"analyst-estimates/{fmp_ticker}", {"period": "annual", "limit": 2})
        if not data or not isinstance(data, list) or len(data) == 0:
            return {}
        # Return the next year estimate (first entry, most recent future date)
        next_est = data[0]
        return {
            "year":              next_est.get("date", "")[:4],
            "revenue_estimate":  _to_m(next_est.get("estimatedRevenueAvg")),
            "eps_estimate":      _f(next_est.get("estimatedEpsAvg")),
            "net_income_estimate": _to_m(next_est.get("estimatedNetIncomeAvg")),
            "ebitda_estimate":   _to_m(next_est.get("estimatedEbitdaAvg")),
        }


def _year_from_date(val) -> Optional[int]:
    """Extract 4-digit year from a date string or int."""
    if val is None:
        return None
    try:
        return int(str(val)[:4])
    except (ValueError, TypeError):
        return None


# ── Quick sanity test ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO)

    key = os.getenv("FMP_API_KEY", "")
    if not key:
        print("Set FMP_API_KEY env variable to test this adapter.")
    else:
        adapter = FMPAdapter(api_key=key)
        for ticker, label in [("AAPL", "Apple"), ("WKL", "Wolters Kluwer"), ("NOKIA", "Nokia")]:
            print(f"\n{'='*60}")
            print(f"  {label} ({ticker})")
            result = adapter.fetch(ticker)
            if result.success:
                c = result.data
                print(f"  {c.summary()}")
                for yr in c.sorted_years()[:3]:
                    af = c.annual_financials[yr]
                    print(f"  {yr}: Rev={af.revenue}M EBIT={af.ebit}M NI={af.net_income}M")
            else:
                print(f"  FAILED: {result.error}")
