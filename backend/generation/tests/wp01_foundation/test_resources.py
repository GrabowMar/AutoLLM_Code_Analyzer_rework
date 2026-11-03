"""ResourcePolicyContent, KNOB_META and the section 4.2 table (no database).

The spec-table test parses ``docs/design/SAMPLE_GEN_V3.md`` section 4.2 and
checks every knob key, its validity class and (where the cell is a plain
value) the frugal / standard / generous defaults against ``KNOB_META``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from backend.generation import identity
from backend.generation.contracts import resources
from backend.generation.contracts.resources import KNOB_META
from backend.generation.contracts.resources import TIERS
from backend.generation.contracts.resources import ResourcePolicyContent

SPEC = Path(__file__).resolve().parents[4] / "docs" / "design" / "SAMPLE_GEN_V3.md"
SKIP = object()
_PIPE = re.compile(r"(?<!\\)\|")  # table cell separator (``\|`` inside a cell is a literal pipe)


# =====================================================================================
# Spec table parsing
# =====================================================================================


def _section(text: str, start: str, end: str) -> str:
    return text[text.index(start) : text.index(end)]


def _parse_value(cell: str) -> Any:  # noqa: PLR0911 - one return per spec cell form
    raw = cell.strip()
    text = raw.strip("`").strip()
    if text in {"—", "-"}:
        return None
    if text in {"true", "false"}:
        return text == "true"
    if text in {"", "see below", "default set", "same"} or "=" in text:
        return SKIP
    if text.startswith("{") and text.endswith("}"):
        return sorted(part.strip() for part in text[1:-1].split(","))
    if text.startswith("[") and text.endswith("]"):
        items = [part.strip() for part in text[1:-1].split(",") if part.strip()]
        return [_parse_value(item) if re.fullmatch(r"[0-9.]+", item) else item for item in items]
    match = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*(k|M|MB|GB)?", text)
    if match:
        number, suffix = match.groups()
        if suffix in {"MB", "GB"}:
            return int(float(number) * (resources.MIB if suffix == "MB" else resources.GIB))
        if suffix in {"k", "M"}:
            return int(float(number) * (1_000 if suffix == "k" else 1_000_000))
        return float(number) if "." in number else int(number)
    match = re.fullmatch(r"([0-9]+)e([0-9]+)", text)
    if match:
        return int(match[1]) * 10 ** int(match[2])
    if re.fullmatch("[a-z0-9_]+|model\u00d7[0-9.]+", text):
        return text
    return SKIP


def _keys(cell: str) -> list[str]:
    keys = re.findall(r"`([a-z_.]+)`", cell)
    prefix = keys[0].rsplit(".", 1)[0] if keys and "." in keys[0] else ""
    return [key if "." in key or not prefix else f"{prefix}.{key}" for key in keys]


def spec_knob_rows() -> list[dict[str, Any]]:
    """Rows of the section 4.2 knob tables: ``{keys, cls, F, S, G}`` (values already split per key)."""
    text = _section(SPEC.read_text(encoding="utf-8"), "### 4.2 Knob catalogue", "### 4.3 Effective policy")
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip().replace("\\|", "|") for cell in _PIPE.split(line.strip().strip("|"))]
        if len(cells) == 8:  # Key | Type | Meaning | Where | Cls | F | S | G
            keys = _keys(cells[0])
            cls_match = re.search(r"\b([CROS])\b", cells[4])
            assert cls_match is not None, line
            cls = cls_match[1]
            tiers = cells[5:8]
        elif len(cells) == 5:  # pilot gates: Gate | Meaning | F | S | G (all class S)
            keys = [f"pilot.gates.{key}" for key in re.findall(r"`([a-z_]+)`", cells[0])]
            cls = "S"
            tiers = cells[2:5]
        else:  # pragma: no cover - table shape changed
            continue
        if keys == ["pilot.gates"]:
            continue  # group row; the gates table below lists each gate
        per_key: list[list[Any]] = [[] for _ in keys]
        for tier_index, tier_cell in enumerate(tiers):
            parts = re.split(r"\s+/\s+", tier_cell) if len(keys) > 1 else [tier_cell]
            if tier_cell.strip() == "same" and tier_index > 0:
                values = [per_key[i][tier_index - 1] for i in range(len(keys))]
            elif len(parts) == len(keys):
                values = [_parse_value(part) for part in parts]
            else:
                values = [_parse_value(tier_cell)] * len(keys)
            for index, value in enumerate(values):
                per_key[index].append(value)
        for index, key in enumerate(keys):
            # The reasoning object ("effort=low") is checked by test_reasoning_defaults_match_spec.
            defaults = [SKIP, SKIP, SKIP] if key == "reasoning" else per_key[index]
            rows.append({"key": key, "cls": cls, "defaults": defaults})
    return rows


SPEC_ROWS = spec_knob_rows()


def test_spec_table_was_parsed():
    assert len(SPEC_ROWS) >= 75
    assert {row["key"] for row in SPEC_ROWS} >= {"run.max_cost_usd", "pilot.gates.max_oom_rate", "network_policy"}


@pytest.mark.parametrize("row", SPEC_ROWS, ids=[row["key"] for row in SPEC_ROWS])
def test_knob_meta_matches_spec_row(row):
    knob = KNOB_META.get(row["key"])
    assert knob is not None, f"{row['key']} is in the spec table but not in KNOB_META"
    assert knob.cls == row["cls"]
    for tier, expected in zip(TIERS, row["defaults"], strict=True):
        if expected is SKIP:
            continue
        actual = knob.defaults[tier]
        if isinstance(expected, list) and isinstance(actual, list):
            assert sorted(map(str, actual)) == sorted(map(str, expected)), (row["key"], tier)
        else:
            assert actual == expected, (row["key"], tier, actual, expected)


def test_every_knob_is_in_the_spec_table():
    spec_keys = {row["key"] for row in SPEC_ROWS}
    assert set(KNOB_META) == spec_keys


def test_reasoning_defaults_match_spec():
    assert KNOB_META["reasoning"].defaults == {
        "frugal": {"mode": "effort", "effort": "low", "max_tokens": None},
        "standard": {"mode": "provider_default", "effort": None, "max_tokens": None},
        "generous": {"mode": "effort", "effort": "high", "max_tokens": None},
    }


def test_run_cost_cap_class_depends_on_budget_basis():
    assert resources.knob_class("run.max_cost_usd", "usd") == "R"
    assert resources.knob_class("run.max_cost_usd", "tokens") == "O"
    assert resources.knob_class("run.max_total_tokens", "tokens") == "R"


# =====================================================================================
# Coverage of ResourcePolicyContent
# =====================================================================================


def test_knob_meta_covers_every_policy_field():
    leaves = list(resources.iter_leaf_paths())
    assert len(leaves) >= 80
    for leaf in leaves:
        knob = resources.knob_for_path(leaf)  # raises when uncovered
        assert leaf == knob.key or leaf.startswith(f"{knob.key}.")


def test_every_knob_key_names_a_policy_field():
    leaves = set(resources.iter_leaf_paths())
    for key in KNOB_META:
        assert key in leaves or any(leaf.startswith(f"{key}.") for leaf in leaves), key


def test_knob_meta_entries_are_complete():
    valid_where = set(resources.Where.__args__)
    for key, knob in KNOB_META.items():
        assert knob.key == key
        assert knob.cls in {"C", "R", "O", "S"}
        assert knob.where
        assert set(knob.where) <= valid_where, key
        assert knob.ui_group
        assert knob.type
        assert knob.description
        assert set(knob.defaults) == set(TIERS), key
    json.dumps([knob.to_dict() for knob in KNOB_META.values()])


def test_key_limits():
    assert set(resources.KEY_LIMITS) == {
        "study.max_cost_usd",
        "run.max_cost_usd",
        "cell.max_cost_usd",
        "concurrency.study_max_parallel",
        "reasoning",
        "routing.serving_tier",
        "pilot.enabled",
    }


# =====================================================================================
# Presets
# =====================================================================================


@pytest.mark.parametrize("tier", TIERS)
def test_preset_values_equal_knob_meta_defaults(tier):
    preset = resources.preset_content(tier)
    values = resources.knob_values(preset)
    for key, knob in KNOB_META.items():
        expected = knob.defaults[tier]
        if key == "retries.retry_on":
            expected = sorted(expected)
        assert values[key] == expected, key


def test_presets_differ_and_round_trip():
    contents = [resources.preset_content(tier).model_dump(mode="json") for tier in TIERS]
    assert len({identity.H(content) for content in contents}) == 3
    for content in contents:
        assert ResourcePolicyContent.model_validate(content).model_dump(mode="json") == content


@pytest.mark.parametrize("tier", TIERS)
def test_presets_satisfy_basic_policy_invariants(tier):
    """The data-level invariants behind the U-B1/U-B2 lint rules hold for every shipped tier."""
    preset = resources.preset_content(tier)
    if preset.call.max_input_tokens is not None and preset.context.max_context_tokens is not None:
        assert preset.call.max_input_tokens >= 1.1 * preset.context.max_context_tokens
    reasoning = preset.reasoning
    assert (reasoning.mode == "effort") == (reasoning.effort is not None)
    assert (reasoning.mode == "max_tokens") == (reasoning.max_tokens is not None)
    assert len(preset.study.stop_rules) == len(resources.DEFAULT_STOP_RULES)


def test_unknown_tier():
    with pytest.raises(ValueError, match="unknown tier"):
        resources.preset_content("lavish")  # type: ignore[arg-type]


def test_stored_policy_must_be_complete():
    content = resources.preset_content("frugal").model_dump(mode="json")
    del content["run"]["max_turns"]
    with pytest.raises(ValidationError, match="max_turns"):
        ResourcePolicyContent.model_validate(content)
    content = resources.preset_content("frugal").model_dump(mode="json")
    content["run"]["max_cost"] = 1
    with pytest.raises(ValidationError, match="max_cost"):
        ResourcePolicyContent.model_validate(content)


# =====================================================================================
# Field rules
# =====================================================================================


def _with(path: str, value: Any) -> dict[str, Any]:
    content = resources.preset_content("frugal").model_dump(mode="json")
    node = content
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
    return content


@pytest.mark.parametrize(("raw", "expected"), [("model\u00d71.25", "model\u00d71.25"), ("modelx2", "model\u00d72")])
def test_max_price_model_multiplier_normalized(raw, expected):
    policy = ResourcePolicyContent.model_validate(_with("routing.max_price", raw))
    assert policy.routing.max_price == expected


@pytest.mark.parametrize("raw", ["model\u00d70", "1.25", "model*2", "modelx"])
def test_max_price_invalid_strings_rejected(raw):
    with pytest.raises(ValidationError, match="max_price"):
        ResourcePolicyContent.model_validate(_with("routing.max_price", raw))


def test_max_price_object_and_null():
    policy = ResourcePolicyContent.model_validate(_with("routing.max_price", {"prompt": 1.0, "completion": 2.0}))
    assert isinstance(policy.routing.max_price, resources.MaxPrice)
    assert ResourcePolicyContent.model_validate(_with("routing.max_price", None)).routing.max_price is None


def test_retry_on_sorted_unique_and_validated():
    policy = ResourcePolicyContent.model_validate(_with("retries.retry_on", ["5xx", "429", "429"]))
    assert policy.retries.retry_on == ["429", "5xx"]
    with pytest.raises(ValidationError):
        ResourcePolicyContent.model_validate(_with("retries.retry_on", ["4x"]))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("retries.upstream_retries", 9),
        ("run.max_llm_requests", 0),
        ("sandbox.memory", "2GB"),
        ("study.alert_at", [1.5]),
        ("pilot.units", 0),
        ("study.max_cost_usd", "unlimited"),
        ("reasoning.effort", "extreme"),
        ("routing.serving_tier", "priority"),
        ("network_policy", "open"),
    ],
)
def test_invalid_values_rejected(path, value):
    with pytest.raises(ValidationError):
        ResourcePolicyContent.model_validate(_with(path, value))


def test_cross_field_rules_are_lint_not_schema():
    # Both effort and max_tokens: invalid as policy (reasoning_mode_invalid) but valid as data.
    content = _with("reasoning", {"mode": "effort", "effort": "low", "max_tokens": 2048, "exclude": False})
    ResourcePolicyContent.model_validate(content)
    # call cap below 1.1 x context cap: context_cap_above_call_cap is a lint issue.
    ResourcePolicyContent.model_validate(_with("call.max_input_tokens", 1000))


def test_default_stop_rules_are_valid():
    rules = [resources.StopRule.model_validate(rule) for rule in resources.DEFAULT_STOP_RULES]
    assert {rule.code for rule in rules} == {
        "infra_error_rate",
        "no_provider_rate",
        "consecutive_failures",
        "cost_overrun",
        "spend_fraction",
    }


# =====================================================================================
# Treatment view
# =====================================================================================


def test_values_by_class_selects_c_and_r_only():
    preset = resources.preset_content("frugal")
    view = resources.values_by_class(preset, {"C", "R"}, budget_basis="usd")
    assert view
    for key in view:
        assert resources.knob_class(key, "usd") in {"C", "R"}
    assert "run.max_cost_usd" in view
    assert "caching.prompt_cache" not in view
    assert "study.max_cost_usd" not in view
    tokens_view = resources.values_by_class(preset, {"C", "R"}, budget_basis="tokens")
    assert "run.max_cost_usd" not in tokens_view
    assert "reasoning" in view
    assert "exclude" not in view["reasoning"]


def test_operational_knobs_do_not_change_the_policy_hash():
    base = resources.preset_content("frugal")
    changed = ResourcePolicyContent.model_validate(_with("concurrency.study_max_parallel", 16))
    stopped = ResourcePolicyContent.model_validate(_with("study.on_budget_exhausted", "pause"))
    treated = ResourcePolicyContent.model_validate(_with("call.max_output_tokens", 4096))

    def view_hash(policy: ResourcePolicyContent) -> str:
        return identity.H(resources.values_by_class(policy, {"C", "R"}, budget_basis="usd"))

    assert view_hash(changed) == view_hash(base)
    assert view_hash(stopped) == view_hash(base)
    assert view_hash(treated) != view_hash(base)
