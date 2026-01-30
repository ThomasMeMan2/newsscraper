"""Email digest generation and sending."""

import asyncio
import logging
from datetime import date
from typing import Optional
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import aiosmtplib

from ..storage.models import NewsItem

logger = logging.getLogger(__name__)


class EmailDigest:
    """Generates and sends email digests of news items."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_username: str,
        smtp_password: str,
        from_address: str,
        recipients: list[str],
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.from_address = from_address
        self.recipients = recipients

    def _group_by_type(self, items: list[NewsItem]) -> dict[str, list[NewsItem]]:
        """Group items by news type."""
        groups: dict[str, list[NewsItem]] = {}
        for item in items:
            news_type = item.news_type
            if news_type not in groups:
                groups[news_type] = []
            groups[news_type].append(item)

        # Sort items within each group by pub_date (newest first)
        for news_type in groups:
            groups[news_type].sort(
                key=lambda x: x.pub_date or date.min,
                reverse=True
            )

        return groups

    def _format_item_text(self, item: NewsItem) -> str:
        """Format a single item for text email."""
        lines = []
        lines.append(f"  • {item.title}")

        if item.company_name:
            lines.append(f"    Bedrijf: {item.company_name}")

        if item.funding_amount:
            lines.append(f"    Bedrag: {item.funding_amount}")

        if item.region and item.region != "Other":
            lines.append(f"    Regio: {item.region}")

        if item.summary:
            lines.append(f"    {item.summary}")

        lines.append(f"    Link: {item.source_url}")
        lines.append(f"    Bron: {item.source_name}")

        return "\n".join(lines)

    def _format_item_html(self, item: NewsItem) -> str:
        """Format a single item for HTML email."""
        html = []
        html.append('<div style="margin-bottom: 20px; padding: 15px; background-color: #f9f9f9; border-radius: 5px;">')

        # Title with link
        html.append(f'<h3 style="margin: 0 0 10px 0;"><a href="{item.source_url}" style="color: #1a73e8; text-decoration: none;">{item.title}</a></h3>')

        # Metadata
        meta_items = []
        if item.company_name:
            meta_items.append(f"<strong>Bedrijf:</strong> {item.company_name}")
        if item.funding_amount:
            meta_items.append(f"<strong>Bedrag:</strong> {item.funding_amount}")
        if item.region and item.region != "Other":
            meta_items.append(f"<strong>Regio:</strong> {item.region}")

        if meta_items:
            html.append(f'<p style="margin: 5px 0; color: #666; font-size: 14px;">{" | ".join(meta_items)}</p>')

        # Summary
        if item.summary:
            html.append(f'<p style="margin: 10px 0;">{item.summary}</p>')

        # Source
        html.append(f'<p style="margin: 5px 0; color: #999; font-size: 12px;">Bron: {item.source_name} | {item.pub_date if item.pub_date else "Geen datum"}</p>')

        html.append('</div>')
        return "\n".join(html)

    def _get_type_emoji(self, news_type: str) -> str:
        """Get emoji for news type."""
        emojis = {
            "Funding": "💰",
            "Acquisition": "🤝",
            "Merger": "🔄",
            "Exit": "🚀",
            "Startup": "🌱",
            "Scale-up": "📈",
            "Fund": "🏦",
            "Other": "📰",
        }
        return emojis.get(news_type, "📰")

    def _get_type_label(self, news_type: str) -> str:
        """Get Dutch label for news type."""
        labels = {
            "Funding": "Financiering",
            "Acquisition": "Overname",
            "Merger": "Fusie",
            "Exit": "Exit / IPO",
            "Startup": "Nieuwe Startups",
            "Scale-up": "Scale-ups",
            "Fund": "Fondsen",
            "Other": "Overig",
        }
        return labels.get(news_type, news_type)

    def generate_text_body(self, items: list[NewsItem], digest_date: date) -> str:
        """Generate plain text email body."""
        groups = self._group_by_type(items)

        lines = []
        lines.append(f"STARTUP NIEUWS DIGEST - {digest_date.strftime('%d %B %Y')}")
        lines.append(f"Totaal: {len(items)} nieuwe items")
        lines.append("=" * 60)
        lines.append("")

        # Order of sections
        type_order = ["Funding", "Acquisition", "Merger", "Exit", "Startup", "Scale-up", "Fund", "Other"]

        for news_type in type_order:
            if news_type in groups and groups[news_type]:
                label = self._get_type_label(news_type)
                emoji = self._get_type_emoji(news_type)
                lines.append(f"{emoji} {label.upper()} ({len(groups[news_type])})")
                lines.append("-" * 40)

                for item in groups[news_type]:
                    lines.append(self._format_item_text(item))
                    lines.append("")

                lines.append("")

        lines.append("=" * 60)
        lines.append("Dit is een automatisch gegenereerde email.")
        lines.append("Beheerd door Belgian Startup News Aggregator")

        return "\n".join(lines)

    def generate_html_body(self, items: list[NewsItem], digest_date: date) -> str:
        """Generate HTML email body."""
        groups = self._group_by_type(items)

        html = []
        html.append("""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #333; max-width: 800px; margin: 0 auto; padding: 20px; }
        h1 { color: #1a73e8; border-bottom: 2px solid #1a73e8; padding-bottom: 10px; }
        h2 { color: #333; margin-top: 30px; }
        .summary { background-color: #e8f0fe; padding: 15px; border-radius: 5px; margin-bottom: 20px; }
        .footer { margin-top: 40px; padding-top: 20px; border-top: 1px solid #ddd; color: #666; font-size: 12px; }
    </style>
</head>
<body>
""")

        # Header
        html.append(f'<h1>🇧🇪 Startup Nieuws Digest</h1>')
        html.append(f'<div class="summary">')
        html.append(f'<strong>Datum:</strong> {digest_date.strftime("%d %B %Y")}<br>')
        html.append(f'<strong>Totaal:</strong> {len(items)} nieuwe items')
        html.append('</div>')

        # Sections by type
        type_order = ["Funding", "Acquisition", "Merger", "Exit", "Startup", "Scale-up", "Fund", "Other"]

        for news_type in type_order:
            if news_type in groups and groups[news_type]:
                label = self._get_type_label(news_type)
                emoji = self._get_type_emoji(news_type)
                count = len(groups[news_type])

                html.append(f'<h2>{emoji} {label} ({count})</h2>')

                for item in groups[news_type]:
                    html.append(self._format_item_html(item))

        # Footer
        html.append("""
<div class="footer">
    <p>Dit is een automatisch gegenereerde email door de Belgian Startup News Aggregator.</p>
</div>
</body>
</html>
""")

        return "\n".join(html)

    async def send(
        self,
        items: list[NewsItem],
        digest_date: Optional[date] = None,
        dry_run: bool = False,
    ) -> bool:
        """
        Send digest email.

        Args:
            items: News items to include
            digest_date: Date for the digest (defaults to today)
            dry_run: If True, log email content but don't send

        Returns:
            True if sent successfully
        """
        if not items:
            logger.info("No items to send in digest")
            return True

        if not self.recipients:
            logger.warning("No recipients configured for email digest")
            return False

        digest_date = digest_date or date.today()

        # Generate content
        text_body = self.generate_text_body(items, digest_date)
        html_body = self.generate_html_body(items, digest_date)

        # Create message
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Startup News Digest - {digest_date.strftime('%Y-%m-%d')} - {len(items)} new items"
        msg["From"] = self.from_address
        msg["To"] = ", ".join(self.recipients)

        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        if dry_run:
            logger.info("DRY RUN - Email would be sent:")
            logger.info(f"  To: {self.recipients}")
            logger.info(f"  Subject: {msg['Subject']}")
            logger.info(f"  Items: {len(items)}")
            return True

        try:
            # Send email
            await aiosmtplib.send(
                msg,
                hostname=self.smtp_host,
                port=self.smtp_port,
                username=self.smtp_username,
                password=self.smtp_password,
                start_tls=True,
            )

            logger.info(f"Sent digest email to {len(self.recipients)} recipients with {len(items)} items")
            return True

        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False
