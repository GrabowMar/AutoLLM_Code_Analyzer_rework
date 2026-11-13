"""Frontend contract fixtures (WP-14): the JSON the Library UI is tested against, produced by the real API.

``frontend/src/lib/api/v3/__fixtures__/*.json`` are responses of the v3 endpoints on the golden library
(volatile fields normalized). This test re-renders every fixture and fails when one drifts (harness facts
and the config vocabulary, which other WPs own, are compared by schema only), so a backend schema change
that would silently break the frontend types and its vitest suite shows up here. After an intended
change, regenerate them::

    ./.wt-test.sh <suffix> -- env WP14_WRITE_FIXTURES=1 pytest -q backend/generation/tests/wp14_frontend

and re-run the frontend tests (``.wt-fecheck.sh test src/lib/components/library``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from typing import Final

import pytest

from backend.generation.contracts.issues import registry_payload
from backend.generation.testing import content as builders
from backend.generation.testing.golden_library import install_golden_library
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

API: Final = "/api/generation"
FIXTURES: Final = Path(__file__).resolve().parents[4] / "frontend" / "src" / "lib" / "api" / "v3" / "__fixtures__"
WRITE: Final = os.environ.get("WP14_WRITE_FIXTURES") == "1"
FIXED_TIME: Final = "2026-01-01T00:00:00Z"

# The cell every render fixture uses (golden library levels).
APP_CONTEXT: Final = {
    "task": "crud_todo_list@1",
    "variant": "standard",
    "stack": "flask-react",
    "starter_level": "minimal",
    "style": "tailwind@1",
    "auth_mode": "cookie_session",
    "guidance": "generic@1",
    "harness_preset": "opencode-native@1",
}


def _normalize(value: Any) -> Any:
    """Fixed timestamps and users, so fixtures do not depend on when or by whom they were made."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key == "created_at" and isinstance(item, str):
                out[key] = FIXED_TIME
            elif key == "created_by" and isinstance(item, dict):
                out[key] = {"id": 1, "username": "researcher"}
            else:
                out[key] = _normalize(item)
        return out
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _shape(value: Any) -> Any:
    """The schema of a JSON value: key sets and scalar types, list elements merged, ``None`` = unknown.

    Used for fixtures whose *values* are other WPs' facts (harness prose, versions, vocabularies) so the
    drift check catches schema changes the frontend types depend on, not wording or version bumps.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in value.items()}
    if isinstance(value, list):
        merged: Any = None
        for item in value:
            merged = _merge_shapes(merged, _shape(item))
        return ["list", merged]
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    return type(value).__name__


def _merge_shapes(a: Any, b: Any) -> Any:
    if a is None:
        return b
    if b is None:
        return a
    if isinstance(a, dict) and isinstance(b, dict):
        return {key: _merge_shapes(a.get(key), b.get(key)) for key in a.keys() | b.keys()}
    if isinstance(a, list) and isinstance(b, list):
        return ["list", _merge_shapes(a[1], b[1])]
    if isinstance(a, str) and isinstance(b, str):
        return "|".join(sorted(set(a.split("|")) | set(b.split("|"))))
    return "mixed"


def _shape_mismatches(stored: Any, fresh: Any, path: str = "$") -> list[str]:
    """Where two shapes disagree; ``None`` (null / empty list) on either side matches anything."""
    if stored is None or fresh is None:
        return []
    if isinstance(stored, dict) and isinstance(fresh, dict):
        if stored.keys() != fresh.keys():
            return [f"{path}: keys {sorted(stored.keys() ^ fresh.keys())} added or removed"]
        return [m for key in stored for m in _shape_mismatches(stored[key], fresh[key], f"{path}.{key}")]
    if isinstance(stored, list) and isinstance(fresh, list):
        return _shape_mismatches(stored[1], fresh[1], f"{path}[]")
    return [] if stored == fresh else [f"{path}: {stored} != {fresh}"]


def _check(name: str, payload: Any, *, schema_only: bool = False) -> None:
    path = FIXTURES / name
    data = _normalize(payload)
    if WRITE:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    assert path.exists(), f"{path} is missing; regenerate the frontend fixtures (see the module docstring)"
    stored = json.loads(path.read_text(encoding="utf-8"))
    hint = (
        "if the change is intended, regenerate the frontend fixtures "
        "(WP14_WRITE_FIXTURES=1, see the module docstring) and update lib/api/v3/types.ts"
    )
    if schema_only:
        mismatches = _shape_mismatches(_shape(stored), _shape(data))
        assert not mismatches, f"{name}: the API's schema drifted ({'; '.join(mismatches[:10])}); {hint}"
        return
    assert stored == data, f"{name} no longer matches the API; {hint}"


def _post(client, path: str, body: dict[str, Any]):
    return client.post(f"{API}{path}", data=json.dumps(body), content_type="application/json")


@pytest.fixture
def researcher(client):
    install_golden_library()
    user = UserFactory.create()
    client.force_login(user)
    return client


def test_knobs_fixture(researcher):
    response = researcher.get(f"{API}/budget/knobs/")
    assert response.status_code == 200
    body = response.json()
    # Every knob the editor shows is in the catalogue, and every key limit is a knob.
    keys = {row["key"] for row in body["knobs"]}
    assert set(body["key_limits"]) <= keys
    assert set(body["tiers"]) == {"frugal", "standard", "generous"}
    _check("knobs.json", body)


@pytest.mark.parametrize("mode", ["app", "slot"])
def test_variables_fixture(researcher, mode):
    response = researcher.get(f"{API}/prompts/variables/?mode={mode}")
    assert response.status_code == 200
    _check(f"variables-{mode}.json", response.json())


def test_render_fixture_and_budget_neutrality(researcher):
    response = _post(researcher, "/prompts/render/", {"prompt_set": "app-default@1", "context_from": APP_CONTEXT})
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["stimuli"], "the golden prompt set renders at least one stimulus"
    for stimulus in body["stimuli"]:
        covered = sorted((s["char_start"], s["char_end"]) for s in stimulus["sources"])
        assert all(0 <= start <= end <= stimulus["chars"] for start, end in covered)
    _check("render-app.json", body)

    # The playground's promise: the Budget preset never changes the rendered text (Appendix A.2).
    context = {**APP_CONTEXT, "resource_policy": "frugal@1"}
    rendered = _post(researcher, "/prompts/render/", {"prompt_set": "app-default@1", "context_from": context})
    assert rendered.status_code == 200, rendered.content
    assert rendered.json()["prompt_hash"] == body["prompt_hash"]


def test_render_diff_fixture(researcher):
    a = {"prompt_set": "app-default@1", "context_from": APP_CONTEXT}
    b = {"prompt_set": "app-default@1", "context_from": {**APP_CONTEXT, "variant": "minimal"}}
    response = _post(researcher, "/prompts/diff/", {"a": a, "b": b})
    assert response.status_code == 200, response.content
    _check("prompt-diff.json", response.json())


@pytest.mark.parametrize("slug", ["crud_todo_list", "notes_markdown_render"])
def test_task_fixtures(researcher, slug):
    response = researcher.get(f"{API}/library/tasks/{slug}/versions/1/")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["kind"] == "task"
    _check(f"task-{slug}.json", body)


def test_resource_policy_list_fixture(researcher):
    response = researcher.get(f"{API}/library/resource-policies/?page_size=200")
    assert response.status_code == 200
    _check("library-resource-policies.json", response.json())


def test_version_diff_fixture(researcher):
    v1 = builders.prompt_content(template="Build {{ task.title }}.\nKeep it small.\n")
    v2 = builders.prompt_content(template="Build {{ task.title }} with care.\nKeep it small.\nAdd tests.\n")
    created = _post(researcher, "/library/prompts/", {"slug": "fixture-brief", "name": "Fixture brief", "content": v1})
    assert created.status_code == 200, created.content
    versioned = _post(researcher, "/library/prompts/fixture-brief/versions/", {"base_version": 1, "content": v2})
    assert versioned.status_code == 200, versioned.content
    response = researcher.get(f"{API}/library/prompts/fixture-brief/diff/?a=1&b=2")
    assert response.status_code == 200
    _check("library-diff-prompt.json", response.json())


def test_harness_and_config_fixtures(researcher):
    harnesses = researcher.get(f"{API}/harnesses/")
    assert harnesses.status_code == 200
    # Harness facts and the config vocabulary belong to other WPs (prose, versions, open questions change
    # in their fix rounds): only their schema is pinned here.
    _check("harnesses.json", harnesses.json(), schema_only=True)
    config = researcher.get(f"{API}/config/")
    assert config.status_code == 200
    _check("config.json", config.json(), schema_only=True)


def test_config_issue_codes_match_the_registry(researcher):
    """`GenerationConfigOut.issue_codes` (`IssueCodeOut[]`) is exactly `registry_payload()`."""
    stored = json.loads((FIXTURES / "config.json").read_text(encoding="utf-8"))
    registry = registry_payload()
    assert {code["code"] for code in stored["issue_codes"]} == {row["code"] for row in registry}
    for row in stored["issue_codes"]:
        assert set(row) == {"code", "exploratory", "confirmatory", "alternatives", "owner", "summary"}
        assert row["exploratory"] in {None, "error", "warn", "info"}
        assert row["confirmatory"] in {None, "error", "warn", "info"}
        assert set(row["alternatives"]) <= {"error", "warn", "info"}
    assert researcher.get(f"{API}/config/").json()["issue_codes"] == registry


def test_shape_check_ignores_wording_but_not_schema():
    a = [{"name": "codex", "blocked_reason": "old prose", "open_questions": [], "versions": ["1"]}]
    b = [{"name": "codex", "blocked_reason": None, "open_questions": ["new question"], "versions": ["2", "3"]}]
    assert _shape_mismatches(_shape(a), _shape(b)) == []
    renamed = [{"name": "codex", "blocked_because": "x", "open_questions": [], "versions": ["1"]}]
    assert _shape_mismatches(_shape(a), _shape(renamed))
    retyped = [{"name": "codex", "blocked_reason": "x", "open_questions": [], "versions": [1]}]
    assert _shape_mismatches(_shape(a), _shape(retyped))
