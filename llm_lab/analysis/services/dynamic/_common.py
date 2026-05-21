"""Shared helpers for dynamic analyzers."""

from __future__ import annotations

import re


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
