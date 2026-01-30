"""Configuration loader for the news aggregator."""

import os
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


@dataclass
class SourceConfig:
    """Configuration for a single news source."""
    url: str
    type: str  # rss, website, linkedin
    name: str
    enabled: bool = True
    note: Optional[str] = None
    config: dict = field(default_factory=dict)


@dataclass
class ProcessingConfig:
    """Processing-related configuration."""
    dedup_similarity_threshold: float = 0.85
    max_enrichment_attempts: int = 2
    min_request_delay: float = 2.0
    max_request_delay: float = 5.0
    llm_model: str = "claude-3-5-haiku-20241022"
    llm_max_tokens: int = 1024
    llm_batch_size: int = 10


@dataclass
class LoggingConfig:
    """Logging configuration."""
    level: str = "INFO"
    file: str = "logs/scraper.log"
    max_bytes: int = 10485760
    backup_count: int = 5


@dataclass
class Config:
    """Main configuration container."""
    sources: list[SourceConfig] = field(default_factory=list)
    theme_keywords: dict[str, list[str]] = field(default_factory=dict)
    email_recipients: list[str] = field(default_factory=list)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    # Environment variables (loaded from .env)
    anthropic_api_key: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    email_from: str = ""
    database_path: str = ""

    @property
    def enabled_sources(self) -> list[SourceConfig]:
        """Get only enabled sources."""
        return [s for s in self.sources if s.enabled]

    def get_all_theme_keywords(self) -> list[str]:
        """Get flattened list of all theme keywords."""
        keywords = []
        for keyword_list in self.theme_keywords.values():
            keywords.extend(keyword_list)
        return keywords


def load_config(
    config_path: Optional[str | Path] = None,
    env_path: Optional[str | Path] = None
) -> Config:
    """Load configuration from YAML file and environment variables."""

    # Determine paths
    base_dir = Path(__file__).parent.parent
    if config_path is None:
        config_path = base_dir / "config" / "settings.yaml"
    else:
        config_path = Path(config_path)

    if env_path is None:
        # Try multiple locations for .env
        for env_candidate in [
            base_dir / "config" / ".env",
            base_dir / ".env",
        ]:
            if env_candidate.exists():
                env_path = env_candidate
                break
    else:
        env_path = Path(env_path)

    # Load environment variables
    if env_path and Path(env_path).exists():
        load_dotenv(env_path)
        logger.info(f"Loaded environment from {env_path}")

    # Load YAML config
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path, 'r', encoding='utf-8') as f:
        yaml_config = yaml.safe_load(f)

    # Parse sources
    sources = []
    for source_data in yaml_config.get('sources', []):
        sources.append(SourceConfig(
            url=source_data['url'],
            type=source_data['type'],
            name=source_data['name'],
            enabled=source_data.get('enabled', True),
            note=source_data.get('note'),
            config=source_data.get('config', {}),
        ))

    # Parse processing config
    proc_data = yaml_config.get('processing', {})
    processing = ProcessingConfig(
        dedup_similarity_threshold=proc_data.get('dedup_similarity_threshold', 0.85),
        max_enrichment_attempts=proc_data.get('max_enrichment_attempts', 2),
        min_request_delay=proc_data.get('min_request_delay', 2.0),
        max_request_delay=proc_data.get('max_request_delay', 5.0),
        llm_model=proc_data.get('llm_model', 'claude-3-5-haiku-20241022'),
        llm_max_tokens=proc_data.get('llm_max_tokens', 1024),
        llm_batch_size=proc_data.get('llm_batch_size', 10),
    )

    # Parse logging config
    log_data = yaml_config.get('logging', {})
    logging_config = LoggingConfig(
        level=log_data.get('level', 'INFO'),
        file=log_data.get('file', 'logs/scraper.log'),
        max_bytes=log_data.get('max_bytes', 10485760),
        backup_count=log_data.get('backup_count', 5),
    )

    # Build config object
    config = Config(
        sources=sources,
        theme_keywords=yaml_config.get('theme_keywords', {}),
        email_recipients=yaml_config.get('email_recipients', []),
        processing=processing,
        logging=logging_config,
        # Environment variables
        anthropic_api_key=os.getenv('ANTHROPIC_API_KEY', ''),
        smtp_host=os.getenv('SMTP_HOST', 'smtp.gmail.com'),
        smtp_port=int(os.getenv('SMTP_PORT', '587')),
        smtp_username=os.getenv('SMTP_USERNAME', ''),
        smtp_password=os.getenv('SMTP_PASSWORD', ''),
        email_from=os.getenv('EMAIL_FROM', ''),
        database_path=os.getenv('DATABASE_PATH', str(base_dir / 'data' / 'news.db')),
    )

    logger.info(f"Loaded configuration with {len(config.enabled_sources)} enabled sources")
    return config


def setup_logging(config: Config):
    """Set up logging based on configuration."""
    log_path = Path(config.logging.file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # File handler with rotation
    from logging.handlers import RotatingFileHandler
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=config.logging.max_bytes,
        backupCount=config.logging.backup_count,
    )
    file_handler.setFormatter(formatter)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, config.logging.level.upper()))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Reduce noise from httpx
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)

    logger.info("Logging configured")
