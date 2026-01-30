"""LLM-based classification of news items."""

import json
import logging
from typing import Optional

from anthropic import Anthropic

from ..storage.models import NewsItem

logger = logging.getLogger(__name__)


# Classification prompt template
CLASSIFICATION_PROMPT = """Je bent een expert in Belgische startup- en venture capital-nieuws. Analyseer het volgende nieuwsartikel en classificeer het.

Titel: {title}
Inhoud: {content}

Classificeer dit artikel volgens de volgende categorieën:

NEWS_TYPE (kies één):
- Funding: Startup/scale-up haalt kapitaal op (seed, Series A/B/C, VC/PE investering)
- Acquisition: Bedrijf wordt overgenomen door een ander bedrijf
- Merger: Twee bedrijven fuseren tot één entiteit
- Exit: Oprichters/investeerders verkopen aandelen, IPO, of secundaire verkoop
- Startup: Nieuw bedrijf gelanceerd, geen financiering genoemd
- Scale-up: Nieuws over groeiend bedrijf, geen specifieke financiering/exit
- Fund: Nieuws over investeringsfonds zelf (nieuw fonds gelanceerd, fund close, nieuwe partners)
- Other: Alles wat niet in bovenstaande categorieën past

REGION (kies één):
- Belgium: Belgisch bedrijf of investeerder
- Netherlands: Nederlands bedrijf of investeerder
- UK: Brits bedrijf of investeerder
- EU: Ander Europees land
- US: Amerikaans bedrijf of investeerder
- Other: Rest van de wereld of onduidelijk

Geef je antwoord in het volgende JSON-formaat:
{{
    "news_type": "Funding|Acquisition|Merger|Exit|Startup|Scale-up|Fund|Other",
    "news_type_confidence": "high|medium|low",
    "region": "Belgium|Netherlands|UK|EU|US|Other",
    "region_confidence": "high|medium|low",
    "company_name": "naam van het hoofdbedrijf of null",
    "related_people": ["lijst van namen van relevante personen"],
    "related_people_confidence": "high|medium|low",
    "funding_amount": "bedrag indien vermeld (bv. '€5M Series A') of null",
    "summary": "1-2 zinnen samenvatting in het Nederlands"
}}

Belangrijke richtlijnen:
- Wees conservatief met confidence scores: gebruik "high" alleen als de informatie expliciet in de tekst staat
- Gebruik "low" als je moet raden of als de informatie onduidelijk is
- Voor related_people: neem alleen personen op die direct betrokken zijn (oprichters, CEO's, investeerders), geen bedrijfsnamen
- De samenvatting moet in het Nederlands zijn en de kernfeiten bevatten
- Als het artikel niet over startups, VC, of corporate finance gaat, gebruik news_type="Other"
"""


class Classifier:
    """LLM-based news classifier using Claude."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-3-5-haiku-20241022",
        max_tokens: int = 1024,
    ):
        self.client = Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def _parse_response(self, response_text: str) -> dict:
        """Parse LLM response JSON."""
        # Try to find JSON in the response
        try:
            # Look for JSON block
            if '```json' in response_text:
                start = response_text.index('```json') + 7
                end = response_text.index('```', start)
                response_text = response_text[start:end]
            elif '```' in response_text:
                start = response_text.index('```') + 3
                end = response_text.index('```', start)
                response_text = response_text[start:end]

            # Clean and parse
            response_text = response_text.strip()
            return json.loads(response_text)

        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse LLM response: {e}")
            logger.debug(f"Response was: {response_text[:500]}")
            return {}

    def _get_default_classification(self) -> dict:
        """Return default classification for failures."""
        return {
            "news_type": "Other",
            "news_type_confidence": "low",
            "region": "Other",
            "region_confidence": "low",
            "company_name": None,
            "related_people": [],
            "related_people_confidence": "low",
            "funding_amount": None,
            "summary": "",
        }

    def classify(self, item: NewsItem) -> NewsItem:
        """Classify a single news item."""
        # Build prompt
        content = item.raw_content or item.title
        if len(content) > 3000:
            content = content[:3000] + "..."

        prompt = CLASSIFICATION_PROMPT.format(
            title=item.title,
            content=content,
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[
                    {"role": "user", "content": prompt}
                ],
            )

            response_text = response.content[0].text
            classification = self._parse_response(response_text)

            if not classification:
                classification = self._get_default_classification()
                logger.warning(f"Using default classification for: {item.title[:50]}...")

            # Update item with classification
            item.news_type = classification.get("news_type", "Other")
            item.news_type_confidence = classification.get("news_type_confidence", "low")
            item.region = classification.get("region", "Other")
            item.region_confidence = classification.get("region_confidence", "low")
            item.company_name = classification.get("company_name")
            item.related_people = classification.get("related_people", [])
            item.related_people_confidence = classification.get("related_people_confidence", "low")
            item.funding_amount = classification.get("funding_amount")
            item.summary = classification.get("summary", "")

            logger.debug(
                f"Classified '{item.title[:30]}...' as {item.news_type} "
                f"({item.news_type_confidence}), region: {item.region}"
            )

        except Exception as e:
            logger.error(f"Classification failed for {item.url_hash}: {e}")
            # Apply defaults
            defaults = self._get_default_classification()
            item.news_type = defaults["news_type"]
            item.news_type_confidence = defaults["news_type_confidence"]
            item.region = defaults["region"]
            item.region_confidence = defaults["region_confidence"]
            item.summary = f"Classificatie mislukt: {item.title}"

        return item

    def classify_batch(self, items: list[NewsItem], batch_size: int = 10) -> list[NewsItem]:
        """Classify a batch of news items."""
        classified = []

        for i, item in enumerate(items):
            logger.info(f"Classifying item {i + 1}/{len(items)}: {item.title[:50]}...")
            classified_item = self.classify(item)
            classified.append(classified_item)

        logger.info(f"Classified {len(classified)} items")
        return classified


class ClassifierWithFallback(Classifier):
    """Classifier with re-classification support for enrichment."""

    def reclassify_with_context(self, item: NewsItem, additional_content: str) -> NewsItem:
        """Re-classify item with additional context from enrichment."""
        # Combine original and new content
        combined_content = f"{item.raw_content}\n\nAanvullende informatie:\n{additional_content}"

        # Create a temporary item with combined content
        item.raw_content = combined_content

        # Re-run classification
        return self.classify(item)
