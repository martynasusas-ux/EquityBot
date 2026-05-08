"""
news_adapter.py — NewsAPI.org integration.

Fetches recent news headlines for any company or topic.
Free tier: 100 req/day, 1-month history.
Register at: https://newsapi.org

Used to enrich LLM analysis context with recent events — passed as
a string into the report-generator prompt, not stored in CompanyData.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class NewsAdapter:
    """
    Fetches recent news headlines from NewsAPI.org.

    Usage:
        adapter = NewsAdapter()
        articles = adapter.fetch_company_news("Wolters Kluwer", "WKL.AS")
        block = adapter.format_for_prompt(articles)
    """

    _BASE_URL = "https://newsapi.org/v2/everything"

    def __init__(self, api_key: str = ""):
        from config import NEWS_API_KEY
        self._key = api_key or NEWS_API_KEY

    # ── Public API ────────────────────────────────────────────────────────────

    def fetch_company_news(
        self,
        company_name: str,
        ticker: str,
        max_articles: int = 8,
    ) -> list[dict]:
        """
        Fetch recent news articles for a company from NewsAPI.

        Parameters
        ----------
        company_name : str
            Full company name (e.g. "Wolters Kluwer").
        ticker : str
            Exchange ticker (e.g. "WKL.AS").  Used as a secondary search term.
        max_articles : int
            Maximum number of articles to return (default 8, max 100).

        Returns
        -------
        list[dict]
            Each dict has keys: title, source, date, url, description.
            Returns an empty list on any failure.
        """
        if not self._key:
            logger.debug("[news] No NEWS_API_KEY set — skipping news fetch")
            return []

        try:
            import requests
        except ImportError:
            logger.warning("[news] requests not installed — cannot fetch news")
            return []

        # Strip exchange suffix for a cleaner ticker query (e.g. "WKL.AS" → "WKL")
        bare_ticker = ticker.split(".")[0] if ticker else ""
        query = f'"{company_name}"'
        if bare_ticker and bare_ticker.upper() != company_name.upper():
            query = f'"{company_name}" OR "{bare_ticker}"'

        params = {
            "q":          query,
            "language":   "en",
            "sortBy":     "publishedAt",
            "pageSize":   max(1, min(max_articles, 100)),
            "apiKey":     self._key,
        }

        try:
            resp = requests.get(
                self._BASE_URL,
                params=params,
                timeout=10,
                headers={"User-Agent": "EquityBot/1.0"},
            )
            resp.raise_for_status()
            payload = resp.json()

            if payload.get("status") != "ok":
                logger.warning(
                    f"[news] NewsAPI returned status={payload.get('status')}: "
                    f"{payload.get('message', 'unknown error')}"
                )
                return []

            articles = []
            for item in payload.get("articles", []):
                articles.append({
                    "title":       item.get("title") or "",
                    "source":      (item.get("source") or {}).get("name") or "",
                    "date":        (item.get("publishedAt") or "")[:10],  # YYYY-MM-DD
                    "url":         item.get("url") or "",
                    "description": item.get("description") or "",
                })

            logger.info(
                f"[news] Fetched {len(articles)} articles for "
                f"'{company_name}' / '{bare_ticker}'"
            )
            return articles

        except Exception as exc:
            logger.warning(f"[news] fetch_company_news failed for '{company_name}': {exc}")
            return []

    def format_for_prompt(self, articles: list[dict]) -> str:
        """
        Format a list of news articles as a plain-text block for LLM injection.

        Returns "" if the article list is empty.

        Example output
        --------------
        RECENT NEWS (last 30 days):
        1. [Reuters, 2024-03-15] Wolters Kluwer raises full-year guidance
           Company lifted its full-year organic growth forecast to 8-9%...
        2. [Bloomberg, 2024-03-10] ...
        """
        if not articles:
            return ""

        lines: list[str] = ["RECENT NEWS (last 30 days):"]
        for i, art in enumerate(articles, start=1):
            source_date = f"{art['source']}, {art['date']}" if art["source"] else art["date"]
            header = f"{i}. [{source_date}] {art['title']}"
            lines.append(header)
            if art.get("description"):
                # Indent description under the headline; trim to ~200 chars
                snippet = art["description"].strip()
                if len(snippet) > 200:
                    snippet = snippet[:197] + "..."
                lines.append(f"   {snippet}")

        return "\n".join(lines)
