#!/usr/bin/env python3
"""CLI entry point for the Belgian Startup News Aggregator."""

import argparse
import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.main import main, NewsAggregator
from src.config import load_config, setup_logging


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Belgian Startup News Aggregator - Scrape, classify, and digest Belgian startup news"
    )

    parser.add_argument(
        "--config", "-c",
        type=str,
        default=None,
        help="Path to configuration file (default: config/settings.yaml)"
    )

    parser.add_argument(
        "--skip-scrape",
        action="store_true",
        help="Skip the scraping step (useful for testing other steps)"
    )

    parser.add_argument(
        "--skip-classify",
        action="store_true",
        help="Skip the LLM classification step"
    )

    parser.add_argument(
        "--skip-enrich",
        action="store_true",
        help="Skip the enrichment step"
    )

    parser.add_argument(
        "--skip-email",
        action="store_true",
        help="Skip sending the email digest"
    )

    parser.add_argument(
        "--email-dry-run",
        action="store_true",
        help="Generate email but don't send it"
    )

    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print database statistics and exit"
    )

    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialize the database and exit"
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose/debug logging"
    )

    return parser.parse_args()


def run_stats(config_path: str = None):
    """Print database statistics."""
    config = load_config(config_path)
    from src.storage.database import Database
    db = Database(config.database_path)
    stats = db.get_stats()

    print("\n=== DATABASE STATISTICS ===")
    print(f"Total items: {stats['total_items']}")
    print(f"Items today: {stats['today']}")
    print(f"Duplicates detected: {stats['duplicates']}")
    print(f"Pending HubSpot sync: {stats['pending_sync']}")

    print("\nItems by type:")
    for news_type, count in stats.get('by_type', {}).items():
        print(f"  {news_type}: {count}")

    print()


def init_database(config_path: str = None):
    """Initialize the database."""
    config = load_config(config_path)
    from src.storage.database import Database
    db = Database(config.database_path)
    print(f"Database initialized at: {config.database_path}")
    stats = db.get_stats()
    print(f"Total items: {stats['total_items']}")


def main_cli():
    """Main CLI entry point."""
    args = parse_args()

    # Handle special commands
    if args.stats:
        run_stats(args.config)
        return 0

    if args.init_db:
        init_database(args.config)
        return 0

    # Override log level if verbose
    if args.verbose:
        import logging
        logging.getLogger().setLevel(logging.DEBUG)

    # Run the main pipeline
    try:
        stats = asyncio.run(main(
            config_path=args.config,
            skip_scrape=args.skip_scrape,
            skip_classify=args.skip_classify,
            skip_enrich=args.skip_enrich,
            skip_email=args.skip_email,
            email_dry_run=args.email_dry_run,
        ))

        # Print summary
        print("\n=== RUN SUMMARY ===")
        print(f"Started: {stats.get('started_at', 'N/A')}")
        print(f"Completed: {stats.get('completed_at', 'N/A')}")
        print(f"Sources processed: {stats.get('sources_processed', 0)}")
        print(f"Items scraped: {stats.get('items_scraped', 0)}")
        print(f"After deduplication: {stats.get('items_after_dedup', 0)}")
        print(f"New items saved: {stats.get('items_saved_new', 0)}")
        print(f"Items updated: {stats.get('items_saved_updated', 0)}")
        print(f"Email sent: {stats.get('email_sent', False)}")

        if stats.get('errors'):
            print(f"\nErrors: {len(stats['errors'])}")
            for error in stats['errors']:
                print(f"  - {error}")

        return 0 if not stats.get('errors') else 1

    except KeyboardInterrupt:
        print("\nInterrupted by user")
        return 130
    except Exception as e:
        print(f"\nFatal error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main_cli())
