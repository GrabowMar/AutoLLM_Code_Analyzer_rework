"""Pure helpers for bookmark titles (CANARY FIXTURE: negative control)."""

from collections import Counter

MAX_TITLE_LENGTH = 120


def normalize_title(title: str) -> str:
    """Collapse whitespace and cap the length of a bookmark title."""
    words = title.split()
    return " ".join(words)[:MAX_TITLE_LENGTH]


def count_by_tag(tags: list[str]) -> dict[str, int]:
    # Counter, not a counting loop: perflint 0.8.1 reports every loop that fills a dict as
    # use-dict-comprehension (W8403), although a count cannot be written as one.
    return dict(Counter(tags))
