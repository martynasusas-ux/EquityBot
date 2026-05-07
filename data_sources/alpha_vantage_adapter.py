"""
alpha_vantage_adapter.py — Tier 2: Alpha Vantage API.

Free with registration (25 calls/day, 500/month on free plan).
Register at: https://www.alphavantage.co/support/#api-key

Covers: Global stocks (US, EU, Asia). Provides up to 20 years of annual data.
Key advantage: fills the 10-year historical gap for non-US companies where
EDGAR doesn't apply and yfinance only returns ~4 years.

Rate limit awareness: 25 calls/day on free plan. We use them conservatively —
only call AV when yfinance + EDGAR leave gaps.
"""

from __future__ import annotations
import time
import logging
from datetime import datetime
from typing import Optional, Dict

import requests

from .base import CompanyData, AnnualFinancials, DataSourceResult
from config import ALPHA_VANTAGE_API_KEY, REQUEST_HEADERS, CACHE_DIR

logger = logging.getLogger(__name__)

AV_BASE = "https://www.alphavantage.co/query"
AV_DELAY = 1.5  # seconds between calls (free tier: 5 calls/minute max)


def _safe_float(val) -> Optional[float]:
    """Parse a value that might be 'None', '', or a number string."""
    if val is None or val in ("None", "none", "-", ""):
        return None
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return None


class AlphaVantageAdapter:
    """
    Fetches annual financial statements from Alpha Vantage.
    Each company requires 3 API calls: income statement, balance sheet, cash flow.
    On the free plan (25 calls/day), limit to ~8 companies per day.

    Data is in raw units — we convert to millions.
    """

    SOURCE_NAME = "alpha_vantage"

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or ALPHA_VANTAGE_API_KEY
        if not self.api_key:
            logger.warning("[alpha_vantage] No API key configured. Adapter disabled.")

    def _is_available(self) -> bool:
        return bool(self.api_key)

    def _call(self, function: str, symbol: str) -> Optional[dict]:
        """Make one Alpha Vantage API call."""
        if not self._is_available():
            return None

        # AV uses simple US-style tickers — strip exchange suffixes
        av_symbol = symbol.upper().split(".")[0].replace("-", ".")

        params = {
            "function": function,
            "symbol": av_symbol,
            "apikey": self.api_key,
        }
        try:
            time.sleep(AV_DELAY)
            resp = requests.get(AV_BASE, params=params, headers=REQUEST_HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            # Check for rate limit or error messages
            if "Note" in data:
                logger.warning(f"[alpha_vantage] Rate limit hit: {data['Note']}")
                return None
            if "Information" in data:
                logger.warning(f"[alpha_vantage] API info: {data['Information']}")
                return None
            if "Error Message" in data:
                logger.error(f"[alpha_vantage] API error: {data['Error Message']}")
                return None

            return data

        except Exception as e:
            logger.error(f"[alpha_vantage] Request failed for {function}/{symbol}: {e}")
            return None

    def fetch(self, ticker: str) -> DataSourceResult:
        """
        Fetch up to 20 years of annual financial data for any global stock.
        Costs 3 API calls (income stmt + balance sheet + cash flow).
        """
        start = time.time()

        if not self._is_available():
            return DataSourceResult(
                success=False,
                source_name=self.SOURCE_NAME,
                error="Alpha Vantage API key not configured. "
                      "Register free at alphavantage.co to enable Tier 2.",
                duration_seconds=time.time() - start,
            )

        logger.info(f"[alpha_vantage] Fetching {ticker} (3 API calls)…")

        # Fetch all three statements
        income_data  = self._call("INCOME_STATEMENT", ticker)
        time.sleep(AV_DELAY)
        balance_data = self._call("BALANCE_SHEET", ticker)
        time.sleep(AV_DELAY)
        cashflow_data = self._call("CASH_FLOW", ticker)

        # Check we got something useful
        if not income_data or "annualReports" not in income_data:
            return DataSourceResult(
                success=False,
                source_name=self.SOURCE_NAME,
                error=f"No annual income statement data from Alpha Vantage for '{ticker}'. "
                      f"Ticker may not be supported or daily limit reached.",
                duration_seconds=time.time() - start,
            )

        # Parse annual reports — AV returns most recent first
        income_annual  = income_data.get("annualReports", [])
        balance_annual = balance_data.get("annualReports", []) if balance_data else []
        cashflow_annual = cashflow_data.get("annualReports", []) if cashflow_data else []

        # Index balance and cashflow by fiscal year end date for easy lookup
        balance_by_date  = {r["fiscalDateEnding"]: r for r in balance_annual}
        cashflow_by_date = {r["fiscalDateEnding"]: r for r in cashflow_annual}

        company = CompanyData(
            ticker=ticker,
            input_ticker=ticker,
            currency=income_annual[0].get("reportedCurrency") if income_annual else None,
            fetch_timestamp=datetime.utcnow().isoformat(),
            as_of_date=datetime.utcnow().strftime("%Y-%m-%d"),
            data_sources=[self.SOURCE_NAME],
        )

        fields_filled = ["annual_financials"]

        for report in income_annual:
            date_str = report.get("fiscalDateEnding", "")
            try:
                year = int(date_str[:4])
            except Exception:
                continue

            af = company.annual_financials.get(year, AnnualFinancials(year=year))

            # Income Statement
            af.revenue      = _to_millions(_safe_float(report.get("totalRevenue")))
            af.gross_profit = _to_millions(_safe_float(report.get("grossProfit")))
            af.ebit         = _to_millions(_safe_float(report.get("operatingIncome") or
                                                        report.get("ebit")))
            af.ebitda       = _to_millions(_safe_float(report.get("ebitda")))
            af.net_income   = _to_millions(_safe_float(report.get("netIncome")))
            af.eps_diluted  = _safe_float(report.get("dilutedEPS"))  # per-share, no scaling

            # Balance Sheet (match by fiscal date)
            b = balance_by_date.get(date_str, {})
            if b:
                af.total_assets = _to_millions(_safe_float(b.get("totalAssets")))
                af.total_equity = _to_millions(_safe_float(
                    b.get("totalShareholderEquity") or b.get("totalStockholdersEquity")
                ))
                # Debt: try several fields
                debt = (
                    _safe_float(b.get("shortLongTermDebtTotal"))
                    or _safe_float(b.get("longTermDebt"))
                )
                af.total_debt = _to_millions(debt)
                af.cash = _to_millions(
                    _safe_float(b.get("cashAndShortTermInvestments"))
                    or _safe_float(b.get("cashAndCashEquivalentsAtCarryingValue"))
                )
                af.shares_outstanding = _to_millions(
                    _safe_float(b.get("commonStockSharesOutstanding"))
                )

            # Cash Flow (match by fiscal date)
            cf = cashflow_by_date.get(date_str, {})
            if cf:
                af.operating_cash_flow = _to_millions(
                    _safe_float(cf.get("operatingCashflow"))
                )
                capex_raw = _safe_float(cf.get("capitalExpenditures"))
                if capex_raw is not None:
                    af.capex = abs(_to_millions(capex_raw) or 0)

                div_raw = _safe_float(cf.get("dividendPayout"))
                # dividendPayout is total, not per-share — skip for now
                # (per-share comes from income stmt if available)

            af.calculate_derived()
            company.annual_financials[year] = af

        if not company.annual_financials:
            return DataSourceResult(
                success=False,
                source_name=self.SOURCE_NAME,
                error=f"Parsed 0 annual records from Alpha Vantage for {ticker}.",
                duration_seconds=time.time() - start,
            )

        logger.info(
            f"[alpha_vantage] {ticker} done. "
            f"Years: {company.year_range()}, "
            f"Records: {len(company.annual_financials)}"
        )

        return DataSourceResult(
            success=True,
            source_name=self.SOURCE_NAME,
            data=company,
            fields_filled=fields_filled,
            duration_seconds=time.time() - start,
        )


def _to_millions(val: Optional[float]) -> Optional[float]:
    """Convert raw unit value to millions."""
    if val is None:
        return None
    return val / 1_000_000


# ── Quick sanity test ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO)

    key = os.getenv("ALPHA_VANTAGE_API_KEY", "demo")
    adapter = AlphaVantageAdapter(api_key=key)

    for ticker, label in [("AAPL", "Apple"), ("NOKIA", "Nokia"), ("IBM", "IBM")]:
        print(f"\n{'='*60}")
        print(f"  {label} ({ticker})")
        result = adapter.fetch(ticker)
        if result.success:
            c = result.data
            print(f"  Years: {c.year_range()}, Records: {len(c.annual_financials)}")
            for yr in c.sorted_years()[:4]:
                af = c.annual_financials[yr]
                print(f"  {yr}: Rev={af.revenue}M EBIT={af.ebit}M NI={af.net_income}M EPS={af.eps_diluted}")
        else:
            print(f"  FAILED: {result.error}")
