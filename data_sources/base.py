"""
base.py — Unified data models for Your Humble EquityBot.

Every data source adapter fills in what it can on CompanyData.
The DataManager merges results and tracks what came from where.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Dict, List
from datetime import datetime


@dataclass
class AnnualFinancials:
    """
    One row of historical annual financial data.
    All monetary values are in the company's reporting currency (millions).
    None = data not available from any source yet.
    """
    year: int

    # Income Statement
    revenue: Optional[float] = None         # Total revenue / net sales
    gross_profit: Optional[float] = None
    ebitda: Optional[float] = None          # Earnings before interest, tax, D&A
    ebit: Optional[float] = None            # Operating income (= EBIT)
    net_income: Optional[float] = None
    eps_diluted: Optional[float] = None     # Earnings per share (diluted)
    dividends_per_share: Optional[float] = None

    # Margins (as decimals: 0.15 = 15%)
    gross_margin: Optional[float] = None
    ebit_margin: Optional[float] = None
    ebitda_margin: Optional[float] = None
    net_margin: Optional[float] = None

    # Balance Sheet
    total_assets: Optional[float] = None
    total_debt: Optional[float] = None      # Short + long term debt
    cash: Optional[float] = None            # Cash & equivalents
    net_debt: Optional[float] = None        # total_debt - cash
    total_equity: Optional[float] = None
    shares_outstanding: Optional[float] = None  # in millions

    # Cash Flow
    operating_cash_flow: Optional[float] = None
    capex: Optional[float] = None           # Capital expenditures (positive number)
    fcf: Optional[float] = None             # Free cash flow = OCF - CapEx

    # Returns & Ratios (calculated or reported)
    roe: Optional[float] = None             # Return on Equity
    roa: Optional[float] = None             # Return on Assets
    roic: Optional[float] = None            # Return on Invested Capital

    # Historical market-based metrics (derived from year-end stock price)
    # Populated by yfinance_adapter after fetching monthly price history.
    price_year_end: Optional[float] = None          # Year-end stock price (local currency)
    market_cap: Optional[float] = None              # In millions  (price × shares_M)
    enterprise_value: Optional[float] = None        # market_cap + net_debt  (millions)
    pe_ratio: Optional[float] = None                # price / eps_diluted
    ev_ebit: Optional[float] = None                 # enterprise_value / ebit
    ev_sales: Optional[float] = None                # enterprise_value / revenue
    fcf_yield: Optional[float] = None               # fcf / market_cap  (decimal)
    div_yield: Optional[float] = None               # dividends_per_share / price (decimal)

    def calculate_derived(self) -> None:
        """Fill in fields that can be derived from other fields."""
        # Net debt
        if self.net_debt is None and self.total_debt is not None and self.cash is not None:
            self.net_debt = self.total_debt - self.cash

        # FCF from OCF - CapEx
        if self.fcf is None and self.operating_cash_flow is not None and self.capex is not None:
            self.fcf = self.operating_cash_flow - self.capex

        # EBITDA from EBIT + D&A (rough proxy if EBITDA missing)
        # We skip this since D&A isn't always available; better to leave None

        # Margins
        if self.revenue and self.revenue > 0:
            if self.net_margin is None and self.net_income is not None:
                self.net_margin = self.net_income / self.revenue
            if self.ebit_margin is None and self.ebit is not None:
                self.ebit_margin = self.ebit / self.revenue
            if self.ebitda_margin is None and self.ebitda is not None:
                self.ebitda_margin = self.ebitda / self.revenue
            if self.gross_margin is None and self.gross_profit is not None:
                self.gross_margin = self.gross_profit / self.revenue

        # ROE
        if self.roe is None and self.net_income is not None and self.total_equity is not None:
            if self.total_equity > 0:
                self.roe = self.net_income / self.total_equity

        # ROA
        if self.roa is None and self.net_income is not None and self.total_assets is not None:
            if self.total_assets > 0:
                self.roa = self.net_income / self.total_assets

        # ── Market-cap based historical ratios ────────────────────────────────
        # These are computed once price_year_end and market_cap are populated
        # by the yfinance adapter after fetching monthly price history.

        # P/E  =  year-end price / diluted EPS
        if self.pe_ratio is None and self.price_year_end is not None:
            if self.eps_diluted is not None and self.eps_diluted > 0:
                self.pe_ratio = self.price_year_end / self.eps_diluted

        # EV/EBIT and EV/Sales  (need enterprise_value populated first)
        # Recompute enterprise_value from market_cap + net_debt if missing
        # (yfinance sets market_cap from year-end price; EODHD resets enterprise_value to None)
        if self.enterprise_value is None and self.market_cap is not None:
            nd = self.net_debt
            if nd is None and self.total_debt is not None and self.cash is not None:
                nd = self.total_debt - self.cash
            self.enterprise_value = self.market_cap + (nd or 0)

        ev = self.enterprise_value
        if ev is not None and ev > 0:
            if self.ev_ebit is None and self.ebit and self.ebit > 0:
                self.ev_ebit = ev / self.ebit
            if self.ev_sales is None and self.revenue and self.revenue > 0:
                self.ev_sales = ev / self.revenue

        # FCF Yield  =  FCF / market cap
        if self.fcf_yield is None and self.fcf is not None:
            if self.market_cap and self.market_cap > 0:
                self.fcf_yield = self.fcf / self.market_cap

        # Dividend Yield  =  DPS / year-end price
        if self.div_yield is None and self.dividends_per_share is not None:
            if self.price_year_end and self.price_year_end > 0:
                self.div_yield = self.dividends_per_share / self.price_year_end


@dataclass
class ForwardEstimates:
    """
    Analyst consensus estimates for the next fiscal year.
    Populated by yfinance_adapter from yt.earnings_estimate / yt.revenue_estimate.
    Monetary values in millions (same convention as AnnualFinancials).
    """
    year: int                                   # Fiscal year this estimate covers
    revenue: Optional[float] = None             # Consensus revenue (millions)
    net_income: Optional[float] = None          # Consensus net income (millions)
    eps_diluted: Optional[float] = None         # Consensus EPS (per share)
    ebitda: Optional[float] = None              # Rarely available from free sources
    revenue_growth_yoy: Optional[float] = None  # Implied YoY growth (decimal)
    eps_growth_yoy: Optional[float] = None      # Implied EPS growth (decimal)
    net_margin: Optional[float] = None          # Derived: net_income / revenue
    pe_ratio: Optional[float] = None            # Forward P/E (current price / fwd EPS)
    ev_sales: Optional[float] = None            # Current EV / forward revenue
    analyst_count: Optional[int] = None         # Number of analysts contributing
    source: str = "yfinance"


@dataclass
class CompanyData:
    """
    Unified company data container populated by one or more adapters.
    The DataManager merges adapter results into this object.

    Monetary values: in company reporting currency, millions unless noted.
    Ratios: as decimals (0.15 = 15%) unless explicitly noted as multiples (e.g. P/E).
    """

    # ── Identity ─────────────────────────────────────────────────────────────
    ticker: str                             # Normalized Yahoo Finance ticker
    input_ticker: str = ""                  # Original ticker as user typed it
    name: Optional[str] = None
    exchange: Optional[str] = None
    currency: Optional[str] = None          # Reporting currency (USD, EUR, SEK…)
    sector: Optional[str] = None
    industry: Optional[str] = None
    country: Optional[str] = None
    isin: Optional[str] = None
    description: Optional[str] = None       # Business description
    website: Optional[str] = None
    employees: Optional[int] = None
    cik: Optional[str] = None              # SEC EDGAR CIK (US companies only)

    # ── Current Market Data ───────────────────────────────────────────────────
    current_price: Optional[float] = None
    currency_price: Optional[str] = None    # Currency of the price quote
    market_cap: Optional[float] = None      # In millions
    shares_outstanding: Optional[float] = None  # In millions
    enterprise_value: Optional[float] = None    # In millions
    as_of_date: Optional[str] = None        # Date of market data snapshot

    # ── Current Valuation Multiples ───────────────────────────────────────────
    pe_ratio: Optional[float] = None        # Price / Earnings (TTM)
    forward_pe: Optional[float] = None
    price_to_book: Optional[float] = None   # P/B
    ev_ebit: Optional[float] = None         # EV / EBIT
    ev_ebitda: Optional[float] = None       # EV / EBITDA
    ev_sales: Optional[float] = None        # EV / Revenue
    fcf_yield: Optional[float] = None       # FCF / Market Cap (decimal)
    dividend_yield: Optional[float] = None  # Dividend / Price (decimal)

    # ── Current Profitability & Quality ──────────────────────────────────────
    net_margin: Optional[float] = None      # TTM
    ebit_margin: Optional[float] = None     # TTM
    ebitda_margin: Optional[float] = None   # TTM
    gross_margin: Optional[float] = None    # TTM
    roe: Optional[float] = None             # TTM
    roa: Optional[float] = None             # TTM
    roic: Optional[float] = None            # TTM

    # ── Current Balance Sheet Health ─────────────────────────────────────────
    gearing: Optional[float] = None         # Net Debt / EBITDA
    net_debt: Optional[float] = None        # In millions
    debt_to_equity: Optional[float] = None
    current_ratio: Optional[float] = None
    interest_coverage: Optional[float] = None
    beta: Optional[float] = None

    # ── Technical / Price Levels ──────────────────────────────────────────────
    week_52_high: Optional[float] = None    # 52-week high price
    week_52_low: Optional[float] = None     # 52-week low price
    ma_50: Optional[float] = None           # 50-day moving average
    ma_200: Optional[float] = None          # 200-day moving average

    # ── Ownership Structure ───────────────────────────────────────────────────
    shares_float: Optional[float] = None        # Float shares in millions
    pct_insiders: Optional[float] = None        # % held by insiders (decimal)
    pct_institutions: Optional[float] = None    # % held by institutions (decimal)

    # ── Dividends & Corporate Actions ────────────────────────────────────────
    payout_ratio: Optional[float] = None
    forward_annual_dividend_rate: Optional[float] = None   # Per-share annual div
    forward_annual_dividend_yield: Optional[float] = None  # Decimal
    dividend_date: Optional[str] = None
    ex_dividend_date: Optional[str] = None
    last_split_factor: Optional[str] = None    # e.g. "2:1"
    last_split_date: Optional[str] = None

    # ── Extended Company Identity ─────────────────────────────────────────────
    ipo_date: Optional[str] = None
    fiscal_year_end: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    officers: List[dict] = field(default_factory=list)  # [{name, title}, ...]

    # ── Additional Per-Share & TTM Metrics ────────────────────────────────────
    peg_ratio: Optional[float] = None
    price_to_sales: Optional[float] = None
    book_value_per_share: Optional[float] = None
    revenue_per_share: Optional[float] = None
    eps_ttm: Optional[float] = None
    quarterly_revenue_growth_yoy: Optional[float] = None
    quarterly_earnings_growth_yoy: Optional[float] = None

    # ── 10-Year Annual History ────────────────────────────────────────────────
    # Keys are fiscal year integers: {2024: AnnualFinancials(...), 2023: ...}
    annual_financials: Dict[int, AnnualFinancials] = field(default_factory=dict)

    # ── Analyst Forward Estimates ─────────────────────────────────────────────
    # Structured consensus estimates for the next full fiscal year.
    forward_estimates: Optional[ForwardEstimates] = None
    # Legacy flat fields (kept for backwards compat, prefer forward_estimates)
    revenue_estimate_next_year: Optional[float] = None
    eps_estimate_next_year: Optional[float] = None
    revenue_growth_estimate: Optional[float] = None  # YoY %

    # ── Data Provenance ───────────────────────────────────────────────────────
    data_sources: List[str] = field(default_factory=list)
    missing_fields: List[str] = field(default_factory=list)
    fetch_timestamp: Optional[str] = None

    # ─────────────────────────────────────────────────────────────────────────
    # Convenience helpers
    # ─────────────────────────────────────────────────────────────────────────

    def sorted_years(self, descending: bool = True) -> List[int]:
        """Return fiscal years for which annual data is available."""
        return sorted(self.annual_financials.keys(), reverse=descending)

    def latest_annual(self) -> Optional[AnnualFinancials]:
        yrs = self.sorted_years()
        return self.annual_financials[yrs[0]] if yrs else None

    def year_range(self) -> str:
        yrs = self.sorted_years(descending=False)
        if not yrs:
            return "n/a"
        return f"{yrs[0]}–{yrs[-1]}" if len(yrs) > 1 else str(yrs[0])

    def revenue_cagr(self, years: int = 3) -> Optional[float]:
        """
        Calculate revenue CAGR over the last N years.
        Returns decimal (0.05 = 5% CAGR) or None if insufficient data.
        """
        yrs = self.sorted_years()
        if len(yrs) < years + 1:
            return None
        latest = self.annual_financials[yrs[0]].revenue
        base   = self.annual_financials[yrs[years]].revenue
        if not latest or not base or base <= 0:
            return None
        return (latest / base) ** (1 / years) - 1

    def calculate_current_ratios(self) -> None:
        """
        Derive current-period ratios from available market + latest annual data.
        Called after all adapters have run.
        """
        la = self.latest_annual()
        if la:
            la.calculate_derived()

        # Enterprise Value = Market Cap + Net Debt
        if self.enterprise_value is None and self.market_cap is not None:
            nd = self.net_debt if self.net_debt is not None else (la.net_debt if la else None)
            if nd is not None:
                self.enterprise_value = self.market_cap + nd

        ev = self.enterprise_value

        # EV/EBIT
        if self.ev_ebit is None and ev and la and la.ebit and la.ebit > 0:
            self.ev_ebit = ev / la.ebit

        # EV/EBITDA
        if self.ev_ebitda is None and ev and la and la.ebitda and la.ebitda > 0:
            self.ev_ebitda = ev / la.ebitda

        # EV/Sales
        if self.ev_sales is None and ev and la and la.revenue and la.revenue > 0:
            self.ev_sales = ev / la.revenue

        # FCF Yield
        if self.fcf_yield is None and self.market_cap and la and la.fcf and self.market_cap > 0:
            self.fcf_yield = la.fcf / self.market_cap

        # Gearing (Net Debt / EBITDA)
        if self.gearing is None:
            nd = self.net_debt if self.net_debt is not None else (la.net_debt if la else None)
            ebitda = la.ebitda if la else None
            if nd is not None and ebitda and ebitda > 0:
                self.gearing = nd / ebitda

        # Current-period profitability from latest annual
        if la:
            if self.net_margin is None:   self.net_margin   = la.net_margin
            if self.ebit_margin is None:  self.ebit_margin  = la.ebit_margin
            if self.ebitda_margin is None:self.ebitda_margin = la.ebitda_margin
            if self.gross_margin is None: self.gross_margin  = la.gross_margin
            if self.roe is None:          self.roe           = la.roe
            if self.roa is None:          self.roa           = la.roa
            if self.net_debt is None:     self.net_debt      = la.net_debt

    def completeness_pct(self) -> float:
        """Rough data completeness score 0-100%."""
        key_fields = [
            self.name, self.current_price, self.market_cap,
            self.pe_ratio, self.ev_ebit, self.ev_sales,
            self.net_margin, self.roe, self.gearing,
            self.dividend_yield, self.description,
        ]
        filled = sum(1 for f in key_fields if f is not None)
        annual_years = len(self.annual_financials)
        annual_score = min(annual_years / 10, 1.0) * 40  # 40% weight
        field_score  = (filled / len(key_fields)) * 60   # 60% weight
        return round(annual_score + field_score, 1)

    def summary(self) -> str:
        """One-line data summary for logging."""
        return (
            f"{self.ticker} | {self.name or 'Unknown'} | "
            f"Price: {self.current_price} {self.currency_price or ''} | "
            f"MCap: {self.market_cap}M | "
            f"Years of data: {self.year_range()} | "
            f"Sources: {', '.join(self.data_sources)} | "
            f"Completeness: {self.completeness_pct()}%"
        )


@dataclass
class DataSourceResult:
    """
    Returned by each adapter's fetch() method.
    Contains a (possibly partial) CompanyData plus metadata.
    """
    success: bool
    source_name: str
    data: Optional[CompanyData] = None
    error: Optional[str] = None
    fields_filled: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0
