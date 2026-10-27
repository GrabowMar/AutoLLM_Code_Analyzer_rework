"""The runtime contract shared by the task brief and the hidden evaluation.

Everything the conformance suite checks is stated to the agent in the brief,
in the same words for every harness and model; nothing is checked that the
brief does not ask for. This module is the single source of both, so the two
cannot drift apart. Bump ``CONTRACT_VERSION`` when either side changes.

App specs say *which* endpoints need a signed-in user ("require auth for
create/update/delete") but almost none define *how* to sign in, so each agent
used to invent its own scheme and write endpoints were ungradable. The
contract fills that gap: unless the spec defines its own auth endpoints, the
app exposes the standard register/login pair below and accepts bearer
tokens; admin endpoints use an admin account whose password comes from the
environment (no hard-coded credential to find).
"""

from __future__ import annotations

from typing import Any

CONTRACT_VERSION = 1

REGISTER_PATH = "/api/auth/register"
LOGIN_PATH = "/api/auth/login"
ADMIN_USERNAME = "admin"
ADMIN_EMAIL = "admin@example.com"
ADMIN_PW_ENV = "ADMIN_PASSWORD"
ADMIN_PASSWORD_DEFAULT = "admin"  # noqa: S105 - the brief's documented fallback; evaluation always sets the env var
HEALTH_PATH = "/api/health"


def _all_endpoints(app: dict[str, Any]) -> list[dict[str, Any]]:
    return [*(app.get("api_endpoints") or []), *(app.get("admin_api_endpoints") or [])]


def access_control_text(app: dict[str, Any]) -> str:
    lines = [r for r in app.get("backend_requirements") or [] if r.lower().startswith("access control")]
    return " ".join(lines).lower()


def writes_require_auth(app: dict[str, Any]) -> bool:
    """The spec reserves at least one write for signed-in users."""
    text = access_control_text(app)
    return "require auth" in text or "restrict" in text


def has_admin_endpoints(app: dict[str, Any]) -> bool:
    return bool(app.get("admin_api_endpoints"))


def spec_defines_auth(app: dict[str, Any]) -> bool:
    paths = {ep.get("path") for ep in _all_endpoints(app)}
    return REGISTER_PATH in paths and LOGIN_PATH in paths


def identity_field(app: dict[str, Any]) -> str:
    """``email`` when the spec's own register endpoint is email-based, else ``username``."""
    for ep in _all_endpoints(app):
        if ep.get("path") == REGISTER_PATH and isinstance(ep.get("request"), dict) and "email" in ep["request"]:
            return "email"
    return "username"


def needs_accounts(app: dict[str, Any]) -> bool:
    return writes_require_auth(app) or has_admin_endpoints(app) or spec_defines_auth(app)


def describe(app: dict[str, Any] | None) -> dict[str, Any]:
    """The contract for one app, as both the brief template and the tester consume it."""
    app = app or {}
    field = identity_field(app)
    return {
        "version": CONTRACT_VERSION,
        "health_path": HEALTH_PATH,
        "accounts": needs_accounts(app),
        "spec_defines_auth": spec_defines_auth(app),
        "writes_require_auth": writes_require_auth(app),
        "admin": has_admin_endpoints(app),
        "register_path": REGISTER_PATH,
        "login_path": LOGIN_PATH,
        "identity_field": field,
        "admin_identity": ADMIN_EMAIL if field == "email" else ADMIN_USERNAME,
        "admin_password_env": ADMIN_PW_ENV,
        "admin_password_default": ADMIN_PASSWORD_DEFAULT,
    }
