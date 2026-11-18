"""Supplemental stateful HTTP contract checks, versioned separately from an EvalSuite.

These checks cover account reset, administrator unlock and session lifecycle. They
operate on fresh isolated app instances and never replace the primary suite grade.
Only response structure/status and boolean assertions are exported; credentials,
reset tokens and response bodies are not retained in the result.
"""

from __future__ import annotations

import html
import http.cookiejar
import json
import os
import re
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

VERSION = 3


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _req: Any, _fp: Any, _code: int, _msg: str, _headers: Any, _url: str) -> None:
        return None


class Client:
    def __init__(self, base_url: str, auth_mode: str, trace: list[dict[str, Any]]) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            msg = "A feature audit requires an HTTP(S) application URL"
            raise ValueError(msg)
        self.base_url = base_url.rstrip("/")
        self.auth_mode = auth_mode
        self.trace = trace
        self.token = ""
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), NoRedirect())

    def request(self, method: str, path: str, body: Any = None, *, authenticated: bool = True) -> tuple[int, Any]:
        headers = {"Accept": "application/json"}
        if authenticated and self.token and self.auth_mode != "cookie_session":
            headers["Authorization"] = "Bearer " + self.token
        if body is not None:
            headers["Content-Type"] = "application/json"
        if authenticated and self.auth_mode == "cookie_session" and method not in {"GET", "HEAD"}:
            for cookie in self.jar:
                if cookie.name.lower() in {"csrftoken", "csrf_token", "csrf"}:
                    headers["X-CSRF-Token"] = cookie.value
                    headers["X-CSRFToken"] = cookie.value
        req = urllib.request.Request(  # noqa: S310 - base URL scheme is checked at construction
            self.base_url + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=headers,
            method=method,
        )
        try:
            response = self.opener.open(req, timeout=8)
        except urllib.error.HTTPError as exc:
            response = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self.trace.append(
                {"method": method, "path": path.split("?", 1)[0], "status": 0, "error": type(exc).__name__},
            )
            return 0, None
        with response:
            status = response.status
            payload = response.read(1024 * 1024)
        try:
            value = json.loads(payload) if payload else None
        except (UnicodeDecodeError, ValueError):
            value = None
        self.trace.append(
            {
                "method": method,
                "path": path.split("?", 1)[0],
                "status": status,
                "json_keys": sorted(value) if isinstance(value, dict) else None,
            },
        )
        return status, value

    def login(self, username: str, password: str) -> bool:
        status, result = self.request(
            "POST",
            "/api/auth/login",
            {"username": username, "password": password},
            authenticated=False,
        )
        if status != 200:
            return False
        token = result.get("token") if isinstance(result, dict) else None
        self.token = token if isinstance(token, str) else ""
        return bool(list(self.jar)) if self.auth_mode == "cookie_session" else bool(self.token)


class Audit:
    def __init__(self, base_url: str, auth_mode: str, prose_variant: str = "standard") -> None:
        self.base_url, self.auth_mode = base_url, auth_mode
        self.prose_variant = prose_variant
        self.trace: list[dict[str, Any]] = []
        self.checks: list[dict[str, Any]] = []
        self.name = "lifecycle_" + secrets.token_hex(8)
        self.password = "Pwd-" + secrets.token_urlsafe(18)
        self.email = self.name + "@example.test"

    def client(self) -> Client:
        return Client(self.base_url, self.auth_mode, self.trace)

    def check(self, name: str, endpoint: str, passed: bool, expected: Any, observed: Any) -> None:
        self.checks.append(
            {
                "id": name,
                "endpoint": endpoint,
                "status": "pass" if passed else "fail",
                "expected": expected,
                "observed": observed,
            },
        )

    def blocked(self, names: list[tuple[str, str]], reason: str) -> None:
        self.checks.extend(
            {"id": name, "endpoint": endpoint, "status": "blocked", "reason": reason} for name, endpoint in names
        )

    def register(self, client: Client) -> bool:
        status, _ = client.request(
            "POST",
            "/api/auth/register",
            {"username": self.name, "password": self.password, "email": self.email},
            authenticated=False,
        )
        return status == 201

    def reset(self) -> None:
        targets = [
            (name, "reset.confirm")
            for name in (
                "reset.invalid_token",
                "reset.confirm",
                "reset.new_password",
                "reset.old_password_rejected",
                "reset.token_single_use",
            )
        ]
        if self.prose_variant == "exhaustive":
            targets.append(("reset.old_session_invalidated", "reset.confirm"))
        else:
            self.checks.append(
                {
                    "id": "reset.old_session_invalidated",
                    "endpoint": "reset.confirm",
                    "status": "not_applicable",
                    "reason": "Invalidating prior sign-ins is not stated in the standard prose.",
                },
            )
        user = self.client()
        if not self.register(user) or not user.login(self.name, self.password):
            self.blocked(targets, "A fresh account could not be registered and signed in.")
            return
        status, _ = user.request("PATCH", "/api/me", {"email": self.email})
        if not 200 <= status < 300:
            self.blocked(targets, "The account email could not be set for mailbox delivery.")
            return
        anon = self.client()
        code, _ = anon.request("POST", "/api/auth/password-reset", {"username": self.name}, authenticated=False)
        if not 200 <= code < 300:
            self.blocked(targets, "The reset-request prerequisite did not return a successful status.")
            return
        code, outbox = anon.request(
            "GET",
            "/api/outbox?" + urllib.parse.urlencode({"to": self.email}),
            authenticated=False,
        )
        items = outbox.get("items", []) if isinstance(outbox, dict) else []
        token = None
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            text = html.unescape(str(item.get("body") or ""))
            for match in re.findall(r'(?:https?://[^\s"<>]+)?/reset\?[^\s"<>]+', text):
                values = urllib.parse.parse_qs(urllib.parse.urlsplit(match.rstrip(".)")).query).get("token")
                if values:
                    token = values[0]
                    break
            if token:
                break
        if not 200 <= code < 300 or not token:
            self.blocked(targets, "The mailbox did not expose the specified /reset?token= link.")
            return
        new_password = "New-" + secrets.token_urlsafe(18)
        code, _ = anon.request(
            "POST",
            "/api/auth/password-reset/confirm",
            {"token": "unknown-" + secrets.token_hex(12), "new_password": new_password},
            authenticated=False,
        )
        self.check("reset.invalid_token", "reset.confirm", 400 <= code < 500, "a 4xx rejection", code)
        code, _ = anon.request(
            "POST",
            "/api/auth/password-reset/confirm",
            {"token": token, "new_password": new_password},
            authenticated=False,
        )
        self.check("reset.confirm", "reset.confirm", 200 <= code < 300, "a 2xx success", code)
        new = self.client()
        ok = new.login(self.name, new_password)
        self.check("reset.new_password", "reset.confirm", ok, "new password signs in", ok)
        old = self.client()
        code, _ = old.request(
            "POST",
            "/api/auth/login",
            {"username": self.name, "password": self.password},
            authenticated=False,
        )
        self.check(
            "reset.old_password_rejected",
            "reset.confirm",
            code in {401, 403, 404},
            "access denied (401/403/404)",
            code,
        )
        code, _ = anon.request(
            "POST",
            "/api/auth/password-reset/confirm",
            {"token": token, "new_password": "Third-" + secrets.token_urlsafe(18)},
            authenticated=False,
        )
        self.check("reset.token_single_use", "reset.confirm", 400 <= code < 500, "a 4xx rejection", code)
        if self.prose_variant == "exhaustive":
            code, _ = user.request("GET", "/api/me")
            self.check(
                "reset.old_session_invalidated",
                "reset.confirm",
                code in {401, 403, 404},
                "old sign-in denied",
                code,
            )

    def unlock(self, admin_username: str, admin_password: str) -> None:
        targets = [
            (name, "admin.unlock")
            for name in (
                "unlock.anon_denied",
                "unlock.non_admin_denied",
                "unlock.lockout_established",
                "unlock.admin_unlock",
                "unlock.login_restored",
            )
        ]
        admin, user = self.client(), self.client()
        if not admin.login(admin_username, admin_password):
            self.blocked(targets, "The runtime-provisioned administrator could not sign in.")
            return
        if not self.register(user) or not user.login(self.name, self.password):
            self.blocked(targets, "A fresh account could not be registered and signed in.")
            return
        path = "/api/admin/locks/" + urllib.parse.quote(self.name, safe="") + "/unlock"
        anon = self.client()
        for _ in range(5):
            anon.request(
                "POST",
                "/api/auth/login",
                {"username": self.name, "password": "wrong-" + self.password},
                authenticated=False,
            )
        code, _ = anon.request(
            "POST",
            "/api/auth/login",
            {"username": self.name, "password": self.password},
            authenticated=False,
        )
        locked = code == 429
        self.check("unlock.lockout_established", "admin.unlock", locked, 429, code)
        if not locked:
            self.blocked(
                [t for t in targets if t[0] != "unlock.lockout_established"],
                "Five incorrect passwords did not establish the specified lockout.",
            )
            return
        code, _ = anon.request("POST", path, {}, authenticated=False)
        self.check("unlock.anon_denied", "admin.unlock", code in {401, 403, 404}, "access denied (401/403/404)", code)
        code, _ = user.request("POST", path, {})
        self.check(
            "unlock.non_admin_denied",
            "admin.unlock",
            code in {401, 403, 404},
            "access denied (401/403/404)",
            code,
        )
        code, _ = admin.request("POST", path, {})
        self.check("unlock.admin_unlock", "admin.unlock", 200 <= code < 300, "a 2xx success", code)
        ok = self.client().login(self.name, self.password)
        self.check("unlock.login_restored", "admin.unlock", ok, "correct password signs in after unlock", ok)

    def session(self) -> None:
        targets = [
            ("session.csrf_anon_denied", "auth.csrf"),
            ("session.csrf_token", "auth.csrf"),
            ("session.logout", "auth.logout"),
        ]
        if self.auth_mode == "cookie_session":
            targets.append(("session.cookie_invalidated", "auth.logout"))
        else:
            self.checks.append(
                {
                    "id": "session.cookie_invalidated",
                    "endpoint": "auth.logout",
                    "status": "not_applicable",
                    "reason": "The selected bearer contract does not specify server-side token revocation on logout.",
                },
            )
        user = self.client()
        if not self.register(user) or not user.login(self.name, self.password):
            self.blocked(targets, "A fresh account could not be registered and signed in.")
            return
        code, _ = self.client().request("GET", "/api/auth/csrf", authenticated=False)
        self.check(
            "session.csrf_anon_denied",
            "auth.csrf",
            code in {401, 403, 404},
            "access denied (401/403/404)",
            code,
        )
        code, value = user.request("GET", "/api/auth/csrf")
        token = value.get("csrf_token") if isinstance(value, dict) else None
        self.check(
            "session.csrf_token",
            "auth.csrf",
            200 <= code < 300 and isinstance(token, str) and bool(token),
            "2xx with a nonempty csrf_token string",
            {"status": code, "token_present": isinstance(token, str) and bool(token)},
        )
        code, _ = user.request("POST", "/api/auth/logout", {})
        self.check("session.logout", "auth.logout", 200 <= code < 300, "a 2xx success", code)
        if self.auth_mode == "cookie_session":
            code, _ = user.request("GET", "/api/account")
            self.check(
                "session.cookie_invalidated",
                "auth.logout",
                code in {401, 403, 404},
                "access denied (401/403/404)",
                code,
            )


def audit(config: dict[str, Any]) -> dict[str, Any]:
    runner = Audit(
        config.get("base_url", "http://app:8000"),
        config.get("auth_mode", "contract_bearer"),
        config.get("prose_variant", "standard"),
    )
    task = config["task"]
    if task == "account_password_reset":
        runner.reset()
    elif task == "login_rate_limited":
        runner.unlock(config.get("admin_username", "admin"), config.get("admin_password", ""))
    elif task == "banking_transfers_cookie":
        runner.session()
    else:
        msg = f"No supplemental lifecycle contract for {task}"
        raise ValueError(msg)
    return {"version": VERSION, "task": task, "checks": runner.checks, "requests": runner.trace}


if __name__ == "__main__":
    settings = json.loads(os.environ["FEATURE_AUDIT_CONFIG"])
    settings["admin_password"] = os.environ.get("ADMIN_PASSWORD", "")
    sys.stdout.write(json.dumps(audit(settings)) + "\n")
    sys.stdout.flush()
