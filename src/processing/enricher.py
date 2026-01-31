"""Improved enrichment pipeline for news items with low confidence.

Uses a multi-phase approach:
1. Article scraping: Fetch full article content for better context
2. Search enrichment: Use OpenAI with web search to find missing info
"""

import asyncio
import json
import logging
from typing import Optional
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

from .classifier import BaseClassifier
from .amount_parser import normalize_funding_amount
from ..storage.models import NewsItem

logger = logging.getLogger(__name__)


# Enrichment prompt for search-based enrichment
SEARCH_ENRICHMENT_PROMPT = """Je bent een research analist die ontbrekende informatie over nieuwsartikelen moet opzoeken.

## Huidige Data (incompleet)
{current_data}

## Wat ontbreekt (velden met "low" confidence moeten verbeterd worden)
{missing_fields}

## Instructies
1. Zoek naar "{company_name}" in combinatie met relevante termen
2. Focus vooral op het vinden van:
   - Related People (founders, CEO, investors, key executives)
   - Region (Belgium, Netherlands, UK, of ander land)
   - Funding details indien van toepassing

## Output Format
Antwoord UITSLUITEND met valid JSON in exact dit formaat:

{{
  "newsType": "Funding|Acquisition|Merger|Exit|Startup|Scale-up|Fund|Other",
  "newsTypeConfidence": "high|low",
  "region": "Belgium|Netherlands|UK|EU|US|Other",
  "regionConfidence": "high|low",
  "relatedPeople": [
    {{"name": "Jan Janssen", "role": "CEO/Founder/Investor/etc"}},
    {{"name": "Piet Pietersen", "role": "CTO"}}
  ],
  "relatedPeopleConfidence": "high|low",
  "companyName": "TechCo",
  "fundingAmount": "€5M",
  "summary": "Korte samenvatting."
}}

BELANGRIJK:
- relatedPeople moet een array van objecten zijn met name en role
- Gebruik ALLEEN informatie die je daadwerkelijk hebt gevonden
- Als je iets niet kunt vinden, behoud de oorspronkelijke waarde
"""


@dataclass
class EnrichmentResult:
    """Result of enrichment with structured data."""
    news_type: Optional[str] = None
    news_type_confidence: Optional[str] = None
    region: Optional[str] = None
    region_confidence: Optional[str] = None
    related_people: Optional[list] = None
    related_people_confidence: Optional[str] = None
    company_name: Optional[str] = None
    funding_amount: Optional[str] = None
    summary: Optional[str] = None
    enrichment_source: str = "none"


class Enricher:
    """Enriches news items using article scraping and web search."""

    def __init__(
        self,
        classifier: BaseClassifier,
        max_attempts: int = 2,
        min_delay: float = 2.0,
        max_delay: float = 5.0,
        search_api_key: Optional[str] = None,
        search_model: str = "gpt-4o-mini",
    ):
        self.classifier = classifier
        self.max_attempts = max_attempts
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.search_api_key = search_api_key
        self.search_model = search_model
        self._http_client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._http_client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *args):
        if self._http_client:
            await self._http_client.aclose()

    def should_enrich(self, item: NewsItem) -> bool:
        """Determine if an item needs enrichment."""
        # Skip items classified as Other
        if item.news_type == "Other":
            return False

        # Skip if max attempts reached
        if item.enrichment_attempts >= self.max_attempts:
            return False

        # Enrich if any confidence is not high
        return any([
            item.news_type_confidence != "high",
            item.region_confidence != "high",
            item.related_people_confidence != "high",
            not item.related_people,  # Always try to find related people if empty
        ])

    async def _fetch_full_article(self, url: str) -> Optional[str]:
        """Fetch and extract full article content using BeautifulSoup."""
        try:
            if not self._http_client:
                self._http_client = httpx.AsyncClient(timeout=30.0)

            response = await self._http_client.get(url)

            if response.status_code != 200:
                logger.warning(f"Failed to fetch article: {response.status_code}")
                return None

            soup = BeautifulSoup(response.text, 'lxml')

            # Remove unwanted elements
            for element in soup.select('script, style, nav, header, footer, aside, .ads, .comments, .social'):
                element.decompose()

            # Try common article content selectors
            content_selectors = [
                'article',
                '[role="main"]',
                '.article-content',
                '.post-content',
                '.entry-content',
                '.content-body',
                '.article-body',
                'main',
            ]

            content = None
            for selector in content_selectors:
                element = soup.select_one(selector)
                if element:
                    content = element.get_text(separator='\n', strip=True)
                    if len(content) > 200:  # Minimum content threshold
                        break

            # Fallback to body if no article container found
            if not content or len(content) < 200:
                body = soup.body
                if body:
                    content = body.get_text(separator='\n', strip=True)

            if content:
                # Truncate very long content
                if len(content) > 5000:
                    content = content[:5000] + "..."
                return content

        except Exception as e:
            logger.warning(f"Error fetching article {url}: {e}")

        return None

    async def _search_enrichment(self, item: NewsItem) -> Optional[EnrichmentResult]:
        """
        Use OpenAI with web search to find missing information.

        Uses OpenAI's responses API with web_search tool for accurate,
        up-to-date information about companies and people.
        """
        if not self.search_api_key:
            logger.warning("No search API key configured for enrichment")
            return None

        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.search_api_key)

            # Build context about what's missing
            missing = []
            if item.news_type_confidence != "high":
                missing.append(f"- NewsType: {item.news_type} (confidence: {item.news_type_confidence})")
            if item.region_confidence != "high":
                missing.append(f"- Region: {item.region} (confidence: {item.region_confidence})")
            if item.related_people_confidence != "high" or not item.related_people:
                people_str = ", ".join(item.related_people) if item.related_people else "none"
                missing.append(f"- Related People: {people_str} (confidence: {item.related_people_confidence})")

            current_data = {
                "title": item.title,
                "company_name": item.company_name or "Unknown",
                "news_type": item.news_type,
                "region": item.region,
                "funding_amount": item.funding_amount,
                "summary": item.summary,
            }

            prompt = SEARCH_ENRICHMENT_PROMPT.format(
                current_data=json.dumps(current_data, ensure_ascii=False, indent=2),
                missing_fields="\n".join(missing) if missing else "All fields need verification",
                company_name=item.company_name or item.title[:50],
            )

            # Use OpenAI Responses API with web search
            # Note: This uses the responses.create() method with web_search tool
            response = client.responses.create(
                model=self.search_model,
                tools=[{"type": "web_search"}],
                input=prompt,
            )

            # Extract the text response
            response_text = ""
            for block in response.output:
                if hasattr(block, 'content'):
                    for content in block.content:
                        if hasattr(content, 'text'):
                            response_text += content.text

            # Parse JSON from response
            result = self._parse_enrichment_response(response_text)
            if result:
                result.enrichment_source = "search"
                return result

        except ImportError:
            logger.warning("OpenAI package not installed, skipping search enrichment")
        except Exception as e:
            logger.warning(f"Search enrichment failed: {e}")

        return None

    def _parse_enrichment_response(self, response_text: str) -> Optional[EnrichmentResult]:
        """Parse enrichment response JSON."""
        try:
            # Extract JSON from response
            text = response_text.strip()

            # Remove markdown code blocks
            if '```json' in text:
                start = text.index('```json') + 7
                end = text.index('```', start)
                text = text[start:end]
            elif '```' in text:
                start = text.index('```') + 3
                end = text.index('```', start)
                text = text[start:end]

            # Find JSON object
            json_match = text[text.find('{'):text.rfind('}') + 1]
            if not json_match:
                return None

            data = json.loads(json_match)

            # Parse related people - support both formats
            related_people = []
            raw_people = data.get('relatedPeople', [])
            if isinstance(raw_people, list):
                for person in raw_people:
                    if isinstance(person, dict):
                        name = person.get('name', '')
                        role = person.get('role', '')
                        if name:
                            related_people.append(f"{name} ({role})" if role else name)
                    elif isinstance(person, str):
                        related_people.append(person)

            return EnrichmentResult(
                news_type=data.get('newsType'),
                news_type_confidence=data.get('newsTypeConfidence'),
                region=data.get('region'),
                region_confidence=data.get('regionConfidence'),
                related_people=related_people if related_people else None,
                related_people_confidence=data.get('relatedPeopleConfidence'),
                company_name=data.get('companyName'),
                funding_amount=normalize_funding_amount(data.get('fundingAmount')),
                summary=data.get('summary'),
                enrichment_source="search",
            )

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse enrichment response: {e}")
            return None

    async def enrich_item(self, item: NewsItem) -> NewsItem:
        """Enrich a single item using multi-phase approach."""
        if not self.should_enrich(item):
            return item

        logger.info(f"Enriching: {item.title[:50]}... (attempt {item.enrichment_attempts + 1})")

        item.enrichment_attempts += 1

        # Phase 1: Fetch full article content
        if item.enrichment_source == "none":
            logger.debug("Phase 1: Fetching full article content")
            full_content = await self._fetch_full_article(item.source_url)

            if full_content and len(full_content) > len(item.raw_content or ""):
                # Re-classify with additional content
                item = self.classifier.reclassify_with_context(item, full_content)
                item.enrichment_source = "article"
                logger.debug(f"Fetched and reclassified with article: {len(full_content)} chars")

        # Phase 2: If still needs enrichment, use search
        if self.should_enrich(item) and self.search_api_key:
            logger.debug("Phase 2: Using web search for enrichment")
            result = await self._search_enrichment(item)

            if result:
                # Apply enrichment results (only if better than existing)
                if result.news_type and result.news_type_confidence == "high":
                    item.news_type = result.news_type
                    item.news_type_confidence = result.news_type_confidence

                if result.region and result.region_confidence == "high":
                    item.region = result.region
                    item.region_confidence = result.region_confidence

                if result.related_people and (
                    not item.related_people or
                    result.related_people_confidence == "high"
                ):
                    item.related_people = result.related_people
                    item.related_people_confidence = result.related_people_confidence

                if result.company_name and not item.company_name:
                    item.company_name = result.company_name

                if result.funding_amount and not item.funding_amount:
                    item.funding_amount = result.funding_amount

                if result.summary and len(result.summary) > len(item.summary or ""):
                    item.summary = result.summary

                item.enrichment_source = "search"
                logger.info(f"Enriched via search: {item.title[:30]}...")

        return item

    async def enrich_batch(self, items: list[NewsItem]) -> list[NewsItem]:
        """Enrich a batch of items that need enrichment."""
        items_to_enrich = [item for item in items if self.should_enrich(item)]

        if not items_to_enrich:
            logger.info("No items need enrichment")
            return items

        logger.info(f"Enriching {len(items_to_enrich)} items")

        async with self:
            enriched = []
            for item in items_to_enrich:
                enriched_item = await self.enrich_item(item)
                enriched.append(enriched_item)
                # Delay between enrichments
                await asyncio.sleep(self.min_delay)

        # Merge enriched items back
        enriched_map = {item.url_hash: item for item in enriched}
        result = []
        for item in items:
            if item.url_hash in enriched_map:
                result.append(enriched_map[item.url_hash])
            else:
                result.append(item)

        return result
