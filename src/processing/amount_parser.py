"""Utility for parsing and normalizing funding amounts."""

import re
from typing import Optional


# Number word mappings (Dutch and English)
NUMBER_WORDS = {
    # Dutch
    'een': 1, 'twee': 2, 'drie': 3, 'vier': 4, 'vijf': 5,
    'zes': 6, 'zeven': 7, 'acht': 8, 'negen': 9, 'tien': 10,
    'elf': 11, 'twaalf': 12, 'dertien': 13, 'veertien': 14, 'vijftien': 15,
    'twintig': 20, 'dertig': 30, 'veertig': 40, 'vijftig': 50,
    'zestig': 60, 'zeventig': 70, 'tachtig': 80, 'negentig': 90,
    'honderd': 100,
    # English
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
    'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15,
    'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
    'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90,
    'hundred': 100,
}

# Magnitude words (Dutch and English)
MAGNITUDES = {
    # Dutch
    'miljard': 1_000_000_000,
    'miljarden': 1_000_000_000,
    'miljoen': 1_000_000,
    'mln': 1_000_000,
    'duizend': 1_000,
    'k': 1_000,
    # English
    'billion': 1_000_000_000,
    'bn': 1_000_000_000,
    'b': 1_000_000_000,
    'million': 1_000_000,
    'm': 1_000_000,
    'thousand': 1_000,
}

# Currency symbols and words
CURRENCY_MAP = {
    '€': '€',
    'euro': '€',
    "euro's": '€',
    'eur': '€',
    '$': '$',
    'dollar': '$',
    'dollars': '$',
    'usd': '$',
    '£': '£',
    'pound': '£',
    'pounds': '£',
    'gbp': '£',
}


def normalize_funding_amount(amount_str: Optional[str]) -> Optional[str]:
    """
    Normalize funding amounts to a standardized format.

    Examples:
        "tot 300 miljoen euro" -> "€300M"
        "5 million dollars" -> "$5M"
        "€2.5M" -> "€2.5M"
        "15 miljard euro" -> "€15B"
        "Series A: €5M" -> "€5M"
        "50.000 euro" -> "€50K"

    Returns:
        Normalized amount string (e.g., "€300M") or None if parsing fails
    """
    if not amount_str:
        return None

    # Clean the input
    text = amount_str.lower().strip()

    # Remove common prefixes
    prefixes_to_remove = ['tot', 'circa', 'ongeveer', 'bijna', 'meer dan',
                          'up to', 'about', 'around', 'nearly', 'over',
                          'series a:', 'series b:', 'series c:', 'seed:']
    for prefix in prefixes_to_remove:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()

    # Find currency
    currency = '€'  # Default to Euro
    for curr_word, curr_symbol in CURRENCY_MAP.items():
        if curr_word in text:
            currency = curr_symbol
            text = text.replace(curr_word, ' ')
            break

    # Remove currency symbols from text
    text = text.replace('€', ' ').replace('$', ' ').replace('£', ' ')

    # Try to extract the numeric value
    value = None
    magnitude = 1

    # Pattern 1: Number with magnitude word (e.g., "300 miljoen", "5 million")
    for mag_word, mag_value in sorted(MAGNITUDES.items(), key=lambda x: len(x[0]), reverse=True):
        if mag_word in text:
            magnitude = mag_value
            text = text.replace(mag_word, ' ')
            break

    # Pattern 2: Extract numeric value
    # Handle both decimal comma (Dutch: 2,5) and decimal point (English: 2.5)
    # Also handle thousand separators (Dutch: 50.000)

    # First, try to find a decimal number
    decimal_match = re.search(r'(\d+)[,.](\d+)', text)
    if decimal_match:
        whole = decimal_match.group(1)
        decimal = decimal_match.group(2)

        # Check if it's a thousand separator (e.g., 50.000) or decimal
        if len(decimal) == 3 and magnitude == 1:
            # It's a thousand separator like 50.000
            value = float(whole + decimal)
        else:
            # It's a decimal like 2.5 or 2,5
            value = float(f"{whole}.{decimal}")
    else:
        # Try to find a whole number
        number_match = re.search(r'(\d+)', text)
        if number_match:
            value = float(number_match.group(1))

    # If no numeric value found, try word numbers
    if value is None:
        for word, num in NUMBER_WORDS.items():
            if word in text:
                value = num
                break

    if value is None:
        return amount_str  # Return original if parsing fails

    # Apply magnitude
    total_value = value * magnitude

    # Format the output
    return format_amount(total_value, currency)


def format_amount(value: float, currency: str = '€') -> str:
    """
    Format a numeric value to a readable amount string.

    Examples:
        1_000_000_000 -> "€1B"
        300_000_000 -> "€300M"
        5_500_000 -> "€5.5M"
        50_000 -> "€50K"
        5_000 -> "€5K"
    """
    if value >= 1_000_000_000:
        # Billions
        formatted = value / 1_000_000_000
        if formatted == int(formatted):
            return f"{currency}{int(formatted)}B"
        return f"{currency}{formatted:.1f}B".rstrip('0').rstrip('.')

    elif value >= 1_000_000:
        # Millions
        formatted = value / 1_000_000
        if formatted == int(formatted):
            return f"{currency}{int(formatted)}M"
        return f"{currency}{formatted:.1f}M".rstrip('0').rstrip('.')

    elif value >= 1_000:
        # Thousands
        formatted = value / 1_000
        if formatted == int(formatted):
            return f"{currency}{int(formatted)}K"
        return f"{currency}{formatted:.1f}K".rstrip('0').rstrip('.')

    else:
        # Small amounts
        if value == int(value):
            return f"{currency}{int(value)}"
        return f"{currency}{value:.2f}"


def parse_amount_to_number(amount_str: Optional[str]) -> Optional[float]:
    """
    Parse an amount string to a numeric value.
    Useful for sorting or comparing amounts.

    Returns:
        Float value or None if parsing fails
    """
    if not amount_str:
        return None

    # If already normalized (e.g., "€300M"), parse that format
    normalized = amount_str.strip().upper()

    # Try normalized format first
    match = re.match(r'^[€$£]?([\d.]+)([KMB])?$', normalized)
    if match:
        value = float(match.group(1))
        suffix = match.group(2)
        if suffix == 'K':
            return value * 1_000
        elif suffix == 'M':
            return value * 1_000_000
        elif suffix == 'B':
            return value * 1_000_000_000
        return value

    # Otherwise, try to normalize and parse
    normalized_amount = normalize_funding_amount(amount_str)
    if normalized_amount and normalized_amount != amount_str:
        return parse_amount_to_number(normalized_amount)

    return None
