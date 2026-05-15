"""
screener_eodhd.py — EODHD-only index screener.

Given an index ticker (Yahoo format like ^GSPC), fetch its constituents
from EODHD `/fundamentals/{INDEX}.INDX → Components`, then bulk-fetch
fundamentals for each component (cached aggressively) and return the
top-N sorted by the requested metric.

Usage:
    from data_sources.screener_eodhd import screen_index
    rows = screen_index("^GSPC", sort_by="market_cap",
                        sort_dir="desc", limit=10)
    # → [{"ticker": "AAPL", "name": "Apple Inc.", "market_cap": 3.45e12, ...}, ...]
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
import json

import requests

from config import EODHD_API_KEY, REQUEST_HEADERS, CACHE_DIR

logger = logging.getLogger(__name__)

EODHD_BASE = "https://eodhistoricaldata.com/api"
_DELAY = 0.15

# ── Persistent cache for slow operations ─────────────────────────────────────
# `/fundamentals` for each constituent is ~500 calls for SP500. We cache the
# WHOLE screen result on disk for 24h so the second-and-onward run is instant.
_SCREEN_CACHE_DIR = CACHE_DIR / "screen"
_SCREEN_CACHE_DIR.mkdir(exist_ok=True, parents=True)
_SCREEN_TTL_HOURS = 24


def _screen_cache_path(idx_yf: str, sort_by: str, sort_dir: str, limit: int) -> Path:
    safe = idx_yf.replace("^", "_").replace(".", "_")
    return _SCREEN_CACHE_DIR / f"{safe}__{sort_by}__{sort_dir}__{limit}.json"


def _load_screen_cache(p: Path) -> Optional[list[dict]]:
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        cached_at = datetime.fromisoformat(raw.get("cached_at", ""))
        age_h = (datetime.utcnow() - cached_at).total_seconds() / 3600
        if age_h > _SCREEN_TTL_HOURS:
            return None
        return raw.get("rows") or []
    except Exception:
        return None


def _save_screen_cache(p: Path, rows: list[dict]) -> None:
    try:
        p.write_text(
            json.dumps(
                {"cached_at": datetime.utcnow().isoformat(), "rows": rows},
                indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"[screener] cache save failed: {e}")


# ── EODHD GET helper ─────────────────────────────────────────────────────────

def _eodhd_get(path: str, params: dict | None = None,
               timeout: int = 30) -> Optional[Any]:
    if not EODHD_API_KEY:
        return None
    p = {"api_token": EODHD_API_KEY, "fmt": "json"}
    if params:
        p.update(params)
    try:
        time.sleep(_DELAY)
        r = requests.get(f"{EODHD_BASE}{path}", params=p,
                         headers=REQUEST_HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


def _to_float(v) -> Optional[float]:
    if v is None or v == "" or v == "NA":
        return None
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None


# ── Ticker conversion: index Yahoo → EODHD ───────────────────────────────────

def _yf_index_to_eodhd(yf_ticker: str) -> str:
    """
    Convert a Yahoo index ticker (^GSPC, ^GDAXI, ^N225) to EODHD format
    ({SYMBOL}.INDX). Already-EODHD-formatted strings pass through.
    """
    t = yf_ticker.strip().upper()
    if t.endswith(".INDX"):
        return t
    if t.startswith("^"):
        return t[1:] + ".INDX"
    return t + ".INDX"


# Reverse mapping for component conversion (built on demand to avoid circular imports)
def _component_to_yf(code: str, exchange: str) -> str:
    """Component (Code, Exchange) from EODHD → Yahoo Finance ticker."""
    from data_sources.eodhd_adapter import _YF_TO_EODHD
    eodhd_to_yf = {v: k for k, v in _YF_TO_EODHD.items()}
    code = (code or "").strip().upper()
    exch = (exchange or "").strip().upper()
    if not code:
        return ""
    if exch in ("", "US"):
        return code
    eodhd_suffix = f".{exch}"
    yf_suffix = eodhd_to_yf.get(eodhd_suffix, eodhd_suffix)
    return f"{code}{yf_suffix}"


# ── Constituent fetch ────────────────────────────────────────────────────────

def fetch_index_components(yf_index: str) -> list[dict]:
    """
    Return the constituent list of an index from EODHD.

    Tries the standard `{SYMBOL}.INDX` ticker first, then a small set of
    fallback exchange codes for indices EODHD lists under a regional
    exchange instead of the global INDX one.

    Each entry is:
        {"yf_ticker": "AAPL", "eodhd_code": "AAPL", "eodhd_exchange": "US",
         "name": "Apple Inc.", "sector": "Technology", ...}
    """
    # Build candidate EODHD tickers to try in order
    primary = _yf_index_to_eodhd(yf_index)
    candidates = [primary]
    # Strip the suffix to derive the bare symbol — used to construct fallbacks
    base = primary.split(".")[0] if "." in primary else primary

    # Known regional exchange codes EODHD uses for non-INDX indices
    for alt_suffix in (".WAR", ".LSE", ".XETRA", ".PA",
                       ".VI", ".SW", ".AS", ".MI", ".HE", ".ST"):
        cand = f"{base}{alt_suffix}"
        if cand not in candidates:
            candidates.append(cand)

    for cand in candidates:
        fund = _eodhd_get(f"/fundamentals/{cand}")
        if not isinstance(fund, dict):
            continue
        comps = fund.get("Components") or {}
        if not isinstance(comps, dict) or not comps:
            continue
        out: list[dict] = []
        for _, c in comps.items():
            if not isinstance(c, dict):
                continue
            code = c.get("Code") or ""
            exch = c.get("Exchange") or ""
            if not code:
                continue
            yf_ticker = _component_to_yf(code, exch)
            if not yf_ticker:
                continue
            out.append({
                "yf_ticker":      yf_ticker,
                "eodhd_code":     code,
                "eodhd_exchange": exch,
                "name":           c.get("Name") or "",
                "sector":         c.get("Sector") or "",
                "industry":       c.get("Industry") or "",
            })
        if out:
            logger.info(f"[screener] resolved {yf_index} → {cand} ({len(out)} components)")
            return out

    logger.warning(f"[screener] no components found for {yf_index} "
                   f"(tried: {', '.join(candidates)})")
    return []


# ── Per-ticker metric fetch ──────────────────────────────────────────────────

_METRIC_TO_PATH = {
    # name → (fundamentals.Section, field)
    "market_cap":  ("Highlights", "MarketCapitalizationMln"),
    "pe_ratio":    ("Highlights", "PERatio"),
    "roe":         ("Highlights", "ReturnOnEquityTTM"),
    "ebit_margin": ("Highlights", "OperatingMarginTTM"),
    "net_margin":  ("Highlights", "ProfitMargin"),
    "revenue":     ("Highlights", "RevenueTTM"),
    "div_yield":   ("Highlights", "DividendYield"),
    "ev_ebit":     ("Valuation",  "EnterpriseValueEbitda"),
}


def _fetch_one_metric(eodhd_ticker: str, sort_by: str) -> dict:
    """
    Pull a compact snapshot for one ticker (5 main metrics).
    Returns a dict that also includes the requested sort metric.
    """
    fund = _eodhd_get(f"/fundamentals/{eodhd_ticker}") or {}
    rt   = _eodhd_get(f"/real-time/{eodhd_ticker}") or {}
    g  = fund.get("General")    or {}
    h  = fund.get("Highlights") or {}
    v  = fund.get("Valuation")  or {}

    price = _to_float(rt.get("close"))
    if price is None or (isinstance(price, float) and price < 0):
        price = _to_float(rt.get("previousClose"))

    mc_mln = _to_float(h.get("MarketCapitalizationMln"))
    market_cap = (mc_mln * 1e6) if mc_mln is not None else _to_float(h.get("MarketCapitalization"))

    snap = {
        "name":         g.get("Name") or "",
        "currency":     g.get("CurrencyCode") or "",
        "sector":       g.get("Sector") or "",
        "price":        price,
        "market_cap":   market_cap,
        "pe_ratio":     _to_float(h.get("PERatio")),
        "roe":          _to_float(h.get("ReturnOnEquityTTM")),
        "ebit_margin":  _to_float(h.get("OperatingMarginTTM")),
        "net_margin":   _to_float(h.get("ProfitMargin")),
        "revenue":      _to_float(h.get("RevenueTTM")),
        "div_yield":    _to_float(h.get("DividendYield")),
        "fcf_yield":    None,   # not directly in Highlights
        "ev_ebit":      _to_float(v.get("EnterpriseValueEbitda")),
    }
    return snap


# ── Public entry: screen ─────────────────────────────────────────────────────

def screen_index(
    yf_index: str,
    sort_by: str = "market_cap",
    sort_dir: str = "desc",
    limit: int = 10,
    *,
    max_universe: int = 600,
    progress_cb=None,
) -> list[dict]:
    """
    Top-N constituents of an index by a chosen metric.

    Returns a list of dicts:
        {
          "rank": 1,
          "ticker": "AAPL",
          "name": "Apple Inc.",
          "sector": "Technology",
          "price": 234.10, "currency": "USD",
          "market_cap": 3.45e12,
          "pe_ratio": 32.1, "roe": 1.58, "ebit_margin": 0.31,
          "net_margin": 0.25, "revenue": 391.0e9, "div_yield": 0.005,
          "ev_ebit": 28.4,
          "sort_value": 3.45e12,
        }

    Cached to cache/screen/{index}__{sort_by}__{dir}__{limit}.json for 24h.
    """
    sort_by  = sort_by  or "market_cap"
    sort_dir = sort_dir or "desc"

    cache_path = _screen_cache_path(yf_index, sort_by, sort_dir, limit)
    cached = _load_screen_cache(cache_path)
    if cached is not None:
        logger.info(f"[screener] cache hit for {yf_index} / {sort_by}")
        return cached

    # ── Fetch constituents ───────────────────────────────────────────────────
    components = fetch_index_components(yf_index)
    if not components:
        logger.warning(f"[screener] no components for {yf_index}")
        return []

    # Truncate to keep API spend predictable on very large indices
    components = components[:max_universe]
    total = len(components)
    logger.info(f"[screener] {yf_index}: {total} components — fetching metrics...")

    # ── Fetch metric per ticker ──────────────────────────────────────────────
    rows: list[dict] = []
    for i, c in enumerate(components):
        yf_t   = c["yf_ticker"]
        eodhd_t = f"{c['eodhd_code']}.{c['eodhd_exchange']}" if c['eodhd_exchange'] else f"{c['eodhd_code']}.US"
        try:
            snap = _fetch_one_metric(eodhd_t, sort_by)
        except Exception as e:
            logger.debug(f"[screener] skip {yf_t}: {e}")
            continue
        sort_val = snap.get(sort_by)
        if sort_val is None:
            continue
        rows.append({
            "ticker":   yf_t,
            "name":     snap.get("name") or c.get("name") or yf_t,
            "sector":   snap.get("sector") or c.get("sector") or "",
            "currency": snap.get("currency") or "",
            "price":        snap.get("price"),
            "market_cap":   snap.get("market_cap"),
            "pe_ratio":     snap.get("pe_ratio"),
            "roe":          snap.get("roe"),
            "ebit_margin":  snap.get("ebit_margin"),
            "net_margin":   snap.get("net_margin"),
            "revenue":      snap.get("revenue"),
            "div_yield":    snap.get("div_yield"),
            "ev_ebit":      snap.get("ev_ebit"),
            "sort_value":   sort_val,
        })
        if progress_cb and (i % 20 == 0):
            try:
                progress_cb(i, total)
            except Exception:
                pass

    if progress_cb:
        try:
            progress_cb(total, total)
        except Exception:
            pass

    # ── Sort + cap ───────────────────────────────────────────────────────────
    reverse = (sort_dir or "desc").lower() != "asc"
    rows.sort(key=lambda r: r.get("sort_value") or float("-inf"), reverse=reverse)
    top = rows[:limit]
    for i, r in enumerate(top, start=1):
        r["rank"] = i

    _save_screen_cache(cache_path, top)
    return top
