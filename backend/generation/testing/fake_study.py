"""Fakes for the study engine's seams (``study/deps.py``), added by WP-10 for its own tests (spec 12.2).

The study engine consumes prompt rendering (WP-03), the effective policy and estimates (WP-11), the cell
environment (WP-07/WP-08), slot context (WP-12), the manifest builder (WP-08) and the pilot gates (WP-11).
:func:`fake_study_deps` binds deterministic stand-ins with the published signatures:

* stimuli are rendered from the levels (task prose, stack, style, auth mode, guidance, network policy); they
  never contain budget numbers, the seed or ids (Appendix A #2), so ``prompt_hash`` is policy-independent;
* the effective policy is the stored content, unclamped; ``treatment_view`` is the real
  ``contracts.resources.values_by_class(content, {"C", "R"})``;
* estimates are ``FakeStudyEnv.cost_per_cell`` per cell (``cap_usd`` = the run cap);
* environments use fixed fake digests (``sha256:<fake>``) and the native gateway mode;
* ``build_manifest`` writes a real ``RunManifest`` row from the resolved cell plus the study block;
* ``enqueue_runs`` records the ids (``FakeStudyEnv.enqueued``) and stamps ``queued_at`` like the executor API.

Use ``with use_fake_study_deps() as env:`` (or the ``fake_study`` fixture in ``tests/wp10_study``).
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from django.utils import timezone

from backend.generation import identity
from backend.generation.contracts.manifest import ManifestGateway
from backend.generation.contracts.manifest import ManifestHarness
from backend.generation.contracts.plan import CellEstimate
from backend.generation.contracts.plan import ConditionEstimate
from backend.generation.contracts.plan import Estimate
from backend.generation.contracts.plan import EstimateFit
from backend.generation.contracts.plan import ResolvedCell
from backend.generation.contracts.plan import ResolvedPlan
from backend.generation.contracts.resources import EffectivePolicy
from backend.generation.contracts.resources import ResourcePolicyContent
from backend.generation.contracts.resources import values_by_class
from backend.generation.contracts.slot import ResolvedContext
from backend.generation.prompts.engine import Stimulus
from backend.generation.study.deps import StudyDeps
from backend.generation.study.deps import default_deps
from backend.generation.study.deps import override_deps
from backend.generation.study.environment import CellEnvironment
from backend.generation.study.environment import EnvironmentRequest
from backend.generation.study.environment import allowed_bases_for

__all__ = ["FakeStudyEnv", "fake_digest", "fake_study_deps", "use_fake_study_deps"]


def fake_digest(label: str) -> str:
    return "sha256:" + identity.H({"fake_digest": label})


def _slug(row: Any) -> str:
    return getattr(row, "slug", None) or "-"


@dataclass
class FakeStudyEnv:
    """Knobs and recordings of the fake binding."""

    cost_per_cell: float = 0.01
    p90_factor: float = 2.2
    gates: list[dict[str, Any]] = field(
        default_factory=lambda: [{"code": "max_infra_error_rate", "ok": True, "value": 0.0, "threshold": 0.3}],
    )
    enqueued: list[list[str]] = field(default_factory=list)
    rendered: int = 0
    environments: int = 0
    lacking_tools: set[str] = field(default_factory=set)  # model ids treated as without tool calling
    coverage: dict[str, bool] | None = field(
        default_factory=lambda: dict.fromkeys(("flask", "fastapi", "django", "express"), True),
    )

    # -- seams -----------------------------------------------------------------------------------

    def render_stimuli(self, mode: str, levels: dict[str, Any], prompt_set_content: Any) -> list[Stimulus]:
        self.rendered += 1
        roles = dict((prompt_set_content or {}).get("roles") or {})
        if mode == "app":
            version = levels["task_version"]
            variant = levels["prose_variant"]
            prose = (version.content.get("prose") or {}).get(variant, "")
            lines = [
                f"# {version.content.get('title')}",
                prose.strip(),
                f"Stack: {_slug(levels.get('stack'))}",
                f"Starter level: {getattr(levels.get('starter'), 'level', '-')}",
                f"Style: {_slug(levels.get('style'))}",
                f"Auth: {levels.get('auth_mode')}",
                f"Guidance: {_slug(levels.get('guidance'))}",
                f"Network: {levels.get('network_policy')}",
            ]
            if levels.get("allowed_bases"):
                lines.append("Allowed bases: " + ", ".join(levels["allowed_bases"]))
            return [Stimulus(role="brief", template_ref=roles.get("brief", "brief@1"), text="\n".join(lines) + "\n")]
        target = levels["slot_target"]
        policy = levels["slot_policy"]
        system = f"Regenerate the {target.kind} {target.label} ({policy.hole_mode}).\n"
        user = f"Context: {levels['resolved_context'].sha}\nGuidance: {_slug(levels.get('guidance'))}\n"
        if levels["executor"] == "restricted_agent":
            return [Stimulus(role="slot_agent_brief", template_ref=roles.get("slot_agent_brief", "x@1"), text=system)]
        return [
            Stimulus(
                role="slot_single_shot_system",
                template_ref=roles.get("slot_single_shot_system", "s@1"),
                text=system,
            ),
            Stimulus(role="slot_single_shot_user", template_ref=roles.get("slot_single_shot_user", "u@1"), text=user),
        ]

    def effective_policy(self, policy: Any, **_: Any) -> EffectivePolicy:
        content = ResourcePolicyContent.model_validate(policy.content)
        tier = content.routing.serving_tier
        basis = content.study.budget_basis
        cap = content.study.cap_basis
        return EffectivePolicy(
            content=content,
            budget_basis="usd" if basis == "auto" else basis,
            cap_basis="billed" if cap == "auto" else cap,
            serving_tier="standard" if tier == "auto_batch" else tier,
        )

    def treatment_view(self, policy: EffectivePolicy) -> dict[str, Any]:
        return values_by_class(policy.content, {"C", "R"}, budget_basis=policy.budget_basis)

    def estimate_cell(self, cell: ResolvedCell) -> CellEstimate:
        cap = float(((cell.effective_policy or {}).get("content") or {}).get("run", {}).get("max_cost_usd", 1.0))
        expected = self.cost_per_cell
        return CellEstimate(
            expected_usd=expected,
            expected_list_usd=expected,
            p10_usd=round(expected * 0.4, 6),
            p90_usd=round(expected * self.p90_factor, 6),
            cap_usd=cap,
            expected_tokens=10_000,
            expected_wall_s=300.0,
            p90_wall_s=900.0,
            basis="heuristic",
        )

    def estimate_plan(self, plan: ResolvedPlan) -> Estimate:
        by_condition: dict[int, list[CellEstimate]] = {}
        for cell in plan.cells:
            if cell.estimate is not None:
                by_condition.setdefault(cell.condition_index, []).append(cell.estimate)
        rows = [
            ConditionEstimate(
                condition_index=index,
                n_cells=len(items),
                expected_usd=round(sum(e.expected_usd for e in items), 6),
                p90_usd=round(sum(e.p90_usd for e in items), 6),
                expected_list_usd=round(sum(e.expected_list_usd for e in items), 6),
            )
            for index, items in sorted(by_condition.items())
        ]
        expected = round(sum(r.expected_usd for r in rows), 6)
        return Estimate(
            expected_total=expected,
            p90_total=round(sum(r.p90_usd for r in rows), 6),
            worst_case_total=round(sum(e.cap_usd for items in by_condition.values() for e in items), 6),
            expected_list_total=expected,
            by_condition=rows,
            fit=EstimateFit(),
            method="heuristic",
        )

    def environment(self, request: EnvironmentRequest) -> CellEnvironment:
        self.environments += 1
        starter_base = [b["digest"] for b in ((request.starter.content or {}) if request.starter else {}).get(
            "base_images", [],
        )]  # fmt: skip
        allowed = allowed_bases_for(request.stack)
        if request.kind not in ("app_agentic", "slot_agent"):
            return CellEnvironment(
                harness=None,
                gateway=None,
                sandbox=None,
                images={"harness": None, "gateway": None, "starter_base": starter_base},
                gateway_treatment=None,
                allowed_bases=allowed,
                agentic=False,
            )
        preset = request.harness_preset
        harness = preset.harness if preset is not None else "opencode"
        harness_digest = fake_digest(f"agent-{harness}")
        gateway_digest = fake_digest("llm-gateway")
        excluded = "config_invalid" if request.model.model_id in self.lacking_tools else None
        issues = []
        if excluded:
            from backend.generation.contracts.issues import make_issue

            issues.append(make_issue("model_lacks_tools", request.study_kind, message="no tool calling"))
        return CellEnvironment(
            harness=ManifestHarness(
                name=harness,
                version=preset.harness_version if preset is not None else "1",
                preset_ref=preset.pinned_ref if preset is not None else "opencode-native@1",
                image=f"agent-{harness}",
                image_digest=harness_digest,
                command_sha=identity.text_sha(f"command:{harness}"),
                facts_version=1,
            ),
            gateway=ManifestGateway(
                image_digest=gateway_digest,
                front_protocol="openai_chat",
                protocol_mode="native",
                config_sha=identity.H({"gateway": request.policy.serving_tier}),
                serving_tier=request.policy.serving_tier,
            ),
            sandbox=request.sandbox,
            images={"harness": harness_digest, "gateway": gateway_digest, "starter_base": starter_base},
            gateway_treatment={"front_protocol": "openai_chat", "protocol_mode": "native"},
            allowed_bases=allowed,
            agentic=harness != "aider",
            controlled=bool((preset.content or {}).get("controlled")) if preset is not None else False,
            excluded=excluded,
            issues=issues,
        )

    def resolve_slot_context(self, target: Any, policy: Any, **_: Any) -> ResolvedContext:
        return ResolvedContext(
            files=[],
            sha=identity.H({"target": str(target.pk), "policy": policy.policy_hash()}),
            tokens=0,
        )

    def slot_issues(self, target: Any, *, study_kind: str, **_: Any) -> list[Any]:
        """Validation and leak issues from the target's stored records (the real scan needs the parent blobs)."""
        from backend.generation.contracts.issues import make_issue
        from backend.generation.contracts.slot import LeakReport
        from backend.generation.contracts.slot import TargetValidation

        validation = TargetValidation.model_validate(target.validation or {})
        issues = []
        if validation.status in ("pending", "error"):
            issues.append(
                make_issue(
                    "slot_tests_uninformative",
                    study_kind,  # type: ignore[arg-type]
                    severity="error",
                    message=f"{target.label}: target validation is {validation.status}",
                ),
            )
        elif validation.status == "uninformative" or not validation.discriminating_checks:
            issues.append(make_issue("slot_tests_uninformative", study_kind, message=f"{target.label}: uninformative"))  # type: ignore[arg-type]
        leak = LeakReport.model_validate(target.leak) if target.leak else None
        if leak is not None and any(match.verbatim for match in leak.matches):
            issues.append(make_issue("slot_leak_verbatim", study_kind, message=f"{target.label}: verbatim leak"))  # type: ignore[arg-type]
        elif leak is not None and leak.score > 0.3:
            issues.append(make_issue("slot_leak_high", study_kind, message=f"{target.label}: high leak"))  # type: ignore[arg-type]
        return issues

    def build_manifest(self, run: Any, cell: ResolvedCell) -> Any:
        from backend.generation.models import RunManifest

        study_block = {
            "id": str(run.study_id),
            "plan_hash": run.cell.plan.plan_hash,
            "condition_hash": run.condition.condition_hash,
            "cell": [cell.condition_index, cell.unit_index, cell.repeat_index],
            "block_index": cell.block_index,
            "stage": cell.stage,
            "cell_attempt": run.cell_attempt or 1,
            "beyond_plan": run.beyond_plan,
        }
        manifest = {
            **cell.manifest.model_dump(mode="json"),
            "study": study_block,
            "created_at": timezone.now().isoformat(),
        }
        return RunManifest.objects.create(
            run=run,
            manifest=manifest,
            stimuli=[record.model_dump(mode="json") for record in cell.stimuli],
        )

    def pilot_gates(self, study: Any) -> list[dict[str, Any]]:
        return [dict(gate) for gate in self.gates]

    def enqueue_runs(self, ids: list[Any]) -> int:
        from backend.generation.models import GenerationRun

        self.enqueued.append([str(i) for i in ids])
        return GenerationRun.objects.filter(pk__in=ids, queued_at__isnull=True).update(queued_at=timezone.now())

    def conformance_coverage(self) -> dict[str, bool] | None:
        return None if self.coverage is None else dict(self.coverage)

    def deps(self) -> StudyDeps:
        base = default_deps()
        return StudyDeps(
            render_stimuli=self.render_stimuli,
            effective_policy=self.effective_policy,
            treatment_view=self.treatment_view,
            estimate_cell=self.estimate_cell,
            estimate_plan=self.estimate_plan,
            environment=self.environment,
            resolve_slot_context=self.resolve_slot_context,
            slot_issues=self.slot_issues,
            build_manifest=self.build_manifest,
            pilot_gates=self.pilot_gates,
            enqueue_runs=self.enqueue_runs,
            cancel_runs=base.cancel_runs,
            conformance_coverage=self.conformance_coverage,
        )


def fake_study_deps(env: FakeStudyEnv | None = None) -> StudyDeps:
    """A :class:`StudyDeps` bound to a :class:`FakeStudyEnv` (a fresh one by default)."""
    return (env or FakeStudyEnv()).deps()


@contextlib.contextmanager
def use_fake_study_deps(env: FakeStudyEnv | None = None, **replace: Any) -> Iterator[FakeStudyEnv]:
    """Bind the fakes for the ``with`` block; ``replace`` overrides single seams."""
    from backend.generation.study.plan import clear_cache

    env = env or FakeStudyEnv()
    clear_cache()
    with override_deps(env.deps(), **replace):
        yield env
    clear_cache()
