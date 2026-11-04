"""Manifest freeze (spec 3.8, 6.2): keys are copied (never recomputed), equal ``compute_keys``, and immutable.

Invariant (13.4, WP-08): identity keys are computed only by ``identity.compute_keys``; the manifest's keys
equal a recomputation on the same inputs.
"""

from __future__ import annotations

import pytest

from backend.generation import identity
from backend.generation.agentic import manifest as manifest_module
from backend.generation.agentic.manifest import ManifestMismatchError
from backend.generation.agentic.manifest import build_manifest
from backend.generation.models import GenerationRun
from backend.generation.models import ImmutableVersionError
from backend.generation.models import RunManifest
from backend.generation.models import choices
from backend.generation.tests.wp08_agentic.conftest import identity_inputs

pytestmark = pytest.mark.django_db


def test_manifest_keys_equal_compute_keys_and_are_copied_to_the_run(agentic_env) -> None:
    run = agentic_env.make_run(claimed=False)
    row = RunManifest.objects.get(run=run)
    task = row.manifest["task"]
    expected = identity.compute_keys(identity_inputs(task)).as_dict()
    assert row.manifest["identity"] == expected
    assert row.manifest_hash == identity.H(row.manifest)
    run.refresh_from_db()
    assert run.fingerprints == {name: expected[name] for name in run.fingerprints}
    assert run.seed == 4242
    assert run.stage == "main"
    assert run.repeat_index == 0
    assert run.auth_mode == "contract_bearer"


def test_build_manifest_never_computes_keys(agentic_env, monkeypatch) -> None:
    run = GenerationRun.objects.create(
        kind=choices.RunKind.APP_AGENTIC,
        task_version=agentic_env.golden.get("task", "crud_todo_list"),
        stack_profile=agentic_env.golden.get("stack", "flask-react"),
        starter=agentic_env.golden.get("starter", "flask-react-minimal"),
        model=agentic_env.model,
        created_by=agentic_env.user,
    )
    cell, keys = agentic_env.resolved_cell(run)

    def forbidden(*args, **kwargs):
        msg = "build_manifest must copy the resolved keys, not compute them"
        raise AssertionError(msg)

    monkeypatch.setattr(identity, "compute_keys", forbidden)
    row = build_manifest(run, cell)
    assert row.manifest["identity"] == keys.as_dict()


def test_manifest_is_write_once(agentic_env) -> None:
    run = agentic_env.make_run(claimed=False)
    row = RunManifest.objects.get(run=run)
    row.manifest = {**row.manifest, "network_policy": "none"}
    with pytest.raises(ImmutableVersionError):
        row.save()
    run.refresh_from_db()
    run.seed = 1
    with pytest.raises(ImmutableVersionError):
        run.save()


def test_run_columns_must_agree_with_the_manifest(agentic_env) -> None:
    other_stack = agentic_env.golden.get("stack", "fastapi-vue")
    run = GenerationRun.objects.create(
        kind=choices.RunKind.APP_AGENTIC,
        task_version=agentic_env.golden.get("task", "crud_todo_list"),
        stack_profile=other_stack,
        starter=agentic_env.golden.get("starter", "flask-react-minimal"),
        model=agentic_env.model,
        created_by=agentic_env.user,
    )
    cell, _ = agentic_env.resolved_cell(run)
    # The cell pins flask-react; the run row says fastapi-vue.
    cell.manifest.factors["stack_profile"] = cell.manifest.factors["stack_profile"].model_copy(
        update={"ref": agentic_env.golden.get("stack", "flask-react").pinned_ref},
    )
    with pytest.raises(ManifestMismatchError) as caught:
        build_manifest(run, cell)
    assert "stack_profile" in str(caught.value)
    assert not RunManifest.objects.filter(run=run).exists()


def test_manifest_columns_mirror_the_endpoint_pin() -> None:
    columns = manifest_module.manifest_columns(
        {
            "kind": "app_agentic",
            "protocol": "app@1",
            "model": {"endpoint": {"served_model_name": "qwen3:30b", "digest": "sha256:abc"}},
            "identity": {"task_hash": "a" * 64},
            "sampling": {"seed": 9},
        },
    )
    assert columns["pinned_served_name"] == "qwen3:30b"
    assert columns["pinned_digest"] == "sha256:abc"
    assert columns["task_hash"] == "a" * 64
    assert columns["seed"] == 9
