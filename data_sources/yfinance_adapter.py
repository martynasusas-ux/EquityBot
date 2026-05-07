"""
yfinance_adapter.py — Tier 1 primary data source.

Covers: prices, market cap, financials, ratios for US + global exchanges.
Free, no API key. Returns ~4 years of annual data.
Global coverage: US, EU (AMS/STO/LSE/XETRA/HEL…), Asia, LatAm.
"""

from __future__ import annotations
import time
import logging
from datetime import datetime
from typing import Optional

import yfinance as yf
import pandas as pd

from .base import CompanyData, AnnualFinancials, ForwardEstimates, DataSourceResult

logger = logging.getLogger(__name__)


def _safe(val, cast=None, scale=1.0):
    """Return val (optionally cast and scaled), or None if missing/NaN."""
    if val is None:
        return None
    try:
        if isinstance(val, float) and pd.isna(val):
            return None
        v = cast(val) if cast else val
        return v * scale if scale != 1.0 else v
    except Exception:
        return None


def _df_val(df: pd.DataFrame, row_key: str, col_idx: int = 0) -> Optional[float]:
    """
    Safely pull a value from a yfinance financial DataFrame.
    Rows are metric names, columns are dates (most recent = col 0).
    Values from yfinance are in raw units (dollars, not millions).
    We convert to millions for consistency.
    """
    if df is None or df.empty:
        return None
    # yfinance sometimes uses slightly different key names — try a few
    candidates = [row_key, row_key.replace(" ", ""), row_key.title()]
    for key in candidates:
        if key in df.index:
            try:
                raw = df.loc[key].iloc[col_idx]
                if pd.isna(raw):
                    return None
                return float(raw) / 1_000_000  # convert to millions
            except Exception:
                return None
    return None


class YFinanceAdapter:
    """
    Fetches company data from Yahoo Finance via the yfinance library.

    Data returned (in millions where monetary):
    - Company profile: name, sector, industry, country, description
    - Current market: price, market cap, shares outstanding
    - Current ratios: P/E, EV/EBIT (calculated), EV/Sales, ROE, margins, etc.
    - Annual history: up to 4 years of income stmt, balance sheet, cash flow
    """

    SOURCE_NAME = "yfinance"

    def fetch(self, ticker: str) -> DataSourceResult:
        """
        Main entry point. ticker should be Yahoo Finance format
        (e.g. "WKL.AS", "ATCO-A.ST", "AAPL", "NOKIA.HE").
        """
        start = time.time()
        logger.info(f"[yfinance] Fetching {ticker}…")

        try:
            yt = yf.Ticker(ticker)
            info = yt.info or {}

            # Validate we got real data
            if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
                # Try to catch a completely empty response
                if not info.get("longName") and not info.get("shortName"):
                    return DataSourceResult(
                        success=False,
                        source_name=self.SOURCE_NAME,
                        error=f"No data returned for ticker '{ticker}'. Check ticker format.",
                        duration_seconds=time.time() - start,
                    )

            company = CompanyData(
                ticker=ticker,
                input_ticker=ticker,
                fetch_timestamp=datetime.utcnow().isoformat(),
            )

            fields_filled = []

            # ── Identity ─────────────────────────────────────────────────────
            company.name     = info.get("longName") or info.get("shortName")
            company.exchange = info.get("exchange") or info.get("exchangeShortName")
            company.currency = info.get("financialCurrency") or info.get("currency")
            company.currency_price = info.get("currency")
            company.sector   = info.get("sector")
            company.industry = info.get("industry")
            company.country  = info.get("country")
            company.website  = info.get("website")
            company.description = info.get("longBusinessSummary")
            company.employees = _safe(info.get("fullTimeEmployees"), int)

            for f in ["name", "sector", "industry", "country", "description"]:
                if getattr(company, f):
                    fields_filled.append(f)

            # ── Current Market Data ───────────────────────────────────────────
            price = (
                info.get("currentPrice")
                or info.get("regularMarketPrice")
                or info.get("previousClose")
            )
            company.current_price = _safe(price, float)

            # Market cap from info (in raw units → millions)
            mc_raw = info.get("marketCap")
            company.market_cap = _safe(mc_raw, float, 1 / 1_000_000)

            shares_raw = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
            company.shares_outstanding = _safe(shares_raw, float, 1 / 1_000_000)

            ev_raw = info.get("enterpriseValue")
            company.enterprise_value = _safe(ev_raw, float, 1 / 1_000_000)

            company.as_of_date = datetime.utcnow().strftime("%Y-%m-%d")

            for f in ["current_price", "market_cap", "shares_outstanding"]:
                if getattr(company, f) is not None:
                    fields_filled.append(f)

            # ── Current Valuation Multiples ───────────────────────────────────
            company.pe_ratio      = _safe(info.get("trailingPE"), float)
            company.forward_pe    = _safe(info.get("forwardPE"), float)
            company.price_to_book = _safe(info.get("priceToBook"), float)
            company.ev_ebitda     = _safe(info.get("enterpriseToEbitda"), float)
            company.ev_sales      = _safe(info.get("enterpriseToRevenue"), float)
            company.beta          = _safe(info.get("beta"), float)

            # Dividend yield: yfinance always returns this as a percentage-style
            # float (e.g. 0.38 means 0.38%, 1.23 means 1.23%), unlike margins/ROE
            # which are returned as true decimals (0.272 = 27.2%).
            # We normalise to decimal by dividing by 100 for consistent storage.
            dy = _safe(info.get("dividendYield"), float)
            if dy is not None:
                dy = dy / 100   # 0.38 → 0.0038 (0.38%), 1.23 → 0.0123 (1.23%)
            company.dividend_yield = dy

            for f in ["pe_ratio", "ev_ebitda", "ev_sales", "dividend_yield"]:
                if getattr(company, f) is not None:
                    fields_filled.append(f)

            # ── Current Profitability ─────────────────────────────────────────
            company.gross_margin  = _safe(info.get("grossMargins"), float)
            company.ebit_margin   = _safe(info.get("operatingMargins"), float)
            company.net_margin    = _safe(info.get("profitMargins"), float)
            company.roe           = _safe(info.get("returnOnEquity"), float)
            company.roa           = _safe(info.get("returnOnAssets"), float)

            for f in ["net_margin", "ebit_margin", "roe"]:
                if getattr(company, f) is not None:
                    fields_filled.append(f)

            # ── Current Balance Sheet Snapshot ────────────────────────────────
            total_debt_raw = info.get("totalDebt")
            total_cash_raw = info.get("totalCash")
            total_debt = _safe(total_debt_raw, float, 1 / 1_000_000)
            total_cash = _safe(total_cash_raw, float, 1 / 1_000_000)

            if total_debt is not None and total_cash is not None:
                company.net_debt = total_debt - total_cash
            elif total_debt is not None:
                company.net_debt = total_debt

            company.current_ratio  = _safe(info.get("currentRatio"), float)
            company.debt_to_equity = _safe(info.get("debtToEquity"), float)

            # ── Annual Financial History ──────────────────────────────────────
            try:
                financials = yt.financials          # Income statement
                balance    = yt.balance_sheet       # Balance sheet
                cashflow   = yt.cashflow            # Cash flows
                self._parse_annual_history(company, financials, balance, cashflow, fields_filled)
            except Exception as e:
                logger.warning(f"[yfinance] Could not fetch annual history for {ticker}: {e}")

            # ── Historical Dividends Per Share (sum payments by calendar year) ───
            try:
                divs = yt.dividends
                if divs is not None and not divs.empty:
                    # Group all dividend payments by calendar year and sum
                    dps_by_year: dict[int, float] = {}
                    for ts, amount in divs.items():
                        try:
                            yr = pd.Timestamp(ts).year
                            dps_by_year[yr] = dps_by_year.get(yr, 0.0) + float(amount)
                        except Exception:
                            pass
                    # Assign to matching AnnualFinancials records
                    for year, af in company.annual_financials.items():
                        if year in dps_by_year and af.dividends_per_share is None:
                            af.dividends_per_share = dps_by_year[year]
                    logger.debug(f"[yfinance] DPS by year: {dps_by_year}")
            except Exception as e:
                logger.warning(f"[yfinance] Could not fetch dividend history for {ticker}: {e}")

            # ── Historical Year-End Prices → per-year valuation ratios ──────────
            # Fetch monthly price history and assign Dec-31 (or last-of-year)
            # closing price to each AnnualFinancials record, then derive
            # historical P/E, EV/EBIT, EV/Sales, FCF Yield, Div Yield, Mkt Cap.
            try:
                hist = yt.history(period="max", interval="1mo")
                if hist is not None and not hist.empty:
                    # current shares in millions (from CompanyData, set above)
                    current_shares_m = company.shares_outstanding  # millions

                    for year, af in company.annual_financials.items():
                        year_prices = hist[hist.index.year == year]
                        if year_prices.empty:
                            continue
                        # last available monthly close for that calendar year
                        af.price_year_end = float(year_prices["Close"].iloc[-1])

                        # shares this year (stored as raw count by _parse_annual_history)
                        # → convert to millions for market-cap calculation
                        if af.shares_outstanding is not None and af.shares_outstanding > 0:
                            shares_m = af.shares_outstanding / 1_000_000
                        elif current_shares_m is not None and current_shares_m > 0:
                            shares_m = current_shares_m
                        else:
                            shares_m = None

                        if shares_m:
                            af.market_cap = af.price_year_end * shares_m  # → millions

                        # Enterprise Value = market cap + net debt
                        if af.market_cap is not None:
                            nd = af.net_debt  # already in millions
                            if nd is not None:
                                af.enterprise_value = af.market_cap + nd
                            else:
                                af.enterprise_value = af.market_cap  # rough if no debt data

                        # Derive P/E, EV/EBIT, EV/Sales, FCF Yield, Div Yield
                        af.calculate_derived()

                    fields_filled.append("historical_valuations")
                    logger.debug(f"[yfinance] Year-end prices fetched for {ticker}")
            except Exception as e:
                logger.warning(f"[yfinance] Could not compute historical valuations for {ticker}: {e}")

            # ── Analyst Forward Estimates ─────────────────────────────────────
            try:
                self._parse_forward_estimates(company, yt, info)
            except Exception as e:
                logger.warning(f"[yfinance] Could not fetch forward estimates for {ticker}: {e}")

            # ── Derived Calculations ──────────────────────────────────────────
            company.calculate_current_ratios()

            # EV/EBIT — yfinance doesn't provide this directly, we calculate it
            if company.ev_ebit is None and company.enterprise_value and company.latest_annual():
                la = company.latest_annual()
                if la and la.ebit and la.ebit > 0:
                    company.ev_ebit = company.enterprise_value / la.ebit
                    fields_filled.append("ev_ebit")

            # FCF Yield
            if company.fcf_yield is None and company.market_cap and company.latest_annual():
                la = company.latest_annual()
                if la and la.fcf and company.market_cap > 0:
                    company.fcf_yield = la.fcf / company.market_cap
                    fields_filled.append("fcf_yield")

            company.data_sources = [self.SOURCE_NAME]

            logger.info(
                f"[yfinance] {ticker} done. "
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

        except Exception as e:
            logger.error(f"[yfinance] Error fetching {ticker}: {e}", exc_info=True)
            return DataSourceResult(
                success=False,
                source_name=self.SOURCE_NAME,
                error=str(e),
                duration_seconds=time.time() - start,
            )

    def _parse_annual_history(
        self,
        company: CompanyData,
        financials: pd.DataFrame,
        balance: pd.DataFrame,
        cashflow: pd.DataFrame,
        fields_filled: list,
    ) -> None:
        """
        Parse yfinance DataFrames into AnnualFinancials objects.
        yfinance returns up to 4 years. Columns are datetime objects.
        Values are in raw units — we convert to millions.
        """
        if financials is None or financials.empty:
            logger.debug(f"[yfinance] No annual financials available.")
            return

        for col in financials.columns:
            try:
                year = pd.Timestamp(col).year
            except Exception:
                continue

            af = company.annual_financials.get(year, AnnualFinancials(year=year))

            # Income statement
            af.revenue     = _df_val(financials, "Total Revenue", financials.columns.get_loc(col))
            af.gross_profit= _df_val(financials, "Gross Profit", financials.columns.get_loc(col))
            af.ebit        = _df_val(financials, "Operating Income", financials.columns.get_loc(col))
            af.ebitda      = _df_val(financials, "EBITDA", financials.columns.get_loc(col))
            af.net_income  = _df_val(financials, "Net Income", financials.columns.get_loc(col))
            af.eps_diluted = _df_val(financials, "Diluted EPS",
                                     financials.columns.get_loc(col))

            # EPS is per-share, not in millions — correct the scale
            if af.eps_diluted is not None:
                af.eps_diluted = af.eps_diluted * 1_000_000  # undo /1M scaling

            # Balance sheet
            if balance is not None and not balance.empty and col in balance.columns:
                idx = balance.columns.get_loc(col)
                af.total_assets  = _df_val(balance, "Total Assets", idx)
                af.total_equity  = _df_val(balance, "Stockholders Equity", idx)
                # Try multiple key names for debt
                for debt_key in ["Total Debt", "Long Term Debt And Capital Lease Obligation",
                                 "Long Term Debt"]:
                    val = _df_val(balance, debt_key, idx)
                    if val is not None:
                        af.total_debt = val
                        break
                for cash_key in ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"]:
                    val = _df_val(balance, cash_key, idx)
                    if val is not None:
                        af.cash = val
                        break
                af.shares_outstanding = _df_val(balance, "Ordinary Shares Number", idx)
                if af.shares_outstanding is not None:
                    af.shares_outstanding = af.shares_outstanding * 1_000_000  # undo /1M

            # Cash flow
            if cashflow is not None and not cashflow.empty and col in cashflow.columns:
                idx = cashflow.columns.get_loc(col)
                af.operating_cash_flow = _df_val(cashflow, "Operating Cash Flow", idx)
                af.fcf  = _df_val(cashflow, "Free Cash Flow", idx)
                capex = _df_val(cashflow, "Capital Expenditure", idx)
                if capex is not None:
                    af.capex = abs(capex)  # yfinance reports as negative

            # Derive what we can
            af.calculate_derived()
            company.annual_financials[year] = af

        if company.annual_financials:
            fields_filled.append("annual_financials")
            logger.debug(f"[yfinance] Annual data years: {list(company.annual_financials.keys())}")

    def _parse_forward_estimates(
        self,
        company: CompanyData,
        yt,           # yf.Ticker instance
        info: dict,
    ) -> None:
        """
        Fetch analyst consensus estimates and populate company.forward_estimates.

        Strategy:
        - Use yfinance's earnings_estimate / revenue_estimate DataFrames.
        - Row '0y' = current fiscal year in progress.
        - Row '+1y' = next full fiscal year.
        - We pick the row that best matches show_years[-1]+1 (the est column).
        - Fallback: use info['forwardEps'] / info['forwardPE'] if DataFrames empty.
        """
        # ── Determine target estimate year ────────────────────────────────────
        sorted_years = company.sorted_years()
        if not sorted_years:
            return
        est_year = sorted_years[0] + 1   # e.g. 2024 → 2025E, 2025 → 2026E

        # ── Fetch DataFrames ──────────────────────────────────────────────────
        try:
            ee = yt.earnings_estimate   # per-share EPS estimates
        except Exception:
            ee = None
        try:
            re_ = yt.revenue_estimate   # revenue estimates (raw units)
        except Exception:
            re_ = None

        # ── Helper: pick best row (0y or +1y) closest to est_year ─────────────
        def _pick_row(df, candidates=("0y", "+1y")):
            """Return the first non-empty row from the candidates list."""
            if df is None or df.empty:
                return None, None
            for period in candidates:
                if period in df.index:
                    row = df.loc[period]
                    avg = _safe(row.get("avg"), float)
                    if avg is not None and avg > 0:
                        return row, period
            return None, None

        eps_row, eps_period = _pick_row(ee)
        rev_row, rev_period = _pick_row(re_)

        # ── Build ForwardEstimates ────────────────────────────────────────────
        fe = ForwardEstimates(year=est_year)

        # Revenue (raw units → millions)
        if rev_row is not None:
            rev_avg = _safe(rev_row.get("avg"), float)
            if rev_avg and rev_avg > 0:
                fe.revenue = rev_avg / 1_000_000
            fe.revenue_growth_yoy = _safe(rev_row.get("growth"), float)
            fe.analyst_count = _safe(rev_row.get("numberOfAnalysts"), int)

        # EPS
        if eps_row is not None:
            fe.eps_diluted = _safe(eps_row.get("avg"), float)
            fe.eps_growth_yoy = _safe(eps_row.get("growth"), float)
            if fe.analyst_count is None:
                fe.analyst_count = _safe(eps_row.get("numberOfAnalysts"), int)

        # Fallback to info fields if DataFrames were empty
        if fe.eps_diluted is None:
            fe.eps_diluted = _safe(info.get("forwardEps"), float)

        # Net income from EPS × shares (shares in millions → NI in millions)
        shares_m = company.shares_outstanding
        if fe.eps_diluted is not None and shares_m and shares_m > 0:
            fe.net_income = fe.eps_diluted * shares_m

        # Net margin (derived)
        if fe.net_income is not None and fe.revenue and fe.revenue > 0:
            fe.net_margin = fe.net_income / fe.revenue

        # Forward P/E: current price / forward EPS
        if fe.eps_diluted is not None and company.current_price:
            if fe.eps_diluted > 0:
                # Handle cross-currency: current_price is in price currency
                # forward_pe from info is already computed correctly by Yahoo
                fpe = _safe(info.get("forwardPE"), float)
                if fpe and fpe > 0:
                    fe.pe_ratio = fpe
                else:
                    fe.pe_ratio = company.current_price / fe.eps_diluted

        # EV/Sales (current EV / forward revenue)
        if company.enterprise_value and fe.revenue and fe.revenue > 0:
            fe.ev_sales = company.enterprise_value / fe.revenue

        # Only store if we got at least some useful data
        if fe.revenue is not None or fe.eps_diluted is not None:
            company.forward_estimates = fe
            logger.debug(
                f"[yfinance] Forward estimates for {est_year}: "
                f"Rev={fe.revenue:.0f}M, EPS={fe.eps_diluted}, "
                f"Analysts={fe.analyst_count}"
                if fe.revenue else f"[yfinance] Forward EPS only: {fe.eps_diluted}"
            )


# ── Quick sanity test (run this file directly) ────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    adapter = YFinanceAdapter()

    test_tickers = [
        ("AAPL",     "US — Apple"),
        ("WKL.AS",   "EU — Wolters Kluwer (Amsterdam)"),
        ("NOKIA.HE", "EU — Nokia (Helsinki)"),
        ("ATCO-A.ST","EU — Atlas Copco (Stockholm)"),
        ("7203.T",   "Asia — Toyota (Tokyo)"),
    ]

    for ticker, label in test_tickers:
        print(f"\n{'='*60}")
        print(f"  {label}")
        result = adapter.fetch(ticker)
        if result.success:
            print(f"  {result.data.summary()}")
            la = result.data.latest_annual()
            if la:
                print(f"  Latest annual ({la.year}): "
                      f"Revenue={la.revenue:.0f}M, EBIT={la.ebit}M, "
                      f"Net Income={la.net_income}M")
        else:
            print(f"  FAILED: {result.error}")
