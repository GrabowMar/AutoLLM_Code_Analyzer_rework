"""Fixtures for the slot-mode tests: the golden parent (frozen), fakes for acceptance, eval and the LLM."""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from unittest import mock

import pytest

from backend.generation.slot.freeze import freeze
from backend.generation.testing.fake_eval import FakeEvaluator
from backend.generation.testing.fake_slot import FakeAcceptance
from backend.generation.testing.fake_slot import SlotEvalScript
from backend.generation.testing.golden_library import InstalledGoldenLibrary
from backend.generation.testing.golden_library import install_golden_library
from backend.generation.tests.wp12_slot.helpers import make_parent
from backend.users.tests.factories import UserFactory

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun
    from backend.generation.models import SlotSet


@pytest.fixture(autouse=True)
def _quiet() -> Any:
    """No Redis traffic: execution doorbells and SSE publishes are no-ops."""
    with mock.patch("backend.execution.api.wake"), mock.patch("backend.realtime.events.publish"):
        yield


@pytest.fixture
def acceptance(monkeypatch: pytest.MonkeyPatch) -> FakeAcceptance:
    return FakeAcceptance().install(monkeypatch)


@pytest.fixture
def researcher(db: Any) -> Any:
    return UserFactory.create()


@pytest.fixture
def golden(db: Any, researcher: Any) -> InstalledGoldenLibrary:
    return install_golden_library(created_by=researcher)


@pytest.fixture
def parent(golden: InstalledGoldenLibrary, researcher: Any) -> GenerationRun:
    return make_parent(golden, researcher)


@pytest.fixture
def slot_set(parent: GenerationRun, researcher: Any, acceptance: FakeAcceptance) -> SlotSet:
    return freeze(parent, name="golden parent", user=researcher)


@pytest.fixture
def script() -> SlotEvalScript:
    return SlotEvalScript()


@pytest.fixture
def evaluator(monkeypatch: pytest.MonkeyPatch, script: SlotEvalScript) -> FakeEvaluator:
    return FakeEvaluator(results=script).install(monkeypatch)
