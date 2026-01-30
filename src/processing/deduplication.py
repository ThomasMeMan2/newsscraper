"""Deduplication logic for news items."""

import logging
import re
from difflib import SequenceMatcher
from typing import Optional

from ..storage.database import Database
from ..storage.models import NewsItem

logger = logging.getLogger(__name__)


class Deduplicator:
    """Handles exact and semantic deduplication of news items."""

    def __init__(self, database: Database, similarity_threshold: float = 0.85):
        self.db = database
        self.similarity_threshold = similarity_threshold
        # Cache of recent items for semantic comparison
        self._recent_items: list[tuple[str, str, str]] = []
        self._cache_loaded = False

    def _load_recent_cache(self):
        """Load recent items from database for comparison."""
        if not self._cache_loaded:
            self._recent_items = self.db.get_recent_titles(days=30, limit=500)
            self._cache_loaded = True
            logger.debug(f"Loaded {len(self._recent_items)} recent items for deduplication")

    def _normalize_text(self, text: str) -> str:
        """Normalize text for comparison."""
        # Lowercase
        text = text.lower()
        # Remove punctuation
        text = re.sub(r'[^\w\s]', ' ', text)
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _extract_key_terms(self, text: str) -> set[str]:
        """Extract key terms from text for comparison."""
        # Common Dutch stopwords
        stopwords = {
            'de', 'het', 'een', 'van', 'in', 'op', 'voor', 'met', 'naar',
            'is', 'zijn', 'worden', 'werd', 'heeft', 'hebben', 'had',
            'en', 'of', 'maar', 'als', 'dan', 'nog', 'al', 'wel', 'niet',
            'te', 'aan', 'bij', 'om', 'uit', 'over', 'door', 'tot',
            'die', 'dat', 'deze', 'dit', 'er', 'zo', 'ook', 'meer',
            'the', 'a', 'an', 'of', 'in', 'to', 'for', 'and', 'or',
        }

        normalized = self._normalize_text(text)
        words = normalized.split()
        # Keep words that are not stopwords and longer than 2 characters
        return {w for w in words if w not in stopwords and len(w) > 2}

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """Calculate similarity between two texts."""
        # Normalize texts
        norm1 = self._normalize_text(text1)
        norm2 = self._normalize_text(text2)

        # Sequence matching (good for titles)
        sequence_ratio = SequenceMatcher(None, norm1, norm2).ratio()

        # Jaccard similarity on key terms
        terms1 = self._extract_key_terms(text1)
        terms2 = self._extract_key_terms(text2)

        if terms1 and terms2:
            intersection = len(terms1 & terms2)
            union = len(terms1 | terms2)
            jaccard = intersection / union if union > 0 else 0
        else:
            jaccard = 0

        # Combined score (weighted average)
        return 0.6 * sequence_ratio + 0.4 * jaccard

    def check_exact_duplicate(self, item: NewsItem) -> bool:
        """Check if item is an exact duplicate (same URL hash)."""
        if self.db.url_hash_exists(item.url_hash):
            logger.debug(f"Exact duplicate found: {item.url_hash}")
            return True

        if self.db.is_known_duplicate(item.url_hash):
            logger.debug(f"Known duplicate: {item.url_hash}")
            return True

        return False

    def check_semantic_duplicate(self, item: NewsItem) -> Optional[tuple[str, float]]:
        """
        Check if item is a semantic duplicate of existing items.
        Returns (primary_hash, similarity_score) if duplicate found, None otherwise.
        """
        self._load_recent_cache()

        best_match: Optional[tuple[str, float]] = None
        best_score = 0

        # Combine title and company for matching
        item_text = item.title
        if item.company_name:
            item_text = f"{item.title} {item.company_name}"

        for existing_hash, existing_title, existing_company in self._recent_items:
            # Skip self
            if existing_hash == item.url_hash:
                continue

            existing_text = existing_title
            if existing_company:
                existing_text = f"{existing_title} {existing_company}"

            similarity = self._calculate_similarity(item_text, existing_text)

            if similarity > best_score:
                best_score = similarity
                if similarity >= self.similarity_threshold:
                    best_match = (existing_hash, similarity)

        if best_match:
            logger.info(
                f"Semantic duplicate found: '{item.title[:50]}...' "
                f"matches {best_match[0]} with {best_match[1]:.2%} similarity"
            )

        return best_match

    def process_item(self, item: NewsItem) -> tuple[bool, Optional[str]]:
        """
        Process an item for deduplication.

        Returns:
            (is_duplicate, primary_hash)
            - is_duplicate: True if item should be skipped
            - primary_hash: Hash of the primary item if semantic duplicate
        """
        # Phase 1: Exact match
        if self.check_exact_duplicate(item):
            return True, None

        # Phase 2: Semantic match
        semantic_result = self.check_semantic_duplicate(item)
        if semantic_result:
            primary_hash, similarity = semantic_result
            # Record the duplicate relationship
            self.db.record_duplicate(item.url_hash, primary_hash, similarity)
            return True, primary_hash

        return False, None

    def process_batch(self, items: list[NewsItem]) -> list[NewsItem]:
        """
        Process a batch of items, removing duplicates.
        Also handles intra-batch deduplication (same story from multiple sources in same run).
        """
        self._load_recent_cache()

        unique_items = []
        batch_seen: dict[str, NewsItem] = {}  # Tracks items within this batch

        for item in items:
            # Check against database
            is_dup, primary = self.process_item(item)
            if is_dup:
                logger.debug(f"Skipping duplicate: {item.title[:50]}...")
                continue

            # Check against other items in this batch
            batch_dup_found = False
            item_text = f"{item.title} {item.company_name or ''}"

            for seen_hash, seen_item in batch_seen.items():
                seen_text = f"{seen_item.title} {seen_item.company_name or ''}"
                similarity = self._calculate_similarity(item_text, seen_text)

                if similarity >= self.similarity_threshold:
                    # Keep the one with earlier publication date
                    if item.pub_date and seen_item.pub_date:
                        if item.pub_date < seen_item.pub_date:
                            # New item is older, replace
                            del batch_seen[seen_hash]
                            batch_seen[item.url_hash] = item
                            # Update unique_items
                            unique_items = [i for i in unique_items if i.url_hash != seen_hash]
                            unique_items.append(item)
                    # Log the duplicate
                    self.db.record_duplicate(item.url_hash, seen_hash, similarity)
                    batch_dup_found = True
                    break

            if not batch_dup_found:
                batch_seen[item.url_hash] = item
                unique_items.append(item)
                # Add to cache for future comparisons
                self._recent_items.append((item.url_hash, item.title, item.company_name or ''))

        logger.info(f"Deduplication: {len(items)} -> {len(unique_items)} items ({len(items) - len(unique_items)} duplicates)")
        return unique_items
