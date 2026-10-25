"""AI-review calls on local models (spec §D.2 call sites: ``analysis/services/ai_runner.py``)."""

from __future__ import annotations

import threading
from unittest import mock

import pytest
from django.db import connection
from django.db import transaction

from backend.analysis.services import ai_runner
from backend.inference.access import LLMAccessError
from backend.inference.access import resolve_access
from backend.inference.chat import ChatResult
from backend.inference.tests.conftest import make_endpoint
from backend.inference.tests.conftest import make_local_model

pytestmark = pytest.mark.django_db(transaction=True)
MESSAGES = [{"role": "user", "content": "review"}]


def _local_access(owner):
    return resolve_access(owner, make_local_model(make_endpoint(owner)))


def test_outside_a_transaction_the_call_runs_inline(owner):
    access = _local_access(owner)
    seen = {}

    def fake_chat(*_args, **kwargs):
        seen["thread"] = threading.get_ident()
        seen["source"] = kwargs["source"]
        return ChatResult(response={"choices": []})

    with mock.patch("backend.inference.chat.chat", side_effect=fake_chat):
        ai_runner._chat(access, MESSAGES, 100)
    assert seen == {"thread": threading.get_ident(), "source": "analysis"}


def test_inside_a_request_transaction_the_call_runs_on_its_own_connection(owner):
    access = _local_access(owner)
    seen = {}

    def fake_chat(*_args, **kwargs):
        # The queued request must be committed for a relay or pump to claim it.
        seen["atomic"] = connection.in_atomic_block
        seen["thread"] = threading.get_ident()
        seen["bounded"] = kwargs.get("cancel_check") is not None
        return ChatResult(response={"choices": [{"message": {"content": "[]"}}]})

    with transaction.atomic(), mock.patch("backend.inference.chat.chat", side_effect=fake_chat):
        response = ai_runner._chat(access, MESSAGES, 100)
    assert response["choices"][0]["message"]["content"] == "[]"
    assert seen["atomic"] is False
    assert seen["thread"] != threading.get_ident()
    assert seen["bounded"] is True


def test_inline_call_past_its_bound_is_a_timeout(owner):
    access = _local_access(owner)

    def fake_chat(*_args, **kwargs):
        assert kwargs["cancel_check"]()  # the bound has already passed
        raise LLMAccessError(code="cancelled", message="cancelled")

    with (
        transaction.atomic(),
        mock.patch.object(ai_runner, "INLINE_LOCAL_TIMEOUT_S", -1),
        mock.patch("backend.inference.chat.chat", side_effect=fake_chat),
        pytest.raises(LLMAccessError) as excinfo,
    ):
        ai_runner._chat(access, MESSAGES, 100)
    assert excinfo.value.code == "timeout"
