"""Run ad-hoc Semgrep rule packs over in-memory files (used by pattern detectors, spec section 7.9 step 3).

Owned by WP-13. There is no pattern-tools image: rules run through the analyzer workspace's existing
Semgrep install (``backend`` ``workspace``, the default) or, for development machines and CI images that
ship Semgrep, a local ``semgrep`` binary (``local``). The backend is ``settings.PATTERNS_SEMGREP_BACKEND``.

``run_semgrep_rules(files, rules)`` writes the files and a ``rules.yaml`` into a private scratch directory
(never the workspace's ``/work``, which analysis runs own), runs ``semgrep scan --json`` once and returns a
:class:`SemgrepRun`: Semgrep's own ``results`` and ``errors`` with paths relative to the given files, the
list of scanned paths, the exit code and the tool version.

Validity: this module never decides "absent". Semgrep exits 0 on parse errors and reports them in
``errors[]``, so callers must treat an error touching a file, a non-zero exit, a missing report or a file
that was not scanned as an error for that file (:meth:`SemgrepRun.file_error`). A run that could not
execute at all has ``error`` set and every file errs.
"""

from __future__ import annotations

import json
import logging
import shlex
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

import yaml
from django.conf import settings

if TYPE_CHECKING:
    from collections.abc import Mapping
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

SRC_DIR = "src"
SCRATCH_ROOT = "/tmp/llmlab-rulepack"  # noqa: S108 - inside the analyzer container (private per run)
DEFAULT_TIMEOUT_S = 300
SEMGREP_TOOL_SLUG = "semgrep"
# semgrep exits 0 with or without findings (no --error flag); anything else is a failure.
OK_EXIT_CODES = frozenset({0})


class RulepackError(RuntimeError):
    """The rule pack could not run (no workspace, Semgrep not installed, no parsable report)."""


@dataclass(frozen=True)
class RawOutput:
    """What a runner returns: Semgrep's exit code, its stdout (the JSON report) and diagnostics."""

    exit_code: int | None
    stdout: str
    stderr: str = ""
    version: str = ""


class SemgrepRunner(Protocol):
    """``(files, config_yaml, *, timeout_s) -> RawOutput``; raises :class:`RulepackError` when it cannot run."""

    name: str

    def __call__(self, files: Mapping[str, str], config_yaml: str, *, timeout_s: int) -> RawOutput: ...


@dataclass
class SemgrepRun:
    """Semgrep's report over the given files; ``error`` is set when the run itself failed."""

    results: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    scanned: list[str] | None = None
    exit_code: int | None = None
    version: str = ""
    error: str = ""
    runner: str = ""

    def file_error(self, path: str) -> str:
        """Why ``path`` cannot be trusted as measured (``""`` when it can)."""
        if self.error:
            return self.error
        if self.exit_code not in OK_EXIT_CODES:
            return f"semgrep exited with {self.exit_code}"
        for err in self.errors:
            if _error_touches(err, path):
                return _error_text(err)
        if self.scanned is not None and path not in self.scanned:
            return "file was not scanned by semgrep"
        return ""

    def results_for(self, path: str) -> list[dict[str, Any]]:
        return [r for r in self.results if r.get("path") == path]


def _error_text(err: dict[str, Any]) -> str:
    kind = err.get("type")
    if isinstance(kind, list):
        kind = kind[0] if kind else ""
    message = str(err.get("message") or err.get("long_msg") or err.get("short_msg") or "")
    return f"semgrep {kind or 'error'}: {message}"[:500]


def _error_touches(err: dict[str, Any], path: str) -> bool:
    """An error with no path (rule or config errors) touches every file."""
    err_path = err.get("path")
    if not err_path:
        spans = err.get("spans") or []
        paths = {s.get("file") for s in spans if isinstance(s, dict) and s.get("file")}
        return not paths or path in paths
    return err_path == path


def _strip(path: str) -> str:
    for prefix in (f"{SRC_DIR}/", f"./{SRC_DIR}/"):
        if path.startswith(prefix):
            return path[len(prefix) :]
    marker = f"/{SRC_DIR}/"
    if marker in path:
        return path.split(marker, 1)[1]
    return path


def _safe_rel(path: str) -> str:
    parts = Path(path).parts
    if not path or Path(path).is_absolute() or any(part in {"..", ""} for part in parts) or "\\" in path:
        msg = f"unsafe path for a rule pack file: {path!r}"
        raise ValueError(msg)
    return path


def render_rules(rules: Sequence[Mapping[str, Any]] | str) -> str:
    """A Semgrep config YAML from rule dicts (or a YAML string with a top-level ``rules`` list)."""
    if isinstance(rules, str):
        data = yaml.safe_load(rules)
        if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
            msg = "a semgrep config must be a mapping with a 'rules' list"
            raise ValueError(msg)  # noqa: TRY004 - a content error, reported as ValueError
        return rules
    return yaml.safe_dump({"rules": [dict(rule) for rule in rules]}, sort_keys=False)


def parse_report(raw: RawOutput, runner: str = "") -> SemgrepRun:
    """Turn a runner's output into a :class:`SemgrepRun` (an unparsable report is an ``error``)."""
    run = SemgrepRun(exit_code=raw.exit_code, version=raw.version, runner=runner)
    try:
        data = json.loads(raw.stdout) if raw.stdout.strip() else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        tail = (raw.stderr or raw.stdout or "").strip()[-300:]
        run.error = f"semgrep produced no JSON report (exit {raw.exit_code}): {tail or '(no output)'}"
        return run
    for raw_result in data.get("results") or []:
        if isinstance(raw_result, dict):
            result = dict(raw_result)
            result["path"] = _strip(str(result.get("path", "")))
            run.results.append(result)
    for raw_err in data.get("errors") or []:
        if isinstance(raw_err, dict):
            err = dict(raw_err)
            if err.get("path"):
                err["path"] = _strip(str(err["path"]))
            for span in err.get("spans") or []:
                if isinstance(span, dict) and span.get("file"):
                    span["file"] = _strip(str(span["file"]))
            run.errors.append(err)
    paths = data.get("paths")
    if isinstance(paths, dict) and isinstance(paths.get("scanned"), list):
        run.scanned = [_strip(str(p)) for p in paths["scanned"]]
    if not run.version and isinstance(data.get("version"), str):
        run.version = data["version"]
    return run


def _command(timeout_s: int) -> str:
    inner = max(30, timeout_s - 10)
    return (
        "semgrep scan --config rules.yaml --json --metrics=off --disable-version-check --no-git-ignore "
        f"--timeout {inner} --quiet {SRC_DIR}"
    )


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------


class LocalRunner:
    """Runs a local ``semgrep`` binary in a temporary directory."""

    name = "local"

    def __init__(self, binary: str | None = None) -> None:
        self.binary = binary or shutil.which("semgrep") or ""

    def __call__(self, files: Mapping[str, str], config_yaml: str, *, timeout_s: int) -> RawOutput:
        if not self.binary:
            msg = "semgrep is not installed on this host"
            raise RulepackError(msg)
        with tempfile.TemporaryDirectory(prefix="llmlab-rulepack-") as tmp:
            root = Path(tmp)
            (root / "rules.yaml").write_text(config_yaml, encoding="utf-8")
            for rel, text in files.items():
                target = root / SRC_DIR / _safe_rel(rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            argv = shlex.split(_command(timeout_s))
            argv[0] = self.binary
            try:
                proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
                    argv,
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                    check=False,
                )
                version = subprocess.run(  # noqa: S603
                    [self.binary, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                ).stdout.strip()
            except subprocess.TimeoutExpired as exc:
                msg = f"semgrep timed out after {timeout_s}s"
                raise RulepackError(msg) from exc
        return RawOutput(exit_code=proc.returncode, stdout=proc.stdout, stderr=proc.stderr, version=version)


class WorkspaceRunner:
    """Runs Semgrep inside the user's analyzer workspace container (scratch dir under ``/tmp``)."""

    name = "workspace"

    def __init__(self, user: Any) -> None:
        self.user = user

    def _container(self) -> str:
        from backend.analysis.models import InstalledTool
        from backend.analysis.services import workspace_service

        if self.user is None:
            msg = "running semgrep in the analyzer workspace needs a user (the workspace owner)"
            raise RulepackError(msg)
        workspace = workspace_service.get_workspace(self.user)
        installed = InstalledTool.objects.filter(
            workspace=workspace,
            tool__slug=SEMGREP_TOOL_SLUG,
            status=InstalledTool.Status.INSTALLED,
        ).exists()
        if not installed:
            msg = "semgrep is not installed in the analyzer workspace (install it from the tool shop)"
            raise RulepackError(msg)
        try:
            return workspace_service.require_ready_container(workspace)
        except RuntimeError as exc:
            raise RulepackError(str(exc)) from exc

    def __call__(self, files: Mapping[str, str], config_yaml: str, *, timeout_s: int) -> RawOutput:
        from backend.analysis.services import workspace_service
        from backend.runtime.services import docker_manager

        container = self._container()
        scratch = f"{SCRATCH_ROOT}/{uuid.uuid4().hex}"
        payload = {"rules.yaml": config_yaml, **{f"{SRC_DIR}/{_safe_rel(p)}": t for p, t in files.items()}}
        copied = docker_manager.copy_files_in(container, payload, dest=scratch, owner=workspace_service.WORKSPACE_UID)
        if "error" in copied:
            msg = f"could not copy the rule pack into the workspace: {copied['error']}"
            raise RulepackError(msg)
        try:
            ran = docker_manager.exec_in(
                container,
                f'cd {scratch} && {_command(timeout_s)} > out.json 2> err.txt; echo "__rc=$?"',
                timeout_s=timeout_s,
            )
            if ran.get("exit_code") == -1:
                msg = f"semgrep could not be started: {ran.get('error')}"
                raise RulepackError(msg)
            if ran.get("exit_code") == 137:
                msg = f"semgrep was killed after {timeout_s}s (timeout) or ran out of memory"
                raise RulepackError(msg)
            output = ran.get("output") or ""
            exit_code: int | None = None
            for line in reversed(output.splitlines()):
                if line.startswith("__rc="):
                    exit_code = int(line.removeprefix("__rc=").strip() or -1)
                    break
            report = docker_manager.exec_in(container, f"cat {scratch}/out.json", timeout_s=60)
            stderr = docker_manager.exec_in(container, f"tail -c 2000 {scratch}/err.txt", timeout_s=30)
            version = docker_manager.exec_in(container, "semgrep --version", timeout_s=60)
        finally:
            docker_manager.exec_in(container, f"rm -rf {scratch}", timeout_s=30)
        return RawOutput(
            exit_code=exit_code,
            stdout=report.get("output") or "",
            stderr=stderr.get("output") or "",
            version=(version.get("output") or "").strip().splitlines()[-1:][0] if version.get("output") else "",
        )


def default_runner(user: Any = None) -> SemgrepRunner:
    """The runner ``settings.PATTERNS_SEMGREP_BACKEND`` names (``workspace`` or ``local``)."""
    backend = getattr(settings, "PATTERNS_SEMGREP_BACKEND", "workspace")
    if backend == "local":
        return LocalRunner()
    if backend == "workspace":
        return WorkspaceRunner(user)
    msg = f"unknown PATTERNS_SEMGREP_BACKEND {backend!r} (workspace | local)"
    raise RulepackError(msg)


def run_semgrep_rules(
    files: Mapping[str, str],
    rules: Sequence[Mapping[str, Any]] | str,
    *,
    user: Any = None,
    timeout_s: int | None = None,
    runner: SemgrepRunner | None = None,
) -> SemgrepRun:
    """Run ``rules`` once over ``files`` (``{relative path: text}``) and return Semgrep's report.

    Never raises for tool problems: a run that cannot execute returns ``SemgrepRun(error=...)``, which
    makes every file an error for the caller.
    """
    timeout = int(timeout_s or getattr(settings, "PATTERNS_SEMGREP_TIMEOUT_S", DEFAULT_TIMEOUT_S))
    try:
        config_yaml = render_rules(rules)
        chosen = runner or default_runner(user)
    except (ValueError, yaml.YAMLError, RulepackError) as exc:
        return SemgrepRun(error=f"rule pack invalid: {exc}"[:500])
    if not files:
        return SemgrepRun(exit_code=0, scanned=[], runner=chosen.name)
    try:
        raw = chosen(files, config_yaml, timeout_s=timeout)
    except (RulepackError, ValueError) as exc:
        return SemgrepRun(error=str(exc)[:500], runner=chosen.name)
    except Exception as exc:
        logger.exception("semgrep rule pack failed")
        return SemgrepRun(error=f"semgrep failed: {exc}"[:500], runner=chosen.name)
    return parse_report(raw, runner=chosen.name)
