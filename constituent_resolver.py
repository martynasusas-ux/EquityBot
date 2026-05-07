"""
constituent_resolver.py — Resolve an index ticker → list of constituent tickers.

Waterfall (stops at first strategy that returns ≥ 3 tickers):
  1. ETF holdings via yfinance           (works for SPY, QQQ, EUNL.DE …)
  2. FMP index constituent endpoint      (S&P 500, NASDAQ 100, Dow Jones)
  3. FMP ETF-holder endpoint             (any ETF with FMP coverage)
  4. Wikipedia constituent table scrape  (~50 major global indexes mapped)
  5. Manual override                     (user-supplied ticker list)

Results are cached to cache/constituents/<INDEX>.json for 7 days.
"""

from __future__ import annotations
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────────
_CACHE_TTL_DAYS = 7


def _cache_dir() -> Path:
    from config import CACHE_DIR
    d = CACHE_DIR / "constituents"
    d.mkdir(exist_ok=True)
    return d


def _cache_path(ticker: str) -> Path:
    safe = ticker.replace("^", "").replace(".", "_").replace("/", "_").upper()
    return _cache_dir() / f"{safe}.json"


def _load_cache(ticker: str) -> list[str] | None:
    p = _cache_path(ticker)
    if not p.exists():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        cached_at = datetime.fromisoformat(d["cached_at"])
        if datetime.utcnow() - cached_at > timedelta(days=_CACHE_TTL_DAYS):
            return None
        return d["tickers"]
    except Exception:
        return None


def _save_cache(ticker: str, tickers: list[str]) -> None:
    try:
        with open(_cache_path(ticker), "w", encoding="utf-8") as f:
            json.dump({"cached_at": datetime.utcnow().isoformat(), "tickers": tickers}, f)
    except Exception:
        pass


# ── Wikipedia index map ───────────────────────────────────────────────────────
# Maps Yahoo Finance index ticker → (Wikipedia article slug, exchange suffix to
# append to bare symbols so yfinance can look them up).
# suffix = "" means tickers are already in the correct Yahoo Finance format.

WIKIPEDIA_MAP: dict[str, tuple[str, str]] = {
    # ── Americas ──────────────────────────────────────────────────────────────
    "^GSPC":     ("List_of_S%26P_500_companies",          ""),
    "^SP500":    ("List_of_S%26P_500_companies",          ""),
    "^DJI":      ("Dow_Jones_Industrial_Average",         ""),
    "^NDX":      ("Nasdaq-100",                           ""),
    "^IXIC":     ("Nasdaq-100",                           ""),   # use NDX as proxy
    "^RUT":      ("Russell_2000_Index",                   ""),
    "^GSPTSE":   ("S%26P/TSX_60",                         ".TO"),
    "^TSX":      ("S%26P/TSX_60",                         ".TO"),
    # ── Europe ────────────────────────────────────────────────────────────────
    "^FTSE":     ("FTSE_100_Index",                       ".L"),
    "^GDAXI":    ("DAX",                                  ".DE"),
    "^FCHI":     ("CAC_40",                               ".PA"),
    "^AEX":      ("AEX_index",                            ".AS"),
    "^IBEX":     ("IBEX_35",                              ".MC"),
    "^STOXX50E": ("Euro_Stoxx_50",                        ""),   # mixed suffixes
    "^MIB":      ("FTSE_MIB",                             ".MI"),
    "^SSMI":     ("Swiss_Market_Index",                   ".SW"),
    "^ATX":      ("ATX_(index)",                          ".VI"),
    "^BFX":      ("BEL_20",                               ".BR"),
    "^PSI20":    ("PSI-20",                               ".LS"),
    "^OMXH25":   ("OMX_Helsinki_25",                      ".HE"),
    "^OMXS30":   ("OMX_Stockholm_30",                     ".ST"),
    "^OMXC25":   ("OMX_Copenhagen_25",                    ".CO"),
    "^OMXC20":   ("OMX_Copenhagen_20",                    ".CO"),
    "^OBX":      ("OBX_Index",                            ".OL"),
    "^OSEAX":    ("Oslo_Stock_Exchange",                   ".OL"),
    "^WIG20":    ("WIG20",                                ".WA"),
    "^BUX":      ("Budapest_Stock_Exchange",              ".BD"),
    "^PX":       ("PX_index",                             ".PR"),
    # ── Asia / Pacific ────────────────────────────────────────────────────────
    "^N225":     ("Nikkei_225",                           ".T"),
    "^HSI":      ("Hang_Seng_Index_constituents",         ".HK"),
    "^AXJO":     ("S%26P/ASX_200",                        ".AX"),
    "^AORD":     ("S%26P/ASX_200",                        ".AX"),
    "^STI":      ("Straits_Times_Index",                  ".SI"),
    "^KS11":     ("KOSPI",                                ".KS"),
    "^KQ11":     ("KOSDAQ",                               ".KQ"),
    "^BSESN":    ("BSE_SENSEX",                           ".BO"),
    "^NSEI":     ("NIFTY_50",                             ".NS"),
    "^TWII":     ("Taiwan_Capitalization_Weighted_Stock_Index", ".TW"),
    "^NZ50":     ("S%26P/NZX_50",                         ".NZ"),
    "^JKSE":     ("IDX_Composite",                        ".JK"),
    "^KLSE":     ("FTSE_Bursa_Malaysia_KLCI",             ".KL"),
    "^SET":      ("SET_Index",                            ".BK"),
    "^PSI":      ("PSEi",                                 ".PS"),
    # ── Middle East / Africa ──────────────────────────────────────────────────
    "^TA125.TA": ("Tel_Aviv_125_Index",                   ".TA"),
    "^TASI.SR":  ("Tadawul_All_Share_Index",              ""),
    "^EGX30":    ("EGX_30_Price_Return_Index",            ".CA"),
    "^JN0U.JO":  ("JSE_Top_40",                           ".JO"),
}

# Column names that typically contain ticker symbols in Wikipedia tables
_TICKER_COLS = {
    "symbol", "ticker", "code", "stock symbol", "stock code", "abbr",
    "index symbol", "listing code", "nse symbol", "bse code", "ric",
}


# ── Public API ────────────────────────────────────────────────────────────────

class ConstituentResolver:
    """
    Resolve an index / ETF ticker to its constituent tickers.

    Usage:
        resolver = ConstituentResolver()
        tickers = resolver.resolve("^OMXH25")
        # → ["KNEBV.HE", "NOKIA.HE", "ELISA.HE", ...]
    """

    def __init__(self, fmp_api_key: str = ""):
        from config import FMP_API_KEY
        self._fmp_key = fmp_api_key or FMP_API_KEY

    # ── Main entry point ──────────────────────────────────────────────────────

    def resolve(
        self,
        ticker: str,
        force_refresh: bool = False,
    ) -> list[str]:
        """
        Return a list of constituent Yahoo Finance tickers.
        Returns an empty list if no source succeeds.
        """
        t = ticker.strip().upper()

        if not force_refresh:
            cached = _load_cache(t)
            if cached:
                logger.info(f"[ConstituentResolver] Cache hit: {t} → {len(cached)} tickers")
                return cached

        result: list[str] = []

        # Strategy 1: ETF holdings via yfinance
        if not t.startswith("^"):
            result = self._from_etf_yfinance(t)
            if result:
                logger.info(f"[ConstituentResolver] ETF yfinance: {t} → {len(result)} tickers")

        # Strategy 2: FMP index constituent endpoint (US indexes)
        if not result and self._fmp_key:
            result = self._from_fmp_index(t)
            if result:
                logger.info(f"[ConstituentResolver] FMP index: {t} → {len(result)} tickers")

        # Strategy 3: FMP ETF-holder endpoint
        if not result and self._fmp_key and not t.startswith("^"):
            result = self._from_fmp_etf(t)
            if result:
                logger.info(f"[ConstituentResolver] FMP ETF: {t} → {len(result)} tickers")

        # Strategy 4: Wikipedia scrape
        if not result:
            result = self._from_wikipedia(t)
            if result:
                logger.info(f"[ConstituentResolver] Wikipedia: {t} → {len(result)} tickers")

        if result:
            _save_cache(t, result)
        else:
            logger.warning(f"[ConstituentResolver] Could not resolve constituents for {t}")

        return result

    # ── Strategy 1: yfinance ETF holdings ────────────────────────────────────

    def _from_etf_yfinance(self, ticker: str) -> list[str]:
        try:
            import yfinance as yf
            yt   = yf.Ticker(ticker)
            info = yt.info or {}

            if info.get("quoteType", "").upper() != "ETF":
                return []

            # Try funds_data first (newer yfinance)
            try:
                fd = yt.funds_data
                if fd and hasattr(fd, "top_holdings"):
                    th = fd.top_holdings
                    if th is not None and not getattr(th, "empty", True):
                        syms = [
                            str(idx).strip()
                            for idx in th.index
                            if str(idx).strip()
                        ]
                        if len(syms) >= 3:
                            return syms
            except Exception:
                pass

            # Fallback: info["holdings"]
            holdings = info.get("holdings", [])
            syms = [
                h["symbol"].strip()
                for h in holdings
                if isinstance(h, dict) and h.get("symbol")
            ]
            return syms if len(syms) >= 3 else []

        except Exception as e:
            logger.debug(f"[ConstituentResolver] ETF yfinance failed for {ticker}: {e}")
            return []

    # ── Strategy 2: FMP index endpoint (US only) ─────────────────────────────

    _FMP_INDEX_ENDPOINTS: dict[str, str] = {
        "^GSPC":  "sp500_constituent",
        "^SP500": "sp500_constituent",
        "^NDX":   "nasdaq_constituent",
        "^IXIC":  "nasdaq_constituent",
        "^DJI":   "dowjones_constituent",
    }

    def _from_fmp_index(self, ticker: str) -> list[str]:
        endpoint = self._FMP_INDEX_ENDPOINTS.get(ticker)
        if not endpoint:
            return []
        try:
            import requests
            url  = f"https://financialmodelingprep.com/api/v3/{endpoint}"
            resp = requests.get(url, params={"apikey": self._fmp_key}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            syms = [item["symbol"] for item in data if isinstance(item, dict) and item.get("symbol")]
            return syms if len(syms) >= 3 else []
        except Exception as e:
            logger.debug(f"[ConstituentResolver] FMP index failed for {ticker}: {e}")
            return []

    # ── Strategy 3: FMP ETF holder endpoint ──────────────────────────────────

    def _from_fmp_etf(self, ticker: str) -> list[str]:
        try:
            import requests
            url  = f"https://financialmodelingprep.com/api/v3/etf-holder/{ticker}"
            resp = requests.get(url, params={"apikey": self._fmp_key}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            syms = [item["asset"] for item in data if isinstance(item, dict) and item.get("asset")]
            return syms if len(syms) >= 3 else []
        except Exception as e:
            logger.debug(f"[ConstituentResolver] FMP ETF holder failed for {ticker}: {e}")
            return []

    # ── Strategy 4: Wikipedia scrape ─────────────────────────────────────────

    def _from_wikipedia(self, ticker: str) -> list[str]:
        mapping = WIKIPEDIA_MAP.get(ticker)
        if not mapping:
            return []

        article, suffix = mapping
        url = f"https://en.wikipedia.org/wiki/{article}"

        try:
            import io
            import pandas as pd
            import requests

            headers = {
                "User-Agent": (
                    "EquityBot/1.0 (financial research; "
                    "contact: equitybot@research.local)"
                )
            }
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()

            # io.StringIO avoids lxml treating the HTML string as a file path
            tables = pd.read_html(io.StringIO(resp.text))
            if not tables:
                return []

            tickers = _extract_tickers_from_tables(tables, suffix)
            return tickers if len(tickers) >= 3 else []

        except Exception as e:
            logger.debug(f"[ConstituentResolver] Wikipedia failed for {ticker}: {e}")
            return []


# ── Wikipedia parsing helpers ─────────────────────────────────────────────────

def _extract_tickers_from_tables(tables, suffix: str) -> list[str]:
    """
    Try each table to find one with a ticker-like column.
    Returns cleaned list with exchange suffix appended where needed.
    """
    for df in tables:
        # Normalise column names
        cols_lower = {str(c).lower().strip(): c for c in df.columns}

        # Find a column whose name looks like a ticker column
        ticker_col = None
        for lc, orig in cols_lower.items():
            if any(kw in lc for kw in _TICKER_COLS):
                ticker_col = orig
                break

        if ticker_col is None:
            continue

        raw = df[ticker_col].dropna().astype(str).tolist()
        cleaned = _clean_ticker_list(raw, suffix)
        if len(cleaned) >= 3:
            return cleaned

    # Second pass: look for a column whose values look like tickers
    for df in tables:
        for col in df.columns:
            vals = df[col].dropna().astype(str).tolist()
            sample = vals[:20]
            # Tickers are typically 1-6 uppercase chars, no spaces
            ticker_like = [
                v.strip() for v in sample
                if 1 <= len(v.strip()) <= 8
                and v.strip().replace(".", "").replace("-", "").isupper()
                and " " not in v.strip()
            ]
            if len(ticker_like) >= max(3, len(sample) * 0.4):
                cleaned = _clean_ticker_list(vals, suffix)
                if len(cleaned) >= 3:
                    return cleaned

    return []


def _clean_ticker_list(raw: list[str], suffix: str) -> list[str]:
    """
    Deduplicate, remove junk rows, and append exchange suffix.
    """
    seen:   set[str] = set()
    result: list[str] = []

    for v in raw:
        v = v.strip().upper()

        # Skip header repeats, footnotes, empty values
        if not v or v in ("TICKER", "SYMBOL", "CODE", "NAN", "-", "N/A"):
            continue
        # Must be plausibly a ticker: 1–10 chars, no spaces, no long strings
        if " " in v or len(v) > 10:
            continue
        # Strip footnote digits that Wikipedia sometimes appends (e.g. "AAPL[1]")
        v = v.split("[")[0].split("(")[0].strip()
        if not v or len(v) > 10:
            continue

        # Append exchange suffix only if ticker doesn't already have one
        final = v if ("." in v or not suffix) else f"{v}{suffix}"

        if final not in seen:
            seen.add(final)
            result.append(final)

    return result
