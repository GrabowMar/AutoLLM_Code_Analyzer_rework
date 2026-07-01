"""Docs API views.

The backend just exposes the file tree, raw markdown, and a simple
keyword search. Rendering (Markdown -> HTML, syntax highlighting,
Mermaid diagrams, callouts) is done on the client.
"""

from __future__ import annotations

from ninja import Router
from ninja import Schema
from ninja.security import SessionAuth

from backend.docs import services

router = Router(tags=["docs"])

_session = SessionAuth()


class DocNodeOut(Schema):
    slug: str
    title: str
    category: str = "Other"
    description: str = ""
    children: list[DocNodeOut] = []


DocNodeOut.model_rebuild()


class DocPageOut(Schema):
    slug: str
    title: str
    category: str
    raw: str
    last_modified: float


class DocSearchResult(Schema):
    slug: str
    title: str
    category: str = "Other"
    section: str = ""
    snippet: str
    score: int


@router.get("/tree", response=list[DocNodeOut], auth=_session)
def get_tree(request):
    return services.list_docs()


@router.get("/categories", response=list[str], auth=_session)
def get_categories(request):
    """Ordered list of category labels — drives sidebar grouping order."""
    return services.CATEGORY_ORDER


@router.get("/search", response=list[DocSearchResult], auth=_session)
def search(request, q: str = ""):
    return services.search_docs(q)


@router.get("/page", response=DocPageOut | None, auth=_session)
def get_page(request, slug: str):
    return services.get_doc(slug)
