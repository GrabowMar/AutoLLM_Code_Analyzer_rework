"""The generation stage of agentic runs: ``app_agentic`` and ``slot_agent`` (spec sections 6.3 to 6.9, 6.12).

One call of :func:`run_agentic` is one attempt:

1. **resolve**: the frozen ``RunManifest`` (never "latest" anything), the harness adapter (refused when not
   enabled: ``excluded:harness_not_enabled``), the effective policy and the rendered stimuli;
2. **images** (``agentic/images.py``): build or pull and compare digests; a mismatch is
   ``infra_error:image_digest_mismatch`` and the run never proceeds on another environment;
3. **workspace**: the seed (starter, or the hole-applied parent tree) and its tree hash;
4. **gateway**: ``budget.gateway_config.build(run)`` (WP-11) and the gateway credentials;
5. **sandbox**: the adapter renders files/env/command (the brief never in argv), the backend runs the entry
   script under the supervisor (idle detection, live spend, cancellation, OOM);
6. **record**: per-call ``GenerationArtifact`` rows, the trajectory and observed inputs, the termination
   (``adapter.classify_termination``: wall clock by elapsed time, never by exit code), ``budget_exhausted``
   when the gateway said so **or** ``main``-class requests reached ``run.max_llm_requests``, served
   providers/models, usage, the time split, ``RunAttempt``, the collected ``RunFile`` rows, ``result``,
   ``metrics`` and ``validity``; then the ledger settles the attempt;
7. **decide** (``agentic/outcome.py``): evaluate (``finish_stage(OUTCOME_EVALUATE)``, the eval lane decides
   the final outcome), end now (``output_invalid``, ``excluded``), retry infrastructure (``infra_retry``:
   same row, same manifest and seed, ``attempt + 1``) or cancel.

Infrastructure failures before the harness ran (daemon, image build, gateway start, credentials) are
retried as infrastructure and never become model outcomes. ``LeaseLost`` propagates to the worker.
"""

from __future__ import annotations

import dataclasses
import logging
import shutil
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from backend.generation import blobs
from backend.generation.agentic import evidence
from backend.generation.agentic import outcome as outcomes
from backend.generation.agentic import sandbox as sandbox_module
from backend.generation.agentic import workspace as workspace_module
from backend.generation.agentic.collector import Collected
from backend.generation.agentic.collector import collect
from backend.generation.agentic.events import LiveUsage
from backend.generation.agentic.events import StepRecorder
from backend.generation.agentic.events import TrajectoryStream
from backend.generation.agentic.images import ImageBuildError
from backend.generation.agentic.images import ImageDigestMismatch
from backend.generation.agentic.outcome import Decision
from backend.generation.agentic.outcome import GenerationDecision
from backend.generation.agentic.validity import build_validity
from backend.generation.agentic.validity import call_ok
from backend.generation.agentic.validity import note
from backend.generation.contracts.harness import HarnessContext
from backend.generation.contracts.harness import ObservedInputs
from backend.generation.contracts.harness import Trajectory
from backend.generation.contracts.library import HarnessPresetContent
from backend.generation.contracts.outcome import AttemptServed
from backend.generation.contracts.outcome import AttemptUsage
from backend.generation.contracts.outcome import ExclusionReason
from backend.generation.contracts.outcome import Note
from backend.generation.contracts.outcome import Outcome
from backend.generation.contracts.outcome import RunMetrics
from backend.generation.contracts.outcome import RunResult
from backend.generation.contracts.outcome import Termination
from backend.generation.contracts.outcome import note_code_valid
from backend.generation.contracts.resources import EffectivePolicy
from backend.generation.services import token_metrics

if TYPE_CHECKING:
    from backend.generation.agentic.sandbox import SandboxBackend
    from backend.generation.agentic.sandbox import SandboxResult
    from backend.generation.contracts.harness import SlotAgentContext
    from backend.generation.harnesses.base import HarnessAdapter
    from backend.generation.models import GenerationRun

logger = logging.getLogger(__name__)

__all__ = [
    "AttemptRecord",
    "ConfigError",
    "InfraError",
    "attempt_number",
    "attempt_served",
    "attempt_usage",
    "budget_exhausted_kind",
    "call_telemetry",
    "default_gateway_wiring",
    "effective_policy",
    "get_adapter",
    "record_attempt",
    "run_agentic",
    "run_app_agentic",
]

BRIEF_ROLES: Final = {"app_agentic": "brief", "slot_agent": "slot_agent_brief"}
SYSTEM_ROLE: Final = "system"
REASON_GATEWAY_CONFIG_INVALID: Final = "gateway_config_invalid"
MAIN: Final = "main"
_LIST_LIMIT: Final = 4000


class InfraError(RuntimeError):
    """A platform failure before or around the harness run: retried as infrastructure."""

    def __init__(self, message: str, *, reason: str = "infra", retryable: bool = True) -> None:
        super().__init__(message)
        self.reason = reason
        self.retryable = retryable


class ConfigError(RuntimeError):
    """The frozen configuration cannot run (``excluded`` with an ``ExclusionReason``; never retried)."""

    def __init__(self, message: str, *, reason: str = ExclusionReason.CONFIG_INVALID.value) -> None:
        super().__init__(message)
        self.reason = reason


# =====================================================================================
# Small pure helpers
# =====================================================================================


def attempt_number(run: GenerationRun) -> int:
    """The 1-based number of the run's current attempt (``RunAttempt.attempt``, ``a<n>/`` evidence, artifacts).

    ``GenerationRun.attempt`` is the executor's 0-based requeue counter (0 on the first claim, +1 per infra
    requeue or manual retry), so attempt ``n`` runs while ``GenerationRun.attempt == n - 1``.
    """
    return int(run.attempt or 0) + 1


def get_adapter(name: str) -> HarnessAdapter:
    """The registered harness adapter (WP-07's registry; tests patch this function)."""
    from backend.generation.harnesses import registry

    return registry.get(name)


def budget_exhausted_kind(
    *,
    state: Mapping[str, Any] | None,
    events: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    max_llm_requests: int | None,
) -> str | None:
    """The budget kind that ended the run, or ``None`` (section 6.9).

    The gateway's word (``state.json`` or a ``budget_exhausted`` event) wins; otherwise ``main``-class
    successful requests reaching ``run.max_llm_requests`` count as ``requests`` (the v2 "100/100 is not
    exhausted" bug).
    """
    reported = (state or {}).get("budget_exhausted")
    if not reported:
        for event in events:
            if str(event.get("event") or "").startswith("budget_exhausted"):
                reported = event.get("kind") or event.get("reason") or event.get("event")
                break
    if reported:
        kind = str(reported).removeprefix("budget_exhausted:").removeprefix("budget_exhausted")
        return kind.strip(":") or "requests"
    if max_llm_requests:
        main = sum(1 for call in calls if call_ok(call) and (call.get("request_class") or MAIN) == MAIN)
        if main >= max_llm_requests:
            return "requests"
    return None


def attempt_usage(calls: list[dict[str, Any]]) -> AttemptUsage:
    """Totals over every gateway call of one attempt (failed calls included: a failed call may be billed).

    Two keys are derived, not summed (definitions in ``services.token_metrics``): ``tokens_per_s_p50`` is the
    median end-to-end output rate of the attempt's clean calls (``tokens_per_s_calls`` of them), never the
    gateway's own ``tokens_per_s``; ``reasoning_tokens`` is ``None`` unless every call that produced output
    reported a reasoning-token count (an unreported count is not 0).
    """
    requests: Counter[str] = Counter()
    totals = Counter[str]()
    cost = list_cost = 0.0
    dropped: Counter[str] = Counter()
    rates: list[float] = []
    reasoning: list[tuple[Any, Any]] = []
    for call in calls:
        usage = call.get("usage") or {}
        if call_ok(call):
            klass = str(call.get("request_class") or MAIN)
            requests[klass if klass in {"main", "subagent", "compaction", "auxiliary"} else "auxiliary"] += 1
        for key in ("prompt_tokens", "completion_tokens", "cached_tokens", "cache_write_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                totals[key] += value
        if usage:
            reasoning.append((usage.get("completion_tokens"), usage.get("reasoning_tokens")))
        if isinstance(usage.get("cost"), (int, float)):
            cost += float(usage["cost"])
        if isinstance(call.get("list_cost"), (int, float)):
            list_cost += float(call["list_cost"])
        for field, count in (call.get("translation_dropped") or {}).items():
            if isinstance(count, int):
                dropped[str(field)] += count
        rate = call_telemetry(call).get("tokens_per_s")
        if rate is not None:
            rates.append(rate)
    truncations = sum(1 for call in calls if call_ok(call) and call.get("finish_reason") == "length")
    return AttemptUsage(
        requests_by_class=dict(requests),  # type: ignore[arg-type]
        prompt_tokens=totals["prompt_tokens"],
        uncached_prompt_tokens=max(0, totals["prompt_tokens"] - totals["cached_tokens"]),
        completion_tokens=totals["completion_tokens"],
        reasoning_tokens=token_metrics.reasoning_total(reasoning),
        cached_tokens=totals["cached_tokens"],
        cache_write_tokens=totals["cache_write_tokens"],
        cost_usd=round(cost, 10),
        list_cost_usd=round(list_cost, 10),
        call_truncations=truncations,
        translation_dropped=dict(dropped),
        tokens_per_s_p50=token_metrics.median_rate(rates),
        tokens_per_s_calls=len(rates),
    )


def attempt_served(calls: list[dict[str, Any]]) -> AttemptServed:
    """Who actually served the attempt's successful calls (providers, served models, response models)."""
    ok = [call for call in calls if call_ok(call)]
    providers = Counter(str(call.get("served_provider") or "unknown") for call in ok)
    models = Counter(str(call.get("served_model") or call.get("upstream_model") or "unknown") for call in ok)
    responses = Counter(str(call["response_model"]) for call in ok if call.get("response_model"))
    fingerprints = sorted({str(call["system_fingerprint"]) for call in ok if call.get("system_fingerprint")})
    routes = Counter(str(call.get("path") or "") for call in calls if call.get("path"))
    return AttemptServed(
        providers=dict(providers),
        models=dict(models),
        response_models=dict(responses),
        system_fingerprints=fingerprints,
        routes=dict(routes),
    )


def _iso(epoch: Any) -> str | None:
    from backend.generation.services.call_telemetry import iso

    return iso(epoch) if isinstance(epoch, (int, float)) else None


def call_telemetry(call: Mapping[str, Any]) -> dict[str, Any]:
    """The normalized per-call telemetry of ``GenerationArtifact.telemetry`` (keys absent = unknown).

    ``tokens_per_s`` is the end-to-end output rate of ``services.token_metrics`` (basis ``e2e``), derived from
    the keys stored next to it and absent for a call that has none; the gateway's own ``tokens_per_s`` is not
    copied. The gateway's ``ttft_ms`` is the time to the first upstream byte of any kind (a keep-alive on the
    proxied routes), so it is stored as ``first_byte_ms`` and not as a time to first token.
    """
    from backend.generation.services.call_telemetry import SCHEMA

    usage = call.get("usage") or {}
    out: dict[str, Any] = {"schema": SCHEMA, "source": "gateway"}

    def put(key: str, value: Any) -> None:
        if value is not None and value != "":
            out[key] = value

    put("started_at", _iso(call.get("started")))
    put("ended_at", _iso(call.get("ended")))
    put("duration_s", call.get("duration_s"))
    put("first_byte_ms", call.get("ttft_ms"))
    out.update(token_metrics.rate_facts(call))
    put("status", call.get("status"))
    put("provider", call.get("served_provider"))
    put("served_model", call.get("served_model") or call.get("upstream_model"))
    put("response_model", call.get("response_model"))
    put("finish_reason", call.get("finish_reason"))
    put("system_fingerprint", call.get("system_fingerprint"))
    put("request_class", call.get("request_class"))
    put("front_protocol", call.get("front_protocol"))
    put("protocol_mode", call.get("protocol_mode"))
    put("serving_tier", call.get("serving_tier"))
    if usage:
        out["tokens"] = {
            "prompt": usage.get("prompt_tokens"),
            "completion": usage.get("completion_tokens"),
            "reasoning": usage.get("reasoning_tokens"),
            "cached": usage.get("cached_tokens"),
            "cache_write": usage.get("cache_write_tokens"),
        }
        if isinstance(usage.get("cost"), (int, float)):
            out["cost"] = usage["cost"]
    put("list_cost", call.get("list_cost") if call_ok(call) else None)
    if call.get("translation_dropped"):
        out["translation_dropped"] = call["translation_dropped"]
    if call.get("error") or not call_ok(call):
        out["error"] = {"status": call.get("status"), "message": str(call.get("error") or "")[:500]}
    rate = token_metrics.telemetry_tokens_per_s(out)
    if rate is not None:
        out["tokens_per_s"] = rate
        out["tokens_per_s_basis"] = token_metrics.RATE_BASIS
    return out


def _truncate(value: Any, limit: int = _LIST_LIMIT) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"... [{len(value) - limit} chars truncated]"
    return value


def _artifact_rows(run: GenerationRun, attempt: int, calls: list[dict[str, Any]]) -> list[Any]:
    from backend.generation.models import GenerationArtifact

    rows = []
    for position, call in enumerate(calls, start=1):
        request = call.get("request") or {}
        usage = call.get("usage") or {}
        index = call.get("index") if isinstance(call.get("index"), int) else position
        rows.append(
            GenerationArtifact(
                run=run,
                attempt=attempt,
                stage=f"agent_call_{int(index):04d}",
                request_payload={
                    "model": request.get("model"),
                    "params": {k: v for k, v in request.items() if k not in ("messages", "tools", "input", "system")},
                    "message_count": len(request.get("messages") or []),
                    "tool_count": len(request.get("tools") or []),
                    "request_class": call.get("request_class"),
                    "native_request_sha": call.get("native_request_sha"),
                },
                response_payload={
                    "status": call.get("status"),
                    "duration_s": call.get("duration_s"),
                    "id": call.get("response_id") or call.get("id"),
                    "provider": call.get("served_provider"),
                    "model": call.get("response_model"),
                    "finish_reason": call.get("finish_reason"),
                    "usage": usage,
                    "error": _truncate(call.get("error")),
                },
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                total_cost=float(usage.get("cost") or 0.0),
                telemetry=call_telemetry(call),
            ),
        )
    return rows


# =====================================================================================
# Resolution
# =====================================================================================


def _manifest(run: GenerationRun) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from backend.generation.models import RunManifest

    row = RunManifest.objects.filter(run_id=run.pk).first()
    if row is None:
        msg = f"run {run.pk} has no RunManifest (runs are frozen at launch)"
        raise ConfigError(msg)
    return dict(row.manifest), list(row.stimuli or [])


def _stimulus_text(stimuli: list[dict[str, Any]], role: str) -> str | None:
    for record in stimuli:
        if record.get("role") == role and record.get("blob"):
            return blobs.get_text(record["blob"])
    return None


def effective_policy(run: GenerationRun, manifest: Mapping[str, Any]) -> EffectivePolicy:
    """The attempt's ``EffectivePolicy`` from the manifest (frozen), else the run's ResourcePolicy content."""
    resources = manifest.get("resources") or {}
    effective = resources.get("effective") or {}
    gateway = manifest.get("gateway") or {}
    serving = gateway.get("serving_tier") or "standard"
    if isinstance(effective, Mapping) and isinstance(effective.get("content"), Mapping):
        return EffectivePolicy.model_validate(effective)
    content: Any = effective if isinstance(effective, Mapping) and "run" in effective else None
    if content is None and run.resource_policy is not None:
        content = run.resource_policy.content
    if content is None:
        msg = "the manifest records no effective resource policy"
        raise ConfigError(msg)
    return EffectivePolicy.model_validate(
        {
            "content": content,
            "budget_basis": resources.get("budget_basis") or "usd",
            "cap_basis": "billed",
            "serving_tier": serving if serving in {"standard", "flex", "batch"} else "standard",
        },
    )


def _preset(run: GenerationRun, manifest: Mapping[str, Any]) -> HarnessPresetContent:
    harness = manifest.get("harness") or {}
    if run.harness_preset is not None:
        return HarnessPresetContent.model_validate(run.harness_preset.content)
    return HarnessPresetContent(
        harness=str(harness.get("name")),
        harness_version=str(harness.get("version")),
        options=dict(harness.get("options") or {}),
    )


@dataclasses.dataclass
class AttemptRecord:
    """What :func:`record_attempt` produced (also returned for tests)."""

    decision: GenerationDecision
    termination: Termination
    collected: Collected | None
    attempt_row: Any


# =====================================================================================
# The stage
# =====================================================================================


def run_app_agentic(run: GenerationRun, *, backend: SandboxBackend | None = None) -> None:
    """Seed the workspace, run the harness in the sandbox, collect and record one attempt (``app@1``)."""
    run_agentic(run, backend=backend)


def run_agentic(
    run: GenerationRun,
    *,
    backend: SandboxBackend | None = None,
    slot: SlotAgentContext | None = None,
) -> AttemptRecord | None:
    """One attempt of an agentic run, ended through ``execution.transitions`` (section 6)."""
    from backend.execution import transitions

    steps = StepRecorder(run.pk)
    started_at = timezone.now()
    try:
        steps.start("resolve")
        manifest, stimuli = _manifest(run)
        if _cancelled(run):
            steps.end("resolve", "skipped", "cancelled")
            transitions.finish_stage(run, transitions.OUTCOME_CANCELLED)
            return None
        harness = manifest.get("harness") or {}
        try:
            adapter = get_adapter(str(harness.get("name")))
        except KeyError as exc:
            raise ConfigError(str(exc), reason=outcomes.REASON_HARNESS_NOT_ENABLED) from exc
        if not adapter.facts.enabled:
            msg = f"harness {adapter.name} is not enabled: {', '.join(adapter.facts.open_questions)}"
            raise ConfigError(msg, reason=outcomes.REASON_HARNESS_NOT_ENABLED)
        policy = effective_policy(run, manifest)
        brief = _stimulus_text(stimuli, BRIEF_ROLES.get(run.kind, "brief"))
        if brief is None:
            msg = f"the manifest stimuli carry no {BRIEF_ROLES.get(run.kind, 'brief')!r} text"
            raise ConfigError(msg)
        system_prompt = _stimulus_text(stimuli, SYSTEM_ROLE)
        steps.end("resolve")

        backend = backend or sandbox_module.get_backend()
        steps.start("images")
        wiring = _gateway_wiring(backend, run, manifest)
        try:
            images = backend.ensure_images(
                manifest,
                adapter.image_spec(str(harness.get("version"))),
                local_gateway=bool(wiring.get("local")),
                run_id=str(run.pk),
            )
        except ImageDigestMismatch as exc:
            steps.end("images", "failed", str(exc))
            return _end_infra(run, steps, outcomes.REASON_IMAGE_DIGEST_MISMATCH, str(exc), retryable=False)
        except (ImageBuildError, sandbox_module.SandboxError) as exc:
            raise InfraError(str(exc)) from exc
        _record_images(run, images.as_record())
        steps.end("images")

        steps.start("workspace")
        include_tests = bool((manifest.get("slot") or {}).get("include_tests"))
        seed = workspace_module.seed_files(run, include_tests=include_tests)
        seed_hash = workspace_module.seed_tree_hash(seed)
        steps.end("workspace")

        steps.start("gateway")
        config = _gateway_config(run)
        steps.end("gateway")

        presented = list(config.model.presented_names or [])
        model_name = presented[0] if presented else str((manifest.get("model") or {}).get("slug") or "")
        ctx = HarnessContext(
            run_id=str(run.pk),
            brief=brief,
            system_prompt=system_prompt,
            model_name=model_name,
            gateway=adapter.gateway_wiring(f"{sandbox_module.GATEWAY_ALIAS}:{sandbox_module.GATEWAY_PORT}"),
            preset=_preset(run, manifest),
            policy=policy,
            slot=slot,
            # The model's context length as frozen into the gateway config (model facts at
            # manifest time); harness context knobs fall back to it (WP-07 ask).
            model_context_window=config.model.context_length,
        )
        rendered = adapter.render(ctx)
        if slot is not None and adapter.capabilities.supports_path_restriction:
            rendered = adapter.restrict_paths(ctx) or rendered
        deps = dependency_config(policy)
        gateway_env = dict(wiring.get("env") or {})
        gateway_env["GATEWAY_PING_INTERVAL_S"] = str(getattr(settings, "LLM_GATEWAY_PING_INTERVAL_S", 10.0))
        extra_notes: list[Note] = []
        if deps is not None:
            rendered = dataclasses.replace(
                rendered,
                env={**deps.env, **rendered.env},
                files={**deps.files, **rendered.files},
            )
            if deps.proxy_hosts:
                gateway_env["GATEWAY_PROXY_HOSTS"] = ",".join(deps.proxy_hosts)
            extra_notes = [note(code) for code in deps.notes if note_code_valid(code)]
        content = policy.content
        sandbox_cfg = manifest.get("sandbox") or {}
        plan = sandbox_module.SandboxPlan(
            run_id=str(run.pk),
            attempt=attempt_number(run),
            rendered=rendered,
            seed=seed,
            wall_clock_s=int(content.time.wall_clock_s),
            idle_timeout_s=content.time.idle_timeout_s,
            max_workspace_bytes=int(content.run.max_workspace_bytes),
            memory=str(sandbox_cfg.get("memory") or content.sandbox.memory),
            nano_cpus=int(sandbox_cfg.get("nano_cpus") or content.sandbox.nano_cpus),
            pids_limit=int(sandbox_cfg.get("pids_limit") or 512),
            network_policy=str(manifest.get("network_policy") or content.network_policy),
            gateway_config=config,
            images=images,
            gateway_env=gateway_env,
            gateway_network=str(wiring.get("network") or "bridge"),
            local_gateway=bool(wiring.get("local")),
        )
        hooks = sandbox_module.SandboxHooks(
            heartbeat=_ownership_check(run),
            on_state=LiveUsage(run, plan.attempt),
            on_stdout_line=TrajectoryStream(run.pk, plan.attempt, live_parser(adapter)),
        )
        transitions.guarded_save(run, ["updated_at"])  # still ours before the long part
        steps.start("sandbox")
        try:
            result = backend.run(plan, hooks)
        except sandbox_module.GatewayConfigInvalid as exc:
            raise InfraError(str(exc), reason=REASON_GATEWAY_CONFIG_INVALID, retryable=False) from exc
        except sandbox_module.SandboxError as exc:
            raise InfraError(str(exc)) from exc
        steps.end("sandbox")
    except ConfigError as exc:
        steps.fail_open(str(exc))
        run.outcome, run.outcome_reason = Outcome.EXCLUDED, exc.reason
        run.error_message = str(exc)[:2000]
        run.result = RunResult(error=str(exc)[:2000], steps=steps.steps).model_dump(mode="json")
        transitions.finish_stage(run, transitions.OUTCOME_DONE)
        return None
    except InfraError as exc:
        steps.fail_open(str(exc))
        return _end_infra(run, steps, exc.reason, str(exc), retryable=exc.retryable)
    except transitions.LeaseLost:
        raise
    except NotImplementedError:
        steps.fail_open("a dependency of the agentic stage is not implemented yet")
        raise

    try:
        if _cancelled(run):
            result = dataclasses.replace(result, exit_info=dataclasses.replace(result.exit_info, cancelled=True))
        elif result.exit_info.cancelled:
            # The heartbeat lost the run to something other than a cancel (requeue, reclaim): not ours to record.
            msg = f"run {run.pk} attempt {run.attempt} was taken from this worker during its sandbox run"
            raise transitions.LeaseLost(msg)
        record = record_attempt(
            run,
            adapter=adapter,
            manifest=manifest,
            seed=seed,
            seed_tree_hash=seed_hash,
            result=result,
            policy=policy,
            steps=steps,
            started_at=started_at,
            static_checker=getattr(backend, "frontend_static_check", None),
            extra_notes=extra_notes,
        )
    finally:
        result.cleanup()
    _finish(run, record, steps)
    return record


def live_parser(adapter: HarnessAdapter) -> Any:
    """The adapter's incremental ``parse_line`` (WP-07 ``adapter.parser()``), or ``None`` (step counts only)."""
    factory = getattr(adapter, "parser", None)
    if callable(factory):
        try:
            return factory().parse_line
        except Exception:  # noqa: BLE001 - no live parser for this harness
            logger.debug("no incremental parser for %s", adapter.name, exc_info=True)
    return getattr(adapter, "parse_line", None)


def dependency_config(policy: EffectivePolicy) -> Any:
    """The dependency-cache settings of the agent container (``build.depcache.sandbox_config``, WP-05)."""
    try:
        from backend.generation.build import depcache
    except ImportError:  # pragma: no cover - WP-05 ships the module
        return None
    content = policy.content
    try:
        return depcache.sandbox_config(
            dependency_cache=content.caches.dependency_cache,
            network_policy=content.network_policy,
        )
    except NotImplementedError:
        return None


def _cancelled(run: GenerationRun) -> bool:
    from backend.generation.models import GenerationRun
    from backend.generation.models.choices import RunStatus

    status = GenerationRun.objects.filter(pk=run.pk).values_list("status", flat=True).first()
    return status == RunStatus.CANCELLED


def _ownership_check(run: GenerationRun) -> Any:
    from backend.generation.models import GenerationRun
    from backend.generation.models.choices import RunStatus

    def still_ours() -> bool:
        return GenerationRun.objects.filter(
            pk=run.pk,
            status=RunStatus.RUNNING,
            attempt=run.attempt,
            lease_owner=run.lease_owner or "",
        ).exists()

    return still_ours


def _gateway_wiring(backend: SandboxBackend, run: GenerationRun, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """``{"env", "network", "local"}`` of the gateway sidecar; backends may provide their own resolver."""
    resolver = getattr(backend, "gateway_wiring", None)
    if callable(resolver):
        return dict(resolver(run, manifest))
    return default_gateway_wiring(run, manifest)


def default_gateway_wiring(run: GenerationRun, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Credentials and egress of the gateway (``inference.gateway_wiring.for_job``); raises Config/InfraError."""
    from backend.inference.access import LLMAccessError
    from backend.inference.gateway_wiring import for_job
    from backend.inference.proxy_server import frozen_endpoint

    try:
        wiring = for_job(run, {"endpoint": frozen_endpoint(dict(manifest), run.model)})
    except LLMAccessError as exc:
        if exc.retryable:
            raise InfraError(str(exc), reason="infra") from exc
        message = f"{exc.code}: {exc}"
        raise ConfigError(message, reason=ExclusionReason.CONFIG_INVALID.value) from exc
    env = dict(wiring.extra_env)
    if wiring.api_key:
        env["OPENROUTER_API_KEY"] = wiring.api_key
    return {"env": env, "network": wiring.network, "local": wiring.image_local}


def _gateway_config(run: GenerationRun) -> Any:
    from backend.generation.budget import gateway_config

    return gateway_config.build(run)


def _record_images(run: GenerationRun, record: dict[str, Any]) -> None:
    from backend.execution.transitions import jsonb_merge
    from backend.generation.models import GenerationRun

    GenerationRun.objects.filter(pk=run.pk, attempt=run.attempt).update(execution=jsonb_merge({"images": record}))
    execution = dict(run.execution or {})
    execution["images"] = record
    run.execution = execution


def _end_infra(run: GenerationRun, steps: StepRecorder, reason: str, error: str, *, retryable: bool) -> None:
    """Retry an infrastructure failure, or end the run ``infra_error`` right away (``retryable=False``)."""
    from backend.execution import transitions

    run.error_message = error[:2000]
    if retryable:
        transitions.infra_retry(run, reason, error)
        return
    run.outcome, run.outcome_reason = Outcome.INFRA_ERROR, reason[:64]
    run.result = RunResult(error=error[:2000], steps=steps.steps).model_dump(mode="json")
    transitions.finish_stage(run, transitions.OUTCOME_FAILED)


# =====================================================================================
# Record (section 6.9)
# =====================================================================================


def _store_evidence(run: GenerationRun, attempt: int, result: SandboxResult, extra: dict[str, bytes]) -> Path:
    directory = evidence.attempt_dir(run.pk, attempt)
    if directory.exists():
        shutil.rmtree(directory)
    files = {**result.outputs(), **extra}
    root = evidence.write_files(run.pk, attempt, files)
    if result.workspace_tar is not None and result.workspace_tar.exists():
        evidence.copy_file(run.pk, attempt, result.workspace_tar, "workspace.tar")
    return root


def _parse_trajectory(adapter: HarnessAdapter, outputs: dict[str, bytes], calls: list[dict[str, Any]]) -> Trajectory:
    try:
        return adapter.parse_trajectory(outputs, calls)
    except Exception:
        logger.exception("trajectory parser of %s failed", adapter.name)
        return Trajectory(parser=adapter.name, parser_version=0, unparsed_lines=1)


def _observed(adapter: HarnessAdapter, calls: list[dict[str, Any]]) -> ObservedInputs | None:
    try:
        observed = adapter.observed_inputs(calls)
    except Exception:
        logger.exception("observed_inputs of %s failed", adapter.name)
        return None
    text: str | None = None
    try:
        from backend.generation.harnesses.trajectory import common

        text = common.native_system_prompt_text(calls)
    except (ImportError, AttributeError):
        first = next(
            (c for c in calls if (c.get("request_class") or MAIN) == MAIN and c.get("native_system_prompt")),
            None,
        )
        text = str(first["native_system_prompt"]) if first is not None else None
    if text:
        # Adapters compute the sha purely (WP-07); the runner stores the text so the blob exists.
        sha = blobs.put_text(text)
        if observed.native_system_prompt_blob is None:
            observed = observed.model_copy(update={"native_system_prompt_blob": sha})
    return observed


def _prior_crash(run: GenerationRun) -> bool:
    current = attempt_number(run)
    return run.attempts.filter(termination=Termination.HARNESS_CRASH).exclude(attempt=current).exists()


def _can_retry(run: GenerationRun) -> bool:
    from backend.execution.transitions import infra_retries_for

    lost = int((run.execution or {}).get("executor_lost_count") or 0)
    return int(run.attempt or 0) - lost < infra_retries_for(run.pk)


_EVALUATED_GENERATION: Final = frozenset({"evaluate", "evaluate_partial", "retry_once_then_evaluate"})


# Evaluated rows that a zero-successful-call run keeps: BUDGET is the treatment's own cap (a call refused by
# the gateway's budget is never logged as a failed upstream call), not an infra reason.
_ZERO_CALL_EXEMPT: Final = frozenset({Termination.BUDGET})


def infra_override(termination: Termination, *, calls: list[dict[str, Any]], result: SandboxResult) -> Termination:
    """Force an evaluated termination to ``INFRA`` / ``LLM_ERROR`` when the attempt is not model data (6.12).

    * The workspace evidence is missing (no ``workspace.tar`` and not truncated): a platform failure that the
      backends normally raise as ``SandboxError``; recorded as ``INFRA`` so it is retried, never analysed.
    * No gateway call succeeded ("zero successful calls for an infra reason"): ``LLM_ERROR`` when the failed
      calls carry upstream HTTP statuses (``no_provider``, provider errors after the gateway's retries),
      ``INFRA`` otherwise (transport failures only, or the harness never called the gateway at all).
    """
    from backend.generation.contracts.outcome import TERMINATION_OUTCOME

    if TERMINATION_OUTCOME[termination].generation not in _EVALUATED_GENERATION:
        return termination
    if result.workspace_tar is None and not result.workspace_truncated:
        return Termination.INFRA
    if termination in _ZERO_CALL_EXEMPT or any(call_ok(c) for c in calls):
        return termination
    upstream = [
        c
        for c in calls
        if not c.get("transport_error") and isinstance(c.get("status"), int) and int(c["status"]) >= 400
    ]
    return Termination.LLM_ERROR if upstream else Termination.INFRA


def record_attempt(
    run: GenerationRun,
    *,
    adapter: HarnessAdapter,
    manifest: Mapping[str, Any],
    seed: Mapping[str, Any],
    seed_tree_hash: str,
    result: SandboxResult,
    policy: EffectivePolicy,
    steps: StepRecorder,
    started_at: datetime,
    static_checker: Any = None,
    extra_notes: list[Note] | None = None,
) -> AttemptRecord:
    """Write everything one finished sandbox run produced and decide what happens next (section 6.9)."""
    from backend.execution.transitions import guarded_save
    from backend.generation.contracts.outcome import TERMINATION_OUTCOME
    from backend.generation.models import GenerationArtifact
    from backend.generation.models import RunAttempt

    attempt = attempt_number(run)
    steps.start("record")
    calls = result.calls()
    events_ = result.events()
    outputs = result.outputs()
    trajectory = _parse_trajectory(adapter, outputs, calls)
    observed = _observed(adapter, calls)
    exhausted = budget_exhausted_kind(
        state=result.state(),
        events=events_,
        calls=calls,
        max_llm_requests=policy.content.run.max_llm_requests,
    )
    exit_info = dataclasses.replace(result.exit_info, budget_exhausted=exhausted)
    termination = Termination(adapter.classify_termination(exit_info, trajectory))
    if exit_info.cancelled:
        termination = Termination.CANCELLED
    termination = infra_override(termination, calls=calls, result=result)
    rule = TERMINATION_OUTCOME[termination]
    collected: Collected | None = None
    if rule.generation in _EVALUATED_GENERATION and not exit_info.cancelled:
        guarded_save(run, ["updated_at"])  # never write rows for an attempt that is no longer ours
        steps.start("collect")
        collected = _collect(run, result, attempt=attempt, seed=seed)
        if collected.rows:
            collected.static["frontend"] = (
                static_checker(collected.rows)
                if callable(static_checker)
                else {"status": "not_measured", "reason": "no frontend validator in this sandbox backend"}
            )
        steps.end("collect")
    prior_crash = _prior_crash(run) or (termination == Termination.HARNESS_CRASH and not _can_retry(run))
    decision = outcomes.decide(
        termination,
        model_code=bool(collected and collected.has_model_code),
        workspace_truncated=result.workspace_truncated,
        prior_crash=prior_crash,
        invalid_reason=collected.invalid_reason if collected is not None else None,
    )
    usage = attempt_usage(calls)
    served = attempt_served(calls)
    validity = build_validity(
        termination=termination,
        budget_exhausted=exhausted,
        calls=calls,
        manifest=manifest,
        extra=extra_notes or (),
    )
    trajectory_json = trajectory.model_dump(mode="json")
    root = _store_evidence(
        run,
        attempt,
        result,
        {evidence.TRAJECTORY_FILE: _json(trajectory_json)},
    )
    ended_at = timezone.now()
    with transaction.atomic():
        GenerationArtifact.objects.filter(run=run, attempt=attempt).delete()
        GenerationArtifact.objects.bulk_create(_artifact_rows(run, attempt, calls), batch_size=200)
        attempt_row, _ = RunAttempt.objects.update_or_create(
            run=run,
            attempt=attempt,
            defaults={
                "executor_id": run.lease_owner or "",
                "started_at": started_at,
                "ended_at": ended_at,
                "termination": termination.value,
                "outcome": decision.outcome.value if decision.outcome and decision.decision != Decision.RETRY else "",
                "infra_error": decision.retry_reason if decision.decision == Decision.RETRY else "",
                "oom_killed": exit_info.oom_killed,
                "served": served.model_dump(mode="json"),
                "usage": usage.model_dump(mode="json"),
                "budget_exhausted": (exhausted or "")[:24],
                "time_split": result.time_split.model_dump(mode="json"),
                "observed_inputs": observed.model_dump(mode="json") if observed is not None else None,
                "trajectory_blob": blobs.put_json(trajectory_json),
                "evidence_dir": str(root)[:255],
            },
        )
    wall = round(exit_info.elapsed_s, 3)
    collection = collected.summary if collected is not None else None
    run.result = RunResult(
        seed_tree_hash=seed_tree_hash,
        collection=collection,
        static=collected.static if collected is not None else {},
        termination=termination,
        budget_exhausted=exhausted,
        exit_code=exit_info.exit_code,
        error=None,
        steps=steps.steps,
    ).model_dump(mode="json")
    total_tokens = usage.prompt_tokens + usage.completion_tokens
    run.metrics = RunMetrics(
        cost_usd=usage.cost_usd,
        list_cost_usd=usage.list_cost_usd,
        prompt_tokens=usage.prompt_tokens,
        uncached_prompt_tokens=usage.uncached_prompt_tokens,
        completion_tokens=usage.completion_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        cached_tokens=usage.cached_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        total_tokens=total_tokens,
        requests_by_class=dict(usage.requests_by_class),
        call_truncations=usage.call_truncations,
        wall_s=wall,
        loc_model=collection.loc_model if collection is not None else None,
        loc_app=_loc_app(collected),
    ).model_dump(mode="json")
    run.validity = validity.model_dump(mode="json")
    if collected is not None:
        run.tree_hash = collected.tree_hash if collected.rows else ""
        run.workspace_blob = collected.workspace_blob
    if decision.decision in {Decision.DONE, Decision.CANCELLED} and decision.outcome is not None:
        run.outcome, run.outcome_reason = decision.outcome, decision.reason
    steps.end("record")
    run.result = {**run.result, "steps": steps.steps}
    _settle(run, attempt)
    _schedule_stats(run)
    return AttemptRecord(decision=decision, termination=termination, collected=collected, attempt_row=attempt_row)


def _collect(run: GenerationRun, result: SandboxResult, *, attempt: int, seed: Mapping[str, Any]) -> Collected:
    """App runs: the collector. Slot agents: ``slot.executors.restricted_agent.collect_variant`` (WP-12)."""
    if run.kind != "slot_agent":
        return collect(
            run,
            result.workspace_tar,
            attempt=attempt,
            seed=seed,
            workspace_truncated=result.workspace_truncated,
        )
    from backend.generation.agentic.workspace import read_tar
    from backend.generation.slot.executors import restricted_agent

    hook = getattr(restricted_agent, "collect_variant", None)
    if hook is None:
        msg = "WP-12: slot.executors.restricted_agent.collect_variant is not implemented yet"
        raise NotImplementedError(msg)
    final = read_tar(result.workspace_tar) if result.workspace_tar is not None else {}
    return hook(run, final=final, seed=dict(seed), workspace_tar=result.workspace_tar)


def _json(value: Any) -> bytes:
    import json

    return json.dumps(value, sort_keys=True, indent=2).encode("utf-8")


def _loc_app(collected: Collected | None) -> int | None:
    if collected is None or not collected.rows:
        return None
    return sum(row.loc for row in collected.rows if row.origin not in {"deleted", "artifact"} and not row.excluded)


def _settle(run: GenerationRun, attempt: int) -> None:
    try:
        from backend.generation.budget import ledger

        ledger.settle_attempt(run, attempt=attempt)
    except NotImplementedError:
        logger.debug("ledger.settle_attempt is not implemented yet (WP-11); run %s attempt %s", run.pk, attempt)
    except Exception:
        # The reconciler settles attempts that ended with an open reservation (section 4.5).
        logger.exception("settling run %s attempt %s failed", run.pk, attempt)


def _schedule_stats(run: GenerationRun) -> None:
    try:
        from backend.generation.services.openrouter_stats import schedule_for_run

        transaction.on_commit(lambda: schedule_for_run(run.pk))
    except Exception:  # noqa: BLE001 - telemetry backfill is best effort
        logger.debug("scheduling OpenRouter stats for %s failed", run.pk, exc_info=True)


def _finish(run: GenerationRun, record: AttemptRecord, steps: StepRecorder) -> None:
    from backend.execution import transitions

    decision = record.decision
    if decision.decision == Decision.EVALUATE:
        run.outcome, run.outcome_reason = "", ""
        transitions.finish_stage(run, transitions.OUTCOME_EVALUATE)
    elif decision.decision == Decision.CANCELLED:
        transitions.finish_stage(run, transitions.OUTCOME_CANCELLED)
    elif decision.decision == Decision.DONE:
        transitions.finish_stage(run, transitions.OUTCOME_DONE)
    else:
        error = f"attempt {attempt_number(run)} ended {record.termination.value}"
        run.error_message = error
        transitions.infra_retry(run, decision.retry_reason or "infra", error)
    steps.log(f"generation stage ended: {record.termination.value} -> {decision.decision.value}")
