"""
URL validation using can_ada.

Validates that all URLs are valid and from the same website.
"""

import logging
from urllib.parse import urlparse

from can_ada import parse as parse_url

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """URL validation error."""
    pass


def validate_url(url: str) -> str:
    """
    Validate a single URL.

    Args:
        url: URL to validate

    Returns:
        Normalized URL

    Raises:
        ValidationError: If URL is invalid
    """
    try:
        parsed = parse_url(url)
        if not parsed:
            raise ValidationError(f"Invalid URL: {url}")
        return str(parsed)
    except Exception as e:
        raise ValidationError(f"Invalid URL '{url}': {e}")


def get_host(url: str) -> str:
    """Extract host from URL."""
    parsed = urlparse(url)
    return parsed.netloc


def validate_urls(urls: list[str]) -> list[str]:
    """
    Validate a list of URLs.

    All URLs must be valid and from the same website.

    Args:
        urls: List of URLs to validate

    Returns:
        List of normalized URLs

    Raises:
        ValidationError: If any URL is invalid or URLs are from different websites
    """
    if not urls:
        raise ValidationError("No URLs provided")

    normalized: list[str] = []
    hosts: set[str] = set()

    for url in urls:
        norm_url = validate_url(url)
        normalized.append(norm_url)
        hosts.add(get_host(norm_url))

    if len(hosts) > 1:
        raise ValidationError(
            f"All URLs must be from the same website. Found: {', '.join(sorted(hosts))}"
        )

    logger.info(f"Validated {len(normalized)} URLs from {hosts.pop()}")
    return normalized
