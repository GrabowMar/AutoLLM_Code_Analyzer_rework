# Hash-algebra golden test (spec section 13.3, layer 1).
#
# CHANGING THESE REQUIRES A PROTOCOL BUMP + CHANGELOG LINE IN docs/STUDIES.md#identity-changelog.
#
# Fixed literal IdentityInputs (no library, no renderer, no database) go through
# identity.compute_keys and every other identity helper; the exact hex values
# below were computed once and frozen. A failure here means an identity
# algorithm changed: either revert it, or bump the protocol (app@2 / slot@2)
# and record the change in the identity changelog before updating the values.
from __future__ import annotations

from backend.generation import identity
from backend.generation.identity import IdentityInputs
from backend.generation.identity import compute_keys

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64
SHA_0 = "0" * 64
SHA_1 = "1" * 64
SHA_2 = "2" * 64
SHA_3 = "3" * 64

GATEWAY_DIGEST = "sha256:" + "9" * 64
HARNESS_DIGEST = "sha256:" + "8" * 64
STARTER_BASE = ["python@sha256:" + "7" * 64, "node@sha256:" + "6" * 64]

POLICY_VIEW = {
    "run.max_cost_usd": 0.3,
    "run.max_llm_requests": 40,
    "call.max_output_tokens": 8192,
    "reasoning": {"mode": "effort", "effort": "low", "max_tokens": None},
    "routing.only": [],
}

APP_INPUTS = IdentityInputs(
    protocol="app@1",
    kind="app_agentic",
    task={"structured_hash": SHA_A, "variant": "standard", "prose_sha": SHA_B},
    target=None,
    treatment={
        "stack": SHA_C,
        "starter": SHA_D,
        "style": SHA_E,
        "auth_mode": "contract_bearer",
        "guidance": SHA_F,
        "prompt_set": SHA_0,
        "harness_preset": SHA_1,
        "gateway": {"front_protocol": "openai_chat", "protocol_mode": "native"},
        "allowed_bases_sha": None,
        "slot_policy": None,
    },
    stimuli=[("brief", SHA_2)],
    model={"slug": "deepseek/deepseek-v4.1-flash", "endpoint_kind": "openrouter", "pinned_digest": None},
    sampling_hash=SHA_3,
    policy_treatment_view=POLICY_VIEW,
    images={"harness": HARNESS_DIGEST, "gateway": GATEWAY_DIGEST, "starter_base": STARTER_BASE},
    seed=1234567,
)

SLOT_INPUTS = IdentityInputs(
    protocol="slot@1",
    kind="slot_single_shot",
    task=None,
    target={"target_hash": SHA_A},
    treatment={
        "stack": None,
        "starter": None,
        "style": None,
        "auth_mode": None,
        "guidance": SHA_F,
        "prompt_set": SHA_0,
        "harness_preset": None,
        "gateway": None,
        "allowed_bases_sha": None,
        "slot_policy": {
            "hole_mode": "stub",
            "keep_docstring": False,
            "context_policy_hash": SHA_B,
            "resolved_context_sha": SHA_C,
            "include_tests": False,
            "edit_policy": "revert",
        },
    },
    stimuli=[("slot_single_shot_user", SHA_2), ("slot_single_shot_system", SHA_1)],
    model={"slug": "qwen/qwen3-coder", "endpoint_kind": "openrouter", "pinned_digest": None},
    sampling_hash=SHA_3,
    policy_treatment_view=POLICY_VIEW,
    images={"harness": None, "gateway": None, "starter_base": []},
    seed=42,
)

EXPECTED_APP = {
    "task_hash": "a40df81f3be8c581b483fc2996d1823bf64f579e27dda0dd5c11f4d3c6cd3dc9",
    "treatment_hash": "009d477c95723cacafb37138edba15ee2523054c00606c1205e42ef26d95c439",
    "prompt_hash": "1567b2c60bfec71def109d650ac99913a5264d1cc8fe33859257af470f653f67",
    "generation_policy_hash": "d19d04b86a9cd90d5b3e9d926f6a4ee5cbfe66773ed2acee7ae63ca32749a5b2",
    "config_fingerprint": "ff36b14b2aeeea1abb26ea7cada280b3bd394884b78b4d6190dcb45ff946bfca",
    "run_fingerprint": "d8bd54e65d2060d67c6b553c6f1e3b40777bb9a394ac572ab42b59138116bbe0",
}

EXPECTED_SLOT = {
    "task_hash": "88b1a94d688bed3158b925a4f40f082535ced8c1a556779033b11aa46b4d7270",
    "treatment_hash": "b1e17337c551294b1487ddda8e86c1fc84f5c1c06a37549fcf831120fbee7281",
    "prompt_hash": "25b7915375f69c62aa0650358b4d6888bad3551c9b6ac4bd20121b58930fbded",
    "generation_policy_hash": "d19d04b86a9cd90d5b3e9d926f6a4ee5cbfe66773ed2acee7ae63ca32749a5b2",
    "config_fingerprint": "17a39f8cb7567ad02e128e749a07caa9ed345adcb63792014605dac883e589dc",
    "run_fingerprint": "b4884184ab64d0eee3bdf1d9758941a6ecd043eacc2bc29c72647e97c5d18dda",
}


def test_app_keys_golden():
    assert compute_keys(APP_INPUTS).as_dict() == EXPECTED_APP


def test_slot_keys_golden():
    assert compute_keys(SLOT_INPUTS).as_dict() == EXPECTED_SLOT


def test_policy_hash_is_shared_across_modes():
    # Same treatment view -> same generation_policy_hash, whatever the protocol.
    assert EXPECTED_APP["generation_policy_hash"] == EXPECTED_SLOT["generation_policy_hash"]


def test_canonical_json_golden_vectors():
    assert identity.canonical_json({"b": 1, "a": [1, 2.5, None, True], "B": "x"}) == (
        b'{"B":"x","a":[1,2.5,null,true],"b":1}'
    )
    assert identity.H({}) == "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    assert identity.H({"k": "é\r\n"}) == "6cf13b25d56e51ac3d993b33708e8d5c66dd0cd6f8c945b7861662b6c2e29bd7"


def test_text_tree_and_ref_golden():
    assert identity.text_sha("hello\r\nworld") == "26c60a61d01db5836ca70fefd44a6a016620413c8ef5f259a6c5612d4f79d3b8"
    assert (
        identity.tree_hash(
            [
                ("src/app.py", 0o644, SHA_A),
                ("Dockerfile", 0o644, SHA_B),
                ("bin/run.sh", 0o755, SHA_C),
            ],
        )
        == "b12ad1ad488525d37ef06268545617cdfd29a32b1d2e9608c5b3443b21f58b6b"
    )
    assert identity.pinned_ref("flask-react", 3, SHA_D) == "flask-react@3#dddddddddddd"


def test_plan_level_hashes_golden():
    assert (
        identity.library_content_hash("stack", 1, {"choice_mode": "fixed"})
        == "3826602feb836df70d662a57ebf04b6721a12626c740e1fa63e1b5c6e174f7db"
    )
    assert (
        identity.task_structured_hash({"title": "Todo", "prose": {"minimal": "x"}})
        == "0d989a9edc8310a30acaf8edf5babed10900f8f638563d6f461163051c6c9d2f"
    )
    assert (
        identity.condition_hash({"model": "m@1#aaaaaaaaaaaa", "prose_variant": "minimal"})
        == "b476c7b2b59215713dbe422519b17780f549f42439ae93c066a4d45ecf3cf5c4"
    )
    assert (
        identity.design_hash({"mode": "app", "repeats": 2})
        == "539456702b473d0fbd5baf6a8cf830f77b94b10e34a87d4394050d2b884cb921"
    )
    plan = identity.plan_hash(
        design_hash=SHA_A,
        conditions=[SHA_B, SHA_C],
        units=["task:crud_todo_list@1"],
        cells=[[1, 0, 0, "main", 7, 1], [0, 0, 0, "main", 7, 0]],
        prompt_hashes={"c0-u0-r0": SHA_D, "c1-u0-r0": SHA_E},
    )
    assert plan == "55fb919b063e959ddbc8a08c0290072c4bf61a4740529c1923e29673b1ff2453"
    assert (
        identity.prereg_hash(
            plan_hash=plan,
            analysis_plan={"itt": True},
            stop_rules=[],
            pilot_gates={"max_oom_rate": 0.05},
            evaluation={"eval_suite": "eval-v2@1#aaaaaaaaaaaa", "eval_level": "security"},
        )
        == "4ee7784e937d832a7cf2204c1804f45e1aa7eee57becaccb0d14bf606fa0ee9d"
    )
    assert (
        identity.target_hash(
            parent_tree=SHA_A,
            address={"kind": "symbol", "path": "app.py", "span": None, "content_sha": SHA_B},
            original_sha=SHA_C,
            allowlist=["b.py", "app.py"],
            interface={"signature": "def f():"},
        )
        == "c8ad66b65e23443668ddd964c07239c22bbf940f5359da3ce6583644ae4fe3e5"
    )
    assert identity.crn_seed(12345, "task:crud_todo_list@1", 0) == 1712254510
