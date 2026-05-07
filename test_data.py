"""
test_data.py — Phase 1 validation: fetch real data and print a structured report.

Run: python test_data.py
     python test_data.py AAPL WKL.AS NOKIA.HE   (custom tickers)

Tests the full data waterfall: yfinance → EDGAR → Alpha Vantage → FMP.
Prints a clean summary table so you can visually verify data quality.
"""

import sys
import os
import logging

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

# Configure logging — INFO shows the waterfall, DEBUG shows everything
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

from data_sources.data_manager import DataManager, _fmt, _pct

# ── Test universe ─────────────────────────────────────────────────────────────
DEFAULT_TICKERS = [
    ("AAPL",      "US   Apple Inc."),
    ("MSFT",      "US   Microsoft Corp"),
    ("WKL.AS",    "EU   Wolters Kluwer (Amsterdam)"),
    ("NOKIA.HE",  "EU   Nokia (Helsinki)"),
    ("ATCO-A.ST", "EU   Atlas Copco (Stockholm)"),
    ("AZN.L",     "UK   AstraZeneca (London)"),
]

SEPARATOR = "=" * 80


def run_tests(tickers):
    dm = DataManager()
    print(f"\n{'Your Humble EquityBot — Phase 1 Data Test':^80}")
    print(SEPARATOR)

    passed = 0
    warned = 0
    failed = 0

    for ticker, label in tickers:
        print(f"\n  [{label}]")
        print(f"  Ticker: {ticker}")

        try:
            c = dm.get(ticker)
        except Exception as e:
            print(f"  [EXCEPTION]: {e}")
            failed += 1
            continue

        # ── Basic identity ────────────────────────────────────────────────────
        print(f"  Name:     {c.name or '⚠ missing'}")
        print(f"  Sector:   {c.sector or '⚠ missing'}  |  Industry: {c.industry or '⚠ missing'}")
        print(f"  Country:  {c.country or '⚠ missing'}  |  Currency: {c.currency or '?'}")
        print(f"  Sources:  {', '.join(c.data_sources)}")

        # ── Market data ───────────────────────────────────────────────────────
        print(f"\n  Market Data (as of {c.as_of_date}):")
        print(f"    Price:      {c.current_price} {c.currency_price or ''}")
        print(f"    Mkt Cap:    {_fmt(c.market_cap)}M")
        print(f"    EV:         {_fmt(c.enterprise_value)}M")
        print(f"    Shares:     {_fmt(c.shares_outstanding)}M")

        # ── Valuation ─────────────────────────────────────────────────────────
        print(f"\n  Valuation Multiples:")
        print(f"    P/E:        {_fmtx(c.pe_ratio)}")
        print(f"    EV/EBIT:    {_fmtx(c.ev_ebit)}")
        print(f"    EV/EBITDA:  {_fmtx(c.ev_ebitda)}")
        print(f"    EV/Sales:   {_fmtx(c.ev_sales)}")
        print(f"    P/B:        {_fmtx(c.price_to_book)}")
        print(f"    Div Yield:  {_pct(c.dividend_yield)}")
        print(f"    FCF Yield:  {_pct(c.fcf_yield)}")

        # ── Quality ───────────────────────────────────────────────────────────
        print(f"\n  Quality Metrics (TTM):")
        print(f"    Net Margin: {_pct(c.net_margin)}")
        print(f"    EBIT Margin:{_pct(c.ebit_margin)}")
        print(f"    ROE:        {_pct(c.roe)}")
        print(f"    ROA:        {_pct(c.roa)}")
        print(f"    Gearing:    {_fmtx(c.gearing)}x net debt/EBITDA")
        print(f"    Net Debt:   {_fmt(c.net_debt)}M")

        # ── Annual history ────────────────────────────────────────────────────
        years = c.sorted_years()
        print(f"\n  Annual History: {c.year_range()} ({len(years)} years)")
        if years:
            hdr = f"    {'Year':>5}  {'Revenue':>10}  {'EBIT':>10}  {'NetIncome':>10}  {'EPS':>7}  {'FCF':>10}  {'ROE':>7}"
            print(hdr)
            print(f"    {'-'*75}")
            for yr in years[:10]:
                af = c.annual_financials[yr]
                print(
                    f"    {yr:>5}  "
                    f"{_fmtm(af.revenue):>10}  "
                    f"{_fmtm(af.ebit):>10}  "
                    f"{_fmtm(af.net_income):>10}  "
                    f"{_fmteps(af.eps_diluted):>7}  "
                    f"{_fmtm(af.fcf):>10}  "
                    f"{_pct(af.roe):>7}"
                )

        # ── Revenue CAGR ──────────────────────────────────────────────────────
        cagr3 = c.revenue_cagr(3)
        cagr5 = c.revenue_cagr(5)
        print(f"\n  Revenue CAGR: 3yr={_pct(cagr3)}  5yr={_pct(cagr5)}")

        # ── Completeness & missing ────────────────────────────────────────────
        score = c.completeness_pct()
        print(f"\n  Completeness: {score}%", end="")
        if c.missing_fields:
            print(f"  [!] Missing: {', '.join(c.missing_fields)}")
        else:
            print("  [OK] All critical fields present")

        # ── Pass/warn/fail logic ──────────────────────────────────────────────
        if score >= 70 and len(years) >= 3:
            print(f"  [PASS]")
            passed += 1
        elif score >= 40 or len(years) >= 1:
            print(f"  [WARN] -- partial data")
            warned += 1
        else:
            print(f"  [FAIL] -- insufficient data")
            failed += 1

        print()

    # ── Summary ───────────────────────────────────────────────────────────────
    print(SEPARATOR)
    total = passed + warned + failed
    print(f"\n  Results: {passed}/{total} PASS  |  {warned} WARN  |  {failed} FAIL")
    if failed == 0 and warned == 0:
        print("  [SUCCESS] Phase 1 data layer is fully operational!")
    elif failed == 0:
        print("  [OK] Phase 1 data layer is operational (some data gaps -- normal for free tiers).")
    else:
        print("  [!] Some tickers failed -- check ticker format or network connectivity.")
    print()


# ── Formatters ────────────────────────────────────────────────────────────────
def _fmtm(v) -> str:
    """Format millions value."""
    if v is None: return "n/a"
    if abs(v) >= 1000: return f"{v/1000:,.1f}B"
    return f"{v:,.0f}M"

def _fmtx(v) -> str:
    """Format multiple (e.g. P/E)."""
    if v is None: return "n/a"
    return f"{v:.1f}x"

def _fmteps(v) -> str:
    """Format EPS."""
    if v is None: return "n/a"
    return f"{v:.2f}"


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Custom tickers from command line
        custom = [(t.upper(), t.upper()) for t in sys.argv[1:]]
        run_tests(custom)
    else:
        run_tests(DEFAULT_TICKERS)
