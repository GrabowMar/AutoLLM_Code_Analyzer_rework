"""``route_error_terminated``: an evaluated attempt whose trajectory ended on a failed LLM call after at least
one successful call is flagged (validity note) without changing its termination or outcome."""

from __future__ import annotations

import pytest

from backend.generation.agentic import runner
from backend.generation.agentic.validity import ROUTE_ERROR_NOTE
from backend.generation.agentic.validity import build_validity
from backend.generation.agentic.validity import route_error_note
from backend.generation.contracts.outcome import Termination
from backend.generation.contracts.outcome import Validity
from backend.generation.study.results import _has_route_error
from backend.generation.testing.fake_gateway import canned_call


def _go_error(index: int, *, status: int = 200) -> dict:
    """The gateway's record of a streamed call the proxy answered with an in-stream error event."""
    call = canned_call(index, status=status)
    call["status"] = status
    call["transport_error"] = False
    call["usage"] = None
    call["error"] = {
        "message": "opencode-go answered 400: Upstream request failed: [server_error] Upstream response was not valid "
        "JSON",
        "code": "upstream_error",
        "type": "upstream_error",
        "status": 400,
    }
    return call


def _note(validity: Validity):
    return next((n for n in validity.notes if n.code == ROUTE_ERROR_NOTE), None)


def test_an_evaluated_attempt_that_ended_on_a_route_error_is_flagged():
    calls = [canned_call(1), canned_call(2), _go_error(3)]
    validity = build_validity(termination=Termination.HARNESS_ERROR, calls=calls)
    flag = _note(validity)
    assert flag is not None
    assert flag.level == "warn"
    assert flag.detail.startswith(
        "kind=http status=400 code=upstream_error call=3 request_class=main ok_calls=2 calls=3 ",
    )
    assert "[server_error]" in flag.detail
    # The termination (and so the outcome the runner decides) is untouched.
    assert validity.termination == Termination.HARNESS_ERROR
    assert {n.code for n in validity.notes} == {"harness_error", ROUTE_ERROR_NOTE}
    # Queryable from the stored JSON.
    assert _has_route_error(validity.model_dump(mode="json"))


@pytest.mark.parametrize(
    ("termination", "last"),
    [
        (Termination.COMPLETED, canned_call(3, status=429)),  # an upstream 4xx after the gateway's retries
        (Termination.HARNESS_CRASH, canned_call(3, status=502)),  # a transport failure
        (Termination.IDLE, canned_call(3, status=503)),
    ],
)
def test_upstream_statuses_and_transport_failures_are_route_errors(termination, last):
    flag = route_error_note(termination, [canned_call(1), canned_call(2), last])
    assert flag is not None
    kind = "transport" if last["transport_error"] else "http"
    assert f"kind={kind} status={last['status']}" in flag.detail


def test_what_is_not_flagged():
    ok = [canned_call(1), canned_call(2)]
    # The trajectory ended on a good call (an earlier failure the harness survived does not count).
    assert route_error_note(Termination.COMPLETED, [canned_call(1), _go_error(2), canned_call(3)]) is None
    assert route_error_note(Termination.COMPLETED, ok) is None
    # Zero successful calls: infra_override already makes it LLM_ERROR / INFRA (not model data).
    assert route_error_note(Termination.HARNESS_ERROR, [_go_error(1)]) is None
    assert route_error_note(Termination.LLM_ERROR, [*ok, _go_error(3)]) is None
    assert route_error_note(Termination.INFRA, [*ok, _go_error(3)]) is None
    # The treatment's own caps are not route errors.
    assert route_error_note(Termination.BUDGET, [*ok, _go_error(3)]) is None
    refused = _go_error(3)
    refused["error"] = {"message": "run budget exhausted", "code": "budget_exhausted", "status": 400}
    assert route_error_note(Termination.HARNESS_ERROR, [*ok, refused]) is None
    # A call cut because the harness was killed (wall clock / idle) is not a route error.
    gone = _go_error(3)
    gone["client_gone"] = True
    assert route_error_note(Termination.WALL_CLOCK, [*ok, gone]) is None


def test_call_order_follows_the_gateway_sequence_not_the_list_order():
    calls = [_go_error(3), canned_call(1), canned_call(2)]
    assert route_error_note(Termination.HARNESS_ERROR, calls) is not None
    calls = [canned_call(3), _go_error(2), canned_call(1)]
    assert route_error_note(Termination.HARNESS_ERROR, calls) is None


def test_the_flag_does_not_change_the_outcome_decision():
    calls = [canned_call(1), _go_error(2)]
    # infra_override only overrides zero-successful-call attempts; this one stays evaluated.
    result = type("R", (), {"workspace_tar": object(), "workspace_truncated": False})()
    assert runner.infra_override(Termination.HARNESS_ERROR, calls=calls, result=result) == Termination.HARNESS_ERROR
    assert build_validity(termination=Termination.HARNESS_ERROR, calls=calls).truncated is False


def test_a_failed_auxiliary_call_after_the_last_main_call_is_not_the_trajectorys_end():
    aux = _go_error(4)
    aux["request_class"] = "auxiliary"
    title = _go_error(5)
    title["request_class"] = "some-side-call"  # an unknown class counts as auxiliary
    calls = [canned_call(1), canned_call(2), canned_call(3), aux, title]
    assert route_error_note(Termination.COMPLETED, calls) is None
    # The last main call failing still counts, with the good auxiliary calls in ok_calls.
    calls = [
        canned_call(1),
        canned_call(2, request_class="auxiliary"),
        _go_error(3),
        canned_call(4, request_class="auxiliary"),
    ]
    flag = route_error_note(Termination.COMPLETED, calls)
    assert flag is not None
    assert "call=3 request_class=main ok_calls=3 calls=4 " in flag.detail
    # Only auxiliary calls: there is no trajectory end to judge.
    assert route_error_note(Termination.COMPLETED, [canned_call(1, request_class="auxiliary"), aux]) is None


@pytest.mark.parametrize(
    "error",
    [
        # The proxy's own call.max_input_tokens refusal (proxy_server._budget_refusal_payload).
        {"message": "estimated 210000 prompt tokens exceed the per-call input cap", "code": "context_length_exceeded"},
        # A provider's context overflow (anthropic_native.error_payload maps it to the same code).
        {"message": "prompt is too long: 210000 tokens > 200000 maximum", "type": "invalid_request_error"},
        "context_length_exceeded: the request is too large",
    ],
)
def test_context_refusals_and_overflows_are_not_route_errors(error):
    last = _go_error(3, status=400)
    last["error"] = error
    for termination in (Termination.COMPLETED, Termination.HARNESS_ERROR):
        assert route_error_note(termination, [canned_call(1), canned_call(2), last]) is None
