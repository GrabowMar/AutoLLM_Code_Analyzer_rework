"""Reusable security helpers (URL validation, SSRF guards, etc.)."""

from __future__ import annotations

from urllib.parse import urlparse


def validate_target_url(url: str) -> tuple[bool, str]:
    """Validate a URL for use as an analysis target.

    Returns (is_valid, error_message).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, f"Invalid URL scheme: {parsed.scheme!r}. Only http/https allowed."
    if not parsed.hostname:
        return False, "URL has no hostname."
    hostname = parsed.hostname.lower()
    blocked = (
        "localhost",
        "127.0.0.1",
        "0.0.0.0",  # noqa: S104
        "::1",
        "metadata.google.internal",
        "169.254.169.254",
    )
    if hostname in blocked:
        return False, f"Blocked hostname: {hostname}"
    if hostname.startswith(("10.", "192.168.")):
        return False, f"Internal network address not allowed: {hostname}"
    min_private_172 = 16
    max_private_172 = 31
    min_parts = 2
    if hostname.startswith("172."):
        parts = hostname.split(".")
        if len(parts) >= min_parts and parts[1].isdigit() and min_private_172 <= int(parts[1]) <= max_private_172:
            return False, f"Internal network address not allowed: {hostname}"
    return True, ""
