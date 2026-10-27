"""Parsers that turn raw tool output into normalized findings and metrics.

Each parser maps a tool's JSON (or text) output to ``list[FindingData]`` or,
for tools whose output is (partly) metric-shaped, a ``ParsedOutput`` carrying
aggregate metrics alongside any findings. Parsers are looked up by the
``parser_key`` declared on a catalog tool.
"""

from __future__ import annotations

import itertools
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from backend.analysis.services.base import FindingData
from backend.analysis.services.base import ParsedOutput

logger = logging.getLogger(__name__)

_PARSERS: dict[str, Callable[[str], list[FindingData] | ParsedOutput]] = {}
# Parsers of ``### <manifest>``-sectioned output (one report per manifest).
_SECTIONED: set[str] = set()

_SECTION_RE = re.compile(r"^### (.+)$", re.MULTILINE)
# Lines a sectioned run command prints when a manifest's tool run failed.
_FAILURE_LINE_PREFIX = "!! "


def register(key: str, *, sectioned: bool = False) -> Callable[[Callable], Callable]:
    def _wrap(fn: Callable[[str], list[FindingData] | ParsedOutput]) -> Callable:
        _PARSERS[key] = fn
        if sectioned:
            _SECTIONED.add(key)
        return fn

    return _wrap


def parse(parser_key: str, raw_output: str) -> ParsedOutput:
    """Run the parser registered for *parser_key* on *raw_output*."""
    fn = _PARSERS.get(parser_key)
    if fn is None:
        logger.warning("No parser registered for key %r", parser_key)
        return ParsedOutput(error=f"No parser registered for {parser_key!r}")
    try:
        result = fn(raw_output or "")
    except Exception as exc:
        logger.exception("Parser %r failed", parser_key)
        # A crashed parser measured nothing; never let it pass as a clean result.
        return ParsedOutput(error=f"Could not parse the tool output ({type(exc).__name__}: {exc})"[:500])
    parsed = result if isinstance(result, ParsedOutput) else ParsedOutput(findings=result)
    if parser_key in _SECTIONED:
        _check_sections(raw_output or "", parsed)
    return parsed


def section_bodies(raw: str) -> list[tuple[str, str]]:
    """``[(path, body)]`` for each ``### <path>`` section of *raw*."""
    parts = _SECTION_RE.split(raw or "")
    # split() yields [preamble, path1, body1, path2, body2, ...]
    return [(path.strip(), body) for path, body in zip(parts[1::2], parts[2::2], strict=False)]


def failure_lines(body: str) -> list[str]:
    """The ``!! `` diagnostic lines a run command printed for a failed manifest."""
    return [
        line[len(_FAILURE_LINE_PREFIX) :].strip()
        for line in body.splitlines()
        if line.startswith(_FAILURE_LINE_PREFIX)
    ]


def _check_sections(raw: str, parsed: ParsedOutput) -> None:
    """Flag manifests whose section holds no report: they were not analyzed.

    Multi-manifest tools (pip-audit, npm audit, deptry, depcheck) run once
    per manifest; a run that fails (an unresolvable pin, a package missing
    from the registry) leaves an empty section. Counting that as "no
    vulnerabilities" would make broken apps look clean.

    Each unanalyzed manifest is a coverage gap in ``warnings``; when none was
    analyzed, ``error`` is set too (the result is ``failed``: not a
    measurement). Findings the parser derived from the failure itself
    (``nonexistent-package``/``-version``) are kept either way: the runner
    stores them on the failed result.
    """
    sections = section_bodies(raw)
    failed = []
    for path, body in sections:
        if _loads(body) is None:
            reason = "; ".join(failure_lines(body))[:300]
            failed.append(f"{path}: {reason}" if reason else path)
    if not failed:
        return
    parsed.warnings.append("Not analyzed: " + " | ".join(failed))
    if len(failed) == len(sections):
        parsed.error = "No manifest could be analyzed — " + " | ".join(failed)


def has_parser(parser_key: str) -> bool:
    return parser_key in _PARSERS


# Upper bound on decode attempts in ``_loads`` (each opener is one attempt).
_MAX_JSON_ATTEMPTS = 200
_OPENER_RE = re.compile(r"[\[{]")
_LINE_OPENER_RE = re.compile(r"^[ \t]*[\[{]", re.MULTILINE)


def _loads(raw: str) -> Any:
    """The JSON report in *raw*, or None when it holds none.

    ``exec_in`` merges stdout and stderr, so the report may be preceded or
    followed by log lines ("WARNING ..."). The report is the first JSON value
    that decodes, tried at the openers that start a line first (a bracket in
    a log line, ``[INFO]``, must not win), then at any opener; text after it
    is ignored.
    """
    text = (raw or "").strip()
    if not text:
        return None
    decoder = json.JSONDecoder()
    tried: set[int] = set()
    candidates = itertools.chain(
        (m.end() - 1 for m in _LINE_OPENER_RE.finditer(text)),
        (m.start() for m in _OPENER_RE.finditer(text)),
    )
    for idx in candidates:
        if idx in tried:
            continue
        if len(tried) >= _MAX_JSON_ATTEMPTS:
            break
        tried.add(idx)
        try:
            return decoder.raw_decode(text, idx)[0]
        except json.JSONDecodeError:
            continue
    return None


class NoJSONReportError(ValueError):
    """The tool printed output, but no JSON report in it."""

    def __init__(self, text: str) -> None:
        tail = ("…" + text[-300:]) if len(text) > 300 else text
        super().__init__(f"no JSON report in the tool output: {tail}")


def _json(raw: str, empty: Any) -> Any:
    """The JSON report in *raw*; *empty* when the tool printed nothing.

    Output without a decodable report (a crash banner, a truncated report)
    raises: ``parse`` turns that into an error, so the result is recorded as
    failed instead of as a clean, zero-finding one.
    """
    data = _loads(raw)
    if data is not None:
        return data
    text = (raw or "").strip()
    if not text:
        return empty
    raise NoJSONReportError(text)


def _clip_list(items: list[str], limit: int = 10) -> str:
    """``"a; b; c (+N more)"`` for warning messages."""
    shown = "; ".join(items[:limit])
    return f"{shown} (+{len(items) - limit} more)" if len(items) > limit else shown


# --- Shared helpers ---------------------------------------------------------
#
# ``FindingData.tool_severity`` and ``tool_specific_data["tool_confidence"]``
# hold the tool's own labels verbatim; ``severity``/``confidence`` are our
# normalization. ``tool_specific_data`` uses a shared key vocabulary the UI
# renders generically (rule_name, docs_url, references, cwe, owasp, end_line,
# end_column, fix, package, installed_version, fixed_versions, vuln_id,
# aliases, cvss_score, cvss_vector, manifest, url, method, param, evidence,
# attack, instances, other_info, secret_type, complexity, function, nloc,
# duplicate_of, lines, tokens) and only carries keys with a real value.
# ``aliases`` lists every id the advisory is known by (PYSEC/GHSA/CVE/vendor
# ids), ``vuln_id`` included, so cross-tool dedup can cluster on any of them.


# Finding.tool_severity is a CharField(max_length=40).
_LABEL_MAX = 40


def _label(value: Any) -> str:
    """A severity/confidence label exactly as the tool printed it; "" when it gave none."""
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    return str(value).strip()[:_LABEL_MAX]


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    """*data* without the keys that hold no value (None, "", [], {}); 0 and False are values."""
    return {k: v for k, v in data.items() if v is not None and not (isinstance(v, (str, list, dict)) and not v)}


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def _urls(*values: Any, limit: int = 20) -> list[str]:
    """Distinct URLs found in *values* (strings or lists of strings), in order."""
    out: list[str] = []
    for value in values:
        for item in value if isinstance(value, list) else [value]:
            for found in _URL_RE.findall(str(item or "")):
                url = found.rstrip(".,;)")
                if url not in out:
                    out.append(url)
    return out[:limit]


def _advisory_ids(*values: Any) -> list[str]:
    """Distinct advisory ids in *values* (strings or lists of strings), in order."""
    out: list[str] = []
    for value in values:
        for item in value if isinstance(value, (list, tuple)) else [value]:
            text = str(item or "").strip()
            if text and text not in out:
                out.append(text)
    return out


def _clip(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


_CWE_RE = re.compile(r"CWE-?(\d+)", re.IGNORECASE)


def _cwe_field(value: Any) -> dict[str, str]:
    """``{"cwe": "CWE-<n>"}`` from an int, a "CWE-89: ..." string or a list of them; {} if none.

    The first CWE wins — tools list the most specific one first. Cross-tool
    deduplication (statistics.services.dedup) matches findings on it.
    """
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, int) and value > 0:
        return {"cwe": f"CWE-{value}"}
    if isinstance(value, str):
        m = _CWE_RE.search(value)
        if m:
            return {"cwe": f"CWE-{int(m.group(1))}"}
    return {}


@register("bandit")
def parse_bandit(raw: str) -> ParsedOutput:
    """``bandit -f json``: ``results[]``; files it could not parse are in ``errors[]``."""
    data = _json(raw, {})
    if not isinstance(data, dict):
        return ParsedOutput(error="Unexpected bandit report (not a JSON object)")
    sev_map = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}
    conf_map = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}
    findings: list[FindingData] = []
    for item in data.get("results", []):
        line_range = [n for n in item.get("line_range") or [] if isinstance(n, int)]
        findings.append(
            FindingData(
                severity=sev_map.get(item.get("issue_severity", "LOW"), "low"),
                category="security",
                title=item.get("issue_text", "Security issue"),
                description=item.get("issue_text", ""),
                file_path=item.get("filename", ""),
                line_number=item.get("line_number"),
                code_snippet=(item.get("code") or "")[:500],
                rule_id=item.get("test_id", ""),
                confidence=conf_map.get(item.get("issue_confidence", "MEDIUM"), "medium"),
                tool_severity=_label(item.get("issue_severity")),
                tool_specific_data=_compact(
                    {
                        "rule_name": item.get("test_name"),
                        "test_name": item.get("test_name", ""),  # legacy key
                        "tool_confidence": _label(item.get("issue_confidence")),
                        "docs_url": item.get("more_info"),
                        "end_line": max(line_range) if line_range else None,
                        **_cwe_field((item.get("issue_cwe") or {}).get("id")),
                    },
                ),
            ),
        )
    parsed = ParsedOutput(findings=findings)
    errors = [e for e in data.get("errors") or [] if isinstance(e, dict)]
    if errors:
        failed = sorted({str(e.get("filename") or "?") for e in errors})
        details = [f"{e.get('filename') or '?'}: {e.get('reason') or 'error'}" for e in errors]
        parsed.warnings.append(f"Not scanned ({len(failed)} file(s)): {_clip_list(details)}")
        # ``metrics`` has one entry per file bandit opened (plus ``_totals``).
        opened = {k for k in (data.get("metrics") or {}) if k != "_totals"}
        if not findings and (not opened or opened <= set(failed)):
            parsed.error = f"Bandit could not scan any file — {_clip_list(details, 5)}"
    return parsed


def _pyflakes_severity(code: str) -> str:
    """Severity of a pycodestyle/pyflakes code (shared by flake8 and Ruff)."""
    # E9: syntax/IO errors; F63/F7/F82: invalid comparisons, misplaced
    # statements, undefined names: all crash or misbehave at runtime.
    if code.startswith(("E9", "F63", "F7", "F82")):
        return "high"
    if code in ("F401", "F841"):  # unused import / variable: dead code, harmless
        return "low"
    if code.startswith("F"):
        return "medium"
    return "low"


@register("ruff")
def parse_ruff(raw: str) -> list[FindingData]:
    data = _json(raw, [])
    findings: list[FindingData] = []
    for item in data:
        loc = item.get("location") or {}
        end = item.get("end_location") or {}
        code = item.get("code") or ""
        url = str(item.get("url") or "")
        findings.append(
            FindingData(
                # Ruff's codes are flake8's: rate them the same way so the
                # two tools' severities stay comparable. Ruff itself rates
                # nothing, so ``tool_severity`` stays empty.
                severity=_pyflakes_severity(code),
                category="quality",
                title=item.get("message", "Lint issue"),
                description=item.get("message", ""),
                file_path=item.get("filename", ""),
                line_number=loc.get("row"),
                column_number=loc.get("column"),
                rule_id=item.get("code") or "",
                confidence="high",
                tool_specific_data=_compact(
                    {
                        # The rule doc URL ends in the rule's name (".../rules/unused-import").
                        "rule_name": url.rstrip("/").rsplit("/", 1)[-1] if "/rules/" in url else None,
                        "docs_url": url,
                        "fix": (item.get("fix") or {}).get("message"),
                        "end_line": end.get("row"),
                        "end_column": end.get("column"),
                    },
                ),
            ),
        )
    return findings


# What the ESLint CLI prints for a message's numeric severity.
_ESLINT_LEVELS = {1: "warning", 2: "error"}


def _eslint_docs_url(rule_id: str) -> str:
    """The rule page of a core ESLint rule; plugin rules (``plugin/rule``) have none we can vouch for."""
    if rule_id and "/" not in rule_id and re.fullmatch(r"[a-z][a-z0-9-]*", rule_id):
        return f"https://eslint.org/docs/latest/rules/{rule_id}"
    return ""


def _eslint_fatal(msg: dict[str, Any]) -> bool:
    """A message saying ESLint could not parse the file ("Parsing error: ..."), not a rule hit."""
    return bool(msg.get("fatal")) or (
        not msg.get("ruleId") and str(msg.get("message") or "").startswith("Parsing error")
    )


@register("eslint")
def parse_eslint(raw: str) -> ParsedOutput:
    """``eslint -f json``: one entry per linted file.

    A fatal message means the file could not be parsed, so no rule ran on
    it: that is a coverage gap (``warnings``), not a finding. When no file
    could be parsed the result is an error.
    """
    data = _json(raw, [])
    if not isinstance(data, list):
        return ParsedOutput(error="Unexpected ESLint report (not a JSON list)")
    sev_map = {2: "high", 1: "low"}
    findings: list[FindingData] = []
    not_parsed: list[str] = []
    for file_entry in data:
        file_path = file_entry.get("filePath", "")
        messages = file_entry.get("messages") or []
        fatal = [m for m in messages if _eslint_fatal(m)]
        if fatal:
            not_parsed.append(f"{file_path}:{fatal[0].get('line', '?')}: {fatal[0].get('message', '')}"[:300])
        for msg in messages:
            if _eslint_fatal(msg):
                continue
            rule_id = msg.get("ruleId") or ""
            suggestions = [
                s.get("desc") for s in msg.get("suggestions") or [] if isinstance(s, dict) and s.get("desc")
            ]
            findings.append(
                FindingData(
                    severity=sev_map.get(msg.get("severity", 1), "low"),
                    category="quality",
                    title=msg.get("message", "Lint issue"),
                    description=msg.get("message", ""),
                    file_path=file_path,
                    line_number=msg.get("line"),
                    column_number=msg.get("column"),
                    rule_id=rule_id,
                    confidence="high",
                    tool_severity=_ESLINT_LEVELS.get(msg.get("severity"), ""),
                    tool_specific_data=_compact(
                        {
                            "docs_url": _eslint_docs_url(rule_id),
                            "fix": "; ".join(suggestions),
                            "end_line": msg.get("endLine"),
                            "end_column": msg.get("endColumn"),
                        },
                    ),
                ),
            )
    parsed = ParsedOutput(findings=findings)
    if not_parsed:
        parsed.warnings.append(f"Not parsed ({len(not_parsed)} of {len(data)} file(s)): {_clip_list(not_parsed)}")
        if len(not_parsed) == len(data):
            parsed.error = f"ESLint could not parse any file — {_clip_list(not_parsed, 3)}"
    return parsed


_SEMGREP_CATEGORIES = {
    "security": "security",
    "correctness": "quality",
    "best-practice": "best_practice",
    "maintainability": "quality",
    "performance": "performance",
    "portability": "quality",
}


@register("semgrep")
def parse_semgrep(raw: str) -> ParsedOutput:
    """``semgrep --json``: ``results[]``, plus ``errors[]`` and ``paths`` for what was not (fully) analyzed."""
    data = _json(raw, {})
    if not isinstance(data, dict):
        return ParsedOutput(error="Unexpected Semgrep report (not a JSON object)")
    sev_map = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}
    findings: list[FindingData] = []
    for item in data.get("results", []):
        extra = item.get("extra") or {}
        start = item.get("start") or {}
        end = item.get("end") or {}
        message = extra.get("message") or item.get("check_id", "Semgrep finding")
        metadata = extra.get("metadata") or {}
        # The registry packs mix security rules with correctness/best-practice
        # ones; only the former belong in security counts.
        rule_category = str(metadata.get("category") or "security").lower()
        check_id = item.get("check_id", "")
        lines = extra.get("lines") or ""
        owasp = metadata.get("owasp") or []
        findings.append(
            FindingData(
                severity=sev_map.get(str(extra.get("severity", "INFO")).upper(), "low"),
                category=_SEMGREP_CATEGORIES.get(rule_category, "quality"),
                title=(message.splitlines() or ["Semgrep finding"])[0][:200],
                description=message,
                file_path=item.get("path", ""),
                line_number=start.get("line"),
                column_number=start.get("col"),
                # Semgrep OSS without a login prints this placeholder instead of the code.
                code_snippet="" if lines.strip() == _SEMGREP_NO_LINES else lines[:500],
                rule_id=check_id,
                confidence=str((extra.get("metadata") or {}).get("confidence", "medium")).lower() or "medium",
                tool_severity=_label(extra.get("severity")),
                tool_specific_data=_compact(
                    {
                        "rule_name": check_id.rsplit(".", 1)[-1] if check_id else None,
                        "tool_confidence": _label(metadata.get("confidence")),
                        "likelihood": _label(metadata.get("likelihood")),
                        "impact": _label(metadata.get("impact")),
                        "docs_url": metadata.get("source") or metadata.get("shortlink"),
                        "references": _urls(metadata.get("references")),
                        "owasp": [str(o) for o in (owasp if isinstance(owasp, list) else [owasp]) if o],
                        "fix": extra.get("fix") if isinstance(extra.get("fix"), str) else None,
                        "end_line": end.get("line"),
                        "end_column": end.get("col"),
                        **_cwe_field((extra.get("metadata") or {}).get("cwe")),
                    },
                ),
            ),
        )
    return _semgrep_coverage(data, ParsedOutput(findings=findings))


_SEMGREP_NO_LINES = "requires login"
# ``paths.skipped`` reasons that mean a file Semgrep should have analyzed was
# not (ignore patterns, wrong language, minified/binary files are by design).
_SEMGREP_GAP_REASONS = frozenset(
    {
        "analysis_failed_parser_or_internal_error",
        "exceeded_size_limit",
        "too_big",
        "too_many_matches",
        "insufficient_permissions",
        "nonexistent_file",
        "timeout",
    },
)


def _semgrep_error_text(err: dict[str, Any]) -> str:
    kind = err.get("type")
    if isinstance(kind, list):  # ["PartialParsing", [locations]]
        kind = kind[0] if kind else ""
    where = err.get("path") or ""
    text = str(err.get("message") or kind or "error").splitlines()[0][:200]
    return f"{where}: {kind}: {text}" if where else f"{kind}: {text}" if kind else text


def _semgrep_coverage(data: dict[str, Any], parsed: ParsedOutput) -> ParsedOutput:
    """Errors (files parsed only partially or not at all, rules that failed) and
    skipped paths are coverage gaps; nothing scanned at all is an error."""
    errors = [e for e in data.get("errors") or [] if isinstance(e, dict)]
    if errors:
        parsed.warnings.append(
            f"Semgrep errors ({len(errors)}): {_clip_list([_semgrep_error_text(e) for e in errors])}",
        )
    paths = data.get("paths") if isinstance(data.get("paths"), dict) else {}
    skipped = [
        f"{s.get('path')}: {s.get('reason')}"
        for s in paths.get("skipped") or []
        if isinstance(s, dict) and str(s.get("reason") or "").lower() in _SEMGREP_GAP_REASONS
    ]
    if skipped:
        parsed.warnings.append(f"Not scanned ({len(skipped)} file(s)): {_clip_list(skipped)}")
    scanned = paths.get("scanned")
    # A file with a (non-partial) parse error was not analyzed at all.
    unparsed = {
        e.get("path")
        for e in errors
        if e.get("path") and "partial" not in str(e.get("type") or "").lower().replace(" ", "")
    }
    if (
        not parsed.findings
        and isinstance(scanned, list)
        and (errors or skipped)
        and (not scanned or set(scanned) <= unparsed)
    ):
        parsed.error = "Semgrep scanned no file — " + _clip_list(
            [_semgrep_error_text(e) for e in errors] + skipped,
            3,
        )
    return parsed


# Pylint message type -> the folder of its message pages in the docs.
_PYLINT_DOC_DIRS = {
    "convention": "convention",
    "refactor": "refactor",
    "warning": "warning",
    "error": "error",
    "fatal": "fatal",
    "info": "information",
}


@register("pylint")
def parse_pylint(raw: str) -> list[FindingData]:
    data = _json(raw, [])
    sev_map = {
        "fatal": "high",
        "error": "high",
        "warning": "medium",
        "refactor": "low",
        "convention": "low",
        "info": "info",
    }
    findings: list[FindingData] = []
    for item in data:
        msg_type = str(item.get("type", "warning")).lower()
        symbol = item.get("symbol") or ""
        doc_dir = _PYLINT_DOC_DIRS.get(msg_type)
        findings.append(
            FindingData(
                severity=sev_map.get(msg_type, "low"),
                category="quality",
                title=item.get("message", "Lint issue"),
                description=item.get("message", ""),
                file_path=item.get("path", ""),
                line_number=item.get("line"),
                column_number=item.get("column"),
                rule_id=symbol or item.get("message-id") or "",
                confidence="high",
                tool_severity=_label(item.get("type")),
                tool_specific_data=_compact(
                    {
                        "obj": item.get("obj", ""),
                        "message_id": item.get("message-id"),
                        "docs_url": (
                            f"https://pylint.readthedocs.io/en/stable/user_guide/messages/{doc_dir}/{symbol}.html"
                            if doc_dir and symbol
                            else None
                        ),
                        "end_line": item.get("endLine"),
                        "end_column": item.get("endColumn"),
                    },
                ),
            ),
        )
    return findings


_MYPY_LINE_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?:(?P<col>\d+):)?\s*"
    r"(?P<level>error|warning|note):\s*(?P<msg>.*?)"
    r"(?:\s+\[(?P<code>[\w-]+)\])?$",
)


_MYPY_FILE_ERROR_RE = re.compile(r"^(?P<file>[^\s:][^:]*): error: (?P<msg>.+)$")
# The error summary's note when a blocking error stopped the run.
_MYPY_BLOCKED = "errors prevented further checking"


@register("mypy")
def parse_mypy(raw: str) -> ParsedOutput:
    """Mypy has no stable JSON output; parse its ``file:line: level: msg`` text.

    A blocking error (a syntax error, a module found twice, an invalid
    package name) stops mypy before it type-checks anything: the findings
    are kept but the result is an error, not a (near-)clean measurement.
    """
    sev_map = {"error": "medium", "warning": "low", "note": "info"}
    findings: list[FindingData] = []
    blockers: list[str] = []
    last: FindingData | None = None
    for raw_line in (raw or "").splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        m = _MYPY_LINE_RE.match(line)
        if not m:
            # File-level errors carry no line number ("app.py: error: Duplicate
            # module named 'app'"); mypy only prints them for blocking errors.
            fm = _MYPY_FILE_ERROR_RE.match(line)
            if fm:
                blockers.append(f"{fm.group('file')}: {fm.group('msg').strip()}"[:300])
            continue
        if m.group("level") == "note":
            # A note continues the preceding error on the same line ("See
            # <docs>", "Possible overload variants: ..."): attach it there
            # rather than reporting it on its own.
            if last is not None and last.file_path == m.group("file") and last.line_number == int(m.group("line")):
                _attach_mypy_note(last, m.group("msg").strip())
            continue
        last = FindingData(
            severity=sev_map.get(m.group("level"), "low"),
            category="quality",
            title=m.group("msg").strip(),
            description=m.group("msg").strip(),
            file_path=m.group("file"),
            line_number=int(m.group("line")),
            column_number=int(m.group("col")) if m.group("col") else None,
            rule_id=m.group("code") or "",
            confidence="high",
            tool_severity=m.group("level"),
        )
        findings.append(last)
        if m.group("code") == "syntax":
            blockers.append(f"{last.file_path}:{last.line_number}: {last.title}"[:300])
    parsed = ParsedOutput(findings=findings)
    if blockers or _MYPY_BLOCKED in (raw or ""):
        parsed.error = "Mypy stopped at a blocking error before type-checking — " + (
            _clip_list(blockers, 3) or _MYPY_BLOCKED
        )
    return parsed


def _attach_mypy_note(finding: FindingData, note: str) -> None:
    urls = _urls(note)
    if note.startswith("See ") and urls and "docs_url" not in finding.tool_specific_data:
        finding.tool_specific_data["docs_url"] = urls[0]
    elif note:
        finding.description = f"{finding.description}\n{note}"


@register("radon")
def parse_radon(raw: str) -> ParsedOutput:
    """Radon ``cc -j`` output: ``{file: [block]}`` with nested methods/closures.

    Complexity is metric-shaped, so every block feeds the metrics; only the
    truly pathological ranks (E/F) also become findings.
    """
    data = _json(raw, {})
    if not isinstance(data, dict):
        return ParsedOutput()
    sev_map = {"E": "high", "F": "critical"}
    blocks: list[dict[str, Any]] = []
    findings: list[FindingData] = []

    def _walk(file_path: str, block: dict) -> None:
        rank = str(block.get("rank", ""))
        complexity = block.get("complexity")
        blocks.append(
            {
                "file": file_path,
                "name": block.get("name", "?"),
                "type": block.get("type", "block"),
                "line": block.get("lineno"),
                "complexity": complexity if isinstance(complexity, int) else 0,
                "rank": rank,
            },
        )
        if rank in sev_map:
            findings.append(
                FindingData(
                    severity=sev_map[rank],
                    category="performance",
                    title=(
                        f"{block.get('type', 'block')} '{block.get('name', '?')}' has "
                        f"cyclomatic complexity {complexity} (rank {rank})"
                    ),
                    file_path=file_path,
                    line_number=block.get("lineno"),
                    column_number=block.get("col_offset"),
                    rule_id=f"CC-{rank}",
                    confidence="high",
                    # Radon grades complexity (rank A-F) but reports no
                    # severity: the rank stays a data point.
                    tool_specific_data=_compact(
                        {
                            "complexity": complexity if isinstance(complexity, int) else None,
                            "rank": rank,
                            "function": (
                                f"{block['classname']}.{block.get('name', '?')}"
                                if block.get("classname")
                                else block.get("name")
                            ),
                            "end_line": block.get("endline"),
                            "endline": block.get("endline"),  # legacy key
                        },
                    ),
                ),
            )
        for child in (block.get("methods") or []) + (block.get("closures") or []):
            _walk(file_path, child)

    for file_path, file_blocks in data.items():
        # A file radon failed to parse maps to {"error": "..."} — skip it.
        if not isinstance(file_blocks, list):
            continue
        for block in file_blocks:
            _walk(file_path, block)

    if not blocks:
        return ParsedOutput(findings=findings)

    complexities = [b["complexity"] for b in blocks]
    rank_distribution = dict.fromkeys("ABCDEF", 0)
    for b in blocks:
        if b["rank"] in rank_distribution:
            rank_distribution[b["rank"]] += 1
    top_functions = sorted(blocks, key=lambda b: b["complexity"], reverse=True)[:25]
    metrics = {
        "total_blocks": len(blocks),
        "average_complexity": round(sum(complexities) / len(complexities), 2),
        "max_complexity": max(complexities),
        "rank_distribution": rank_distribution,
        "top_functions": top_functions,
    }
    return ParsedOutput(findings=findings, metrics=metrics)


_VULTURE_LINE_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+): (?P<msg>.+?) \((?P<conf>\d+)% confidence\)$",
)


@register("vulture")
def parse_vulture(raw: str) -> list[FindingData]:
    """Vulture has no JSON output; parse ``file:line: msg (NN% confidence)`` text."""
    findings: list[FindingData] = []
    for raw_line in (raw or "").splitlines():
        m = _VULTURE_LINE_RE.match(raw_line.rstrip())
        if not m:
            continue
        msg = m.group("msg")
        conf = int(m.group("conf"))
        findings.append(
            FindingData(
                severity="medium" if msg.startswith("unreachable code") else "low",
                category="quality",
                title=msg,
                description=msg,
                file_path=m.group("file"),
                line_number=int(m.group("line")),
                rule_id="-".join(msg.split()[:2]).lower(),
                confidence="high" if conf >= 90 else "medium" if conf >= 60 else "low",
                tool_specific_data={"tool_confidence": f"{m.group('conf')}%", "confidence_pct": conf},
            ),
        )
    return findings


@register("detect_secrets")
def parse_detect_secrets(raw: str) -> list[FindingData]:
    """detect-secrets baseline JSON: ``{"results": {path: [item]}}``."""
    data = _json(raw, {})
    if not isinstance(data, dict):
        return []
    findings: list[FindingData] = []
    for path, items in (data.get("results") or {}).items():
        if not isinstance(items, list):
            continue
        for item in items:
            secret_type = item.get("type", "secret")
            findings.append(
                FindingData(
                    severity="high",
                    category="secrets",
                    title=f"Potential secret: {secret_type}",
                    description=f"detect-secrets flagged a {secret_type} in {path}.",
                    file_path=item.get("filename") or path,
                    line_number=item.get("line_number"),
                    rule_id=secret_type,
                    confidence="medium",
                    # The baseline holds only a SHA-1 of the secret, never its value.
                    tool_specific_data={
                        "secret_type": secret_type,
                        "hashed_secret": item.get("hashed_secret", ""),
                        "is_verified": item.get("is_verified", False),
                    },
                ),
            )
    return findings


def _jscpd_range(side: dict[str, Any]) -> tuple[int, int] | None:
    start, end = _int(side.get("start")), _int(side.get("end"))
    if start is None:
        return None
    end = start if end is None else end
    return (min(start, end), max(start, end))


def _ranges_overlap(a: tuple[int, int] | None, b: tuple[int, int] | None) -> bool:
    return a is not None and b is not None and a[0] <= b[1] and b[0] <= a[1]


def _jscpd_unique_clones(duplicates: list[Any]) -> list[dict[str, Any]]:
    """The clones of *duplicates* minus those that repeat an already-kept clone.

    jscpd tokenizes a file once per embedded language (a .jsx file yields
    ``javascript``, ``jsx`` and ``css`` token streams) and detects clones in
    each stream, so one copied block can be reported two or three times with
    slightly different line ranges. A clone whose ranges both overlap a kept
    clone of the same file pair is such a repeat. Larger clones are kept
    first; a clone whose range runs backwards (an artifact of a non-contiguous
    embedded-language stream) only when nothing overlaps it.
    """
    clones = [d for d in duplicates if isinstance(d, dict)]

    def _key(dup: dict[str, Any]) -> tuple[int, int]:
        sides = (dup.get("firstFile") or {}, dup.get("secondFile") or {})
        sane = all((_int(s.get("end")) or 0) >= (_int(s.get("start")) or 0) for s in sides)
        return (0 if sane else 1, -(_int(dup.get("lines")) or 0))

    kept: list[dict[str, Any]] = []
    for dup in sorted(clones, key=_key):
        first, second = dup.get("firstFile") or {}, dup.get("secondFile") or {}
        a = (str(first.get("name") or ""), _jscpd_range(first))
        b = (str(second.get("name") or ""), _jscpd_range(second))
        repeat = False
        for other in kept:
            of, os_ = other.get("firstFile") or {}, other.get("secondFile") or {}
            oa = (str(of.get("name") or ""), _jscpd_range(of))
            ob = (str(os_.get("name") or ""), _jscpd_range(os_))
            for x, y in ((oa, ob), (ob, oa)):
                if a[0] == x[0] and b[0] == y[0] and _ranges_overlap(a[1], x[1]) and _ranges_overlap(b[1], y[1]):
                    repeat = True
                    break
            if repeat:
                break
        if not repeat:
            kept.append(dup)
    # Back in report order (stable output for the same input).
    order = {id(d): i for i, d in enumerate(clones)}
    return sorted(kept, key=lambda d: order[id(d)])


def _jscpd_metrics(data: dict[str, Any], clones: list[dict[str, Any]]) -> dict[str, Any]:
    """Duplication statistics per unique source file.

    jscpd's own totals count a file once per token stream (format) it was
    split into: a .jsx file is three "sources" and its lines are summed
    three times, and repeated clones inflate the duplicated lines. The
    per-format ``sources`` give each file's size; a file's lines are the
    largest span any of its streams covers, its tokens the sum (the streams
    split the file's tokens between them).
    """
    stats = data.get("statistics") or {}
    files: dict[str, dict[str, int]] = {}
    for fmt in (stats.get("formats") or {}).values():
        for name, src in ((fmt or {}).get("sources") or {}).items():
            entry = files.setdefault(name, {"lines": 0, "tokens": 0})
            entry["lines"] = max(entry["lines"], _int((src or {}).get("lines")) or 0)
            entry["tokens"] += _int((src or {}).get("tokens")) or 0
    total = stats.get("total") or {}
    if not files:
        # No per-format breakdown: jscpd's totals are all there is.
        return (
            {
                "clones": total.get("clones", 0),
                "duplicated_lines": total.get("duplicatedLines", 0),
                "duplicated_tokens": total.get("duplicatedTokens", 0),
                "total_lines": total.get("lines", 0),
                "total_sources": total.get("sources", 0),
                "duplication_percentage": round(float(total.get("percentage", 0.0)), 2),
                "token_duplication_percentage": round(float(total.get("percentageTokens", 0.0)), 2),
            }
            if total
            else {}
        )
    total_lines = sum(f["lines"] for f in files.values())
    total_tokens = sum(f["tokens"] for f in files.values())
    sources = len(files)
    # jscpd's measure: each clone's lines/tokens once (as its totals count them).
    dup_lines = sum(_int(c.get("lines")) or 0 for c in clones)
    dup_tokens = sum(_int(c.get("tokens")) or 0 for c in clones)
    return {
        "clones": len(clones),
        "duplicated_lines": dup_lines,
        "duplicated_tokens": dup_tokens,
        "total_lines": total_lines,
        "total_sources": sources,
        "duplication_percentage": round(100.0 * dup_lines / total_lines, 2) if total_lines else 0.0,
        "token_duplication_percentage": round(100.0 * dup_tokens / total_tokens, 2) if total_tokens else 0.0,
    }


@register("jscpd")
def parse_jscpd(raw: str) -> ParsedOutput:
    """jscpd JSON report: ``{"duplicates": [...], "statistics": {"formats": {...}, "total": {...}}}``.

    Clones are location-anchored and stay findings; the overall duplication
    statistics are metric-shaped and would otherwise be lost. Both are
    computed over unique clones and unique source files (see
    :func:`_jscpd_unique_clones`, :func:`_jscpd_metrics`).
    """
    data = _json(raw, {})
    if not isinstance(data, dict):
        return ParsedOutput(error="Unexpected jscpd report (not a JSON object)")
    clones = _jscpd_unique_clones(data.get("duplicates") or [])
    findings: list[FindingData] = []
    for dup in clones:
        first = dup.get("firstFile") or {}
        second = dup.get("secondFile") or {}
        lines = dup.get("lines") or 0
        findings.append(
            FindingData(
                severity="medium" if lines >= 25 else "low",
                category="quality",
                title=(
                    f"Duplicated block ({lines} lines) also found in {second.get('name', '?')}:{second.get('start')}"
                ),
                file_path=first.get("name", ""),
                line_number=first.get("start"),
                code_snippet=(dup.get("fragment") or "")[:500],
                rule_id="duplicate-code",
                confidence="high",
                tool_specific_data=_compact(
                    {
                        "duplicate_of": _compact(
                            {
                                "file": second.get("name"),
                                "start_line": second.get("start"),
                                "end_line": second.get("end"),
                            },
                        ),
                        "lines": lines or None,
                        "tokens": dup.get("tokens"),
                        "end_line": first.get("end"),
                        # Legacy keys (older rows, readers of the raw data).
                        "second_file": second.get("name", ""),
                        "second_start": second.get("start"),
                    },
                ),
            ),
        )
    return ParsedOutput(findings=findings, metrics=_jscpd_metrics(data, clones))


@register("hadolint")
def parse_hadolint(raw: str) -> list[FindingData]:
    data = _json(raw, [])
    if not isinstance(data, list):
        return []
    sev_map = {"error": "high", "warning": "medium", "info": "low", "style": "info"}
    findings: list[FindingData] = []
    for item in data:
        code = str(item.get("code") or "")
        findings.append(
            FindingData(
                severity=sev_map.get(str(item.get("level", "warning")).lower(), "low"),
                category="quality",
                title=item.get("message", "Dockerfile issue"),
                description=item.get("message", ""),
                file_path=item.get("file", ""),
                line_number=item.get("line"),
                column_number=item.get("column"),
                rule_id=item.get("code", ""),
                confidence="high",
                tool_severity=_label(item.get("level")),
                tool_specific_data=_compact({"docs_url": _hadolint_docs_url(code)}),
            ),
        )
    return findings


def _hadolint_docs_url(code: str) -> str:
    """Hadolint's own rules (DLxxxx) and the ShellCheck rules it runs on RUN lines (SCxxxx)."""
    if re.fullmatch(r"DL\d+", code):
        return f"https://github.com/hadolint/hadolint/wiki/{code}"
    if re.fullmatch(r"SC\d+", code):
        return f"https://www.shellcheck.net/wiki/{code}"
    return ""


_CODESPELL_LINE_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):\s+(?P<wrong>\S+) ==> (?P<right>.+)$",
)


@register("codespell")
def parse_codespell(raw: str) -> list[FindingData]:
    """codespell has no JSON output; parse ``file:line: wrong ==> right`` text."""
    findings: list[FindingData] = []
    for raw_line in (raw or "").splitlines():
        m = _CODESPELL_LINE_RE.match(raw_line.rstrip())
        if not m:
            continue
        wrong, right = m.group("wrong"), m.group("right")
        findings.append(
            FindingData(
                severity="info",
                category="quality",
                title=f"Possible typo: '{wrong}'",
                description=f"'{wrong}' looks like a misspelling.",
                suggestion=f"Replace '{wrong}' with '{right}'",
                file_path=m.group("file"),
                line_number=int(m.group("line")),
                rule_id="typo",
                confidence="medium",
                tool_specific_data={"fix": right.strip()},
            ),
        )
    return findings


@register("gitleaks")
def parse_gitleaks(raw: str) -> list[FindingData]:
    data = _json(raw, [])
    if not isinstance(data, list):
        return []
    findings: list[FindingData] = []
    for item in data:
        rule = item.get("RuleID") or item.get("Rule") or "secret"
        desc = item.get("Description") or f"Potential secret ({rule})"
        findings.append(
            FindingData(
                severity="high",
                category="secrets",
                title=desc[:200],
                description=desc,
                file_path=item.get("File", ""),
                line_number=item.get("StartLine"),
                column_number=item.get("StartColumn"),
                code_snippet=_redacted_match(item)[:500],
                rule_id=rule,
                confidence="high",
                tool_specific_data=_compact(
                    {
                        "secret_type": rule,
                        "entropy": item.get("Entropy"),
                        "end_line": item.get("EndLine"),
                        "end_column": item.get("EndColumn"),
                    },
                ),
            ),
        )
    return findings


_REDACTED = "REDACTED"


def _redacted_match(item: dict[str, Any]) -> str:
    """Gitleaks' ``Match`` (the matched text around the secret) with the secret masked.

    The run command passes ``--redact``, so both ``Match`` and ``Secret`` come
    back as ``REDACTED``. Should a report arrive unredacted (an edited
    command, an older gitleaks), the secret value must still never be stored:
    mask it in the match, and keep nothing when it cannot be masked (the
    match holds only part of a multi-line secret).
    """
    match = str(item.get("Match") or "")
    secret = str(item.get("Secret") or "")
    if not secret or secret == _REDACTED:
        return match
    if secret not in match:
        return ""
    return match.replace(secret, _REDACTED)
