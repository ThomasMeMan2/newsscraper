"""Enrichment pipeline for news items with low confidence."""

import asyncio
import logging
from typing import Optional

import httpx

from .classifier import ClassifierWithFallback
from ..scrapers.web_scraper import ArticleScraper
from ..storage.models import NewsItem
from ..config import SourceConfig

logger = logging.getLogger(__name__)


class Enricher:
    """Enriches news items by fetching full content and performing web searches."""

    def __init__(
        self,
        classifier: ClassifierWithFallback,
        max_attempts: int = 2,
        min_delay: float = 2.0,
        max_delay: float = 5.0,
    ):
        self.classifier = classifier
        self.max_attempts = max_attempts
        self.min_delay = min_delay
        self.max_delay = max_delay

    def should_enrich(self, item: NewsItem) -> bool:
        """Determine if an item needs enrichment."""
        # Skip items classified as Other
        if item.news_type == "Other":
            return False

        # Skip if max attempts reached
        if item.enrichment_attempts >= self.max_attempts:
            return False

        # Enrich if any confidence is not high
        return any([
            item.news_type_confidence != "high",
            item.region_confidence != "high",
            item.related_people_confidence != "high"
        ])

    async def _fetch_full_article(self, url: str) -> Optional[str]:
        """Fetch full article content."""
        # Create a dummy source config for the article scraper
        dummy_config = SourceConfig(
            url=url,
            type="website",
            name="Article Fetch",
        )

        async with ArticleScraper(dummy_config, [], self.min_delay, self.max_delay) as scraper:
            return await scraper.scrape_article(url)

    async def _web_search(self, query: str) -> Optional[str]:
        """
        Perform a web search for additional context.
        Note: This is a placeholder - in production you'd use a search API.
        """
        # For now, we'll use DuckDuckGo's HTML search as a basic option
        # In production, consider using:
        # - Google Custom Search API
        # - Bing Search API
        # - SerpAPI
        # - Or a dedicated news API

        try:
            search_url = f"https://html.duckduckgo.com/html/?q={query}"

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    search_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                    },
                    follow_redirects=True,
                )

                if response.status_code == 200:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(response.text, 'lxml')

                    # Extract search result snippets
                    results = []
                    for result in soup.select('.result__snippet')[:3]:
                        text = result.get_text(strip=True)
                        if text:
                            results.append(text)

                    if results:
                        return "\n".join(results)

        except Exception as e:
            logger.warning(f"Web search failed for '{query}': {e}")

        return None

    async def enrich_item(self, item: NewsItem) -> NewsItem:
        """Enrich a single item."""
        if not self.should_enrich(item):
            return item

        logger.info(f"Enriching: {item.title[:50]}... (attempt {item.enrichment_attempts + 1})")

        item.enrichment_attempts += 1
        additional_content = ""

        # Phase 1: Try to fetch full article
        if item.enrichment_source == "none":
            full_content = await self._fetch_full_article(item.source_url)
            if full_content and len(full_content) > len(item.raw_content):
                additional_content = full_content
                item.enrichment_source = "scrape"
                logger.debug(f"Fetched full article: {len(full_content)} chars")

        # Phase 2: If still low confidence after scrape, try web search
        if item.enrichment_source == "scrape" and self.should_enrich(item):
            # Build search query
            search_terms = []
            if item.company_name:
                search_terms.append(item.company_name)
            if item.related_people:
                search_terms.extend(item.related_people[:2])

            if search_terms:
                query = " ".join(search_terms) + " Belgium startup"
                search_results = await self._web_search(query)

                if search_results:
                    additional_content += f"\n\nZoekresultaten:\n{search_results}"
                    item.enrichment_source = "search"
                    logger.debug(f"Added search results for: {query}")

        # Re-classify with additional content
        if additional_content:
            item = self.classifier.reclassify_with_context(item, additional_content)

        return item

    async def enrich_batch(self, items: list[NewsItem]) -> list[NewsItem]:
        """Enrich a batch of items that need enrichment."""
        items_to_enrich = [item for item in items if self.should_enrich(item)]

        if not items_to_enrich:
            logger.info("No items need enrichment")
            return items

        logger.info(f"Enriching {len(items_to_enrich)} items")

        enriched = []
        for item in items_to_enrich:
            enriched_item = await self.enrich_item(item)
            enriched.append(enriched_item)
            # Small delay between enrichments
            await asyncio.sleep(1)

        # Merge enriched items back
        enriched_map = {item.url_hash: item for item in enriched}
        result = []
        for item in items:
            if item.url_hash in enriched_map:
                result.append(enriched_map[item.url_hash])
            else:
                result.append(item)

        return result
