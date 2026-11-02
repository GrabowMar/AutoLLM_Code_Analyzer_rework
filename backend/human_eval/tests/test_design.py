"""services/design.py and services/presets.py: validation rules, normalization, freezing, presets."""

from __future__ import annotations

import copy

import pytest

from backend.human_eval.services import design as d
from backend.human_eval.services import presets
from backend.human_eval.services.checklists import DEFAULT_SECURITY_CHECKLIST

pytestmark = pytest.mark.django_db


def _paths(errors):
    return {error["path"] for error in errors}


def _rating(block_id="ratings", **extra):
    block = {"id": block_id, "type": "rating", "title": "Ratings", "criteria": [{"id": "overall", "label": "Overall"}]}
    block.update(extra)
    return block


def _design(*blocks, **extra):
    design = {"blocks": list(blocks)}
    design.update(extra)
    return design


# --- defaults and normalization ------------------------------------------------


@pytest.mark.parametrize("mode", ["single", "pairwise"])
def test_default_design_is_valid_normalized_and_openable(mode):
    design = d.default_design(mode)
    normalized, errors = d.validate_design(mode, design)
    assert errors == []
    assert normalized == design
    assert d.design_open_blockers(mode, design) == []
    assert design["consent"]["enabled"] is True
    assert [q["id"] for q in design["pre_questions"]] == [
        "programming_years",
        "web_dev_experience",
        "security_testing_experience",
    ]
    assert design["analysis"]["primary"] is not None


def test_default_settings_roundtrip_and_values():
    settings = d.default_settings()
    normalized, errors = d.validate_settings(settings)
    assert errors == []
    assert normalized == settings
    assert list(settings) == ["blinding", "allocation", "pairing", "access", "hosting", "quality"]
    assert settings["hosting"] == {
        "on_demand": True,
        "idle_stop_minutes": 20,
        "max_running_apps": 3,
        "isolation": "shared",
        "reset": "none",
    }
    assert settings["allocation"]["max_items_per_participant"] is None


def test_minimal_design_gets_every_default_in_ts_key_order():
    normalized, errors = d.validate_design("single", _design(_rating()))
    assert errors == []
    assert list(normalized) == ["version", "blocks", "analysis", "consent", "pre_questions", "post_questions"]
    block = normalized["blocks"][0]
    assert list(block) == ["id", "type", "title", "instructions", "criteria", "comment"]
    assert block["comment"] == "optional"
    criterion = block["criteria"][0]
    assert criterion == {
        "id": "overall",
        "label": "Overall",
        "help": "",
        "scale": {"min": 1, "max": 7, "step": 1, "min_label": "Very poor", "max_label": "Excellent", "labels": {}},
        "required": True,
        "allow_na": False,
    }
    assert normalized["analysis"] == {
        "primary": None,
        "planned_ratings_per_item": None,
        "planned_participants": None,
        "exclusion_rules": {
            "speeding": False,
            "attention_failed": False,
            "straightlining": False,
            "app_not_viewed": False,
        },
        "unevaluable_policy": "exclude",
    }
    assert normalized["consent"] == {"enabled": False, "text": ""}


def test_block_defaults_per_type():
    normalized, errors = d.validate_design(
        "single",
        _design(
            {"id": "req", "type": "requirements", "title": "R"},
            {"id": "sec", "type": "security", "title": "S"},
            {"id": "sc", "type": "scenarios", "title": "Sc", "scenarios": [{"id": "s1", "title": "Do it"}]},
            {"id": "q", "type": "questionnaire", "title": "Q"},
            {"id": "bugs", "type": "bugs", "title": "B"},
        ),
    )
    assert errors == []
    req, sec, scen, quest, bugs = normalized["blocks"]
    assert req["sources"] == ["backend", "frontend", "admin", "api", "admin_api"]
    assert req["note"] == "optional"
    assert sec["checklist"] == DEFAULT_SECURITY_CHECKLIST
    assert sec["reports"] is True
    assert sec["http_console"] is False
    assert sec["finding_review"] == {
        "enabled": False,
        "max_findings": 10,
        "categories": [],
        "include_ai": False,
        "include_dependency": False,
        "include_dynamic": False,
    }
    assert scen["timed"] is True
    assert scen["ease_question"] is True
    assert scen["scenarios"][0] == {"id": "s1", "title": "Do it", "description": "", "success_criteria": ""}
    assert quest["instrument"] == "sus"
    assert quest["questions"] == []
    assert bugs["min_reports"] == 0


def test_question_keys_depend_on_kind():
    questions = [
        {"id": "t", "kind": "text", "label": "T", "options": [{"value": "x", "label": "X"}], "min": 1},
        {
            "id": "s",
            "kind": "single",
            "label": "S",
            "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}],
        },
        {"id": "sc", "kind": "scale", "label": "Sc", "scale": {"min": 1, "max": 5}},
        {"id": "n", "kind": "number", "label": "N", "min": 0},
        {
            "id": "att",
            "kind": "attention",
            "label": "Pick B",
            "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}],
            "expected": "b",
        },
    ]
    normalized, errors = d.validate_design("single", _design(pre_questions=questions))
    assert errors == []
    by_id = {q["id"]: q for q in normalized["pre_questions"]}
    base = ["id", "kind", "label", "help", "required"]
    assert list(by_id["t"]) == base
    assert list(by_id["s"]) == [*base, "options"]
    assert list(by_id["sc"]) == [*base, "scale"]
    assert list(by_id["n"]) == [*base, "min", "max"]
    assert by_id["n"]["max"] is None
    assert list(by_id["att"]) == [*base, "options", "expected"]


def test_labels_are_stripped_and_scale_labels_sorted():
    block = _rating(title="  Ratings  ")
    block["criteria"][0]["scale"] = {"min": 1, "max": 10, "labels": {"10": "Top", "2": "Low", "1": "Bottom"}}
    normalized, errors = d.validate_design("single", _design(block))
    assert errors == []
    assert normalized["blocks"][0]["title"] == "Ratings"
    assert list(normalized["blocks"][0]["criteria"][0]["scale"]["labels"]) == ["1", "2", "10"]


def test_validation_is_idempotent_and_does_not_mutate_input():
    raw = _design(_rating())
    original = copy.deepcopy(raw)
    first, _ = d.validate_design("single", raw)
    second, _ = d.validate_design("single", first)
    assert raw == original
    assert first == second


# --- structural errors -----------------------------------------------------------


def test_structural_errors_have_dotted_paths_without_the_union_tag():
    _, errors = d.validate_design(
        "single",
        _design(
            {
                "id": "r",
                "type": "rating",
                "title": "",
                "criteria": [{"id": "x", "label": "X", "scale": {"min": "1", "max": 5}}],
            },
            {"id": "b", "type": "bugs", "title": "B", "unknown": 1},
            {"id": "z", "type": "nope", "title": "Z"},
            {"title": "no type"},
        ),
    )
    by_path = {error["path"]: error["message"] for error in errors}
    assert by_path["blocks.0.title"] == "Required"
    assert "blocks.0.criteria.0.scale.min" in by_path
    assert by_path["blocks.1.unknown"] == "Unknown field"
    assert by_path["blocks.2"] == "Unknown block type 'nope'"
    assert by_path["blocks.3"] == "Block type is required"


@pytest.mark.parametrize(
    ("design", "path"),
    [
        ("not a dict", ""),
        ({"version": 2}, "version"),
        ({"blocks": "x"}, "blocks"),
        ({"extra": True}, "extra"),
        ({"blocks": [_rating(comment="sometimes")]}, "blocks.0.comment"),
        ({"pre_questions": [{"id": "q", "kind": "date", "label": "When"}]}, "pre_questions.0.kind"),
        ({"consent": {"enabled": "yes", "text": "x"}}, "consent.enabled"),
        ({"blocks": [{"id": "b", "type": "bugs", "title": "B", "min_reports": 21}]}, "blocks.0.min_reports"),
        (
            {"blocks": [{"id": "s", "type": "security", "title": "S", "finding_review": {"max_findings": 51}}]},
            "blocks.0.finding_review.max_findings",
        ),
        ({"analysis": {"planned_ratings_per_item": 0}}, "analysis.planned_ratings_per_item"),
        ({"blocks": [_rating(instructions="x" * 20001)]}, "blocks.0.instructions"),
    ],
)
def test_structural_rejections(design, path):
    normalized, errors = d.validate_design("single", design)
    assert normalized is None
    assert path in _paths(errors)


def test_strict_types_reject_coercion():
    block = _rating()
    block["criteria"][0]["required"] = 1
    _, errors = d.validate_design("single", _design(block))
    assert "blocks.0.criteria.0.required" in _paths(errors)


# --- semantic rules --------------------------------------------------------------


def test_ids_must_match_pattern_and_be_unique():
    block = _rating("Bad-Id")
    block["criteria"] = [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}, {"id": "x" * 41, "label": "C"}]
    _, errors = d.validate_design("single", _design(block, _rating("ok"), _rating("ok")))
    paths = _paths(errors)
    assert "blocks.0.id" in paths
    assert "blocks.0.criteria.1.id" in paths
    assert "blocks.0.criteria.2.id" in paths
    assert "blocks.2.id" in paths


@pytest.mark.parametrize(
    ("scale", "path"),
    [
        ({"min": 1, "max": 1}, "max"),
        ({"min": 5, "max": 1}, "max"),
        ({"min": 0, "max": 101}, "max"),
        ({"min": 0, "max": 10, "step": 3}, "step"),
        ({"min": 0, "max": 10, "step": 0}, "step"),
        ({"min": 1, "max": 5, "labels": {"6": "x"}}, "labels.6"),
        ({"min": 0, "max": 10, "step": 5, "labels": {"3": "x"}}, "labels.3"),
        ({"min": 1, "max": 5, "labels": {"01": "x"}}, "labels.01"),
        ({"min": 1, "max": 5, "labels": {"one": "x"}}, "labels.one"),
    ],
)
def test_scale_rules(scale, path):
    block = _rating()
    block["criteria"][0]["scale"] = scale
    _, errors = d.validate_design("single", _design(block))
    assert f"blocks.0.criteria.0.scale.{path}" in _paths(errors)


def test_scale_with_step_on_grid_is_valid():
    block = _rating()
    block["criteria"][0]["scale"] = {
        "min": 0,
        "max": 100,
        "step": 5,
        "labels": {"0": "Low", "50": "Mid", "100": "High"},
    }
    _, errors = d.validate_design("single", _design(block))
    assert errors == []
    assert d.scale_values({"min": 0, "max": 10, "step": 5}) == [0, 5, 10]


def test_mode_restricts_block_types():
    _, errors = d.validate_design("pairwise", _design(_rating()))
    assert "blocks.0.type" in _paths(errors)
    pairwise = {"id": "p", "type": "pairwise", "title": "P", "criteria": [{"id": "c", "label": "C"}]}
    _, errors = d.validate_design("single", _design(pairwise))
    assert "blocks.0.type" in _paths(errors)
    _, errors = d.validate_design("pairwise", _design(pairwise, {"id": "bugs", "type": "bugs", "title": "B"}))
    assert errors == []
    _, errors = d.validate_design("triple", _design())
    assert _paths(errors) == {"mode"}


def test_open_blockers_for_empty_and_pairwise_without_pairwise_block():
    assert d.design_open_blockers("single", d.validate_design("single", _design())[0]) != []
    bugs_only, errors = d.validate_design("pairwise", _design({"id": "bugs", "type": "bugs", "title": "B"}))
    assert errors == []
    assert len(d.design_open_blockers("pairwise", bugs_only)) == 1
    settings = d.default_settings()
    assert d.settings_open_blockers(settings) == []
    settings["access"] = dict.fromkeys(settings["access"], False)
    assert d.settings_open_blockers(settings) != []


def test_at_most_one_finding_review_block_and_one_bug_log():
    review = {"enabled": True}
    _, errors = d.validate_design(
        "single",
        _design(
            {"id": "s1", "type": "security", "title": "S1", "finding_review": review},
            {"id": "s2", "type": "security", "title": "S2", "finding_review": review},
            {"id": "b1", "type": "bugs", "title": "B1"},
            {"id": "b2", "type": "bugs", "title": "B2"},
        ),
    )
    paths = _paths(errors)
    assert "blocks.1.finding_review.enabled" in paths
    assert "blocks.0.finding_review.enabled" not in paths
    assert "blocks.3.type" in paths


def test_security_block_rules():
    _, errors = d.validate_design(
        "single",
        _design(
            {"id": "s", "type": "security", "title": "S", "checklist": [], "reports": False},
            {
                "id": "s2",
                "type": "security",
                "title": "S2",
                "checklist": [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}],
                "finding_review": {"categories": ["security", "Not Valid", "security"]},
            },
        ),
    )
    paths = _paths(errors)
    assert "blocks.0.checklist" in paths
    assert "blocks.1.checklist.1.id" in paths
    assert "blocks.1.finding_review.categories.1" in paths
    assert "blocks.1.finding_review.categories" in paths


def test_questionnaire_rules():
    standard_with_questions = {
        "id": "q1",
        "type": "questionnaire",
        "title": "SUS",
        "instrument": "sus",
        "questions": [{"id": "x", "kind": "text", "label": "X"}],
    }
    custom_empty = {"id": "q2", "type": "questionnaire", "title": "Custom", "instrument": "custom"}
    _, errors = d.validate_design("single", _design(standard_with_questions, custom_empty))
    assert {"blocks.0.questions", "blocks.1.questions"} <= _paths(errors)


def test_requirements_scenarios_rating_and_pairwise_need_content():
    _, errors = d.validate_design(
        "single",
        _design(
            {"id": "r", "type": "requirements", "title": "R", "sources": [], "custom": []},
            {"id": "r2", "type": "requirements", "title": "R2", "sources": ["api", "api"]},
            {"id": "sc", "type": "scenarios", "title": "S"},
            {"id": "rt", "type": "rating", "title": "Rt"},
        ),
    )
    assert {"blocks.0.sources", "blocks.1.sources", "blocks.2.scenarios", "blocks.3.criteria"} <= _paths(errors)
    _, errors = d.validate_design("pairwise", _design({"id": "p", "type": "pairwise", "title": "P", "overall": False}))
    assert "blocks.0.criteria" in _paths(errors)


def test_question_rules():
    questions = [
        {"id": "s", "kind": "single", "label": "S", "options": [{"value": "a", "label": "A"}]},
        {
            "id": "m",
            "kind": "multi",
            "label": "M",
            "options": [{"value": "a", "label": "A"}, {"value": "a", "label": "B"}],
        },
        {
            "id": "att",
            "kind": "attention",
            "label": "Pick",
            "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}],
        },
        {
            "id": "att2",
            "kind": "attention",
            "label": "Pick",
            "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}],
            "expected": "z",
        },
        {"id": "sc", "kind": "scale", "label": "Sc"},
        {"id": "n", "kind": "number", "label": "N", "min": 10, "max": 1},
        {"id": "n2", "kind": "number", "label": "N", "min": "low"},
        {"id": "n3", "kind": "number", "label": "N", "min": True},
    ]
    _, errors = d.validate_design("single", _design(pre_questions=questions))
    paths = _paths(errors)
    assert {
        "pre_questions.0.options",
        "pre_questions.1.options.1.value",
        "pre_questions.2.expected",
        "pre_questions.3.expected",
        "pre_questions.4.scale",
        "pre_questions.5.max",
        "pre_questions.6.min",
        "pre_questions.7.min",
    } <= paths


def test_pre_and_post_question_ids_are_unique_across_both_lists():
    question = {"id": "same", "kind": "text", "label": "Q"}
    _, errors = d.validate_design("single", _design(pre_questions=[question], post_questions=[question]))
    assert _paths(errors) == {"post_questions.0.id"}


def test_consent_enabled_needs_text_and_limits():
    _, errors = d.validate_design("single", _design(consent={"enabled": True, "text": "   "}))
    assert _paths(errors) == {"consent.text"}
    _, errors = d.validate_design("single", _design(consent={"enabled": False, "text": ""}))
    assert errors == []


def test_too_many_blocks_and_entries():
    blocks = [_rating(f"r{i}") for i in range(31)]
    _, errors = d.validate_design("single", _design(*blocks))
    assert "blocks" in _paths(errors)
    block = _rating()
    block["criteria"] = [{"id": f"c{i}", "label": f"C{i}"} for i in range(51)]
    _, errors = d.validate_design("single", _design(block))
    assert "blocks.0.criteria" in _paths(errors)


@pytest.mark.parametrize(
    ("primary", "ok"),
    [
        ({"block_id": "ratings", "metric": "composite"}, True),
        ({"block_id": "ratings", "metric": "overall"}, True),
        ({"block_id": "ratings", "metric": "score"}, False),
        ({"block_id": "missing", "metric": "composite"}, False),
        ({"block_id": "sus", "metric": "score"}, True),
        ({"block_id": "custom", "metric": "score"}, False),
        ({"block_id": "sec", "metric": "vulnerable_share"}, True),
        ({"block_id": "nochecklist", "metric": "vulnerable_share"}, False),
    ],
)
def test_primary_metric_must_belong_to_the_block(primary, ok):
    design = _design(
        _rating(),
        {"id": "sus", "type": "questionnaire", "title": "SUS", "instrument": "sus"},
        {
            "id": "custom",
            "type": "questionnaire",
            "title": "C",
            "instrument": "custom",
            "questions": [{"id": "q", "kind": "text", "label": "Q"}],
        },
        {"id": "sec", "type": "security", "title": "S"},
        {"id": "nochecklist", "type": "security", "title": "S", "checklist": [], "http_console": True},
        analysis={"primary": primary},
    )
    _, errors = d.validate_design("single", design)
    assert (errors == []) is ok
    if not ok:
        assert _paths(errors) & {"analysis.primary.block_id", "analysis.primary.metric"}


def test_metric_options():
    assert d.metric_options({"type": "rating", "criteria": [{"id": "a"}]}) == ["composite", "a"]
    assert d.metric_options({"type": "pairwise", "overall": False, "criteria": [{"id": "a"}]}) == ["a"]
    assert d.metric_options({"type": "pairwise", "overall": True, "criteria": []}) == ["overall"]
    assert d.metric_options({"type": "bugs"}) == ["bugs_per_rater"]
    assert d.metric_options({"type": "unknown"}) == []


# --- settings ---------------------------------------------------------------------


def test_settings_partial_input_is_deep_merged():
    normalized, errors = d.validate_settings({"allocation": {"ratings_per_item": 5}})
    assert errors == []
    assert normalized["allocation"]["ratings_per_item"] == 5
    assert normalized["allocation"]["assignment_timeout_minutes"] == 60
    assert normalized["hosting"] == d.default_settings()["hosting"]
    assert d.validate_settings(None)[0] == d.default_settings()


@pytest.mark.parametrize(
    ("raw_settings", "path"),
    [
        ("x", ""),
        ({"unknown": {}}, "unknown"),
        ({"blinding": {"hide_model": True}}, "blinding.hide_model"),
        ({"allocation": {"ratings_per_item": 0}}, "allocation.ratings_per_item"),
        ({"allocation": {"ratings_per_item": 51}}, "allocation.ratings_per_item"),
        ({"allocation": {"assignment_timeout_minutes": 4}}, "allocation.assignment_timeout_minutes"),
        ({"allocation": {"strategy": "random"}}, "allocation.strategy"),
        ({"pairing": {"k": 21}}, "pairing.k"),
        ({"hosting": {"max_running_apps": 21}}, "hosting.max_running_apps"),
        ({"hosting": {"idle_stop_minutes": 241}}, "hosting.idle_stop_minutes"),
        ({"hosting": {"reset": "per_assignment"}}, "hosting.reset"),
        ({"quality": {"min_seconds_per_item": -1}}, "quality.min_seconds_per_item"),
        ({"access": {"allow_accounts": "yes"}}, "access.allow_accounts"),
    ],
)
def test_settings_rejections(raw_settings, path):
    normalized, errors = d.validate_settings(raw_settings)
    assert normalized is None
    assert path in _paths(errors)


def test_reset_per_assignment_with_exclusive_isolation_is_valid():
    normalized, errors = d.validate_settings({"hosting": {"isolation": "exclusive", "reset": "per_assignment"}})
    assert errors == []
    assert normalized["hosting"]["reset"] == "per_assignment"


# --- participant view --------------------------------------------------------------


def test_participant_blocks_and_questions_strip_expected_without_mutating():
    attention = {
        "id": "att",
        "kind": "attention",
        "label": "Pick B",
        "options": [{"value": "a", "label": "A"}, {"value": "b", "label": "B"}],
        "expected": "b",
    }
    design, errors = d.validate_design(
        "single",
        _design(
            {"id": "q", "type": "questionnaire", "title": "Q", "instrument": "custom", "questions": [attention]},
            pre_questions=[attention],
        ),
    )
    assert errors == []
    blocks = d.participant_blocks(design)
    assert "expected" not in blocks[0]["questions"][0]
    assert blocks[0]["questions"][0]["options"] == attention["options"]
    assert "expected" not in d.participant_questions(design["pre_questions"])[0]
    assert design["blocks"][0]["questions"][0]["expected"] == "b"
    assert design["pre_questions"][0]["expected"] == "b"
    blocks[0]["title"] = "changed"
    assert design["blocks"][0]["title"] == "Q"


# --- freezing ------------------------------------------------------------------------


def test_editable_and_frozen_keys_partition_the_settings():
    leaves = {f"{section}.{key}" for section, values in d.default_settings().items() for key in values}
    assert leaves == d.EDITABLE_AFTER_FREEZE | d.FROZEN_SETTINGS_KEYS
    assert not d.EDITABLE_AFTER_FREEZE & d.FROZEN_SETTINGS_KEYS
    assert {"hosting.isolation", "hosting.reset", "allocation.ratings_per_item", "allocation.strategy"} <= (
        d.FROZEN_SETTINGS_KEYS
    )
    assert "pairing.k" in d.FROZEN_SETTINGS_KEYS
    assert "blinding.show_spec" in d.FROZEN_SETTINGS_KEYS
    assert {"hosting.on_demand", "hosting.idle_stop_minutes", "hosting.max_running_apps"} <= d.EDITABLE_AFTER_FREEZE
    assert "quality.min_seconds_per_item" in d.EDITABLE_AFTER_FREEZE


def test_changed_frozen_keys():
    old = d.default_settings()
    editable_change = d.deep_merge(
        old,
        {
            "allocation": {"max_items_per_participant": 5, "allow_skip": False, "assignment_timeout_minutes": 30},
            "access": {"allow_accounts": False},
            "hosting": {"on_demand": False, "idle_stop_minutes": 10, "max_running_apps": 5},
            "quality": {"min_seconds_per_item": 0},
        },
    )
    assert d.changed_frozen_keys(old, editable_change) == []
    frozen_change = d.deep_merge(
        old,
        {"allocation": {"ratings_per_item": 4}, "hosting": {"isolation": "exclusive", "reset": "per_assignment"}},
    )
    assert d.changed_frozen_keys(old, frozen_change) == [
        "allocation.ratings_per_item",
        "hosting.isolation",
        "hosting.reset",
    ]
    # partial documents are merged over the defaults first
    assert d.changed_frozen_keys({}, {"pairing": {"k": 2}}) == []
    assert d.changed_frozen_keys({}, {"pairing": {"k": 3}}) == ["pairing.k"]


def test_quality_is_frozen_when_the_plan_excludes_speeders():
    design = d.default_design("single")
    old = d.default_settings()
    new = d.deep_merge(old, {"quality": {"min_seconds_per_item": 300}})
    assert d.changed_frozen_keys(old, new, design=design) == []
    design["analysis"]["exclusion_rules"]["speeding"] = True
    assert d.changed_frozen_keys(old, new, design=design) == ["quality.min_seconds_per_item"]
    assert "quality.min_seconds_per_item" not in d.editable_after_freeze(design)


def test_design_hash_is_stable_and_ignores_only_editable_settings():
    design = d.default_design("single")
    settings = d.default_settings()
    digest = d.design_hash("single", design, settings)
    assert len(digest) == 64
    reordered = {key: settings[key] for key in reversed(list(settings))}
    assert d.design_hash("single", design, reordered) == digest
    assert d.design_hash("pairwise", design, settings) != digest
    editable = d.deep_merge(settings, {"access": {"allow_accounts": False}, "quality": {"min_seconds_per_item": 1}})
    assert d.design_hash("single", design, editable) == digest
    frozen = d.deep_merge(settings, {"blinding": {"show_spec": False}})
    assert d.design_hash("single", design, frozen) != digest
    changed_design = copy.deepcopy(design)
    changed_design["blocks"][0]["title"] = "Other"
    assert d.design_hash("single", changed_design, settings) != digest
    speeding = copy.deepcopy(design)
    speeding["analysis"]["exclusion_rules"]["speeding"] = True
    quality = d.deep_merge(settings, {"quality": {"min_seconds_per_item": 1}})
    assert d.design_hash("single", speeding, quality) != d.design_hash("single", speeding, settings)


# --- presets ---------------------------------------------------------------------------


def test_six_presets_in_order():
    assert presets.PRESET_SLUGS == (
        "app-grading",
        "requirements-audit",
        "security-assessment",
        "usability-test",
        "ab-comparison",
        "full-evaluation",
    )
    for preset in presets.list_presets():
        assert list(preset) == ["slug", "name", "description", "mode", "design", "settings"]


@pytest.mark.parametrize("slug", presets.PRESET_SLUGS)
def test_every_preset_validates_and_is_normalized(slug):
    preset = presets.get_preset(slug)
    design, design_errors = d.validate_design(preset["mode"], preset["design"])
    settings, settings_errors = d.validate_settings(preset["settings"])
    assert design_errors == []
    assert settings_errors == []
    assert design == preset["design"]
    assert settings == preset["settings"]
    assert d.design_open_blockers(preset["mode"], design) == []
    assert d.settings_open_blockers(settings) == []
    assert design["consent"]["enabled"] is True
    assert design["consent"]["text"] == d.DEFAULT_CONSENT_TEXT
    assert [q["id"] for q in design["pre_questions"]] == [q["id"] for q in d.DEFAULT_PRE_QUESTIONS]
    primary = design["analysis"]["primary"]
    assert primary is not None
    assert primary["metric"] in d.metric_options(d.block_by_id(design, primary["block_id"]))
    for block in design["blocks"]:
        for criterion in block.get("criteria", []):
            if block["type"] == "rating":
                assert (criterion["scale"]["min"], criterion["scale"]["max"]) == (1, 7)
                assert criterion["allow_na"] is True
                assert criterion["help"]
                assert set(criterion["scale"]["labels"]) == {"1", "4", "7"}


def test_security_presets_isolate_and_reset_apps():
    for slug in ("security-assessment", "full-evaluation"):
        preset = presets.get_preset(slug)
        assert preset["settings"]["hosting"]["isolation"] == "exclusive"
        assert preset["settings"]["hosting"]["reset"] == "per_assignment"
        assert d.has_security_testing(preset["design"])
        assert d.has_http_console(preset["design"])
        review = d.finding_review_block(preset["design"])
        assert review is not None
        assert review["finding_review"]["enabled"] is True
    for slug in ("app-grading", "requirements-audit", "usability-test", "ab-comparison"):
        assert not d.has_security_testing(presets.get_preset(slug)["design"])


def test_full_evaluation_has_an_attention_check():
    preset = presets.get_preset("full-evaluation")
    attention = [q for q in preset["design"]["post_questions"] if q["kind"] == "attention"]
    assert len(attention) == 1
    assert attention[0]["expected"] in {option["value"] for option in attention[0]["options"]}
    assert preset["design"]["analysis"]["exclusion_rules"]["attention_failed"] is True


def test_ab_comparison_is_pairwise_across_conditions():
    preset = presets.get_preset("ab-comparison")
    assert preset["mode"] == "pairwise"
    assert preset["settings"]["pairing"]["cross_condition_only"] is True
    assert {block["type"] for block in preset["design"]["blocks"]} == {"pairwise"}


def test_get_preset_returns_copies():
    preset = presets.get_preset("app-grading")
    preset["design"]["blocks"].clear()
    assert presets.get_preset("app-grading")["design"]["blocks"]
    assert presets.get_preset("nope") is None
