"""Parsers that turn raw tool output into normalized findings and metrics.

Each parser maps a tool's JSON (or text) output to ``list[FindingData]`` or,
for tools whose output is (partly) metric-shaped, a ``ParsedOutput`` carrying
aggregate metrics alongside any findings. Parsers are looked up by the
``parser_key`` declared on a catalog tool.
"""

from __future__ import annotations

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


def _loads(raw: str) -> Any:
    raw = (raw or "").strip()
    if not raw:
        return None
    # Tools sometimes emit warnings before the JSON payload; trim to first
    # JSON delimiter to be forgiving.
    for opener in ("[", "{"):
        idx = raw.find(opener)
        if idx > 0:
            try:
                return json.loads(raw[idx:])
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Tool produced no JSON (e.g. an empty scan or a CLI error banner).
        # Treat as "no findings" rather than raising a noisy parser error.
        return None


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
def parse_bandit(raw: str) -> list[FindingData]:
    data = _loads(raw) or {}
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
    return findings


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
    data = _loads(raw) or []
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


@register("eslint")
def parse_eslint(raw: str) -> list[FindingData]:
    data = _loads(raw) or []
    sev_map = {2: "high", 1: "low"}
    findings: list[FindingData] = []
    for file_entry in data:
        file_path = file_entry.get("filePath", "")
        for msg in file_entry.get("messages", []):
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
    return findings


_SEMGREP_CATEGORIES = {
    "security": "security",
    "correctness": "quality",
    "best-practice": "best_practice",
    "maintainability": "quality",
    "performance": "performance",
    "portability": "quality",
}


@register("semgrep")
def parse_semgrep(raw: str) -> list[FindingData]:
    data = _loads(raw) or {}
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
    return findings


_SEMGREP_NO_LINES = "requires login"

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
    data = _loads(raw) or []
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


@register("mypy")
def parse_mypy(raw: str) -> list[FindingData]:
    """Mypy has no stable JSON output; parse its ``file:line: level: msg`` text."""
    sev_map = {"error": "medium", "warning": "low", "note": "info"}
    findings: list[FindingData] = []
    last: FindingData | None = None
    for raw_line in (raw or "").splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        m = _MYPY_LINE_RE.match(line)
        if not m:
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
    return findings


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
    data = _loads(raw) or {}
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
    data = _loads(raw) or {}
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


@register("jscpd")
def parse_jscpd(raw: str) -> ParsedOutput:
    """jscpd JSON report: ``{"duplicates": [...], "statistics": {"total": {...}}}``.

    Clones are location-anchored and stay findings; the overall duplication
    statistics are metric-shaped and would otherwise be lost.
    """
    data = _loads(raw) or {}
    if not isinstance(data, dict):
        return ParsedOutput()
    findings: list[FindingData] = []
    for dup in data.get("duplicates") or []:
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

    total = ((data.get("statistics") or {}).get("total")) or {}
    metrics = (
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
    return ParsedOutput(findings=findings, metrics=metrics)


@register("hadolint")
def parse_hadolint(raw: str) -> list[FindingData]:
    data = _loads(raw) or []
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
    data = _loads(raw) or []
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
