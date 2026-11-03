"""Fixtures for the WP-08 agentic pipeline tests.

``agentic_env`` installs the golden library and patches the two seams owned by other WPs: the harness
registry (``runner.get_adapter`` -> ``FakeHarness``) and ``budget.gateway_config.build`` (-> a small
``GatewayConfig`` for the run's attempt). ``make_run`` creates a claimed ``app_agentic`` run (status running,
leased, attempt 1) whose manifest is frozen through ``agentic.manifest.build_manifest``.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any

import pytest
from django.utils import timezone

from backend.generation import blobs
from backend.generation import identity
from backend.generation.agentic import runner
from backend.generation.agentic.manifest import build_manifest
from backend.generation.contracts.manifest import ManifestBody
from backend.generation.contracts.manifest import StimulusRecord
from backend.generation.contracts.plan import ResolvedCell
from backend.generation.contracts.plan import cell_key
from backend.generation.contracts.resources import EffectivePolicy
from backend.generation.contracts.resources import preset_content
from backend.generation.models import GenerationRun
from backend.generation.models import choices
from backend.generation.testing import content as samples
from backend.generation.testing.fake_agentic import LocalSandboxBackend
from backend.generation.testing.fake_gateway import gateway_config
from backend.generation.testing.fake_harness import FakeHarness
from backend.generation.testing.fake_harness import FakeScript
from backend.generation.testing.golden_library import install_golden_library
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

EXECUTOR = "executor-wp08"
BRIEF = "Build the todo app described below.\n\nEndpoints: GET /api/todos ..."
SLUG = "deepseek/deepseek-v4.1-flash"


def effective_policy(**time_overrides: Any) -> EffectivePolicy:
    content = preset_content("frugal")
    time = content.time.model_copy(update={"wall_clock_s": 60, "idle_timeout_s": None, **time_overrides})
    content = content.model_copy(update={"time": time})
    return EffectivePolicy(content=content, budget_basis="usd", cap_basis="billed", serving_tier="standard")


def identity_inputs(task: dict[str, Any], *, seed: int = 4242) -> identity.IdentityInputs:
    """Fixed identity inputs of the fake cell (the resolver, WP-10, builds these in production)."""
    treatment = dict.fromkeys(identity.TREATMENT_KEYS)
    treatment["auth_mode"] = "contract_bearer"
    treatment["gateway"] = {"front_protocol": "openai_chat", "protocol_mode": "native"}
    return identity.IdentityInputs(
        protocol="app@1",
        kind="app_agentic",
        task={"structured_hash": task["structured_hash"], "variant": task["variant"], "prose_sha": task["prose_sha"]},
        target=None,
        treatment=treatment,
        stimuli=[("brief", identity.text_sha(BRIEF))],
        model={"slug": SLUG, "endpoint_kind": "openrouter", "pinned_digest": None},
        sampling_hash=samples.fake_sha("sampling"),
        policy_treatment_view={"run.max_llm_requests": 10},
        images={
            "harness": "sha256:" + samples.fake_sha("agent-fake"),
            "gateway": "sha256:" + samples.fake_sha("gw"),
            "starter_base": [],
        },
        seed=seed,
    )


@dataclass
class AgenticEnv:
    golden: Any
    user: Any
    model: Any
    script: FakeScript
    backend: LocalSandboxBackend
    gateway_overrides: dict[str, Any] = field(default_factory=dict)
    policy: EffectivePolicy = field(default_factory=effective_policy)

    def resolved_cell(self, run: GenerationRun, *, seed: int = 4242) -> tuple[ResolvedCell, identity.IdentityKeys]:
        version = run.task_version
        task = {
            "ref": version.pinned_ref,
            "structured_hash": version.structured_hash,
            "variant": "standard",
            "prose_sha": version.prose_hashes["standard"],
        }
        inputs = identity_inputs(task, seed=seed)
        keys = identity.compute_keys(inputs)
        body = samples.manifest_content(kind="app_agentic", task=task, model_slug=SLUG, identity=keys.as_dict())
        body.pop("created_at")
        body["sampling"]["seed"] = seed
        body["harness"] = {
            "name": "fake",
            "version": "1",
            "preset_ref": "fake-native@1#" + samples.fake_sha("fake-native")[:12],
            "image": "agent-fake",
            "image_digest": inputs.images["harness"],
            "command_sha": samples.fake_sha("command"),
            "facts_version": 1,
            "unsupported_knobs": list(FakeHarness.facts.unsupported_knobs),
        }
        body["gateway"]["image_digest"] = inputs.images["gateway"]
        # Library refs in a manifest are pinned (slug@v#hash12; agentic.manifest.unpinned_refs).
        body["sampling"]["ref"] = self.golden.pinned_ref("sampling", "t07")
        body["resources"]["ref"] = self.golden.pinned_ref("resource_policy", "frugal")
        body["planned_evaluation"]["eval_suite"]["ref"] = self.golden.pinned_ref("eval_suite", "eval-v2")
        starter = run.starter
        body["starter"] = {"ref": starter.pinned_ref, "tree_hash": starter.tree_hash, "base_images": []}
        body["factors"] = {
            "model": {"value": SLUG},
            "stack_profile": {"ref": run.stack_profile.pinned_ref, "content_hash": run.stack_profile.content_hash},
            "auth_mode": {"value": "contract_bearer"},
        }
        body["resources"]["effective"] = self.policy.model_dump(mode="json", by_alias=True)
        body["stimuli"] = [{"role": "brief", "template_ref": "brief-app-default@1", "sha": identity.text_sha(BRIEF)}]
        brief_blob = blobs.put_text(BRIEF)
        stimulus = StimulusRecord(
            role="brief",
            template_ref="brief-app-default@1",
            sha=identity.text_sha(BRIEF),
            blob=brief_blob,
            chars=len(BRIEF),
            est_tokens=len(BRIEF) // 4,
        )
        cell = ResolvedCell(
            cell_key=cell_key(0, 0, 0, "main"),
            condition_index=0,
            unit_index=0,
            repeat_index=0,
            block_index=0,
            sequence=0,
            stage="main",
            seed=seed,
            unit_key=f"task:{version.ref}",
            levels={},
            manifest=ManifestBody.model_validate(body),
            stimuli=[stimulus],
        )
        return cell, keys

    def make_run(self, *, claimed: bool = True, **fields: Any) -> GenerationRun:
        golden = self.golden
        values: dict[str, Any] = {
            "kind": choices.RunKind.APP_AGENTIC,
            "task_version": golden.get("task", "crud_todo_list"),
            "prose_variant": "standard",
            "stack_profile": golden.get("stack", "flask-react"),
            "starter": golden.get("starter", "flask-react-minimal"),
            "resource_policy": golden.get("resource_policy", "frugal"),
            "model": self.model,
            "created_by": self.user,
        }
        values.update(fields)
        run = GenerationRun.objects.create(**values)
        cell, _ = self.resolved_cell(run)
        build_manifest(run, cell)
        if claimed:
            now = timezone.now()
            GenerationRun.objects.filter(pk=run.pk).update(
                status=choices.RunStatus.RUNNING,
                phase=choices.RunPhase.RUNNING,
                lease_owner=EXECUTOR,
                lease_expires_at=now + timezone.timedelta(minutes=5),
                queued_at=now,
                started_at=now,
            )
        run.refresh_from_db()
        return run

    def claim(self, run: GenerationRun) -> GenerationRun:
        """Claim a requeued run again (what the scheduler does for attempt n+1)."""
        now = timezone.now()
        GenerationRun.objects.filter(pk=run.pk).update(
            status=choices.RunStatus.RUNNING,
            phase=choices.RunPhase.RUNNING,
            lease_owner=EXECUTOR,
            lease_expires_at=now + timezone.timedelta(minutes=5),
            started_at=now,
            run_after=None,
        )
        run.refresh_from_db()
        return run


@pytest.fixture
def agentic_env(db, monkeypatch) -> AgenticEnv:
    golden = install_golden_library()
    user = UserFactory.create()
    model = LLMModelFactory.create(model_id=SLUG)
    env = AgenticEnv(
        golden=golden,
        user=user,
        model=model,
        script=FakeScript(),
        backend=LocalSandboxBackend(),
    )

    def adapter(name: str) -> FakeHarness:
        if name != "fake":
            raise KeyError(name)
        return FakeHarness(env.script)

    def build(run: GenerationRun) -> Any:
        return gateway_config(run_id=str(run.pk), session_id=str(run.pk), attempt=run.attempt + 1,
                              **env.gateway_overrides)  # fmt: skip

    monkeypatch.setattr(runner, "get_adapter", adapter)
    monkeypatch.setattr("backend.generation.budget.gateway_config.build", build)
    return env
