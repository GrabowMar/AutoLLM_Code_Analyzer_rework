"""Functional smoke test: probe a running generated app's declared endpoints.

Static analysis can't tell whether a generated app actually works; this can.
After a container boots, hit its health endpoint and every GET endpoint the
app-requirement template declares, and record which respond. The result is
persisted onto the generation job's metrics so model comparisons can weigh
"does it run" alongside findings, even after the container is removed.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING

import requests

from backend.runtime.proxy import _upstream_base

if TYPE_CHECKING:
    from backend.generation.models import GenerationJob
    from backend.runtime.models import ContainerInstance

logger = logging.getLogger(__name__)

HEALTH_PATH = "/api/health"
_HEALTH_ATTEMPTS = 3
_HEALTH_RETRY_S = 2.0
_REQUEST_TIMEOUT_S = 8
_PASS_THRESHOLD = 0.8

# ":id" / "{id}"-style path parameters; seeded apps have a record with id 1.
_PATH_PARAM_RE = re.compile(r":(\w+)|\{(\w+)\}")


def run_smoke(job: GenerationJob | None, container: ContainerInstance) -> dict:
    """Probe *container*'s health + declared GET endpoints; persist the result."""
    base = _upstream_base(container)
    if base is None:
        result = {"passed": False, "error": "App routing not configured"}
        _persist(job, container, result)
        return result

    checks: list[dict] = []
    health_ok = _probe_health(base, checks)
    if health_ok and job is not None:
        for endpoint, is_admin in _declared_get_endpoints(job):
            checks.append(_probe_endpoint(base, endpoint, is_admin=is_admin))

    endpoint_checks = [c for c in checks if c["path"] != HEALTH_PATH]
    ok_count = sum(1 for c in endpoint_checks if c["ok"])
    pass_rate = (ok_count / len(endpoint_checks)) if endpoint_checks else (1.0 if health_ok else 0.0)
    result = {
        "passed": bool(health_ok and pass_rate >= _PASS_THRESHOLD),
        "health_ok": health_ok,
        "endpoints_checked": len(endpoint_checks),
        "endpoints_ok": ok_count,
        "pass_rate": round(pass_rate, 3),
        "checks": checks,
    }
    _persist(job, container, result)
    return result


def _probe_health(base: str, checks: list[dict]) -> bool:
    """The app may still be booting right after the container starts; retry."""
    status: int | None = None
    for attempt in range(_HEALTH_ATTEMPTS):
        status = _get_status(f"{base}{HEALTH_PATH}")
        if status is not None and 200 <= status < 300:
            checks.append({"method": "GET", "path": HEALTH_PATH, "status": status, "ok": True})
            return True
        if attempt < _HEALTH_ATTEMPTS - 1:
            time.sleep(_HEALTH_RETRY_S)
    checks.append({"method": "GET", "path": HEALTH_PATH, "status": status, "ok": False})
    return False


def _declared_get_endpoints(job: GenerationJob) -> list[tuple[dict, bool]]:
    app_req = job.app_requirement
    if app_req is None:
        return []
    out: list[tuple[dict, bool]] = []
    for endpoint in app_req.api_endpoints or []:
        if str(endpoint.get("method", "")).upper() == "GET":
            out.append((endpoint, False))
    for endpoint in app_req.admin_api_endpoints or []:
        if str(endpoint.get("method", "")).upper() == "GET":
            out.append((endpoint, True))
    return out


def _probe_endpoint(base: str, endpoint: dict, *, is_admin: bool) -> dict:
    raw_path = str(endpoint.get("path", ""))
    parametrized = bool(_PATH_PARAM_RE.search(raw_path))
    path = _PATH_PARAM_RE.sub("1", raw_path)
    if raw_path == "/api/health":
        parametrized = False
    status = _get_status(f"{base}{path}")

    # A 401/403 means the route exists behind auth — functionally present.
    # A 404 on a static path means the declared route is missing; on a
    # parametrized path it may just be an absent record, so tolerate it.
    if status is None or status >= 500:
        ok = False
    elif status == 404:
        ok = parametrized
    else:
        ok = True
    return {
        "method": "GET",
        "path": raw_path,
        "probed": path,
        "status": status,
        "ok": ok,
        "admin": is_admin,
    }


def _get_status(url: str) -> int | None:
    try:
        return requests.get(url, timeout=_REQUEST_TIMEOUT_S, allow_redirects=False).status_code
    except requests.RequestException:
        return None


def _persist(job: GenerationJob | None, container: ContainerInstance, result: dict) -> None:
    container.metadata = {**(container.metadata or {}), "smoke": result}
    container.save(update_fields=["metadata", "updated_at"])
    if job is not None:
        # Keep the job copy lean — reports only need the summary numbers.
        job.metrics = {
            **(job.metrics or {}),
            "functional": {k: v for k, v in result.items() if k != "checks"},
        }
        job.save(update_fields=["metrics", "updated_at"])
