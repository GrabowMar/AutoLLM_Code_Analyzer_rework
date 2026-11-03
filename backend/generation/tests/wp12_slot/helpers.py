"""Builders shared by the slot-mode tests (the golden parent, claimed slot runs)."""

from __future__ import annotations

from typing import Any

from backend.generation import blobs
from backend.generation.models import EvalSuite
from backend.generation.models import GenerationRun
from backend.generation.models import RunEvaluation
from backend.generation.models import RunFile
from backend.generation.models import RunManifest
from backend.generation.models import SlotTarget
from backend.generation.models.choices import EvaluationStatus
from backend.generation.models.choices import RunKind
from backend.generation.models.choices import RunStatus
from backend.generation.testing.content import fake_sha
from backend.generation.testing.factories import run_manifest_content
from backend.generation.testing.fake_slot import slot_eval_result
from backend.generation.testing.golden_library import InstalledGoldenLibrary
from backend.generation.testing.golden_library import install_parent_run

LEASE = "exec-test"


def make_parent(installed: InstalledGoldenLibrary, user: Any, *, model: str | None = None) -> GenerationRun:
    """The golden parent app as a tested app run with a manifest and a measured parent evaluation."""
    parent = install_parent_run(installed, created_by=user)
    if model is not None:
        from backend.llm_models.models import LLMModel

        llm, _ = LLMModel.objects.get_or_create(
            model_id=model,
            defaults={"canonical_slug": model.replace("/", "_"), "provider": "test", "model_name": model},
        )
        GenerationRun.objects.filter(pk=parent.pk).update(model=llm)
        parent.refresh_from_db()
    RunManifest.objects.create(run=parent, manifest=run_manifest_content(parent))
    suite = installed.get("eval_suite", "eval-v2")
    RunEvaluation.objects.create(
        run=parent,
        eval_suite=suite,
        eval_level="security",
        status=EvaluationStatus.MEASURED,
        result=slot_eval_result({"todos.list.ok": "pass"}),
        image_id="sha256:" + fake_sha("parent-image"),
    )
    return parent


def eval_suite(installed: InstalledGoldenLibrary) -> EvalSuite:
    return installed.get("eval_suite", "eval-v2")


def slot_run(
    target: SlotTarget,
    *,
    kind: str = RunKind.SLOT_SINGLE_SHOT,
    user: Any = None,
    stimuli: dict[str, str] | None = None,
    manifest_updates: dict[str, Any] | None = None,
    seed: int = 1234,
) -> GenerationRun:
    """A claimed slot variant of ``target`` with a manifest and rendered stimuli in the blob store."""
    slot_set = target.slot_set
    run = GenerationRun.objects.create(
        kind=kind,
        status=RunStatus.RUNNING,
        phase="running",
        slot_target=target,
        parent_run=slot_set.parent_run,
        task_version=slot_set.task_version,
        model=slot_set.parent_run.model,
        seed=seed,
        attempt=0,  # the first claim (0-based requeue counter)
        lease_owner=LEASE,
        created_by=user or slot_set.created_by,
    )
    content = run_manifest_content(run)
    content["slot"]["resolved_context"] = {"files": [], "sha": fake_sha("context")}
    for key, value in (manifest_updates or {}).items():
        if isinstance(value, dict) and isinstance(content.get(key), dict):
            content[key] = {**content[key], **value}
        else:
            content[key] = value
    texts = stimuli or {
        "slot_single_shot_system": "You complete one part of an existing application.",
        "slot_single_shot_user": f"Write the missing {target.label}.",
    }
    records = []
    for role, text in texts.items():
        sha = blobs.put_text(text)
        records.append(
            {
                "role": role,
                "template_ref": f"{role}@1",
                "sha": sha,
                "blob": sha,
                "chars": len(text),
                "est_tokens": len(text) // 4,
            },
        )
    RunManifest.objects.create(run=run, manifest=content, stimuli=records)
    return run


def parent_text(parent: GenerationRun, path: str) -> str:
    row = RunFile.objects.get(run=parent, path=path)
    return blobs.get_text(row.blob)
