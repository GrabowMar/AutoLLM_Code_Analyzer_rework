"""``manage.py go_reasoning_effort``: flags OpenCode Go rows so the policy keeps ``reasoning.mode=effort`` (C2)."""

from __future__ import annotations

import io
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from backend.generation.budget.model_facts import model_facts
from backend.inference.tests.conftest import make_endpoint
from backend.inference.tests.conftest import make_local_model
from backend.llm_models.management.commands import go_reasoning_effort as command
from backend.llm_models.models import LLMModel
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def _record(efforts: list[str] | None) -> dict:
    options = [{"type": "effort", "values": efforts}] if efforts else [{"type": "toggle"}]
    return {"catalog_twin": {"source": "models.dev", "provider_record": {"reasoning_options": options}}}


@pytest.fixture
def go_rows():
    endpoint = make_endpoint(
        UserFactory(),
        slug="opencode-go",
        kind="openai_compatible",
        api_flavor="openai",
        connection="direct",
        base_url="https://opencode.ai/zen/go",
    )
    rows = {}
    for model_id, efforts in (
        ("local-opencode-go/deepseek-v4-pro", ["high", "max"]),
        ("local-opencode-go/minimax-m3", None),
    ):
        name = model_id.rsplit("/", 1)[1]
        rows[model_id] = make_local_model(endpoint, name=name, model_id=model_id, metadata=_record(efforts))
    return rows


def _run(*args: str) -> str:
    out = io.StringIO()
    call_command("go_reasoning_effort", *args, stdout=out)
    return out.getvalue()


def test_set_is_idempotent_reports_listed_efforts_and_unset_restores(go_rows):
    ids = list(go_rows)
    args = [arg for model_id in ids for arg in ("--model", model_id)]
    before = dict(LLMModel.objects.values_list("pk", "updated_at"))

    dry = _run(*args, "--dry-run")
    assert "would update" in dry
    assert not any(LLMModel.objects.get(model_id=m).metadata.get("supports_reasoning") for m in ids)

    out = _run(*args)
    assert "changed 2 of 2" in out
    assert "provider lists high/max" in out
    assert "minimax-m3" in out
    assert "WARNING: 'high' is not a listed effort" in out
    for model_id in ids:
        row = LLMModel.objects.get(model_id=model_id)
        assert row.metadata["supports_reasoning"] is True
        assert row.metadata["reasoning_control"]["value"] == command.SOURCE_VALUE
        assert row.metadata["catalog_twin"]  # the rest of the metadata is kept
        assert row.metadata["reasoning_modes"] == ["effort"]
        assert model_facts(row).supports_reasoning is True
        assert model_facts(row).extra["reasoning_modes"] == ["effort"]
    # updated_at (the manifests' pricing as_of) is untouched.
    assert dict(LLMModel.objects.values_list("pk", "updated_at")) == before
    assert "changed 0 of 2" in _run(*args)

    assert "changed 2 of 2" in _run(*args, "--unset")
    for model_id in ids:
        meta = LLMModel.objects.get(model_id=model_id).metadata
        assert "supports_reasoning" not in meta
        assert "reasoning_modes" not in meta
        assert "reasoning_control" not in meta


def test_the_default_list_is_the_final_studys_go_models_and_unknown_rows_fail(go_rows):
    assert len(command.FINAL_STUDY_MODELS) == 8
    assert all(m.startswith("local-opencode-go/") for m in command.FINAL_STUDY_MODELS)
    with pytest.raises(CommandError, match="not OpenCode Go"):
        _run()  # most of the default rows do not exist in this database
    with pytest.raises(CommandError, match="local-ollama/x"):
        _run("--model", "local-ollama/x")


def test_probe_reports_a_rejection(go_rows):
    from backend.inference.access import LLMAccessError

    model_id = "local-opencode-go/deepseek-v4-pro"
    error = LLMAccessError("upstream_error", "opencode-go answered 400: Unsupported parameter: 'reasoning_effort'")
    with mock.patch("backend.inference.chat.chat", side_effect=error) as chat:
        out = _run("--model", model_id, "--probe")
    sent = chat.call_args.kwargs
    assert sent["params"]["reasoning"] == {"effort": "high"}
    assert sent["source"] == "probe"
    assert "REJECTED/FAILED" in out
    assert "reasoning_effort" in out


@pytest.fixture
def all_go_rows(go_rows):
    endpoint = next(iter(go_rows.values())).endpoint
    listed = {"local-opencode-go/deepseek-v4.1-flash": ["low", "high", "max"], "local-opencode-go/hy4": ["high"]}
    for model_id in command.FINAL_STUDY_MODELS:
        if model_id not in go_rows:
            name = model_id.rsplit("/", 1)[1]
            go_rows[model_id] = make_local_model(
                endpoint,
                name=name,
                model_id=model_id,
                metadata=_record(listed.get(model_id)),
            )
    return go_rows


def _flagged() -> set[str]:
    return {
        row.model_id
        for row in LLMModel.objects.filter(model_id__in=command.FINAL_STUDY_MODELS)
        if (row.metadata or {}).get("supports_reasoning") is True
    }


def test_by_default_only_rows_that_list_the_effort_are_flagged(all_go_rows):
    out = _run()
    assert "changed 3 of 8" in out
    assert _flagged() == {
        "local-opencode-go/deepseek-v4-pro",
        "local-opencode-go/deepseek-v4.1-flash",
        "local-opencode-go/hy4",
    }
    assert out.count("skipped") == 5
    assert "pass --force" in out
    # --force: every final-study Go row (with a warning on those that list no such effort).
    out = _run("--force")
    assert "changed 5 of 8" in out
    assert _flagged() == set(command.FINAL_STUDY_MODELS)
    assert out.count("WARNING") == 5
    # --unset clears them all.
    assert "changed 8 of 8" in _run("--unset")
    assert _flagged() == set()


def test_dry_run_never_probes(go_rows):
    with mock.patch("backend.inference.chat.chat") as chat, pytest.raises(CommandError, match="--dry-run"):
        _run("--model", "local-opencode-go/deepseek-v4-pro", "--dry-run", "--probe")
    chat.assert_not_called()


def _answer(reasoning_tokens: int | None):
    details = {} if reasoning_tokens is None else {"reasoning_tokens": reasoning_tokens}
    usage = {"completion_tokens": 40, "completion_tokens_details": details}
    return mock.Mock(response={"usage": usage})


@pytest.mark.parametrize(
    ("tokens", "verdict"),
    [
        ((120, 900), "effect visible"),
        ((300, 300), "no visible effect: the value may be ignored"),
        ((None, None), "reasoning tokens not reported"),
    ],
)
def test_probe_compares_reasoning_tokens_with_and_without_the_effort(go_rows, tokens, verdict):
    model_id = "local-opencode-go/deepseek-v4-pro"
    with mock.patch("backend.inference.chat.chat", side_effect=[_answer(t) for t in tokens]) as chat:
        out = _run("--model", model_id, "--probe")
    default_call, effort_call = (c.kwargs for c in chat.call_args_list)
    assert "reasoning" not in default_call["params"]
    assert effort_call["params"]["reasoning"] == {"effort": "high"}
    assert verdict in out
    assert f"default={tokens[0]}, high={tokens[1]}" in out


def test_a_flagged_go_row_keeps_only_the_effort_mode_in_the_plan(go_rows):
    from backend.generation.budget import policy
    from backend.generation.contracts.resources import preset_content

    model_id = "local-opencode-go/deepseek-v4-pro"
    row = go_rows[model_id]
    effort = {"reasoning": {"mode": "effort", "effort": "high", "max_tokens": None, "exclude": True}}
    budget = {"reasoning": {"mode": "max_tokens", "effort": None, "max_tokens": 3000, "exclude": False}}
    # Unflagged: provider default, as before.
    plan = policy.effective_policy(policy.apply_overrides(preset_content("standard"), effort), model=row)
    assert plan.content.reasoning.mode == "provider_default"
    assert "reasoning_unsupported" in plan.notes

    _run("--model", model_id)
    row = LLMModel.objects.select_related("endpoint").get(pk=row.pk)
    plan = policy.effective_policy(policy.apply_overrides(preset_content("standard"), effort), model=row)
    assert (plan.content.reasoning.mode, plan.content.reasoning.effort) == ("effort", "high")
    assert "reasoning_unsupported" not in plan.notes
    # Go cannot exclude the reasoning output: not recorded as if it could.
    assert plan.content.reasoning.exclude is False
    assert "reasoning_exclude_unsupported" in plan.notes
    # A reasoning token budget would be recorded but never sent to Go: provider default instead.
    plan = policy.effective_policy(policy.apply_overrides(preset_content("standard"), budget), model=row)
    assert plan.content.reasoning.mode == "provider_default"
    assert plan.content.reasoning.max_tokens is None
    assert "reasoning_unsupported" in plan.notes
