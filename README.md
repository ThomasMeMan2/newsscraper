# Belgian Startup News Aggregator

An automated pipeline that aggregates Belgian startup and venture capital news from multiple sources, deduplicates, classifies using AI, enriches, and outputs structured data with an email digest.

## Features

- **Multi-source scraping**: RSS feeds and websites
- **Smart deduplication**: Exact URL matching + semantic similarity detection
- **AI-powered classification**: Uses LLM (OpenAI, Anthropic, or Gemini) to classify news into categories (Funding, Acquisition, Merger, Exit, Startup, Scale-up, Fund, Other)
- **Automatic enrichment**: Fetches full article content for low-confidence classifications
- **Email digest**: Daily summary grouped by news type
- **HubSpot-ready**: Schema includes sync tracking for future CRM integration

## Data Sources

| Source | Type | Status |
|--------|------|--------|
| De Tijd - Durfkapitaal RSS | RSS | ✅ Active |
| Forbes.be - Ondernemers | Website | ✅ Active |
| Forbes.be - Bedrijf | Website | ✅ Active |
| Made in Belgium | Website | ✅ Active |
| Trends - Ondernemen | Website | ✅ Active |
| LinkedIn | N/A | ⚠️ Disabled (requires manual review) |

## Installation

### Prerequisites

- Python 3.11+
- API key for one of: **OpenAI** (default), **Anthropic**, or **Google Gemini**
- SMTP credentials for email digest (optional)

### Setup

1. Clone the repository:
```bash
git clone <repository-url>
cd newsscraper
```

2. Create and activate a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate  # Windows
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Install Playwright browsers (for JavaScript-heavy sites):
```bash
playwright install chromium
```

5. Create your environment file:
```bash
cp config/.env.example config/.env
# Edit config/.env with your API keys
```

6. Initialize the database:
```bash
python run.py --init-db
```

## Configuration

### Environment Variables (config/.env)

```env
# LLM API Keys (only one required - based on llm_provider in settings.yaml)
OPENAI_API_KEY=sk-xxxxx          # Default provider
ANTHROPIC_API_KEY=sk-ant-xxxxx   # Alternative
GEMINI_API_KEY=xxxxx             # Alternative

# Required for email digest
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-app-password
EMAIL_FROM=your-email@gmail.com
```

### Switching LLM Providers

In `config/settings.yaml`, change the `llm_provider` setting:

```yaml
processing:
  # Options: openai, anthropic, gemini
  llm_provider: "openai"      # Uses gpt-4o-mini by default
  # llm_provider: "anthropic" # Uses claude-3-5-haiku by default
  # llm_provider: "gemini"    # Uses gemini-1.5-flash by default

  # Optional: Override the default model
  llm_model: ""  # Leave empty for provider default
```

### Settings (config/settings.yaml)

The main configuration file includes:
- **sources**: List of news sources with scraping configuration
- **theme_keywords**: Keywords used to filter relevant articles
- **email_recipients**: List of email addresses for the digest
- **processing**: Deduplication threshold, LLM settings, delays

## Usage

### Run Full Pipeline

```bash
python run.py
```

### Command Line Options

```bash
# Skip specific steps
python run.py --skip-scrape      # Skip scraping (use existing data)
python run.py --skip-classify    # Skip LLM classification
python run.py --skip-enrich      # Skip enrichment
python run.py --skip-email       # Skip email digest

# Test email without sending
python run.py --email-dry-run

# View statistics
python run.py --stats

# Enable debug logging
python run.py --verbose

# Custom config file
python run.py --config /path/to/settings.yaml
```

### Scheduling (Cron)

Add to crontab for daily execution at 8 AM:
```bash
0 8 * * * cd /path/to/newsscraper && /path/to/venv/bin/python run.py >> logs/cron.log 2>&1
```

## Pipeline Steps

1. **Scrape**: Fetch articles from all enabled sources
2. **Deduplicate**: Remove exact and semantic duplicates
3. **Classify**: Use Claude AI to categorize and extract structured data
4. **Enrich**: Fetch full content for low-confidence items
5. **Save**: Store in SQLite database
6. **Email**: Send daily digest to recipients

## Output Schema

Each news item includes:

| Field | Description |
|-------|-------------|
| `url_hash` | Unique 8-char identifier |
| `source_url` | Original article URL |
| `title` | Article title |
| `pub_date` | Publication date |
| `news_type` | Category (Funding, Acquisition, etc.) |
| `region` | Geographic region |
| `company_name` | Primary company mentioned |
| `related_people` | Names of key people |
| `funding_amount` | Investment amount if applicable |
| `summary` | Dutch summary |
| `enrichment_source` | none/scrape/search |
| `hubspot_synced` | Sync status for CRM |

## Project Structure

```
newsscraper/
├── config/
│   ├── settings.yaml      # Main configuration
│   └── .env.example       # Environment template
├── src/
│   ├── main.py            # Pipeline orchestrator
│   ├── config.py          # Configuration loader
│   ├── scrapers/          # Source scrapers
│   │   ├── base.py        # Base scraper class
│   │   ├── rss_scraper.py # RSS feed handler
│   │   └── web_scraper.py # Website scraper
│   ├── processing/        # Data processing
│   │   ├── deduplication.py
│   │   ├── classifier.py  # LLM classification
│   │   └── enrichment.py
│   ├── storage/           # Database operations
│   │   ├── database.py
│   │   └── models.py
│   └── output/
│       └── email_digest.py
├── data/                  # SQLite database (gitignored)
├── logs/                  # Log files (gitignored)
├── tests/
├── requirements.txt
├── run.py                 # CLI entry point
└── README.md
```

## Error Handling

- **Source unavailable**: Logged, continues with other sources
- **Scraping blocked**: Retries with exponential backoff
- **Classification failure**: Defaults to "Other" with low confidence
- **Rate limits**: Automatic delays between requests
- **Duplicates**: Errs on inclusion (better duplicate than missed)

## Extending

### Adding a New Source

1. Add source configuration to `config/settings.yaml`
2. If needed, create custom scraper in `src/scrapers/`

### Custom Website Selectors

Configure CSS selectors in `settings.yaml`:
```yaml
- url: "https://example.com/news"
  type: "website"
  name: "Example News"
  config:
    article_selector: "article.post"
    title_selector: "h2 a"
    link_selector: "h2 a"
    date_selector: "time"
    max_pages: 3
```

## Future Enhancements

- [ ] HubSpot CRM integration
- [ ] Slack notifications
- [ ] Web dashboard
- [ ] Custom alert rules
- [ ] Multi-language support

## License

MIT License
