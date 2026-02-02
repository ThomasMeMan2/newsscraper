"""Main orchestrator for the news aggregation pipeline."""

import asyncio
import logging
from datetime import datetime, date
from typing import Optional

from .config import Config, load_config, setup_logging, SourceConfig
from .storage.database import Database
from .storage.models import NewsItem
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
    ) -> dict:
        """
        Run the full pipeline.

        Returns:
            dict with run statistics
        """
        stats = {
            "started_at": datetime.now().isoformat(),
            "sources_processed": 0,
            "items_scraped": 0,
            "items_after_dedup": 0,
            "items_classified": 0,
            "items_enriched": 0,
            "items_saved_new": 0,
            "items_saved_updated": 0,
            "email_sent": False,
            "errors": [],
        }

        try:
            # Step 1: Scrape
            if not skip_scrape:
                logger.info("=== STEP 1: SCRAPING ===")
                items = await self.scrape_all_sources()
                stats["sources_processed"] = len(self.config.enabled_sources)
                stats["items_scraped"] = len(items)
            else:
                logger.info("Skipping scrape step")
                items = []

            if not items:
                logger.info("No items to process")
                stats["completed_at"] = datetime.now().isoformat()
                return stats

            # Step 2: Deduplicate
            logger.info("=== STEP 2: DEDUPLICATION ===")
            items = self.deduplicate(items)
            stats["items_after_dedup"] = len(items)

            if not items:
                logger.info("All items were duplicates")
                stats["completed_at"] = datetime.now().isoformat()
                return stats

            # Step 3: Classify
            if not skip_classify:
                logger.info("=== STEP 3: CLASSIFICATION ===")
                items = self.classify(items)
                stats["items_classified"] = len(items)
            else:
                logger.info("Skipping classification step")

            # Step 4: Enrich
            if not skip_enrich:
                logger.info("=== STEP 4: ENRICHMENT ===")
                items = await self.enrich(items)
                stats["items_enriched"] = len([i for i in items if i.enrichment_source != "none"])
            else:
                logger.info("Skipping enrichment step")

            # Step 5: Save
            logger.info("=== STEP 5: SAVING ===")
            new_count, updated_count = self.save_items(items)
            stats["items_saved_new"] = new_count
            stats["items_saved_updated"] = updated_count

            # Step 6: Email digest
            if not skip_email:
                logger.info("=== STEP 6: EMAIL DIGEST ===")
                stats["email_sent"] = await self.send_digest(
                    items=items,
                    dry_run=email_dry_run
                )
            else:
                logger.info("Skipping email step")

        except Exception as e:
            logger.error(f"Pipeline error: {e}")
            stats["errors"].append(str(e))

        stats["completed_at"] = datetime.now().isoformat()

        # Log summary
        logger.info("=== PIPELINE COMPLETE ===")
        logger.info(f"Items: {stats['items_scraped']} scraped -> {stats['items_after_dedup']} unique -> {stats['items_saved_new']} new")

        return stats

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
