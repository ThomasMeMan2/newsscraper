"""SQLite database operations for the news aggregator."""

import sqlite3
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

from .models import NewsItem, SourceState

logger = logging.getLogger(__name__)


class Database:
    """SQLite database manager for news items and source states."""

    SCHEMA_VERSION = 2  # Bumped for duplicate_of field

    def __init__(self, db_path: str | Path):
        """Initialize database connection."""
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._run_migrations()

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        """Initialize database schema."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # News items table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS news_items (
                    url_hash TEXT PRIMARY KEY,
                    source_url TEXT UNIQUE NOT NULL,
                    source_type TEXT NOT NULL,
                    source_name TEXT,
                    title TEXT NOT NULL,
                    pub_date DATE,
                    raw_content TEXT,
                    news_type TEXT DEFAULT 'Other',
                    news_type_confidence TEXT DEFAULT 'low',
                    region TEXT DEFAULT 'Other',
                    region_confidence TEXT DEFAULT 'low',
                    company_name TEXT,
                    related_people TEXT,
                    related_people_confidence TEXT DEFAULT 'low',
                    funding_amount TEXT,
                    summary TEXT,
                    processed_date DATE,
                    enrichment_source TEXT DEFAULT 'none',
                    enrichment_attempts INTEGER DEFAULT 0,
                    hubspot_synced BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Source state tracking table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS source_states (
                    source_url TEXT PRIMARY KEY,
                    last_processed_date TIMESTAMP,
                    last_successful_run TIMESTAMP,
                    last_error TEXT,
                    consecutive_failures INTEGER DEFAULT 0
                )
            """)

            # Duplicate tracking table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS duplicates (
                    duplicate_hash TEXT PRIMARY KEY,
                    primary_hash TEXT NOT NULL,
                    similarity_score REAL,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (primary_hash) REFERENCES news_items(url_hash)
                )
            """)

            # Schema version tracking
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                )
            """)

            # Pipeline runs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TIMESTAMP NOT NULL,
                    completed_at TIMESTAMP,
                    status TEXT DEFAULT 'running',
                    total_sources INTEGER DEFAULT 0,
                    total_items_found INTEGER DEFAULT 0,
                    total_items_scraped INTEGER DEFAULT 0,
                    total_items_rejected INTEGER DEFAULT 0,
                    total_duplicates INTEGER DEFAULT 0,
                    total_classified INTEGER DEFAULT 0,
                    total_enriched INTEGER DEFAULT 0,
                    total_saved_new INTEGER DEFAULT 0,
                    total_saved_updated INTEGER DEFAULT 0,
                    email_sent BOOLEAN DEFAULT FALSE,
                    error_message TEXT,
                    settings_snapshot TEXT
                )
            """)

            # Per-source statistics for each run
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS run_source_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    source_name TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    source_type TEXT,
                    items_found INTEGER DEFAULT 0,
                    items_scraped INTEGER DEFAULT 0,
                    items_rejected INTEGER DEFAULT 0,
                    items_duplicate INTEGER DEFAULT 0,
                    rejection_reasons TEXT,
                    error_message TEXT,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (run_id) REFERENCES pipeline_runs(id)
                )
            """)

            # Enrichment log for each run
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS run_enrichment_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    url_hash TEXT NOT NULL,
                    title TEXT,
                    phase TEXT,
                    fields_before TEXT,
                    fields_after TEXT,
                    success BOOLEAN DEFAULT TRUE,
                    error_message TEXT,
                    FOREIGN KEY (run_id) REFERENCES pipeline_runs(id)
                )
            """)

            # Create indexes for run queries
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_runs_started ON pipeline_runs(started_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_run_sources_run ON run_source_stats(run_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_run_enrichment_run ON run_enrichment_log(run_id)")

            # Insert schema version if not exists
            cursor.execute("INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
                          (self.SCHEMA_VERSION,))

            # Create indexes for common queries
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_pub_date ON news_items(pub_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_type ON news_items(news_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_processed ON news_items(processed_date)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_hubspot ON news_items(hubspot_synced)")

            logger.info(f"Database initialized at {self.db_path}")

    def _run_migrations(self):
        """Run database migrations for schema updates."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Check if duplicate_of column exists
            cursor.execute("PRAGMA table_info(news_items)")
            columns = [row[1] for row in cursor.fetchall()]

            if 'duplicate_of' not in columns:
                logger.info("Running migration: adding duplicate_of column")
                cursor.execute("""
                    ALTER TABLE news_items ADD COLUMN duplicate_of TEXT
                """)
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_news_duplicate_of ON news_items(duplicate_of)
                """)

            if 'similarity_score' not in columns:
                logger.info("Running migration: adding similarity_score column")
                cursor.execute("""
                    ALTER TABLE news_items ADD COLUMN similarity_score REAL
                """)

    # === News Items Operations ===

    def url_hash_exists(self, url_hash: str) -> bool:
        """Check if a URL hash already exists in the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM news_items WHERE url_hash = ?", (url_hash,))
            return cursor.fetchone() is not None

    def get_news_item(self, url_hash: str) -> Optional[NewsItem]:
        """Get a news item by its URL hash."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM news_items WHERE url_hash = ?", (url_hash,))
            row = cursor.fetchone()
            if row:
                return NewsItem.from_dict(dict(row))
            return None

    def upsert_news_item(self, item: NewsItem) -> bool:
        """Insert or update a news item. Returns True if inserted, False if updated."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Check if exists
            cursor.execute("SELECT 1 FROM news_items WHERE url_hash = ?", (item.url_hash,))
            exists = cursor.fetchone() is not None

            if exists:
                # Update existing
                cursor.execute("""
                    UPDATE news_items SET
                        title = ?,
                        raw_content = ?,
                        news_type = ?,
                        news_type_confidence = ?,
                        region = ?,
                        region_confidence = ?,
                        company_name = ?,
                        related_people = ?,
                        related_people_confidence = ?,
                        funding_amount = ?,
                        summary = ?,
                        enrichment_source = ?,
                        enrichment_attempts = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE url_hash = ?
                """, (
                    item.title,
                    item.raw_content,
                    item.news_type,
                    item.news_type_confidence,
                    item.region,
                    item.region_confidence,
                    item.company_name,
                    item.related_people_str,
                    item.related_people_confidence,
                    item.funding_amount,
                    item.summary,
                    item.enrichment_source,
                    item.enrichment_attempts,
                    item.url_hash,
                ))
                logger.debug(f"Updated news item: {item.url_hash}")
                return False
            else:
                # Insert new
                cursor.execute("""
                    INSERT INTO news_items (
                        url_hash, source_url, source_type, source_name, title,
                        pub_date, raw_content, news_type, news_type_confidence,
                        region, region_confidence, company_name, related_people,
                        related_people_confidence, funding_amount, summary,
                        processed_date, enrichment_source, enrichment_attempts,
                        hubspot_synced
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item.url_hash,
                    item.source_url,
                    item.source_type,
                    item.source_name,
                    item.title,
                    item.pub_date.isoformat() if item.pub_date else None,
                    item.raw_content,
                    item.news_type,
                    item.news_type_confidence,
                    item.region,
                    item.region_confidence,
                    item.company_name,
                    item.related_people_str,
                    item.related_people_confidence,
                    item.funding_amount,
                    item.summary,
                    item.processed_date.isoformat() if item.processed_date else None,
                    item.enrichment_source,
                    item.enrichment_attempts,
                    item.hubspot_synced,
                ))
                logger.debug(f"Inserted news item: {item.url_hash}")
                return True

    def get_items_for_enrichment(self, limit: int = 50) -> list[NewsItem]:
        """Get items that need enrichment."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM news_items
                WHERE news_type != 'Other'
                AND enrichment_attempts < 2
                AND (
                    news_type_confidence != 'high'
                    OR region_confidence != 'high'
                    OR related_people_confidence != 'high'
                )
                ORDER BY processed_date DESC
                LIMIT ?
            """, (limit,))
            return [NewsItem.from_dict(dict(row)) for row in cursor.fetchall()]

    def get_recent_items(self, days: int = 7, include_duplicates: bool = False) -> list[NewsItem]:
        """Get items from the last N days."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if include_duplicates:
                cursor.execute("""
                    SELECT * FROM news_items
                    WHERE processed_date >= date('now', ?)
                    ORDER BY pub_date DESC
                """, (f'-{days} days',))
            else:
                cursor.execute("""
                    SELECT * FROM news_items
                    WHERE processed_date >= date('now', ?)
                    AND duplicate_of IS NULL
                    ORDER BY pub_date DESC
                """, (f'-{days} days',))
            return [NewsItem.from_dict(dict(row)) for row in cursor.fetchall()]

    def get_duplicates_of(self, primary_hash: str) -> list[NewsItem]:
        """Get all items that are duplicates of the given primary item."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM news_items
                WHERE duplicate_of = ?
                ORDER BY processed_date DESC
            """, (primary_hash,))
            return [NewsItem.from_dict(dict(row)) for row in cursor.fetchall()]

    def get_all_duplicates(self) -> list[NewsItem]:
        """Get all duplicate items."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM news_items
                WHERE duplicate_of IS NOT NULL
                ORDER BY processed_date DESC
            """)
            return [NewsItem.from_dict(dict(row)) for row in cursor.fetchall()]

    def unlink_duplicate(self, url_hash: str) -> bool:
        """Remove the duplicate relationship for an item. Returns True if updated."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE news_items
                SET duplicate_of = NULL, similarity_score = NULL
                WHERE url_hash = ?
            """, (url_hash,))
            return cursor.rowcount > 0

    def mark_as_duplicate(self, duplicate_hash: str, primary_hash: str, similarity: float):
        """Mark an item as a duplicate of another."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE news_items
                SET duplicate_of = ?, similarity_score = ?
                WHERE url_hash = ?
            """, (primary_hash, similarity, duplicate_hash))
            logger.info(f"Marked {duplicate_hash} as duplicate of {primary_hash} ({similarity:.2%})")

    def get_items_by_date(self, target_date: date) -> list[NewsItem]:
        """Get all items processed on a specific date."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM news_items
                WHERE processed_date = ?
                ORDER BY news_type, pub_date DESC
            """, (target_date.isoformat(),))
            return [NewsItem.from_dict(dict(row)) for row in cursor.fetchall()]

    def get_recent_titles(self, days: int = 30, limit: int = 500) -> list[tuple[str, str, str]]:
        """Get recent titles for semantic deduplication. Returns (url_hash, title, company_name)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT url_hash, title, company_name FROM news_items
                WHERE processed_date >= date('now', ?)
                ORDER BY processed_date DESC
                LIMIT ?
            """, (f'-{days} days', limit))
            return [(row['url_hash'], row['title'], row['company_name'] or '') for row in cursor.fetchall()]

    def get_unsynced_items(self, limit: int = 100) -> list[NewsItem]:
        """Get items not yet synced to HubSpot."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM news_items
                WHERE hubspot_synced = FALSE
                ORDER BY processed_date DESC
                LIMIT ?
            """, (limit,))
            return [NewsItem.from_dict(dict(row)) for row in cursor.fetchall()]

    def mark_hubspot_synced(self, url_hashes: list[str]):
        """Mark items as synced to HubSpot."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(
                "UPDATE news_items SET hubspot_synced = TRUE WHERE url_hash = ?",
                [(h,) for h in url_hashes]
            )

    # === Duplicate Tracking ===

    def record_duplicate(self, duplicate_hash: str, primary_hash: str, similarity: float):
        """Record a duplicate detection."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO duplicates (duplicate_hash, primary_hash, similarity_score)
                VALUES (?, ?, ?)
            """, (duplicate_hash, primary_hash, similarity))
            logger.info(f"Recorded duplicate: {duplicate_hash} -> {primary_hash} ({similarity:.2%})")

    def is_known_duplicate(self, url_hash: str) -> bool:
        """Check if a URL hash is a known duplicate."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM duplicates WHERE duplicate_hash = ?", (url_hash,))
            return cursor.fetchone() is not None

    # === Source State Operations ===

    def get_source_state(self, source_url: str) -> Optional[SourceState]:
        """Get the state for a source."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM source_states WHERE source_url = ?", (source_url,))
            row = cursor.fetchone()
            if row:
                return SourceState.from_dict(dict(row))
            return None

    def update_source_state(self, state: SourceState):
        """Update or insert source state."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO source_states (
                    source_url, last_processed_date, last_successful_run,
                    last_error, consecutive_failures
                ) VALUES (?, ?, ?, ?, ?)
            """, (
                state.source_url,
                state.last_processed_date.isoformat() if state.last_processed_date else None,
                state.last_successful_run.isoformat() if state.last_successful_run else None,
                state.last_error,
                state.consecutive_failures,
            ))

    def mark_source_success(self, source_url: str):
        """Mark a source run as successful."""
        now = datetime.now()
        state = self.get_source_state(source_url) or SourceState(source_url=source_url)
        state.last_processed_date = now
        state.last_successful_run = now
        state.last_error = None
        state.consecutive_failures = 0
        self.update_source_state(state)

    def mark_source_failure(self, source_url: str, error: str):
        """Mark a source run as failed."""
        state = self.get_source_state(source_url) or SourceState(source_url=source_url)
        state.last_processed_date = datetime.now()
        state.last_error = error
        state.consecutive_failures += 1
        self.update_source_state(state)

    def get_unique_sources(self) -> list[str]:
        """Get list of unique source names."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT source_name FROM news_items
                WHERE source_name IS NOT NULL
                ORDER BY source_name
            """)
            return [row['source_name'] for row in cursor.fetchall()]

    # === Statistics ===

    def get_stats(self) -> dict:
        """Get database statistics."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            stats = {}

            # Total items
            cursor.execute("SELECT COUNT(*) FROM news_items")
            stats['total_items'] = cursor.fetchone()[0]

            # Items by type
            cursor.execute("""
                SELECT news_type, COUNT(*) as count
                FROM news_items
                GROUP BY news_type
                ORDER BY count DESC
            """)
            stats['by_type'] = {row['news_type']: row['count'] for row in cursor.fetchall()}

            # Items today
            cursor.execute("""
                SELECT COUNT(*) FROM news_items
                WHERE processed_date = date('now')
            """)
            stats['today'] = cursor.fetchone()[0]

            # Duplicates detected
            cursor.execute("SELECT COUNT(*) FROM duplicates")
            stats['duplicates'] = cursor.fetchone()[0]

            # Pending HubSpot sync
            cursor.execute("SELECT COUNT(*) FROM news_items WHERE hubspot_synced = FALSE")
            stats['pending_sync'] = cursor.fetchone()[0]

            return stats

    # === Run Tracking Operations ===

    def create_run(self, settings_snapshot: Optional[dict] = None) -> int:
        """Create a new pipeline run record. Returns the run ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO pipeline_runs (started_at, status, settings_snapshot)
                VALUES (?, 'running', ?)
            """, (datetime.now().isoformat(), json.dumps(settings_snapshot) if settings_snapshot else None))
            return cursor.lastrowid

    def update_run(self, run_id: int, **kwargs):
        """Update run statistics."""
        if not kwargs:
            return

        with self._get_connection() as conn:
            cursor = conn.cursor()
            set_clauses = []
            values = []
            for key, value in kwargs.items():
                set_clauses.append(f"{key} = ?")
                values.append(value)
            values.append(run_id)

            cursor.execute(f"""
                UPDATE pipeline_runs
                SET {', '.join(set_clauses)}
                WHERE id = ?
            """, values)

    def complete_run(self, run_id: int, status: str = 'completed', error_message: str = None):
        """Mark a run as completed."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE pipeline_runs
                SET completed_at = ?, status = ?, error_message = ?
                WHERE id = ?
            """, (datetime.now().isoformat(), status, error_message, run_id))

    def add_source_stats(self, run_id: int, source_name: str, source_url: str, source_type: str,
                         items_found: int = 0, items_scraped: int = 0, items_rejected: int = 0,
                         items_duplicate: int = 0, rejection_reasons: dict = None,
                         error_message: str = None, started_at: datetime = None, completed_at: datetime = None):
        """Add source statistics for a run."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO run_source_stats (
                    run_id, source_name, source_url, source_type,
                    items_found, items_scraped, items_rejected, items_duplicate,
                    rejection_reasons, error_message, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id, source_name, source_url, source_type,
                items_found, items_scraped, items_rejected, items_duplicate,
                json.dumps(rejection_reasons) if rejection_reasons else None,
                error_message,
                started_at.isoformat() if started_at else None,
                completed_at.isoformat() if completed_at else None,
            ))

    def add_enrichment_log(self, run_id: int, url_hash: str, title: str, phase: str,
                           fields_before: dict, fields_after: dict,
                           success: bool = True, error_message: str = None):
        """Add enrichment log entry for a run."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO run_enrichment_log (
                    run_id, url_hash, title, phase, fields_before, fields_after, success, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id, url_hash, title, phase,
                json.dumps(fields_before), json.dumps(fields_after),
                success, error_message,
            ))

    def get_runs(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Get recent pipeline runs."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM pipeline_runs
                ORDER BY started_at DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))
            return [dict(row) for row in cursor.fetchall()]

    def get_run(self, run_id: int) -> Optional[dict]:
        """Get a single run by ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM pipeline_runs WHERE id = ?", (run_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_run_source_stats(self, run_id: int) -> list[dict]:
        """Get source statistics for a run."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM run_source_stats
                WHERE run_id = ?
                ORDER BY source_name
            """, (run_id,))
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                if d.get('rejection_reasons'):
                    d['rejection_reasons'] = json.loads(d['rejection_reasons'])
                results.append(d)
            return results

    def get_run_enrichment_log(self, run_id: int) -> list[dict]:
        """Get enrichment log for a run."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM run_enrichment_log
                WHERE run_id = ?
                ORDER BY id
            """, (run_id,))
            results = []
            for row in cursor.fetchall():
                d = dict(row)
                if d.get('fields_before'):
                    d['fields_before'] = json.loads(d['fields_before'])
                if d.get('fields_after'):
                    d['fields_after'] = json.loads(d['fields_after'])
                results.append(d)
            return results

    def get_last_run(self) -> Optional[dict]:
        """Get the most recent run."""
        runs = self.get_runs(limit=1)
        return runs[0] if runs else None

    def get_runs_count(self) -> int:
        """Get total number of runs."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM pipeline_runs")
            return cursor.fetchone()[0]
