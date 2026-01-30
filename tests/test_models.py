"""Tests for data models."""

import pytest
from datetime import date

from src.storage.models import NewsItem, SourceState


class TestNewsItem:
    """Tests for NewsItem model."""

    def test_create_news_item(self):
        """Test basic NewsItem creation."""
        item = NewsItem(
            source_url="https://example.com/article",
            title="Test Article",
        )

        assert item.source_url == "https://example.com/article"
        assert item.title == "Test Article"
        assert item.url_hash != ""  # Should be auto-generated
        assert len(item.url_hash) == 8

    def test_url_hash_generation(self):
        """Test that URL hash is consistent."""
        url = "https://example.com/test"
        item1 = NewsItem(source_url=url)
        item2 = NewsItem(source_url=url)

        assert item1.url_hash == item2.url_hash

    def test_different_urls_different_hashes(self):
        """Test that different URLs produce different hashes."""
        item1 = NewsItem(source_url="https://example.com/article1")
        item2 = NewsItem(source_url="https://example.com/article2")

        assert item1.url_hash != item2.url_hash

    def test_related_people_str(self):
        """Test related_people_str property."""
        item = NewsItem(
            source_url="https://example.com",
            related_people=["John Doe", "Jane Smith"]
        )

        assert item.related_people_str == "John Doe, Jane Smith"

    def test_related_people_str_empty(self):
        """Test related_people_str with empty list."""
        item = NewsItem(source_url="https://example.com")
        assert item.related_people_str == ""

    def test_needs_enrichment_other_type(self):
        """Test that 'Other' type items don't need enrichment."""
        item = NewsItem(
            source_url="https://example.com",
            news_type="Other",
            news_type_confidence="low",
        )

        assert item.needs_enrichment is False

    def test_needs_enrichment_high_confidence(self):
        """Test that all-high-confidence items don't need enrichment."""
        item = NewsItem(
            source_url="https://example.com",
            news_type="Funding",
            news_type_confidence="high",
            region_confidence="high",
            related_people_confidence="high",
        )

        assert item.needs_enrichment is False

    def test_needs_enrichment_low_confidence(self):
        """Test that low-confidence items need enrichment."""
        item = NewsItem(
            source_url="https://example.com",
            news_type="Funding",
            news_type_confidence="low",
        )

        assert item.needs_enrichment is True

    def test_needs_enrichment_max_attempts(self):
        """Test that max attempts blocks enrichment."""
        item = NewsItem(
            source_url="https://example.com",
            news_type="Funding",
            news_type_confidence="low",
            enrichment_attempts=2,
        )

        assert item.needs_enrichment is False

    def test_to_dict(self):
        """Test conversion to dictionary."""
        item = NewsItem(
            source_url="https://example.com",
            title="Test",
            news_type="Funding",
            pub_date=date(2024, 1, 15),
            related_people=["John Doe"],
        )

        d = item.to_dict()

        assert d["source_url"] == "https://example.com"
        assert d["title"] == "Test"
        assert d["news_type"] == "Funding"
        assert d["pub_date"] == "2024-01-15"
        assert d["related_people"] == "John Doe"

    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            "url_hash": "abc12345",
            "source_url": "https://example.com",
            "source_type": "rss",
            "title": "Test Article",
            "pub_date": "2024-01-15",
            "news_type": "Funding",
            "related_people": "John Doe, Jane Smith",
        }

        item = NewsItem.from_dict(data)

        assert item.url_hash == "abc12345"
        assert item.source_url == "https://example.com"
        assert item.title == "Test Article"
        assert item.pub_date == date(2024, 1, 15)
        assert item.related_people == ["John Doe", "Jane Smith"]


class TestSourceState:
    """Tests for SourceState model."""

    def test_create_source_state(self):
        """Test basic SourceState creation."""
        state = SourceState(source_url="https://example.com/feed")

        assert state.source_url == "https://example.com/feed"
        assert state.consecutive_failures == 0

    def test_to_dict(self):
        """Test conversion to dictionary."""
        from datetime import datetime

        state = SourceState(
            source_url="https://example.com/feed",
            last_processed_date=datetime(2024, 1, 15, 10, 30),
            consecutive_failures=2,
        )

        d = state.to_dict()

        assert d["source_url"] == "https://example.com/feed"
        assert "2024-01-15" in d["last_processed_date"]
        assert d["consecutive_failures"] == 2
