"""canonical_json / H / text_sha / tree_hash vectors and IdentityInputs validation (no database)."""

from __future__ import annotations

import dataclasses
import hashlib
from decimal import Decimal

import pytest

from backend.generation import identity
from backend.generation.identity import CanonicalJSONError
from backend.generation.identity import IdentityInputs
from backend.generation.identity import compute_keys
from backend.generation.tests.wp01_foundation.test_identity_algebra import APP_INPUTS
from backend.generation.tests.wp01_foundation.test_identity_algebra import SLOT_INPUTS

SHA = "a" * 64


# --- canonical_json -------------------------------------------------------------------------------


class TestCanonicalJson:
    def test_sorted_keys_and_compact_separators(self):
        assert identity.canonical_json({"b": 1, "a": {"d": [], "c": {}}}) == b'{"a":{"c":{},"d":[]},"b":1}'

    def test_key_order_is_code_point_order(self):
        assert identity.canonical_json({"b": 0, "a": 0, "B": 0, "_": 0}) == b'{"B":0,"_":0,"a":0,"b":0}'

    def test_insertion_order_does_not_matter(self):
        assert identity.H({"x": 1, "y": 2}) == identity.H({"y": 2, "x": 1})

    def test_scalars(self):
        assert identity.canonical_json([None, True, False, 0, -3, 1.5, "s"]) == b'[null,true,false,0,-3,1.5,"s"]'

    def test_int_and_float_are_distinct(self):
        assert identity.canonical_json(1) == b"1"
        assert identity.canonical_json(1.0) == b"1.0"

    def test_nfc_normalization_of_values_and_keys(self):
        decomposed = "é"  # e + combining acute
        composed = "é"
        assert identity.canonical_json(decomposed) == identity.canonical_json(composed)
        assert identity.canonical_json({decomposed: 1}) == '{"é":1}'.encode()
        assert identity.H({"k": decomposed}) == identity.H({"k": composed})

    def test_utf8_not_ascii_escaped(self):
        assert identity.canonical_json("zażółć") == '"zażółć"'.encode()

    def test_crlf_and_cr_become_lf(self):
        assert identity.canonical_json("a\r\nb\rc\nd") == b'"a\\nb\\nc\\nd"'
        assert identity.H(["x\r\n"]) == identity.H(["x\n"])

    def test_tuples_become_lists(self):
        assert identity.canonical_json((1, (2, 3))) == identity.canonical_json([1, [2, 3]])

    def test_none_is_kept(self):
        assert identity.canonical_json({"a": None}) == b'{"a":null}'

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_floats_rejected(self, value):
        with pytest.raises(CanonicalJSONError, match="non-finite"):
            identity.canonical_json({"x": [value]})

    @pytest.mark.parametrize("key", [1, 1.5, None, ("a",), True])
    def test_non_str_keys_rejected(self, key):
        with pytest.raises(CanonicalJSONError, match="must be str"):
            identity.canonical_json({key: 1})

    def test_keys_colliding_after_normalization_rejected(self):
        with pytest.raises(CanonicalJSONError, match="collide"):
            identity.canonical_json({"é": 1, "é": 2})

    @pytest.mark.parametrize("value", [{1, 2}, b"bytes", Decimal("1.5"), object()])
    def test_unsupported_types_rejected(self, value):
        with pytest.raises(CanonicalJSONError, match="unsupported type"):
            identity.canonical_json([value])

    def test_error_paths_point_at_the_offender(self):
        with pytest.raises(CanonicalJSONError, match=r"\$?\.a\[1\]"):
            identity.canonical_json({"a": [0, float("nan")]})

    def test_h_is_sha256_of_canonical_json(self):
        obj = {"z": [1, "two"], "a": None}
        assert identity.H(obj) == hashlib.sha256(identity.canonical_json(obj)).hexdigest()


# --- text / bytes / tree / refs ---------------------------------------------------------------------


class TestTextAndBytes:
    def test_text_sha_normalizes_line_endings_and_nfc(self):
        assert identity.text_sha("a\r\nb") == hashlib.sha256(b"a\nb").hexdigest()
        assert identity.text_sha("é") == hashlib.sha256("é".encode()).hexdigest()

    def test_text_sha_is_not_json_quoted(self):
        assert identity.text_sha("x") == hashlib.sha256(b"x").hexdigest()
        assert identity.text_sha("x") != identity.H("x")

    def test_bytes_sha(self):
        assert identity.bytes_sha(b"") == hashlib.sha256(b"").hexdigest()
        with pytest.raises(TypeError):
            identity.bytes_sha("text")  # type: ignore[arg-type]

    def test_normalize_text_rejects_non_str(self):
        with pytest.raises(TypeError):
            identity.normalize_text(b"x")  # type: ignore[arg-type]


class TestTreeHash:
    def test_only_exec_bit_matters(self):
        a = identity.tree_hash([("f.py", 0o644, SHA)])
        assert a == identity.tree_hash([("f.py", 0o600, SHA)])
        assert a != identity.tree_hash([("f.py", 0o755, SHA)])
        assert identity.tree_hash([("f.py", 0o755, SHA)]) == identity.tree_hash([("f.py", 0o100, SHA)])

    def test_order_independent(self):
        rows = [("b.py", 0o644, SHA), ("a/c.py", 0o644, "b" * 64)]
        assert identity.tree_hash(rows) == identity.tree_hash(list(reversed(rows)))

    def test_definition(self):
        rows = [("b.py", 0o644, SHA), ("a.sh", 0o755, "b" * 64)]
        assert identity.tree_hash(rows) == identity.H([["a.sh", True, "b" * 64], ["b.py", False, SHA]])

    def test_paths_nfc_normalized(self):
        assert identity.tree_hash([("café.py", 0o644, SHA)]) == identity.tree_hash(
            [("café.py", 0o644, SHA)],
        )

    def test_empty_tree(self):
        assert identity.tree_hash([]) == identity.H([])

    @pytest.mark.parametrize("path", ["", "/abs.py", "a\\b.py", "a//b", "./a", "a/../b", "a/"])
    def test_bad_paths_rejected(self, path):
        with pytest.raises(ValueError, match="path"):
            identity.tree_hash([(path, 0o644, SHA)])

    def test_duplicates_rejected(self):
        with pytest.raises(ValueError, match="duplicate"):
            identity.tree_hash([("a", 0o644, SHA), ("a", 0o755, SHA)])

    @pytest.mark.parametrize("sha", ["", "A" * 64, "a" * 63, None])
    def test_bad_blob_sha_rejected(self, sha):
        with pytest.raises(ValueError, match="sha256"):
            identity.tree_hash([("a", 0o644, sha)])


class TestRefsAndLibraryHashes:
    def test_pinned_ref(self):
        assert identity.pinned_ref("x", 2, "0123456789abcdef" + "0" * 48) == "x@2#0123456789ab"

    @pytest.mark.parametrize(
        ("slug", "version", "content_hash"),
        [("", 1, SHA), ("a@b", 1, SHA), ("a", 0, SHA), ("a", True, SHA), ("a", 1, "zz")],
    )
    def test_pinned_ref_rejects_bad_input(self, slug, version, content_hash):
        with pytest.raises(ValueError, match=r"slug|version|sha256"):
            identity.pinned_ref(slug, version, content_hash)

    def test_library_content_hash_ignores_nothing_but_wraps_kind_and_schema(self):
        content = {"a": 1}
        assert identity.library_content_hash("stack", 1, content) == identity.H(
            {"kind": "stack", "schema": 1, "content": content},
        )
        assert identity.library_content_hash("stack", 1, content) != identity.library_content_hash("style", 1, content)
        assert identity.library_content_hash("stack", 1, content) != identity.library_content_hash("stack", 2, content)

    def test_task_structured_hash_excludes_prose(self):
        base = {"title": "T", "prose": {"minimal": "one"}}
        other = {"title": "T", "prose": {"minimal": "two", "standard": "three"}}
        assert identity.task_structured_hash(base) == identity.task_structured_hash(other)
        assert identity.task_structured_hash(base) == identity.H({"title": "T"})

    def test_task_prose_hashes(self):
        hashes = identity.task_prose_hashes({"prose": {"standard": "b\r\n", "minimal": "a"}})
        assert hashes == {"minimal": identity.text_sha("a"), "standard": identity.text_sha("b\n")}


class TestPlanLevelHashes:
    def test_condition_hash_includes_factor_keys(self):
        a = identity.condition_hash({"hole_mode": "stub", "keep_docstring": "true"})
        b = identity.condition_hash({"hole_mode": "true", "keep_docstring": "stub"})
        assert a != b
        assert a == identity.condition_hash({"keep_docstring": "true", "hole_mode": "stub"})

    def test_plan_hash_independent_of_cell_order(self):
        kwargs = {"design_hash": SHA, "conditions": [SHA], "units": ["task:x@1"], "prompt_hashes": {}}
        cells = [[0, 0, 1, "main", 5, 1], [0, 0, 0, "main", 5, 0]]
        assert identity.plan_hash(cells=cells, **kwargs) == identity.plan_hash(cells=cells[::-1], **kwargs)
        assert identity.plan_hash(cells=cells, **kwargs) != identity.plan_hash(cells=cells[:1], **kwargs)

    def test_plan_hash_rejects_bad_rows(self):
        with pytest.raises(ValueError, match="plan cell rows"):
            identity.plan_hash(design_hash=SHA, conditions=[], units=[], cells=[[0, 0]], prompt_hashes={})

    def test_target_hash_sorts_allowlist(self):
        kwargs = {"parent_tree": SHA, "address": {"kind": "file"}, "original_sha": SHA, "interface": {}}
        assert identity.target_hash(allowlist=["b", "a"], **kwargs) == identity.target_hash(
            allowlist=["a", "b"],
            **kwargs,
        )

    def test_crn_seed_range_and_pairing(self):
        seeds = {identity.crn_seed(1, f"task:t{i}@1", r) for i in range(20) for r in range(5)}
        assert all(0 <= seed < 2**31 for seed in seeds)
        assert len(seeds) == 100
        assert identity.crn_seed(7, "task:a@1", 3) == identity.crn_seed(7, "task:a@1", 3)


# --- IdentityInputs / compute_keys --------------------------------------------------------------------


def _replace(inputs: IdentityInputs, **changes) -> IdentityInputs:
    return dataclasses.replace(inputs, **changes)


class TestComputeKeys:
    def test_pure_and_deterministic(self):
        assert compute_keys(APP_INPUTS) == compute_keys(APP_INPUTS)

    def test_seed_changes_only_run_fingerprint(self):
        a, b = compute_keys(APP_INPUTS), compute_keys(_replace(APP_INPUTS, seed=APP_INPUTS.seed + 1))
        assert a.config_fingerprint == b.config_fingerprint
        assert a.run_fingerprint != b.run_fingerprint

    def test_prose_variant_changes_task_hash(self):
        task = {**APP_INPUTS.task, "variant": "minimal"}
        assert compute_keys(_replace(APP_INPUTS, task=task)).task_hash != compute_keys(APP_INPUTS).task_hash

    def test_stimulus_order_does_not_matter(self):
        reordered = _replace(SLOT_INPUTS, stimuli=list(reversed(SLOT_INPUTS.stimuli)))
        assert compute_keys(reordered).prompt_hash == compute_keys(SLOT_INPUTS).prompt_hash

    def test_policy_view_changes_policy_and_config_but_not_treatment(self):
        changed = _replace(APP_INPUTS, policy_treatment_view={**APP_INPUTS.policy_treatment_view, "x": 1})
        a, b = compute_keys(APP_INPUTS), compute_keys(changed)
        assert a.treatment_hash == b.treatment_hash
        assert a.generation_policy_hash != b.generation_policy_hash
        assert a.config_fingerprint != b.config_fingerprint

    def test_executor_kind_is_part_of_treatment(self):
        agent = _replace(
            SLOT_INPUTS,
            kind="slot_agent",
            images={**SLOT_INPUTS.images, "gateway": "sha256:" + "1" * 64},
        )
        assert compute_keys(agent).treatment_hash != compute_keys(SLOT_INPUTS).treatment_hash

    def test_reference_runs_accept_task_or_target(self):
        ref_task = _replace(APP_INPUTS, protocol="ref@1", kind="reference")
        ref_target = _replace(SLOT_INPUTS, protocol="ref@1", kind="reference")
        assert compute_keys(ref_task).task_hash != compute_keys(APP_INPUTS).task_hash
        assert compute_keys(ref_target).task_hash != compute_keys(SLOT_INPUTS).task_hash

    def test_as_dict_has_the_six_keys(self):
        assert set(compute_keys(APP_INPUTS).as_dict()) == {
            "task_hash",
            "treatment_hash",
            "prompt_hash",
            "generation_policy_hash",
            "config_fingerprint",
            "run_fingerprint",
        }


class TestIdentityInputsValidation:
    @pytest.mark.parametrize(
        "changes",
        [
            {"protocol": "app@2"},
            {"kind": "no_such_kind"},
            {"kind": "slot_agent"},  # kind/protocol mismatch
            {"task": None},
            {"target": {"target_hash": SHA}},
            {"task": {"structured_hash": SHA, "variant": "standard"}},
            {"task": {"structured_hash": "x", "variant": "standard", "prose_sha": SHA}},
            {"seed": -1},
            {"seed": True},
            {"sampling_hash": "nope"},
            {"stimuli": [("brief", SHA), ("brief", SHA)]},
            {"stimuli": [("brief", "short")]},
            {"model": {"slug": "m", "endpoint_kind": "openrouter"}},
            {"images": {"harness": None, "gateway": None, "starter_base": []}},
            {"images": {"harness": None, "gateway": "d", "starter_base": "nope"}},
        ],
    )
    def test_invalid_app_inputs_rejected(self, changes):
        with pytest.raises((ValueError, TypeError)):
            _replace(APP_INPUTS, **changes)

    def test_treatment_requires_exact_keys(self):
        missing = {k: v for k, v in APP_INPUTS.treatment.items() if k != "style"}
        with pytest.raises(ValueError, match="missing=\\['style'\\]"):
            _replace(APP_INPUTS, treatment=missing)
        with pytest.raises(ValueError, match="unexpected=\\['protocol'\\]"):
            _replace(APP_INPUTS, treatment={**APP_INPUTS.treatment, "protocol": "app@1"})

    def test_treatment_hashes_must_be_shas(self):
        with pytest.raises(ValueError, match=r"treatment\.stack"):
            _replace(APP_INPUTS, treatment={**APP_INPUTS.treatment, "stack": "flask@1"})

    def test_slot_policy_rules(self):
        with pytest.raises(ValueError, match=r"slot@1 requires treatment\.slot_policy"):
            _replace(SLOT_INPUTS, treatment={**SLOT_INPUTS.treatment, "slot_policy": None})
        with pytest.raises(ValueError, match="must not carry"):
            _replace(
                APP_INPUTS,
                treatment={**APP_INPUTS.treatment, "slot_policy": SLOT_INPUTS.treatment["slot_policy"]},
            )
        with pytest.raises(ValueError, match="slot_policy keys"):
            _replace(SLOT_INPUTS, treatment={**SLOT_INPUTS.treatment, "slot_policy": {"hole_mode": "stub"}})

    def test_gateway_digest_required_for_sidecar_kinds(self):
        with pytest.raises(ValueError, match=r"images\.gateway is required"):
            _replace(SLOT_INPUTS, kind="slot_agent")

    def test_ref_requires_exactly_one_unit(self):
        with pytest.raises(ValueError, match="exactly one"):
            _replace(APP_INPUTS, protocol="ref@1", kind="reference", target={"target_hash": SHA})


def test_prompt_hash_is_the_compute_keys_prompt_hash():
    """WP-03 ask: one prompt-hash implementation, used by ``compute_keys`` and the preview."""
    from backend.generation.prompts.engine import preview_prompt_hash

    for inputs in (APP_INPUTS, SLOT_INPUTS):
        assert identity.prompt_hash(inputs.stimuli) == compute_keys(inputs).prompt_hash
        assert identity.prompt_hash(list(reversed(list(inputs.stimuli)))) == compute_keys(inputs).prompt_hash
    texts = [("brief", "Build it."), ("system", "Be terse.")]
    assert preview_prompt_hash(texts) == identity.prompt_hash([(r, identity.text_sha(t)) for r, t in texts])
    with pytest.raises(ValueError, match="duplicate stimulus role"):
        identity.prompt_hash([("brief", "a" * 64), ("brief", "b" * 64)])
    with pytest.raises(ValueError, match="text_sha"):
        identity.prompt_hash([("brief", "not-a-sha")])
