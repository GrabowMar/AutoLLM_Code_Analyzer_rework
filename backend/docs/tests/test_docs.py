"""Tests for the docs app."""

from __future__ import annotations

import pytest

from backend.docs import services


@pytest.fixture
def docs_root(tmp_path, settings):
    """Create a temporary docs tree and point the service at it."""
    (tmp_path / "index.md").write_text("# Welcome\n\nIntro text.", encoding="utf-8")
    (tmp_path / "QUICKSTART.md").write_text(
        "# Quickstart\n\nGet up and running in minutes.",
        encoding="utf-8",
    )
    (tmp_path / "getting-started.md").write_text(
        "# Getting Started\n\nStep one.\nStep two.",
        encoding="utf-8",
    )
    sub = tmp_path / "api"
    sub.mkdir()
    (sub / "overview.md").write_text(
        "# API Overview\n\nREST endpoints overview.",
        encoding="utf-8",
    )
    (sub / "reference.md").write_text(
        "# API Reference\n\n## Endpoints\n\nCall things with `requests`.\n",
        encoding="utf-8",
    )
    settings.DOCS_ROOT = str(tmp_path)
    services.DOCS_ROOT = tmp_path
    return tmp_path


# ---------------------------------------------------------------------------
# list_docs / tree
# ---------------------------------------------------------------------------


def test_list_docs_returns_list(docs_root):
    tree = services.list_docs()
    assert isinstance(tree, list)
    assert len(tree) > 0


def test_list_docs_contains_index(docs_root):
    tree = services.list_docs()
    slugs = [n["slug"] for n in tree]
    assert "index" in slugs


def test_list_docs_nested_directory(docs_root):
    tree = services.list_docs()
    api_node = next((n for n in tree if n["slug"] == "api"), None)
    assert api_node is not None
    child_slugs = [c["slug"] for c in api_node["children"]]
    assert "api/overview" in child_slugs


def test_list_docs_title_from_h1(docs_root):
    tree = services.list_docs()
    node = next(n for n in tree if n["slug"] == "index")
    assert node["title"] == "Welcome"


def test_list_docs_includes_category(docs_root):
    tree = services.list_docs()
    qs = next(n for n in tree if n["slug"] == "QUICKSTART")
    assert qs["category"] == "Getting Started"


def test_list_docs_includes_description(docs_root):
    tree = services.list_docs()
    qs = next(n for n in tree if n["slug"] == "QUICKSTART")
    assert "minutes" in qs["description"].lower()


# ---------------------------------------------------------------------------
# get_doc
# ---------------------------------------------------------------------------


def test_get_doc_returns_raw_markdown(docs_root):
    doc = services.get_doc("index")
    assert doc is not None
    assert doc["raw"].startswith("# Welcome")
    assert doc["title"] == "Welcome"


def test_get_doc_no_html_field(docs_root):
    doc = services.get_doc("index")
    assert doc is not None
    assert "html" not in doc
    assert "toc" not in doc


def test_get_doc_includes_category(docs_root):
    doc = services.get_doc("api/reference")
    assert doc is not None
    assert doc["category"] == "Reference"


def test_get_doc_nested_slug(docs_root):
    doc = services.get_doc("api/overview")
    assert doc is not None
    assert doc["slug"] == "api/overview"
    assert "API Overview" in doc["title"]


def test_get_doc_nonexistent_returns_none(docs_root):
    assert services.get_doc("does-not-exist") is None


def test_get_doc_has_last_modified(docs_root):
    doc = services.get_doc("index")
    assert doc is not None
    assert isinstance(doc["last_modified"], float)


# ---------------------------------------------------------------------------
# Slug sanitization
# ---------------------------------------------------------------------------


def test_sanitize_slug_rejects_traversal():
    assert services.sanitize_slug("../etc/passwd") is None


def test_sanitize_slug_rejects_absolute():
    assert services.sanitize_slug("/etc/passwd") is None


def test_sanitize_slug_rejects_dotdot_component():
    assert services.sanitize_slug("api/../../../etc/passwd") is None


def test_sanitize_slug_accepts_valid():
    assert services.sanitize_slug("api/overview") == "api/overview"


def test_get_doc_rejects_traversal(docs_root, tmp_path):
    secret = tmp_path.parent / "secret.md"
    secret.write_text("# Secret", encoding="utf-8")
    result = services.get_doc("../secret")
    assert result is None


# ---------------------------------------------------------------------------
# search_docs
# ---------------------------------------------------------------------------


def test_search_returns_results(docs_root):
    results = services.search_docs("API")
    assert len(results) > 0
    slugs = [r["slug"] for r in results]
    assert any("api" in s for s in slugs)


def test_search_sorted_by_score(docs_root):
    results = services.search_docs("API")
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_search_empty_query_returns_empty(docs_root):
    assert services.search_docs("") == []


def test_search_no_match_returns_empty(docs_root):
    assert services.search_docs("xyzzy_no_match_12345") == []


def test_search_result_has_snippet_and_category(docs_root):
    results = services.search_docs("REST")
    assert len(results) > 0
    top = results[0]
    assert top["snippet"] != ""
    assert "category" in top
    assert "section" in top


def test_search_result_includes_nearest_heading(docs_root):
    results = services.search_docs("requests")
    assert results
    top = next(r for r in results if r["slug"] == "api/reference")
    assert top["section"] == "Endpoints"


# ---------------------------------------------------------------------------
# Category map
# ---------------------------------------------------------------------------


def test_category_order_includes_all_categories():
    assert "Getting Started" in services.CATEGORY_ORDER
    assert "Architecture" in services.CATEGORY_ORDER
    assert "Reference" in services.CATEGORY_ORDER
    assert "Operations" in services.CATEGORY_ORDER
    assert "Other" in services.CATEGORY_ORDER
