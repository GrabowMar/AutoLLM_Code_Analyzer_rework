"""The slot-agent seed never falls back to the unholed parent tree (appendix A #16)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.generation.agentic import workspace
from backend.generation.agentic.runner import ConfigError
from backend.generation.slot.executors import restricted_agent


class _View:
    def files(self, _scope: str) -> list:
        return []


@pytest.fixture
def slot_run(monkeypatch):
    monkeypatch.setattr("backend.generation.runs.view.run_view", lambda parent: _View())
    return SimpleNamespace(pk=7, parent_run=SimpleNamespace(pk=6))


def _raise(run):
    raise NotImplementedError


@pytest.mark.parametrize("hook", [None, _raise, lambda run: {}])
def test_missing_hole_overlay_refuses_to_seed_the_original(monkeypatch, slot_run, hook) -> None:
    if hook is None:
        monkeypatch.delattr(restricted_agent, "hole_overlay")
    else:
        monkeypatch.setattr(restricted_agent, "hole_overlay", hook)
    with pytest.raises(ConfigError, match="no hole overlay"):
        workspace.slot_agent_seed(slot_run)


def test_hole_overlay_is_applied(monkeypatch, slot_run) -> None:
    monkeypatch.setattr(restricted_agent, "hole_overlay", lambda run: {"app/x.py": b"# hole\n"})
    seed = workspace.slot_agent_seed(slot_run)
    assert seed["app/x.py"].data == b"# hole\n"
