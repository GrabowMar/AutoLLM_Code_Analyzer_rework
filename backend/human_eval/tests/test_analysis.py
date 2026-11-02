"""services/analysis.py: compute_results end to end on fixture tasks (shape = TS ``TaskResults``)."""

from __future__ import annotations

import copy
import json
import math
from datetime import timedelta

import pytest
from django.utils import timezone

from backend.analysis.tests.factories import FindingFactory
from backend.generation.services.experiment_results import wilcoxon_p
from backend.human_eval.models import Assignment
from backend.human_eval.models import EvalItem
from backend.human_eval.models import EvalTask
from backend.human_eval.models import EvalTaskApp
from backend.human_eval.models import Observation
from backend.human_eval.models import Participant
from backend.human_eval.models import Response
from backend.human_eval.services import analysis
from backend.human_eval.services import design as design_service
from backend.human_eval.services import stats
from backend.human_eval.services.analysis import canonical_score
from backend.human_eval.services.analysis import compute_results
from backend.human_eval.services.errors import HumanEvalServiceError
from backend.human_eval.tests.factories import AssignmentFactory
from backend.human_eval.tests.factories import EvalTaskFactory
from backend.human_eval.tests.factories import FindingVerdictFactory
from backend.human_eval.tests.factories import HttpExchangeFactory
from backend.human_eval.tests.factories import ObservationFactory
from backend.human_eval.tests.factories import ParticipantFactory
from backend.human_eval.tests.factories import make_open_task
from backend.statistics.services.inference import holm

pytestmark = pytest.mark.django_db

S = Assignment.Status

# ---------------------------------------------------------------------------
# TS shapes (frontend/src/lib/api/types/human-eval.ts)
# ---------------------------------------------------------------------------

TASK_RESULTS = {
    "task_id",
    "generated_at",
    "provisional",
    "p_values_withheld",
    "confirmatory",
    "deviations",
    "rule_exclusions",
    "group_by",
    "include_excluded",
    "counts",
    "warnings",
    "groups",
    "apps",
    "blocks",
    "quality",
    "contrasts",
    "human_vs_automated",
}
COUNTS = {
    "participants",
    "participants_included",
    "assignments_submitted",
    "assignments_included",
    "assignments_skipped",
    "assignments_excluded",
}
RESULT_GROUP = {"key", "label", "n_apps_pool", "n_apps_rated", "n_apps_unevaluable"}
RESULT_APP = {
    "app_id",
    "label",
    "group",
    "model_name",
    "condition_label",
    "app_slug",
    "job_id",
    "n_submitted",
    "n_unavailable",
}
AGREEMENT = {"alpha", "level", "percent_agreement", "n_units", "n_values"}
APP_SCORE = {"app_id", "label", "group", "n", "mean", "sd", "median", "na_count"}
GROUP_SCORE = {"group", "label", "n_apps", "n_ratings", "mean", "ci", "floor"}
PRECISION_ROW = {
    "key",
    "label",
    "tp",
    "fp",
    "unsure",
    "n_findings",
    "n_apps",
    "n_split",
    "precision",
    "ci",
    "majority_precision",
    "weighted",
}
BT_ENTRY = {
    "key",
    "label",
    "component",
    "strength",
    "log_strength",
    "ci",
    "wins",
    "losses",
    "ties",
    "comparisons",
    "rank",
}
PARTICIPANT_QUALITY = {
    "participant_id",
    "label",
    "kind",
    "excluded",
    "submitted",
    "skipped",
    "median_seconds",
    "speeding",
    "straightlining",
    "attention_failed",
    "flags",
}
CONTRAST = {
    "block_id",
    "metric",
    "label",
    "group",
    "group_label",
    "baseline",
    "baseline_label",
    "diff",
    "ci",
    "paired",
    "n_cells",
    "p_value",
    "p_adjusted",
    "primary",
}
HUMAN_VS_AUTOMATED = {"metric", "label", "rho", "ci", "n", "note"}


def _ci(value):
    assert value is None or set(value) == {"low", "high"}


def _keys(value, expected):
    assert isinstance(value, dict)
    assert set(value) == expected, set(value) ^ expected


def _agreement(value):
    _keys(value, AGREEMENT)
    assert value["level"] in ("nominal", "ordinal", "interval")


def _app_scores(rows):
    for row in rows:
        _keys(row, APP_SCORE)


def _group_scores(rows):
    for row in rows:
        _keys(row, GROUP_SCORE)
        _ci(row["ci"])
        if row["floor"] is not None:
            _keys(row["floor"], {"mean", "ci", "n_apps"})
            _ci(row["floor"]["ci"])


def _precision(row):
    _keys(row, PRECISION_ROW)
    _ci(row["ci"])


def _block(block):
    kind = block["type"]
    if kind == "rating":
        _keys(block, {"type", "block_id", "title", "criteria", "composite"})
        for criterion in block["criteria"]:
            _keys(criterion, {"criterion_id", "label", "scale", "per_app", "per_group", "agreement"})
            _app_scores(criterion["per_app"])
            _group_scores(criterion["per_group"])
            _agreement(criterion["agreement"])
        _keys(block["composite"], {"per_app", "per_group"})
        _app_scores(block["composite"]["per_app"])
        _group_scores(block["composite"]["per_group"])
    elif kind == "requirements":
        _keys(
            block,
            {
                "type",
                "block_id",
                "title",
                "per_app",
                "per_group",
                "per_requirement",
                "agreement",
                "vs_conformance",
                "endpoint_agreement",
            },
        )
        for row in block["per_app"]:
            _keys(
                row,
                {
                    "app_id",
                    "label",
                    "group",
                    "n_raters",
                    "compliance",
                    "compliance_strict",
                    "by_source",
                    "blocked",
                    "cannot_test",
                    "conformance_pass_rate",
                    "conformance_endpoint_pass_rate",
                    "conformance_status",
                },
            )
        _group_scores(block["per_group"])
        for row in block["per_requirement"]:
            _keys(
                row,
                {"req_id", "source", "text", "n_ratings", "met", "partial", "not_met", "blocked", "cannot_test"},
            )
        _agreement(block["agreement"])
        for row in block["vs_conformance"]:
            _keys(row, {"scope", "rho", "ci", "n", "excluded_apps", "note"})
            assert row["scope"] in ("endpoints", "overall")
            _ci(row["ci"])
        if block["endpoint_agreement"] is not None:
            _keys(
                block["endpoint_agreement"],
                {"n", "kappa", "both_pass", "human_only", "auto_only", "both_fail", "n_unmatched"},
            )
    elif kind == "security":
        _keys(block, {"type", "block_id", "title", "checklist", "reports", "finding_review", "console"})
        for item in block["checklist"]:
            _keys(item, {"item_id", "label", "category", "per_group", "agreement"})
            for row in item["per_group"]:
                _keys(
                    row,
                    {
                        "group",
                        "label",
                        "n_apps",
                        "n",
                        "vulnerable",
                        "not_vulnerable",
                        "not_tested",
                        "vulnerable_share",
                        "ci",
                    },
                )
            _agreement(item["agreement"])
        _keys(block["reports"], {"per_app", "per_group"})
        for row in block["reports"]["per_app"]:
            _keys(row, {"app_id", "label", "group", "total", "by_severity", "by_category"})
        for row in block["reports"]["per_group"]:
            _keys(row, {"group", "label", "n_apps", "total", "per_rater_mean", "ci", "by_severity"})
        review = block["finding_review"]
        if review is not None:
            _keys(
                review,
                {"n_findings", "n_verdicts", "overall", "per_tool", "per_tool_severity", "per_group", "agreement"},
            )
            _precision(review["overall"])
            for row in [*review["per_tool"], *review["per_tool_severity"], *review["per_group"]]:
                _precision(row)
            _agreement(review["agreement"])
        if block["console"] is not None:
            _keys(block["console"], {"requests", "participants_using", "per_app"})
            for row in block["console"]["per_app"]:
                _keys(row, {"app_id", "label", "requests"})
    elif kind == "scenarios":
        _keys(block, {"type", "block_id", "title", "scenarios"})
        for scenario in block["scenarios"]:
            _keys(
                scenario,
                {
                    "scenario_id",
                    "title",
                    "n",
                    "success",
                    "partial",
                    "failure",
                    "not_attempted",
                    "success_rate",
                    "median_seconds",
                    "ease_mean",
                    "per_group",
                },
            )
            for row in scenario["per_group"]:
                _keys(row, {"group", "label", "n_apps", "n", "success_rate", "ci", "median_seconds"})
    elif kind == "questionnaire":
        _keys(block, {"type", "block_id", "title", "instrument", "score", "questions"})
        if block["score"] is not None:
            _keys(block["score"], {"per_app", "per_group"})
            _app_scores(block["score"]["per_app"])
            _group_scores(block["score"]["per_group"])
        for question in block["questions"]:
            _keys(question, {"question_id", "label", "kind", "n", "mean", "distribution"})
    elif kind == "bugs":
        _keys(block, {"type", "block_id", "title", "per_app", "per_group"})
        for row in block["per_app"]:
            _keys(
                row,
                {"app_id", "label", "group", "n_raters", "total", "per_rater_mean", "by_severity", "none_found"},
            )
        _group_scores(block["per_group"])
    elif kind == "pairwise":
        _keys(block, {"type", "block_id", "title", "criteria"})
        for criterion in block["criteria"]:
            _keys(
                criterion,
                {"criterion_id", "label", "n_comparisons", "by_group", "by_app", "position_bias", "agreement"},
            )
            for entry in [*criterion["by_group"], *criterion["by_app"]]:
                _keys(entry, BT_ENTRY)
                _ci(entry["ci"])
            _keys(criterion["position_bias"], {"left", "right", "ties", "p_value"})
            _agreement(criterion["agreement"])
    else:  # pragma: no cover - unknown block types fail the test
        pytest.fail(f"unknown block type {kind}")


def _no_key(value, name):
    if isinstance(value, dict):
        assert name not in value
        for item in value.values():
            _no_key(item, name)
    elif isinstance(value, list):
        for item in value:
            _no_key(item, name)


def check_shape(results):
    """Every field of TS ``TaskResults`` and nothing else; never a normalized ``severity`` key."""
    _keys(results, TASK_RESULTS)
    _keys(results["counts"], COUNTS)
    for warning in results["warnings"]:
        assert {"code", "message"} <= set(warning) <= {"code", "message", "block_id"}
    for group in results["groups"]:
        _keys(group, RESULT_GROUP)
    for app in results["apps"]:
        _keys(app, RESULT_APP)
    for block in results["blocks"]:
        _block(block)
    for row in results["quality"]:
        _keys(row, PARTICIPANT_QUALITY)
    for row in results["contrasts"]:
        _keys(row, CONTRAST)
        _ci(row["ci"])
    for row in results["human_vs_automated"]:
        _keys(row, HUMAN_VS_AUTOMATED)
        _ci(row["ci"])
    _no_key(results, "severity")
    json.dumps(results)  # serialisable as is


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _views(item):
    slots = ["single"] if item.app_b_id is None else ["A", "B"]
    return {slot: {"viewed_seconds": 120, "first_running_at": "2026-11-01T10:00:00+00:00"} for slot in slots}


def submit(task, item, participant, answers=None, *, swapped=False, context=None, **fields):
    now = timezone.now()
    values = {
        "status": S.SUBMITTED,
        "swapped": swapped,
        "started_at": now - timedelta(minutes=15),
        "submitted_at": now,
        "active_seconds": 900,
        "wall_seconds": 900,
        "app_views": _views(item),
        "context": context or {},
        **fields,
    }
    assignment = AssignmentFactory(task=task, item=item, participant=participant, **values)
    for block_id, block_answers in (answers or {}).items():
        Response.objects.create(assignment=assignment, block_id=block_id, answers=block_answers)
    return assignment


def skip(task, item, participant, reason, **fields):
    return AssignmentFactory(
        task=task,
        item=item,
        participant=participant,
        status=S.SKIPPED,
        skip_reason=reason,
        **fields,
    )


def raters(task, n, start=1):
    return [ParticipantFactory(task=task, number=start + i) for i in range(n)]


def apps_of(task):
    return list(EvalTaskApp.objects.filter(task=task).order_by("position"))


def single_items(task):
    return {item.app_a_id: item for item in EvalItem.objects.filter(task=task)}


def close(task, *, reopened=False):
    now = timezone.now()
    task.status = EvalTask.Status.CLOSED
    task.closed_at = now
    task.first_closed_at = now - timedelta(days=1) if reopened else now
    task.save(update_fields=["status", "closed_at", "first_closed_at"])


def ratings(functionality, usability, overall):
    return {"ratings": {"criteria": {"functionality": functionality, "usability": usability, "overall": overall}}}


def block_of(results, block_id):
    return next(block for block in results["blocks"] if block["block_id"] == block_id)


def by_app(rows, key="app_id"):
    return {row[key]: row for row in rows}


def design_with(blocks, primary, mode="single", **analysis):
    design = design_service.default_design(mode)
    design["blocks"] = blocks
    design["analysis"] = {**design["analysis"], "primary": primary, **analysis}
    return design


# ---------------------------------------------------------------------------
# Rating blocks
# ---------------------------------------------------------------------------

# rater → app index → (functionality, usability, overall); 1..7 scales
RATING_TABLE = [
    [(7, 6, 7), (3, 2, 3), (5, 5, 5), (1, "na", 2)],
    [(6, 6, 6), (4, 3, 3), (5, 4, 6), (2, 2, 1)],
    [(7, 7, 6), (3, 3, 4), (4, 5, 5), (1, 1, 1)],
]


@pytest.fixture
def rated_task():
    task = make_open_task(n_apps=4)
    items = single_items(task)
    apps = apps_of(task)
    people = raters(task, 3)
    for person, row in zip(people, RATING_TABLE, strict=True):
        for app, values in zip(apps, row, strict=True):
            submit(task, items[app.pk], person, ratings(*values))
    return task, apps, people


def _composite(values):
    parts = [(value - 1) / 6 for value in values if value != "na"]
    return sum(parts) / len(parts)


def test_rating_block_end_to_end(rated_task):
    task, apps, _people = rated_task
    results = compute_results(task)
    check_shape(results)
    assert results["provisional"] is True
    assert results["p_values_withheld"] is True
    assert "provisional" in results["deviations"]
    assert results["confirmatory"] is False
    assert results["counts"] == {
        "participants": 3,
        "participants_included": 3,
        "assignments_submitted": 12,
        "assignments_included": 12,
        "assignments_skipped": 0,
        "assignments_excluded": 0,
    }
    block = block_of(results, "ratings")
    functionality, usability, overall = block["criteria"]
    assert [c["criterion_id"] for c in block["criteria"]] == ["functionality", "usability", "overall"]

    rows = by_app(functionality["per_app"])
    first = rows[str(apps[0].pk)]
    assert first["n"] == 3
    assert first["mean"] == round(20 / 3, 4)
    assert first["median"] == 7
    assert first["sd"] == round(stats.sd([7, 6, 7]), 4)
    assert first["label"] == "App 01"
    last_usability = by_app(usability["per_app"])[str(apps[3].pk)]
    assert last_usability["n"] == 2
    assert last_usability["na_count"] == 1
    assert last_usability["mean"] == 1.5

    # groups: condition 1 = apps 1 and 3, condition 2 = apps 2 and 4; mean of app means; no CI below 3 apps
    groups = by_app(functionality["per_group"], key="label")
    cond1 = groups["Condition 1"]
    assert cond1["n_apps"] == 2
    assert cond1["n_ratings"] == 6
    assert cond1["mean"] == round((20 / 3 + 14 / 3) / 2, 4)
    assert cond1["ci"] is None
    assert cond1["floor"] == {"mean": cond1["mean"], "ci": None, "n_apps": 2}

    # agreement over apps, ordinal (7-point scale)
    units = [[7, 6, 7], [3, 4, 3], [5, 5, 4], [1, 2, 1]]
    assert functionality["agreement"] == {
        "alpha": round(stats.krippendorff_alpha(units, "ordinal"), 4),
        "level": "ordinal",
        "percent_agreement": round(stats.percent_agreement(units), 4),
        "n_units": 4,
        "n_values": 12,
    }
    assert overall["agreement"]["n_values"] == 12

    composite = by_app(block["composite"]["per_app"])
    expected = [_composite(RATING_TABLE[r][3]) for r in range(3)]
    assert composite[str(apps[3].pk)]["mean"] == round(sum(expected) / 3, 4)
    assert composite[str(apps[3].pk)]["na_count"] == 0

    assert [group["label"] for group in results["groups"]] == ["Condition 1", "Condition 2"]
    assert all(group["n_apps_pool"] == 2 and group["n_apps_rated"] == 2 for group in results["groups"])
    assert [app["label"] for app in results["apps"]] == ["App 01", "App 02", "App 03", "App 04"]
    assert results["apps"][0]["n_submitted"] == 3
    assert results["apps"][0]["job_id"] == str(apps[0].job_id)
    assert results["apps"][0]["condition_label"] == "Condition 1"

    hva = by_app(results["human_vs_automated"], key="metric")
    conformance = hva["composite_vs_conformance"]
    assert conformance["n"] == 4
    assert conformance["rho"] is not None
    static = hva["composite_vs_static_findings_per_kloc"]
    assert static["rho"] is None
    assert "not deduplicated" in static["note"]


def test_preview_participants_never_count(rated_task):
    task, apps, _people = rated_task
    baseline = compute_results(task)
    preview = ParticipantFactory(task=task, number=99, kind=Participant.Kind.PREVIEW)
    items = single_items(task)
    for app in apps:
        submit(task, items[app.pk], preview, ratings(1, 1, 1))
    skip(task, items[apps[0].pk], ParticipantFactory(task=task, number=98, kind=Participant.Kind.PREVIEW), "other")
    after = compute_results(task)
    assert after["counts"] == baseline["counts"]
    assert after["blocks"] == baseline["blocks"]
    assert after["quality"] == baseline["quality"]
    assert {row["participant_id"] for row in after["quality"]}.isdisjoint({str(preview.pk)})


def test_manual_exclusions_and_include_excluded(rated_task):
    task, apps, people = rated_task
    now = timezone.now()
    Participant.objects.filter(pk=people[2].pk).update(excluded=True, excluded_at=now, exclusion_reason="test")
    row = Assignment.objects.get(participant=people[0], item__app_a=apps[0])
    row.excluded = True
    row.excluded_at = now
    row.save(update_fields=["excluded", "excluded_at"])

    results = compute_results(task)
    check_shape(results)
    assert results["counts"]["assignments_included"] == 7
    assert results["counts"]["assignments_excluded"] == 5
    assert results["counts"]["participants_included"] == 2
    first = by_app(block_of(results, "ratings")["criteria"][0]["per_app"])[str(apps[0].pk)]
    assert first["n"] == 1
    assert first["mean"] == 6.0
    assert "post_hoc_exclusions" in results["deviations"]

    everything = compute_results(task, include_excluded=True)
    assert everything["include_excluded"] is True
    assert everything["counts"]["assignments_included"] == 12
    assert everything["counts"]["assignments_excluded"] == 5  # the plan's exclusions, reported in both views
    assert by_app(block_of(everything, "ratings")["criteria"][0]["per_app"])[str(apps[0].pk)]["n"] == 3
    excluded_row = next(row for row in everything["quality"] if row["participant_id"] == str(people[2].pk))
    assert excluded_row["excluded"] is True


ATTENTION = {
    "id": "attention_1",
    "kind": "attention",
    "label": "Choose 'Agree' to show you are reading.",
    "required": True,
    "options": [{"value": "agree", "label": "Agree"}, {"value": "disagree", "label": "Disagree"}],
    "expected": "agree",
}


def test_rule_exclusions_are_computed_not_stored():
    design = design_service.default_design("single")
    design["post_questions"] = [copy.deepcopy(ATTENTION)]
    design["analysis"]["exclusion_rules"] = {"speeding": True, "attention_failed": True, "straightlining": False}
    task = make_open_task(n_apps=3, design=design, settings={"quality": {"min_seconds_per_item": 120}})
    items = single_items(task)
    apps = apps_of(task)
    careful, speeder, inattentive = raters(task, 3)
    Participant.objects.filter(pk=inattentive.pk).update(post_answers={"attention_1": "disagree"})
    for app in apps:
        submit(task, items[app.pk], careful, ratings(5, 5, 5))
        submit(task, items[app.pk], inattentive, ratings(1, 1, 1))
    submit(task, items[apps[0].pk], speeder, ratings(7, 7, 7), active_seconds=20, wall_seconds=30)
    submit(task, items[apps[1].pk], speeder, ratings(7, 7, 7), active_seconds=20, wall_seconds=600)

    results = compute_results(task)
    check_shape(results)
    assert results["rule_exclusions"] == {"speeding": 1, "attention_failed": 3}
    assert results["counts"]["assignments_included"] == 4
    assert results["counts"]["assignments_excluded"] == 4
    first = by_app(block_of(results, "ratings")["criteria"][0]["per_app"])[str(apps[0].pk)]
    assert (first["n"], first["mean"]) == (1, 5.0)
    second = by_app(block_of(results, "ratings")["criteria"][0]["per_app"])[str(apps[1].pk)]
    assert (second["n"], second["mean"]) == (2, 6.0)  # slow wall time: not speeding
    # nothing was written to the rows
    assert not Assignment.objects.filter(task=task, excluded=True).exists()
    assert not Participant.objects.filter(task=task, excluded=True).exists()
    quality = by_app(results["quality"], key="participant_id")
    assert quality[str(speeder.pk)]["speeding"] == 1
    assert quality[str(inattentive.pk)]["attention_failed"] == 1
    assert quality[str(inattentive.pk)]["flags"] == ["attention_failed"]
    assert compute_results(task, include_excluded=True)["counts"]["assignments_included"] == 8


def test_rule_exclusions_skip_already_excluded_rows():
    design = design_service.default_design("single")
    design["analysis"]["exclusion_rules"] = {"speeding": True}
    task = make_open_task(n_apps=1, design=design, settings={"quality": {"min_seconds_per_item": 120}})
    person = raters(task, 1)[0]
    submit(task, next(iter(single_items(task).values())), person, ratings(5, 5, 5), active_seconds=1, wall_seconds=1)
    Participant.objects.filter(pk=person.pk).update(excluded=True)
    results = compute_results(task)
    assert results["rule_exclusions"] == {"speeding": 0}
    assert results["counts"]["assignments_excluded"] == 1


# ---------------------------------------------------------------------------
# Unevaluable apps and the floor policy
# ---------------------------------------------------------------------------


def _broken_app_task(policy):
    design = design_service.default_design("single")
    design["analysis"]["unevaluable_policy"] = policy
    task = make_open_task(n_apps=4, design=design)
    items = single_items(task)
    apps = apps_of(task)
    people = raters(task, 3)
    for person in people:
        for app, value in zip(apps[:3], (6, 4, 5), strict=True):
            submit(task, items[app.pk], person, ratings(value, value, value))
    skip(task, items[apps[3].pk], people[0], "app_broken")
    skip(task, items[apps[3].pk], people[1], "platform_unavailable")
    return task, apps


def test_unevaluable_apps_floor_sensitivity_with_exclude_policy():
    task, apps = _broken_app_task("exclude")
    results = compute_results(task)
    check_shape(results)
    cond2 = by_app(results["groups"], key="label")["Condition 2"]
    assert cond2 == {
        "key": cond2["key"],
        "label": "Condition 2",
        "n_apps_pool": 2,
        "n_apps_rated": 1,
        "n_apps_unevaluable": 1,
    }
    row = by_app(block_of(results, "ratings")["criteria"][0]["per_group"], key="label")["Condition 2"]
    assert row["mean"] == 4.0  # complete case: App 02 only
    assert row["n_apps"] == 1
    assert row["floor"] == {"mean": 2.5, "ci": None, "n_apps": 2}  # App 04 at the scale minimum 1
    composite = by_app(block_of(results, "ratings")["composite"]["per_group"], key="label")["Condition 2"]
    assert composite["floor"]["mean"] == round((0.5 + 0.0) / 2, 4)
    last = by_app(results["apps"])[str(apps[3].pk)]
    assert last["n_unavailable"] == 2
    assert last["n_submitted"] == 0
    assert "app_unavailable" in {warning["code"] for warning in results["warnings"]}


def test_floor_policy_drives_the_primary_mean():
    task, _apps = _broken_app_task("floor")
    row = by_app(block_of(compute_results(task), "ratings")["criteria"][0]["per_group"], key="label")["Condition 2"]
    assert row["mean"] == 2.5
    assert row["n_apps"] == 2
    assert row["floor"]["mean"] == 2.5


def test_platform_unavailable_alone_is_not_unevaluable():
    task = make_open_task(n_apps=2)
    items = single_items(task)
    apps = apps_of(task)
    person = raters(task, 1)[0]
    submit(task, items[apps[0].pk], person, ratings(5, 5, 5))
    skip(task, items[apps[1].pk], person, "platform_unavailable")
    results = compute_results(task)
    assert sum(group["n_apps_unevaluable"] for group in results["groups"]) == 0


# ---------------------------------------------------------------------------
# Confirmatory status
# ---------------------------------------------------------------------------


def _planned_task(**analysis):
    design = design_service.default_design("single")
    design["analysis"].update({"planned_ratings_per_item": 2, **analysis})
    task = make_open_task(n_apps=3, design=design, settings={"allocation": {"ratings_per_item": 2}})
    items = single_items(task)
    people = raters(task, 2)
    for person in people:
        for item in items.values():
            submit(task, item, person, ratings(5, 4, 6))
    return task, items, people


def test_confirmatory_when_the_plan_was_followed():
    task, _items, _people = _planned_task()
    close(task)
    results = compute_results(task)
    assert results["provisional"] is False
    assert results["p_values_withheld"] is False
    assert results["deviations"] == []
    assert results["confirmatory"] is True


def test_reopened_task_is_not_confirmatory():
    task, _items, _people = _planned_task()
    close(task, reopened=True)
    assert compute_results(task)["deviations"] == ["reopened_after_close"]
    task.status = EvalTask.Status.OPEN
    task.save(update_fields=["status"])
    assert compute_results(task)["deviations"] == ["provisional", "reopened_after_close"]


def test_stopped_early_and_broken_apps_count_toward_the_plan():
    task, items, people = _planned_task()
    close(task)
    victim = Assignment.objects.filter(task=task, participant=people[1]).first()
    victim.delete()
    assert compute_results(task)["deviations"] == ["stopped_early"]
    skip(task, victim.item, people[1], "app_broken")
    assert compute_results(task)["deviations"] == []
    del items


def test_post_hoc_exclusion_and_missing_primary():
    task, _items, people = _planned_task(primary=None)
    close(task)
    Participant.objects.filter(pk=people[0].pk).update(excluded=True, excluded_at=timezone.now())
    deviations = compute_results(task)["deviations"]
    assert "post_hoc_exclusions" in deviations
    assert "no_primary" in deviations
    assert "stopped_early" in deviations  # the excluded rater's ratings no longer count


def test_exclusion_before_open_is_not_post_hoc():
    task, _items, people = _planned_task()
    close(task)
    Assignment.objects.filter(task=task, participant=people[0]).update(
        excluded=True,
        excluded_at=task.opened_at - timedelta(hours=1),
    )
    assert "post_hoc_exclusions" not in compute_results(task)["deviations"]


def test_strategy_all_uses_planned_participants():
    design = design_service.default_design("single")
    task = make_open_task(n_apps=2, design=design, settings={"allocation": {"strategy": "all"}})
    items = single_items(task)
    people = raters(task, 2)
    for person in people:
        for item in items.values():
            submit(task, item, person, ratings(5, 5, 5))
    close(task)
    assert compute_results(task)["deviations"] == ["no_planned_sample"]
    task.design["analysis"]["planned_participants"] = 2
    task.save(update_fields=["design"])
    assert compute_results(task)["deviations"] == []
    task.design["analysis"]["planned_participants"] = 3
    task.save(update_fields=["design"])
    assert compute_results(task)["deviations"] == ["stopped_early"]


# ---------------------------------------------------------------------------
# Contrasts
# ---------------------------------------------------------------------------


def _contrast_task(n_apps, shift):
    """Condition 2 rates higher than condition 1 within each (app_slug, repeat) cell by ``shift[cell]``."""
    task = make_open_task(n_apps=n_apps)
    items = single_items(task)
    person = raters(task, 1)[0]
    for index, app in enumerate(apps_of(task)):
        cell = index // 2
        value = 3 + (shift[cell] if index % 2 else 0)
        submit(task, items[app.pk], person, ratings(value, value, value))
    return task


def test_paired_contrasts_with_wilcoxon_and_holm():
    task = _contrast_task(6, shift=[1, 2, 3])
    close(task)
    results = compute_results(task)
    check_shape(results)
    rows = results["contrasts"]
    assert [(row["block_id"], row["metric"]) for row in rows] == [
        ("ratings", "composite"),
        ("ratings", "functionality"),
        ("ratings", "usability"),
        ("ratings", "overall"),
    ]
    functionality = rows[1]
    assert functionality["paired"] is True
    assert functionality["n_cells"] == 3
    assert functionality["diff"] == 2.0
    assert functionality["group_label"] == "Condition 2"
    assert functionality["baseline_label"] == "Condition 1"
    assert functionality["p_value"] == pytest.approx(wilcoxon_p([1.0, 2.0, 3.0]))
    assert functionality["ci"] is not None
    assert functionality["primary"] is False
    assert rows[0]["primary"] is True  # design.analysis.primary = ratings / composite
    assert rows[0]["diff"] == round(2 / 6, 4)
    assert [row["p_adjusted"] for row in rows] == holm([wilcoxon_p([1.0, 2.0, 3.0])] * 4)


def test_contrast_p_values_are_withheld_while_provisional():
    task = _contrast_task(6, shift=[1, 2, 3])
    rows = compute_results(task)["contrasts"]
    assert rows
    assert all(row["p_value"] is None and row["p_adjusted"] is None for row in rows)
    assert rows[1]["diff"] == 2.0


def test_unpaired_contrast_without_three_common_cells():
    task = _contrast_task(6, shift=[1, 1, 1])
    for app in EvalTaskApp.objects.filter(task=task, position__in=(1, 3, 5)):
        app.snapshot = {**app.snapshot, "repeat_index": app.snapshot["repeat_index"] + 10}
        app.save(update_fields=["snapshot"])
    close(task)
    row = compute_results(task)["contrasts"][1]
    assert row["paired"] is False
    assert row["n_cells"] == 0
    assert row["p_value"] is None
    assert row["diff"] == 1.0
    assert row["ci"] == {"low": 1.0, "high": 1.0}


def test_no_baseline_means_no_contrasts():
    task = make_open_task(n_apps=2, conditions=0)
    items = single_items(task)
    person = raters(task, 1)[0]
    for item in items.values():
        submit(task, item, person, ratings(4, 4, 4))
    results = compute_results(task)
    assert results["contrasts"] == []
    assert "no_baseline" in {warning["code"] for warning in results["warnings"]}
    assert all(group["key"].startswith("model:") for group in results["groups"])
    by_model = compute_results(task, group_by="model")
    assert by_model["contrasts"] == []
    assert "no_baseline" not in {warning["code"] for warning in by_model["warnings"]}


def test_group_by_model_and_app(rated_task):
    task, apps, _people = rated_task
    by_model = compute_results(task, group_by="model")
    check_shape(by_model)
    assert by_model["group_by"] == "model"
    assert {group["key"] for group in by_model["groups"]} == {app.snapshot["model_id"] for app in apps}
    assert by_model["contrasts"] == []
    by_app_results = compute_results(task, group_by="app")
    assert [group["key"] for group in by_app_results["groups"]] == [str(app.pk) for app in apps]
    with pytest.raises(HumanEvalServiceError) as error:
        compute_results(task, group_by="spec")
    assert error.value.status == 400


# ---------------------------------------------------------------------------
# Pairwise
# ---------------------------------------------------------------------------


def test_canonical_orientation():
    assert canonical_score("A", "ab_tie", swapped=False) == 1
    assert canonical_score("A", "ab_tie", swapped=True) == -1
    assert canonical_score("B", "ab_tie", swapped=True) == 1
    assert canonical_score("tie", "ab_tie", swapped=True) == 0
    # five_point: -2 = displayed A much better
    assert canonical_score(-2, "five_point", swapped=False) == 2
    assert canonical_score(-2, "five_point", swapped=True) == -2
    assert canonical_score(1, "five_point", swapped=True) == 1
    assert canonical_score(3, "five_point", swapped=False) is None
    assert canonical_score("A", "five_point", swapped=False) is None


def _pairwise_task(scale="ab_tie"):
    design = design_service.default_design("pairwise")
    design["blocks"][0]["scale"] = scale
    task = make_open_task(n_apps=2, mode="pairwise", design=design)
    item = EvalItem.objects.get(task=task)
    return task, item


def test_pairwise_maps_swapped_sides_back_to_apps():
    task, item = _pairwise_task()
    people = raters(task, 4)
    # (swapped, displayed answer): app_a wins, app_a wins (shown on the right), app_b wins (shown left), tie
    votes = [(False, "A"), (True, "B"), (True, "A"), (False, "tie")]
    for person, (swapped, value) in zip(people, votes, strict=True):
        submit(task, item, person, {"preference": {"criteria": {}, "overall": value}}, swapped=swapped)
    results = compute_results(task)
    check_shape(results)
    overall = block_of(results, "preference")["criteria"][0]
    assert overall["criterion_id"] == "overall"
    assert overall["n_comparisons"] == 4
    assert overall["position_bias"] == {"left": 2, "right": 1, "ties": 1, "p_value": None}
    entries = by_app(overall["by_app"], key="key")
    app_a, app_b = entries[str(item.app_a_id)], entries[str(item.app_b_id)]
    assert (app_a["wins"], app_a["losses"], app_a["ties"], app_a["comparisons"]) == (2, 1, 1, 4)
    assert (app_b["wins"], app_b["losses"], app_b["ties"]) == (1, 2, 1)
    assert app_a["strength"] > 1 > app_b["strength"]
    assert app_a["strength"] * app_b["strength"] == pytest.approx(1.0, abs=1e-3)
    assert app_a["component"] == app_b["component"] == "1"
    assert (app_a["rank"], app_b["rank"]) == (1, 2)
    assert app_a["ci"] is None  # one item: too few clusters to bootstrap
    assert app_a["log_strength"] == pytest.approx(math.log(app_a["strength"]), abs=1e-3)
    # canonical values [1, 1, -1, 0] on one item: ordinal agreement
    assert overall["agreement"]["level"] == "ordinal"
    assert overall["agreement"]["n_units"] == 1
    assert overall["agreement"]["alpha"] == round(stats.krippendorff_alpha([[1, 1, -1, 0]], "ordinal"), 4)
    groups = by_app(overall["by_group"], key="key")
    assert len(groups) == 2
    winner = next(app for app in apps_of(task) if app.pk == item.app_a_id)
    assert groups[str(winner.snapshot["condition_id"])]["wins"] == 2

    close(task)
    closed = block_of(compute_results(task), "preference")["criteria"][0]
    assert closed["position_bias"]["p_value"] == 1.0  # exact binomial: 2 left vs 1 right


def test_pairwise_five_point_uses_magnitude_in_canonical_orientation():
    task, item = _pairwise_task("five_point")
    first, second = raters(task, 2)
    submit(task, item, first, {"preference": {"criteria": {}, "overall": -2}}, swapped=False)
    submit(task, item, second, {"preference": {"criteria": {}, "overall": -1}}, swapped=True)
    overall = block_of(compute_results(task), "preference")["criteria"][0]
    assert overall["position_bias"] == {"left": 2, "right": 0, "ties": 0, "p_value": None}
    # canonical values on the item: +2 (app_a much better) and -1 (app_b better): no agreement beyond chance
    assert overall["agreement"]["alpha"] == 0.0
    entries = by_app(overall["by_app"], key="key")
    assert entries[str(item.app_a_id)]["wins"] == 1
    assert entries[str(item.app_b_id)]["wins"] == 1


def test_bradley_terry_by_app_is_per_connected_component():
    task = make_open_task(n_apps=4, mode="pairwise")
    apps = apps_of(task)
    EvalItem.objects.filter(task=task).delete()
    pair_1 = EvalItem.objects.create(task=task, app_a=apps[0], app_b=apps[1], position=0)
    pair_2 = EvalItem.objects.create(task=task, app_a=apps[2], app_b=apps[3], position=1)
    people = raters(task, 3)
    for person in people:
        submit(task, pair_1, person, {"preference": {"criteria": {}, "overall": "A"}})
        submit(task, pair_2, person, {"preference": {"criteria": {}, "overall": "B"}})
    overall = block_of(compute_results(task), "preference")["criteria"][0]
    entries = by_app(overall["by_app"], key="key")
    assert {entry["component"] for entry in overall["by_app"]} == {"1", "2"}
    assert entries[str(apps[0].pk)]["component"] == entries[str(apps[1].pk)]["component"] == "1"
    assert entries[str(apps[0].pk)]["rank"] == 1
    assert entries[str(apps[3].pk)]["rank"] == 1  # ranks restart within each component
    assert entries[str(apps[2].pk)]["rank"] == 2


def test_bradley_terry_cluster_bootstrap_ci_is_seeded():
    task = make_open_task(n_apps=4, mode="pairwise")
    items = list(EvalItem.objects.filter(task=task).order_by("position"))
    people = raters(task, 2)
    for index, item in enumerate(items):
        for person in people:
            submit(task, item, person, {"preference": {"criteria": {}, "overall": "A" if index % 2 else "B"}})
    first = block_of(compute_results(task), "preference")["criteria"][0]
    second = block_of(compute_results(task), "preference")["criteria"][0]
    assert len(items) >= 3
    assert all(entry["ci"] is not None for entry in first["by_app"])
    assert first["by_app"] == second["by_app"]
    for entry in first["by_app"]:
        assert entry["ci"]["low"] <= entry["log_strength"] <= entry["ci"]["high"]


def test_pairwise_skip_blames_the_failed_slot():
    task, item = _pairwise_task()
    person = raters(task, 1)[0]
    skip(
        task,
        item,
        person,
        "app_broken",
        swapped=False,
        context={"skip_hosting": {"A": {"state": "running", "error": ""}, "B": {"state": "failed", "error": "crash"}}},
    )
    results = compute_results(task)
    unavailable = {app["app_id"]: app["n_unavailable"] for app in results["apps"]}
    assert unavailable == {str(item.app_a_id): 0, str(item.app_b_id): 1}
    assert sum(group["n_apps_unevaluable"] for group in results["groups"]) == 1


# ---------------------------------------------------------------------------
# Requirements
# ---------------------------------------------------------------------------

REQUIREMENT_ITEMS = [
    {"id": "b_0000000001", "source": "backend", "text": "Users can register and log in"},
    {"id": "ep_0000000001", "source": "api", "text": "GET /api/items — List the user's items"},
    {"id": "ep_0000000002", "source": "api", "text": "POST /api/items — Create an item"},
    {"id": "aep_000000001", "source": "admin_api", "text": "GET /api/admin/users — List all users"},
    {"id": "c_footer", "source": "custom", "text": "Has a footer"},
]


def _requirements_context():
    return {"blocks": {"requirements": {"requirements": {"single": copy.deepcopy(REQUIREMENT_ITEMS)}}}}


def _verdicts(*values):
    return {
        "requirements": {
            "items": {
                item["id"]: {"verdict": verdict}
                for item, verdict in zip(REQUIREMENT_ITEMS, values, strict=True)
                if verdict
            },
        },
    }


@pytest.fixture
def requirements_task():
    block = {
        "id": "requirements",
        "type": "requirements",
        "title": "Requirements",
        "sources": ["backend", "api", "admin_api"],
        "custom": [{"id": "footer", "text": "Has a footer"}],
        "note": "optional",
    }
    task = make_open_task(n_apps=4, design=design_with([block], {"block_id": "requirements", "metric": "compliance"}))
    items = single_items(task)
    apps = apps_of(task)
    first, second = raters(task, 2)
    table = {
        0: (("met", "met", "not_met", "met", "met"), ("met", "met", "partial", "met", "cannot_test")),
        1: (("met", "met", "met", "not_met", "met"), ("partial", "met", "met", "not_met", "met")),
        2: (("not_met", "blocked", "met", "met", "met"), ("met", "met", "not_met", "met", None)),
        3: (("met", "met", "met", "met", "met"), ("met", "met", "met", "met", "met")),
    }
    for index, (one, two) in table.items():
        item = items[apps[index].pk]
        submit(task, item, first, _verdicts(*one), context=_requirements_context())
        submit(task, item, second, _verdicts(*two), context=_requirements_context())
    return task, apps


def test_requirements_compliance_agreement_and_conformance(requirements_task):
    task, apps = requirements_task
    results = compute_results(task)
    check_shape(results)
    block = block_of(results, "requirements")
    rows = by_app(block["per_app"])
    first = rows[str(apps[0].pk)]
    # rater 1: 4 of 5 met = 0.8; rater 2: met, met, partial, met (cannot_test excluded) = 3.5 / 4
    assert first["compliance"] == round((0.8 + 3.5 / 4) / 2, 4)
    assert first["compliance_strict"] == round((0.8 + 3.5 / 5) / 2, 4)
    assert first["cannot_test"] == 1
    assert first["n_raters"] == 2
    assert first["by_source"] == {"backend": 1.0, "api": round((0.5 + 0.75) / 2, 4), "admin_api": 1.0, "custom": 1.0}
    assert first["conformance_pass_rate"] == 1.0
    assert first["conformance_endpoint_pass_rate"] == 1.0
    assert rows[str(apps[1].pk)]["conformance_endpoint_pass_rate"] == 0.75  # odd apps fail the admin check
    assert rows[str(apps[2].pk)]["blocked"] == 1
    assert rows[str(apps[0].pk)]["conformance_status"] == "passed"

    per_requirement = by_app(block["per_requirement"], key="req_id")
    assert [row["req_id"] for row in block["per_requirement"]] == [item["id"] for item in REQUIREMENT_ITEMS]
    footer = per_requirement["c_footer"]
    assert (footer["n_ratings"], footer["met"], footer["cannot_test"], footer["source"]) == (7, 6, 1, "custom")
    assert per_requirement["ep_0000000001"]["blocked"] == 1

    assert block["agreement"]["level"] == "ordinal"
    assert block["agreement"]["n_units"] > 0
    group = block["per_group"][0]
    assert group["floor"]["mean"] == group["mean"]  # no unevaluable app

    # endpoint majority (met = pass; partial / not met / blocked = fail) vs the matching check; ties unmatched.
    # App 01: POST fails for both raters but passes the suite (auto_only); App 02: the admin endpoint fails for
    # both (both_fail); App 03: two tied endpoints; App 04: the admin endpoint passes for humans only.
    agreement = block["endpoint_agreement"]
    assert agreement == {
        "n": 10,
        "kappa": 0.375,  # observed 0.8, expected (8*8 + 2*2) / 100 = 0.68
        "both_pass": 7,
        "human_only": 1,
        "auto_only": 1,
        "both_fail": 1,
        "n_unmatched": 2,
    }

    scopes = by_app(block["vs_conformance"], key="scope")
    assert set(scopes) == {"endpoints", "overall"}
    assert scopes["endpoints"]["n"] == 4
    assert scopes["endpoints"]["excluded_apps"] == []
    assert "Primary" in scopes["endpoints"]["note"]
    assert "different constructs" in scopes["overall"]["note"]


def test_vs_conformance_keeps_the_modal_evaluator_version(requirements_task):
    task, apps = requirements_task
    odd = apps[3]
    odd.snapshot = {**odd.snapshot, "evaluator_version": "2"}
    odd.save(update_fields=["snapshot"])
    results = compute_results(task)
    scopes = by_app(block_of(results, "requirements")["vs_conformance"], key="scope")
    assert scopes["endpoints"]["n"] == 3
    assert scopes["endpoints"]["excluded_apps"] == ["App 04"]
    assert "1 another evaluator version" in scopes["endpoints"]["note"]
    assert "mixed_evaluator_versions" in {warning["code"] for warning in results["warnings"]}


def test_requirements_without_frozen_context_fall_back_to_answers():
    block = {"id": "requirements", "type": "requirements", "title": "Requirements", "sources": ["backend"]}
    task = make_open_task(n_apps=1, design=design_with([block], {"block_id": "requirements", "metric": "compliance"}))
    item = next(iter(single_items(task).values()))
    submit(task, item, raters(task, 1)[0], {"requirements": {"items": {"b_x": {"verdict": "partial"}}}})
    result = block_of(compute_results(task), "requirements")
    assert result["per_app"][0]["compliance"] == 0.5
    assert result["per_requirement"][0]["source"] == "backend"
    assert result["endpoint_agreement"] is None


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

SECURITY_BLOCK = {
    "id": "security",
    "type": "security",
    "title": "Security",
    "checklist": [
        {"id": "injection", "label": "Injection", "category": "A03:2021 Injection"},
        {"id": "xss", "label": "XSS", "category": "A03:2021 Injection"},
    ],
    "reports": True,
    "finding_review": {"enabled": True, "max_findings": 10},
    "http_console": True,
}

FINDINGS = [
    {"id": "f1", "tool": "bandit", "tool_name": "Bandit", "tool_severity": "HIGH", "title": "exec"},
    {"id": "f2", "tool": "bandit", "tool_name": "Bandit", "tool_severity": "LOW", "title": "assert"},
    {"id": "f3", "tool": "semgrep", "tool_name": "Semgrep", "tool_severity": "", "title": "xss"},
]


def _security_context(findings):
    return {"blocks": {"security": {"findings": {"single": copy.deepcopy(findings)}}}}


def _checklist(injection, xss):
    return {"security": {"checklist": {"injection": {"outcome": injection}, "xss": {"outcome": xss}}}}


def test_security_checklist_reports_console_and_finding_precision():
    design = design_with([copy.deepcopy(SECURITY_BLOCK)], {"block_id": "security", "metric": "vulnerable_share"})
    task = make_open_task(n_apps=3, conditions=1, design=design)
    items = single_items(task)
    apps = apps_of(task)
    EvalTaskApp.objects.filter(pk=apps[0].pk).update(
        finding_frame={"bandit": {"population": 10, "sampled": 2}, "semgrep": {"population": 2, "sampled": 2}},
    )
    first, second = raters(task, 2)
    outcomes = {
        0: (("vulnerable", "not_tested"), ("not_vulnerable", "vulnerable")),
        1: (("not_tested", "not_vulnerable"), ("vulnerable", "not_vulnerable")),
        2: (("not_vulnerable", "not_vulnerable"), ("not_vulnerable", "not_vulnerable")),
    }
    rows = {}
    for index, (one, two) in outcomes.items():
        context = _security_context(FINDINGS if index == 0 else [])
        rows[(index, 1)] = submit(task, items[apps[index].pk], first, _checklist(*one), context=context)
        rows[(index, 2)] = submit(task, items[apps[index].pk], second, _checklist(*two), context=context)
    normalized = {"severity": "critical"}  # a normalized severity that must never surface
    for finding_key, verdict in (("f1", "tp"), ("f2", "fp"), ("f3", "tp")):
        FindingVerdictFactory(
            assignment=rows[(0, 1)],
            task_app=apps[0],
            finding_key=finding_key,
            verdict=verdict,
            finding_snapshot=normalized,
        )
    for finding_key, verdict in (("f1", "tp"), ("f2", "tp"), ("f3", "fp"), ("f9", "fp")):
        FindingVerdictFactory(assignment=rows[(0, 2)], task_app=apps[0], finding_key=finding_key, verdict=verdict)
    ObservationFactory(
        assignment=rows[(0, 1)],
        task_app=apps[0],
        kind=Observation.Kind.VULNERABILITY,
        block_id="security",
        severity="high",
        category="A03",
    )
    ObservationFactory(assignment=rows[(0, 2)], task_app=apps[0], kind=Observation.Kind.VULNERABILITY, severity="")
    ObservationFactory(
        assignment=rows[(2, 1)],
        task_app=apps[2],
        kind=Observation.Kind.VULNERABILITY,
        block_id="security",
        severity="low",
    )
    for _ in range(3):
        HttpExchangeFactory(assignment=rows[(0, 1)], task_app=apps[0])

    results = compute_results(task)
    check_shape(results)
    assert "critical" not in json.dumps(results)
    block = block_of(results, "security")

    injection = block["checklist"][0]
    group = injection["per_group"][0]
    # app shares: App 01 1/2, App 02 1/1 (not_tested dropped), App 03 0/2 → mean 0.5 over 3 apps
    assert (group["vulnerable"], group["not_vulnerable"], group["not_tested"], group["n"]) == (2, 3, 1, 6)
    assert group["vulnerable_share"] == 0.5
    assert group["n_apps"] == 3
    assert group["ci"] is not None
    assert injection["agreement"]["level"] == "nominal"
    assert injection["category"] == "A03:2021 Injection"

    reports = block["reports"]
    per_app = by_app(reports["per_app"])
    assert per_app[str(apps[0].pk)]["total"] == 2
    assert per_app[str(apps[0].pk)]["by_severity"] == {"high": 1, "unrated": 1}
    assert per_app[str(apps[0].pk)]["by_category"] == {"A03": 1, "uncategorized": 1}
    assert reports["per_group"][0]["per_rater_mean"] == round((1 + 0 + 0.5) / 3, 4)
    assert reports["per_group"][0]["by_severity"] == {"high": 1, "low": 1, "unrated": 1}

    review = block["finding_review"]
    assert review["n_findings"] == 3  # f9 was not in the rater's context
    assert review["n_verdicts"] == 6
    overall = review["overall"]
    # soft labels f1 = 1, f2 = 0.5, f3 = 0.5; weights bandit 10/2 = 5, semgrep 2/2 = 1
    assert overall["precision"] == round((5 * 1 + 5 * 0.5 + 1 * 0.5) / 11, 4)
    assert overall["weighted"] is True
    assert overall["n_split"] == 2
    assert (overall["tp"], overall["fp"], overall["unsure"]) == (4, 2, 0)
    assert overall["majority_precision"] == 1.0  # only f1 has a majority
    assert overall["ci"] is None  # one app
    tools = by_app(review["per_tool"], key="key")
    assert tools["bandit"]["precision"] == 0.75
    assert tools["bandit"]["weighted"] is False
    assert tools["bandit"]["label"] == "Bandit"
    assert tools["semgrep"]["precision"] == 0.5
    levels = by_app(review["per_tool_severity"], key="key")
    assert set(levels) == {"HIGH", "LOW", "unrated"}
    assert levels["HIGH"]["precision"] == 1.0
    assert levels["unrated"]["precision"] == 0.5
    assert review["agreement"]["n_units"] == 3

    assert block["console"]["requests"] == 3
    assert block["console"]["participants_using"] == 1
    assert by_app(block["console"]["per_app"])[str(apps[0].pk)]["requests"] == 3


def test_precision_is_unweighted_without_a_complete_sampling_frame():
    design = design_with([copy.deepcopy(SECURITY_BLOCK)], {"block_id": "security", "metric": "vulnerable_share"})
    task = make_open_task(n_apps=1, conditions=1, design=design)
    item = next(iter(single_items(task).values()))
    app = apps_of(task)[0]
    EvalTaskApp.objects.filter(pk=app.pk).update(finding_frame={"bandit": {"population": 10, "sampled": 2}})
    row = submit(
        task,
        item,
        raters(task, 1)[0],
        _checklist("not_tested", "not_tested"),
        context=_security_context(FINDINGS),
    )
    for finding_key, verdict in (("f1", "tp"), ("f2", "fp"), ("f3", "unsure")):
        FindingVerdictFactory(assignment=row, task_app=app, finding_key=finding_key, verdict=verdict)
    overall = block_of(compute_results(task), "security")["finding_review"]["overall"]
    assert overall["weighted"] is False  # semgrep has no frame entry
    assert overall["precision"] == 0.5
    assert overall["unsure"] == 1


# ---------------------------------------------------------------------------
# Scenarios, questionnaires, bugs
# ---------------------------------------------------------------------------


def test_scenarios_block():
    block = {
        "id": "scenarios",
        "type": "scenarios",
        "title": "Tasks",
        "scenarios": [{"id": "signup", "title": "Sign up"}, {"id": "create", "title": "Create an item"}],
    }
    task = make_open_task(n_apps=2, design=design_with([block], {"block_id": "scenarios", "metric": "success_rate"}))
    items = single_items(task)
    apps = apps_of(task)
    first, second = raters(task, 2)

    def answers(signup, create):
        return {"scenarios": {"scenarios": {"signup": signup, "create": create}}}

    submit(
        task,
        items[apps[0].pk],
        first,
        answers({"outcome": "success", "seconds": 40, "ease": 6}, {"outcome": "failure", "seconds": 300}),
    )
    submit(
        task,
        items[apps[0].pk],
        second,
        answers({"outcome": "partial", "seconds": 80, "ease": 4}, {"outcome": "not_attempted"}),
    )
    submit(
        task,
        items[apps[1].pk],
        first,
        answers({"outcome": "success", "seconds": 20, "ease": 7}, {"outcome": "success", "seconds": 60}),
    )
    results = compute_results(task)
    check_shape(results)
    signup, create = block_of(results, "scenarios")["scenarios"]
    assert (signup["n"], signup["success"], signup["partial"]) == (3, 2, 1)
    assert signup["success_rate"] == round(2 / 3, 4)  # pooled, descriptive
    assert signup["median_seconds"] == 40.0
    assert signup["ease_mean"] == round(17 / 3, 4)
    assert create["not_attempted"] == 1
    assert create["success_rate"] == 0.5
    per_group = by_app(signup["per_group"], key="label")
    assert per_group["Condition 1"]["success_rate"] == 0.5  # App 01: 1 success of 2 attempts
    assert per_group["Condition 2"]["success_rate"] == 1.0


def test_questionnaire_standard_and_custom():
    survey = {
        "id": "survey",
        "type": "questionnaire",
        "title": "Survey",
        "instrument": "custom",
        "questions": [
            {
                "id": "liked",
                "kind": "single",
                "label": "Liked it?",
                "required": True,
                "options": [{"value": "yes", "label": "Yes"}, {"value": "no", "label": "No"}],
            },
            {
                "id": "features",
                "kind": "multi",
                "label": "Features used",
                "required": False,
                "options": [{"value": "list", "label": "List"}, {"value": "edit", "label": "Edit"}],
            },
            {"id": "stars", "kind": "scale", "label": "Stars", "required": True, "scale": {"min": 1, "max": 5}},
            {"id": "notes", "kind": "text", "label": "Notes", "required": False},
        ],
    }
    sus = {"id": "sus", "type": "questionnaire", "title": "SUS", "instrument": "sus", "questions": []}
    task = make_open_task(n_apps=1, design=design_with([sus, survey], {"block_id": "sus", "metric": "score"}))
    item = next(iter(single_items(task).values()))
    first, second = raters(task, 2)
    neutral = {f"sus_{n}": 3 for n in range(1, 11)}
    best = {f"sus_{n}": 5 if n % 2 else 1 for n in range(1, 11)}
    submit(
        task,
        item,
        first,
        {
            "sus": {"answers": neutral},
            "survey": {"answers": {"liked": "yes", "features": ["list", "edit"], "stars": 4, "notes": "ok"}},
        },
    )
    submit(
        task,
        item,
        second,
        {"sus": {"answers": best}, "survey": {"answers": {"liked": "no", "features": [], "stars": 2, "notes": ""}}},
    )
    results = compute_results(task)
    check_shape(results)
    sus_result = block_of(results, "sus")
    assert sus_result["instrument"] == "sus"
    assert sus_result["score"]["per_app"][0]["mean"] == 75.0  # (50 + 100) / 2
    assert sus_result["score"]["per_group"][0]["floor"] is None
    assert len(sus_result["questions"]) == 10
    assert sus_result["questions"][0]["distribution"] == {"3": 1, "5": 1}
    custom = by_app(block_of(results, "survey")["questions"], key="question_id")
    assert block_of(results, "survey")["score"] is None
    assert custom["liked"]["distribution"] == {"no": 1, "yes": 1}
    assert custom["features"] == {
        "question_id": "features",
        "label": "Features used",
        "kind": "multi",
        "n": 1,
        "mean": None,
        "distribution": {"edit": 1, "list": 1},
    }
    assert custom["stars"]["mean"] == 3.0
    assert (custom["notes"]["n"], custom["notes"]["distribution"]) == (1, {})


def test_bugs_block_single_and_pairwise_slots():
    bugs = {"id": "bugs", "type": "bugs", "title": "Bugs", "min_reports": 0}
    task = make_open_task(n_apps=2, design=design_with([bugs], {"block_id": "bugs", "metric": "bugs_per_rater"}))
    items = single_items(task)
    apps = apps_of(task)
    first, second = raters(task, 2)
    row = submit(task, items[apps[0].pk], first, {"bugs": {"none_found": {}}})
    ObservationFactory(assignment=row, task_app=apps[0], kind=Observation.Kind.BUG, severity="high")
    ObservationFactory(assignment=row, task_app=apps[0], kind=Observation.Kind.BUG, severity="")
    ObservationFactory(assignment=row, task_app=apps[0], kind=Observation.Kind.NOTE)
    submit(task, items[apps[0].pk], second, {"bugs": {"none_found": {"single": True}}})
    results = compute_results(task)
    check_shape(results)
    first_app = by_app(block_of(results, "bugs")["per_app"])[str(apps[0].pk)]
    assert first_app == {
        "app_id": str(apps[0].pk),
        "label": "App 01",
        "group": first_app["group"],
        "n_raters": 2,
        "total": 2,
        "per_rater_mean": 1.0,
        "by_severity": {"high": 1, "unrated": 1},
        "none_found": 1,
    }

    pairwise = make_open_task(n_apps=2, mode="pairwise")
    design = pairwise.design
    design["blocks"].append(copy.deepcopy(bugs))
    pairwise.save(update_fields=["design"])
    item = EvalItem.objects.get(task=pairwise)
    person = raters(pairwise, 1)[0]
    shown_left = EvalTaskApp.objects.get(pk=item.app_b_id)
    row = submit(
        pairwise,
        item,
        person,
        {"bugs": {"none_found": {"A": True, "B": False}}, "preference": {"criteria": {}, "overall": "A"}},
        swapped=True,
    )
    ObservationFactory(assignment=row, task_app=item.app_a, kind=Observation.Kind.BUG)
    result = by_app(block_of(compute_results(pairwise), "bugs")["per_app"])
    assert result[str(shown_left.pk)]["none_found"] == 1  # swapped: displayed A is item.app_b
    assert result[str(item.app_a_id)]["none_found"] == 0
    assert result[str(item.app_a_id)]["total"] == 1


# ---------------------------------------------------------------------------
# Warnings and the quality table
# ---------------------------------------------------------------------------


def _codes(results):
    return {warning["code"] for warning in results["warnings"]}


def test_validity_warnings(rated_task):
    task, apps, people = rated_task
    results = compute_results(task)
    codes = _codes(results)
    assert {"provisional", "low_n", "shared_app_state"} <= codes  # 4 apps < 5; reset "none" with 3 raters per app
    assert "instructions_changed" not in codes
    Assignment.objects.filter(participant=people[0]).update(context={"instructions_sha256": "a" * 64})
    Assignment.objects.filter(participant=people[1]).update(context={"instructions_sha256": "b" * 64})
    assert "instructions_changed" in _codes(compute_results(task))
    del apps


def test_unbalanced_coverage_and_low_agreement():
    task = make_open_task(n_apps=6)
    items = single_items(task)
    apps = apps_of(task)
    people = raters(task, 4)
    # five apps with two raters who disagree completely; the sixth gets four raters
    for index, app in enumerate(apps[:5]):
        submit(task, items[app.pk], people[0], ratings(1 + index, 7, 1))
        submit(task, items[app.pk], people[1], ratings(7 - index, 1, 7))
    for person in people:
        submit(task, items[apps[5].pk], person, ratings(4, 4, 4))
    results = compute_results(task)
    codes = _codes(results)
    assert "low_agreement" in codes
    low = next(warning for warning in results["warnings"] if warning["code"] == "low_agreement")
    assert low["block_id"] == "ratings"
    assert "unbalanced_coverage" not in codes  # 2..4 is within a factor of two
    extra = raters(task, 1, start=10)[0]
    submit(task, items[apps[5].pk], extra, ratings(4, 4, 4))
    assert "unbalanced_coverage" in _codes(compute_results(task))


def test_quality_table(rated_task):
    task, apps, people = rated_task
    row = Assignment.objects.get(participant=people[0], item__app_a=apps[0])
    row.active_seconds = 10
    row.wall_seconds = 10
    row.save(update_fields=["active_seconds", "wall_seconds"])
    skip(task, EvalItem.objects.get(task=task, app_a=apps[1]), ParticipantFactory(task=task, number=4), "other")
    quality = by_app(compute_results(task)["quality"], key="participant_id")
    first = quality[str(people[0].pk)]
    assert first["label"] == "P001"
    assert first["submitted"] == 4
    assert first["speeding"] == 1
    assert first["flags"] == ["speeding"]
    assert first["median_seconds"] == 900.0
    assert first["kind"] == "invite"
    assert [row["label"] for row in quality.values()] == ["P001", "P002", "P003", "P004"]
    assert quality[next(key for key, value in quality.items() if value["label"] == "P004")]["skipped"] == 1


def test_finding_review_never_reads_the_normalized_severity():
    design = design_with([copy.deepcopy(SECURITY_BLOCK)], {"block_id": "security", "metric": "vulnerable_share"})
    task = make_open_task(n_apps=1, conditions=1, design=design)
    item = next(iter(single_items(task).values()))
    app = apps_of(task)[0]
    finding = FindingFactory(severity="critical", tool_severity="warning", result__tool_slug="eslint")
    shown = [{"id": str(finding.pk), "tool": "eslint", "tool_name": "ESLint", "tool_severity": "warning"}]
    row = submit(
        task,
        item,
        raters(task, 1)[0],
        _checklist("not_tested", "not_tested"),
        context=_security_context(shown),
    )
    FindingVerdictFactory(assignment=row, task_app=app, finding=finding, finding_key=str(finding.pk), verdict="tp")
    results = compute_results(task)
    check_shape(results)
    review = block_of(results, "security")["finding_review"]
    assert [entry["key"] for entry in review["per_tool_severity"]] == ["warning"]
    assert "critical" not in json.dumps(results)


def test_vs_conformance_reasons_for_apps_without_a_graded_conformance(requirements_task):
    task, apps = requirements_task
    smoke = apps[0]
    smoke.snapshot = {**smoke.snapshot, "functional": {"source": "smoke", "pass_rate": 1.0, "status": None}}
    smoke.save(update_fields=["snapshot"])
    scopes = by_app(block_of(compute_results(task), "requirements")["vs_conformance"], key="scope")
    assert scopes["overall"]["excluded_apps"] == ["App 01"]
    assert "1 no conformance result" in scopes["overall"]["note"]
    assert scopes["overall"]["n"] == 3


def test_endpoint_key_parsing():
    assert analysis._endpoint_key("GET /api/items — List the user's items") == "GET /api/items"
    assert analysis._endpoint_key("DELETE /api/items/<id>") == "DELETE /api/items/<id>"
    assert analysis._endpoint_key("POST /api/x - with a hyphen") == "POST /api/x"
    assert analysis._endpoint_key("Users can log in") == "Users can log in"


def test_contrasts_follow_the_unevaluable_policy():
    excluded = compute_results(_broken_app_task("exclude")[0])["contrasts"]
    floored = compute_results(_broken_app_task("floor")[0])["contrasts"]
    pick = {row["metric"]: row for row in excluded}
    assert pick["functionality"]["paired"] is False  # two common cells only
    assert pick["functionality"]["diff"] == round(4 - 5.5, 4)  # App 02 vs mean(App 01, App 03)
    assert {row["metric"]: row for row in floored}["functionality"]["diff"] == round((4 + 1) / 2 - 5.5, 4)


def test_pairwise_contrasts_use_preference_shares():
    task = make_open_task(n_apps=6, mode="pairwise")
    person = raters(task, 1)[0]
    baseline = {str(app.pk) for app in apps_of(task) if app.position % 2 == 0}  # condition 1 (baseline)
    for item in EvalItem.objects.filter(task=task):
        a_is_baseline = str(item.app_a_id) in baseline
        submit(task, item, person, {"preference": {"criteria": {}, "overall": "B" if a_is_baseline else "A"}})
    close(task)
    rows = {row["metric"]: row for row in compute_results(task)["contrasts"]}
    overall = rows["overall"]
    assert overall["primary"] is True
    assert overall["paired"] is True
    assert overall["n_cells"] == 3
    assert overall["diff"] == 1.0  # condition 2 won every comparison: share 1 vs 0
    assert overall["p_value"] == pytest.approx(wilcoxon_p([1.0, 1.0, 1.0]))
    assert "preference share" in overall["label"]


@pytest.mark.parametrize("mode", ["single", "pairwise"])
def test_empty_draft_task_has_the_full_shape(mode):
    task = EvalTaskFactory(mode=mode)
    results = compute_results(task)
    check_shape(results)
    assert results["groups"] == []
    assert results["apps"] == []
    assert results["contrasts"] == []
    assert results["deviations"][0] == "provisional"
    assert len(results["blocks"]) == 1
    assert "low_n" in {warning["code"] for warning in results["warnings"]}


# ---------------------------------------------------------------------------
# Review regressions: endpoint scope, float ties, frozen plan, deletions, drift, skipped resets
# ---------------------------------------------------------------------------


def _requirements_design(sources):
    block = {"id": "requirements", "type": "requirements", "title": "Requirements", "sources": sources}
    return design_with([block], {"block_id": "requirements", "metric": "compliance"})


def test_endpoint_pass_rate_covers_only_the_endpoint_sources_shown(monkeypatch):
    """C19: a block without admin_api compares public-endpoint compliance with the api checks only."""
    task = make_open_task(n_apps=4, design=_requirements_design(["backend", "api"]))
    items = single_items(task)
    apps = apps_of(task)
    person = raters(task, 1)[0]
    shown = [item for item in REQUIREMENT_ITEMS if item["source"] in ("backend", "api")]
    context = {"blocks": {"requirements": {"requirements": {"single": copy.deepcopy(shown)}}}}
    for app in apps:
        answers = {"requirements": {"items": {item["id"]: {"verdict": "met"} for item in shown}}}
        submit(task, items[app.pk], person, answers, context=context)
    automated = {}
    original = analysis._vs_conformance

    def spy(ctx, scope, human, auto, *args, **kwargs):
        automated[scope] = dict(auto)
        return original(ctx, scope, human, auto, *args, **kwargs)

    monkeypatch.setattr(analysis, "_vs_conformance", spy)
    block = block_of(compute_results(task), "requirements")
    # odd apps fail their admin check (factories._checks): invisible to a block without admin_api
    assert {row["conformance_endpoint_pass_rate"] for row in block["per_app"]} == {1.0}
    assert automated["endpoints"] == {str(app.pk): 1.0 for app in apps}
    endpoints = by_app(block["vs_conformance"], key="scope")["endpoints"]
    assert "on the api checks" in endpoints["note"]

    admin = analysis._endpoint_categories({"sources": ["admin_api"]})
    both = analysis._endpoint_categories({"sources": ["backend", "api", "admin_api"]})
    assert admin == {"admin"}
    assert both == {"api", "admin"}
    odd = apps[1].snapshot
    assert analysis._endpoint_pass_rate(odd, admin) == 0.0
    assert analysis._endpoint_pass_rate(odd, both) == 0.75
    assert analysis._endpoint_pass_rate(odd, frozenset()) is None


def test_paired_deltas_snap_float_residue_to_exact_ties():
    """C20: equal means that differ by ~1e-16 after summation are a tie, not a tiny difference."""
    cell = ("slug", 0)
    assert 0.1 + 0.2 - 0.3 != 0
    assert analysis._paired_deltas({cell: [0.1 + 0.2]}, {cell: [0.3]}, [cell]) == [0.0]
    assert analysis._paired_deltas({cell: [0.5]}, {cell: [0.25]}, [cell]) == [0.25]


def test_composite_contrast_drops_exactly_tied_cells():
    """C20 end to end: a tied composite cell must not count as a positive difference in the signed-rank test."""
    task = make_open_task(n_apps=10)
    items = single_items(task)
    apps = apps_of(task)
    first, second = raters(task, 2)
    # cell 0: both composite means are 19/36, but the float sums differ by +1.1e-16 (group minus baseline)
    table = {0: ([(4, 3, 7), (6, 2, 3)], [(4, 2, 3), (4, 6, 6)])}
    for cell in range(1, 5):
        table[cell] = ([(3, 3, 3), (3, 3, 3)], [(3 + cell,) * 3, (3 + cell,) * 3])
    for cell, (baseline, group) in table.items():
        for app, values in ((apps[2 * cell], baseline), (apps[2 * cell + 1], group)):
            for person, rating in zip((first, second), values, strict=True):
                submit(task, items[app.pk], person, ratings(*rating))
    close(task)
    composite = next(row for row in compute_results(task)["contrasts"] if row["metric"] == "composite")
    assert composite["paired"] is True
    assert composite["n_cells"] == 5
    # the tie is dropped: n = 4 all positive (two-sided exact p 0.125), not n = 5 (0.0625)
    assert composite["p_value"] == pytest.approx(wilcoxon_p([1 / 6, 2 / 6, 3 / 6, 4 / 6]))
    assert composite["p_value"] == pytest.approx(0.125)


def test_deactivated_items_still_count_toward_the_planned_sample():
    """C21: taking an under-rated app out of the pool after open does not make the plan look reached."""
    task, items, people = _planned_task()
    close(task)
    under_rated = next(iter(items.values()))
    Assignment.objects.filter(task=task, participant=people[1], item=under_rated).delete()
    assert compute_results(task)["deviations"] == ["stopped_early"]
    EvalTaskApp.objects.filter(pk=under_rated.app_a_id).update(is_active=False)
    EvalItem.objects.filter(pk=under_rated.pk).update(is_active=False)
    results = compute_results(task)
    assert results["deviations"] == ["stopped_early"]
    assert results["confirmatory"] is False
    # an app_broken skip still counts for the deactivated item
    skip(task, under_rated, people[1], "app_broken")
    assert compute_results(task)["deviations"] == []


def test_strategy_all_counts_deactivated_items_in_the_plan():
    """C21 (strategy all): a participant who did not do a later deactivated item did not do every item."""
    design = design_service.default_design("single")
    design["analysis"]["planned_participants"] = 2
    task = make_open_task(n_apps=2, design=design, settings={"allocation": {"strategy": "all"}})
    first_item, second_item = list(single_items(task).values())
    people = raters(task, 2)
    for person in people:
        submit(task, first_item, person, ratings(5, 5, 5))
    submit(task, second_item, people[0], ratings(5, 5, 5))
    close(task)
    assert compute_results(task)["deviations"] == ["stopped_early"]
    EvalItem.objects.filter(pk=second_item.pk).update(is_active=False)
    assert compute_results(task)["deviations"] == ["stopped_early"]


def test_participant_deleted_after_open_is_a_post_hoc_exclusion():
    """C45: deleting a rater with data after open leaves no row, but the protocol log keeps the gate honest."""
    task, items, _people = _planned_task()
    extra = raters(task, 1, start=3)[0]
    for item in items.values():
        submit(task, item, extra, ratings(1, 1, 1))
    close(task)
    extra.delete()  # the plan (2 per item) is still met by the two remaining raters
    assert compute_results(task)["deviations"] == []
    task.protocol_log = [
        {"at": timezone.now().isoformat(), "by": "", "field": "title", "old_sha256": "a", "new_sha256": "b"},
        {
            "at": timezone.now().isoformat(),
            "by": "",
            "field": f"{analysis.PROTOCOL_PARTICIPANT_DELETED}:3",
            "old_sha256": "",
            "new_sha256": "",
        },
    ]
    task.save(update_fields=["protocol_log"])
    results = compute_results(task)
    assert results["deviations"] == ["post_hoc_exclusions"]
    assert results["confirmatory"] is False


def test_protocol_log_participant_deletion_entries():
    assert analysis.is_participant_deletion({"field": "participant_deleted:12"})
    assert analysis.is_participant_deletion({"field": "participant_deleted"})
    assert not analysis.is_participant_deletion({"field": "participant_deleted_by_mistake"})
    assert not analysis.is_participant_deletion({"field": "instructions"})
    assert not analysis.is_participant_deletion("participant_deleted:1")


def _drift(results):
    return [warning for warning in results["warnings"] if warning["code"] == "review_items_differ"]


def test_requirement_lists_that_differ_between_raters_of_an_app_warn(requirements_task):
    """C18: a later rater shown a shorter checklist (job deleted after open) is flagged per block."""
    task, apps = requirements_task
    assert _drift(compute_results(task)) == []
    later = Assignment.objects.filter(task=task, item__app_a=apps[2]).order_by("participant__number").last()
    shorter = [item for item in REQUIREMENT_ITEMS if item["source"] == "custom"]
    later.context = {"blocks": {"requirements": {"requirements": {"single": shorter}}}}
    later.save(update_fields=["context"])
    warnings = _drift(compute_results(task))
    assert len(warnings) == 1
    assert warnings[0]["block_id"] == "requirements"
    assert "1 app(s) saw different requirement lists (App 03)" in warnings[0]["message"]
    # excluded raters are not part of the results, so their lists do not count
    Assignment.objects.filter(pk=later.pk).update(excluded=True, excluded_at=timezone.now())
    assert _drift(compute_results(task)) == []


def test_finding_lists_are_checked_against_the_frozen_sample():
    """C18: deleted findings shrink later raters' lists; all raters short of the sample is flagged too."""
    design = design_with([copy.deepcopy(SECURITY_BLOCK)], {"block_id": "security", "metric": "vulnerable_share"})
    task = make_open_task(n_apps=2, conditions=1, design=design)
    items = single_items(task)
    app = apps_of(task)[0]
    EvalTaskApp.objects.filter(pk=app.pk).update(finding_sample=["f1", "f2", "f3"], finding_sample_at=timezone.now())
    first, second = raters(task, 2)
    submit(task, items[app.pk], first, _checklist("not_tested", "not_tested"), context=_security_context(FINDINGS))
    later = submit(
        task,
        items[app.pk],
        second,
        _checklist("not_tested", "not_tested"),
        context=_security_context(FINDINGS),
    )
    assert _drift(compute_results(task)) == []

    later.context = _security_context(FINDINGS[:2])  # f3's analysis run was deleted before this rater opened
    later.save(update_fields=["context"])
    (warning,) = _drift(compute_results(task))
    assert warning["block_id"] == "security"
    assert "saw different finding lists (App 01)" in warning["message"]
    assert "different finding set than their frozen sample (App 01)" in warning["message"]

    Assignment.objects.filter(participant=first).update(context=_security_context(FINDINGS[:2]))
    (warning,) = _drift(compute_results(task))
    assert "saw different finding lists" not in warning["message"]
    assert "frozen sample (App 01)" in warning["message"]


def _per_assignment_task():
    return make_open_task(n_apps=2, settings={"hosting": {"isolation": "exclusive", "reset": "per_assignment"}})


def _hosting_context(prior_users):
    return {
        "hosting": {
            "single": {
                "container_id": "c1",
                "container_name": "llm-x",
                "image": "img",
                "reset_at": None,
                "prior_users": prior_users,
                "first_running_at": "2026-11-01T10:00:00+00:00",
            },
        },
    }


def test_shared_app_state_warns_when_a_per_assignment_reset_was_skipped():
    """C04: under per_assignment, a submission handed an un-reset app (prior_users > 0) is flagged."""
    task = _per_assignment_task()
    items = single_items(task)
    app = apps_of(task)[0]
    first, second = raters(task, 2)
    submit(task, items[app.pk], first, ratings(5, 5, 5), context=_hosting_context(0))
    later = submit(task, items[app.pk], second, ratings(5, 5, 5), context=_hosting_context(0))
    assert "shared_app_state" not in _codes(compute_results(task))  # every rater got a fresh copy

    later.context = _hosting_context(2)
    later.save(update_fields=["context"])
    warning = next(w for w in compute_results(task)["warnings"] if w["code"] == "shared_app_state")
    assert warning["message"].startswith("1 included submitted assignment(s)")
    assert "prior_users" in warning["message"]

    Assignment.objects.filter(pk=later.pk).update(excluded=True, excluded_at=timezone.now())
    assert "shared_app_state" not in _codes(compute_results(task))  # only included submissions count
