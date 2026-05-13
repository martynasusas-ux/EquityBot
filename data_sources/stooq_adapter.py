"""
stooq_adapter.py — Free Stooq EOD price fallback.

Stooq.com publishes free end-of-day close prices for stocks worldwide.
We use it as a last-resort price source when yfinance fails (commonly on
Streamlit Cloud where Yahoo blocks cloud IPs) and the EODHD subscription
doesn't include live/EOD quotes.

The Stooq close is typically 1 trading day old — far better than EODHD's
MarketCapitalization snapshot which can be days/weeks stale.

CSV format returned: ticker,date,time,close
  RHM.DE,2026-05-12,17:30:00,1162

No API key required, no rate limits documented.
"""

from __future__ import annotations
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

STOOQ_URL = "https://stooq.com/q/l/"

# Stooq quietly rejects custom / bot-style User-Agents on some POPs.
# Use a plain browser UA to maximise reliability across hosting environments
# (Streamlit Cloud, AWS, Azure, local).
_STOOQ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


# Yahoo Finance suffix → Stooq suffix.
# Stooq mostly mirrors Yahoo's format for European exchanges, with a few
# differences for US listings and some Asian markets.
_YF_TO_STOOQ = {
    "":     ".us",      # US — no suffix on Yahoo, .us on Stooq
    ".DE":  ".de",      # Germany — Xetra
    ".F":   ".f",
    ".L":   ".uk",      # UK — Stooq uses .uk, not .l
    ".PA":  ".fr",      # France — Stooq uses .fr
    ".AS":  ".nl",      # Netherlands
    ".BR":  ".be",      # Belgium
    ".MI":  ".it",      # Italy
    ".MC":  ".es",      # Spain
    ".HE":  ".fi",      # Finland
    ".ST":  ".se",      # Sweden
    ".OL":  ".no",      # Norway
    ".CO":  ".dk",      # Denmark
    ".SW":  ".ch",      # Switzerland
    ".VI":  ".at",      # Austria
    ".WA":  ".pl",      # Poland
    ".LS":  ".pt",      # Portugal
    ".AT":  ".gr",      # Greece
    ".IR":  ".ie",      # Ireland
    ".TO":  ".ca",      # Canada — TSX
    ".V":   ".cv",      # Canada Venture
    ".SA":  ".br",      # Brazil
    ".MX":  ".mx",      # Mexico
    ".HK":  ".hk",      # Hong Kong
    ".T":   ".jp",      # Japan
    ".AX":  ".au",      # Australia
}


def _yf_to_stooq(yf_ticker: str) -> str:
    """Convert Yahoo Finance ticker to Stooq URL slug (lowercase)."""
    dot = yf_ticker.rfind(".")
    if dot == -1:
        base, suffix = yf_ticker, ""
    else:
        base, suffix = yf_ticker[:dot], yf_ticker[dot:]
    stooq_suffix = _YF_TO_STOOQ.get(suffix.upper(), suffix.lower())
    return f"{base.lower()}{stooq_suffix}"


def fetch_stooq_close(yf_ticker: str, timeout: int = 15) -> Optional[float]:
    """
    Fetch the most recent close price for a ticker from Stooq.

    Returns float (price in the company's reporting currency) or None on
    any failure (network, parse error, unknown ticker).
    """
    stooq_ticker = _yf_to_stooq(yf_ticker)
    url = f"{STOOQ_URL}?s={stooq_ticker}&f=sd2t2c"
    try:
        resp = requests.get(url, headers=_STOOQ_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            logger.warning(f"[stooq] {yf_ticker} → {stooq_ticker} HTTP {resp.status_code}")
            return None
        body = resp.text.strip()
        # Expected: TICKER,YYYY-MM-DD,HH:MM:SS,CLOSE
        # Error / no data: "N/D" appears or fewer than 4 columns
        if not body or "N/D" in body or "," not in body:
            logger.warning(f"[stooq] {yf_ticker} no data: {body[:120]!r}")
            return None
        parts = body.split(",")
        if len(parts) < 4:
            logger.warning(f"[stooq] {yf_ticker} unexpected format: {body[:120]!r}")
            return None
        try:
            price = float(parts[3])
            if price > 0:
                logger.info(f"[stooq] {yf_ticker} → {stooq_ticker} = {price}")
                return price
            logger.warning(f"[stooq] {yf_ticker} non-positive price: {price}")
        except ValueError:
            logger.warning(f"[stooq] {yf_ticker} could not parse price field: {parts[3]!r}")
            return None
        return None
    except Exception as e:
        logger.warning(f"[stooq] fetch failed for {yf_ticker} ({stooq_ticker}): {e}")
        return None
