"""Pure helpers for bookmark titles (CANARY FIXTURE: negative control)."""

MAX_TITLE_LENGTH = 120


def normalize_title(title: str) -> str:
    """Collapse whitespace and cap the length of a bookmark title."""
    words = title.split()
    return " ".join(words)[:MAX_TITLE_LENGTH]


def count_by_tag(tags: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tag in tags:
        counts[tag] = counts.get(tag, 0) + 1
    return counts
