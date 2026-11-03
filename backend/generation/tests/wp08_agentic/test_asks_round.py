"""Cross-WP asks addressed to WP-08 (CHANGES): context window, runs list task fields and search,
manifest source spans, the plain-text file content response, pinned manifest refs and pinned treatment
columns (follow-ups #3b and #4)."""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client
from pydantic import ValidationError as PydanticValidationError

from backend.generation.agentic import runner
from backend.generation.agentic.manifest import build_manifest
from backend.generation.agentic.manifest import unpinned_refs
from backend.generation.contracts.manifest import StimulusRecord
from backend.generation.models import GenerationRun
from backend.generation.models import ImmutableVersionError
from backend.generation.models import RunManifest
from backend.generation.testing.fake_gateway import canned_call
from backend.generation.testing.fake_harness import FakeHarness

pytestmark = pytest.mark.django_db

BASE = "/api/generation"


@pytest.fixture
def client(agentic_env) -> Client:
    client = Client()
    client.force_login(agentic_env.user)
    return client


@pytest.fixture
def generated(agentic_env) -> GenerationRun:
    agentic_env.script.files = {"backend/todos.py": b"def list_todos():\n    return []\n"}
    agentic_env.script.calls = [canned_call(1)]
    run = agentic_env.make_run()
    runner.run_app_agentic(run, backend=agentic_env.backend)
    run.refresh_from_db()
    return run


# -- WP-07 -> WP-08: model_context_window -------------------------------------------------------------


def test_harness_context_carries_the_model_context_window(agentic_env, monkeypatch) -> None:
    seen: list[Any] = []
    original = FakeHarness.render

    def render(self: FakeHarness, ctx: Any) -> Any:
        seen.append(ctx)
        return original(self, ctx)

    monkeypatch.setattr(FakeHarness, "render", render)
    agentic_env.gateway_overrides = {"model": {"context_length": 65_536}}
    agentic_env.script.files = {"app.py": b"print('hi')\n"}
    agentic_env.script.calls = [canned_call(1)]
    runner.run_app_agentic(agentic_env.make_run(), backend=agentic_env.backend)
    assert [ctx.model_context_window for ctx in seen] == [65_536]


def test_unknown_context_window_stays_none(agentic_env, monkeypatch) -> None:
    seen: list[Any] = []
    original = FakeHarness.render
    monkeypatch.setattr(FakeHarness, "render", lambda self, ctx: (seen.append(ctx), original(self, ctx))[1])
    agentic_env.script.files = {"app.py": b"print('hi')\n"}
    agentic_env.script.calls = [canned_call(1)]
    runner.run_app_agentic(agentic_env.make_run(), backend=agentic_env.backend)
    assert [ctx.model_context_window for ctx in seen] == [None]


# -- WP-17 -> WP-08: task fields, q and status on GET /runs/ -------------------------------------------


def test_list_rows_carry_the_task(client, generated) -> None:
    item = client.get(f"{BASE}/runs/").json()["items"][0]
    version = generated.task_version
    assert item["task"] == version.ref  # slug@v
    assert item["task_title"] == (version.content or {}).get("title", version.name)
    detail = client.get(f"{BASE}/runs/{generated.pk}/").json()
    assert detail["task"] == version.ref


def test_list_search(client, generated) -> None:
    def total(**params: Any) -> int:
        return client.get(f"{BASE}/runs/", params).json()["total"]

    slug = generated.task_version.task.slug
    assert total(q=slug.upper()) == 1
    assert total(q="deepseek-v4.1") == 1  # model id
    assert total(q=str(generated.pk)[:8]) == 1  # run id prefix
    assert total(q=str(generated.pk)) == 1
    assert total(q="no-such-thing-anywhere") == 0
    title = (generated.task_version.content or {}).get("title") or generated.task_version.name
    assert total(q=title.split()[0].lower()) == 1


def test_list_status_filter(client, generated) -> None:
    status = generated.status
    assert client.get(f"{BASE}/runs/", {"status": status}).json()["total"] == 1
    assert client.get(f"{BASE}/runs/", {"status": f"failed,{status}"}).json()["total"] == 1
    assert client.get(f"{BASE}/runs/", {"status": "failed"}).json()["total"] == 0
    response = client.get(f"{BASE}/runs/", {"status": "bogus"})
    assert response.status_code == 400
    assert response.json()["code"] == "invalid_filter"


# -- WP-16 -> WP-08: manifest source spans; files content type ------------------------------------------


def test_manifest_stimuli_carry_source_spans(agentic_env, client, monkeypatch) -> None:
    original = agentic_env.resolved_cell

    def with_spans(run: GenerationRun, **kwargs: Any) -> Any:
        cell, keys = original(run, **kwargs)
        stimulus = cell.stimuli[0].model_dump(mode="json")
        stimulus["sources"] = [
            {
                "template_ref": "brief-app-default@1",
                "owner_kind": "task",
                "owner_ref": run.task_version.ref,
                "role": "brief",
                "char_start": 0,
                "char_end": 5,
            },
        ]
        return cell.model_copy(update={"stimuli": [StimulusRecord.model_validate(stimulus)]}), keys

    monkeypatch.setattr(agentic_env, "resolved_cell", with_spans)
    run = agentic_env.make_run(claimed=False)
    stimuli = client.get(f"{BASE}/runs/{run.pk}/manifest/").json()["stimuli"]
    assert stimuli[0]["sources"] == [
        {
            "template_ref": "brief-app-default@1",
            "owner_kind": "task",
            "owner_ref": run.task_version.ref,
            "role": "brief",
            "char_start": 0,
            "char_end": 5,
        },
    ]


def test_manifest_without_spans_returns_an_empty_list(client, generated) -> None:
    stimuli = client.get(f"{BASE}/runs/{generated.pk}/manifest/").json()["stimuli"]
    assert stimuli[0]["sources"] == []


def test_file_content_is_declared_and_served_as_text_plain(client, generated) -> None:
    response = client.get(f"{BASE}/runs/{generated.pk}/files/content/", {"path": "backend/todos.py"})
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    assert response.content.decode() == "def list_todos():\n    return []\n"
    from config.api import api

    schema = api.get_openapi_schema()
    op = schema["paths"]["/api/generation/runs/{run_id}/files/content/"]["get"]
    assert op["operationId"] == "backend_generation_runs_api_get_run_file_content"
    ok = op["responses"]["200"]
    assert list(ok["content"]) == ["text/plain"]
    assert ok["content"]["text/plain"]["schema"] == {"type": "string"}


# -- follow-up #4: manifest library refs are pinned ----------------------------------------------------


def test_build_manifest_refuses_unpinned_library_refs(agentic_env) -> None:
    run = agentic_env.make_run(claimed=False)
    RunManifest.objects.filter(run=run).delete()
    cell, _ = agentic_env.resolved_cell(run)
    body = cell.manifest.model_dump(mode="json")
    body["sampling"]["ref"] = "t07@1"
    body["harness"]["preset_ref"] = "fake-native"
    # The contract itself refuses them now (follow-up #4: manifest refs are PinnedRefStr) ...
    with pytest.raises(PydanticValidationError) as contract:
        type(cell.manifest).model_validate(body)
    assert {"sampling.ref", "harness.preset_ref"} <= {".".join(map(str, e["loc"])) for e in contract.value.errors()}
    # ... and build_manifest still checks a payload that bypassed validation (model_construct).
    bad = cell.model_copy(update={"manifest": type(cell.manifest).model_construct(**body)})
    with pytest.raises(Exception, match="not a pinned ref") as info:
        build_manifest(run, bad)
    assert {"sampling.ref", "harness.preset_ref"} <= {p.split(":")[0] for p in info.value.message_dict["refs"]}
    assert not RunManifest.objects.filter(run=run).exists()


def test_unpinned_refs_lists_every_library_ref(agentic_env) -> None:
    run = agentic_env.make_run(claimed=False)
    payload = RunManifest.objects.get(run=run).manifest
    assert unpinned_refs(payload) == []
    payload["factors"]["stack_profile"]["ref"] = "flask-react@1"
    payload["task"]["ref"] = "crud_todo_list@1"
    payload["starter"]["ref"] = "flask-react-minimal@1"
    payload["resources"]["ref"] = "frugal"
    payload["planned_evaluation"]["eval_suite"]["ref"] = "eval-v2@1"
    payload["stimuli"][0]["template_ref"] = "draft:brief"  # not a library version: never listed
    paths = [p.split(":")[0] for p in unpinned_refs(payload)]
    assert paths == [
        "factors.stack_profile.ref",
        "task.ref",
        "starter.ref",
        "resources.ref",
        "planned_evaluation.eval_suite.ref",
    ]


# -- follow-up #3b: treatment columns are pinned against the manifest ----------------------------------


def test_treatment_columns_cannot_drift_from_the_manifest(agentic_env) -> None:
    golden = agentic_env.golden
    run = agentic_env.make_run(claimed=False)
    other_stack = golden.get("stack", "fastapi-vue")
    run.stack_profile = other_stack
    with pytest.raises(ImmutableVersionError, match="stack_profile"):
        run.save()
    run.refresh_from_db()
    run.task_version = golden.get("task", "notes_markdown_render")
    with pytest.raises(ImmutableVersionError, match="task_version"):
        run.save(update_fields=["task_version"])
    run.refresh_from_db()
    run.prose_variant = "minimal"
    with pytest.raises(ImmutableVersionError, match="prose_variant"):
        run.save(update_fields=["prose_variant"])
    run.refresh_from_db()
    run.starter = None  # clearing a pinned column is a change too
    with pytest.raises(ImmutableVersionError, match="starter"):
        run.save()


def test_empty_treatment_columns_fill_only_with_the_manifest_value(agentic_env) -> None:
    run = agentic_env.make_run(claimed=False)
    stack = run.stack_profile
    GenerationRun.objects.filter(pk=run.pk).update(stack_profile=None)  # bulk update is not guarded
    run.refresh_from_db()
    run.stack_profile = agentic_env.golden.get("stack", "fastapi-vue")
    with pytest.raises(ImmutableVersionError, match="stack_profile"):
        run.save()
    run.stack_profile = stack
    run.save()  # empty -> the manifest's pinned ref is allowed
    run.refresh_from_db()
    assert run.stack_profile == stack
    run.outcome_reason = "unchanged treatment saves normally"
    run.save()


def test_runs_without_a_manifest_are_not_pinned(agentic_env) -> None:
    run = agentic_env.make_run(claimed=False)
    RunManifest.objects.filter(run=run).delete()
    run.stack_profile = agentic_env.golden.get("stack", "fastapi-vue")
    run.save()
