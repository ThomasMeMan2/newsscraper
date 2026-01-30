"""Base scraper class and common utilities."""

import asyncio
import random
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from ..config import SourceConfig
from ..storage.models import NewsItem

logger = logging.getLogger(__name__)


# User agents for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


@dataclass
class ScraperResult:
    """Result from a scraping operation."""
    items: list[NewsItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped_count: int = 0  # Items skipped due to theme filter or date
    success: bool = True


class BaseScraper(ABC):
    """Abstract base class for all scrapers."""

    def __init__(
        self,
        source_config: SourceConfig,
        theme_keywords: list[str],
        min_delay: float = 2.0,
        max_delay: float = 5.0,
        last_processed_date: Optional[datetime] = None,
    ):
        self.source_config = source_config
        self.theme_keywords = [kw.lower() for kw in theme_keywords]
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.last_processed_date = last_processed_date
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        """Async context manager entry."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            headers=self._get_headers(),
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _get_headers(self) -> dict:
        """Get HTTP headers with rotated user agent."""
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "nl-BE,nl;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

    async def _delay(self):
        """Apply random delay between requests."""
        delay = random.uniform(self.min_delay, self.max_delay)
        await asyncio.sleep(delay)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException)),
    )
    async def _fetch_url(self, url: str) -> str:
        """Fetch URL content with retry logic."""
        if not self._client:
            raise RuntimeError("Scraper must be used as async context manager")

        # Rotate user agent on each request
        headers = self._get_headers()
        response = await self._client.get(url, headers=headers)
        response.raise_for_status()
        return response.text

    def matches_theme(self, text: str) -> bool:
        """Check if text matches any theme keyword."""
        if not text:
            return False
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in self.theme_keywords)

    def is_after_last_processed(self, pub_date: Optional[date]) -> bool:
        """Check if publication date is after last processed date."""
        if not self.last_processed_date:
            return True
        if not pub_date:
            return True  # Include items without dates
        # Convert to date if datetime
        if isinstance(pub_date, datetime):
            pub_date = pub_date.date()
        last_date = self.last_processed_date.date() if isinstance(self.last_processed_date, datetime) else self.last_processed_date
        return pub_date >= last_date

    def clean_text(self, text: str) -> str:
        """Clean and normalize text."""
        if not text:
            return ""
        # Remove extra whitespace
        text = re.sub(r'\s+', ' ', text)
        # Remove HTML entities
        text = re.sub(r'&\w+;', ' ', text)
        return text.strip()

    def create_news_item(
        self,
        url: str,
        title: str,
        pub_date: Optional[date] = None,
        content: str = "",
    ) -> NewsItem:
        """Create a NewsItem with source metadata."""
        return NewsItem(
            source_url=url,
            source_type=self.source_config.type,
            source_name=self.source_config.name,
            title=self.clean_text(title),
            pub_date=pub_date,
            raw_content=self.clean_text(content),
        )

    @abstractmethod
    async def scrape(self) -> ScraperResult:
        """Perform the scraping operation. Must be implemented by subclasses."""
        pass
