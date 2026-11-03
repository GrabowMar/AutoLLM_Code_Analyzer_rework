"""WP-09: the acceptance compiler (spec 8.1-8.4): snapshots for 3 tasks x 3 auth modes and the merge rules.

Snapshots live in ``snapshots/<task>.<auth_mode>.json``; regenerate them (after an intended compiler change,
with a ``COMPILER_VERSION`` bump) with ``WP09_UPDATE_SNAPSHOTS=1``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.generation.contracts.library import TaskSpecContent
from backend.generation.evaluation import acceptance
from backend.generation.evaluation import probes as probe_catalog

DATA = Path(__file__).resolve().parents[2] / "data"
SNAPSHOTS = Path(__file__).with_name("snapshots")
TASKS = ("crud_todo_list", "team_roles_multitenant", "file_share_multipart")
STACK = yaml.safe_load((DATA / "stacks" / "flask-react.yaml").read_text())["content"]


def task_content(slug: str) -> dict[str, Any]:
    content = yaml.safe_load((DATA / "tasks" / slug / "task.yaml").read_text())
    content["prose"] = {
        p.stem: p.read_text().rstrip("\n") for p in sorted((DATA / "tasks" / slug / "prose").glob("*.md"))
    }
    return TaskSpecContent.model_validate(content).model_dump(mode="json", by_alias=True)


@pytest.mark.parametrize("mode", acceptance.AUTH_MODES)
@pytest.mark.parametrize("slug", TASKS)
def test_compiler_snapshot(slug: str, mode: str) -> None:
    plan = acceptance.plan_json(acceptance.compile_content(task_content(slug), mode, STACK))
    path = SNAPSHOTS / f"{slug}.{mode}.json"
    text = json.dumps(plan, indent=1, sort_keys=True) + "\n"
    if os.environ.get("WP09_UPDATE_SNAPSHOTS") == "1" or not path.exists():
        SNAPSHOTS.mkdir(exist_ok=True)
        path.write_text(text)
    assert text == path.read_text(), f"compiler output for {slug}/{mode} changed; bump COMPILER_VERSION"


def test_plan_shape_and_rules() -> None:
    content = task_content("crud_todo_list")
    plan = acceptance.compile_content(content, "contract_bearer", STACK)
    ids = [c.id for c in plan.checks]
    assert ids[0] == "health"
    assert {"index_served", "spa_fallback"} <= set(ids)
    assert {"todos.get.ok", "todos.get.anon_401", "todos.get.other_user", "todos.create.invalid.missing"} <= set(ids)
    assert "todos.list.page_size" in ids
    assert "todos.list.filter.priority" in ids
    assert "todos.get.persists" in ids
    by_id = {c.id: c for c in plan.checks}
    assert by_id["todos.get.other_user"].expect.status == 404  # errors lists 404 only
    assert by_id["todos.create.invalid.missing"].expect.status == [400, 422]
    assert by_id["todos.get.persists"].restart_before is True
    assert all(c.depends_on == ["health"] for c in plan.checks if c.id != "health")
    # the hand-written check is merged and keeps its statedness
    assert by_id["todos.list.open_first"].source == "acceptance"
    assert by_id["todos.list.open_first"].stated_in == ["standard", "exhaustive"]
    # compiled checks are tagged by the lint rules
    assert any(c.stated_in for c in plan.checks if c.source == "compiler")
    # no secret in the plan
    assert "ADMIN_PASSWORD" not in json.dumps(acceptance.plan_json(plan))


def test_hand_written_check_wins_on_id_collision() -> None:
    content = task_content("crud_todo_list")
    content["acceptance"].append(
        {
            "id": "todos.create.ok",
            "category": "functional",
            "endpoint": "todos.create",
            "request": {"method": "POST", "path": "/api/todos", "json": {"title": "mine"}},
            "expect": {"status": 201},
        },
    )
    plan = acceptance.compile_content(content, "contract_bearer", STACK)
    check = next(c for c in plan.checks if c.id == "todos.create.ok")
    assert check.source == "acceptance"
    assert check.request.json_body == {"title": "mine"}


def test_cookie_and_spec_defined_drivers() -> None:
    content = task_content("crud_todo_list")
    cookie = acceptance.compile_content(content, "cookie_session", STACK).auth
    assert cookie.credential == "cookie"
    assert cookie.csrf == "double_submit_cookie"
    spec = acceptance.compile_content(content, "spec_defined", STACK).auth
    assert spec.login_path == "/api/auth/login"
    assert spec.register_path == "/api/auth/register"


def test_probes_get_preconditions_and_controls() -> None:
    plan = acceptance.compile_content(task_content("crud_todo_list"), "contract_bearer", STACK)
    idor = next(p for p in plan.probes if p.kind == "idor_enumeration")
    assert idor.requires_checks == ["todos.get.ok"]
    assert idor.positive_control is not None
    plaintext = next(p for p in plan.probes if p.kind == "plaintext_password_db")
    assert plaintext.requires_checks == ["auth.login.ok"]


def test_unvalidated_probe_kinds_are_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probe_catalog, "kind_validated", lambda kind, record=None: kind != "idor_enumeration")
    plan = acceptance.compile_content(task_content("crud_todo_list"), "contract_bearer", STACK)
    idor = next(p for p in plan.probes if p.kind == "idor_enumeration")
    assert idor.params["disabled"] is True
    assert "probe_unvalidated" in idor.params["disabled_reason"]


def test_all_applicable_adds_generic_probes() -> None:
    content = task_content("crud_todo_list")
    plan = acceptance.compile_content(content, "contract_bearer", STACK, probes="all_applicable")
    kinds = {p.kind for p in plan.probes}
    assert {"idor_enumeration", "mass_assignment", "plaintext_password_db", "rate_limit_missing"} <= kinds
    none = acceptance.compile_content(content, "contract_bearer", STACK, probes="none")
    assert none.probes == []


def test_webhook_routes_are_signed() -> None:
    plan = acceptance.compile_content(task_content_webhook(), "contract_bearer", STACK)
    assert plan.signed_routes == [{"method": "POST", "path": "/api/webhooks/payments"}]


def task_content_webhook() -> dict[str, Any]:
    return task_content("webhook_receiver_signed")


@pytest.mark.django_db
def test_checks_for_endpoints_lists_compiled_and_hand_written_checks() -> None:
    from backend.generation.testing.factories import TaskSpecVersionFactory

    version = TaskSpecVersionFactory(content=task_content("crud_todo_list"))
    checks = acceptance.checks_for_endpoints(version, ["todos.list"])
    assert "todos.list.ok" in checks
    assert "todos.list.open_first" in checks
    assert all(c.startswith("todos.list") for c in checks)
    functional = acceptance.checks_for_endpoints(version, ["todos.list"], categories=["functional"])
    assert "todos.list.anon_401" not in functional
    assert "todos.list.ok" in functional
