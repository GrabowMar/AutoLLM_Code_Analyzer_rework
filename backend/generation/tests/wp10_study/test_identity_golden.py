"""Golden plan test (spec section 13.3, layer 2; WP-10, wave 3).

CHANGING ANY CONSTANT IN THIS FILE REQUIRES A PROTOCOL BUMP AND A CHANGELOG LINE IN
``docs/STUDIES.md#identity-changelog``. A failure here means a run's identity would change for the same inputs:
fix the code that drifted, or (only for an intended identity change) bump the protocol, record it in the
changelog and regenerate the constants with ``GOLDEN_PLAN_DUMP=1`` (prints the observed values).

The plan is built from the frozen golden library (``generation/testing/golden_library/``, not the evolving seed
data) with the real prompt renderer (WP-03), the real effective policy and treatment view (WP-11) and the real
cell environment (harness registry, WP-07/WP-08). Two inputs are machine state rather than library content and
are pinned here so that the keys depend only on the library, the renderer, the policy view and
``identity.compute_keys``:

* image digests (``config_fingerprint.images``): a docker daemon may or may not hold the images, and their tags
  hash the image build contexts; they are replaced by :data:`IMAGES`;
* estimates and conformance coverage: not identity inputs (the WP-10 fakes bind them).

The design: 2 tasks x 2 stacks x 2 models x 1 style x 1 auth mode x 2 prose variants x 2 repeats, base_seed 12345
(order_seed 7), resource policy ``frugal`` (pilot: 1 repeat of the first 3 units, so both tasks).
"""

from __future__ import annotations

import dataclasses
import json
import os
from typing import Any

import pytest

from backend.generation import identity
from backend.generation.models import LIBRARY_MODELS
from backend.generation.models import TaskSpecVersion
from backend.generation.study import plan as planner
from backend.generation.study.deps import default_deps
from backend.generation.study.deps import override_deps
from backend.generation.study.launch import create_study
from backend.generation.testing.fake_study import FakeStudyEnv
from backend.generation.testing.golden_library import load_golden_library

from .conftest import MODELS

pytestmark = pytest.mark.django_db

# Fixed stand-ins for the image digests (machine state, see the module docstring).
IMAGES: dict[str, Any] = {
    "harness": "sha256:" + "a1" * 32,
    "gateway": "sha256:" + "b2" * 32,
}

DESIGN: dict[str, Any] = {
    "mode": "app",
    "kind": "exploratory",
    "factors": [
        {"key": "model", "role": "crossed", "levels": list(MODELS)},
        {"key": "stack_profile", "role": "crossed", "levels": ["flask-react@1", "fastapi-vue@1"]},
        {"key": "prose_variant", "role": "crossed", "levels": ["minimal", "standard"]},
        {"key": "style", "role": "fixed", "levels": ["model-choice@1"]},
        {"key": "auth_mode", "role": "fixed", "levels": ["contract_bearer"]},
    ],
    "units": {"tasks": ["crud_todo_list@1", "notes_markdown_render@1"]},
    "repeats": 2,
    "base_seed": 12345,
    "order_seed": 7,
    "resource_policy": "frugal@1",
    "evaluation": {"eval_suite": "eval-v2@1"},
}

# The three cells whose keys are frozen in full.
CHOSEN = ("c0-u0-r0-main", "c5-u1-r1-main", "c3-u0-r0-pilot")
KEY_NAMES = ("task_hash", "treatment_hash", "prompt_hash", "config_fingerprint", "run_fingerprint")

# ---- frozen values (regenerate only with a protocol bump; see the module docstring) ----------------------------
CONTENT_HASHES: dict[str, str] = {
    "detector:py-sql-fstring": "9f062be1810751b3b22424b9105c99a51f14771241062d5aba464923924eed57",
    "eval_suite:eval-v2": "7b58d5ac48cee115ed839eb90dbde2ed2cdf1beddc1621cbc3513d453f790876",
    "guidance:generic": "c1ecd23414e23b6f076fb9ad098255f91da3f01bbcd3077b7523eafd0eefedef",
    "guidance:none": "f777d9b952b107c1ca4c54f6c7fe62cd024294f7d3d4921c7f4a9c4bb49ee344",
    "harness_preset:opencode-controlled": "18f8e1103d74a93458e30c23b3456945492ac9a4b857ab4384013d7b5ec53fe1",
    "harness_preset:opencode-native": "93f23e2de042a1c4785e40e4ec797685c09fea2dc6638068aaf4794f9ce9fb64",
    "prompt:auth-contract-contract_bearer": "d07ba3633ed478e6a38e6721e686600495e80c00a2c7e0ba36cfe6c90ac44a7f",
    "prompt:auth-contract-cookie_session": "f37e7e5744f476a0735cd52067d217868aa91806d077ae0b8e51d59ce2104516",
    "prompt:auth-contract-spec_defined": "78181b651eacb3c9df31e81d639a17ea2b8a797f5166fd72548341e6dd181c7c",
    "prompt:brief-app-default": "3c5d369e5d503a81afc04f645cf21038c3a0f72ef101354e9a0175ef051f151c",
    "prompt:budget-neutral": "3fbb6b600facf14f25f94430342d6063c2147ccdf8203fac3ba3d31276c0a3e0",
    "prompt:guidance-generic": "36b398a5e7238edf4de0f45bc045a9f52ca85fb650c44c6dad3a55e951d41331",
    "prompt:network-none": "939339d9c888c73069dcf9386cd75aee61d3474a56ee49cdb3f9753391d380b5",
    "prompt:network-packages": "1b20bb653ec26efb53f13e8d069e9da44eb7bc1fd4d970e32e65a476063f7030",
    "prompt:slot-agent-brief": "b58e72ddf1d5bbe6043625864accd9c4f8db29ac62b51ce83d75e6014cc0f38f",
    "prompt:slot-single-shot-system": "8888397787f9a8339578b80067a56ae8640dc49651895550fd8c210aeea5f4c9",
    "prompt:slot-single-shot-user": "9b948f24f0716a0d9ebbdccd20fa98759541ed68a17df9ef5149356f370eece5",
    "prompt:stack-fastapi-vue": "3e1fbedc07b32e639b08d9fe2e4573bec831c36fa8b9403fa100debdffc8d95c",
    "prompt:stack-flask-react": "2b069ba8cdf8be96634d7d4217a3327ce42ccd010244129ed6b9b7989e013b57",
    "prompt:style-model-choice": "596dec67361067e713805d3a3e662c726b991515241587ffc292df256a003937",
    "prompt:style-tailwind": "f3e0ec3112b38fc6dcb5f6e42dac9fd784e83a631698a45c1c4a4bf86e3ad3b6",
    "prompt_set:app-default": "4ec8d571fa5520a9bd3f94fea99f79bcd354249c804795ddd9aebd60660045ef",
    "prompt_set:slot-agent-default": "5b2756743bb26da004958abe79f9ea136f2ffebba5af42d7e8ef21c9c0eef4e5",
    "prompt_set:slot-single-shot-default": "03d383625c40eae362c9c093367408db1b4f469943bb29b031699dac1b77dffa",
    "resource_policy:frugal": "2057b33cec07c48daaa5276e78b03f0e888a64970cd1b7421ffce76be9203e27",
    "sampling:provider-default": "8122d02240e3036a77c66ad668281e56b8ec0aab91a857a45f8a92f2c9c4a35e",
    "sampling:t07": "d21765e9ad0918f6ce7747f716f8ea5c8c980d23cf41b916058ee32550c1d0e7",
    "stack:fastapi-vue": "48adfa14f241d13e3c8df91dda9a7e487344532299c2f1740aad98ea36011559",
    "stack:flask-react": "59ad6388e7653674bbc766b5200dbe2d2246910ca11bd63eff5856463bcb97b7",
    "starter:fastapi-vue-bare": "5b1840ed5e02ae8af3dc7279d3983acc246431cb17eeb28ea0e85b4892b59cee",
    "starter:fastapi-vue-minimal": "08f22f1f4527c415b36f954f0602377dba2ccd9652ab47e05a5d88ca49e1fe43",
    "starter:flask-react-minimal": "39ecf6e8baabaac72972fcdc9d697d02d2b213721a707cc8d9d9b6e0eff17d8d",
    "style:model-choice": "7e19c1feac9bc56a9ea2f02e4a3edaf13fcd55a02e4db3b2da9af26c786d3b1a",
    "style:tailwind": "145b9e940cf3d328f7dfe54476be6a3c46b3850965f9db7e37f48c5fee163003",
    "task:crud_todo_list": "53048f6ec6f417e588584ab07c982e2f391734f1856c91d4e673cd69d0497867",
    "task:notes_markdown_render": "8caff467fee8dde3dc3887dfb3bae8396520fd4aab4e764c113999959ce77c79",
}
DESIGN_HASH = "967da6cc40ea24fd50747f2ae1c949db56c5f1e8aa01cb658aeb151f6e559b33"
PLAN_HASH = "ca4041358c057f3a685110281526843a29246c44ff05670157bdeefb81bcbc27"
UNIT_KEYS = ["task:crud_todo_list@1", "task:notes_markdown_render@1"]
CONDITION_HASHES = [
    "0cee495a2b4286d6ebc2e0c52a7344b89cb5b6b436e41c5ef4c5ddbb78c56e7f",
    "646ee6a732de667e86f0497c7cda70c21e294ce3ba61c3206fb995dbc42febce",
    "fa91934417eede6ca34b0a395cdd322842bb696a4f42cb5f57d3a264234f85dc",
    "a69b1f7d9abd90c593705b36afd993c5b075ea3e3f539a9df3171d7ea45cab8e",
    "67d0d3c78beec790bc4ab281424dff91aa488d067153d896341e2249cf57f87e",
    "c14f151b82e0026a179bf3a94afbdd358bea5d2aab101780a5db432c38c16e94",
    "b39060650a45123fc6e4e6cc9c0882232e2fb2f6ac0bafee6277075af920c170",
    "602a5a45a84adca7aef0ff7cc9d8eaf64f60fde1710a2d56638ef18be7c25510",
]
# (unit_index, repeat_index) -> CRN seed: one seed for every condition, prose variants included.
SEEDS: dict[tuple[int, int], int] = {
    (0, 0): 1712254510,
    (0, 1): 550057696,
    (1, 0): 107474531,
    (1, 1): 542601949,
}
# Every cell in sequence order as (cell_key, block_index): pilot blocks first, then repeat-major main blocks.
ORDER: list[tuple[str, int]] = [
    ("c6-u1-r0-pilot", 0),
    ("c4-u1-r0-pilot", 0),
    ("c0-u1-r0-pilot", 0),
    ("c2-u1-r0-pilot", 0),
    ("c1-u1-r0-pilot", 0),
    ("c3-u1-r0-pilot", 0),
    ("c5-u1-r0-pilot", 0),
    ("c7-u1-r0-pilot", 0),
    ("c2-u0-r0-pilot", 1),
    ("c1-u0-r0-pilot", 1),
    ("c0-u0-r0-pilot", 1),
    ("c4-u0-r0-pilot", 1),
    ("c5-u0-r0-pilot", 1),
    ("c6-u0-r0-pilot", 1),
    ("c3-u0-r0-pilot", 1),
    ("c7-u0-r0-pilot", 1),
    ("c1-u1-r0-main", 2),
    ("c0-u1-r0-main", 2),
    ("c7-u1-r0-main", 2),
    ("c6-u1-r0-main", 2),
    ("c4-u1-r0-main", 2),
    ("c5-u1-r0-main", 2),
    ("c2-u1-r0-main", 2),
    ("c3-u1-r0-main", 2),
    ("c1-u0-r0-main", 3),
    ("c7-u0-r0-main", 3),
    ("c4-u0-r0-main", 3),
    ("c2-u0-r0-main", 3),
    ("c5-u0-r0-main", 3),
    ("c0-u0-r0-main", 3),
    ("c3-u0-r0-main", 3),
    ("c6-u0-r0-main", 3),
    ("c7-u1-r1-main", 4),
    ("c5-u1-r1-main", 4),
    ("c3-u1-r1-main", 4),
    ("c2-u1-r1-main", 4),
    ("c0-u1-r1-main", 4),
    ("c4-u1-r1-main", 4),
    ("c6-u1-r1-main", 4),
    ("c1-u1-r1-main", 4),
    ("c3-u0-r1-main", 5),
    ("c5-u0-r1-main", 5),
    ("c6-u0-r1-main", 5),
    ("c1-u0-r1-main", 5),
    ("c2-u0-r1-main", 5),
    ("c0-u0-r1-main", 5),
    ("c7-u0-r1-main", 5),
    ("c4-u0-r1-main", 5),
]
CELL_KEYS: dict[str, dict[str, str]] = {
    "c0-u0-r0-main": {
        "task_hash": "3fe4ddf800059c76415e0a899e4655d2c9edd1c76e28d2555b4fce6e88644ebb",
        "treatment_hash": "9b40615c1157afa2296a0fff507771e3735aeecc71ac1ca1b4dbed00b6eb5cf7",
        "prompt_hash": "78759e90cd5cfdfb9a1d6bb66c66000e4131f1d10c5a0d0936a9782558de990d",
        "config_fingerprint": "c8215e2dadb52ff3e8b123459673d20bf1cfa069b25c523a7f1ff76b86d0454b",
        "run_fingerprint": "0d467698b174a977b6fff6491450e217f63ee73b59a9768baf822aa9421e9b79",
    },
    "c5-u1-r1-main": {
        "task_hash": "0aa8707a4ee26bc9efbf4890e40c8dd580fb2a90c5a7b5234942686c5e693105",
        "treatment_hash": "9b40615c1157afa2296a0fff507771e3735aeecc71ac1ca1b4dbed00b6eb5cf7",
        "prompt_hash": "969a71cd0769d2fadaa6e10da0568fb8d893000054b266a00e29d58caf3b905c",
        "config_fingerprint": "3ae2b3845d2c5f26dc7825b5afcf9cfcc0324588351c289220f5b6cbd2422c53",
        "run_fingerprint": "5b8f961d81b614e501c26dade2a5d873603c0958ec6148afcd012ea49a26ca3b",
    },
    "c3-u0-r0-pilot": {
        "task_hash": "b0709a245d8818080e730b145eb3ed68fd1a9a8c6913503a7d026733582702e3",
        "treatment_hash": "57014842fb752a0421acd4c00dbd471f6049eb77e8d3ee22e9ea0d843238a530",
        "prompt_hash": "4062ab7055ef7fc220e9e3cd8b720e5b90a5b1064c62bdf83fae412b9268945c",
        "config_fingerprint": "82eadf80e36b578ecc79da6613e1d7bcff1c222d9a10492630721bc75bc7218e",
        "run_fingerprint": "e72b43dfb4eccc968fde7e0092f4cb4e6d2cc78f027c74551f4ca3e7e1973d3d",
    },
}


def _golden_environment(request):
    env = default_deps().environment(request)
    images = dict(env.images)
    if images.get("harness") is not None:
        images["harness"] = IMAGES["harness"]
    if images.get("gateway") is not None:
        images["gateway"] = IMAGES["gateway"]
    return dataclasses.replace(env, images=images)


class _NoDocker:
    class images:  # noqa: N801 - mimics docker's client.images
        @staticmethod
        def get(_tag):
            raise LookupError


@pytest.fixture
def golden_plan(golden, user, monkeypatch):
    from backend.runtime.services import docker_manager

    monkeypatch.setattr(docker_manager, "new_client", _NoDocker)
    fakes = FakeStudyEnv()
    planner.clear_cache()
    with override_deps(
        default_deps(),
        environment=_golden_environment,
        estimate_cell=fakes.estimate_cell,
        estimate_plan=fakes.estimate_plan,
        conformance_coverage=lambda: dict.fromkeys(("flask", "fastapi"), True),
    ):
        study = create_study(user=user, name="Golden", design=json.loads(json.dumps(DESIGN)))
        plan = planner.resolve(study)
    planner.clear_cache()
    return plan


def _observed(plan) -> dict[str, Any]:
    """Every frozen value as computed now (GOLDEN_PLAN_DUMP=1 prints it)."""
    by_key = {cell.cell_key: cell for cell in plan.cells}
    return {
        "content_hashes": {entry.key: entry.content_hash() for entry in load_golden_library()},
        "design_hash": plan.design_hash,
        "plan_hash": plan.plan_hash,
        "unit_keys": [unit.unit_key for unit in plan.units],
        "condition_hashes": [condition.condition_hash for condition in plan.conditions],
        "seeds": {f"{cell.unit_index}/{cell.repeat_index}": cell.seed for cell in plan.cells},
        "order": [[cell.cell_key, cell.block_index] for cell in sorted(plan.cells, key=lambda cell: cell.sequence)],
        "cell_keys": {
            key: {name: getattr(by_key[key].manifest.identity, name) for name in KEY_NAMES} for key in CHOSEN
        },
    }


def test_golden_content_hashes_match_the_installed_rows(golden):
    observed = {entry.key: entry.content_hash() for entry in load_golden_library()}
    assert observed == CONTENT_HASHES
    for entry in load_golden_library():
        if entry.kind == "task":
            row = TaskSpecVersion.objects.get(task__slug=entry.slug, version=1)
        else:
            row = LIBRARY_MODELS[entry.kind].objects.get(slug=entry.slug, version=1)
        assert row.content_hash == CONTENT_HASHES[entry.key], entry.key


def test_golden_plan_yields_the_frozen_keys_cells_and_seeds(golden_plan):
    plan = golden_plan
    if os.environ.get("GOLDEN_PLAN_DUMP"):
        print("GOLDEN_PLAN_DUMP", json.dumps(_observed(plan), indent=2))  # noqa: T201
    assert not [issue for issue in plan.issues if issue.severity == "error"], plan.issues
    # Shape: 2 models x 2 stacks x 2 prose variants = 8 conditions; 2 task units; 2 main repeats + 1 pilot repeat.
    assert len(plan.conditions) == 8
    assert [unit.unit_key for unit in plan.units] == UNIT_KEYS
    assert [condition.condition_hash for condition in plan.conditions] == CONDITION_HASHES
    assert plan.design_hash == DESIGN_HASH
    # Every seed, equal across conditions (prose variants included) for one (unit, repeat), in both stages.
    for cell in plan.cells:
        assert cell.seed == SEEDS[(cell.unit_index, cell.repeat_index)], cell.cell_key
        assert cell.seed == identity.crn_seed(DESIGN["base_seed"], cell.unit_key, cell.repeat_index)
        assert cell.manifest.sampling is None or cell.manifest.sampling.seed == cell.seed
    # The block order (sequence order and block index of every cell).
    ordered = sorted(plan.cells, key=lambda cell: cell.sequence)
    assert [cell.sequence for cell in ordered] == list(range(len(ORDER)))
    assert [(cell.cell_key, cell.block_index) for cell in ordered] == ORDER
    # The identity keys of the three chosen cells; the manifest carries the same keys.
    by_key = {cell.cell_key: cell for cell in plan.cells}
    for key, expected in CELL_KEYS.items():
        manifest_keys = by_key[key].manifest.identity
        assert {name: getattr(manifest_keys, name) for name in KEY_NAMES} == expected, key
    assert plan.prompt_hashes == {cell.cell_key: cell.manifest.identity.prompt_hash for cell in plan.cells}
    assert plan.plan_hash == PLAN_HASH


def test_golden_identity_is_paired_across_conditions(golden_plan):
    """Structure behind the frozen values: what each key depends on (section 3.1)."""
    plan = golden_plan
    by_key = {cell.cell_key: cell.manifest.identity for cell in plan.cells}
    levels = {condition.index: condition.levels for condition in plan.conditions}
    # Pilot and main share the CRN seed, so a (condition, unit, repeat 0) has one run fingerprint in both stages.
    for cell in plan.cells:
        if cell.stage == "pilot":
            twin = by_key[cell.cell_key.replace("-pilot", "-main")]
            assert twin.run_fingerprint == by_key[cell.cell_key].run_fingerprint
    # The treatment does not take the model or the prose variant; the task hash takes the variant, not the model.
    assert by_key["c0-u0-r0-main"].treatment_hash == by_key["c5-u0-r0-main"].treatment_hash
    assert levels[0]["model"] != levels[5]["model"]
    assert by_key["c0-u0-r0-main"].task_hash == by_key["c4-u0-r0-main"].task_hash
    assert by_key["c0-u0-r0-main"].task_hash != by_key["c1-u0-r0-main"].task_hash
    # Repeats change the run fingerprint only.
    assert by_key["c0-u0-r0-main"].config_fingerprint == by_key["c0-u0-r1-main"].config_fingerprint
    assert by_key["c0-u0-r0-main"].run_fingerprint != by_key["c0-u0-r1-main"].run_fingerprint
