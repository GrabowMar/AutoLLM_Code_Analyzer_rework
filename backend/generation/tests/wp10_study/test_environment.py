"""The default cell environment against the real harness registry and image pinning (no docker needed)."""

from __future__ import annotations

import pytest

from backend.generation.contracts.resources import EffectivePolicy
from backend.generation.contracts.resources import ResourcePolicyContent
from backend.generation.models import HarnessPreset
from backend.generation.models import ResourcePolicy
from backend.generation.models import StackProfile
from backend.generation.study.environment import EnvironmentRequest
from backend.generation.study.environment import cell_environment
from backend.generation.study.environment import memory_bytes
from backend.generation.study.environment import study_sandbox
from backend.llm_models.models import LLMModel

from .conftest import MODELS

pytestmark = pytest.mark.django_db


class ImageNotFound(Exception):  # noqa: N818 - the docker SDK's name, matched by agentic.images
    pass


class _NoDocker:
    class images:  # noqa: N801 - mimics docker's client.images
        @staticmethod
        def get(_tag):
            raise ImageNotFound


def _policy() -> EffectivePolicy:
    content = ResourcePolicyContent.model_validate(ResourcePolicy.objects.get(slug="frugal").content)
    return EffectivePolicy(content=content, budget_basis="usd", cap_basis="billed", serving_tier="standard")


def test_opencode_environment_pins_images_and_hashes_the_render(golden, monkeypatch):
    from backend.runtime.services import docker_manager

    monkeypatch.setattr(docker_manager, "new_client", _NoDocker)
    preset = HarnessPreset.objects.get(slug="opencode-native")
    model = LLMModel.objects.get(model_id=MODELS[0])
    request = EnvironmentRequest(
        kind="app_agentic",
        model=model,
        policy=_policy(),
        harness_preset=preset,
        sandbox=study_sandbox("2g", 1_000_000_000, list(StackProfile.objects.all())),
        brief="Build a todo app.\n",
    )
    env = cell_environment(request)
    assert env.harness is not None, env.issues
    assert env.harness.name == "opencode"
    assert env.gateway_treatment == {"front_protocol": "openai_chat", "protocol_mode": "native"}
    assert env.images["harness"].startswith("pending:")
    assert "image_built_after_plan" in {issue.code for issue in env.issues}
    assert env.excluded is None
    again = cell_environment(request)
    assert again.harness.command_sha == env.harness.command_sha


def test_plan_time_render_gets_the_model_context_window(golden):
    """WP-07/WP-01 ask: the plan-time render passes the same context window the run-time render gets."""
    from backend.generation.study.environment import _render_hashes

    seen = []

    class _Rendered:
        command = "agent run"
        files = {"config.json": b"{}"}

    class _Adapter:
        def render(self, ctx):
            seen.append(ctx)
            return _Rendered()

    model = LLMModel.objects.get(model_id=MODELS[0])
    model.context_window = 131_072
    request = EnvironmentRequest(kind="app_agentic", model=model, policy=_policy(), brief="b")
    _render_hashes(_Adapter(), request, None, None)
    assert seen[0].model_context_window == 131_072
    model.context_window = 0  # unknown -> None, never 0
    _render_hashes(_Adapter(), request, None, None)
    assert seen[1].model_context_window is None


def test_single_shot_cells_have_no_sidecar(golden):
    model = LLMModel.objects.get(model_id=MODELS[0])
    env = cell_environment(EnvironmentRequest(kind="slot_single_shot", model=model, policy=_policy()))
    assert (env.harness, env.gateway, env.sandbox, env.images["gateway"]) == (None, None, None, None)


def test_study_sandbox_takes_the_stack_minimum_and_the_machine_cap(golden, settings):
    settings.AGENT_SANDBOX_MEM_LIMIT_MAX = "3g"
    sandbox = study_sandbox("1g", 1_000_000_000, list(StackProfile.objects.all()))
    assert memory_bytes(sandbox.memory) >= memory_bytes("1g")
    assert memory_bytes(study_sandbox("8g", 1, []).memory) == memory_bytes("3g")
