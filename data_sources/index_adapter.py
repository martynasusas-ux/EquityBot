"""
index_adapter.py — Fetches market index and ETF data for Your Humble EquityBot.

Handles both:
  - Pure market indexes (^OMXH25, ^GSPC, ^GDAXI …) — price + returns
  - ETF tickers  (SPY, QQQ, EUNL.DE …) — full holdings + sector weights
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class IndexData:
    """
    Unified container for a market index or ETF.
    Used instead of CompanyData when the ticker resolves to an INDEX or ETF.
    """

    # ── Identity ─────────────────────────────────────────────────────────────
    ticker: str
    name: str = ""
    index_type: str = "INDEX"       # "INDEX" | "ETF"
    currency: Optional[str] = None
    description: str = ""

    # ── Current level / price ─────────────────────────────────────────────────
    current_level: Optional[float] = None
    change_1d_pct: Optional[float] = None   # percent (e.g. 1.2 = +1.2%)
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None

    # ── Returns (as decimals: 0.12 = 12%) ────────────────────────────────────
    return_ytd: Optional[float] = None
    return_1m:  Optional[float] = None
    return_3m:  Optional[float] = None
    return_6m:  Optional[float] = None
    return_1y:  Optional[float] = None
    return_3y_ann: Optional[float] = None   # Annualised
    return_5y_ann: Optional[float] = None   # Annualised

    # ── Risk ─────────────────────────────────────────────────────────────────
    volatility_1y_ann: Optional[float] = None   # Annualised daily vol

    # ── Valuation ─────────────────────────────────────────────────────────────
    weighted_pe:    Optional[float] = None
    dividend_yield: Optional[float] = None      # Decimal

    # ── ETF-specific ──────────────────────────────────────────────────────────
    aum_millions:   Optional[float] = None
    expense_ratio:  Optional[float] = None      # Decimal (0.0003 = 0.03%)

    # ── Holdings (ETFs only; empty list for pure indexes) ────────────────────
    top_holdings:   list = field(default_factory=list)
    # [{ticker, name, weight_pct}]

    sector_weights:  dict = field(default_factory=dict)
    # {sector_name: weight_pct}

    country_weights: dict = field(default_factory=dict)
    # {country_name: weight_pct}

    # ── Provenance ────────────────────────────────────────────────────────────
    data_sources:     list = field(default_factory=list)
    as_of_date:       str  = ""
    fetch_timestamp:  str  = ""


# ── Adapter ───────────────────────────────────────────────────────────────────

class IndexAdapter:
    """
    Fetch index / ETF data via yfinance.
    Returns IndexData (not CompanyData).
    """

    def fetch(self, ticker: str) -> IndexData:
        result = IndexData(
            ticker=ticker,
            as_of_date=datetime.utcnow().strftime("%Y-%m-%d"),
            fetch_timestamp=datetime.utcnow().isoformat(),
        )

        try:
            import yfinance as yf
        except ImportError:
            logger.error("[IndexAdapter] yfinance not installed")
            return result

        try:
            yt   = yf.Ticker(ticker)
            info = yt.info or {}

            # ── Identity ──────────────────────────────────────────────────────
            result.name = (
                info.get("longName")
                or info.get("shortName")
                or ticker
            )
            result.currency    = info.get("currency")
            result.description = info.get("longBusinessSummary") or ""
            qt = info.get("quoteType", "").upper()
            result.index_type = "ETF" if qt == "ETF" else "INDEX"

            # ── Current level ─────────────────────────────────────────────────
            result.current_level = (
                info.get("regularMarketPrice")
                or info.get("previousClose")
                or info.get("navPrice")
            )
            chg = info.get("regularMarketChangePercent")
            result.change_1d_pct = chg * 100 if chg and abs(chg) < 1 else chg
            result.high_52w = info.get("fiftyTwoWeekHigh")
            result.low_52w  = info.get("fiftyTwoWeekLow")

            # ── Valuation ─────────────────────────────────────────────────────
            result.weighted_pe = (
                info.get("trailingPE")
                or info.get("forwardPE")
            )
            div = info.get("yield") or info.get("dividendYield")
            result.dividend_yield = div

            # ── ETF-specific ──────────────────────────────────────────────────
            if result.index_type == "ETF":
                ta = info.get("totalAssets")
                result.aum_millions = ta / 1_000_000 if ta else None
                result.expense_ratio = (
                    info.get("annualReportExpenseRatio")
                    or info.get("expenseRatio")
                )
                result.top_holdings  = _parse_holdings(info.get("holdings", []))
                result.sector_weights  = _parse_weights(info.get("sectorWeightings", []))
                result.country_weights = _parse_weights(info.get("countryWeightings", []))

                # Newer yfinance: funds_data.top_holdings (richer data)
                if not result.top_holdings:
                    try:
                        fd = yt.funds_data
                        if fd and hasattr(fd, "top_holdings"):
                            th = fd.top_holdings
                            if th is not None and not getattr(th, "empty", True):
                                result.top_holdings = [
                                    {
                                        "ticker":     str(idx),
                                        "name":       row.get("holdingName", str(idx)),
                                        "weight_pct": round(
                                            row.get("holdingPercent", 0) * 100, 2
                                        ),
                                    }
                                    for idx, row in th.iterrows()
                                ][:25]
                    except Exception:
                        pass

            # ── Returns + volatility from price history ───────────────────────
            try:
                hist = yt.history(period="5y", interval="1d", auto_adjust=True)
                if not hist.empty and "Close" in hist.columns:
                    _fill_returns(result, hist)
            except Exception as e:
                logger.warning(f"[IndexAdapter] price history failed for {ticker}: {e}")

            result.data_sources = ["yfinance"]
            logger.info(
                f"[IndexAdapter] {ticker} → {result.name} "
                f"({result.index_type}, level={result.current_level})"
            )

        except Exception as e:
            logger.error(f"[IndexAdapter] fetch failed for {ticker}: {e}")
            result.name = result.name or ticker

        return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_holdings(raw: list) -> list:
    """Normalise yfinance holdings list → [{ticker, name, weight_pct}]."""
    out = []
    for h in raw:
        if not isinstance(h, dict):
            continue
        sym = h.get("symbol") or ""
        if not sym:
            continue
        out.append({
            "ticker":     sym,
            "name":       h.get("holdingName") or sym,
            "weight_pct": round(h.get("holdingPercent", 0) * 100, 2),
        })
    return out


def _parse_weights(raw) -> dict:
    """Normalise yfinance sector/country weightings → {name: weight_pct}."""
    out: dict[str, float] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                for k, v in item.items():
                    try:
                        out[k] = round(float(v) * 100, 2)
                    except (TypeError, ValueError):
                        pass
    elif isinstance(raw, dict):
        for k, v in raw.items():
            try:
                out[k] = round(float(v) * 100, 2)
            except (TypeError, ValueError):
                pass
    return out


def _fill_returns(data: IndexData, hist) -> None:
    """Compute return and volatility metrics from a price history DataFrame."""
    try:
        import pandas as pd
        import numpy as np
    except ImportError:
        return

    close = hist["Close"].dropna()
    if len(close) < 5:
        return

    latest      = float(close.iloc[-1])
    latest_date = close.index[-1]

    def _price_ago(days: int) -> float | None:
        target     = latest_date - pd.Timedelta(days=days)
        candidates = close.index[close.index <= target]
        if not len(candidates):
            return None
        return float(close[candidates[-1]])

    def _ret(days: int) -> float | None:
        p = _price_ago(days)
        return (latest / p - 1) if p and p > 0 else None

    data.return_1m = _ret(30)
    data.return_3m = _ret(91)
    data.return_6m = _ret(182)
    data.return_1y = _ret(365)

    p3 = _price_ago(365 * 3)
    p5 = _price_ago(365 * 5)
    data.return_3y_ann = ((latest / p3) ** (1 / 3) - 1) if p3 and p3 > 0 else None
    data.return_5y_ann = ((latest / p5) ** (1 / 5) - 1) if p5 and p5 > 0 else None

    # YTD
    try:
        tz = latest_date.tzinfo
        ytd_start = pd.Timestamp(f"{latest_date.year}-01-01", tz=tz)
        ytd_cands = close.index[close.index >= ytd_start]
        if len(ytd_cands):
            p_ytd = float(close[ytd_cands[0]])
            data.return_ytd = (latest / p_ytd - 1) if p_ytd > 0 else None
    except Exception:
        pass

    # Annualised 1Y volatility
    try:
        recent = close.tail(252)
        if len(recent) > 20:
            dr = recent.pct_change().dropna()
            data.volatility_1y_ann = float(dr.std() * np.sqrt(252))
    except Exception:
        pass
