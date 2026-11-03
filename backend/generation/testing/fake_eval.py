"""``FakeEvaluator``: canned ``EvalResult`` rows instead of the eval pipeline (spec sections 6.11 and 12.2).

``FakeEvaluator.evaluate`` has the signature of ``evaluation.suite.evaluate`` and writes a real, frozen
``RunEvaluation`` row (append-only, like the real one) with a canned result. Results are looked up by run
id, then by tree hash, then fall back to ``default``; ``results`` may also be a callable ``run -> result``.
A result whose ``measured`` is False gives ``status=not_measured``; it is never a pass rate of 0.

``install(monkeypatch)`` swaps ``backend.generation.evaluation.suite.evaluate`` for the fake.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

from backend.generation.contracts.evaluation import EvalResult
from backend.generation.models import EvalSuite
from backend.generation.models import GenerationRun
from backend.generation.models import RunEvaluation
from backend.generation.models import choices
from backend.generation.testing.content import adherence_content
from backend.generation.testing.content import eval_result_content

if TYPE_CHECKING:
    import pytest

__all__ = ["TESTER_VERSIONS", "EvaluateCall", "FakeEvaluator", "canned_eval_result"]

ResultLike = EvalResult | Mapping[str, Any]
# The tester versions the real suite records in ``stages.tester.detail`` (images/eval-tester/tester.py, v1).
TESTER_VERSIONS: Final = {"tester_version": 1, "tester_rules_version": 1, "probe_rules_version": 1}


def canned_eval_result(**kwargs: Any) -> EvalResult:
    """A validated ``EvalResult`` (keyword arguments as in ``content.eval_result_content``)."""
    return EvalResult.model_validate(eval_result_content(**kwargs))


@dataclass(frozen=True)
class EvaluateCall:
    """One recorded ``evaluate`` call."""

    run_id: str
    suite_ref: str
    only_checks: list[str] | None
    probes: list[str] | None
    mode: str
    image_override: str | None
    overlay_files: dict[str, str] | None


def _as_result(value: ResultLike) -> dict[str, Any]:
    return EvalResult.model_validate(value).model_dump(mode="json")


class FakeEvaluator:
    """Evaluate runs with canned results; every call is recorded in ``calls``."""

    def __init__(
        self,
        results: Mapping[str, ResultLike] | Callable[[GenerationRun], ResultLike] | None = None,
        *,
        default: ResultLike | None = None,
        adherence: Mapping[str, Any] | None = None,
        eval_level: str = choices.EvalLevel.SECURITY,
    ) -> None:
        self.results = results or {}
        self.default = default if default is not None else eval_result_content()
        self.adherence = dict(adherence) if adherence is not None else adherence_content()
        self.eval_level = eval_level
        self.calls: list[EvaluateCall] = []

    def result_for(self, run: GenerationRun) -> dict[str, Any]:
        if callable(self.results):
            return _as_result(self.results(run))
        for key in (str(run.pk), run.tree_hash):
            if key and key in self.results:
                return _as_result(self.results[key])
        return _as_result(self.default)

    def evaluate(
        self,
        run: GenerationRun,
        suite: EvalSuite,
        *,
        only_checks: list[str] | None = None,
        probes: list[str] | None = None,
        mode: str = "full",
        image_override: str | None = None,
        overlay_files: dict[str, str] | None = None,
        eval_level: str | None = None,
        evaluation: RunEvaluation | None = None,
        runtime: Any = None,
        force_rebuild: bool = False,
        cancelled: Callable[[], bool] | None = None,
        retest_of: RunEvaluation | None = None,
    ) -> RunEvaluation:
        """Write and return a final ``RunEvaluation`` of ``run`` under ``suite`` (same signature as the real one).

        ``stages.tester.detail`` carries what the real suite writes there (``reuse.selection_detail`` plus the
        tester versions), so ``reuse.find_reusable`` treats fake rows like real ones. ``evaluation`` (a pending
        row) is filled in place; ``runtime``, ``force_rebuild`` and ``cancelled`` are accepted and ignored.
        """
        from backend.generation.evaluation.reuse import selection_detail

        del runtime, force_rebuild, cancelled
        self.calls.append(
            EvaluateCall(
                run_id=str(run.pk),
                suite_ref=suite.pinned_ref,
                only_checks=list(only_checks) if only_checks is not None else None,
                probes=list(probes) if probes is not None else None,
                mode=mode,
                image_override=image_override,
                overlay_files=dict(overlay_files) if overlay_files is not None else None,
            ),
        )
        result = self.result_for(run)
        measured = bool(result["measured"])
        detail = {
            **selection_detail(
                only_checks=only_checks,
                probes=probes,
                image_override=image_override,
                overlay_files=overlay_files,
            ),
            **TESTER_VERSIONS,
            "tester_version": suite.tester_version or TESTER_VERSIONS["tester_version"],
        }
        stage = {"status": "ok" if measured else "not_measured", "detail": detail}
        fields: dict[str, Any] = {
            "eval_suite": suite,
            "eval_level": eval_level or self.eval_level,
            "mode": mode,
            "status": choices.EvaluationStatus.MEASURED if measured else choices.EvaluationStatus.NOT_MEASURED,
            "stages": {
                "lock": {"status": "ok"},
                "build": {"status": "ok"},
                "start": {"status": "ok"},
                "tester": stage,
            },
            "result": result,
            "adherence": self.adherence if measured else adherence_content(status="not_measured"),
        }
        if retest_of is not None:
            fields["retest_of"] = retest_of
        if evaluation is not None:
            for name, value in fields.items():
                setattr(evaluation, name, value)
            evaluation.save()
            return evaluation
        return RunEvaluation.objects.create(run=run, **fields)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeEvaluator:
        """Patch ``evaluation.suite.evaluate`` with ``self.evaluate`` for the test (returns ``self``)."""
        from backend.generation.evaluation import suite

        monkeypatch.setattr(suite, "evaluate", self.evaluate)
        return self
