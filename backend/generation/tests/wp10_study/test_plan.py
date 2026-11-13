"""Resolver tests (spec sections 6.1, 9.4, 13.4): cells, pairing, seeds, block order, plan hash, persistence."""

from __future__ import annotations

from collections import Counter
from collections import defaultdict

import pytest

from backend.generation import identity
from backend.generation.models import GenerationRun
from backend.generation.models import ResourcePolicy
from backend.generation.models import StudyCell
from backend.generation.models import StudyCondition
from backend.generation.models import StudyPlan
from backend.generation.models import StyleDirective
from backend.generation.study import plan as planner
from backend.generation.study.design import DesignError
from backend.generation.study.launch import create_study
from backend.generation.study.launch import update_study
from backend.generation.testing.content import resource_policy_content

from .conftest import app_design

pytestmark = pytest.mark.django_db


def _main(plan):
    return [cell for cell in plan.cells if cell.stage == "main"]


def test_done_when_design_resolves_into_64_main_cells_with_pairing(make_study, fake_study):
    study = make_study(
        factors=[
            *app_design()["factors"],
            {"key": "prose_variant", "role": "crossed", "levels": ["minimal", "standard"]},
        ],
    )
    plan = planner.resolve(study)
    main = _main(plan)
    # 2 models x 2 stacks x 2 styles x 2 prose variants = 16 conditions; x 2 tasks x 2 repeats.
    assert len(plan.conditions) == 16
    assert len(plan.units) == 2
    assert len(main) == 64
    blocks = defaultdict(set)
    for cell in main:
        blocks[cell.block_index].add((cell.repeat_index, cell.unit_index))
    assert all(len(members) == 1 for members in blocks.values())
    assert len(blocks) == 4  # one block per (repeat, unit)
    # CRN: every condition (prose variants included) gets the same seed for one (unit, repeat).
    seeds = defaultdict(set)
    for cell in plan.cells:
        seeds[(cell.unit_key, cell.repeat_index)].add(cell.seed)
        assert cell.seed == identity.crn_seed(12345, cell.unit_key, cell.repeat_index)
    assert all(len(values) == 1 for values in seeds.values())
    # Each (condition, unit) has every repeat once: the grid is balanced.
    counts = Counter((cell.condition_index, cell.unit_index) for cell in main)
    assert set(counts.values()) == {2}
    assert not [i for i in plan.issues if i.severity == "error"], plan.issues


def test_pilot_cells_come_first_and_share_main_seeds(make_study, fake_study):
    plan = planner.resolve(make_study())
    pilot = [cell for cell in plan.cells if cell.stage == "pilot"]
    main = _main(plan)
    # frugal: pilot.repeats=1 over 3 units (only 2 exist) -> every condition x unit once.
    assert len(pilot) == 8 * 2
    assert max(c.sequence for c in pilot) < min(c.sequence for c in main)
    assert max(c.block_index for c in pilot) < min(c.block_index for c in main)
    main_seeds = {(c.condition_index, c.unit_index, c.repeat_index): c.seed for c in main}
    for cell in pilot:
        assert cell.seed == main_seeds[(cell.condition_index, cell.unit_index, cell.repeat_index)]
        assert cell.cell_key.endswith("-pilot")


def test_block_order_is_repeat_major_and_deterministic_per_order_seed(make_study, fake_study):
    a = planner.resolve(make_study(name="a"))
    planner.clear_cache()
    b = planner.resolve(make_study(name="b"))
    order = [
        (c.stage, c.repeat_index, c.unit_index, c.condition_index) for c in sorted(a.cells, key=lambda c: c.sequence)
    ]
    assert order == [
        (c.stage, c.repeat_index, c.unit_index, c.condition_index) for c in sorted(b.cells, key=lambda c: c.sequence)
    ]
    main = sorted(_main(a), key=lambda c: c.sequence)
    repeats = [c.repeat_index for c in main]
    assert repeats == sorted(repeats)  # every unit gets replicate 0 before any gets replicate 1
    other = planner.resolve(make_study(name="c", order_seed=99))
    other_order = [
        (c.repeat_index, c.unit_index, c.condition_index) for c in sorted(_main(other), key=lambda c: c.sequence)
    ]
    assert other_order != [(c.repeat_index, c.unit_index, c.condition_index) for c in main]
    assert {c.seed for c in other.cells} == {c.seed for c in a.cells}  # order_seed never changes seeds


def test_plan_hash_is_stable_and_sensitive_to_every_pinned_level(make_study, fake_study, golden, user):
    study = make_study()
    first = planner.resolve(study)
    planner.clear_cache()
    assert planner.resolve(study).plan_hash == first.plan_hash
    changed = make_study(name="other", base_seed=1)
    assert planner.resolve(changed).plan_hash != first.plan_hash
    # A new version of a library level that a bare slug names moves the plan (latest is pinned at plan time).
    factors = [*app_design()["factors"][:2], {"key": "style", "role": "crossed", "levels": ["tailwind"]}]
    bare = make_study(name="bare", factors=factors)
    bare_hash = planner.resolve(bare).plan_hash
    tailwind = StyleDirective.objects.get(slug="tailwind", version=1)
    content = dict(tailwind.content, tone_text="Calm and dense.")
    v2 = StyleDirective.objects.create(
        slug="tailwind",
        version=2,
        name="tailwind v2",
        content=content,
        created_by=user,
    )
    planner.clear_cache()
    rebased = planner.resolve(bare)
    assert rebased.plan_hash != bare_hash
    assert {c.levels["style"] for c in rebased.conditions} == {v2.pinned_ref}
    # A pinned level keeps its version.
    pinned = make_study(
        name="pinned",
        factors=[*factors[:2], {"key": "style", "role": "crossed", "levels": ["tailwind@1"]}],
    )
    assert {c.levels["style"] for c in planner.resolve(pinned).conditions} == {tailwind.pinned_ref}


def test_ephemeral_resolve_writes_no_rows_and_freeze_persists(make_study, fake_study):
    study = make_study()
    before = (StudyPlan.objects.count(), StudyCondition.objects.count(), StudyCell.objects.count())
    planner.resolve(study)
    assert (StudyPlan.objects.count(), StudyCondition.objects.count(), StudyCell.objects.count()) == before
    assert GenerationRun.objects.count() == 0
    row = planner.freeze(study)
    study.refresh_from_db()
    assert study.status == "planned"
    assert row.n_cells == len(planner.resolve(study).cells)
    loaded = planner.load_bundle(row).plan
    assert loaded.plan_hash == row.plan_hash
    assert study.factors.count() == 3


def test_identity_keys_come_from_compute_keys_and_prompts_ignore_the_policy(make_study, fake_study, golden, user):
    study = make_study()
    plan = planner.resolve(study)
    cell = plan.cells[0]
    assert cell.manifest.identity is not None
    assert cell.manifest.sampling.seed == cell.seed
    # Same rendered stimuli under another budget preset: equal prompt_hash (budget never in prompts).
    generous = ResourcePolicy.objects.create(
        slug="generous",
        version=1,
        name="generous",
        content=resource_policy_content("generous"),
        tier="generous",
        created_by=user,
    )
    other = make_study(name="generous", resource_policy=generous.ref)
    other_plan = planner.resolve(other)
    by_key = {c.cell_key: c for c in other_plan.cells}
    for cell in _main(plan):
        twin = by_key[cell.cell_key]
        assert twin.manifest.identity.prompt_hash == cell.manifest.identity.prompt_hash
        assert twin.manifest.identity.generation_policy_hash != cell.manifest.identity.generation_policy_hash
    assert plan.prompt_hashes == {c.cell_key: c.manifest.identity.prompt_hash for c in plan.cells}


def test_incompatible_style_is_an_error_unless_dropped(make_study, fake_study, golden, user):
    from backend.generation.testing.content import style_content

    content = style_content(system="tailwind", fragment=golden.pinned_ref("prompt", "style-tailwind"))
    content["compatible_frontends"] = ["react"]
    StyleDirective.objects.create(slug="react-only", version=1, name="react only", content=content, created_by=user)
    factors = [
        {"key": "model", "role": "crossed", "levels": ["deepseek/deepseek-v4.1-flash"]},
        {"key": "stack_profile", "role": "crossed", "levels": ["flask-react@1", "fastapi-vue@1"]},
        {"key": "style", "role": "crossed", "levels": ["react-only@1"]},
    ]
    study = make_study(factors=factors)
    plan = planner.resolve(study)
    assert any(i.code == "incompatible_style" and i.severity == "error" for i in plan.issues)
    dropped = planner.resolve(study, drop_incompatible=True)
    assert len(dropped.conditions) == 1
    codes = {i.code for i in dropped.issues}
    assert {"combination_pruned", "unbalanced_grid"} <= codes
    assert not [i for i in dropped.issues if i.severity == "error"]


def test_exclusions_and_unknown_refs(make_study, fake_study):
    study = make_study(grid={"type": "full", "exclude": [{"style": "tailwind@1", "stack_profile": "fastapi-vue@1"}]})
    plan = planner.resolve(study)
    assert len(plan.conditions) == 6
    assert any(i.code == "unbalanced_grid" for i in plan.issues)
    broken = make_study(
        name="broken",
        factors=[
            *app_design()["factors"][:2],
            {"key": "style", "role": "crossed", "levels": ["model-choice@1", "nope@3"]},
        ],
    )
    plan = planner.resolve(broken)
    assert len(plan.conditions) == 4
    assert any(i.code == "lint_unknown_library_ref" and i.severity == "error" for i in plan.issues)


def test_structural_errors_raise(golden, user):
    with pytest.raises(DesignError, match="not allowed in app mode"):
        create_study(
            user=user,
            name="x",
            design=app_design(
                factors=[*app_design()["factors"], {"key": "hole_mode", "role": "crossed", "levels": ["stub"]}],
            ),
        )
    with pytest.raises(DesignError, match="analysis_plan"):
        create_study(user=user, name="x", design=app_design(kind="confirmatory"))


def test_legacy_per_task_variant_units_are_flagged(make_study, fake_study):
    study = make_study(units={"tasks": ["crud_todo_list@1:minimal"]})
    plan = planner.resolve(study)
    assert any(i.code == "prose_variant_in_unit" and i.severity == "error" for i in plan.issues)


def test_design_edit_after_freeze_makes_the_plan_stale(make_study, fake_study):
    study = make_study()
    planner.freeze(study)
    study = update_study(study, design=app_design(repeats=3))
    assert study.status == "draft"
