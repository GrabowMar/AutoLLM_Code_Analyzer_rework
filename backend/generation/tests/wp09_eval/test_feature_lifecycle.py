"""Stateful positive/negative controls for supplemental feature checks."""

from __future__ import annotations

import pytest

from backend.generation.evaluation.feature_lifecycle import audit
from backend.generation.testing.lifecycle_control import control


@pytest.mark.parametrize("task", ["account_password_reset", "login_rate_limited", "banking_transfers_cookie"])
def test_lifecycle_control_satisfies_applicable_contracts(task: str) -> None:
    with control() as (url, password):
        result = audit({"task": task, "base_url": url, "admin_password": password})
    assert result["checks"]
    assert all(c["status"] in {"pass", "not_applicable"} for c in result["checks"])


def test_cookie_logout_invalidates_the_session() -> None:
    with control() as (url, password):
        result = audit(
            {
                "task": "banking_transfers_cookie",
                "base_url": url,
                "admin_password": password,
                "auth_mode": "cookie_session",
            },
        )
    assert all(c["status"] == "pass" for c in result["checks"])


@pytest.mark.parametrize("task", ["account_password_reset", "login_rate_limited", "banking_transfers_cookie"])
def test_standard_contract_accepts_success_codes_it_does_not_specify(task: str) -> None:
    with control(success_status=200) as (url, password):
        result = audit({"task": task, "base_url": url, "admin_password": password})
    assert all(c["status"] in {"pass", "not_applicable"} for c in result["checks"])


def test_reset_prior_session_requirement_tracks_prose_variant() -> None:
    for variant, expected in [("standard", "not_applicable"), ("exhaustive", "pass")]:
        with control() as (url, password):
            result = audit(
                {
                    "task": "account_password_reset",
                    "base_url": url,
                    "admin_password": password,
                    "prose_variant": variant,
                },
            )
        checks = [c for c in result["checks"] if c["id"] == "reset.old_session_invalidated"]
        assert len(checks) == 1
        assert checks[0]["status"] == expected


@pytest.mark.parametrize(
    ("mutant", "task", "failed"),
    [
        ("reset_accepts_reuse", "account_password_reset", "reset.token_single_use"),
        ("unlock_no_effect", "login_rate_limited", "unlock.login_restored"),
        ("unlock_unprotected", "login_rate_limited", "unlock.non_admin_denied"),
        ("csrf_missing", "banking_transfers_cookie", "session.csrf_token"),
        ("logout_unavailable", "banking_transfers_cookie", "session.logout"),
    ],
)
def test_lifecycle_mutants_are_detected(mutant: str, task: str, failed: str) -> None:
    with control(mutant) as (url, password):
        result = audit({"task": task, "base_url": url, "admin_password": password})
    assert failed in {c["id"] for c in result["checks"] if c["status"] == "fail"}
