"""The slots API (spec section 9.6) through ninja's TestClient on the slot router."""

from __future__ import annotations

import json
from typing import Any

import pytest
from ninja.testing import TestClient

from backend.generation.models import GenerationRun
from backend.generation.models import SlotTarget
from backend.generation.models import StudyUnit
from backend.generation.slot import selection
from backend.generation.slot.api import router
from backend.generation.slot.assemble import assemble
from backend.generation.slot.part import part_of
from backend.generation.slot.variants import record_variant
from backend.generation.tests.wp12_slot.helpers import slot_run
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PATCH = {"kind": "endpoint", "endpoint": {"method": "PATCH", "path": "/api/todos/{id}"}}


class Client:
    def __init__(self, user: Any) -> None:
        self.user = user
        self.client = TestClient(router)

    def get(self, url: str, **params: Any) -> Any:
        return self.client.get(url, query_params=params, auth=self.user, user=self.user)

    def post(self, path: str, body: Any = None) -> Any:
        return self.client.post(
            path,
            data=json.dumps(body or {}),
            content_type="application/json",
            auth=self.user,
            user=self.user,
        )

    def delete(self, path: str) -> Any:
        return self.client.delete(path, auth=self.user, user=self.user)


@pytest.fixture
def api(researcher: Any) -> Client:
    return Client(researcher)


def test_parents_list_draw_and_freeze(api: Client, parent: GenerationRun, acceptance: Any) -> None:
    listed = api.get("/slots/parents/", task=parent.task_version.ref)
    assert listed.status_code == 200
    (row,) = listed.json()["items"]
    assert row["run_id"] == str(parent.pk)
    assert row["frozen"] is False
    drawn = api.post("/slots/parents/draw/", {"pool_query": {"task": parent.task_version.ref}, "k": 3, "seed": 5})
    assert drawn.json() == {"parents": [str(parent.pk)], "seed": 5, "pool_size": 1}
    created = api.post("/slots/sets/", {"parent_run": str(parent.pk), "name": "p1"})
    assert created.status_code == 201
    body = created.json()
    assert body["frozen_tree_hash"] == parent.tree_hash
    assert body["index_stats"]["endpoints"] >= 6
    assert body["parent_levels"] == {
        "stack_profile": parent.stack_profile.pinned_ref if parent.stack_profile is not None else None,
        "starter_level": parent.starter.level if parent.starter is not None else None,
        "auth_mode": parent.auth_mode or None,
        "prose_variant": parent.prose_variant or None,
    }
    assert body["parent_levels"]["stack_profile"]
    assert api.get("/slots/parents/").json()["items"][0]["slot_sets"] == 1
    other = Client(UserFactory.create())
    assert other.get("/slots/parents/").json()["total"] == 0
    assert other.get(f"/slots/sets/{body['id']}/").status_code == 404


def test_draw_and_list_rank_only_the_users_visible_runs(api: Client, researcher: Any) -> None:
    from backend.generation.contracts.slot import ParentSelection
    from backend.generation.slot import parents
    from backend.generation.testing.factories import GenerationRunFactory
    from backend.generation.testing.factories import TaskSpecVersionFactory

    task = TaskSpecVersionFactory.create()
    stranger = UserFactory.create()
    mine = [GenerationRunFactory.create(tested=True, task_version=task, created_by=researcher) for _ in range(3)]
    for _ in range(6):
        GenerationRunFactory.create(tested=True, task_version=task, created_by=stranger)
    body = {"pool_query": {"task": task.ref}, "k": 2, "seed": 11, "stratify_by": "none"}
    drawn = api.post("/slots/parents/draw/", body).json()
    assert drawn["pool_size"] == 3  # other users' runs are not in the pool
    assert len(drawn["parents"]) == 2  # k honoured within the visible pool
    assert set(drawn["parents"]) <= {str(r.pk) for r in mine}
    # Reproducible for this user: the same seed over the same visible pool, whatever others add.
    GenerationRunFactory.create(tested=True, task_version=task, created_by=stranger)
    assert api.post("/slots/parents/draw/", body).json() == drawn
    visible = GenerationRun.objects.filter(created_by=researcher)
    expected = parents.draw(ParentSelection.model_validate(body), base=visible)
    assert drawn["parents"] == [str(r.pk) for r in expected]
    assert api.get("/slots/parents/", task=task.ref).json()["total"] == 3


def test_slot_set_browsing(api: Client, slot_set: Any) -> None:
    base = f"/slots/sets/{slot_set.pk}"
    tree = api.get(f"{base}/tree/").json()
    todos = next(row for row in tree if row["path"] == "backend/todos.py")
    assert todos["symbols"] >= 5
    assert next(row for row in tree if row["path"] == "backend/tests/test_todos.py")["is_test"] is True
    symbols = api.get(f"{base}/symbols/", path="backend/todos.py", kind="route_handler").json()
    assert [s["qualname"] for s in symbols] == ["list_todos", "create_todo", "update_todo", "delete_todo"]
    endpoints = api.get(f"{base}/endpoints/").json()
    update = next(e for e in endpoints if e["spec_endpoint_id"] == "todos.update")
    assert (update["linked_checks"], update["linked_probes"]) == (1, 1)
    components = api.get(f"{base}/components/").json()
    assert {c["name"] for c in components} == {"App", "TodoForm", "TodoList"}
    file = api.get(f"{base}/file/", path="backend/auth.py").json()
    assert file["language"] == "python"
    assert any(s["qualname"] == "login" for s in file["symbols"])
    assert api.get(f"{base}/file/", path="nope.py").status_code == 404
    listing = api.get("/slots/sets/", task=slot_set.task_version.ref).json()
    assert listing["total"] == 1


def test_preview_create_get_and_errors(api: Client, slot_set: Any, evaluator: Any) -> None:
    base = f"/slots/sets/{slot_set.pk}"
    preview = api.post(f"{base}/preview-target/", {**PATCH, "cost_model": slot_set.parent_model})
    assert preview.status_code == 200
    body = preview.json()
    assert body["linked_checks"] == ["todos.update.ok"]
    assert body["cost_hint"]["model"] == slot_set.parent_model
    stale = api.post(
        f"{base}/preview-target/",
        {
            "kind": "region",
            "path": "backend/todos.py",
            "span": {"start_line": 1, "end_line": 2},
            "content_sha": "0" * 64,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "address_stale"
    invalid = api.post(f"{base}/preview-target/", {"kind": "symbol", "symbol_id": "nope"})
    assert invalid.status_code == 422
    created = api.post(f"{base}/targets/", {**PATCH, "label": "update handler"})
    assert created.status_code == 201
    target = created.json()
    assert target["label"] == "update handler"
    assert target["validation"]["status"] == "pending"
    assert target["usable"] is False
    again = api.post(f"{base}/targets/", {**PATCH, "label": "update handler"})
    assert (again.status_code, again.json()["id"]) == (200, target["id"])
    detail = api.get(f"/slots/targets/{target['id']}/").json()
    assert detail["variants_summary"] == {"n": 0, "by_outcome": {}, "by_parse_status": {}}
    assert detail["original_text"].startswith('@todos.patch("/<int:todo_id>")')


def test_match_revalidate_and_delete(api: Client, slot_set: Any, golden: Any, researcher: Any) -> None:
    from backend.generation.slot.freeze import freeze
    from backend.generation.tests.wp12_slot.helpers import make_parent

    other = freeze(make_parent(golden, researcher, model="openai/gpt-oss-120b"), name="other", user=researcher)
    target = selection.create_target(slot_set, PATCH, user=researcher, validate=False)
    matched = api.post(f"/slots/targets/{target.pk}/match/", {"slot_sets": "same_task"}).json()
    assert [t["slot_set"] for t in matched["created"]] == [str(other.pk)]
    revalidated = api.post(f"/slots/targets/{target.pk}/revalidate/")
    assert revalidated.status_code == 200
    assert len(revalidated.json()["validation"]["runs"]) == 4
    unit = StudyUnit.objects.filter(slot_target=target)
    assert not unit.exists()
    deleted = api.delete(f"/slots/targets/{target.pk}/")
    assert deleted.status_code == 204
    assert not SlotTarget.objects.filter(pk=target.pk).exists()
    assert not GenerationRun.objects.filter(slot_target_id=target.pk).exists()


def test_delete_refused_when_variants_exist(api: Client, slot_set: Any, researcher: Any) -> None:
    target = selection.create_target(slot_set, PATCH, user=researcher, validate=False)
    slot_run(target)
    response = api.delete(f"/slots/targets/{target.pk}/")
    assert (response.status_code, response.json()["code"]) == (409, "in_use")


def test_variants_list_and_diff(api: Client, slot_set: Any, researcher: Any) -> None:
    target = selection.create_target(slot_set, PATCH, user=researcher, validate=False)
    part = part_of(target)
    new = part.original_text.replace("return serialize(row)", "return serialize(row), 200")
    a = slot_run(target, seed=1)
    assemble(a, new)
    record_variant(a, part, parse_status="ok", replacement=new)
    b = slot_run(target, seed=2)
    record_variant(b, part, parse_status="invalid_json", replacement=None)
    GenerationRun.objects.filter(pk=b.pk).update(outcome="output_invalid", outcome_reason="invalid_json")
    page = api.get(f"/slots/targets/{target.pk}/variants/").json()
    assert page["total"] == 2
    statuses = {item["run_id"]: item["parse_status"] for item in page["items"]}
    assert statuses == {str(a.pk): "ok", str(b.pk): "invalid_json"}
    assert all(item["slot_pass_rate"] is None for item in page["items"])  # not validated: n/a, never 0
    diff = api.get(f"/slots/variants/{a.pk}/diff/").json()
    assert "+    return serialize(row), 200" in diff["unified"]
    assert diff["original"] == part.original_text
    against = api.get(f"/slots/variants/{a.pk}/diff/", against=str(b.pk))
    assert against.status_code == 200


def test_render_uses_the_slot_prompt_set(api: Client, slot_set: Any, researcher: Any, golden: Any) -> None:
    target = selection.create_target(slot_set, PATCH, user=researcher, validate=False)
    prompt_set = golden.get("prompt_set", "slot-single-shot-default")
    try:
        response = api.post(f"/slots/targets/{target.pk}/render/", {"prompt_set": prompt_set.pinned_ref})
    except NotImplementedError:
        pytest.skip("the prompt engine (WP-03) is not on the branch yet")
    assert response.status_code == 200, response.json()
    body = response.json()
    roles = [s["role"] for s in body["stimuli"]]
    assert roles == ["slot_single_shot_system", "slot_single_shot_user"]
    user_text = body["stimuli"][1]["text"]
    assert "backend/todos.py" in user_text
    assert "raise NotImplementedError" in user_text  # the hole is shown, the original is not
    assert "owner_id = ?" not in user_text.split("def update_todo")[1].split("@todos.delete")[0]
    assert len(body["prompt_hash"]) == 64
    app_set = golden.get("prompt_set", "app-default")
    refused = api.post(f"/slots/targets/{target.pk}/render/", {"prompt_set": app_set.pinned_ref})
    assert refused.status_code == 422
