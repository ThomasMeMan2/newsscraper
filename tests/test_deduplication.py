"""Tests for deduplication logic."""

import pytest
from unittest.mock import Mock, MagicMock

from src.processing.deduplication import Deduplicator
from src.storage.models import NewsItem


class TestDeduplicator:
    """Tests for Deduplicator class."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database."""
        db = Mock()
        db.url_hash_exists.return_value = False
        db.is_known_duplicate.return_value = False
        db.get_recent_titles.return_value = []
        db.record_duplicate = Mock()
        return db

    @pytest.fixture
    def deduplicator(self, mock_db):
        """Create a deduplicator with mock database."""
        # Use lower threshold for testing to ensure matches are detected
        return Deduplicator(mock_db, similarity_threshold=0.70)

    def test_exact_duplicate_detection(self, deduplicator, mock_db):
        """Test detection of exact URL duplicates."""
        mock_db.url_hash_exists.return_value = True

        item = NewsItem(source_url="https://example.com/article")
        is_dup = deduplicator.check_exact_duplicate(item)

        assert is_dup is True

    def test_no_exact_duplicate(self, deduplicator, mock_db):
        """Test no duplicate when URL is new."""
        mock_db.url_hash_exists.return_value = False
        mock_db.is_known_duplicate.return_value = False

        item = NewsItem(source_url="https://example.com/new-article")
        is_dup = deduplicator.check_exact_duplicate(item)

        assert is_dup is False

    def test_normalize_text(self, deduplicator):
        """Test text normalization."""
        text = "  This is  a TEST!  with  Punctuation...  "
        normalized = deduplicator._normalize_text(text)

        assert normalized == "this is a test with punctuation"

    def test_extract_key_terms(self, deduplicator):
        """Test key term extraction."""
        text = "Belgian startup raises €5 million in Series A funding"
        terms = deduplicator._extract_key_terms(text)

        assert "belgian" in terms
        assert "startup" in terms
        assert "million" in terms
        assert "series" in terms
        assert "funding" in terms
        # Stopwords should be excluded
        assert "in" not in terms

    def test_calculate_similarity_identical(self, deduplicator):
        """Test similarity for identical texts."""
        text = "Company X raises €10 million"
        similarity = deduplicator._calculate_similarity(text, text)

        assert similarity == 1.0

    def test_calculate_similarity_different(self, deduplicator):
        """Test similarity for completely different texts."""
        text1 = "Belgian startup raises funding"
        text2 = "Weather forecast for tomorrow"
        similarity = deduplicator._calculate_similarity(text1, text2)

        assert similarity < 0.5

    def test_calculate_similarity_similar(self, deduplicator):
        """Test similarity for similar texts."""
        text1 = "Company X raises €10 million in Series A"
        text2 = "Company X haalt €10 miljoen op in Series A ronde"
        similarity = deduplicator._calculate_similarity(text1, text2)

        # These should be somewhat similar due to shared terms
        assert similarity > 0.3

    def test_semantic_duplicate_detection(self, deduplicator, mock_db):
        """Test semantic duplicate detection."""
        # Set up existing items in cache
        mock_db.get_recent_titles.return_value = [
            ("hash123", "Company X raises €10 million in Series A", "Company X"),
        ]

        item = NewsItem(
            source_url="https://other-source.com/article",
            title="Company X raises €10 million in Series A funding round",
            company_name="Company X",
        )

        result = deduplicator.check_semantic_duplicate(item)

        # Should find a semantic match
        assert result is not None
        assert result[0] == "hash123"

    def test_process_batch_removes_duplicates(self, deduplicator, mock_db):
        """Test batch processing removes duplicates."""
        items = [
            NewsItem(
                source_url="https://source1.com/article",
                title="Company X raises €10 million",
                company_name="Company X",
            ),
            NewsItem(
                source_url="https://source2.com/article",
                title="Company X raises €10 million funding",
                company_name="Company X",
            ),
        ]

        result = deduplicator.process_batch(items)

        # Should only keep one item
        assert len(result) == 1

    def test_process_batch_keeps_unique(self, deduplicator, mock_db):
        """Test batch processing keeps unique items."""
        items = [
            NewsItem(
                source_url="https://source1.com/article1",
                title="Company X raises funding",
                company_name="Company X",
            ),
            NewsItem(
                source_url="https://source2.com/article2",
                title="Company Y launches new product",
                company_name="Company Y",
            ),
        ]

        result = deduplicator.process_batch(items)

        # Should keep both items
        assert len(result) == 2
