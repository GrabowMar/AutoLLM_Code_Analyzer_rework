"""Parsers for the DAST scanners and the multi-ecosystem static scanners.

Dynamic tools (ZAP, Nuclei, Wapiti) probe a running app, so their findings
point at a URL path instead of a source line: ``file_path`` holds the path
(``/api/items``) so findings on the same endpoint group together, and the full
URL, HTTP method and parameter go into ``tool_specific_data``.

Registered into the shared registry from ``AnalysisConfig.ready``.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from urllib.parse import urlsplit

from packaging.version import InvalidVersion
from packaging.version import Version

from backend.analysis.services.base import FindingData
from backend.analysis.services.base import ParsedOutput
from backend.analysis.services.extra_parsers import _cwe
from backend.analysis.services.parsers import _advisory_ids
from backend.analysis.services.parsers import _clip
from backend.analysis.services.parsers import _compact
from backend.analysis.services.parsers import _cwe_field
from backend.analysis.services.parsers import _float
from backend.analysis.services.parsers import _json
from backend.analysis.services.parsers import _label
from backend.analysis.services.parsers import _urls
from backend.analysis.services.parsers import register

_TAG_RE = re.compile(r"<[^>]+>")


def _plain(text: Any) -> str:
    """Strip the HTML markup ZAP puts in descriptions (``<p>…</p>``)."""
    return html.unescape(_TAG_RE.sub("\n", str(text or ""))).strip()


def _url_path(url: str) -> str:
    try:
        return urlsplit(url).path or "/"
    except ValueError:
        return url


def _cvss_severity(score: Any) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "medium"
    if value >= 9.0:
        return "critical"
    if value >= 7.0:
        return "high"
    if value >= 4.0:
        return "medium"
    return "low" if value > 0 else "info"


# Lines a tool's ``run_cmd`` (data/tools/*.yaml) echoes after the tool
# itself, never the tool's own output:
#   llmlab-warning: <gap>  a coverage gap (a package.json without a lockfile,
#                          a crashed djLint pass): kept on the result
#   llmlab-done: <note>    the tool ran to its end (nuclei, which prints
#                          nothing for a clean scan)
_MARKER_RE = re.compile(r"^llmlab-(warning|done):\s*(.*)$")


@dataclass
class _Markers:
    warnings: list[str] = field(default_factory=list)
    done: bool = False


def _markers(raw: str) -> tuple[str, _Markers]:
    """*raw* without the run_cmd's marker lines, and what those lines said."""
    marks = _Markers()
    if "llmlab-" not in (raw or ""):
        return raw or "", marks
    kept: list[str] = []
    # split("\n"), not splitlines(): a JSON string may hold U+2028 or NEL,
    # which splitlines() would cut and the rejoin would turn into a raw newline.
    for line in raw.split("\n"):
        m = _MARKER_RE.match(line.strip())
        if not m:
            kept.append(line)
            continue
        if m.group(1) == "warning":
            marks.warnings.append(m.group(2).strip())
        else:
            marks.done = True
    return "\n".join(kept), marks


# --- Dynamic (DAST) ---------------------------------------------------------

# The scanners report the same weakness under their own ids (ZAP plugin
# 10038, Nuclei http-missing-security-headers:content-security-policy, Wapiti
# csp). ``issue_key`` names it once so cross-tool dedup counts it once, and
# keeps Nuclei's per-header hits (one template id) apart.
_SECURITY_HEADERS = (
    ("content-security-policy", ("content-security-policy", "content security policy", "csp")),
    ("x-frame-options", ("x-frame-options", "clickjacking")),
    ("x-content-type-options", ("x-content-type-options", "mime type confusion")),
    ("strict-transport-security", ("strict-transport-security", "strict transport security", "hsts")),
    ("referrer-policy", ("referrer-policy",)),
    ("permissions-policy", ("permissions-policy", "permissions policy")),
    ("cross-origin-embedder-policy", ("cross-origin-embedder-policy",)),
    ("cross-origin-opener-policy", ("cross-origin-opener-policy",)),
    ("cross-origin-resource-policy", ("cross-origin-resource-policy",)),
    ("x-permitted-cross-domain-policies", ("x-permitted-cross-domain-policies",)),
)
# Fingerprints, not weaknesses: ZAP's "Modern Web Application" (10109).
_ZAP_SKIP = frozenset({"10109"})


def _dast_issue_key(*texts: Any) -> dict[str, str]:
    """``{"issue_key": ...}`` for a missing security header or a CORS misconfiguration, else {}."""
    text = " ".join(str(t or "") for t in texts).lower()
    if "cors" in text or "cross-domain misconfiguration" in text:
        return {"issue_key": "cors-misconfiguration"}
    if not any(word in text for word in ("missing", "not set")):
        return {}
    for header, needles in _SECURITY_HEADERS:
        if any(needle in text for needle in needles):
            return {"issue_key": f"missing-header:{header}"}
    return {}


_CORS_KEY = "cors-misconfiguration"
_HSTS_KEY = "missing-header:strict-transport-security"


def _hsts_over_http(issue: dict[str, str], url: str) -> bool:
    """A missing HSTS header on a plain-http response.

    Browsers ignore Strict-Transport-Security sent over http, and the
    sandboxed apps are only ever served over http: not a weakness of the app.
    """
    if issue.get("issue_key") != _HSTS_KEY:
        return False
    try:
        return urlsplit(str(url or "")).scheme.lower() == "http"
    except ValueError:
        return False


_ZAP_RISK = {"3": "high", "2": "medium", "1": "low", "0": "info"}
_ZAP_CONFIDENCE = {"4": "high", "3": "high", "2": "medium", "1": "low"}
# ZAP's own words for its codes, as its reports print them (``riskdesc``
# "Medium (High)" = risk Medium, confidence High).
_ZAP_RISK_WORDS = {"3": "High", "2": "Medium", "1": "Low", "0": "Informational"}
_ZAP_CONFIDENCE_WORDS = {"4": "Confirmed", "3": "High", "2": "Medium", "1": "Low", "0": "False Positive"}
_ZAP_RISKDESC_RE = re.compile(r"^\s*([^()]+?)\s*\(([^()]+)\)\s*$")


def _zap_labels(alert: dict[str, Any]) -> tuple[str, str]:
    """ZAP's risk and confidence words for *alert*: from ``riskdesc``, else from the codes."""
    m = _ZAP_RISKDESC_RE.match(str(alert.get("riskdesc") or ""))
    if m:
        return m.group(1), m.group(2)
    return (
        _ZAP_RISK_WORDS.get(str(alert.get("riskcode", "")), ""),
        _ZAP_CONFIDENCE_WORDS.get(str(alert.get("confidence", "")), ""),
    )


# A bundler's content-hashed build output: Vite's ``/assets/<name>-<hash>``
# (an 8-character base64url hash with a digit or an upper-case letter; any
# prefix, e.g. Flask's ``/static/assets/index-DcMykpf9.js``) and CRA's
# ``/static/js|css/<name>.<8 hex>[.chunk]``. Minified (comments stripped) and
# renamed on every build. Hand-written files (``/static/js/todo-ListView.js``)
# never match.
_HASHED_ASSET_RE = re.compile(
    r"(?:/assets/[\w.-]+-(?=[A-Za-z0-9_-]{0,7}[0-9A-Z])[A-Za-z0-9_-]{8}"
    r"|/static/(?:js|css)/[\w.-]+\.[0-9a-f]{8}(?:\.chunk)?)\.(?:m?js|css)(?:\.map)?$",
)


def _is_hashed_asset(url: str) -> bool:
    return bool(_HASHED_ASSET_RE.search(_url_path(url)))


def _zap_instance_groups(instances: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """*instances* grouped into one group per finding.

    Instances on the same path collapse (one per HTTP method). So do paths
    whose hits are all passive (no attack) with the same set of evidence: an
    SPA serves its index page for every unknown route (``/robots.txt``,
    ``/sitemap.xml``), so ZAP sees the same response — and reports the same
    issues (one instance per suspicious comment) — once per route it probed.
    """
    by_path: dict[str, list[dict[str, Any]]] = {}
    for inst in instances:
        by_path.setdefault(_url_path(inst.get("uri", "")), []).append(inst)
    groups: list[list[dict[str, Any]]] = []
    by_evidence: dict[frozenset[tuple[str, str]], list[dict[str, Any]]] = {}
    for path_group in by_path.values():
        if all(i.get("evidence") and not i.get("attack") for i in path_group):
            key = frozenset((str(i.get("evidence") or ""), str(i.get("param") or "")) for i in path_group)
            if key in by_evidence:
                by_evidence[key].extend(path_group)
                continue
            by_evidence[key] = path_group
        groups.append(path_group)
    return groups


@register("zap")
def parse_zap(raw: str) -> ParsedOutput:
    """ZAP's traditional JSON report: ``site[].alerts[]`` with ``instances[]``.

    One finding per alert and endpoint (instances of the same alert on the
    same path, e.g. one per HTTP method, collapse; so do passive hits with
    the same evidence on several SPA-fallback routes). ``systemic`` alerts —
    the same issue on every page, like a missing security header — are one
    finding listing the affected URLs. Alerts with confidence 0 are ones a
    user marked as false positives. ``evidence``/``other_info`` hold every
    distinct value of the finding's instances (two suspicious comments on one
    page are one finding showing both).

    Informational alerts (risk 0: suspicious comments, fingerprints) are not
    weaknesses and are kept out of the ``security`` category. Content-hashed
    build bundles are minified (comments stripped): informational hits in
    them are dropped, others are kept and marked ``build_asset``. A missing
    HSTS header on a plain-http URL is dropped (see :func:`_hsts_over_http`).
    """
    if not (raw or "").strip():
        # The run_cmd prints the report file; a scan always writes one.
        return ParsedOutput(error="ZAP wrote no report")
    data = _json(raw, {})
    if not isinstance(data, dict):
        return ParsedOutput(error="Unexpected ZAP report (not a JSON object)")
    findings: list[FindingData] = []
    for site in data.get("site") or []:
        for alert in site.get("alerts") or []:
            confidence = str(alert.get("confidence", ""))
            if confidence == "0" or str(alert.get("pluginid", "")) in _ZAP_SKIP:
                continue
            risk = str(alert.get("riskcode", ""))
            informational = risk == "0"
            title = alert.get("name") or alert.get("alert") or "ZAP alert"
            issue = _dast_issue_key(title)
            instances = alert.get("instances") or [{"uri": site.get("@name", "")}]
            instances = [inst for inst in instances if not _hsts_over_http(issue, inst.get("uri", ""))]
            if informational:
                instances = [inst for inst in instances if not _is_hashed_asset(inst.get("uri", ""))]
            if not instances:
                continue
            urls = list(dict.fromkeys(inst.get("uri", "") for inst in instances))
            groups = [instances] if alert.get("systemic") else _zap_instance_groups(instances)
            risk_word, confidence_word = _zap_labels(alert)
            plugin_id = str(alert.get("pluginid", ""))
            references = _urls(_plain(alert.get("reference")))
            for group in groups:
                inst = group[0]
                group_urls = urls if alert.get("systemic") else list(dict.fromkeys(i.get("uri", "") for i in group))
                evidence = "\n".join(dict.fromkeys(str(i.get("evidence") or "") for i in group if i.get("evidence")))
                # An instance's otherinfo is plain text (it may quote the
                # comment "<!-- Admin -->"); the alert's is HTML (<p>…</p>).
                other_info = "\n".join(
                    dict.fromkeys(
                        text
                        for i in group
                        if (text := str(i.get("otherinfo") or "").strip() or _plain(alert.get("otherinfo")))
                    ),
                )
                findings.append(
                    FindingData(
                        severity=_ZAP_RISK.get(risk, "info"),
                        category="best_practice" if informational else "security",
                        title=title,
                        description=_plain(alert.get("desc")),
                        suggestion=_plain(alert.get("solution")),
                        file_path=_url_path(inst.get("uri", "")),
                        code_snippet=evidence[:500],
                        rule_id=plugin_id,
                        confidence=_ZAP_CONFIDENCE.get(confidence, "medium"),
                        tool_severity=risk_word,
                        tool_specific_data=_compact(
                            {
                                "tool_confidence": confidence_word,
                                "url": inst.get("uri", ""),
                                "method": inst.get("method", ""),
                                "param": inst.get("param", ""),
                                "attack": _clip(inst.get("attack"), 500),
                                "evidence": _clip(evidence, 500),
                                "other_info": _clip(other_info, 1000),
                                # ZAP's count can exceed the instances it lists;
                                # an alert split into several findings counts
                                # each one's own instances.
                                "instances": len(group) if len(groups) > 1 else int(alert.get("count") or 1),
                                "urls": group_urls[:50] if len(group_urls) > 1 or alert.get("systemic") else [],
                                "build_asset": True if all(_is_hashed_asset(u) for u in group_urls) else None,
                                "references": references,
                                "docs_url": f"https://www.zaproxy.org/docs/alerts/{plugin_id}/"
                                if plugin_id.isdigit()
                                else None,
                                **issue,
                                **_cwe_field(int(alert["cweid"]) if str(alert.get("cweid", "")).isdigit() else None),
                            },
                        ),
                    ),
                )
    return ParsedOutput(findings=findings)


_NUCLEI_SEVERITY = {"critical": "critical", "high": "high", "medium": "medium", "low": "low", "info": "info"}


@register("nuclei")
def parse_nuclei(raw: str) -> ParsedOutput:
    """``nuclei -jsonl``: one JSON object per line, one line per matcher hit.

    Hits nuclei itself rates ``info`` (missing headers, API-docs discovery)
    are not weaknesses: category ``best_practice``, like ZAP's informational
    alerts — except a CORS misconfiguration, which stays ``security``. A
    missing HSTS header on a plain-http URL is dropped.

    With ``-silent`` a clean scan prints nothing, so the run_cmd ends with an
    ``llmlab-done`` line. Output with neither a hit nor that line (nuclei
    never ran to its end) measured nothing: an error, not a clean zero. The
    runner checks that the app answered before and after the scan.
    """
    raw, marks = _markers(raw)
    findings: list[FindingData] = []
    printed_hits = False
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            hit = json.loads(line)
        except json.JSONDecodeError:
            continue
        printed_hits = True
        info = hit.get("info") or {}
        tags = info.get("tags") or []
        if hit.get("template-id") == "tech-detect" or "tech" in (tags if isinstance(tags, list) else [tags]):
            continue  # technology fingerprinting, not a weakness
        matcher = hit.get("matcher-name") or ""
        name = info.get("name") or hit.get("template-id") or "Nuclei match"
        matched = hit.get("matched-at") or hit.get("url") or ""
        issue = _dast_issue_key(hit.get("template-id"), matcher)
        if _hsts_over_http(issue, matched):
            continue
        rated = str(info.get("severity", "")).lower()
        classification = info.get("classification") or {}
        refs = info.get("reference") or []
        extracted = hit.get("extracted-results") or []
        evidence = ", ".join(str(e) for e in extracted)[:500]
        cvss_score = _float(classification.get("cvss-score"))
        cve_ids = classification.get("cve-id") or []
        cve_ids = [str(c).upper() for c in (cve_ids if isinstance(cve_ids, list) else [cve_ids]) if c]
        findings.append(
            FindingData(
                severity=_NUCLEI_SEVERITY.get(rated, "info"),
                category="best_practice" if rated == "info" and issue.get("issue_key") != _CORS_KEY else "security",
                title=f"{name}: {matcher}" if matcher else name,
                description=str(info.get("description") or "").strip(),
                suggestion=str(info.get("remediation") or "").strip(),
                file_path=_url_path(matched),
                code_snippet=evidence,
                rule_id=hit.get("template-id", ""),
                confidence="medium",
                tool_severity=_label(info.get("severity")),
                tool_specific_data=_compact(
                    {
                        "rule_name": info.get("name"),
                        "url": matched,
                        "matcher": matcher,
                        "tags": tags,
                        **issue,
                        "references": [str(r) for r in (refs if isinstance(refs, list) else [refs]) if r],
                        "docs_url": hit.get("template-url"),
                        "evidence": evidence,
                        "vuln_id": cve_ids[0] if cve_ids else None,
                        "aliases": cve_ids,
                        "cvss_score": cvss_score,
                        "cvss_vector": classification.get("cvss-metrics") if cvss_score else None,
                        "curl": hit.get("curl-command"),
                        **_cwe(classification.get("cwe-id")),
                    },
                ),
            ),
        )
    if not (printed_hits or marks.done):
        return ParsedOutput(
            error="Nuclei printed nothing, not even the run_cmd's completion line: nothing was measured.",
            warnings=marks.warnings,
        )
    return ParsedOutput(findings=findings, warnings=marks.warnings)


_WAPITI_LEVEL = {4: "critical", 3: "high", 2: "medium", 1: "low", 0: "info"}


@register("wapiti")
def parse_wapiti(raw: str) -> ParsedOutput:
    """Wapiti's JSON report.

    ``vulnerabilities`` are security issues — except level 0 (Wapiti's info
    level) ones, ``best_practice`` like ZAP's informational alerts, unless
    they are a CORS misconfiguration. ``anomalies`` are server errors the
    attacks provoked (a robustness signal). ``additionals`` (fingerprints,
    allowed HTTP methods) are informational noise and skipped.
    """
    if not (raw or "").strip():
        # The run_cmd prints the report file; a scan always writes one.
        return ParsedOutput(error="Wapiti wrote no report")
    data = _json(raw, {})
    if not isinstance(data, dict):
        return ParsedOutput(error="Unexpected Wapiti report (not a JSON object)")
    classifications = data.get("classifications") or {}
    findings: list[FindingData] = []
    for section, section_category in (("vulnerabilities", "security"), ("anomalies", "reliability")):
        for name, items in (data.get(section) or {}).items():
            meta = classifications.get(name) or {}
            refs = meta.get("ref") or {}
            cwe = next((_cwe(k) for k in refs if "CWE" in k), {})
            references = _urls(list(refs.values()) if isinstance(refs, dict) else refs)
            for item in items or []:
                level = item.get("level")
                severity = _WAPITI_LEVEL.get(level, "medium") if section == "vulnerabilities" else "medium"
                issue = _dast_issue_key(name, item.get("info")) if section == "vulnerabilities" else {}
                category = section_category
                if section == "vulnerabilities" and str(level) == "0" and issue.get("issue_key") != _CORS_KEY:
                    category = "best_practice"
                findings.append(
                    FindingData(
                        severity=severity,
                        category=category,
                        title=f"{name}: {item.get('info', '')}".rstrip(": ")[:500],
                        description=str(meta.get("desc") or "").strip(),
                        suggestion=str(meta.get("sol") or "").strip(),
                        file_path=item.get("path") or "/",
                        rule_id=item.get("module") or name,
                        confidence="medium",
                        # Wapiti's JSON has only the numeric level (4 critical .. 0 info).
                        tool_severity=_label(level),
                        tool_specific_data=_compact(
                            {
                                "rule_name": name,
                                "method": item.get("method", ""),
                                "param": item.get("parameter", ""),
                                "curl": item.get("curl_command", ""),
                                "wstg": item.get("wstg") or [],
                                "references": references,
                                **cwe,
                                **issue,
                            },
                        ),
                    ),
                )
    return ParsedOutput(findings=findings)


# --- Static multi-ecosystem scanners ---------------------------------------

_TRIVY_SEVERITY = {"CRITICAL": "critical", "HIGH": "high", "MEDIUM": "medium", "LOW": "low", "UNKNOWN": "info"}


@register("trivy")
def parse_trivy(raw: str) -> ParsedOutput:
    """``trivy fs --format json``: ``Results[]`` with vulns, misconfigs and secrets.

    The run_cmd adds an ``llmlab-warning`` line per package.json with no
    lockfile beside it: Trivy reads npm dependencies only from lockfiles.
    """
    raw, marks = _markers(raw)
    data = _json(raw, {})
    if not isinstance(data, dict):
        return ParsedOutput(warnings=marks.warnings)
    findings: list[FindingData] = []
    for result in data.get("Results") or []:
        target = result.get("Target", "")
        for v in result.get("Vulnerabilities") or []:
            pkg, version, fixed = v.get("PkgName", "?"), v.get("InstalledVersion", ""), v.get("FixedVersion", "")
            vid = v.get("VulnerabilityID", "")
            findings.append(
                FindingData(
                    severity=_TRIVY_SEVERITY.get(v.get("Severity", ""), "medium"),
                    category="dependency",
                    title=f"{pkg} {version}: {vid}" + (f" — {v['Title']}" if v.get("Title") else ""),
                    description=v.get("Description", ""),
                    suggestion=f"Upgrade {pkg} to {fixed}" if fixed else "",
                    file_path=target,
                    rule_id=vid,
                    confidence="high",
                    tool_severity=_label(v.get("Severity")),
                    tool_specific_data=_compact(
                        {
                            "package": pkg,
                            "installed_version": version,
                            # "1.2.5, 2.0.1" when several release lines are fixed.
                            "fixed_versions": [f.strip() for f in str(fixed).split(",") if f.strip()],
                            "vuln_id": vid,
                            "aliases": _advisory_ids(vid, v.get("VendorIDs")),
                            **_trivy_cvss(v),
                            "docs_url": v.get("PrimaryURL"),
                            "references": _urls(v.get("References")),
                            "manifest": target,
                            # Legacy keys.
                            "version": version,
                            "fixed_version": fixed,
                            "url": v.get("PrimaryURL", ""),
                            **_cwe(v.get("CweIDs")),
                        },
                    ),
                ),
            )
        for m in result.get("Misconfigurations") or []:
            if m.get("Status") not in (None, "FAIL"):
                continue
            cause = m.get("CauseMetadata") or {}
            lines = (cause.get("Code") or {}).get("Lines") or []
            findings.append(
                FindingData(
                    severity=_TRIVY_SEVERITY.get(m.get("Severity", ""), "medium"),
                    category="security",
                    title=m.get("Title") or m.get("ID", "Misconfiguration"),
                    description=m.get("Message") or m.get("Description", ""),
                    suggestion=m.get("Resolution", ""),
                    file_path=target,
                    line_number=cause.get("StartLine") or None,
                    code_snippet="\n".join(str(ln.get("Content", "")) for ln in lines if ln.get("IsCause"))[:500],
                    rule_id=m.get("ID") or m.get("AVDID", ""),
                    confidence="high",
                    tool_severity=_label(m.get("Severity")),
                    tool_specific_data=_compact(
                        {
                            "type": m.get("Type", ""),
                            "docs_url": m.get("PrimaryURL"),
                            "references": _urls(m.get("References")),
                            "end_line": cause.get("EndLine") or None,
                            "url": m.get("PrimaryURL", ""),  # legacy key
                        },
                    ),
                ),
            )
        for s in result.get("Secrets") or []:
            findings.append(
                FindingData(
                    severity=_TRIVY_SEVERITY.get(s.get("Severity", ""), "high"),
                    category="secrets",
                    title=s.get("Title") or s.get("RuleID", "Secret"),
                    description=f"{s.get('Category', 'Secret')} detected in source.",
                    file_path=target,
                    line_number=s.get("StartLine") or None,
                    # Trivy masks the secret in Match ("KEY=\"****\"") and
                    # prints its value nowhere else.
                    code_snippet=str(s.get("Match", ""))[:500],
                    rule_id=s.get("RuleID", ""),
                    confidence="high",
                    tool_severity=_label(s.get("Severity")),
                    tool_specific_data=_compact(
                        {
                            "secret_type": s.get("RuleID"),
                            "end_line": s.get("EndLine") or None,
                            "cwe": "CWE-798",
                        },
                    ),
                ),
            )
    return ParsedOutput(findings=findings, warnings=marks.warnings)


def _trivy_cvss(vuln: dict[str, Any]) -> dict[str, Any]:
    """``cvss_score``/``cvss_vector`` from Trivy's per-source ``CVSS`` map.

    The source Trivy took ``Severity`` from (``SeveritySource``) comes first,
    then NVD; within a source the newest CVSS version. Score and vector
    always come from the same entry.
    """
    cvss = vuln.get("CVSS")
    if not isinstance(cvss, dict):
        return {}
    for source in dict.fromkeys([vuln.get("SeveritySource"), "nvd", *cvss]):
        entry = cvss.get(source) if source else None
        if not isinstance(entry, dict):
            continue
        for version in ("V40", "V3", "V2"):
            score = _float(entry.get(f"{version}Score"))
            if score:
                return {"cvss_score": score, "cvss_vector": entry.get(f"{version}Vector")}
    return {}


# Advisory labels (GitHub's database_specific.severity) as normalized levels.
_OSV_LABEL_SEVERITY = {"critical": "critical", "high": "high", "moderate": "medium", "medium": "medium", "low": "low"}


def _version(text: Any) -> Version | None:
    try:
        return Version(str(text))
    except InvalidVersion:
        return None


def _version_order(text: str) -> tuple[int, Version, str]:
    """Sort key: parsable versions in version order, then the rest as strings."""
    parsed = _version(text)
    return (0, parsed, text) if parsed is not None else (1, Version("0"), text)


def _in_range(current: Version | None, introduced: Any, fixed: str) -> bool:
    """Whether *current* lies in ``[introduced, fixed)`` (introduced "0" or none: from the start)."""
    high = _version(fixed)
    if current is None or high is None:
        return False
    if introduced in (None, "0"):
        return current < high
    low = _version(introduced)
    return low is not None and low <= current < high


def _osv_fixes(records: list[dict[str, Any]], name: str, installed: str) -> tuple[list[str], list[str]]:
    """``(every fixed version, the ones to suggest)`` for *name* at *installed*.

    The records list a fix per release line (``[0, 4.5.6)``, ``[5.0.0,
    5.4.12)``…). The suggestion is the fix of the range holding the installed
    version (the highest, if the records disagree), else the lowest fix above
    it; all fixes only when the versions do not parse. Git ranges (commit
    hashes) are not versions.
    """
    fixes: set[str] = set()
    applicable: set[str] = set()
    current = _version(installed)
    for rec in records:
        for aff in rec.get("affected") or []:
            if (aff.get("package") or {}).get("name", name).lower() != name.lower():
                continue
            for rng in aff.get("ranges") or []:
                if str(rng.get("type", "")).upper() == "GIT":
                    continue
                introduced: Any = None
                for ev in rng.get("events") or []:
                    if "introduced" in ev:
                        introduced = ev["introduced"]
                    elif ev.get("fixed"):
                        fixed = str(ev["fixed"])
                        fixes.add(fixed)
                        if _in_range(current, introduced, fixed):
                            applicable.add(fixed)
                        introduced = None
                    else:  # last_affected / limit: the range ends without a fix
                        introduced = None
    ordered = sorted(fixes, key=_version_order)
    if applicable:
        return ordered, [max(applicable, key=_version_order)]
    if current is None or any(_version(f) is None for f in ordered):
        return ordered, ordered
    higher = [f for f in ordered if _version(f) > current]  # type: ignore[operator]
    return ordered, higher[:1]


def _relative(path: str) -> str:
    """A workspace path as the other scanners print it (``/work/a/b`` -> ``a/b``)."""
    return path.removeprefix("/work/")


@register("osv_scanner")
def parse_osv_scanner(raw: str) -> ParsedOutput:
    """``osv-scanner scan source --format json``.

    ``packages[].groups`` collapses aliases of one vulnerability (PYSEC/GHSA/
    CVE) and carries the max CVSS score; one finding per package and group.

    Packages only dev dependencies reach (``dependency_groups`` holds
    ``dev``: vite, esbuild, vitest…) never ship with the built app; like npm
    audit (``--omit=dev``) and Trivy, they are left out. ``["dev",
    "optional"]`` is kept (tagged with its ``dependency_groups``): for
    lockfile v2+ osv prints it both for a dev tool's optional platform
    binary and for npm's ``devOptional`` packages, which can ship as an
    optional dependency of a production package, and the two cannot be told
    apart. ``metrics`` counts what was left out (``0`` when nothing was).
    ``tool_severity`` is the advisory's own label (GitHub's ``MODERATE``),
    never the CVSS number (that is ``cvss_score``); the normalized severity
    follows the label, else the max CVSS score.

    A scan always prints its report (``{"results": [...]}``), also when it
    finds nothing; the run_cmd turns osv's exit 1 ("vulnerabilities found")
    into 0. Output without that report measured nothing: an error, not a
    clean result.
    """
    raw, marks = _markers(raw)
    data = _json(raw, None)
    if data is None:
        # 128 (no package source it can read) and 127 (osv's general error)
        # print no report.
        return ParsedOutput(error="osv-scanner printed no report: nothing was measured.", warnings=marks.warnings)
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        return ParsedOutput(error="Unexpected osv-scanner report (no results list)", warnings=marks.warnings)
    findings: list[FindingData] = []
    dev_only: dict[str, int] = {}
    # A manifest can appear twice (direct "lockfile" pass + transitive pass).
    seen: set[tuple[str, str, str, str]] = set()
    for result in data.get("results") or []:
        path = _relative((result.get("source") or {}).get("path", ""))
        for pkg in result.get("packages") or []:
            meta = pkg.get("package") or {}
            name, version = meta.get("name", "?"), meta.get("version", "")
            groups = pkg.get("dependency_groups") or []
            vulns = {v.get("id"): v for v in pkg.get("vulnerabilities") or []}
            for group in pkg.get("groups") or []:
                ids = group.get("ids") or []
                key = (path, name, version, ids[0] if ids else "")
                if key in seen:
                    continue
                seen.add(key)
                if "dev" in groups and "optional" not in groups:
                    dev_only[f"{name}@{version}"] = dev_only.get(f"{name}@{version}", 0) + 1
                    continue
                aliases = group.get("aliases") or ids
                # One group = the same vulnerability in several databases
                # (PYSEC + GHSA): each record carries part of the data (GHSA:
                # summary, CWE, severity label; PYSEC: often only details).
                records = [vulns[i] for i in dict.fromkeys([*ids, *aliases]) if i in vulns]
                cve = next((a for a in aliases if str(a).startswith("CVE-")), "")
                ghsa = next((a for a in aliases if str(a).startswith("GHSA-")), "")
                fixed, suggested = _osv_fixes(records, name, version)
                summary = next((r["summary"] for r in records if r.get("summary")), "")
                db_specific = [r.get("database_specific") or {} for r in records]
                max_severity = _label(group.get("max_severity"))
                label = next((_label(d.get("severity")) for d in db_specific if _label(d.get("severity"))), "")
                vectors = {
                    str(s.get("score"))
                    for r in records
                    for s in r.get("severity") or []
                    if str(s.get("type", "")).startswith("CVSS") and s.get("score")
                }
                findings.append(
                    FindingData(
                        severity=_OSV_LABEL_SEVERITY.get(label.lower()) or _cvss_severity(max_severity),
                        category="dependency",
                        title=f"{name} {version}: {cve or ghsa or (ids[0] if ids else 'vulnerability')}"
                        + (f" — {summary}" if summary else ""),
                        description=next((r["details"] for r in records if r.get("details")), "") or summary,
                        suggestion=f"Upgrade {name} to {' or '.join(suggested)}" if suggested else "",
                        file_path=path,
                        rule_id=ids[0] if ids else "",
                        confidence="high",
                        # The advisory's own label (GitHub's "MODERATE"); ""
                        # (unrated) when no record has one.
                        tool_severity=label,
                        tool_specific_data=_compact(
                            {
                                "package": name,
                                "installed_version": version,
                                "ecosystem": meta.get("ecosystem", ""),
                                "dependency_groups": groups,
                                "fixed_versions": fixed,
                                "vuln_id": ids[0] if ids else None,
                                "aliases": _advisory_ids(ids, aliases),
                                "cvss_score": _float(max_severity),
                                # Only when unambiguous: max_severity may come
                                # from any of the records' vectors.
                                "cvss_vector": next(iter(vectors)) if len(vectors) == 1 else None,
                                "references": _urls(
                                    [ref.get("url") for r in records for ref in r.get("references") or []],
                                ),
                                "manifest": path,
                                # Legacy keys.
                                "version": version,
                                "fix_versions": fixed,
                                "cvss": max_severity or None,
                                **next(
                                    (_cwe(d.get("cwe_ids")) for d in db_specific if _cwe(d.get("cwe_ids"))),
                                    {},
                                ),
                            },
                        ),
                    ),
                )
    # Always present on a parsed report, so an average over results counts the
    # scans that left nothing out (a result without the key was not measured).
    metrics: dict[str, Any] = {"dev_only_advisories_skipped": sum(dev_only.values())}
    if dev_only:
        metrics["dev_only_packages_skipped"] = dev_only
    return ParsedOutput(findings=findings, metrics=metrics, warnings=marks.warnings)


_RETIRE_SEVERITY = {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}


@register("retire")
def parse_retire(raw: str) -> list[FindingData]:
    """``retire --outputformat json``: ``data[].results[].vulnerabilities[]``."""
    data = _json(raw, {})
    if not isinstance(data, dict):
        return []
    findings: list[FindingData] = []
    for entry in data.get("data") or []:
        path = entry.get("file", "")
        for res in entry.get("results") or []:
            component, version = res.get("component", "?"), res.get("version", "")
            for vuln in res.get("vulnerabilities") or []:
                ids = vuln.get("identifiers") or {}
                cves = ids.get("CVE") or []
                ref = cves[0] if cves else ids.get("githubID") or ids.get("issue") or ids.get("bug") or ""
                below = vuln.get("below")
                github_id = ids.get("githubID")
                findings.append(
                    FindingData(
                        severity=_RETIRE_SEVERITY.get(str(vuln.get("severity", "")).lower(), "medium"),
                        category="dependency",
                        title=f"{component} {version}: {ids.get('summary') or ref or 'known vulnerability'}"[:500],
                        description="\n".join(vuln.get("info") or []),
                        suggestion=f"Upgrade {component} to {below} or later" if below else "",
                        file_path=path,
                        rule_id=str(ref),
                        confidence="high",
                        tool_severity=_label(vuln.get("severity")),
                        tool_specific_data=_compact(
                            {
                                "package": component,
                                "installed_version": version,
                                "fixed_versions": [str(below)] if below else [],
                                "vuln_id": str(ref),
                                "aliases": _advisory_ids(ref, cves, github_id),
                                "references": _urls(vuln.get("info")),
                                "detection": res.get("detection", ""),
                                "version": version,  # legacy key
                                **_cwe(vuln.get("cwe")),
                            },
                        ),
                    ),
                )
    return findings


# djLint's {line} expands to "line:column".
_DJLINT_LINE_RE = re.compile(r"^(?P<file>.+?):(?P<line>\d+)(?::(?P<col>\d+))?:(?P<code>[A-Z]\d{3}):(?P<msg>.+)$")
# Suggestions rather than defects (meta description / keywords); the run_cmd
# now ignores both, kept for older outputs.
_DJLINT_INFO = {"H030", "H031"}
# "Linted 3 files, found 2 errors." after each pass that linted something.
_DJLINT_LINTED_RE = re.compile(r"\bLinted [1-9]\d* files?\b")


@register("djlint")
def parse_djlint(raw: str) -> ParsedOutput:
    """``djlint --lint --linter-output-format '{filename}:{line}:{code}:{message}'``.

    Progress-bar and blank lines are ignored. djLint prints absolute paths
    without the leading slash (``work/app/templates/x.html``). Every pass
    that lints a file ends with "Linted N files, found M errors."; one that
    finds none prints "No files to check!" and (1.36) exits 0. Output with
    no finding and no such summary measured nothing: an error, not a clean
    result.
    """
    raw, marks = _markers(raw)
    findings: list[FindingData] = []
    for line in raw.splitlines():
        m = _DJLINT_LINE_RE.match(line.strip())
        if not m:
            continue
        code = m.group("code")
        path = m.group("file").lstrip("/").removeprefix("work/")
        findings.append(
            FindingData(
                # djLint's lint output carries no level: ``tool_severity`` stays empty.
                severity="info" if code in _DJLINT_INFO else "low",
                category="quality",
                title=m.group("msg").strip(),
                file_path=path,
                line_number=int(m.group("line")),
                column_number=int(m.group("col")) if m.group("col") else None,
                rule_id=code,
                confidence="high",
                # The rule table (djLint has no per-rule pages).
                tool_specific_data={"docs_url": "https://www.djlint.com/docs/linter/#rules"},
            ),
        )
    if not findings and not _DJLINT_LINTED_RE.search(raw):
        reason = '"No files to check!"' if "No files to check" in raw else "no summary line"
        return ParsedOutput(
            error=f"djLint linted no template ({reason}): nothing was measured.",
            warnings=marks.warnings,
        )
    return ParsedOutput(findings=findings, warnings=marks.warnings)
