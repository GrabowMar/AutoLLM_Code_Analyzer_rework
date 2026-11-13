"""Run-order policies (spec §C.5): deterministic, stable under top-ups and added replicates."""

from __future__ import annotations

import itertools
import uuid
from types import SimpleNamespace

import pytest

from backend.execution.sequence import ConditionInfo
from backend.execution.sequence import cell_sequence
from backend.execution.sequence import condition_info
from backend.execution.sequence import estimate_swaps
from backend.execution.sequence import policies_by_endpoint
from backend.execution.sequence import resident_limit

STUDY = SimpleNamespace(order_seed=42, id=uuid.uuid4(), execution_options={})
# conditions 1, 2 → model A on a K=1 GPU; 3 → model B on the same GPU; 4 → OpenRouter
CONDITIONS = {
    1: ConditionInfo("A", "gpu", finite_k=True),
    2: ConditionInfo("A", "gpu", finite_k=True),
    3: ConditionInfo("B", "gpu", finite_k=True),
    4: ConditionInfo("C", "openrouter", finite_k=False),
}
UNITS = (10, 11, 12)


def _cells(conditions=(1, 2, 3), repeats=3):
    return [(c, u, r) for c in conditions for u in UNITS for r in range(repeats)]


def _ordered(sequence):
    return [cell for cell, _pos in sorted(sequence.items(), key=lambda kv: kv[1])]


@pytest.mark.parametrize("policy", ["interleaved", "blocked", "auto"])
def test_deterministic_and_unique(policy):
    first = cell_sequence(STUDY, _cells(), policy, conditions=CONDITIONS)
    second = cell_sequence(STUDY, reversed(_cells()), policy, conditions=CONDITIONS)
    assert first == second
    assert len(set(first.values())) == len(first) == len(_cells())
    other = SimpleNamespace(order_seed=43, id=STUDY.id, execution_options={})
    assert cell_sequence(other, _cells(), policy, conditions=CONDITIONS) != first


@pytest.mark.parametrize("policy", ["interleaved", "blocked"])
def test_repeat_blocks_run_in_order(policy):
    order = _ordered(cell_sequence(STUDY, _cells(), policy, conditions=CONDITIONS))
    repeats = [r for _c, _u, r in order]
    assert repeats == sorted(repeats)


def test_blocked_keeps_a_models_cells_contiguous_within_each_block():
    order = _ordered(cell_sequence(STUDY, _cells(), "blocked", conditions=CONDITIONS))
    for repeat in range(3):
        models = [CONDITIONS[c].model_key for c, _u, r in order if r == repeat]
        switches = sum(1 for x, y in itertools.pairwise(models) if x != y)
        assert switches == 1  # A…A then B…B (or the reverse): one swap per block
    assert estimate_swaps([CONDITIONS[c].model_key for c, _u, _r in order], 1) <= 2 * 3


def test_interleaved_thrashes_more_than_blocked():
    interleaved = _ordered(cell_sequence(STUDY, _cells(), "interleaved", conditions=CONDITIONS))
    blocked = _ordered(cell_sequence(STUDY, _cells(), "blocked", conditions=CONDITIONS))

    def swaps(order):
        return estimate_swaps([CONDITIONS[c].model_key for c, _u, _r in order], 1)

    assert swaps(blocked) <= swaps(interleaved)


def test_grouped_is_not_a_v3_policy():
    with pytest.raises(ValueError, match="Unknown scheduling policy"):
        cell_sequence(STUDY, _cells(), "grouped", conditions=CONDITIONS)


def test_top_up_keeps_positions():
    full = cell_sequence(STUDY, _cells(), "blocked", conditions=CONDITIONS)
    again = cell_sequence(STUDY, _cells(), "blocked", conditions=CONDITIONS)
    pending = {cell for cell in _cells() if cell[1] == 11}
    assert {cell: again[cell] for cell in pending} == {cell: full[cell] for cell in pending}


@pytest.mark.parametrize("policy", ["interleaved", "blocked", "auto"])
def test_adding_replicates_never_moves_existing_cells(policy):
    before = cell_sequence(STUDY, _cells(repeats=2), policy, conditions=CONDITIONS)
    after = cell_sequence(STUDY, _cells(repeats=4), policy, conditions=CONDITIONS)
    assert {cell: after[cell] for cell in before} == before
    assert min(after[c] for c in after if c[2] >= 2) > max(before.values())


def test_auto_resolves_per_endpoint():
    cells = _cells(conditions=(1, 3, 4), repeats=2)
    auto = cell_sequence(STUDY, cells, "auto", conditions=CONDITIONS)
    gpu_cells = [c for c in cells if c[0] != 4]
    blocked_gpu = cell_sequence(STUDY, gpu_cells, "blocked", conditions=CONDITIONS)
    gpu_auto = [c for c in _ordered(auto) if c[0] in (1, 3)]
    gpu_blocked = [c for c in _ordered(blocked_gpu) if c[0] in (1, 3)]
    assert gpu_auto == gpu_blocked
    assert policies_by_endpoint("auto", CONDITIONS) == {"gpu": "blocked", "openrouter": "interleaved"}
    assert policies_by_endpoint("blocked", CONDITIONS) == {"gpu": "blocked", "openrouter": "blocked"}


def test_unknown_policy_is_refused():
    with pytest.raises(ValueError, match="Unknown scheduling policy"):
        cell_sequence(STUDY, _cells(), "random", conditions=CONDITIONS)


def test_estimate_swaps():
    assert estimate_swaps(["a", "a", "b", "b", "a"], 1) == 3
    assert estimate_swaps(["a", "b", "a", "b"], 2) == 2
    assert estimate_swaps(["a", "b", "a", "b"], None) == 2


def test_resident_limit_takes_the_tighter_bound():
    ep = SimpleNamespace(is_system=False, max_resident_models=3)
    assert resident_limit(ep, SimpleNamespace(execution_options={"max_resident_models": 1})) == 1
    assert resident_limit(ep, SimpleNamespace(execution_options={})) == 3
    assert resident_limit(SimpleNamespace(is_system=True, max_resident_models=1), None) is None
    assert resident_limit(SimpleNamespace(is_system=False, max_resident_models=None), None) is None


def test_study_residency_applies_only_to_endpoints_that_host_models_locally():
    frugal = SimpleNamespace(execution_options={"max_resident_models": 1})

    def ep(**kw):
        base = {"is_system": False, "max_resident_models": None, "kind": "openai_compatible"}
        return SimpleNamespace(**{**base, "connection": "direct", "cost_basis": "billed", **kw})

    # A remote API (OpenCode Go: direct, list price, no declared K) serves every model at once.
    assert resident_limit(ep(cost_basis="list_price"), frugal) is None
    assert resident_limit(ep(), frugal) is None
    # Local hosts: Ollama, a relay (GPU box), a tokens-only endpoint, or one that declares its own K.
    assert resident_limit(ep(kind="ollama", connection="relay"), frugal) == 1
    assert resident_limit(ep(connection="relay"), frugal) == 1
    assert resident_limit(ep(cost_basis="free"), frugal) == 1
    assert resident_limit(ep(max_resident_models=3), frugal) == 1
    # The endpoint's own declared K always applies; the explicit options mapping overrides the study.
    assert resident_limit(ep(max_resident_models=2, cost_basis="list_price"), None) == 2
    assert resident_limit(ep(kind="ollama"), None, options={"max_resident_models": 2}) == 2


@pytest.mark.django_db
def test_condition_info_reads_models_and_endpoints():
    from backend.execution.tests.conftest import make_endpoint
    from backend.execution.tests.conftest import make_model
    from backend.generation.tests.factories import StudyConditionFactory
    from backend.users.tests.factories import UserFactory

    user = UserFactory()
    endpoint = make_endpoint(user, max_resident_models=1)
    local = StudyConditionFactory(model=make_model(endpoint, "qwen"))
    remote = StudyConditionFactory(plan=local.plan, model=make_model())
    info = condition_info(local.study, [local.id, remote.id])
    assert info[local.id] == ConditionInfo(local.model_id, endpoint.slug, finite_k=True)
    assert info[remote.id] == ConditionInfo(remote.model_id, "openrouter", finite_k=False)
