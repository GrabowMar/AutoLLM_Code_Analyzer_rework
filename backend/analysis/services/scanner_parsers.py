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
from typing import Any
from urllib.parse import urlsplit

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


# A bundler's content-hashed build output (Vite ``/assets/index-DcMykpf9.js``,
# CRA ``/static/js/main.3f2a1b9c.js``): minified vendor code, not the app's
# source, and renamed on every build.
_HASHED_ASSET_RE = re.compile(
    r"/[\w.-]*?[.-](?=[A-Za-z0-9_-]*[0-9A-Z])[A-Za-z0-9_-]{8,}(?:\.chunk)?\.(?:m?js|css)(?:\.map)?$",
)


def _is_hashed_asset(url: str) -> bool:
    return bool(_HASHED_ASSET_RE.search(_url_path(url)))


def _zap_instance_groups(instances: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """*instances* grouped into one group per finding.

    Instances on the same path collapse (one per HTTP method). So do paths
    whose hits are all passive (no attack) with the same evidence: an SPA
    serves its index page for every unknown route (``/robots.txt``,
    ``/sitemap.xml``), so ZAP sees the same response — and reports the same
    issue — once per route it probed.
    """
    by_path: dict[str, list[dict[str, Any]]] = {}
    for inst in instances:
        by_path.setdefault(_url_path(inst.get("uri", "")), []).append(inst)
    groups: list[list[dict[str, Any]]] = []
    by_evidence: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for path_group in by_path.values():
        keys = {
            (str(i.get("evidence") or ""), str(i.get("param") or ""))
            for i in path_group
            if i.get("evidence") and not i.get("attack")
        }
        passive_same = len(keys) == 1 and all(i.get("evidence") and not i.get("attack") for i in path_group)
        if passive_same:
            key = next(iter(keys))
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
    user marked as false positives.

    Informational alerts (risk 0: suspicious comments, fingerprints) are not
    weaknesses and are kept out of the ``security`` category. Hits inside
    content-hashed build bundles are third-party minified code: informational
    ones are dropped, others are kept and marked ``build_asset``.
    """
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
            instances = alert.get("instances") or [{"uri": site.get("@name", "")}]
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
                findings.append(
                    FindingData(
                        severity=_ZAP_RISK.get(risk, "info"),
                        category="best_practice" if informational else "security",
                        title=title,
                        description=_plain(alert.get("desc")),
                        suggestion=_plain(alert.get("solution")),
                        file_path=_url_path(inst.get("uri", "")),
                        code_snippet=str(inst.get("evidence") or "")[:500],
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
                                "evidence": _clip(inst.get("evidence"), 500),
                                "other_info": _clip(_plain(inst.get("otherinfo") or alert.get("otherinfo")), 1000),
                                "instances": int(alert.get("count") or 1),
                                "urls": group_urls[:50] if len(group_urls) > 1 or alert.get("systemic") else [],
                                "build_asset": True if all(_is_hashed_asset(u) for u in group_urls) else None,
                                "references": references,
                                "docs_url": f"https://www.zaproxy.org/docs/alerts/{plugin_id}/"
                                if plugin_id.isdigit()
                                else None,
                                **_dast_issue_key(title),
                                **_cwe_field(int(alert["cweid"]) if str(alert.get("cweid", "")).isdigit() else None),
                            },
                        ),
                    ),
                )
    return ParsedOutput(findings=findings)


_NUCLEI_SEVERITY = {"critical": "critical", "high": "high", "medium": "medium", "low": "low", "info": "info"}


@register("nuclei")
def parse_nuclei(raw: str) -> list[FindingData]:
    """``nuclei -jsonl``: one JSON object per line, one line per matcher hit."""
    findings: list[FindingData] = []
    for raw_line in (raw or "").splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            hit = json.loads(line)
        except json.JSONDecodeError:
            continue
        info = hit.get("info") or {}
        tags = info.get("tags") or []
        if hit.get("template-id") == "tech-detect" or "tech" in (tags if isinstance(tags, list) else [tags]):
            continue  # technology fingerprinting, not a weakness
        matcher = hit.get("matcher-name") or ""
        name = info.get("name") or hit.get("template-id") or "Nuclei match"
        matched = hit.get("matched-at") or hit.get("url") or ""
        classification = info.get("classification") or {}
        refs = info.get("reference") or []
        extracted = hit.get("extracted-results") or []
        evidence = ", ".join(str(e) for e in extracted)[:500]
        cvss_score = _float(classification.get("cvss-score"))
        cve_ids = classification.get("cve-id") or []
        cve_ids = [str(c).upper() for c in (cve_ids if isinstance(cve_ids, list) else [cve_ids]) if c]
        findings.append(
            FindingData(
                severity=_NUCLEI_SEVERITY.get(str(info.get("severity", "")).lower(), "info"),
                category="security",
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
                        **_dast_issue_key(hit.get("template-id"), matcher),
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
    return findings


_WAPITI_LEVEL = {4: "critical", 3: "high", 2: "medium", 1: "low", 0: "info"}


@register("wapiti")
def parse_wapiti(raw: str) -> list[FindingData]:
    """Wapiti's JSON report.

    ``vulnerabilities`` are security issues; ``anomalies`` are server errors
    the attacks provoked (a robustness signal). ``additionals`` (fingerprints,
    allowed HTTP methods) are informational noise and skipped.
    """
    data = _json(raw, {})
    if not isinstance(data, dict):
        return []
    classifications = data.get("classifications") or {}
    findings: list[FindingData] = []
    for section, category in (("vulnerabilities", "security"), ("anomalies", "reliability")):
        for name, items in (data.get(section) or {}).items():
            meta = classifications.get(name) or {}
            refs = meta.get("ref") or {}
            cwe = next((_cwe(k) for k in refs if "CWE" in k), {})
            references = _urls(list(refs.values()) if isinstance(refs, dict) else refs)
            for item in items or []:
                level = item.get("level")
                severity = _WAPITI_LEVEL.get(level, "medium") if section == "vulnerabilities" else "medium"
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
                                **(_dast_issue_key(name, item.get("info")) if section == "vulnerabilities" else {}),
                            },
                        ),
                    ),
                )
    return findings


# --- Static multi-ecosystem scanners ---------------------------------------

_TRIVY_SEVERITY = {"CRITICAL": "critical", "HIGH": "high", "MEDIUM": "medium", "LOW": "low", "UNKNOWN": "info"}


@register("trivy")
def parse_trivy(raw: str) -> list[FindingData]:
    """``trivy fs --format json``: ``Results[]`` with vulns, misconfigs and secrets."""
    data = _json(raw, {})
    if not isinstance(data, dict):
        return []
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
    return findings


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


@register("osv_scanner")
def parse_osv_scanner(raw: str) -> list[FindingData]:
    """``osv-scanner scan source --format json``.

    ``packages[].groups`` collapses aliases of one vulnerability (PYSEC/GHSA/
    CVE) and carries the max CVSS score; one finding per package and group.
    """
    data = _json(raw, {})
    if not isinstance(data, dict):
        return []
    findings: list[FindingData] = []
    # A manifest can appear twice (direct "lockfile" pass + transitive pass).
    seen: set[tuple[str, str, str, str]] = set()
    for result in data.get("results") or []:
        path = (result.get("source") or {}).get("path", "")
        for pkg in result.get("packages") or []:
            meta = pkg.get("package") or {}
            name, version = meta.get("name", "?"), meta.get("version", "")
            vulns = {v.get("id"): v for v in pkg.get("vulnerabilities") or []}
            for group in pkg.get("groups") or []:
                ids = group.get("ids") or []
                key = (path, name, version, ids[0] if ids else "")
                if key in seen:
                    continue
                seen.add(key)
                aliases = group.get("aliases") or ids
                # One group = the same vulnerability in several databases
                # (PYSEC + GHSA): each record carries part of the data (GHSA:
                # summary, CWE, severity label; PYSEC: often only details).
                records = [vulns[i] for i in dict.fromkeys([*ids, *aliases]) if i in vulns]
                cve = next((a for a in aliases if str(a).startswith("CVE-")), "")
                ghsa = next((a for a in aliases if str(a).startswith("GHSA-")), "")
                fixed = sorted(
                    {
                        ev["fixed"]
                        for rec in records
                        for aff in rec.get("affected") or []
                        if (aff.get("package") or {}).get("name", name).lower() == name.lower()
                        for rng in aff.get("ranges") or []
                        for ev in rng.get("events") or []
                        if ev.get("fixed")
                    },
                )
                summary = next((r["summary"] for r in records if r.get("summary")), "")
                db_specific = [r.get("database_specific") or {} for r in records]
                max_severity = _label(group.get("max_severity"))
                vectors = {
                    str(s.get("score"))
                    for r in records
                    for s in r.get("severity") or []
                    if str(s.get("type", "")).startswith("CVSS") and s.get("score")
                }
                findings.append(
                    FindingData(
                        severity=_cvss_severity(group.get("max_severity")),
                        category="dependency",
                        title=f"{name} {version}: {cve or ghsa or (ids[0] if ids else 'vulnerability')}"
                        + (f" — {summary}" if summary else ""),
                        description=next((r["details"] for r in records if r.get("details")), "") or summary,
                        suggestion=f"Upgrade {name} to {' or '.join(fixed)}" if fixed else "",
                        file_path=path,
                        rule_id=ids[0] if ids else "",
                        confidence="high",
                        # The advisory's own label (GitHub's "MODERATE") when a
                        # record has one, else the CVSS score osv-scanner
                        # prints in its severity column.
                        tool_severity=next(
                            (_label(d.get("severity")) for d in db_specific if _label(d.get("severity"))),
                            max_severity,
                        ),
                        tool_specific_data=_compact(
                            {
                                "package": name,
                                "installed_version": version,
                                "ecosystem": meta.get("ecosystem", ""),
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
    return findings


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
# Suggestions rather than defects (meta description / keywords).
_DJLINT_INFO = {"H030", "H031"}


@register("djlint")
def parse_djlint(raw: str) -> list[FindingData]:
    """``djlint --lint --linter-output-format '{filename}:{line}:{code}:{message}'``.

    Progress-bar and blank lines are ignored. djLint prints absolute paths
    without the leading slash (``work/app/templates/x.html``).
    """
    findings: list[FindingData] = []
    for line in (raw or "").splitlines():
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
    return findings
