"""Parsers for the dependency, web and extra lint/performance shop tools.

Registered into the same registry as :mod:`parsers` (imported from
``AnalysisConfig.ready`` so every process has them). Tools that scan several
manifests (pip-audit, npm audit, deptry, depcheck) print one
``### <relative path>`` header per manifest followed by that manifest's JSON
report; :func:`_sections` splits that back apart.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from backend.analysis.services.base import FindingData
from backend.analysis.services.base import ParsedOutput
from backend.analysis.services.parsers import _advisory_ids
from backend.analysis.services.parsers import _compact
from backend.analysis.services.parsers import _float
from backend.analysis.services.parsers import _label
from backend.analysis.services.parsers import _loads
from backend.analysis.services.parsers import _pyflakes_severity
from backend.analysis.services.parsers import failure_lines
from backend.analysis.services.parsers import parse_eslint
from backend.analysis.services.parsers import parse_pylint
from backend.analysis.services.parsers import register
from backend.analysis.services.parsers import section_bodies

_CWE_RE = re.compile(r"CWE-(\d+)", re.IGNORECASE)


def _sections(raw: str) -> list[tuple[str, Any]]:
    """``[(path, parsed_json)]`` for ``### path`` sections; unparsable bodies are dropped
    (``parsers.parse`` reports them as manifests that were not analyzed)."""
    out: list[tuple[str, Any]] = []
    for path, body in section_bodies(raw):
        data = _loads(body)
        if data is not None:
            out.append((path, data))
    return out


# pip / npm resolution errors the audit commands echo as "!! " lines.
_PIP_NO_DIST_RE = re.compile(r"No matching distribution found for (\S+)")
_PIP_NO_VERSIONS_RE = re.compile(r"satisfies the requirement (\S+) \(from versions: none\)")
_NPM_NOT_IN_REGISTRY_RE = re.compile(r"'(\S+?)' is not in this registry")
_NPM_NO_VERSION_RE = re.compile(r"No matching version found for (\S+?)\.?$")
_REQ_NAME_RE = re.compile(r"^(@?[A-Za-z0-9_.\-/]+?)(?:\[.*\])?(?:[=<>!~@^].*)?$")


def _unresolvable(path: str, spec: str, *, exists: bool, ecosystem: str) -> FindingData:
    """A declared dependency the package index cannot satisfy: the app cannot be installed.

    A package missing from the registry is the hallucinated-dependency
    ("slopsquatting") pattern: anyone may later publish that name.
    """
    m = _REQ_NAME_RE.match(spec)
    name = m.group(1) if m else spec
    if exists:
        title = f"{spec}: no such version on {ecosystem}"
        rule, severity = "nonexistent-version", "medium"
        suggestion = f"Pin {name} to a released version"
    else:
        title = f"{name}: package does not exist on {ecosystem}"
        rule, severity = "nonexistent-package", "high"
        suggestion = f"Remove {name} or replace it with the intended, existing package"
    return FindingData(
        severity=severity,
        category="dependency",
        title=title,
        description="The dependency cannot be resolved, so installing the app fails. "
        + ("" if exists else "An unregistered package name can be claimed by anyone (hallucinated dependency)."),
        suggestion=suggestion,
        file_path=path,
        rule_id=rule,
        confidence="high",
        tool_specific_data={"package": name, "requirement": spec, "ecosystem": ecosystem},
    )


def _resolution_findings(raw: str, ecosystem: str) -> list[FindingData]:
    findings: list[FindingData] = []
    for path, body in section_bodies(raw):
        lines = failure_lines(body)
        text = "\n".join(lines)
        seen: set[str] = set()
        if ecosystem == "PyPI":
            missing = set(_PIP_NO_VERSIONS_RE.findall(text))
            for spec in _PIP_NO_DIST_RE.findall(text):
                if spec not in seen:
                    seen.add(spec)
                    findings.append(_unresolvable(path, spec, exists=spec not in missing, ecosystem=ecosystem))
        else:
            for spec in _NPM_NOT_IN_REGISTRY_RE.findall(text):
                if spec not in seen:
                    seen.add(spec)
                    findings.append(_unresolvable(path, spec, exists=False, ecosystem=ecosystem))
            for line in lines:
                hit = _NPM_NO_VERSION_RE.search(line)
                if hit and hit.group(1) not in seen:
                    seen.add(hit.group(1))
                    findings.append(_unresolvable(path, hit.group(1), exists=True, ecosystem=ecosystem))
    return findings


def _json_docs(raw: str) -> list[Any]:
    """Every top-level JSON value in *raw* (xargs may run a tool more than once)."""
    decoder = json.JSONDecoder()
    docs: list[Any] = []
    idx, text = 0, raw or ""
    while True:
        starts = [i for i in (text.find("[", idx), text.find("{", idx)) if i != -1]
        if not starts:
            return docs
        start = min(starts)
        try:
            value, idx = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            idx = start + 1
            continue
        docs.append(value)


def _cwe(value: Any) -> dict[str, str]:
    """``{"cwe": "CWE-<n>"}`` from the first CWE in *value* (str or list), else {}."""
    if isinstance(value, list):
        value = value[0] if value else None
    m = _CWE_RE.search(str(value or ""))
    return {"cwe": f"CWE-{int(m.group(1))}"} if m else {}


# --- Dependency vulnerabilities ---------------------------------------------


@register("pip_audit", sectioned=True)
def parse_pip_audit(raw: str) -> list[FindingData]:
    """``pip-audit -f json``: ``{"dependencies": [{name, version, vulns: [...]}]}``."""
    findings: list[FindingData] = _resolution_findings(raw, "PyPI")
    for path, data in _sections(raw):
        if not isinstance(data, dict):
            continue
        seen: set[tuple[str, str]] = set()
        for dep in data.get("dependencies") or []:
            name, version = dep.get("name", "?"), dep.get("version", "")
            for vuln in dep.get("vulns") or []:
                vid = vuln.get("id", "")
                # pip-audit repeats a vuln once per requirement that pulls it in.
                if (name, vid) in seen:
                    continue
                seen.add((name, vid))
                aliases = vuln.get("aliases") or []
                cve = next((a for a in aliases if a.startswith("CVE-")), "")
                fixes = vuln.get("fix_versions") or []
                description = vuln.get("description") or ""
                findings.append(
                    FindingData(
                        # The OSV records pip-audit returns carry no severity
                        # or CVSS score: ``tool_severity`` stays empty.
                        severity="medium",
                        category="dependency",
                        title=f"{name} {version}: {cve or vid}",
                        description=description,
                        suggestion=f"Upgrade {name} to {' or '.join(fixes)}" if fixes else "",
                        file_path=path,
                        rule_id=vid,
                        confidence="high",
                        tool_specific_data=_compact(
                            {
                                "package": name,
                                "installed_version": version,
                                "fixed_versions": fixes,
                                "vuln_id": vid,
                                "aliases": _advisory_ids(vid, aliases),
                                "manifest": path,
                                # Legacy keys.
                                "version": version,
                                "fix_versions": fixes,
                            },
                        ),
                    ),
                )
    return findings


_NPM_SEVERITY = {"critical": "critical", "high": "high", "moderate": "medium", "low": "low", "info": "info"}


@register("npm_audit", sectioned=True)
def parse_npm_audit(raw: str) -> list[FindingData]:
    """``npm audit --json`` (report v2): one finding per advisory.

    Entries whose ``via`` holds only package names are inherited from another
    vulnerable package and are already reported through that package.
    """
    findings: list[FindingData] = _resolution_findings(raw, "npm")
    for path, data in _sections(raw):
        if not isinstance(data, dict):
            continue
        seen: set[Any] = set()
        for pkg, entry in (data.get("vulnerabilities") or {}).items():
            fix = entry.get("fixAvailable")
            if isinstance(fix, dict):
                major = " (major upgrade)" if fix.get("isSemVerMajor") else ""
                suggestion = f"Upgrade {fix.get('name')} to {fix.get('version')}{major}"
            elif fix:
                suggestion = "Run npm audit fix"
            else:
                suggestion = "No fix available yet"
            for via in entry.get("via") or []:
                if not isinstance(via, dict):
                    continue
                key = via.get("source") or via.get("url") or (pkg, via.get("title"))
                if key in seen:
                    continue
                seen.add(key)
                cvss = via.get("cvss") or {}
                # npm prints score 0 / vectorString null for an unscored advisory.
                score = _float(cvss.get("score")) or None
                url = str(via.get("url") or "")
                advisory_id = url.rsplit("/", 1)[-1] if url else ""
                findings.append(
                    FindingData(
                        severity=_NPM_SEVERITY.get(str(via.get("severity") or entry.get("severity")), "medium"),
                        category="dependency",
                        title=f"{pkg} {via.get('range', '')}: {via.get('title', 'Vulnerable dependency')}".strip(),
                        # npm audit's report has no advisory text, only its URL (in docs_url).
                        description="",
                        suggestion=suggestion,
                        file_path=path,
                        rule_id=advisory_id or str(via.get("source", "")),
                        confidence="high",
                        tool_severity=_label(via.get("severity") or entry.get("severity")),
                        tool_specific_data=_compact(
                            {
                                "package": pkg,
                                "direct": bool(entry.get("isDirect")),
                                "vuln_id": advisory_id,
                                "docs_url": url,
                                "cvss_score": score,
                                "cvss_vector": cvss.get("vectorString") if score else None,
                                "manifest": path,
                                "cvss": score,  # legacy key
                                **_cwe(via.get("cwe")),
                            },
                        ),
                    ),
                )
    return findings


# --- Dependency hygiene -----------------------------------------------------

_DEPTRY_SEVERITY = {
    "DEP001": "high",  # imported but not declared: ImportError at runtime
    "DEP002": "low",  # declared but unused
    "DEP003": "low",  # transitive dependency imported directly
    "DEP004": "medium",  # dev dependency imported in app code
    "DEP005": "info",  # stdlib module declared as a dependency
}


@register("deptry", sectioned=True)
def parse_deptry(raw: str) -> list[FindingData]:
    """``deptry --json-output``: ``[{error: {code, message}, module, location}]``."""
    findings: list[FindingData] = []
    for directory, data in _sections(raw):
        if not isinstance(data, list):
            continue
        prefix = "" if directory in ("", ".") else directory.rstrip("/") + "/"
        for item in data:
            error = item.get("error") or {}
            code = error.get("code", "")
            loc = item.get("location") or {}
            message = error.get("message", "")
            findings.append(
                FindingData(
                    # deptry rates nothing: ``tool_severity`` stays empty.
                    severity=_DEPTRY_SEVERITY.get(code, "low"),
                    category="dependency",
                    title=message or f"{code} {item.get('module', '')}",
                    description=message,
                    file_path=prefix + str(loc.get("file") or ""),
                    line_number=loc.get("line"),
                    column_number=loc.get("column"),
                    rule_id=code,
                    confidence="medium",
                    tool_specific_data={"module": item.get("module", "")},
                ),
            )
    return findings


@register("depcheck", sectioned=True)
def parse_depcheck(raw: str) -> list[FindingData]:
    """``depcheck --json``: unused ``dependencies``/``devDependencies`` and ``missing``."""
    findings: list[FindingData] = []
    for manifest, data in _sections(raw):
        if not isinstance(data, dict):
            continue
        root = manifest.rsplit("/", 1)[0] + "/" if "/" in manifest else ""
        for pkg, files in (data.get("missing") or {}).items():
            first = str((files or [""])[0])
            findings.append(
                FindingData(
                    severity="high",
                    category="dependency",
                    title=f"'{pkg}' is imported but missing from package.json",
                    description=f"Used in {len(files or [])} file(s); the build fails until it is declared.",
                    file_path=first,
                    rule_id="missing-dependency",
                    confidence="high",
                    tool_specific_data={"package": pkg, "files": files or [], "manifest": manifest},
                ),
            )
        for key, severity in (("dependencies", "low"), ("devDependencies", "info")):
            for pkg in data.get(key) or []:
                findings.append(
                    FindingData(
                        severity=severity,
                        category="dependency",
                        title=f"'{pkg}' is declared in {key} but never imported",
                        file_path=root + "package.json" if root else manifest,
                        rule_id=f"unused-{key}",
                        confidence="medium",
                        tool_specific_data={"package": pkg, "manifest": manifest},
                    ),
                )
    return findings


# --- Python lint / performance ----------------------------------------------

_FLAKE8_LINE_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+):(?P<col>\d+): (?P<code>[A-Z]+\d+) (?P<msg>.*)$")


@register("flake8")
def parse_flake8(raw: str) -> list[FindingData]:
    """flake8 default ``path:row:col: CODE message`` lines."""
    findings: list[FindingData] = []
    for line in (raw or "").splitlines():
        m = _FLAKE8_LINE_RE.match(line.strip())
        if not m:
            continue
        code = m.group("code")
        findings.append(
            FindingData(
                severity=_pyflakes_severity(code),
                category="quality" if code.startswith(("F", "E9", "C9")) else "style",
                title=m.group("msg"),
                description=m.group("msg"),
                file_path=m.group("file"),
                line_number=int(m.group("line")),
                column_number=int(m.group("col")),
                rule_id=code,
                confidence="high",
            ),
        )
    return findings


@register("perflint")
def parse_perflint(raw: str) -> list[FindingData]:
    """Perflint emits pylint JSON; its messages are performance hints."""
    findings = parse_pylint(raw)
    for f in findings:
        f.category = "performance"
        f.severity = "low"
        # Perflint's checks (W8xxx) are a plugin's: pylint's message pages do not cover them.
        f.tool_specific_data.pop("docs_url", None)
    return findings


_LIZARD_FIELDS = (
    "nloc",
    "ccn",
    "tokens",
    "params",
    "length",
    "location",
    "file",
    "function",
    "signature",
    "start",
    "end",
)
_LIZARD_THRESHOLDS = ((25, "high"), (15, "medium"))


# The run command's preamble: ``# files: <ext> <count>`` per source extension.
_LIZARD_FILES_RE = re.compile(r"^# files: (?P<ext>\w+) (?P<count>\d+)\s*$", re.MULTILINE)


@register("lizard")
def parse_lizard(raw: str) -> ParsedOutput:
    """``lizard --csv``: one row per function. All rows feed the metrics; only
    functions over CCN 15 become findings.

    The runner only runs Lizard when applicable files exist, so no function
    row at all means it read nothing (a language it was not told to parse):
    an error, not a clean result. The preamble's per-extension file counts
    flag an extension none of whose files yielded a function.
    """
    functions: list[dict[str, Any]] = []
    findings: list[FindingData] = []
    present = {m.group("ext"): int(m.group("count")) for m in _LIZARD_FILES_RE.finditer(raw or "")}
    for row in csv.reader(io.StringIO(raw or "")):
        if len(row) != len(_LIZARD_FIELDS):
            continue
        rec = dict(zip(_LIZARD_FIELDS, row, strict=True))
        try:
            ccn, nloc, start = int(rec["ccn"]), int(rec["nloc"]), int(rec["start"])
            params, length = int(rec["params"]), int(rec["length"])
        except ValueError:
            continue
        functions.append({"file": rec["file"], "name": rec["function"], "line": start, "ccn": ccn, "nloc": nloc})
        severity = next((sev for limit, sev in _LIZARD_THRESHOLDS if ccn > limit), None)
        if severity:
            end_line = int(rec["end"]) if rec["end"].isdigit() else start
            findings.append(
                FindingData(
                    # Lizard rates nothing (only a CCN warning threshold):
                    # ``tool_severity`` stays empty.
                    severity=severity,
                    category="performance",
                    title=f"Function '{rec['function']}' has cyclomatic complexity {ccn}",
                    description=f"{nloc} lines of code, {params} parameter(s), {length} lines long.",
                    file_path=rec["file"],
                    line_number=start,
                    rule_id="CCN",
                    confidence="high",
                    tool_specific_data={
                        "complexity": ccn,
                        "function": rec["function"],
                        "nloc": nloc,
                        "end_line": end_line,
                        "ccn": ccn,  # legacy key
                    },
                ),
            )
    if not functions:
        files = ", ".join(f"{n} .{ext}" for ext, n in sorted(present.items()))
        return ParsedOutput(
            findings=findings,
            error="Lizard measured no function" + (f" in {files} file(s)" if files else "") + ": nothing was analyzed",
        )
    measured = {f["file"].rsplit(".", 1)[-1].lower() for f in functions if "." in f["file"]}
    warnings = [
        f"No function measured in {n} .{ext} file(s)" for ext, n in sorted(present.items()) if ext not in measured
    ]
    ccns = [f["ccn"] for f in functions]
    worst = max(functions, key=lambda f: f["ccn"])
    metrics = {
        "functions": len(functions),
        "avg_ccn": round(sum(ccns) / len(ccns), 2),
        "max_ccn": worst["ccn"],
        "max_ccn_function": f"{worst['name']} ({worst['file']}:{worst['line']})",
        "total_nloc": sum(f["nloc"] for f in functions),
        "functions_over_15": sum(1 for c in ccns if c > 15),
    }
    return ParsedOutput(findings=findings, metrics=metrics, warnings=warnings)


# --- JavaScript security ----------------------------------------------------

# Rules whose hits are directly exploitable (code / command injection).
_ESLINT_SECURITY_HIGH = {
    "security/detect-eval-with-expression",
    "security/detect-child-process",
    "security/detect-non-literal-require",
    "security/detect-disable-mustache-escape",
}
_ESLINT_SECURITY_CWE = {
    "security/detect-eval-with-expression": "CWE-95",
    "security/detect-child-process": "CWE-78",
    "security/detect-non-literal-require": "CWE-829",
    "security/detect-non-literal-fs-filename": "CWE-22",
    "security/detect-non-literal-regexp": "CWE-1333",
    "security/detect-unsafe-regex": "CWE-1333",
    "security/detect-possible-timing-attacks": "CWE-208",
    "security/detect-pseudoRandomBytes": "CWE-338",
    "security/detect-disable-mustache-escape": "CWE-79",
    "security/detect-no-csrf-before-method-override": "CWE-352",
    "security/detect-buffer-noassert": "CWE-787",
    "security/detect-new-buffer": "CWE-787",
    "security/detect-bidi-characters": "CWE-1007",
}


@register("eslint_security")
def parse_eslint_security(raw: str) -> ParsedOutput:
    """ESLint JSON from eslint-plugin-security (every rule reports as a warning)."""
    parsed = parse_eslint(raw)
    for f in parsed.findings:
        f.category = "security"
        f.severity = "high" if f.rule_id in _ESLINT_SECURITY_HIGH else "medium"
        f.confidence = "medium"
        if f.rule_id in _ESLINT_SECURITY_CWE:
            f.tool_specific_data = {**f.tool_specific_data, "cwe": _ESLINT_SECURITY_CWE[f.rule_id]}
    return parsed


# --- Web (HTML / CSS) and shell ---------------------------------------------


@register("htmlhint")
def parse_htmlhint(raw: str) -> list[FindingData]:
    """``htmlhint --format json``: ``[{file, messages: [{type, message, line, col, rule}]}]``."""
    findings: list[FindingData] = []
    for doc in _json_docs(raw):
        for entry in doc if isinstance(doc, list) else []:
            for msg in entry.get("messages") or []:
                rule = msg.get("rule") or {}
                findings.append(
                    FindingData(
                        severity="medium" if msg.get("type") == "error" else "low",
                        category="quality",
                        title=msg.get("message", "HTML issue"),
                        description=rule.get("description", ""),
                        file_path=entry.get("file", ""),
                        line_number=msg.get("line"),
                        column_number=msg.get("col"),
                        code_snippet=str(msg.get("evidence") or "")[:300],
                        rule_id=rule.get("id", ""),
                        confidence="high",
                        tool_severity=_label(msg.get("type")),
                        # htmlhint links each rule's page in its report.
                        tool_specific_data=_compact({"docs_url": rule.get("link")}),
                    ),
                )
    return findings


def _stylelint_docs_url(rule: str) -> str:
    """The page of a core stylelint rule; plugin rules (``plugin/rule``) and syntax errors have none."""
    if re.fullmatch(r"[a-z][a-z0-9-]*", rule or ""):
        return f"https://stylelint.io/user-guide/rules/{rule}"
    return ""


@register("stylelint")
def parse_stylelint(raw: str) -> list[FindingData]:
    """``stylelint -f json``: ``[{source, warnings: [...], parseErrors: [...]}]``."""
    findings: list[FindingData] = []
    for doc in _json_docs(raw):
        for entry in doc if isinstance(doc, list) else []:
            for warning in (entry.get("warnings") or []) + (entry.get("parseErrors") or []):
                text = warning.get("text", "CSS issue")
                rule = warning.get("rule", "parse-error")
                findings.append(
                    FindingData(
                        severity="medium" if warning.get("severity") == "error" else "low",
                        category="quality",
                        # "Unexpected unknown property "x" (property-no-unknown)" -> drop the rule suffix.
                        title=re.sub(r"\s*\([\w/-]+\)$", "", text),
                        description=text,
                        file_path=entry.get("source", ""),
                        line_number=warning.get("line"),
                        column_number=warning.get("column"),
                        rule_id=rule,
                        confidence="high",
                        tool_severity=_label(warning.get("severity")),
                        tool_specific_data=_compact(
                            {
                                "docs_url": _stylelint_docs_url(rule) if rule != "parse-error" else "",
                                "end_line": warning.get("endLine"),
                                "end_column": warning.get("endColumn"),
                            },
                        ),
                    ),
                )
    return findings


_SHELLCHECK_SEVERITY = {"error": "high", "warning": "medium", "info": "low", "style": "info"}


@register("shellcheck")
def parse_shellcheck(raw: str) -> list[FindingData]:
    """``shellcheck -f json``: a flat list of comments (one list per xargs batch)."""
    findings: list[FindingData] = []
    for doc in _json_docs(raw):
        for item in doc if isinstance(doc, list) else []:
            code = item.get("code")
            findings.append(
                FindingData(
                    severity=_SHELLCHECK_SEVERITY.get(item.get("level", ""), "low"),
                    category="quality",
                    title=item.get("message", "Shell issue"),
                    description=item.get("message", ""),
                    file_path=item.get("file", ""),
                    line_number=item.get("line"),
                    column_number=item.get("column"),
                    rule_id=f"SC{code}" if code else "",
                    confidence="high",
                    tool_severity=_label(item.get("level")),
                    tool_specific_data=_compact(
                        {
                            "docs_url": f"https://www.shellcheck.net/wiki/SC{code}" if code else None,
                            "end_line": item.get("endLine"),
                            "end_column": item.get("endColumn"),
                        },
                    ),
                ),
            )
    return findings
