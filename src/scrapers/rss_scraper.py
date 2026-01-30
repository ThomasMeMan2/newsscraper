"""RSS feed scraper."""

import logging
from datetime import datetime
from typing import Optional
from email.utils import parsedate_to_datetime

import feedparser

from .base import BaseScraper, ScraperResult
from ..config import SourceConfig

logger = logging.getLogger(__name__)


class RssScraper(BaseScraper):
    """Scraper for RSS feeds."""

    def __init__(
        self,
        source_config: SourceConfig,
        theme_keywords: list[str],
        min_delay: float = 2.0,
        max_delay: float = 5.0,
        last_processed_date: Optional[datetime] = None,
    ):
        super().__init__(source_config, theme_keywords, min_delay, max_delay, last_processed_date)

    def _parse_date(self, entry: dict) -> Optional[datetime]:
        """Parse publication date from RSS entry."""
        # Try different date fields
        for date_field in ['published', 'updated', 'created', 'pubDate']:
            if date_field in entry:
                date_str = entry[date_field]
                try:
                    # Try parsing as RFC 2822 format (standard RSS)
                    return parsedate_to_datetime(date_str)
                except (TypeError, ValueError):
                    pass

        # Try parsed date tuple
        for date_field in ['published_parsed', 'updated_parsed', 'created_parsed']:
            if date_field in entry and entry[date_field]:
                try:
                    return datetime(*entry[date_field][:6])
                except (TypeError, ValueError):
                    pass

        return None

    def _extract_content(self, entry: dict) -> str:
        """Extract content from RSS entry."""
        # Try content field first (often has full text)
        if 'content' in entry and entry['content']:
            content_list = entry['content']
            if isinstance(content_list, list) and len(content_list) > 0:
                return content_list[0].get('value', '')

        # Fall back to summary/description
        for field in ['summary', 'description']:
            if field in entry and entry[field]:
                return entry[field]

        return ""

    def _extract_link(self, entry: dict) -> Optional[str]:
        """Extract the article link from RSS entry."""
        # Direct link field
        if 'link' in entry and entry['link']:
            return entry['link']

        # Links array
        if 'links' in entry and entry['links']:
            for link in entry['links']:
                if link.get('rel') == 'alternate' or link.get('type', '').startswith('text/html'):
                    return link.get('href')
            # Fall back to first link
            if entry['links']:
                return entry['links'][0].get('href')

        # GUID as fallback (sometimes it's the URL)
        if 'id' in entry and entry['id'] and entry['id'].startswith('http'):
            return entry['id']

        return None

    async def scrape(self) -> ScraperResult:
        """Scrape the RSS feed."""
        result = ScraperResult()
        url = self.source_config.url

        logger.info(f"Scraping RSS feed: {self.source_config.name} ({url})")

        try:
            # Fetch RSS content
            content = await self._fetch_url(url)

            # Parse feed
            feed = feedparser.parse(content)

            if feed.bozo and feed.bozo_exception:
                # Feed had parsing issues but may still have entries
                logger.warning(f"RSS feed parsing warning: {feed.bozo_exception}")

            if not feed.entries:
                logger.warning(f"No entries found in RSS feed: {url}")
                return result

            logger.info(f"Found {len(feed.entries)} entries in feed")

            for entry in feed.entries:
                try:
                    # Extract link
                    link = self._extract_link(entry)
                    if not link:
                        logger.debug(f"Skipping entry without link: {entry.get('title', 'Unknown')}")
                        result.skipped_count += 1
                        continue

                    # Extract title
                    title = entry.get('title', '').strip()
                    if not title:
                        logger.debug(f"Skipping entry without title: {link}")
                        result.skipped_count += 1
                        continue

                    # Extract content
                    content = self._extract_content(entry)

                    # Extract publication date
                    pub_date = self._parse_date(entry)

                    # Check if after last processed date
                    if not self.is_after_last_processed(pub_date):
                        logger.debug(f"Skipping old entry: {title} ({pub_date})")
                        result.skipped_count += 1
                        continue

                    # Check theme relevance
                    combined_text = f"{title} {content}"
                    if not self.matches_theme(combined_text):
                        logger.debug(f"Skipping non-matching entry: {title}")
                        result.skipped_count += 1
                        continue

                    # Create news item
                    item = self.create_news_item(
                        url=link,
                        title=title,
                        pub_date=pub_date.date() if pub_date else None,
                        content=content,
                    )

                    result.items.append(item)
                    logger.debug(f"Added RSS item: {title}")

                except Exception as e:
                    error_msg = f"Error processing RSS entry: {e}"
                    logger.error(error_msg)
                    result.errors.append(error_msg)

            logger.info(
                f"RSS scrape complete: {len(result.items)} items, "
                f"{result.skipped_count} skipped, {len(result.errors)} errors"
            )

        except Exception as e:
            error_msg = f"Failed to scrape RSS feed {url}: {e}"
            logger.error(error_msg)
            result.errors.append(error_msg)
            result.success = False

        return result
