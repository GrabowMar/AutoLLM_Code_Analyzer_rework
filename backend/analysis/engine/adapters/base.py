"""Adapter contract (owner: toolchain stage).

The sample is mounted read-only at ``/src``; the container's cwd is ``/tmp``
(tmpfs) so tool caches never land in the scanned tree; ``/out`` is a writable
volume collected after the run. No network. Adapters must:

* select only the in-scope files of their languages (none -> NotApplicable),
* run tools with explicit, baked configs and isolation flags so an analysed
  app cannot configure or disable its own scanner,
* declare exit-code semantics and raise :class:`ToolFailure` on anything that
  is not a trustworthy result (crash, unparseable output, fatal errors) —
  never return an empty ToolParse for a failed tool,
* report coverage: files scanned and files the tool could not parse.

The helpers below implement the shared part of that contract. Every tool
gets its files as explicit paths (never "scan /src"), so files the sample
excludes are never scanned whatever the executor puts in the volume, and
paths in reports are mapped back to sample-relative paths.

A selected file a tool would silently pass over (a file type it cannot read,
a path its built-in defaults always exclude) is never claimed as scanned:
:meth:`ToolAdapter.unsupported` names the reason, the file is not handed to
the tool and coverage lists it as skipped. In-file markers a tool honours
without a switch to ignore them (``#lizard forgive``, ``jscpd:ignore-start``,
``# pylint: skip-file``...) are neutralized in staged copies
(:func:`staged_argv`), so an app cannot switch its scanner off.
"""

from __future__ import annotations

import json
import math
import shlex
from collections.abc import Iterable
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar

from backend.analysis.engine import catalog
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import Invocation
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import Sample
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import ToolFailure
from backend.analysis.engine.types import ToolParse

if TYPE_CHECKING:
    from backend.analysis.engine.catalog import ToolSpec

SRC = "/src"
OUT = "/out"
ANALYZER = "/opt/analyzer"
BIN = f"{ANALYZER}/bin"
CONFIG = f"{ANALYZER}/config"
SCRIPTS = f"{ANALYZER}/scripts"
VENVS = f"{ANALYZER}/venvs"

# Exit statuses produced by the container runtime, not by the tool.
EXIT_NOT_EXECUTABLE = 126
EXIT_NOT_FOUND = 127
EXIT_SIGNAL_BASE = 128
EXIT_SIGKILL = 137

TAIL_CHARS = 1500
# A sniff of the first bytes decides "text file" for tools that scan any text.
_TEXT_SNIFF_BYTES = 8192
# Staged copies of the selected files, and the empty directory a staged tool runs from (tmpfs; see
# staged_argv).
STAGE = "/tmp/llmlab-src"  # noqa: S108 - the tool container's private tmpfs
STAGE_CWD = "/tmp/llmlab-cwd"  # noqa: S108 - the tool container's private tmpfs


class ToolAdapter:
    slug: ClassVar[str] = ""

    @property
    def spec(self) -> ToolSpec:
        return catalog.get_spec(self.slug)

    def select_files(self, sample: Sample, config: dict[str, Any]) -> list[SampleFile]:
        """Included files this tool scans (default: spec languages x file kinds).
        An empty list means the executor records ``not_applicable``."""
        return self.applicable(select_files(sample, self.spec))

    def unsupported(self, file: SampleFile) -> str | None:
        """Why the tool cannot analyse *file* although it matches the spec (None: it can).

        Such a file stays selected, so coverage lists it as skipped with this
        reason, but it is never passed to the tool (which would pass over it
        silently). The default: every selected file is analysable.
        """
        return None

    def applicable(self, files: list[SampleFile]) -> list[SampleFile]:
        """*files*, or [] when the tool can read none of them (not applicable: never run on nothing)."""
        return files if any(self.unsupported(f) is None for f in files) else []

    def analysable(self, files: list[SampleFile]) -> list[SampleFile]:
        """The selected files that are handed to the tool."""
        return [f for f in files if self.unsupported(f) is None]

    def unsupported_coverage(self, files: list[SampleFile]) -> list[dict[str, Any]]:
        """``skipped`` coverage entries of the selected files the tool cannot analyse."""
        out = []
        for file in files:
            reason = self.unsupported(file)
            if reason:
                out.append({"path": file.path, "reason": reason})
        return out

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        raise NotImplementedError

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        """Raise ``ToolFailure`` unless the outcome is a trustworthy result."""
        raise NotImplementedError

    # -- helpers for subclasses ------------------------------------------------

    def make_invocation(
        self,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        inputs: dict[str, bytes] | None = None,
        report_files: Iterable[str] = (),
    ) -> Invocation:
        """An Invocation with the spec's resources (timeout, memory limit)."""
        spec = self.spec
        return Invocation(
            argv=[str(a) for a in argv],
            timeout_s=spec.timeout_s,
            mem_limit=spec.mem_limit,
            env=dict(env or {}),
            inputs=dict(inputs or {}),
            report_files=tuple(report_files),
        )

    def effective_config(self, config: dict[str, Any] | None) -> dict[str, Any]:
        """The frozen plan's config with defaults filled in, validated against the catalog.

        A plan frozen before an option existed still runs (the default applies);
        a value the catalog no longer accepts fails the tool as invalid_config.
        """
        try:
            return catalog.validate_tool_config(self.slug, config or {})
        except catalog.ConfigError as exc:
            raise ToolFailure(reason="invalid_config", message=f"{self.slug}: invalid config: {exc}") from exc


# --------------------------------------------------------------------------
# File selection
# --------------------------------------------------------------------------


def is_text(file: SampleFile) -> bool:
    """A file a text scanner may read (no NUL byte in its first 8 KiB)."""
    return b"\0" not in file.content[:_TEXT_SNIFF_BYTES]


def select_files(sample: Sample, spec: ToolSpec) -> list[SampleFile]:
    """Included files matching the spec's languages and file kinds, sorted by path.

    ``languages: ["*"]`` means any text file; ``file_kinds: ["*"]`` any kind.
    """
    languages = set(spec.languages)
    kinds = set(spec.file_kinds)
    selected = []
    for file in sample.files:
        if not file.included:
            continue
        if "*" not in kinds and file.kind not in kinds:
            continue
        if "*" in languages:
            if not is_text(file):
                continue
        elif file.language not in languages:
            continue
        selected.append(file)
    return sorted(selected, key=lambda f: f.path)


def src_path(file: SampleFile | str) -> str:
    """Absolute path of a sample file inside the container (never starts with '-')."""
    path = file.path if isinstance(file, SampleFile) else file
    return f"{SRC}/{path}"


def rel_path(path: str, *, root: str = SRC) -> str:
    """A tool's report path -> sample-relative posix path (strips ``/src/`` or ``./``)."""
    text = str(path).replace("\\", "/")
    prefix = root.rstrip("/") + "/"
    if text.startswith(prefix):
        text = text[len(prefix) :]
    while text.startswith("./"):
        text = text[2:]
    return text


def staged_argv(command: list[str], files: list[SampleFile], sed_expressions: list[str]) -> list[str]:
    """``sh -c`` argv that runs *command* on rewritten copies of *files*.

    The files are copied from /src into :data:`STAGE` (same relative paths, a
    fresh tmpfs directory), every copy is rewritten in place with the given
    ``sed -E`` expressions — which must keep line numbers, i.e. never add or
    remove a newline — and *command* (ending with ``--``) is exec'ed with the
    staged paths appended. Report paths start with ``STAGE``; map them back
    with ``rel_path(path, root=STAGE)``. The tool runs from an empty directory
    beside the stage, never from a parent of it: tools such as pylint print
    paths below their cwd relative to it.
    """
    seds = " ".join(f"-e {shlex.quote(expression)}" for expression in sed_expressions)
    script = (
        "set -eu; "
        f'mkdir {STAGE}; cd {SRC}; cp --parents -- "$@" {STAGE}/; '
        f'cd {STAGE}; sed -E -i {seds} -- "$@"; mkdir {STAGE_CWD}; cd {STAGE_CWD}; '
        f'n=$#; for f in "$@"; do set -- "$@" "{STAGE}/$f"; done; shift "$n"; '
        f'exec {shlex.join(command)} "$@"'
    )
    return ["sh", "-c", script, "stage", *(f.path for f in files)]


# --------------------------------------------------------------------------
# Outcome checks
# --------------------------------------------------------------------------


def tail(outcome: ExecOutcome, limit: int = TAIL_CHARS) -> str:
    """The end of stderr (or stdout when stderr is empty), for failure messages."""
    for stream in (outcome.stderr, outcome.stdout):
        text = (stream or b"").decode("utf-8", errors="replace").strip()
        if text:
            return text[-limit:]
    return "(no output)"


def check_outcome(outcome: ExecOutcome, tool: str) -> None:
    """Raise ToolFailure for anything that is not "the tool ran and exited on its own".

    Covers infrastructure errors, cancellation, timeouts, OOM kills, missing or
    non-executable binaries and death by signal; the executor checks some of
    these too, adapters re-check so ``interpret`` is safe on any outcome.
    """
    if outcome.infra_error:
        raise ToolFailure(
            reason="infra_error",
            message=f"{tool}: the container did not run properly: {outcome.infra_error}",
        )
    if outcome.cancelled:
        raise ToolFailure(reason="cancelled", message=f"{tool} was cancelled")
    if outcome.timed_out:
        raise ToolFailure(reason="timeout", message=f"{tool} exceeded its time limit ({outcome.wall_seconds:.0f}s)")
    if outcome.oom_killed:
        raise ToolFailure(reason="oom", message=f"{tool} was killed by the out-of-memory killer")
    code = outcome.exit_code
    if code is None:
        raise ToolFailure(reason="infra_error", message=f"{tool}: the container reported no exit status")
    if code == EXIT_SIGKILL:
        # SIGKILL without a timeout: in a memory-limited container this is the
        # OOM killer hitting a child process (the container's init survives).
        raise ToolFailure(
            reason="oom",
            message=f"{tool} was killed by SIGKILL (exit 137), most likely by the memory limit",
        )
    if code in (EXIT_NOT_EXECUTABLE, EXIT_NOT_FOUND):
        raise ToolFailure(reason="unavailable", message=f"{tool} could not be executed (exit {code}): {tail(outcome)}")
    if code > EXIT_SIGNAL_BASE:
        raise ToolFailure(
            reason="tool_error",
            message=f"{tool} was terminated by signal {code - EXIT_SIGNAL_BASE}: {tail(outcome)}",
        )


def check_exit(outcome: ExecOutcome, ok: Iterable[int], tool: str) -> int:
    """check_outcome + the tool's declared exit codes; returns the exit code."""
    check_outcome(outcome, tool)
    allowed = frozenset(ok)
    code = int(outcome.exit_code or 0)
    if code not in allowed:
        expected = ", ".join(str(c) for c in sorted(allowed))
        raise ToolFailure(
            reason="tool_error",
            message=f"{tool} exited with status {code} (expected {expected}): {tail(outcome)}",
        )
    return code


def stdout_of(outcome: ExecOutcome, tool: str, *, stderr: bool = False) -> bytes:
    """The complete stdout (or stderr) of a tool whose results are printed there.

    The sandbox caps captured output; a capped stream would silently lose
    findings (text formats) or break JSON, so it fails the tool instead.
    """
    truncated = outcome.stderr_truncated if stderr else outcome.stdout_truncated
    if truncated:
        stream = "stderr" if stderr else "stdout"
        raise ToolFailure(reason="parse_error", message=f"{tool}'s {stream} exceeded the capture limit (truncated)")
    return outcome.stderr if stderr else outcome.stdout


def read_report(outcome: ExecOutcome, name: str, tool: str) -> bytes:
    """A report file the tool must have written to /out (missing or empty -> ToolFailure)."""
    data = outcome.out_files.get(name)
    if data is None:
        raise ToolFailure(reason="tool_error", message=f"{tool} wrote no report {OUT}/{name}: {tail(outcome)}")
    if not data.strip():
        raise ToolFailure(reason="parse_error", message=f"{tool}'s report {OUT}/{name} is empty")
    return data


def load_json(data: bytes, *, what: str, expect: type | tuple[type, ...] = (dict, list)) -> Any:
    """Strict JSON: the whole document must parse and have the expected top-level type."""
    if not data or not data.strip():
        raise ToolFailure(reason="parse_error", message=f"{what} is empty")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolFailure(reason="parse_error", message=f"{what} is not UTF-8: {exc}") from exc
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolFailure(
            reason="parse_error",
            message=f"{what} is not valid JSON ({exc.msg} at line {exc.lineno})",
        ) from exc
    if not isinstance(value, expect):
        raise ToolFailure(reason="parse_error", message=f"{what} has an unexpected top-level {type(value).__name__}")
    return value


@contextmanager
def report_structure(what: str) -> Iterator[None]:
    """Turn a missing key / wrong type while reading a report into ToolFailure(parse_error)."""
    try:
        yield
    except ToolFailure:
        raise
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        raise ToolFailure(
            reason="parse_error",
            message=f"{what} has an unexpected structure: {type(exc).__name__}: {exc}",
        ) from exc


def as_int(value: Any) -> int | None:
    """An optional positive line/column number (tools use null, 0 or -1 for "none")."""
    if value is None or isinstance(value, bool):
        return None
    number = int(value)
    return number if number > 0 else None


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


def coverage(
    files: list[SampleFile],
    *,
    unparseable: Iterable[dict[str, Any]] = (),
    skipped: Iterable[dict[str, Any]] = (),
    partial: Iterable[dict[str, Any]] = (),
    scanned: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Coverage of one execution over the selected files.

    ``unparseable``: files the tool could not parse (counted in parse coverage);
    ``skipped``: files the tool did not analyse for another reason;
    ``partial``: files analysed only in part (e.g. a rule timed out).
    ``scanned`` (what the tool reports it read) defaults to all selected files;
    either way the result is restricted to selected files that were neither
    unparseable nor skipped.
    """
    selected = [f.path for f in files]
    bad = _dedupe(unparseable)
    left_out = [entry for entry in _dedupe(skipped) if entry["path"] not in {b["path"] for b in bad}]
    excluded = {e["path"] for e in bad} | {e["path"] for e in left_out}
    # Only selected files count, whatever a report claims to have scanned.
    pool = set(scanned) if scanned is not None else set(selected)
    scanned_paths = [p for p in selected if p in pool and p not in excluded]
    return {
        "files_selected": len(selected),
        "files_scanned": len(scanned_paths),
        "scanned": scanned_paths,
        "unparseable": bad,
        "skipped": left_out,
        "partial": _dedupe(partial),
    }


def _dedupe(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for entry in entries:
        path = str(entry.get("path") or "")
        if path and path not in seen:
            seen[path] = {"path": path, "reason": " ".join(str(entry.get("reason") or "").split())[:500]}
    return [seen[p] for p in sorted(seen)]


def build_parse(
    tool: str,
    files: list[SampleFile],
    findings: list[RawFinding],
    cov: dict[str, Any],
    *,
    metrics: dict[str, Any] | None = None,
    notes: Iterable[str] = (),
) -> ToolParse:
    """The ToolParse of a trustworthy execution.

    A tool that analysed none of its selected files did not produce a
    measurement (every file unparseable or skipped): that is a failure, not
    "0 findings".
    """
    if files and cov["files_scanned"] == 0:
        reasons = "; ".join(f"{e['path']}: {e['reason']}" for e in (cov["unparseable"] + cov["skipped"])[:5])
        raise ToolFailure(
            reason="tool_error",
            message=f"{tool} could not analyse any of the {len(files)} selected files ({reasons})",
        )
    out_notes = list(notes)
    if cov["unparseable"]:
        out_notes.append(f"{len(cov['unparseable'])} of {len(files)} files could not be parsed")
    if cov["skipped"]:
        out_notes.append(f"{len(cov['skipped'])} of {len(files)} files were skipped")
    return ToolParse(findings=findings, metrics=dict(metrics or {}), coverage=cov, notes=out_notes)


def complexity_summary(values: list[float]) -> dict[str, Any]:
    """{functions, max, mean, p90, rank_distribution} with radon's rank bands (A 1-5 ... F 41+).

    p90 uses linear interpolation between closest ranks (numpy's default), so
    it is defined for any non-empty list. Empty -> None statistics, never 0.
    """
    ordered = sorted(values)
    distribution = dict.fromkeys("ABCDEF", 0)
    for value in ordered:
        distribution[cc_rank(value)] += 1
    if not ordered:
        return {"functions": 0, "max": None, "mean": None, "p90": None, "rank_distribution": distribution}
    return {
        "functions": len(ordered),
        "max": ordered[-1],
        "mean": round(sum(ordered) / len(ordered), 4),
        "p90": round(percentile(ordered, 90), 4),
        "rank_distribution": distribution,
    }


def percentile(ordered: list[float], pct: float) -> float:
    """Linear-interpolation percentile of an already sorted, non-empty list."""
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * pct / 100
    low, high = math.floor(position), math.ceil(position)
    return float(ordered[low] + (ordered[high] - ordered[low]) * (position - low))


def cc_rank(value: float) -> str:
    """radon's cyclomatic-complexity rank letter."""
    for rank, upper in (("A", 5), ("B", 10), ("C", 20), ("D", 30), ("E", 40)):
        if value <= upper:
            return rank
    return "F"
