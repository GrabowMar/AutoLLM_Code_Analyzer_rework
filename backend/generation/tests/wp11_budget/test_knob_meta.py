"""``KNOB_META`` matches the section 4.2 table of the spec (class, enforcement point, key limits).

The spec rows are parsed from ``docs/design/SAMPLE_GEN_V3.md``: a row lists one or more keys
(```a``` / ```b```), the enforcement points and the validity class. Default values are pinned by
WP-01's ``test_resources.py``; this test pins the classification the treatment hash depends on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.generation.contracts.resources import KEY_LIMITS
from backend.generation.contracts.resources import KNOB_META

SPEC = Path(__file__).resolve().parents[4] / "docs" / "design" / "SAMPLE_GEN_V3.md"
_CODES = {"GW", "SBX", "HAR", "SCH", "LED", "STU", "EXE", "PLN", "SLT", "EVL", "UI"}
_GATES = (
    "max_infra_error_rate",
    "min_build_rate",
    "max_cost_overrun",
    "max_cap_hit_rate",
    "max_truncation_rate",
    "max_call_truncation_rate",
    "max_wall_clock_rate",
    "max_oom_rate",
    "usd_cap_binding",
)


def _section_42() -> list[str]:
    text = SPEC.read_text(encoding="utf-8")
    start = text.index("### 4.2 Knob catalogue")
    end = text.index("### 4.3 Effective policy")
    return text[start:end].splitlines()


def _rows() -> dict[str, tuple[set[str], str]]:
    """``{knob key: (enforcement points, class)}`` from the 4.2 tables (``Where`` and ``Cls`` columns)."""
    out: dict[str, tuple[set[str], str]] = {}
    for line in _section_42():
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if len(cells) < 8 or not cells[0].startswith("`"):
            continue
        keys = re.findall(r"`([a-z_.]+)`", cells[0])
        where_text = cells[3].replace("build", "BLD")
        where = set(re.findall(r"\b[A-Z]{2,3}\b", where_text))
        cls = cells[4].split()[0]
        prefix = keys[0].rsplit(".", 1)[0] + "." if "." in keys[0] else ""
        for key in keys:
            full = key if "." in key or not prefix else prefix + key
            if full != "pilot.gates":  # expanded into one knob per gate (tested below)
                out[full] = (where, cls)
    return out


SPEC_ROWS = _rows()


def test_the_table_was_parsed():
    assert len(SPEC_ROWS) >= 60
    assert "run.max_cost_usd" in SPEC_ROWS
    assert "estimate.require_confirm_above_usd" in SPEC_ROWS


@pytest.mark.parametrize("key", sorted(SPEC_ROWS))
def test_knob_class_and_enforcement_point_match_the_spec(key):
    where, cls = SPEC_ROWS[key]
    meta = KNOB_META[key]
    assert meta.cls == cls, key
    assert set(meta.where) & _CODES >= where & _CODES, (key, meta.where, where)


def test_every_spec_knob_and_gate_has_meta_and_nothing_else():
    expected = set(SPEC_ROWS) | {f"pilot.gates.{gate}" for gate in _GATES}
    assert set(KNOB_META) == expected


def test_gates_are_stopping_rules_evaluated_by_stu():
    for gate in _GATES:
        meta = KNOB_META[f"pilot.gates.{gate}"]
        assert (meta.cls, meta.where) == ("S", ("STU",))


def test_run_cap_is_a_backstop_under_the_tokens_basis():
    assert KNOB_META["run.max_cost_usd"].cls_when_tokens == "O"


def test_key_limits_are_the_always_visible_group():
    # Study cap, run cap, cell cap, max parallel, reasoning, serving tier, pilot on/off ("repeats" is a column).
    assert set(KEY_LIMITS) == {
        "study.max_cost_usd",
        "run.max_cost_usd",
        "cell.max_cost_usd",
        "concurrency.study_max_parallel",
        "reasoning",
        "routing.serving_tier",
        "pilot.enabled",
    }


def test_the_editor_payload_has_every_knob():
    from backend.generation.budget.policy import knob_meta_payload

    payload = knob_meta_payload()
    assert {row["key"] for row in payload} == set(KNOB_META)
    assert {"class", "where", "ui_group", "key_limit", "description", "defaults"} <= set(payload[0])
