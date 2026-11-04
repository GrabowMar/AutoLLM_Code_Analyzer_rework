"""Fakes for slot mode (WP-12's own area; spec section 12.2).

* ``FakeAcceptance``: stands in for ``evaluation.acceptance.checks_for_endpoints`` (WP-09) with a
  deterministic rule: every endpoint id ``e`` yields the compiler-style check ``f"{e}.ok"`` plus the task's
  hand-written acceptance checks whose ``endpoint`` is ``e``.
* ``slot_eval_result(checks, probes)``: an ``EvalResult`` payload with exactly these check / probe statuses.
* ``SlotEvalScript``: a ``FakeEvaluator`` results callable that answers validation and variant evaluations
  by the run's role (original, holed, variant) and an optional per-call script (to make a check flaky).
* ``FakeSlotTransport``: the single-shot transport (``slot.executors.single_shot.transport``) with canned
  chat completions; ``chat_response(...)`` builds one.
* ``FakeBatchBackend``: a batch backend whose batches complete after N polls (or never).
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    import pytest

    from backend.generation.models import GenerationRun
    from backend.generation.models import TaskSpecVersion
    from backend.generation.slot.executors.batch_submitter import BatchItem
    from backend.generation.slot.executors.batch_submitter import BatchStatus
    from backend.generation.slot.executors.single_shot import LLMCall
    from backend.generation.slot.executors.single_shot import SlotRequest

__all__ = [
    "FakeAcceptance",
    "FakeBatchBackend",
    "FakeSlotTransport",
    "SlotEvalScript",
    "chat_response",
    "slot_eval_result",
]


class FakeAcceptance:
    """``checks_for_endpoints(task_version, endpoint_ids)`` with a fixed, documented rule."""

    def __init__(self, extra: Mapping[str, list[str]] | None = None) -> None:
        self.extra = dict(extra or {})
        self.calls: list[tuple[str, list[str]]] = []

    def checks_for_endpoints(self, task_version: TaskSpecVersion, endpoint_ids: list[str]) -> list[str]:
        self.calls.append((str(task_version.pk), list(endpoint_ids)))
        acceptance = (task_version.content or {}).get("acceptance", [])
        checks: list[str] = []
        for endpoint in endpoint_ids:
            checks.append(f"{endpoint}.ok")
            checks += [c["id"] for c in acceptance if c.get("endpoint") == endpoint]
            checks += self.extra.get(endpoint, [])
        return sorted(dict.fromkeys(checks))

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeAcceptance:
        from backend.generation.evaluation import acceptance

        monkeypatch.setattr(acceptance, "checks_for_endpoints", self.checks_for_endpoints)
        return self


def slot_eval_result(
    checks: Mapping[str, str],
    probes: Mapping[str, str] | None = None,
    *,
    flaky: Iterable[str] = (),
) -> dict[str, Any]:
    """An ``EvalResult`` with exactly these check statuses (plus the health check) and probe statuses."""
    rows = [{"id": "health", "category": "infra", "status": "pass"}]
    rows += [{"id": check, "category": "functional", "status": status} for check, status in sorted(checks.items())]
    probe_rows = [
        {"id": probe, "cwe": "cwe-89", "kind": "sqli_boolean", "status": status}
        for probe, status in sorted((probes or {}).items())
    ]
    functional = [row for row in rows if row["category"] == "functional"]
    passed = sum(1 for row in functional if row["status"] == "pass")
    return {
        "tester_version": 1,
        "compiler_version": 1,
        "auth_mode": "contract_bearer",
        "checks": rows,
        "probes": probe_rows,
        "metrics": {
            "infra_ok": True,
            "agent_checks": len(functional),
            "agent_passed": passed,
            "agent_pass_rate": passed / len(functional) if functional else None,
            "probes_tested": len(probe_rows),
            "probes_exploited": sum(1 for row in probe_rows if row["status"] == "exploited"),
        },
        "measured": True,
        "flaky_checks": sorted(flaky),
    }


@dataclass
class SlotEvalScript:
    """Answers ``FakeEvaluator`` calls for slot runs by role.

    ``original`` / ``holed`` / ``variant`` map check ids to statuses (missing checks pass on the original,
    fail on the holed tree, pass on variants). ``original_runs`` overrides per original run index (0, 1, 2),
    e.g. ``{1: {"todos.update.ok": "fail"}}`` makes that check flaky. ``probes`` are the original's probe
    statuses (``probe_runs`` overrides per original run). The evaluator's selection is read from the
    target's recorded validation, exactly what the eval stage passes as ``only_checks``.
    """

    original: dict[str, str] = field(default_factory=dict)
    holed: dict[str, str] = field(default_factory=dict)
    variant: dict[str, str] = field(default_factory=dict)
    original_runs: dict[int, dict[str, str]] = field(default_factory=dict)
    probes: dict[str, str] = field(default_factory=dict)
    probe_runs: dict[int, dict[str, str]] = field(default_factory=dict)
    seen: list[str] = field(default_factory=list)

    def __call__(self, run: GenerationRun) -> dict[str, Any]:
        target = run.slot_target
        validation = (target.validation or {}) if target is not None else {}
        candidates = list((validation.get("original_pass") or {}).keys())
        probe_ids = list((validation.get("probe_stability") or {}).keys())
        if run.reference_of == "slot_holed":
            return slot_eval_result({c: self.holed.get(c, "fail") for c in candidates})
        if run.reference_of == "slot_original":
            ids = validation.get("runs", [])
            index = ids.index(str(run.pk)) if str(run.pk) in ids else 0
            overrides = self.original_runs.get(index, {})
            checks = {c: overrides.get(c, self.original.get(c, "pass")) for c in candidates}
            probe_overrides = self.probe_runs.get(index, {})
            probes = {p: probe_overrides.get(p, self.probes.get(p, "not_exploited")) for p in probe_ids}
            self.seen.append(f"original:{index}")
            return slot_eval_result(checks, probes)
        discriminating = list(validation.get("discriminating_checks") or [])
        self.seen.append("variant")
        return slot_eval_result({c: self.variant.get(c, "pass") for c in discriminating})


def chat_response(
    content: str | None,
    *,
    finish_reason: str = "stop",
    prompt_tokens: int = 1200,
    completion_tokens: int = 300,
    cost: float | None = 0.00021,
    model: str = "deepseek/deepseek-v4.1-flash",
    refusal: str | None = None,
) -> dict[str, Any]:
    """An OpenAI ``chat.completion`` dict as OpenRouter returns it."""
    usage: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    if cost is not None:
        usage["cost"] = cost
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if refusal is not None:
        message["refusal"] = refusal
    return {
        "id": "gen-fake",
        "model": model,
        "provider": "FakeProvider",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage,
    }


@dataclass
class FakeSlotTransport:
    """Canned answers for single-shot slot calls; ``errors`` raise ``TransportError`` first (in order)."""

    responses: list[dict[str, Any]] | Callable[[GenerationRun, SlotRequest], dict[str, Any]] = field(
        default_factory=list,
    )
    errors: list[Any] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, run: GenerationRun, request: SlotRequest) -> LLMCall:
        from backend.generation.slot.executors.single_shot import LLMCall

        self.requests.append(request.body)
        if self.errors:
            raise self.errors.pop(0)
        if callable(self.responses):
            return LLMCall(response=self.responses(run, request), served={"endpoint": "fake"})
        return LLMCall(response=self.responses.pop(0), served={"endpoint": "fake"}, seconds=0.5)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeSlotTransport:
        from backend.generation.slot.executors import single_shot

        monkeypatch.setattr(single_shot, "transport", self)
        monkeypatch.setattr(single_shot.time, "sleep", lambda _s: None)
        return self


@dataclass
class FakeBatchBackend:
    """Completes a batch after ``polls_until_done`` polls with ``answer(custom_id, body)``; ``None`` never does."""

    answer: Callable[[str, dict[str, Any]], dict[str, Any]]
    polls_until_done: int | None = 1
    name: str = "fake"
    submitted: list[list[BatchItem]] = field(default_factory=list)
    polls: int = 0

    def submit(self, items: list[BatchItem]) -> str:
        self.submitted.append(list(items))
        return f"batch-{len(self.submitted)}"

    def poll(self, provider_batch_ref: str) -> BatchStatus:
        from backend.generation.slot.executors.batch_submitter import BatchStatus

        self.polls += 1
        if self.polls_until_done is None or self.polls < self.polls_until_done:
            return BatchStatus("in_progress")
        items = self.submitted[int(provider_batch_ref.rsplit("-", 1)[-1]) - 1]
        return BatchStatus("completed", {item.custom_id: self.answer(item.custom_id, item.body) for item in items})
