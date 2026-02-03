"""Main orchestrator for the news aggregation pipeline."""

import asyncio
import logging
from datetime import datetime, date
from typing import Optional

from .config import Config, load_config, setup_logging, SourceConfig
from .storage.database import Database
from .storage.models import NewsItem
from .storage.run_tracker import RunTracker
from .scrapers.base import ScraperResult
from .scrapers.rss_scraper import RssScraper
from .scrapers.web_scraper import WebScraper
from .processing.deduplication import Deduplicator
from .processing.classifier import create_classifier, BaseClassifier
from .processing.enricher import Enricher
from .output.email_digest import EmailDigest

logger = logging.getLogger(__name__)


class NewsAggregator:
    """Main orchestrator for the news aggregation pipeline."""

    def __init__(self, config: Config):
        self.config = config
        self.db = Database(config.database_path)
        self.deduplicator = Deduplicator(
            self.db,
            config.processing.dedup_similarity_threshold
        )

        # Initialize classifier if API key available
        self.classifier: Optional[BaseClassifier] = None
        api_key = config.get_llm_api_key()
        if api_key:
            self.classifier = create_classifier(
                provider=config.processing.llm_provider,
                api_key=api_key,
                model=config.processing.llm_model or None,  # Empty string -> None -> use default
                max_tokens=config.processing.llm_max_tokens,
            )
            logger.info(f"Using LLM provider: {config.processing.llm_provider}")
        else:
            logger.warning(f"No API key found for provider: {config.processing.llm_provider}")

        # Initialize enricher
        self.enricher: Optional[Enricher] = None
        if self.classifier:
            # Get API key for enrichment (uses OpenAI for web search)
            enrichment_api_key = config.openai_api_key if config.processing.enrichment_provider == "openai" else None
            self.enricher = Enricher(
                classifier=self.classifier,
                max_attempts=config.processing.max_enrichment_attempts,
                min_delay=config.processing.min_request_delay,
                max_delay=config.processing.max_request_delay,
                search_api_key=enrichment_api_key,
                search_model=config.processing.enrichment_model,
            )

        # Initialize email digest
        self.email_digest = EmailDigest(
            smtp_host=config.smtp_host,
            smtp_port=config.smtp_port,
            smtp_username=config.smtp_username,
            smtp_password=config.smtp_password,
            from_address=config.email_from,
            recipients=config.email_recipients,
        )

    def _get_scraper(self, source: SourceConfig, last_processed: Optional[datetime]):
        """Get appropriate scraper for source type."""
        theme_keywords = self.config.get_all_theme_keywords()

        if source.type == "rss":
            return RssScraper(
                source_config=source,
                theme_keywords=theme_keywords,
                min_delay=self.config.processing.min_request_delay,
                max_delay=self.config.processing.max_request_delay,
                last_processed_date=last_processed,
            )
        elif source.type == "website":
            return WebScraper(
                source_config=source,
                theme_keywords=theme_keywords,
                min_delay=self.config.processing.min_request_delay,
                max_delay=self.config.processing.max_request_delay,
                last_processed_date=last_processed,
            )
        elif source.type == "linkedin":
            logger.warning(f"LinkedIn source skipped (not supported): {source.name}")
            return None
        else:
            logger.warning(f"Unknown source type: {source.type}")
            return None

    async def scrape_source(self, source: SourceConfig) -> ScraperResult:
        """Scrape a single source."""
        logger.info(f"Processing source: {source.name}")

        # Get last processed date
        state = self.db.get_source_state(source.url)
        last_processed = state.last_processed_date if state else None

        # Get scraper
        scraper = self._get_scraper(source, last_processed)
        if not scraper:
            return ScraperResult(success=False, errors=["No scraper available"])

        # Run scraper
        try:
            async with scraper:
                result = await scraper.scrape()

            if result.success:
                self.db.mark_source_success(source.url)
            else:
                error_msg = "; ".join(result.errors) if result.errors else "Unknown error"
                self.db.mark_source_failure(source.url, error_msg)

            return result

        except Exception as e:
            error_msg = str(e)
            logger.error(f"Failed to scrape {source.name}: {error_msg}")
            self.db.mark_source_failure(source.url, error_msg)
            return ScraperResult(success=False, errors=[error_msg])

    async def scrape_all_sources(self) -> list[NewsItem]:
        """Scrape all enabled sources and return collected items."""
        all_items: list[NewsItem] = []

        for source in self.config.enabled_sources:
            try:
                result = await self.scrape_source(source)
                all_items.extend(result.items)
                logger.info(
                    f"Source {source.name}: {len(result.items)} items, "
                    f"{result.skipped_count} skipped, {len(result.errors)} errors"
                )

                # Small delay between sources
                await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Error processing source {source.name}: {e}")
                continue

        logger.info(f"Total items collected: {len(all_items)}")
        return all_items

    def deduplicate(self, items: list[NewsItem]) -> list[NewsItem]:
        """Remove duplicates from items."""
        return self.deduplicator.process_batch(items)

    def classify(self, items: list[NewsItem]) -> list[NewsItem]:
        """Classify all items using LLM."""
        if not self.classifier:
            logger.warning("No classifier configured (missing API key)")
            return items

        return self.classifier.classify_batch(
            items,
            batch_size=self.config.processing.llm_batch_size
        )

    async def enrich(self, items: list[NewsItem]) -> list[NewsItem]:
        """Enrich items with low confidence."""
        if not self.enricher:
            logger.warning("No enricher configured")
            return items

        return await self.enricher.enrich_batch(items)

    def save_items(self, items: list[NewsItem]) -> tuple[int, int]:
        """Save items to database. Returns (new_count, updated_count)."""
        new_count = 0
        updated_count = 0

        for item in items:
            is_new = self.db.upsert_news_item(item)
            if is_new:
                new_count += 1
            else:
                updated_count += 1

        logger.info(f"Saved {new_count} new items, updated {updated_count} items")
        return new_count, updated_count

    async def send_digest(
        self,
        items: Optional[list[NewsItem]] = None,
        dry_run: bool = False
    ) -> bool:
        """Send email digest for today's items."""
        if items is None:
            items = self.db.get_items_by_date(date.today())

        # Filter out "Other" items for digest
        relevant_items = [item for item in items if item.news_type != "Other"]

        if not relevant_items:
            logger.info("No relevant items for digest")
            return True

        return await self.email_digest.send(
            items=relevant_items,
            dry_run=dry_run
        )

    async def run(
        self,
        skip_scrape: bool = False,
        skip_classify: bool = False,
        skip_enrich: bool = False,
        skip_email: bool = False,
        email_dry_run: bool = False,
        tracker: Optional[RunTracker] = None,
    ) -> dict:
        """
        Run the full pipeline.

        Args:
            tracker: Optional RunTracker for detailed statistics. If not provided, one will be created.

        Returns:
            dict with run statistics
        """
        # Create tracker if not provided
        if tracker is None:
            tracker = RunTracker(self.db)

        # Start the run with settings snapshot
        settings_snapshot = {
            "llm_provider": self.config.processing.llm_provider,
            "llm_model": self.config.processing.llm_model,
            "enrichment_provider": self.config.processing.enrichment_provider,
            "enrichment_model": self.config.processing.enrichment_model,
            "dedup_threshold": self.config.processing.dedup_similarity_threshold,
            "enabled_sources": [s.name for s in self.config.enabled_sources],
        }
        tracker.start_run(settings_snapshot)

        error_message = None

        try:
            # Step 1: Scrape
            if not skip_scrape:
                logger.info("=== STEP 1: SCRAPING ===")
                items = await self._scrape_all_sources_tracked(tracker)
            else:
                logger.info("Skipping scrape step")
                items = []

            if not items:
                logger.info("No items to process")
                tracker.end_run('completed')
                return tracker.get_summary()

            # Step 2: Deduplicate
            logger.info("=== STEP 2: DEDUPLICATION ===")
            original_count = len(items)
            items = self.deduplicate(items)
            duplicates_removed = original_count - len(items)
            tracker.total_duplicates = duplicates_removed
            logger.info(f"Deduplication: {original_count} -> {len(items)} ({duplicates_removed} duplicates)")

            if not items:
                logger.info("All items were duplicates")
                tracker.end_run('completed')
                return tracker.get_summary()

            # Step 3: Classify
            if not skip_classify:
                logger.info("=== STEP 3: CLASSIFICATION ===")
                items = self.classify(items)
                tracker.record_classified(len(items))
            else:
                logger.info("Skipping classification step")

            # Step 4: Enrich
            if not skip_enrich:
                logger.info("=== STEP 4: ENRICHMENT ===")
                items = await self._enrich_tracked(items, tracker)
            else:
                logger.info("Skipping enrichment step")

            # Step 5: Save
            logger.info("=== STEP 5: SAVING ===")
            new_count, updated_count = self.save_items(items)
            tracker.record_saved(new_count, updated_count)

            # Step 6: Email digest
            if not skip_email:
                logger.info("=== STEP 6: EMAIL DIGEST ===")
                email_sent = await self.send_digest(
                    items=items,
                    dry_run=email_dry_run
                )
                tracker.record_email_sent(email_sent)
            else:
                logger.info("Skipping email step")

        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            error_message = str(e)

        # End run
        status = 'failed' if error_message else 'completed'
        tracker.end_run(status, error_message)

        return tracker.get_summary()

    async def _scrape_all_sources_tracked(self, tracker: RunTracker) -> list[NewsItem]:
        """Scrape all sources with detailed tracking."""
        all_items: list[NewsItem] = []

        for source in self.config.enabled_sources:
            tracker.start_source(source.name, source.url, source.type)

            try:
                result = await self.scrape_source(source)

                # Record stats
                tracker.record_item_found(len(result.items) + result.skipped_count)
                tracker.record_item_scraped(len(result.items))

                # Record rejections by reason
                if result.skipped_count > 0:
                    tracker.record_item_rejected("keyword_filter", result.skipped_count)

                if result.errors:
                    tracker.record_source_error("; ".join(result.errors))

                all_items.extend(result.items)

                # Small delay between sources
                await asyncio.sleep(2)

            except Exception as e:
                tracker.record_source_error(str(e))
                logger.error(f"Error processing source {source.name}: {e}")

            tracker.end_source()

        return all_items

    async def _enrich_tracked(self, items: list[NewsItem], tracker: RunTracker) -> list[NewsItem]:
        """Enrich items with tracking."""
        if not self.enricher:
            logger.warning("No enricher configured")
            return items

        enriched_items = []
        for item in items:
            if not self.enricher.should_enrich(item):
                enriched_items.append(item)
                continue

            # Capture before state
            fields_before = {
                "news_type": item.news_type,
                "news_type_confidence": item.news_type_confidence,
                "region": item.region,
                "region_confidence": item.region_confidence,
                "related_people": item.related_people,
                "related_people_confidence": item.related_people_confidence,
                "company_name": item.company_name,
                "funding_amount": item.funding_amount,
            }

            try:
                enriched_item = await self.enricher.enrich_item(item)

                # Capture after state
                fields_after = {
                    "news_type": enriched_item.news_type,
                    "news_type_confidence": enriched_item.news_type_confidence,
                    "region": enriched_item.region,
                    "region_confidence": enriched_item.region_confidence,
                    "related_people": enriched_item.related_people,
                    "related_people_confidence": enriched_item.related_people_confidence,
                    "company_name": enriched_item.company_name,
                    "funding_amount": enriched_item.funding_amount,
                }

                # Record enrichment
                tracker.record_enrichment(
                    url_hash=item.url_hash,
                    title=item.title,
                    phase=enriched_item.enrichment_source,
                    fields_before=fields_before,
                    fields_after=fields_after,
                    success=True,
                )

                enriched_items.append(enriched_item)

            except Exception as e:
                logger.error(f"Enrichment error for {item.title[:30]}...: {e}")
                tracker.record_enrichment(
                    url_hash=item.url_hash,
                    title=item.title,
                    phase="error",
                    fields_before=fields_before,
                    fields_after=fields_before,
                    success=False,
                    error_message=str(e),
                )
                enriched_items.append(item)

        return enriched_items

    def get_stats(self) -> dict:
        """Get current database statistics."""
        return self.db.get_stats()


async def main(
    config_path: Optional[str] = None,
    skip_scrape: bool = False,
    skip_classify: bool = False,
    skip_enrich: bool = False,
    skip_email: bool = False,
    email_dry_run: bool = False,
):
    """Main entry point."""
    # Load configuration
    config = load_config(config_path)

    # Setup logging
    setup_logging(config)

    logger.info("Starting Belgian Startup News Aggregator")

    # Create aggregator and run
    aggregator = NewsAggregator(config)
    stats = await aggregator.run(
        skip_scrape=skip_scrape,
        skip_classify=skip_classify,
        skip_enrich=skip_enrich,
        skip_email=skip_email,
        email_dry_run=email_dry_run,
    )

    return stats


if __name__ == "__main__":
    asyncio.run(main())
