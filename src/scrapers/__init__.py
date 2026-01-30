"""Scrapers module for fetching news from various sources."""

from .base import BaseScraper, ScraperResult
from .rss_scraper import RssScraper
from .web_scraper import WebScraper

__all__ = ["BaseScraper", "ScraperResult", "RssScraper", "WebScraper"]
