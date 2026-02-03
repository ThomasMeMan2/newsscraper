"""Run tracker for collecting pipeline statistics."""

import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

from .database import Database

logger = logging.getLogger(__name__)


@dataclass
class SourceStats:
    """Statistics for a single source during a run."""
    source_name: str
    source_url: str
    source_type: str
    items_found: int = 0
    items_scraped: int = 0
    items_rejected: int = 0
    items_duplicate: int = 0
    rejection_reasons: dict = field(default_factory=dict)
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    def add_rejection(self, reason: str, count: int = 1):
        """Add rejection count for a reason."""
        self.rejection_reasons[reason] = self.rejection_reasons.get(reason, 0) + count


@dataclass
class EnrichmentLog:
    """Log entry for an enrichment operation."""
    url_hash: str
    title: str
    phase: str  # 'article' or 'search'
    fields_before: dict
    fields_after: dict
    success: bool = True
    error_message: Optional[str] = None


class RunTracker:
    """
    Tracks statistics and logs for a pipeline run.

    Usage:
        tracker = RunTracker(db)
        tracker.start_run(settings)

        # For each source
        tracker.start_source("De Tijd", "https://...", "rss")
        tracker.record_item_found()
        tracker.record_item_scraped()
        tracker.record_item_rejected("no_keywords")
        tracker.end_source()

        # For classification
        tracker.record_classified(10)

        # For enrichment
        tracker.record_enrichment(url_hash, title, "article", before, after)

        # End run
        tracker.end_run()
    """

    def __init__(self, db: Database):
        self.db = db
        self.run_id: Optional[int] = None
        self.current_source: Optional[SourceStats] = None
        self.source_stats: list[SourceStats] = []
        self.enrichment_logs: list[EnrichmentLog] = []

        # Aggregate stats
        self.total_sources = 0
        self.total_items_found = 0
        self.total_items_scraped = 0
        self.total_items_rejected = 0
        self.total_duplicates = 0
        self.total_classified = 0
        self.total_enriched = 0
        self.total_saved_new = 0
        self.total_saved_updated = 0
        self.email_sent = False

    def start_run(self, settings_snapshot: Optional[dict] = None) -> int:
        """Start a new pipeline run. Returns the run ID."""
        self.run_id = self.db.create_run(settings_snapshot)
        logger.info(f"Started pipeline run #{self.run_id}")
        return self.run_id

    def start_source(self, source_name: str, source_url: str, source_type: str):
        """Start tracking a source."""
        if self.current_source:
            self.end_source()

        self.current_source = SourceStats(
            source_name=source_name,
            source_url=source_url,
            source_type=source_type,
            started_at=datetime.now(),
        )
        self.total_sources += 1
        logger.info(f"Processing source: {source_name}")

    def record_item_found(self, count: int = 1):
        """Record items found from current source."""
        if self.current_source:
            self.current_source.items_found += count
        self.total_items_found += count

    def record_item_scraped(self, count: int = 1):
        """Record items successfully scraped."""
        if self.current_source:
            self.current_source.items_scraped += count
        self.total_items_scraped += count

    def record_item_rejected(self, reason: str, count: int = 1):
        """Record rejected items with reason."""
        if self.current_source:
            self.current_source.items_rejected += count
            self.current_source.add_rejection(reason, count)
        self.total_items_rejected += count
        logger.debug(f"Rejected {count} items: {reason}")

    def record_item_duplicate(self, count: int = 1):
        """Record duplicate items."""
        if self.current_source:
            self.current_source.items_duplicate += count
        self.total_duplicates += count

    def record_source_error(self, error: str):
        """Record an error for the current source."""
        if self.current_source:
            self.current_source.error_message = error
        logger.error(f"Source error: {error}")

    def end_source(self):
        """End tracking current source and save stats."""
        if self.current_source:
            self.current_source.completed_at = datetime.now()
            self.source_stats.append(self.current_source)

            # Log summary
            s = self.current_source
            logger.info(
                f"Source {s.source_name}: found={s.items_found}, "
                f"scraped={s.items_scraped}, rejected={s.items_rejected}, "
                f"duplicates={s.items_duplicate}"
            )

            # Save to database
            if self.run_id:
                self.db.add_source_stats(
                    run_id=self.run_id,
                    source_name=s.source_name,
                    source_url=s.source_url,
                    source_type=s.source_type,
                    items_found=s.items_found,
                    items_scraped=s.items_scraped,
                    items_rejected=s.items_rejected,
                    items_duplicate=s.items_duplicate,
                    rejection_reasons=s.rejection_reasons,
                    error_message=s.error_message,
                    started_at=s.started_at,
                    completed_at=s.completed_at,
                )

            self.current_source = None

    def record_classified(self, count: int):
        """Record number of items classified."""
        self.total_classified = count
        logger.info(f"Classified {count} items")

    def record_enrichment(self, url_hash: str, title: str, phase: str,
                          fields_before: dict, fields_after: dict,
                          success: bool = True, error_message: str = None):
        """Record an enrichment operation."""
        log = EnrichmentLog(
            url_hash=url_hash,
            title=title,
            phase=phase,
            fields_before=fields_before,
            fields_after=fields_after,
            success=success,
            error_message=error_message,
        )
        self.enrichment_logs.append(log)

        if success:
            self.total_enriched += 1

        # Save to database
        if self.run_id:
            self.db.add_enrichment_log(
                run_id=self.run_id,
                url_hash=url_hash,
                title=title[:100] if title else "",
                phase=phase,
                fields_before=fields_before,
                fields_after=fields_after,
                success=success,
                error_message=error_message,
            )

        # Log changes
        changes = []
        for key in fields_after:
            before = fields_before.get(key)
            after = fields_after.get(key)
            if before != after:
                changes.append(f"{key}: {before} -> {after}")

        if changes:
            logger.info(f"Enriched [{phase}] {title[:40]}...: {', '.join(changes)}")

    def record_saved(self, new_count: int, updated_count: int):
        """Record saved items count."""
        self.total_saved_new = new_count
        self.total_saved_updated = updated_count
        logger.info(f"Saved: {new_count} new, {updated_count} updated")

    def record_email_sent(self, sent: bool):
        """Record whether email was sent."""
        self.email_sent = sent

    def end_run(self, status: str = 'completed', error_message: str = None):
        """End the run and save final stats."""
        # Make sure current source is saved
        if self.current_source:
            self.end_source()

        if self.run_id:
            # Update run with final stats
            self.db.update_run(
                self.run_id,
                total_sources=self.total_sources,
                total_items_found=self.total_items_found,
                total_items_scraped=self.total_items_scraped,
                total_items_rejected=self.total_items_rejected,
                total_duplicates=self.total_duplicates,
                total_classified=self.total_classified,
                total_enriched=self.total_enriched,
                total_saved_new=self.total_saved_new,
                total_saved_updated=self.total_saved_updated,
                email_sent=self.email_sent,
            )

            # Mark as completed
            self.db.complete_run(self.run_id, status, error_message)

            logger.info(
                f"Run #{self.run_id} {status}: "
                f"sources={self.total_sources}, found={self.total_items_found}, "
                f"scraped={self.total_items_scraped}, rejected={self.total_items_rejected}, "
                f"duplicates={self.total_duplicates}, enriched={self.total_enriched}, "
                f"saved_new={self.total_saved_new}"
            )

    def get_summary(self) -> dict:
        """Get run summary as dictionary."""
        return {
            "run_id": self.run_id,
            "total_sources": self.total_sources,
            "total_items_found": self.total_items_found,
            "total_items_scraped": self.total_items_scraped,
            "total_items_rejected": self.total_items_rejected,
            "total_duplicates": self.total_duplicates,
            "total_classified": self.total_classified,
            "total_enriched": self.total_enriched,
            "total_saved_new": self.total_saved_new,
            "total_saved_updated": self.total_saved_updated,
            "email_sent": self.email_sent,
            "source_stats": [
                {
                    "source_name": s.source_name,
                    "items_found": s.items_found,
                    "items_scraped": s.items_scraped,
                    "items_rejected": s.items_rejected,
                    "items_duplicate": s.items_duplicate,
                    "rejection_reasons": s.rejection_reasons,
                    "error_message": s.error_message,
                }
                for s in self.source_stats
            ],
        }
