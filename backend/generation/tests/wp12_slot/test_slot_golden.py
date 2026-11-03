"""Golden identity values of slot mode (spec section 13.3).

Changing any constant below requires a protocol bump (``slot@2``) and a changelog line in
``docs/STUDIES.md#identity-changelog``. The values are computed from the frozen golden parent
(``testing/golden_library/parent_app/``, tree hash in ``hashes.json``): one target of each kind, the resolved
context of two context policies, and the ``normalized_sha`` of three fixture variants (Python, TSX, Go).
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.generation.contracts.slot import ContextPolicy
from backend.generation.slot import selection
from backend.generation.slot.context import resolve_context
from backend.generation.testing.golden_library import golden_hashes

pytestmark = pytest.mark.django_db

TARGETS: dict[str, tuple[dict[str, Any], str]] = {
    "file": (
        {"kind": "file", "path": "backend/db.py"},
        "72206252b4e16f8ef2d387571ba1b3d75815b4859e645384ebbeadd339803d36",
    ),
    "region": (
        {"kind": "region", "path": "backend/todos.py", "span": {"start_line": 20, "end_line": 23}},
        "1ef24d100feaee05836580640c7260d6c06b65d091d4b8da76de914e5266fd05",
    ),
    "symbol": (
        {"kind": "symbol", "symbol_id": "backend/auth.py::login_required"},
        "952da9bfab7a30b72ebf0649cfd297171e1d1971e84a11541f9aa3183f2cce31",
    ),
    "endpoint": (
        {"kind": "endpoint", "endpoint": {"method": "PATCH", "path": "/api/todos/{id}"}},
        "ced3c50315fda5b24f8195ebb2616c87e2dde6c67257ce5d90db4d97f3afbe6c",
    ),
    "component": (
        {"kind": "component", "component": "TodoList"},
        "6bce3ca0426b6a55ec72226345d7bf2981494d15fcd52c0c2eeeb13f49bfd071",
    ),
}
CONTEXT_SHAS: dict[str, str] = {
    "related_files": "678370e06dda0b2aab51c03cded68b97c2041733908898cd8897f0884b6f09ec",
    "slot_file_only": "09f28f241d5cb42cd18507893fa021ed00753596be425f62b1ab22a92b9eb5a1",
}
VARIANTS: dict[str, tuple[str, str, str]] = {
    "python": (
        "python",
        'def update(todo_id):\n    row = get(todo_id)  # fetch\n    return {"id": row}\n',
        "f400f9cd9bed7c8c795b350ce54601211c3d829c7732b56439008d1c17a4004e",
    ),
    "tsx": (
        "tsx",
        "export function Item({ title }: { title: string }) {\n  return <li>{title}</li>;\n}\n",
        "4e91a5779173a307528bac137046f4081a44d481ac0fd4af0a93bfb22d896172",
    ),
    "go": (
        "go",
        'func Get(id int) string {\n\tname := lookup(id)\n\treturn "x" + name\n}\n',
        "a68b22c99d99146cb307023b707e38444ccc3e0f98bcc9f02558fc6473c02ce5",
    ),
}


def test_parent_tree_is_the_frozen_fixture(slot_set: Any) -> None:
    assert slot_set.frozen_tree_hash == golden_hashes()["parent_app"]


@pytest.mark.parametrize("kind", sorted(TARGETS))
def test_target_hash_per_kind(slot_set: Any, kind: str) -> None:
    selection_in, expected = TARGETS[kind]
    assert selection.preview(slot_set, selection_in)["target_hash"] == expected


@pytest.mark.parametrize("mode", sorted(CONTEXT_SHAS))
def test_resolved_context_sha(slot_set: Any, researcher: Any, mode: str) -> None:
    target = selection.create_target(slot_set, TARGETS["endpoint"][0], user=researcher, validate=False)
    assert resolve_context(target, ContextPolicy(mode=mode)).sha == CONTEXT_SHAS[mode]


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_normalized_sha_of_fixture_variants(name: str) -> None:
    from backend.generation.patterns import normalize

    language, code, expected = VARIANTS[name]
    try:
        result = normalize.normalized_sha(code, language)
    except NotImplementedError:
        pytest.skip("the WP-13 normalizer is not on the branch yet")
    sha = getattr(result, "sha", result)
    assert sha == expected
