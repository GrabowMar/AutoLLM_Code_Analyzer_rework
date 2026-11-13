"""eval-v2@2: what an evaluation records about its instrument, and when it refuses the tree.

* stored check rows carry the plan's ``stated_in`` / ``source`` / ``overlay`` whatever the tester echoed, so the
  stated / unstated pass rates are computed (they were ``None`` / "everything unstated" under eval-v2@1);
* ``plan_sha256`` / ``fixtures_sha256`` / overlay-skipped checks are recorded;
* a suite that pins ``fixtures_digest`` refuses a tree with other overlays or another compiler;
* a plan with ``upstream`` needs the runtime's mock upstream network.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from backend.generation.contracts.library import EvalSuiteContent
from backend.generation.evaluation import acceptance
from backend.generation.evaluation import metrics as metrics_mod
from backend.generation.evaluation import suite as suite_mod
from backend.generation.evaluation.runtime import RuntimeInfraError
from backend.generation.library import service
from backend.generation.models import GenerationRun
from backend.generation.models import RunEvaluation
from backend.generation.models import choices
from backend.generation.tests.wp09_eval.helpers import kit_plan
from backend.generation.tests.wp09_eval.test_suite import ScriptedRuntime
from backend.generation.tests.wp09_eval.test_suite import make_run
from backend.generation.tests.wp09_eval.test_suite import suite_of

pytestmark = pytest.mark.django_db

HEX64 = re.compile(r"^[0-9a-f]{64}$")
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


@pytest.fixture(autouse=True)
def _local_runtime(settings: Any) -> None:
    settings.EVAL_RUNTIME = "local"


@pytest.fixture
def golden() -> Any:
    from backend.generation.testing.golden_library import install_golden_library

    return install_golden_library()


class V1TesterRuntime(ScriptedRuntime):
    """A tester that reports like tester v1: rows without the plan's metadata. Records what it was asked."""

    def __init__(self) -> None:
        super().__init__()
        self.plans: list[dict[str, Any]] = []
        self.calls: list[str] = []

    def prepare_upstream(self, spec: Any) -> None:
        self.calls.append("prepare_upstream")
        super().prepare_upstream(spec)

    def start(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append("start")
        return super().start(*args, **kwargs)

    def run_tester(self, handle: Any, plan: dict[str, Any], env: Any, timeout_s: float) -> dict[str, Any]:
        self.plans.append(plan)
        self.calls.append("run_tester")
        out = super().run_tester(handle, {k: v for k, v in plan.items() if k != "upstream"}, env, timeout_s)
        for row in out.get("checks") or []:
            for key in ("stated_in", "source", "overlay"):
                row.pop(key, None)
        return out


def standard_run(golden: Any, **kw: Any) -> GenerationRun:
    """A reference run that carries a prose variant like a study run (reference runs have none)."""
    run = make_run(golden, **kw)
    GenerationRun.objects.filter(pk=run.pk).update(prose_variant="standard")
    run.refresh_from_db()
    return run


def pinned_suite(golden: Any, **content: Any) -> Any:
    """The golden suite with other content, in memory only (the stored eval-v2 row is not touched)."""
    suite = suite_of(golden)
    # A pinned suite names the compiler it was validated with: this tree's, unless a test says otherwise.
    suite.content = {**suite.content, "acceptance_compiler_version": acceptance.COMPILER_VERSION, **content}
    return suite


# --- plan metadata in the stored rows ---------------------------------------------------------------


def test_merge_plan_metadata_copies_by_id_and_the_plan_wins() -> None:
    plan = {
        "checks": [
            {"id": "a", "stated_in": ["standard", "detailed"], "source": "acceptance", "overlay": True},
            {"id": "b", "stated_in": [], "source": "compiler"},
            {"id": "c"},
        ],
    }
    rows = [
        {"id": "a", "status": "pass", "stated_in": [], "source": "compiler", "overlay": False},
        {"id": "b", "status": "fail"},
        {"id": "c", "status": "pass"},
        {"id": "not-in-plan", "status": "pass"},
    ]
    merged = suite_mod.merge_plan_metadata(rows, plan)
    assert merged[0] == {**rows[0], "stated_in": ["standard", "detailed"], "source": "acceptance", "overlay": True}
    assert merged[1] == {"id": "b", "status": "fail", "stated_in": [], "source": "compiler", "overlay": False}
    assert merged[2] == {"id": "c", "status": "pass", "stated_in": [], "source": "compiler", "overlay": False}
    assert merged[3] == {"id": "not-in-plan", "status": "pass"}  # nothing known: left unrecorded
    assert "stated_in" not in rows[1]  # the tester's rows are not mutated


def test_stored_rows_carry_the_plan_metadata_even_from_a_v1_tester(golden: Any) -> None:
    runtime = V1TesterRuntime()
    row = suite_mod.evaluate(standard_run(golden, level="functional"), suite_of(golden), runtime=runtime)
    assert row.status == choices.EvaluationStatus.MEASURED
    plan = {c["id"]: c for c in runtime.plans[0]["checks"]}
    stored = row.result["checks"]
    assert stored
    assert {c["id"] for c in stored} == set(plan)
    for check in stored:
        compiled = plan[check["id"]]
        assert check["stated_in"] == list(compiled.get("stated_in") or [])
        assert check["source"] == (compiled.get("source") or "compiler")
        assert check["overlay"] is bool(compiled.get("overlay"))


def test_stated_and_unstated_pass_rates_follow_the_plan(golden: Any) -> None:
    runtime = V1TesterRuntime()
    row = suite_mod.evaluate(standard_run(golden, level="functional"), suite_of(golden), runtime=runtime)
    counted = [c for c in runtime.plans[0]["checks"] if c.get("category") != "infra"]
    stated = [c for c in counted if "standard" in (c.get("stated_in") or [])]
    unstated = [c for c in counted if "standard" not in (c.get("stated_in") or [])]
    assert stated, "the golden task states behaviours in its standard prose"
    metrics = row.result["metrics"]
    assert metrics["agent_pass_rate"] == 1.0
    assert metrics["stated_pass_rate"] == 1.0  # was None under eval-v2@1: no row recorded stated_in
    assert metrics["unstated_pass_rate"] == (1.0 if unstated else None)


def test_a_run_without_a_prose_variant_has_no_stated_split(golden: Any) -> None:
    row = suite_mod.evaluate(make_run(golden, level="functional"), suite_of(golden))
    assert row.result["metrics"]["stated_pass_rate"] is None
    assert row.result["metrics"]["unstated_pass_rate"] is None


def test_rows_without_recorded_stated_in_give_an_unknown_split_not_all_unstated() -> None:
    v1_rows = [
        {"id": "a", "category": "contract", "status": "pass"},
        {"id": "b", "category": "crud", "status": "fail"},
    ]
    old = metrics_mod.compute(v1_rows, [], prose_variant="standard")
    assert old.agent_pass_rate == 0.5
    assert old.stated_pass_rate is None
    assert old.unstated_pass_rate is None  # eval-v2@1 stored 0.5 here: every check counted as unstated
    rows = [
        {**v1_rows[0], "stated_in": ["standard"]},
        {**v1_rows[1], "stated_in": []},
        {"id": "c", "category": "crud", "status": "pass"},  # not in the plan: in the agent rate only
    ]
    new = metrics_mod.compute(rows, [], prose_variant="standard")
    assert new.stated_pass_rate == 1.0
    assert new.unstated_pass_rate == 0.0
    assert new.agent_pass_rate == pytest.approx(2 / 3)
    assert metrics_mod.compute(rows, [], prose_variant=None).stated_pass_rate is None


# --- plan_sha256 --------------------------------------------------------------------------------------


def test_plan_sha256_ignores_run_values_and_sees_every_instrument_change() -> None:
    plan = kit_plan()
    base = suite_mod.plan_sha256(plan)
    assert HEX64.match(base)
    run_values = {"only": ["x"], "base_url": "http://127.0.0.1:1", "mock_host": "127.0.0.1", "mock_port": 0}
    assert suite_mod.plan_sha256({**plan, **run_values}) == base
    assert suite_mod.plan_sha256(dict(reversed(list(plan.items())))) == base  # key order is not identity
    changed = {**plan, "checks": [{**plan["checks"][0], "id": "renamed"}, *plan["checks"][1:]]}
    assert suite_mod.plan_sha256(changed) != base
    assert suite_mod.plan_sha256({**plan, "upstream": {"hosts": ["www.example.com"], "port": 80}}) != base
    assert suite_mod.plan_sha256({**plan, "skipped": [{"id": "x", "why": "y"}]}) != base
    assert suite_mod.plan_sha256(kit_plan("cookie_session")) != base


def test_plan_sha256_is_the_same_at_every_level_and_selection(golden: Any) -> None:
    suite = suite_of(golden)
    security = suite_mod.evaluate(make_run(golden, level="security"), suite)
    functional = suite_mod.evaluate(make_run(golden, level="functional"), suite)
    some = [c["id"] for c in functional.result["checks"]][:3]
    selected = suite_mod.evaluate(make_run(golden, level="functional"), suite, only_checks=some)
    hashes = {row.result["plan_sha256"] for row in (security, functional, selected)}
    assert len(hashes) == 1
    assert HEX64.match(next(iter(hashes)))
    assert len(selected.result["checks"]) < len(functional.result["checks"])
    detail = security.stages["tester"]["detail"]
    assert detail["plan_sha256"] == security.result["plan_sha256"]
    assert detail["compiler_version"] == acceptance.COMPILER_VERSION
    assert detail["upstream"] is False
    assert detail["checks_skipped"] == len(security.result["skipped"])


def test_a_level_without_the_tester_records_no_plan(golden: Any) -> None:
    row = suite_mod.evaluate(make_run(golden, level="build"), suite_of(golden))
    assert row.result["plan_sha256"] is None
    assert row.result["skipped"] == []


# --- overlay-skipped checks ---------------------------------------------------------------------------


def test_overlay_skipped_checks_are_stored_and_in_no_denominator(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    real = acceptance.plan_json
    dropped: list[str] = []

    def plan_json(plan: Any) -> dict[str, Any]:
        data = real(plan)
        needed = {dep for c in data["checks"] for dep in c.get("depends_on") or []}
        victim = next(c for c in reversed(data["checks"]) if c["id"] not in needed and c.get("category") != "infra")
        dropped[:] = [victim["id"]]
        data["checks"] = [c for c in data["checks"] if c["id"] != victim["id"]]
        data["skipped"] = [{"id": victim["id"], "why": "wrong expectation (probe report)"}]
        return data

    plain = suite_mod.evaluate(make_run(golden, level="functional"), suite_of(golden))
    monkeypatch.setattr(acceptance, "plan_json", plan_json)
    row = suite_mod.evaluate(make_run(golden, level="functional"), suite_of(golden))
    assert row.status == choices.EvaluationStatus.MEASURED
    assert row.result["skipped"] == [{"id": dropped[0], "why": "wrong expectation (probe report)"}]
    assert dropped[0] not in {c["id"] for c in row.result["checks"]}
    assert len(row.result["checks"]) == len(plain.result["checks"]) - 1
    assert row.stages["tester"]["detail"]["checks_skipped"] == 1
    assert row.result["plan_sha256"] != plain.result["plan_sha256"]


# --- the fixtures pin ---------------------------------------------------------------------------------


def test_an_unpinned_suite_never_checks_the_tree(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acceptance, "fixtures_digest", lambda: DIGEST_B, raising=False)
    monkeypatch.setattr(acceptance, "COMPILER_VERSION", acceptance.COMPILER_VERSION)
    assert "fixtures_digest" not in suite_of(golden).content  # eval-v2@1
    row = suite_mod.evaluate(make_run(golden, level="functional"), suite_of(golden))
    assert row.status == choices.EvaluationStatus.MEASURED
    assert row.result["fixtures_sha256"] == DIGEST_B  # recorded even when not pinned


@pytest.mark.parametrize("tree", [DIGEST_A, f"sha256:{DIGEST_A}", DIGEST_A.upper()])
def test_a_matching_fixtures_digest_evaluates_and_is_recorded(
    golden: Any,
    monkeypatch: pytest.MonkeyPatch,
    tree: str,
) -> None:
    monkeypatch.setattr(acceptance, "fixtures_digest", lambda: tree, raising=False)
    suite = pinned_suite(golden, fixtures_digest=f"sha256:{DIGEST_A}")
    row = suite_mod.evaluate(make_run(golden, level="functional"), suite)
    assert row.status == choices.EvaluationStatus.MEASURED
    assert row.result["fixtures_sha256"] == DIGEST_A
    assert row.stages["tester"]["detail"]["fixtures_sha256"] == DIGEST_A


def test_another_trees_overlays_are_an_infra_error_before_anything_is_built(
    golden: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(acceptance, "fixtures_digest", lambda: DIGEST_B, raising=False)
    runtime = ScriptedRuntime()
    row = suite_mod.evaluate(make_run(golden), pinned_suite(golden, fixtures_digest=DIGEST_A), runtime=runtime)
    assert row.status == choices.EvaluationStatus.ERROR
    assert suite_mod.outcome_of(row) == (choices.Outcome.INFRA_ERROR, "fixtures_digest_mismatch")
    assert runtime.build_calls == 0
    assert "build" not in row.stages
    assert "start" not in row.stages
    detail = row.stages["tester"]["detail"]
    assert detail["reason"] == "fixtures_digest_mismatch"
    assert (detail["pinned"], detail["tree"]) == (DIGEST_A, DIGEST_B)
    assert row.result["measured"] is False
    assert row.result["metrics"]["agent_pass_rate"] is None  # never a score


def test_a_tree_without_overlays_cannot_serve_a_pinned_suite(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(acceptance, "fixtures_digest", raising=False)
    row = suite_mod.evaluate(make_run(golden), pinned_suite(golden, fixtures_digest=DIGEST_A))
    assert suite_mod.outcome_of(row) == (choices.Outcome.INFRA_ERROR, "fixtures_digest_mismatch")
    assert row.stages["tester"]["detail"]["tree"] is None


def test_an_unreadable_overlay_is_an_infra_error(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> str:
        msg = "tester.yaml of crud_todo_list is not valid"
        raise ValueError(msg)

    monkeypatch.setattr(acceptance, "fixtures_digest", broken, raising=False)
    row = suite_mod.evaluate(make_run(golden), suite_of(golden))
    assert suite_mod.outcome_of(row) == (choices.Outcome.INFRA_ERROR, "fixtures_digest_mismatch")
    assert "not valid" in row.stages["tester"]["detail"]["error"]


def test_a_pinned_suite_needs_its_compiler_version(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acceptance, "fixtures_digest", lambda: DIGEST_A, raising=False)
    suite = pinned_suite(
        golden,
        fixtures_digest=DIGEST_A,
        acceptance_compiler_version=acceptance.COMPILER_VERSION + 1,
    )
    runtime = ScriptedRuntime()
    row = suite_mod.evaluate(make_run(golden), suite, runtime=runtime)
    assert suite_mod.outcome_of(row) == (choices.Outcome.INFRA_ERROR, "compiler_version_mismatch")
    assert runtime.build_calls == 0
    detail = row.stages["tester"]["detail"]
    assert (detail["pinned"], detail["tree"]) == (acceptance.COMPILER_VERSION + 1, acceptance.COMPILER_VERSION)


def test_levels_without_the_tester_do_not_need_the_instrument(golden: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(acceptance, "fixtures_digest", lambda: DIGEST_B, raising=False)
    row = suite_mod.evaluate(make_run(golden, level="build"), pinned_suite(golden, fixtures_digest=DIGEST_A))
    assert suite_mod.outcome_of(row) == (choices.Outcome.TESTED, "not_tested:build")


# --- suite content: the optional pin keeps eval-v2@1 byte-identical -----------------------------------


def test_fixtures_digest_is_optional_and_absent_from_unpinned_content(golden: Any) -> None:
    suite = suite_of(golden)
    content = dict(suite.content)
    assert "fixtures_digest" not in EvalSuiteContent.model_validate(content).model_dump(mode="json")
    assert service.content_hash_of("eval_suite", content) == suite.content_hash  # eval-v2@1 hashes as before
    pinned = EvalSuiteContent.model_validate({**content, "fixtures_digest": f"sha256:{DIGEST_A}"})
    assert pinned.model_dump(mode="json")["fixtures_digest"] == f"sha256:{DIGEST_A}"
    assert service.content_hash_of("eval_suite", {**content, "fixtures_digest": DIGEST_A}) != suite.content_hash
    with pytest.raises(ValueError, match="fixtures_digest"):
        EvalSuiteContent.model_validate({**content, "fixtures_digest": "not-a-digest"})


def test_a_pinned_suite_is_a_new_version_and_old_evaluations_keep_theirs(golden: Any) -> None:
    v1 = suite_of(golden)
    old = suite_mod.evaluate(make_run(golden, level="functional"), v1)
    before = (old.eval_suite_id, old.result, old.stages)
    v2 = service.new_version(
        v1,
        base_version=v1.version,
        content={**v1.content, "fixtures_digest": DIGEST_A},
        user=None,
        system=service.is_system_row(v1),
    )
    assert v2.version == v1.version + 1
    assert v2.pk != v1.pk
    assert v2.content["fixtures_digest"] == DIGEST_A
    v1.refresh_from_db()
    assert "fixtures_digest" not in v1.content
    old = RunEvaluation.objects.get(pk=old.pk)
    assert (old.eval_suite_id, old.result, old.stages) == before


# --- the mock upstream --------------------------------------------------------------------------------


def _with_upstream(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    spec = {"hosts": ["www.example-news.com"], "port": 80}
    real = acceptance.plan_json
    monkeypatch.setattr(acceptance, "plan_json", lambda plan: {**real(plan), "upstream": spec})
    return spec


def test_a_plan_with_upstream_prepares_the_network_before_the_app_starts(
    golden: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _with_upstream(monkeypatch)
    runtime = V1TesterRuntime()
    row = suite_mod.evaluate(make_run(golden, level="functional"), suite_of(golden), runtime=runtime)
    assert row.status == choices.EvaluationStatus.MEASURED
    assert runtime.calls.index("prepare_upstream") < runtime.calls.index("start")
    assert runtime.plans[0]["upstream"] == spec
    assert row.stages["tester"]["detail"]["upstream"] is True


def test_a_plan_without_upstream_never_asks_for_the_network(golden: Any) -> None:
    runtime = V1TesterRuntime()
    suite_mod.evaluate(make_run(golden, level="functional"), suite_of(golden), runtime=runtime)
    assert "prepare_upstream" not in runtime.calls


class NoUpstreamRuntime(ScriptedRuntime):
    prepare_upstream = None  # type: ignore[assignment]  # a runtime written before eval-v2@2


class BrokenUpstreamRuntime(ScriptedRuntime):
    def prepare_upstream(self, spec: Any) -> None:
        msg = "creating the mock upstream network failed: no free subnet"
        raise RuntimeInfraError(msg)


@pytest.mark.parametrize("runtime_class", [NoUpstreamRuntime, BrokenUpstreamRuntime])
def test_no_upstream_network_is_an_infra_error_never_the_samples(
    golden: Any,
    monkeypatch: pytest.MonkeyPatch,
    runtime_class: type[ScriptedRuntime],
) -> None:
    _with_upstream(monkeypatch)
    row = suite_mod.evaluate(make_run(golden, level="functional"), suite_of(golden), runtime=runtime_class())
    assert row.status == choices.EvaluationStatus.ERROR
    assert suite_mod.outcome_of(row) == (choices.Outcome.INFRA_ERROR, "upstream_unavailable")
    assert row.stages["start"]["status"] == "error"
    assert "upstream" in row.stages["start"]["detail"]["error"]
    assert "tester" not in row.stages or row.stages["tester"]["status"] != "ok"
    assert row.result["metrics"]["agent_pass_rate"] is None
