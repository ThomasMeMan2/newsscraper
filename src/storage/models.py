"""Data models for the news aggregator."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional
import hashlib


@dataclass
class NewsItem:
    """Represents a news article with all extracted and classified data."""

    # Core identifiers
    source_url: str
    url_hash: str = ""

    # Source metadata
    source_type: str = ""  # rss, website, linkedin
    source_name: str = ""

    # Article data
    title: str = ""
    pub_date: Optional[date] = None
    raw_content: str = ""

    # Classification results
    news_type: str = "Other"
    news_type_confidence: str = "low"
    region: str = "Other"
    region_confidence: str = "low"
    company_name: Optional[str] = None
    related_people: list[str] = field(default_factory=list)
    related_people_confidence: str = "low"
    funding_amount: Optional[str] = None
    summary: str = ""

    # Processing metadata
    processed_date: Optional[date] = None
    enrichment_source: str = "none"  # none, scrape, search
    enrichment_attempts: int = 0
    hubspot_synced: bool = False

    # Duplicate tracking
    duplicate_of: Optional[str] = None  # url_hash of primary item
    similarity_score: Optional[float] = None  # similarity to primary (0-1)

    def __post_init__(self):
        """Generate url_hash if not provided."""
        if not self.url_hash and self.source_url:
            self.url_hash = self.generate_hash(self.source_url)
        if not self.processed_date:
            self.processed_date = date.today()

    @staticmethod
    def generate_hash(url: str) -> str:
        """Generate first 8 characters of SHA256 hash."""
        return hashlib.sha256(url.encode()).hexdigest()[:8]

    @property
    def related_people_str(self) -> str:
        """Return related people as comma-separated string."""
        return ", ".join(self.related_people) if self.related_people else ""

    @property
    def needs_enrichment(self) -> bool:
        """Check if item needs enrichment based on confidence scores."""
        if self.news_type == "Other":
            return False
        if self.enrichment_attempts >= 2:
            return False
        return any([
            self.news_type_confidence != "high",
            self.region_confidence != "high",
            self.related_people_confidence != "high"
        ])

    @property
    def is_duplicate(self) -> bool:
        """Check if this item is marked as a duplicate."""
        return self.duplicate_of is not None

    def to_dict(self) -> dict:
        """Convert to dictionary for API/frontend."""
        return {
            "url_hash": self.url_hash,
            "source_url": self.source_url,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "title": self.title,
            "pub_date": self.pub_date.isoformat() if self.pub_date else None,
            "raw_content": self.raw_content,
            "news_type": self.news_type,
            "news_type_confidence": self.news_type_confidence,
            "region": self.region,
            "region_confidence": self.region_confidence,
            "company_name": self.company_name,
            "related_people": self.related_people_str,
            "related_people_confidence": self.related_people_confidence,
            "funding_amount": self.funding_amount,
            "summary": self.summary,
            "processed_date": self.processed_date.isoformat() if self.processed_date else None,
            "enrichment_source": self.enrichment_source,
            "enrichment_attempts": self.enrichment_attempts,
            "hubspot_synced": self.hubspot_synced,
            "duplicate_of": self.duplicate_of,
            "similarity_score": self.similarity_score,
            "is_duplicate": self.is_duplicate,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NewsItem":
        """Create NewsItem from dictionary (database row)."""
        # Parse dates
        pub_date = None
        if data.get("pub_date"):
            pub_date = date.fromisoformat(data["pub_date"])

        processed_date = None
        if data.get("processed_date"):
            processed_date = date.fromisoformat(data["processed_date"])

        # Parse related people
        related_people = []
        if data.get("related_people"):
            related_people = [p.strip() for p in data["related_people"].split(",") if p.strip()]

        return cls(
            url_hash=data.get("url_hash", ""),
            source_url=data.get("source_url", ""),
            source_type=data.get("source_type", ""),
            source_name=data.get("source_name", ""),
            title=data.get("title", ""),
            pub_date=pub_date,
            raw_content=data.get("raw_content", ""),
            news_type=data.get("news_type", "Other"),
            news_type_confidence=data.get("news_type_confidence", "low"),
            region=data.get("region", "Other"),
            region_confidence=data.get("region_confidence", "low"),
            company_name=data.get("company_name"),
            related_people=related_people,
            related_people_confidence=data.get("related_people_confidence", "low"),
            funding_amount=data.get("funding_amount"),
            summary=data.get("summary", ""),
            processed_date=processed_date,
            enrichment_source=data.get("enrichment_source", "none"),
            enrichment_attempts=data.get("enrichment_attempts", 0),
            hubspot_synced=bool(data.get("hubspot_synced", False)),
            duplicate_of=data.get("duplicate_of"),
            similarity_score=data.get("similarity_score"),
        )


@dataclass
class SourceState:
    """Tracks the last processed state for each source."""

    source_url: str
    last_processed_date: Optional[datetime] = None
    last_successful_run: Optional[datetime] = None
    last_error: Optional[str] = None
    consecutive_failures: int = 0

    def to_dict(self) -> dict:
        """Convert to dictionary for database storage."""
        return {
            "source_url": self.source_url,
            "last_processed_date": self.last_processed_date.isoformat() if self.last_processed_date else None,
            "last_successful_run": self.last_successful_run.isoformat() if self.last_successful_run else None,
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SourceState":
        """Create SourceState from dictionary."""
        last_processed = None
        if data.get("last_processed_date"):
            last_processed = datetime.fromisoformat(data["last_processed_date"])

        last_success = None
        if data.get("last_successful_run"):
            last_success = datetime.fromisoformat(data["last_successful_run"])

        return cls(
            source_url=data["source_url"],
            last_processed_date=last_processed,
            last_successful_run=last_success,
            last_error=data.get("last_error"),
            consecutive_failures=data.get("consecutive_failures", 0),
        )
