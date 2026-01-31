"""FastAPI backend for the news aggregator web interface."""

import asyncio
import os
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

import yaml

# Add parent to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.config import load_config, Config
from src.storage.database import Database
from src.storage.models import NewsItem
from src.main import NewsAggregator

logger = logging.getLogger(__name__)

# Global state
config: Optional[Config] = None
db: Optional[Database] = None
aggregator: Optional[NewsAggregator] = None
run_status = {"running": False, "last_run": None, "last_result": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup."""
    global config, db, aggregator
    config = load_config()
    db = Database(config.database_path)
    aggregator = NewsAggregator(config)
    logger.info("Web API initialized")
    yield
    logger.info("Web API shutting down")


app = FastAPI(
    title="Belgian Startup News Aggregator",
    description="Web interface for managing and viewing startup news",
    version="1.0.0",
    lifespan=lifespan,
)

# Mount static files and templates
static_path = Path(__file__).parent / "static"
templates_path = Path(__file__).parent / "templates"
app.mount("/static", StaticFiles(directory=static_path), name="static")
templates = Jinja2Templates(directory=templates_path)


# ============== Pydantic Models ==============

class SourceCreate(BaseModel):
    url: str
    type: str = "website"
    name: str
    enabled: bool = True
    note: Optional[str] = None
    config: dict = Field(default_factory=dict)


class SourceUpdate(BaseModel):
    url: Optional[str] = None
    type: Optional[str] = None
    name: Optional[str] = None
    enabled: Optional[bool] = None
    note: Optional[str] = None
    config: Optional[dict] = None


class KeywordCategory(BaseModel):
    category: str
    keywords: list[str]


class SettingsUpdate(BaseModel):
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    dedup_similarity_threshold: Optional[float] = None
    max_enrichment_attempts: Optional[int] = None
    min_request_delay: Optional[float] = None
    max_request_delay: Optional[float] = None


class ApiKeysUpdate(BaseModel):
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_username: Optional[str] = None
    smtp_password: Optional[str] = None
    email_from: Optional[str] = None


class EmailRecipients(BaseModel):
    recipients: list[str]


# ============== Helper Functions ==============

def get_config_path() -> Path:
    """Get path to settings.yaml."""
    return Path(__file__).parent.parent.parent / "config" / "settings.yaml"


def get_env_path() -> Path:
    """Get path to .env file."""
    return Path(__file__).parent.parent.parent / "config" / ".env"


def load_yaml_config() -> dict:
    """Load raw YAML configuration."""
    config_path = get_config_path()
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def save_yaml_config(data: dict):
    """Save YAML configuration."""
    config_path = get_config_path()
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def load_env_file() -> dict:
    """Load .env file as dictionary."""
    env_path = get_env_path()
    env_vars = {}
    if env_path.exists():
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = value.strip()
    return env_vars


def save_env_file(env_vars: dict):
    """Save dictionary to .env file."""
    env_path = get_env_path()
    lines = []

    # Group by category
    categories = {
        "LLM API Keys": ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"],
        "Email Configuration": ["SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_FROM"],
        "Database": ["DATABASE_PATH"],
    }

    for category, keys in categories.items():
        lines.append(f"# {category}")
        for key in keys:
            if key in env_vars and env_vars[key]:
                lines.append(f"{key}={env_vars[key]}")
        lines.append("")

    with open(env_path, 'w') as f:
        f.write("\n".join(lines))


def reload_config():
    """Reload configuration after changes."""
    global config, aggregator
    config = load_config()
    aggregator = NewsAggregator(config)


# ============== Page Routes ==============

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page."""
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/news", response_class=HTMLResponse)
async def news_page(request: Request):
    """News items page."""
    return templates.TemplateResponse("news.html", {"request": request})


@app.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request):
    """Sources management page."""
    return templates.TemplateResponse("sources.html", {"request": request})


@app.get("/keywords", response_class=HTMLResponse)
async def keywords_page(request: Request):
    """Keywords management page."""
    return templates.TemplateResponse("keywords.html", {"request": request})


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Settings page."""
    return templates.TemplateResponse("settings.html", {"request": request})


# ============== API Routes: Stats ==============

@app.get("/api/stats")
async def get_stats():
    """Get database and system statistics."""
    stats = db.get_stats()
    stats["run_status"] = run_status
    stats["llm_provider"] = config.processing.llm_provider
    stats["sources_count"] = len(config.sources)
    stats["enabled_sources"] = len(config.enabled_sources)
    return stats


# ============== API Routes: News Items ==============

@app.get("/api/news")
async def get_news_items(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    news_type: Optional[str] = None,
    region: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    """Get news items with pagination and filtering."""
    # Get all recent items (last 90 days for performance)
    items = db.get_recent_items(days=90)

    # Apply filters
    if news_type and news_type != "all":
        items = [i for i in items if i.news_type == news_type]

    if region and region != "all":
        items = [i for i in items if i.region == region]

    if search:
        search_lower = search.lower()
        items = [i for i in items if
                 search_lower in (i.title or "").lower() or
                 search_lower in (i.company_name or "").lower() or
                 search_lower in (i.summary or "").lower()]

    if date_from:
        try:
            from_date = datetime.strptime(date_from, "%Y-%m-%d").date()
            items = [i for i in items if i.pub_date and i.pub_date >= from_date]
        except ValueError:
            pass

    if date_to:
        try:
            to_date = datetime.strptime(date_to, "%Y-%m-%d").date()
            items = [i for i in items if i.pub_date and i.pub_date <= to_date]
        except ValueError:
            pass

    # Pagination
    total = len(items)
    start = (page - 1) * limit
    end = start + limit
    paginated_items = items[start:end]

    return {
        "items": [i.to_dict() for i in paginated_items],
        "total": total,
        "page": page,
        "limit": limit,
        "pages": (total + limit - 1) // limit,
    }


@app.get("/api/news/{url_hash}")
async def get_news_item(url_hash: str):
    """Get a single news item by hash."""
    item = db.get_news_item(url_hash)
    if not item:
        raise HTTPException(status_code=404, detail="News item not found")
    return item.to_dict()


@app.delete("/api/news/{url_hash}")
async def delete_news_item(url_hash: str):
    """Delete a news item."""
    # Note: This would require adding a delete method to the database
    # For now, return not implemented
    raise HTTPException(status_code=501, detail="Delete not implemented")


# ============== API Routes: Sources ==============

@app.get("/api/sources")
async def get_sources():
    """Get all sources."""
    yaml_config = load_yaml_config()
    return yaml_config.get("sources", [])


@app.post("/api/sources")
async def create_source(source: SourceCreate):
    """Add a new source."""
    yaml_config = load_yaml_config()
    sources = yaml_config.get("sources", [])

    # Check for duplicate URL
    if any(s["url"] == source.url for s in sources):
        raise HTTPException(status_code=400, detail="Source URL already exists")

    new_source = {
        "url": source.url,
        "type": source.type,
        "name": source.name,
        "enabled": source.enabled,
    }
    if source.note:
        new_source["note"] = source.note
    if source.config:
        new_source["config"] = source.config

    sources.append(new_source)
    yaml_config["sources"] = sources
    save_yaml_config(yaml_config)
    reload_config()

    return {"status": "created", "source": new_source}


@app.put("/api/sources/{index}")
async def update_source(index: int, source: SourceUpdate):
    """Update a source by index."""
    yaml_config = load_yaml_config()
    sources = yaml_config.get("sources", [])

    if index < 0 or index >= len(sources):
        raise HTTPException(status_code=404, detail="Source not found")

    # Update only provided fields
    if source.url is not None:
        sources[index]["url"] = source.url
    if source.type is not None:
        sources[index]["type"] = source.type
    if source.name is not None:
        sources[index]["name"] = source.name
    if source.enabled is not None:
        sources[index]["enabled"] = source.enabled
    if source.note is not None:
        sources[index]["note"] = source.note
    if source.config is not None:
        sources[index]["config"] = source.config

    yaml_config["sources"] = sources
    save_yaml_config(yaml_config)
    reload_config()

    return {"status": "updated", "source": sources[index]}


@app.delete("/api/sources/{index}")
async def delete_source(index: int):
    """Delete a source by index."""
    yaml_config = load_yaml_config()
    sources = yaml_config.get("sources", [])

    if index < 0 or index >= len(sources):
        raise HTTPException(status_code=404, detail="Source not found")

    deleted = sources.pop(index)
    yaml_config["sources"] = sources
    save_yaml_config(yaml_config)
    reload_config()

    return {"status": "deleted", "source": deleted}


# ============== API Routes: Keywords ==============

@app.get("/api/keywords")
async def get_keywords():
    """Get all theme keywords."""
    yaml_config = load_yaml_config()
    return yaml_config.get("theme_keywords", {})


@app.put("/api/keywords")
async def update_keywords(keywords: dict):
    """Update all theme keywords."""
    yaml_config = load_yaml_config()
    yaml_config["theme_keywords"] = keywords
    save_yaml_config(yaml_config)
    reload_config()
    return {"status": "updated", "keywords": keywords}


@app.post("/api/keywords/{category}")
async def add_keyword_category(category: str, data: KeywordCategory):
    """Add or update a keyword category."""
    yaml_config = load_yaml_config()
    keywords = yaml_config.get("theme_keywords", {})
    keywords[category] = data.keywords
    yaml_config["theme_keywords"] = keywords
    save_yaml_config(yaml_config)
    reload_config()
    return {"status": "updated", "category": category, "keywords": data.keywords}


@app.delete("/api/keywords/{category}")
async def delete_keyword_category(category: str):
    """Delete a keyword category."""
    yaml_config = load_yaml_config()
    keywords = yaml_config.get("theme_keywords", {})

    if category not in keywords:
        raise HTTPException(status_code=404, detail="Category not found")

    del keywords[category]
    yaml_config["theme_keywords"] = keywords
    save_yaml_config(yaml_config)
    reload_config()

    return {"status": "deleted", "category": category}


# ============== API Routes: Settings ==============

@app.get("/api/settings")
async def get_settings():
    """Get processing settings."""
    yaml_config = load_yaml_config()
    processing = yaml_config.get("processing", {})
    return {
        "llm_provider": processing.get("llm_provider", "openai"),
        "llm_model": processing.get("llm_model", ""),
        "dedup_similarity_threshold": processing.get("dedup_similarity_threshold", 0.85),
        "max_enrichment_attempts": processing.get("max_enrichment_attempts", 2),
        "min_request_delay": processing.get("min_request_delay", 2.0),
        "max_request_delay": processing.get("max_request_delay", 5.0),
        "llm_max_tokens": processing.get("llm_max_tokens", 1024),
        "llm_batch_size": processing.get("llm_batch_size", 10),
    }


@app.put("/api/settings")
async def update_settings(settings: SettingsUpdate):
    """Update processing settings."""
    yaml_config = load_yaml_config()
    processing = yaml_config.get("processing", {})

    if settings.llm_provider is not None:
        processing["llm_provider"] = settings.llm_provider
    if settings.llm_model is not None:
        processing["llm_model"] = settings.llm_model
    if settings.dedup_similarity_threshold is not None:
        processing["dedup_similarity_threshold"] = settings.dedup_similarity_threshold
    if settings.max_enrichment_attempts is not None:
        processing["max_enrichment_attempts"] = settings.max_enrichment_attempts
    if settings.min_request_delay is not None:
        processing["min_request_delay"] = settings.min_request_delay
    if settings.max_request_delay is not None:
        processing["max_request_delay"] = settings.max_request_delay

    yaml_config["processing"] = processing
    save_yaml_config(yaml_config)
    reload_config()

    return {"status": "updated", "settings": processing}


# ============== API Routes: API Keys ==============

@app.get("/api/keys")
async def get_api_keys():
    """Get API keys (masked for security)."""
    env_vars = load_env_file()

    def mask_key(key: str) -> str:
        if not key or len(key) < 8:
            return ""
        return key[:4] + "..." + key[-4:]

    return {
        "openai_api_key": mask_key(env_vars.get("OPENAI_API_KEY", "")),
        "anthropic_api_key": mask_key(env_vars.get("ANTHROPIC_API_KEY", "")),
        "gemini_api_key": mask_key(env_vars.get("GEMINI_API_KEY", "")),
        "smtp_host": env_vars.get("SMTP_HOST", ""),
        "smtp_port": env_vars.get("SMTP_PORT", "587"),
        "smtp_username": env_vars.get("SMTP_USERNAME", ""),
        "smtp_password": mask_key(env_vars.get("SMTP_PASSWORD", "")),
        "email_from": env_vars.get("EMAIL_FROM", ""),
        # Show which keys are set
        "has_openai": bool(env_vars.get("OPENAI_API_KEY")),
        "has_anthropic": bool(env_vars.get("ANTHROPIC_API_KEY")),
        "has_gemini": bool(env_vars.get("GEMINI_API_KEY")),
    }


@app.put("/api/keys")
async def update_api_keys(keys: ApiKeysUpdate):
    """Update API keys."""
    env_vars = load_env_file()

    # Only update non-None values
    if keys.openai_api_key is not None:
        env_vars["OPENAI_API_KEY"] = keys.openai_api_key
    if keys.anthropic_api_key is not None:
        env_vars["ANTHROPIC_API_KEY"] = keys.anthropic_api_key
    if keys.gemini_api_key is not None:
        env_vars["GEMINI_API_KEY"] = keys.gemini_api_key
    if keys.smtp_host is not None:
        env_vars["SMTP_HOST"] = keys.smtp_host
    if keys.smtp_port is not None:
        env_vars["SMTP_PORT"] = str(keys.smtp_port)
    if keys.smtp_username is not None:
        env_vars["SMTP_USERNAME"] = keys.smtp_username
    if keys.smtp_password is not None:
        env_vars["SMTP_PASSWORD"] = keys.smtp_password
    if keys.email_from is not None:
        env_vars["EMAIL_FROM"] = keys.email_from

    save_env_file(env_vars)

    # Update environment variables in current process
    for key, value in env_vars.items():
        os.environ[key] = value

    reload_config()

    return {"status": "updated"}


# ============== API Routes: Email Recipients ==============

@app.get("/api/recipients")
async def get_recipients():
    """Get email recipients."""
    yaml_config = load_yaml_config()
    return {"recipients": yaml_config.get("email_recipients", [])}


@app.put("/api/recipients")
async def update_recipients(data: EmailRecipients):
    """Update email recipients."""
    yaml_config = load_yaml_config()
    yaml_config["email_recipients"] = data.recipients
    save_yaml_config(yaml_config)
    reload_config()
    return {"status": "updated", "recipients": data.recipients}


# ============== API Routes: Pipeline Control ==============

async def run_pipeline_task(
    skip_scrape: bool = False,
    skip_classify: bool = False,
    skip_enrich: bool = False,
    skip_email: bool = False,
):
    """Background task to run the pipeline."""
    global run_status, aggregator

    run_status["running"] = True
    run_status["started_at"] = datetime.now().isoformat()

    try:
        # Reload config and create fresh aggregator
        reload_config()

        result = await aggregator.run(
            skip_scrape=skip_scrape,
            skip_classify=skip_classify,
            skip_enrich=skip_enrich,
            skip_email=skip_email,
        )

        run_status["last_result"] = result
        run_status["last_run"] = datetime.now().isoformat()

    except Exception as e:
        run_status["last_result"] = {"error": str(e)}
        logger.error(f"Pipeline error: {e}")

    finally:
        run_status["running"] = False


@app.post("/api/run")
async def trigger_run(
    background_tasks: BackgroundTasks,
    skip_scrape: bool = False,
    skip_classify: bool = False,
    skip_enrich: bool = False,
    skip_email: bool = True,  # Default to skip email for manual runs
):
    """Trigger a pipeline run."""
    if run_status["running"]:
        raise HTTPException(status_code=409, detail="Pipeline is already running")

    background_tasks.add_task(
        run_pipeline_task,
        skip_scrape=skip_scrape,
        skip_classify=skip_classify,
        skip_enrich=skip_enrich,
        skip_email=skip_email,
    )

    return {"status": "started", "message": "Pipeline started in background"}


@app.get("/api/run/status")
async def get_run_status():
    """Get current pipeline run status."""
    return run_status


# ============== Run Server ==============

def run_server(host: str = "0.0.0.0", port: int = 8000):
    """Run the web server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
