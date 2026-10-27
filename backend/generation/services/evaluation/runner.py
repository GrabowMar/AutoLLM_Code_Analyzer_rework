"""Build, run and grade one generated app with the hidden conformance suite.

Every sample of every mode is evaluated by the platform the same way, never
by its own report::

    build   prepare_build_dir(job) + the stack's own Dockerfile (apps-dind)
    run     the image on an *internal* network (no egress), memory/CPU caps,
            ADMIN_PASSWORD set to a fresh random value
    grade   images/conformance-tester on the same network, plan = the app's
            API contract + the shared runtime/accounts contract

Outcome ``status``:

* ``passed`` / ``failed`` -- the suite ran; ``summary.pass_rate`` grades it,
* ``build_failed`` / ``start_failed`` -- the sample does not build or never
  answers its health check (a sample outcome, pass_rate 0),
* ``infra_error`` -- the platform could not evaluate (daemon down or out of
  disk, docker client/API errors, registry outage during build after a
  retry): **not** a property of the sample, exclude it from analyses and
  re-evaluate.

Build errors are split by where they come from: an ``error`` chunk of the
build stream is a failed build step (the sample's ``build_failed``, unless
its text is a registry/network/disk signature); an exception raised by the
docker client itself (connection refused, ``Max retries exceeded``, an API
500, ...) is the platform's and makes the build ``infra_error``. A read
timeout of the build stream (a step silent for ``BUILD_TIMEOUT_S``) stays a
build failure.

A tester that is still running at ``TESTER_TIMEOUT_S`` means the suite got
past the health phase (an app that never answers health fails every check
without a request) and the app then answered too slowly to finish: when the
app still answers its health endpoint (probed from inside the tester
container) the outcome is ``failed`` with ``pass_rate`` 0 and
``tester_timed_out`` set (the tester reports only at the end, so there is no
partial score). If that cannot be confirmed it stays ``infra_error``.

``EVALUATOR_VERSION`` + the tester's own VERSION and sha256 identify the
grading procedure, so scores are only compared under the same one. The
build's healing (``runtime/services/scaffolding.py``: patches, inferred
requirements, placeholder entry files) is part of that procedure, so a
change to it bumps ``EVALUATOR_VERSION`` too.
"""

# Teardown/probing code catches everything on purpose: evaluation must always
# clean up and report, never take the job down with it.
# ruff: noqa: BLE001

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

import requests

from backend.generation.services.agent.images import IMAGES_DIR
from backend.generation.services.agent.images import TESTER_IMAGE
from backend.generation.services.agent.sandbox import SandboxError
from backend.generation.services.agent.sandbox import ensure_image
from backend.generation.services.agent.workspace import make_tar
from backend.generation.services.evaluation import contract as contract_mod
from backend.generation.services.profile_resolver import app_requirement_to_dict
from backend.generation.services.prompt_renderer import templates_state_contract
from backend.runtime.services import docker_manager
from backend.runtime.services.scaffolding import HealingMode
from backend.runtime.services.scaffolding import HealingReport
from backend.runtime.services.scaffolding import get_stack_config
from backend.runtime.services.scaffolding import prepare_build_dir
from backend.runtime.services.scaffolding import resolve_stack_slug

if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.generation.models import GenerationJob

logger = logging.getLogger(__name__)

# 2: generation-library modernization (lucide icons aliased instead of dropped
# and healed in Vue too, react-router-dom imports and the Tailwind 4
# stylesheet entry healed, requirement names normalized, local modules and
# passlib's bcrypt handled, placeholder backends serving ./static by path).
EVALUATOR_VERSION = 2
LABEL = "llmlab.evaluation"
BUILD_TIMEOUT_S = 1200
TESTER_TIMEOUT_S = 600
APP_MEM_LIMIT = "1g"
# Build-log signatures of a registry/network problem rather than a broken app.
_INFRA_PATTERNS = re.compile(
    r"ECONNRESET|ETIMEDOUT|EAI_AGAIN|ENOTFOUND|Temporary failure in name resolution|"
    r"Could not resolve host|Read timed out|Connection reset by peer|503 Service Unavailable|"
    r"TLS handshake timeout|network is unreachable|no space left on device",
    re.IGNORECASE,
)
HEALTH_PROBE_TIMEOUT_S = 10


def tester_identity() -> dict[str, Any]:
    source = (IMAGES_DIR / TESTER_IMAGE / "tester.py").read_bytes()
    match = re.search(rb"^VERSION = (\d+)", source, re.MULTILINE)
    return {
        "evaluator_version": EVALUATOR_VERSION,
        "tester_version": int(match.group(1)) if match else None,
        "tester_sha256": hashlib.sha256(source).hexdigest(),
        "contract_version": contract_mod.CONTRACT_VERSION,
    }


def _app_spec(job: GenerationJob) -> dict[str, Any] | None:
    snapshot = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
    if isinstance(snapshot.get("app_requirement"), dict):
        return snapshot["app_requirement"]
    return app_requirement_to_dict(job.app_requirement) if job.app_requirement else None


def build_plan(job: GenerationJob, *, admin_password: str, has_frontend: bool) -> dict[str, Any] | None:
    app = _app_spec(job)
    if not app:
        return None
    snapshot = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
    contract = snapshot.get("contract") or contract_mod.describe(app)
    endpoints = [{**ep, "admin": False} for ep in app.get("api_endpoints") or []]
    endpoints += [{**ep, "admin": True} for ep in app.get("admin_api_endpoints") or []]
    return {
        "base_url": "http://app:8000",
        "has_frontend": has_frontend,
        "contract": contract,
        "admin_password": admin_password,
        "endpoints": endpoints,
        "startup_timeout_s": 120,
        # Deterministic per job, so a re-evaluation sends identical requests.
        "nonce": str(job.id).replace("-", "")[:8],
    }


class Evaluation:
    def __init__(self, job: GenerationJob, *, healing: str | None = None) -> None:
        self.job = job
        # None: the job's own build_healing; set by the healing ablation.
        self.healing = healing
        self.short = str(job.id).replace("-", "")[:12]
        if healing is not None:
            # Distinct names so an ablation never collides with the job's own evaluation.
            self.short = f"{self.short}-{healing.replace('_', '')}"
        self.client = None
        self.network = None
        self.containers: list = []
        self.image_tag = f"llmlab-eval/{self.short}:latest"
        # llmlab.started: the executor's orphan reaper skips containers younger than 2 minutes.
        self.labels = {LABEL: str(job.id), "llmlab.started": str(int(time.time()))}

    def run(self) -> dict[str, Any]:
        started = time.monotonic()
        result: dict[str, Any] = {**tester_identity(), "status": "infra_error"}
        try:
            result |= self._run()
        except SandboxError as exc:
            result |= {"status": "infra_error", "error": str(exc)[:2000]}
        except Exception as exc:
            logger.exception("Evaluation of job %s failed", self.job.id)
            result |= {"status": "infra_error", "error": f"{type(exc).__name__}: {exc}"[:2000]}
        finally:
            self._teardown()
        result["seconds"] = round(time.monotonic() - started, 2)
        return result

    def _run(self) -> dict[str, Any]:
        stack = get_stack_config(resolve_stack_slug(self.job))
        admin_password = secrets.token_urlsafe(18)
        plan = build_plan(self.job, admin_password=admin_password, has_frontend=bool(stack.get("has_frontend")))
        if plan is None:
            return {"status": "not_applicable", "error": "job has no app spec to evaluate against"}
        self.client = docker_manager.new_client()
        tester_tag, tester_id = ensure_image(self.client, TESTER_IMAGE)

        build = self._build()
        if not build["ok"]:
            status = "infra_error" if build.get("infra_suspected") else "build_failed"
            return {"status": status, "build": build, "summary": _zero_summary()}

        self.network = self.client.networks.create(
            f"llmlab-eval-{self.short}",
            driver="bridge",
            internal=True,
            labels=self.labels,
        )
        app = self.client.containers.create(
            self.image_tag,
            name=f"llmlab-eval-app-{self.short}",
            environment={contract_mod.ADMIN_PASSWORD_ENV: admin_password},
            network=self.network.name,
            labels=self.labels,
            mem_limit=APP_MEM_LIMIT,
            nano_cpus=1_000_000_000,
            pids_limit=512,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
        )
        self.containers.append(app)
        self.network.disconnect(app)
        self.network.connect(app, aliases=["app"])
        app.start()

        tester = self.client.containers.create(
            tester_tag,
            name=f"llmlab-eval-tester-{self.short}",
            network=self.network.name,
            labels=self.labels,
            mem_limit="256m",
            nano_cpus=500_000_000,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges"],
        )
        self.containers.append(tester)
        tester.put_archive("/work", make_tar({"plan.json": json.dumps(plan).encode()}))
        tester.start()
        try:
            tester.wait(timeout=TESTER_TIMEOUT_S)
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
            # Only a tester that is demonstrably still running timed out; a
            # daemon that cannot answer reload() raises here (infra_error).
            tester.reload()
            if tester.status != "running":
                raise
            return self._tester_timed_out(app, tester, plan, build, tester_tag, tester_id)
        output = tester.logs().decode("utf-8", errors="replace")
        report = _parse_report(output)

        app.reload()
        app_state = app.attrs.get("State", {})
        deps = _resolved_python_deps(app)
        app_log = app.logs(tail=200).decode("utf-8", errors="replace")
        if report is None:
            return {
                "status": "infra_error",
                "error": f"tester produced no report: {output[-1500:]}",
                "build": build,
                "app_log_tail": app_log[-8000:],
            }
        summary = report.get("summary") or _zero_summary()
        health_ok = any(c["name"] == "health responds 200" and c["passed"] for c in report.get("checks", []))
        if report.get("error"):
            status = "infra_error"
        elif not health_ok:
            status = "start_failed"
        else:
            status = "passed" if summary.get("failed") == 0 and summary.get("total") else "failed"
        return {
            "status": status,
            "build": build,
            "summary": summary,
            "checks": report.get("checks", []),
            "tester_image": tester_tag,
            "tester_image_id": tester_id,
            "app_exit_code": app_state.get("ExitCode") if app_state.get("Status") != "running" else None,
            "app_oom_killed": bool(app_state.get("OOMKilled")),
            "app_log_tail": app_log[-8000:],
            "resolved_python_deps": deps,
            "error": report.get("error"),
        }

    def _tester_timed_out(self, app, tester, plan, build, tester_tag, tester_id) -> dict[str, Any]:
        health_status = _probe_health(tester, plan)
        app.reload()
        app_state = app.attrs.get("State", {})
        app_log = app.logs(tail=200).decode("utf-8", errors="replace")
        common = {
            "build": build,
            "tester_image": tester_tag,
            "tester_image_id": tester_id,
            "tester_timed_out": True,
            "health_probe_status": health_status,
            "app_exit_code": app_state.get("ExitCode") if app_state.get("Status") != "running" else None,
            "app_oom_killed": bool(app_state.get("OOMKilled")),
            "app_log_tail": app_log[-8000:],
        }
        if health_status != 200:
            return {
                **common,
                "status": "infra_error",
                "error": f"tester timed out after {TESTER_TIMEOUT_S}s and the app's health could not be confirmed",
            }
        summary = {**_zero_summary(), "incomplete": True}
        return {
            **common,
            "status": "failed",
            "summary": summary,
            # The tester's own health check is not in a report; record the probe as that check.
            "checks": [
                {
                    "category": "runtime",
                    "name": "health responds 200",
                    "passed": True,
                    "detail": "platform probe after the tester timed out",
                },
            ],
            "error": (
                f"the conformance suite did not finish within {TESTER_TIMEOUT_S}s: "
                "the app answers its health check but too slowly elsewhere (no partial score)"
            ),
        }

    def _build(self) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        report = HealingReport()
        for _attempt in range(2):
            report = HealingReport()
            with tempfile.TemporaryDirectory(prefix="llmlab-eval-") as tmp:
                prepare_build_dir(self.job, Path(tmp), healing=self.healing, report=report)
                started = time.monotonic()
                ok, log, client_error = self._docker_build(tmp)
            infra = not ok and (client_error is not None or bool(_INFRA_PATTERNS.search(log)))
            attempt = {"ok": ok, "seconds": round(time.monotonic() - started, 1), "infra_suspected": infra}
            if client_error is not None:
                attempt["client_error"] = client_error[:1000]
            attempts.append(attempt)
            if ok or not infra:
                break
        image_bytes = None
        if ok:
            image_bytes = self.client.images.get(self.image_tag).attrs.get("Size")
        return {
            "ok": ok,
            "attempts": attempts,
            "infra_suspected": attempts[-1]["infra_suspected"],
            "log_tail": log[-6000:],
            "image_bytes": image_bytes,
            "healing": report.to_dict(),
        }

    def _docker_build(self, path: str) -> tuple[bool, str, str | None]:
        """``(ok, log, client_error)``: ``client_error`` is set when the docker client/daemon failed."""
        lines: list[str] = []
        ok = True
        client_error = None
        try:
            for chunk in self.client.api.build(
                path=path,
                tag=self.image_tag,
                rm=True,
                forcerm=True,
                decode=True,
                timeout=BUILD_TIMEOUT_S,
                labels=self.labels,
            ):
                if chunk.get("stream"):
                    lines.append(chunk["stream"])
                if chunk.get("error"):
                    lines.append("ERROR: " + chunk["error"] + "\n")
                    ok = False
        except requests.exceptions.ReadTimeout as exc:
            # A build step silent for BUILD_TIMEOUT_S: counted against the sample.
            lines.append(f"ERROR: build timed out: {exc}\n")
            ok = False
        except Exception as exc:
            lines.append(f"ERROR: {exc}\n")
            ok = False
            client_error = f"{type(exc).__name__}: {exc}"
        return ok, "".join(lines), client_error

    def _teardown(self) -> None:
        for container in self.containers:
            try:
                container.remove(force=True)
            except Exception as exc:
                logger.warning("Removing evaluation container failed: %s", exc)
        if self.network is not None:
            try:
                self.network.remove()
            except Exception as exc:
                logger.warning("Removing evaluation network failed: %s", exc)
        if self.client is not None:
            try:
                self.client.images.remove(self.image_tag, force=True)
            except Exception as exc:
                logger.debug("Removing evaluation image failed: %s", exc)


def _probe_health(tester, plan: dict[str, Any]) -> int | None:
    """HTTP status of the app's health endpoint, fetched from inside the (still running) tester container."""
    url = plan["base_url"].rstrip("/") + (plan.get("contract") or {}).get("health_path", "/health")
    script = (
        "import sys, urllib.request, urllib.error\n"
        "try:\n"
        f"    r = urllib.request.urlopen(sys.argv[1], timeout={HEALTH_PROBE_TIMEOUT_S})\n"
        "    print(r.status)\n"
        "except urllib.error.HTTPError as e:\n"
        "    print(e.code)\n"
        "except Exception:\n"
        "    print(0)\n"
    )
    try:
        code, out = tester.exec_run(["python", "-c", script, url], demux=False)
    except Exception as exc:
        logger.warning("Health probe after tester timeout failed: %s", exc)
        return None
    if code != 0:
        return None
    try:
        return int(out.decode("utf-8", errors="replace").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def _zero_summary() -> dict[str, Any]:
    return {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0.0, "by_category": {}}


def _parse_report(output: str) -> dict[str, Any] | None:
    for line in reversed(output.splitlines()):
        if line.startswith("LLMLAB-CONFORMANCE "):
            try:
                return json.loads(line[len("LLMLAB-CONFORMANCE ") :])
            except ValueError:
                return None
    return None


def _resolved_python_deps(container) -> list[str]:
    """What pip actually installed in the built image (unpinned requirements resolve at build time)."""
    try:
        code, out = container.exec_run(["python", "-m", "pip", "freeze", "--all"], demux=False)
    except Exception:
        return []
    if code != 0:
        return []
    return [line for line in out.decode("utf-8", errors="replace").splitlines() if "==" in line][:500]


def evaluate_job(
    job: GenerationJob,
    *,
    save: Callable[[GenerationJob, list[str]], None] | None = None,
) -> dict[str, Any]:
    """Evaluate *job* and store the outcome on it (``result_data.evaluation`` + ``metrics.functional``).

    ``save(job, fields)`` persists the result; the executor passes
    ``execution.transitions.guarded_save`` so a requeued or cancelled job is
    never written to (it raises ``LeaseLost``). Default: a plain ``save``.
    """
    outcome = Evaluation(job).run()
    # Only samples whose prompt stated this contract were asked for e.g. the
    # accounts endpoints: agent briefs v2+ (the snapshot freezes the contract)
    # and scaffolding prompts whose backend templates render it. Scores of
    # older template-mode prompts are not comparable with those.
    snapshot = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
    outcome["contract_stated_in_prompt"] = isinstance(snapshot.get("contract"), dict) or templates_state_contract(
        snapshot.get("prompt_templates"),
    )
    result = dict(job.result_data or {})
    result["evaluation"] = outcome
    job.result_data = result
    summary = outcome.get("summary") or {}
    checks = outcome.get("checks") or []
    endpoint_checks = [c for c in checks if c["category"] in ("api", "admin")]
    healing = (outcome.get("build") or {}).get("healing") or {}
    metrics = dict(job.metrics or {})
    metrics["functional"] = {
        "source": "conformance",
        "status": outcome["status"],
        "passed": outcome["status"] == "passed",
        # Non-sample outcomes (infra_error, not_applicable) are ungraded, never a measured 0.
        "pass_rate": summary.get("pass_rate", 0.0) if outcome["status"] not in NON_SAMPLE_STATUSES else None,
        "health_ok": any(c["name"] == "health responds 200" and c["passed"] for c in checks),
        "endpoints_ok": sum(1 for c in endpoint_checks if c["passed"]),
        "endpoints_checked": len(endpoint_checks),
        "evaluator_version": outcome.get("evaluator_version"),
        "tester_sha256": outcome.get("tester_sha256"),
        "contract_stated_in_prompt": outcome["contract_stated_in_prompt"],
        "deployed": is_deployed(outcome["status"]),
        "healing_mode": healing.get("mode"),
        "healing_changed": healing.get("changed"),
    }
    job.metrics = metrics
    if isinstance(result.get("agent"), dict):
        from backend.generation.services.agent.runner import assess_validity

        result["agent"]["validity"] = assess_validity(
            job.resolved_bundle or {},
            result,
            execution=job.execution if isinstance(job.execution, dict) else {},
            attempt=job.attempt,
        )
    if save is None:
        job.save(update_fields=["result_data", "metrics", "updated_at"])
    else:
        save(job, ["result_data", "metrics"])
    return outcome


# Outcomes where the app built and answered its health check.
_DEPLOYED_STATUSES = frozenset({"passed", "failed"})
# Outcomes that say nothing about the sample (re-evaluate instead of counting).
NON_SAMPLE_STATUSES = frozenset({"infra_error", "not_applicable"})


def is_deployed(status: str | None) -> bool | None:
    """True/False for a sample outcome, None when the evaluation says nothing about the sample."""
    if status is None or status in NON_SAMPLE_STATUSES:
        return None
    return status in _DEPLOYED_STATUSES


def evaluate_healing_ablation(
    job: GenerationJob,
    modes: tuple[str, ...] = (HealingMode.NONE,),
    *,
    save: Callable[[GenerationJob, list[str]], None] | None = None,
) -> dict[str, Any]:
    """Re-evaluate *job*'s unchanged output under each healing *mode* (paired ablation).

    The job's own evaluation stays untouched; every mode lands in
    ``result_data.healing_ablation[mode]`` so the deploy rate of the same
    model output can be compared with and without build repair.
    """
    ablation: dict[str, Any] = {}
    for mode in modes:
        outcome = Evaluation(job, healing=HealingMode(mode)).run()
        build = outcome.get("build") or {}
        summary = outcome.get("summary") or {}
        ablation[str(mode)] = {
            "status": outcome.get("status"),
            "deployed": is_deployed(outcome.get("status")),
            "pass_rate": summary.get("pass_rate") if outcome.get("status") not in NON_SAMPLE_STATUSES else None,
            "healing": build.get("healing") or {},
            "build_log_tail": (build.get("log_tail") or "")[-2000:],
            "error": outcome.get("error"),
            "evaluator_version": outcome.get("evaluator_version"),
            "tester_sha256": outcome.get("tester_sha256"),
            "seconds": outcome.get("seconds"),
        }
    result = dict(job.result_data or {})
    result["healing_ablation"] = {**(result.get("healing_ablation") or {}), **ablation}
    job.result_data = result
    if save is None:
        job.save(update_fields=["result_data", "updated_at"])
    else:
        save(job, ["result_data"])
    return ablation
