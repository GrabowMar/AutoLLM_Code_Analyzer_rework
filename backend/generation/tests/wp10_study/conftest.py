"""Fixtures of the study engine tests (WP-10): the golden library, two models, fake seams, design builder."""

from __future__ import annotations

from typing import Any

import pytest

from backend.generation.testing.fake_study import FakeStudyEnv
from backend.generation.testing.fake_study import use_fake_study_deps
from backend.generation.testing.golden_library import install_golden_library
from backend.llm_models.tests.factories import LLMModelFactory

MODELS = ("deepseek/deepseek-v4.1-flash", "qwen/qwen3-coder")


@pytest.fixture
def fake_study():
    """The fake seams (render, policy, estimate, environment, manifest, gates, enqueue)."""
    env = FakeStudyEnv()
    with use_fake_study_deps(env):
        yield env


@pytest.fixture
def golden(db, user):
    """The golden library installed as system rows, plus two remote models with tool calling."""
    library = install_golden_library(created_by=None)
    from backend.generation.models import StackProfile
    from backend.generation.models import TaskSpecVersion

    for model_id in MODELS:
        LLMModelFactory.create(model_id=model_id, metadata={"openrouter_supported_parameters": ["seed", "tools"]})
    # Passing oracles for every (task, stack): confirmatory plans require them (oracle_missing is an error).
    oracle = {stack.ref: {"status": "pass"} for stack in StackProfile.objects.all()}
    TaskSpecVersion.objects.update(oracle=oracle)
    return library


def app_design(**overrides: Any) -> dict[str, Any]:
    """The WP-10 "done when" design: 2 stacks x 2 styles x 2 models x 2 tasks x 2 repeats (golden library)."""
    design: dict[str, Any] = {
        "mode": "app",
        "kind": "exploratory",
        "factors": [
            {"key": "model", "role": "crossed", "levels": list(MODELS)},
            {"key": "stack_profile", "role": "crossed", "levels": ["flask-react@1", "fastapi-vue@1"]},
            {"key": "style", "role": "crossed", "levels": ["model-choice@1", "tailwind@1"]},
        ],
        "units": {"tasks": ["crud_todo_list@1", "notes_markdown_render@1"]},
        "repeats": 2,
        "base_seed": 12345,
        "order_seed": 7,
        "resource_policy": "frugal@1",
        "evaluation": {"eval_suite": "eval-v2@1"},
    }
    design.update(overrides)
    return design


@pytest.fixture
def make_study(golden, user):
    from backend.generation.study.launch import create_study

    def build(**overrides: Any):
        return create_study(user=user, name=overrides.pop("name", "Study"), design=app_design(**overrides))

    return build
