"""Website scraper for HTML pages."""

import logging
import re
from datetime import datetime, date
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from .base import BaseScraper, ScraperResult
from ..config import SourceConfig

logger = logging.getLogger(__name__)


# Common date patterns in Belgian/Dutch websites
DATE_PATTERNS = [
    r'(\d{1,2}[-/]\d{1,2}[-/]\d{4})',  # DD-MM-YYYY or DD/MM/YYYY
    r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})',  # YYYY-MM-DD
    r'(\d{1,2}\s+(?:jan|feb|mrt|apr|mei|jun|jul|aug|sep|okt|nov|dec)[a-z]*\.?\s+\d{4})',  # Dutch months
    r'(\d{1,2}\s+(?:januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december)\s+\d{4})',
]

# Dutch month name mapping
DUTCH_MONTHS = {
    'januari': 1, 'jan': 1,
    'februari': 2, 'feb': 2,
    'maart': 3, 'mrt': 3,
    'april': 4, 'apr': 4,
    'mei': 5,
    'juni': 6, 'jun': 6,
    'juli': 7, 'jul': 7,
    'augustus': 8, 'aug': 8,
    'september': 9, 'sep': 9,
    'oktober': 10, 'okt': 10,
    'november': 11, 'nov': 11,
    'december': 12, 'dec': 12,
}


class WebScraper(BaseScraper):
    """Scraper for website article listings."""

    def __init__(
        self,
        source_config: SourceConfig,
        theme_keywords: list[str],
        min_delay: float = 2.0,
        max_delay: float = 5.0,
        last_processed_date: Optional[datetime] = None,
    ):
        super().__init__(source_config, theme_keywords, min_delay, max_delay, last_processed_date)
        self.config = source_config.config or {}
        self.base_url = self._get_base_url(source_config.url)

    def _get_base_url(self, url: str) -> str:
        """Extract base URL for resolving relative links."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _resolve_url(self, url: str) -> str:
        """Resolve potentially relative URL to absolute."""
        if url.startswith('http'):
            return url
        return urljoin(self.base_url, url)

    def _parse_dutch_date(self, date_str: str) -> Optional[date]:
        """Parse date string with Dutch month names."""
        date_str = date_str.lower().strip()

        # Replace Dutch month names with numbers for parsing
        for dutch, num in DUTCH_MONTHS.items():
            if dutch in date_str:
                # Replace month name with standard format
                date_str = re.sub(
                    rf'\b{dutch}\.?\b',
                    f'{num:02d}',
                    date_str
                )
                break

        try:
            # Try dateutil parser with dayfirst for European format
            parsed = date_parser.parse(date_str, dayfirst=True, fuzzy=True)
            return parsed.date()
        except (ValueError, TypeError):
            pass

        return None

    def _extract_date(self, element) -> Optional[date]:
        """Extract date from an HTML element or its text."""
        if not element:
            return None

        # Check for datetime attribute (common in <time> elements)
        if hasattr(element, 'get'):
            datetime_attr = element.get('datetime')
            if datetime_attr:
                try:
                    return date_parser.parse(datetime_attr).date()
                except (ValueError, TypeError):
                    pass

        # Get text content
        text = element.get_text() if hasattr(element, 'get_text') else str(element)
        text = text.strip()

        # Try date patterns
        for pattern in DATE_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                date_str = match.group(1)
                parsed = self._parse_dutch_date(date_str)
                if parsed:
                    return parsed

        # Try generic parsing
        return self._parse_dutch_date(text)

    def _extract_article_content(self, soup: BeautifulSoup, article_element) -> str:
        """Extract text content from an article element."""
        # Try to find description/excerpt
        for selector in ['.excerpt', '.summary', '.description', 'p', '.intro', '.lead']:
            desc = article_element.select_one(selector)
            if desc:
                return desc.get_text(strip=True)

        # Fall back to full text (limited)
        text = article_element.get_text(strip=True)
        return text[:500] if len(text) > 500 else text

    def _find_articles(self, soup: BeautifulSoup) -> list:
        """Find article elements on the page."""
        selectors = self.config.get('article_selector', 'article').split(', ')

        articles = []
        for selector in selectors:
            found = soup.select(selector.strip())
            articles.extend(found)

        # Deduplicate while preserving order
        seen = set()
        unique_articles = []
        for article in articles:
            article_id = id(article)
            if article_id not in seen:
                seen.add(article_id)
                unique_articles.append(article)

        return unique_articles

    def _extract_link_and_title(self, article) -> tuple[Optional[str], Optional[str]]:
        """Extract link and title from an article element."""
        link_selectors = self.config.get('link_selector', 'a').split(', ')
        title_selectors = self.config.get('title_selector', 'h2, h3').split(', ')

        link = None
        title = None

        # Find link
        for selector in link_selectors:
            link_elem = article.select_one(selector.strip())
            if link_elem:
                href = link_elem.get('href')
                if href:
                    link = self._resolve_url(href)
                    # Also try to get title from link
                    if not title:
                        title = link_elem.get_text(strip=True)
                    break

        # Find title (may override link text)
        for selector in title_selectors:
            title_elem = article.select_one(selector.strip())
            if title_elem:
                title_text = title_elem.get_text(strip=True)
                if title_text:
                    title = title_text
                    # Also try to get link from title element
                    if not link:
                        link_in_title = title_elem.find('a')
                        if link_in_title and link_in_title.get('href'):
                            link = self._resolve_url(link_in_title['href'])
                    break

        return link, title

    def _find_next_page(self, soup: BeautifulSoup) -> Optional[str]:
        """Find link to next page for pagination."""
        pagination_selectors = self.config.get('pagination_selector', '.next a').split(', ')

        for selector in pagination_selectors:
            next_link = soup.select_one(selector.strip())
            if next_link:
                href = next_link.get('href')
                if href:
                    return self._resolve_url(href)

        return None

    async def _scrape_page(self, url: str) -> tuple[list, Optional[str]]:
        """Scrape a single page. Returns (items, next_page_url)."""
        items = []

        html = await self._fetch_url(url)
        soup = BeautifulSoup(html, 'lxml')

        articles = self._find_articles(soup)
        logger.debug(f"Found {len(articles)} article elements on {url}")

        for article in articles:
            try:
                link, title = self._extract_link_and_title(article)

                if not link or not title:
                    continue

                # Extract date
                date_selectors = self.config.get('date_selector', 'time, .date').split(', ')
                pub_date = None
                for selector in date_selectors:
                    date_elem = article.select_one(selector.strip())
                    if date_elem:
                        pub_date = self._extract_date(date_elem)
                        if pub_date:
                            break

                # Check date filter
                if not self.is_after_last_processed(pub_date):
                    continue

                # Extract content/excerpt
                content = self._extract_article_content(soup, article)

                # Check theme relevance
                combined_text = f"{title} {content}"
                if not self.matches_theme(combined_text):
                    continue

                item = self.create_news_item(
                    url=link,
                    title=title,
                    pub_date=pub_date,
                    content=content,
                )
                items.append(item)

            except Exception as e:
                logger.warning(f"Error extracting article: {e}")
                continue

        # Find next page
        next_page = self._find_next_page(soup)

        return items, next_page

    async def scrape(self) -> ScraperResult:
        """Scrape the website."""
        result = ScraperResult()
        url = self.source_config.url
        max_pages = self.config.get('max_pages', 3)

        logger.info(f"Scraping website: {self.source_config.name} ({url})")

        current_url = url
        page_count = 0
        seen_urls = set()

        try:
            while current_url and page_count < max_pages:
                # Avoid duplicate pages
                if current_url in seen_urls:
                    break
                seen_urls.add(current_url)

                logger.debug(f"Scraping page {page_count + 1}: {current_url}")

                try:
                    items, next_page = await self._scrape_page(current_url)
                    result.items.extend(items)

                    page_count += 1

                    # Apply delay before next page
                    if next_page and page_count < max_pages:
                        await self._delay()
                        current_url = next_page
                    else:
                        current_url = None

                except Exception as e:
                    error_msg = f"Error scraping page {current_url}: {e}"
                    logger.error(error_msg)
                    result.errors.append(error_msg)
                    break

            logger.info(
                f"Website scrape complete: {len(result.items)} items from "
                f"{page_count} pages, {len(result.errors)} errors"
            )

        except Exception as e:
            error_msg = f"Failed to scrape website {url}: {e}"
            logger.error(error_msg)
            result.errors.append(error_msg)
            result.success = False

        return result


class ArticleScraper(BaseScraper):
    """Scraper for fetching full article content (used in enrichment)."""

    async def scrape_article(self, url: str) -> Optional[str]:
        """Fetch and extract main content from an article page."""
        try:
            html = await self._fetch_url(url)
            soup = BeautifulSoup(html, 'lxml')

            # Remove unwanted elements
            for tag in soup.find_all(['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe']):
                tag.decompose()

            # Try to find main content area
            content_selectors = [
                'article',
                '[role="main"]',
                '.article-content',
                '.post-content',
                '.entry-content',
                '.content-body',
                'main',
                '.main-content',
            ]

            content = None
            for selector in content_selectors:
                content_elem = soup.select_one(selector)
                if content_elem:
                    content = content_elem.get_text(separator=' ', strip=True)
                    break

            # Fall back to body
            if not content:
                body = soup.find('body')
                if body:
                    content = body.get_text(separator=' ', strip=True)

            if content:
                # Clean up whitespace
                content = re.sub(r'\s+', ' ', content)
                # Limit length
                return content[:5000] if len(content) > 5000 else content

            return None

        except Exception as e:
            logger.error(f"Failed to scrape article {url}: {e}")
            return None

    async def scrape(self) -> ScraperResult:
        """Not used for article scraper."""
        return ScraperResult()
