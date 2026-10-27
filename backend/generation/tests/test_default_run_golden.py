"""Golden identity values of a default agent run (fairness invariant 2).

The values below were captured from HEAD f5b9071, before run presets existed.
A default run -- no presets, or the system default presets passed explicitly
-- must keep producing exactly these ``prompt_hash`` / ``run_fingerprint``
values and byte-identical ``agent`` / ``gateway`` / ``harness`` sections and
brief, so default runs keep pooling with every run made before presets.

A failure here means default runs changed identity. Only recapture on purpose
(a deliberate BRIEF/CONTRACT/SANDBOX version bump or a harness release bump),
never to make an unrelated change pass.

Recaptured once, deliberately, for the generation-library modernization:
BRIEF_VERSION 4 (current stack runtimes in the summaries, JSON blocks no
longer HTML-escaped), the agent-base move to Node 24 / Python 3.13, the
modernized flask-react skeleton (Vite base ``/``, flask-sock, JSON 404s) and
the spec ``content_hash`` in the run fingerprint. The agent, gateway and harness
sections were unchanged by it; runs from before it form separate conditions.
"""

from __future__ import annotations

import hashlib
import json
from unittest import mock

import pytest

from backend.generation.models import GenerationJob
from backend.generation.services.agent.snapshot import build_agent_snapshot
from backend.generation.services.profile_resolver import derive_experiment_seed
from backend.generation.tests.factories import AppRequirementTemplateFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.runtime.seeding import seed_stacks
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

BASE_SEED = 20261022
APP_SLUG = "golden-notes"
HARNESS_NAMES = ("opencode", "mini-swe-agent", "aider")
API_ENDPOINTS = [
    {"method": "GET", "path": "/api/notes", "description": "List notes", "request": None, "response": {"items": []}},
    {
        "method": "POST",
        "path": "/api/notes",
        "description": "Create a note",
        "request": {"title": "string", "body": "string"},
        "response": {"id": 1},
    },
]
ADMIN_API_ENDPOINTS = [
    {
        "method": "GET",
        "path": "/api/admin/notes",
        "description": "All notes",
        "request": None,
        "response": {"items": []},
    },
]

_PROMPT_HASH = "c79c0562d20d7f16"
_BRIEF_SHA256 = "7c0f44e07eb487c5a478df1efbdddb0b0c723103188e9785afe1d45fb441333a"
_GATEWAY = "fe9f848f79c51ae6baea1ad9ecacbc1d435b035e1d6119162c07276ff5cb1872"
GOLDEN: dict[str, dict[str, str]] = {
    "opencode": {
        "prompt_hash": _PROMPT_HASH,
        "run_fingerprint": "30a9c7c0aa736270",
        "brief_sha256": _BRIEF_SHA256,
        "agent": "cd524fc38b85a1ce8569d68d93a8243bde793b685ad3289a6870c742640fcff3",
        "gateway": _GATEWAY,
        "harness": "e1d524554c9f67687944a09ecaca87748f3c4d81161b429818f4d1eabfda5e7e",
    },
    "mini-swe-agent": {
        "prompt_hash": _PROMPT_HASH,
        "run_fingerprint": "20073c1dd853953e",
        "brief_sha256": _BRIEF_SHA256,
        "agent": "4a949a3b01eec4a8097c5ec28b8d024bbbb3e31eb76c02e646b211d97ec656b1",
        "gateway": _GATEWAY,
        "harness": "8e629fd2a3784d27076f470811db13ff60c6fc212894259898c1b104d5b0feb9",
    },
    "aider": {
        "prompt_hash": _PROMPT_HASH,
        "run_fingerprint": "24efee6d0b548cfe",
        "brief_sha256": _BRIEF_SHA256,
        "agent": "fd5eb9b752f3a3cbe71262bbe6a918f5f0b9d73ccf35769102dcafa8a214e82e",
        "gateway": _GATEWAY,
        "harness": "6ab068c4c31b2ac92b71e8d311117a69cf93bcbe7aee7d9787c28f00b2f1152a",
    },
}


def _digest(section) -> str:
    canonical = json.dumps(section, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _identity(snapshot: dict) -> dict[str, str]:
    return {
        "prompt_hash": snapshot["prompt_hash"],
        "run_fingerprint": snapshot["run_fingerprint"],
        "brief_sha256": snapshot["brief"]["sha256"],
        "agent": _digest(snapshot["agent"]),
        "gateway": _digest(snapshot["gateway"]),
        "harness": _digest(snapshot["harness"]),
    }


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.AGENT_AUTO_EVALUATE = False


@pytest.fixture
def fixture_rows():
    # Under --reuse-db the Stack rows date from DB creation; re-seed so the
    # pinned stack content hash always reflects the repository's skeletons.
    seed_stacks()
    model = LLMModelFactory(
        model_id="golden-vendor/golden-coder",
        canonical_slug="golden-vendor_golden-coder-20260101",
        provider="Golden Vendor",
        model_name="Golden Coder",
        description="",
        context_window=262_144,
        max_output_tokens=65_536,
        input_price_per_token=0.000001,
        output_price_per_token=0.000004,
        supports_function_calling=True,
        metadata={
            "openrouter_supported_parameters": [
                "max_tokens",
                "temperature",
                "top_p",
                "seed",
                "tools",
                "tool_choice",
                "reasoning",
                "include_reasoning",
            ],
        },
    )
    app = AppRequirementTemplateFactory(
        slug=APP_SLUG,
        is_default=True,
        name="Golden Notes",
        category="Productivity",
        description="",
        backend_requirements=[
            "Notes have a title and a body",
            "Access control: anyone may read notes; creating, editing and deleting require auth",
        ],
        frontend_requirements=["List notes and show one note"],
        admin_requirements=[],
        api_endpoints=API_ENDPOINTS,
        data_model={"name": "Note", "fields": {"id": "int", "title": "str", "body": "str"}},
        admin_api_endpoints=ADMIN_API_ENDPOINTS,
    )
    return model, app


def _seed() -> int:
    return derive_experiment_seed(BASE_SEED, APP_SLUG, 0)


def _post_agent(client, payload: dict) -> GenerationJob:
    user = UserFactory()
    client.force_login(user)
    with (
        mock.patch("backend.generation.api.views.custom.has_resolvable_key", return_value=True),
        mock.patch("backend.generation.api.views.custom.dispatch_job"),
    ):
        res = client.post("/api/generation/jobs/agent/", data=json.dumps(payload), content_type="application/json")
    assert res.status_code == 200, res.content
    jobs = list(GenerationJob.objects.filter(created_by=user))
    assert len(jobs) == 1
    assert jobs[0].status != GenerationJob.Status.FAILED, jobs[0].error_message
    return jobs[0]


@pytest.mark.parametrize("harness", HARNESS_NAMES)
def test_builder_default_snapshot_matches_golden(fixture_rows, harness):
    model, app = fixture_rows
    snapshot = build_agent_snapshot(
        app_requirement=app,
        description="",
        profile=None,
        stack_slug="flask-react",
        model=model,
        max_tokens=32000,
        experiment_seed=_seed(),
        llm_overrides={},
        agent_overrides={"harness": harness},
    )
    assert _identity(snapshot) == GOLDEN[harness]


@pytest.mark.parametrize("harness", HARNESS_NAMES)
def test_api_default_batch_matches_golden(client, fixture_rows, harness):
    model, app = fixture_rows
    job = _post_agent(
        client,
        {
            "stack_slug": "flask-react",
            "app_requirement_ids": [app.id],
            "model_ids": [model.id],
            "harnesses": [harness],
            "base_seed": BASE_SEED,
        },
    )
    assert job.experiment_seed == _seed()
    assert _identity(job.resolved_bundle) == GOLDEN[harness]


def test_api_bare_request_runs_the_default_harness_golden(client, fixture_rows):
    model, app = fixture_rows
    job = _post_agent(client, {"app_requirement_ids": [app.id], "model_ids": [model.id], "base_seed": BASE_SEED})
    assert job.resolved_bundle["harness"]["name"] == "opencode"
    assert _identity(job.resolved_bundle) == GOLDEN["opencode"]


# ── the same defaults reached through run presets ─────────────────────


@pytest.mark.parametrize("harness", HARNESS_NAMES)
def test_api_explicit_system_default_presets_match_golden(client, fixture_rows, harness):
    from backend.generation.services.presets import system_default_preset

    model, app = fixture_rows
    harness_preset = system_default_preset("harness", harness)
    sampling_preset = system_default_preset("sampling")
    budget_preset = system_default_preset("budget")
    job = _post_agent(
        client,
        {
            "app_requirement_ids": [app.id],
            "model_ids": [model.id],
            "harness_preset_ids": [harness_preset.id],
            "sampling_preset_id": sampling_preset.id,
            "budget_preset_id": budget_preset.id,
            "base_seed": BASE_SEED,
        },
    )
    snapshot = job.resolved_bundle
    assert _identity(snapshot) == GOLDEN[harness]
    # Provenance is recorded, but outside every identity key.
    assert snapshot["presets"]["harness"]["slug"] == f"{harness}-default"
    assert snapshot["presets"]["sampling"]["slug"] == "provider-default"
    assert snapshot["presets"]["budget"]["slug"] == "platform-default"
    assert job.harness_preset_id == harness_preset.id
    assert job.harness_options == {}
    assert job.bundle_key == f"agent:{harness}@{snapshot['harness']['version']}"


@pytest.mark.parametrize("harness", HARNESS_NAMES)
def test_builder_with_default_options_and_provenance_matches_golden(fixture_rows, harness):
    from backend.generation.services.agent.harnesses import get_harness

    model, app = fixture_rows
    snapshot = build_agent_snapshot(
        app_requirement=app,
        description="",
        profile=None,
        stack_slug=None,
        model=model,
        max_tokens=None,
        experiment_seed=_seed(),
        llm_overrides={},
        agent_overrides={"harness": harness},
        # Spelled-out defaults normalize away: same section, same keys.
        harness_options=get_harness(harness).default_options(),
        presets={"harness": {"id": 1, "slug": f"{harness}-default", "version": 1, "content_hash": "x", "name": "n"}},
    )
    assert _identity(snapshot) == GOLDEN[harness]
    assert snapshot["harness_options"] == {}
