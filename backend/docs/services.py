"""Documentation service — walks DOCS_ROOT and returns raw Markdown.

Rendering happens on the client (marked + Shiki + Mermaid), so the backend
just exposes the file tree, raw source, and a simple keyword search.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from django.conf import settings

DOCS_ROOT: Path = Path(getattr(settings, "DOCS_ROOT", settings.BASE_DIR / "docs"))

# Static map: slug (or slug prefix) -> category. Most specific match wins.
# Categories are surfaced on the landing page and used to group the sidebar.
CATEGORY_MAP: dict[str, str] = {
    "QUICKSTART": "Getting Started",
    "README": "Getting Started",
    "development-guide": "Getting Started",
    "ARCHITECTURE": "Architecture",
    "ANALYSIS_PIPELINE": "Architecture",
    "BACKGROUND_SERVICES": "Architecture",
    "GENERATION_PROCESS": "Architecture",
    "EXPERIMENTS": "Architecture",
    "architecture": "Architecture",
    "api-reference": "Reference",
    "api": "Reference",
    "ANALYZER_GUIDE": "Reference",
    "MODELS_REFERENCE": "Reference",
    "AUTOMATION_GUIDE": "Reference",
    "deployment-guide": "Operations",
    "TROUBLESHOOTING": "Operations",
}
DEFAULT_CATEGORY = "Other"

# Ordered list controls the order categories render in the UI.
CATEGORY_ORDER: list[str] = [
    "Getting Started",
    "Architecture",
    "Reference",
    "Operations",
    "Other",
]


def _slug_from_path(path: Path) -> str:
    rel = path.relative_to(DOCS_ROOT)
    return str(rel.with_suffix("")).replace(os.sep, "/")


_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_FIRST_PARA_RE = re.compile(r"^(?!#|>|\s*[-*]|\s*\d+\.|\s*```)(.+?)$", re.MULTILINE)


def _title_from_text(text: str, fallback: str) -> str:
    match = _H1_RE.search(text)
    if match:
        return match.group(1).strip()
    return fallback.replace("-", " ").replace("_", " ").title()


def _description_from_text(text: str) -> str:
    """First non-heading, non-list paragraph, truncated. Used on landing cards."""
    # Skip the H1 line if present.
    body = _H1_RE.sub("", text, count=1).lstrip()
    match = _FIRST_PARA_RE.search(body)
    if not match:
        return ""
    line = match.group(1).strip()
    # Strip markdown emphasis / links for a cleaner blurb.
    line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
    line = re.sub(r"[*_`]+", "", line)
    return line[:160].strip()


def _category_for_slug(slug: str) -> str:
    """Most-specific prefix match in CATEGORY_MAP."""
    # Try whole slug, then progressively shorter prefixes split on '/'.
    candidates = [slug]
    parts = slug.split("/")
    if len(parts) > 1:
        candidates.append(parts[0])
    leaf = parts[-1]
    candidates.append(leaf)
    for key in candidates:
        if key in CATEGORY_MAP:
            return CATEGORY_MAP[key]
    return DEFAULT_CATEGORY


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _build_tree(root: Path) -> list[dict]:
    items: list[dict] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_dir(), p.name.lower()))
    except OSError:
        return items
    for entry in entries:
        if entry.name.startswith(".") or entry.name.startswith("_"):
            continue
        if entry.is_dir():
            children = _build_tree(entry)
            if children:
                items.append(
                    {
                        "slug": _slug_from_path(entry),
                        "title": entry.name.replace("-", " ").replace("_", " ").title(),
                        "category": _category_for_slug(_slug_from_path(entry)),
                        "description": "",
                        "children": children,
                    },
                )
        elif entry.suffix.lower() == ".md":
            text = _read(entry)
            slug = _slug_from_path(entry)
            items.append(
                {
                    "slug": slug,
                    "title": _title_from_text(text, entry.stem),
                    "category": _category_for_slug(slug),
                    "description": _description_from_text(text),
                    "children": [],
                },
            )
    return items


def list_docs() -> list[dict]:
    """Walk DOCS_ROOT and return a nested tree of Markdown files."""
    return _build_tree(DOCS_ROOT)


def sanitize_slug(slug: str) -> str | None:
    """Return None if slug is unsafe (path traversal / absolute)."""
    if not slug:
        return None
    if slug.startswith("/"):
        return None
    parts = slug.replace("\\", "/").split("/")
    for part in parts:
        if part in ("", ".", ".."):
            return None
    return slug


def get_doc(slug: str) -> dict | None:
    """Return the raw markdown for a single doc, or None if not found."""
    clean = sanitize_slug(slug)
    if clean is None:
        return None
    path = DOCS_ROOT / Path(*clean.split("/"))
    path = path.with_suffix(".md")
    if not path.exists() or not path.is_file():
        return None
    try:
        path.resolve().relative_to(DOCS_ROOT.resolve())
    except ValueError:
        return None

    raw = path.read_text(encoding="utf-8")
    return {
        "slug": clean,
        "title": _title_from_text(raw, path.stem),
        "category": _category_for_slug(clean),
        "raw": raw,
        "last_modified": path.stat().st_mtime,
    }


def _iter_md_files() -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(DOCS_ROOT):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and not d.startswith("_")]
        files.extend(Path(dirpath) / fn for fn in filenames if fn.lower().endswith(".md") and not fn.startswith("."))
    return files


def _nearest_heading_above(lines: list[str], idx: int) -> str:
    for i in range(idx, -1, -1):
        line = lines[i].lstrip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def search_docs(query: str) -> list[dict]:
    """Naive multi-term full-text search across all Markdown files.

    Returns the best matching line as a snippet plus the nearest heading
    above it (so the user gets a sense of where in the doc the match lives).
    """
    if not query or not query.strip():
        return []
    terms = [t.lower() for t in query.strip().split() if t]
    if not terms:
        return []

    results: list[dict] = []
    for path in _iter_md_files():
        content = _read(path)
        if not content:
            continue
        lower = content.lower()
        score = sum(lower.count(t) for t in terms)
        if score == 0:
            continue
        # Find best line: line with most term hits.
        lines = content.splitlines()
        best_idx = 0
        best_hits = -1
        for i, line in enumerate(lines):
            ll = line.lower()
            hits = sum(ll.count(t) for t in terms)
            if hits > best_hits:
                best_hits = hits
                best_idx = i
        snippet = lines[best_idx].strip()
        if len(snippet) > 180:
            snippet = snippet[:180].rstrip() + "…"
        heading = _nearest_heading_above(lines, best_idx)
        slug = _slug_from_path(path)
        results.append(
            {
                "slug": slug,
                "title": _title_from_text(content, path.stem),
                "category": _category_for_slug(slug),
                "section": heading,
                "snippet": snippet,
                "score": score,
            },
        )
    results.sort(key=lambda r: r["score"], reverse=True)
    return results
