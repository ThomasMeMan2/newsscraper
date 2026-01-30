"""Tests for database operations."""

import pytest
import tempfile
from pathlib import Path
from datetime import date, datetime

from src.storage.database import Database
from src.storage.models import NewsItem, SourceState


class TestDatabase:
    """Tests for Database class."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            db = Database(db_path)
            yield db

    def test_database_creation(self, temp_db):
        """Test database is created successfully."""
        assert temp_db.db_path.exists()

    def test_url_hash_exists_false(self, temp_db):
        """Test url_hash_exists returns False for new hash."""
        assert temp_db.url_hash_exists("abc12345") is False

    def test_upsert_and_retrieve(self, temp_db):
        """Test inserting and retrieving a news item."""
        item = NewsItem(
            source_url="https://example.com/article",
            source_type="rss",
            source_name="Test Source",
            title="Test Article",
            news_type="Funding",
            pub_date=date(2024, 1, 15),
        )

        # Insert
        is_new = temp_db.upsert_news_item(item)
        assert is_new is True

        # Retrieve
        retrieved = temp_db.get_news_item(item.url_hash)
        assert retrieved is not None
        assert retrieved.title == "Test Article"
        assert retrieved.news_type == "Funding"
        assert retrieved.pub_date == date(2024, 1, 15)

    def test_upsert_updates_existing(self, temp_db):
        """Test that upsert updates existing items."""
        item = NewsItem(
            source_url="https://example.com/article",
            title="Original Title",
        )

        # First insert
        is_new = temp_db.upsert_news_item(item)
        assert is_new is True

        # Update
        item.title = "Updated Title"
        item.news_type = "Acquisition"
        is_new = temp_db.upsert_news_item(item)
        assert is_new is False

        # Verify update
        retrieved = temp_db.get_news_item(item.url_hash)
        assert retrieved.title == "Updated Title"
        assert retrieved.news_type == "Acquisition"

    def test_url_hash_exists_true(self, temp_db):
        """Test url_hash_exists returns True for existing hash."""
        item = NewsItem(source_url="https://example.com/article")
        temp_db.upsert_news_item(item)

        assert temp_db.url_hash_exists(item.url_hash) is True

    def test_get_recent_titles(self, temp_db):
        """Test getting recent titles for deduplication."""
        # Insert some items
        for i in range(3):
            item = NewsItem(
                source_url=f"https://example.com/article{i}",
                title=f"Article {i}",
                company_name=f"Company {i}",
            )
            temp_db.upsert_news_item(item)

        titles = temp_db.get_recent_titles(days=7, limit=10)

        assert len(titles) == 3
        # Each tuple should be (url_hash, title, company_name)
        assert all(len(t) == 3 for t in titles)

    def test_record_duplicate(self, temp_db):
        """Test recording duplicate relationships."""
        # Insert primary item
        primary = NewsItem(source_url="https://example.com/primary")
        temp_db.upsert_news_item(primary)

        # Record duplicate
        temp_db.record_duplicate("dup12345", primary.url_hash, 0.92)

        # Check duplicate is known
        assert temp_db.is_known_duplicate("dup12345") is True
        assert temp_db.is_known_duplicate("unknown") is False

    def test_source_state_operations(self, temp_db):
        """Test source state tracking."""
        source_url = "https://example.com/feed"

        # Initially no state
        state = temp_db.get_source_state(source_url)
        assert state is None

        # Mark success
        temp_db.mark_source_success(source_url)
        state = temp_db.get_source_state(source_url)
        assert state is not None
        assert state.consecutive_failures == 0

        # Mark failure
        temp_db.mark_source_failure(source_url, "Connection error")
        state = temp_db.get_source_state(source_url)
        assert state.consecutive_failures == 1
        assert state.last_error == "Connection error"

    def test_get_items_by_date(self, temp_db):
        """Test retrieving items by processed date."""
        today = date.today()

        # Insert item for today
        item = NewsItem(
            source_url="https://example.com/today",
            title="Today's Article",
            processed_date=today,
        )
        temp_db.upsert_news_item(item)

        # Retrieve
        items = temp_db.get_items_by_date(today)
        assert len(items) == 1
        assert items[0].title == "Today's Article"

    def test_get_stats(self, temp_db):
        """Test database statistics."""
        # Insert some items with different types
        for i, news_type in enumerate(["Funding", "Funding", "Acquisition", "Other"]):
            item = NewsItem(
                source_url=f"https://example.com/article{i}",
                title=f"Article {i}",
                news_type=news_type,
            )
            temp_db.upsert_news_item(item)

        stats = temp_db.get_stats()

        assert stats["total_items"] == 4
        assert stats["by_type"]["Funding"] == 2
        assert stats["by_type"]["Acquisition"] == 1
        assert stats["by_type"]["Other"] == 1

    def test_hubspot_sync_tracking(self, temp_db):
        """Test HubSpot sync status tracking."""
        # Insert unsynced items
        items = []
        for i in range(3):
            item = NewsItem(
                source_url=f"https://example.com/article{i}",
                title=f"Article {i}",
                hubspot_synced=False,
            )
            temp_db.upsert_news_item(item)
            items.append(item)

        # Check unsynced count
        unsynced = temp_db.get_unsynced_items()
        assert len(unsynced) == 3

        # Mark some as synced
        temp_db.mark_hubspot_synced([items[0].url_hash, items[1].url_hash])

        # Check again
        unsynced = temp_db.get_unsynced_items()
        assert len(unsynced) == 1
