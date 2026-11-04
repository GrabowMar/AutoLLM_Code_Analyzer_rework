"""Detector engines (spec section 7.9 step 3): semgrep (via the analysis rulepack), ts_query, regex, probe, manual.

Each engine yields one :class:`Verdict` per (variant, detector, scope): ``present``, ``absent``, ``error``
(with ``error_reason``) or ``not_applicable``. Scopes:

- ``span``: a match intersects the slot span inside the assembled file;
- ``file``: a match anywhere in the assembled file (the slot file with the parent's code around it).

Engines read the **assembled file** (a whole file), so Semgrep taint rules always see flows through parent
helpers; the ``span`` feature is then the subset of matches that touch the regenerated part.

Validity rules (Appendix A #10): a Semgrep ``errors[]`` entry touching the file, a non-zero exit, a missing
report, an unscanned file or an unparsed slot file is ``error``, never ``absent``; ``tool_severity`` is the
tool's own label copied verbatim (Semgrep's ``extra.severity``, or the field ``tool_severity_field``
names); in-process engines have no severity and leave it blank. Nothing here computes a severity.

``probe`` detectors map eval probe results of the variant (latest evaluation): ``exploited`` -> present,
``not_exploited`` -> absent, a probe ``error`` -> error, ``skipped`` (precondition failed, disabled,
level) -> n/a, not measured -> n/a. A probe detector selects probes by kind when its ``rule`` is
``kind:<ProbeKind>`` (the seed set has one per probe kind, spec 8.3), by id when it is any other value (an
id or an ``fnmatch`` glob), else by CWE overlap with ``content.cwe``; a target's ``linked_probes`` restrict
the candidates. ``probe`` and ``manual`` verdicts are reported at scope ``span`` only (they describe the
regenerated part, not the file).

``manual`` detectors read cluster labels (``ClusterLabel``) by the variant's exact cluster key
(``normalized_sha``): the majority of the labelers' ``present``/``absent`` codes (``unsure`` ignored; a
tie, only-unsure or no label is n/a).
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

import yaml

from backend.generation import identity
from backend.generation.patterns import normalize as norm

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Mapping
    from collections.abc import Sequence

    from backend.generation.models import PatternDetector

ENGINES = ("semgrep", "ts_query", "regex", "probe", "manual")
IN_PROCESS_ENGINES = frozenset({"ts_query", "regex", "probe", "manual"})
SCOPES = ("span", "file")
DETECTOR_SET_HASH_VERSION = identity.DETECTOR_SET_HASH_VERSION
_REF_RE = re.compile(r"^(?P<slug>[a-z0-9][a-z0-9_-]*)@(?P<version>[1-9][0-9]*)(?:#(?P<hash>[0-9a-f]{12}))?$")
_RULE_ID_RE = re.compile(r"(?:^|\.)llmlab-d(?P<d>\d+)-r(?P<r>\d+)$")
MAX_MATCHES = 50
PROBE_KIND_PREFIX = "kind:"


class DetectorRefError(ValueError):
    """A detector ref that does not resolve (unknown slug/version, hash mismatch or malformed)."""


# ---------------------------------------------------------------------------
# Detector and variant inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DetectorSpec:
    """A pinned detector version (``content`` is ``contracts.library.DetectorContent`` JSON)."""

    slug: str
    version: int
    content_hash: str
    content: dict[str, Any]
    pk: int | None = None

    @property
    def ref(self) -> str:
        return f"{self.slug}@{self.version}"

    @property
    def pinned_ref(self) -> str:
        return identity.pinned_ref(self.slug, self.version, self.content_hash)

    @property
    def engine(self) -> str:
        return str(self.content["engine"])

    @property
    def feature_key(self) -> str:
        return str(self.content["feature_key"])

    @property
    def languages(self) -> list[str]:
        return [norm.canonical_language(lang) for lang in self.content.get("languages") or []]

    @property
    def scopes(self) -> list[str]:
        return list(self.content.get("scopes") or SCOPES)

    @property
    def rule(self) -> str:
        return str(self.content.get("rule") or "")

    @property
    def tool(self) -> dict[str, str]:
        return dict(self.content.get("tool") or {})

    def applies_to(self, language: str) -> bool:
        """Whether the detector covers ``language`` (``probe``/``manual`` with no languages cover all)."""
        langs = self.languages
        if not langs:
            return self.engine in {"probe", "manual"}
        return norm.canonical_language(language) in langs

    @classmethod
    def from_row(cls, row: PatternDetector) -> DetectorSpec:
        return cls(slug=row.slug, version=row.version, content_hash=row.content_hash, content=row.content, pk=row.pk)


@dataclass(frozen=True)
class Label:
    value: str  # present | absent | unsure
    labeler: str


@dataclass
class VariantInput:
    """Everything a detector needs about one variant.

    ``file_text`` is the assembled slot file (``None`` when it could not be read); ``span`` is the 1-based
    inclusive line range of the regenerated part inside it (``None`` for a whole-file target, where the
    span is the file). ``probes`` is the latest evaluation's probe results, ``None`` when not measured.
    ``labels`` maps a manual detector's ref to its labels for this variant's cluster key.
    """

    run_id: str
    path: str
    language: str
    file_text: str | None
    span: tuple[int, int] | None = None
    normalized_sha: str = ""
    parse_reason: str = ""
    file_parse_error: bool = False
    probes: list[dict[str, Any]] | None = None
    linked_probes: list[str] = field(default_factory=list)
    labels: dict[str, list[Label]] = field(default_factory=dict)

    def in_span(self, start: int, end: int) -> bool:
        if self.span is None:
            return True
        lo, hi = self.span
        return start <= hi and end >= lo


@dataclass
class Verdict:
    scope: str
    status: str  # present | absent | error | not_applicable
    error_reason: str = ""
    matches: list[dict[str, Any]] = field(default_factory=list)
    tool_severity: str = ""
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "status": self.status,
            "error_reason": self.error_reason,
            "matches": self.matches,
            "tool_severity": self.tool_severity,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# Refs, sets and the detector-set hash
# ---------------------------------------------------------------------------


def parse_ref(ref: str) -> tuple[str, int, str | None]:
    match = _REF_RE.match(str(ref).strip())
    if match is None:
        msg = f"malformed detector ref {ref!r} (expected slug@version or slug@version#hash12)"
        raise DetectorRefError(msg)
    return match["slug"], int(match["version"]), match["hash"]


def resolve_refs(refs: Iterable[str]) -> list[DetectorSpec]:
    """The detector versions ``refs`` name, in the given order (duplicates dropped)."""
    from backend.generation.models import PatternDetector

    specs: list[DetectorSpec] = []
    seen: set[tuple[str, int]] = set()
    for ref in refs:
        slug, version, short_hash = parse_ref(ref)
        if (slug, version) in seen:
            continue
        seen.add((slug, version))
        row = PatternDetector.objects.filter(slug=slug, version=version).first()
        if row is None:
            msg = f"unknown detector {slug}@{version}"
            raise DetectorRefError(msg)
        if short_hash and not row.content_hash.startswith(short_hash):
            msg = f"detector {ref} does not match the stored content hash {row.content_hash[:12]}"
            raise DetectorRefError(msg)
        specs.append(DetectorSpec.from_row(row))
    return specs


def detector_set_hash(detectors: Iterable[DetectorSpec]) -> str:
    """``H({"version": 1, "detectors": sorted([[slug@version, content_hash], ...])})``.

    Order-independent and committed to every detector's full content hash; this is the value
    ``analysis_plan.patterns.detector_set_hash`` must hold for a planned PatternRun (section 7.9 step 9).
    """
    return identity.detector_set_hash((spec.ref, spec.content_hash) for spec in detectors)


def detector_set_hash_for_refs(refs: Iterable[str]) -> str:
    """:func:`detector_set_hash` of the versions ``refs`` name (resolved through the library)."""
    return detector_set_hash(resolve_refs(refs))


def requested_scopes(spec: DetectorSpec, scope_features: Sequence[str]) -> list[str]:
    """The scopes this detector reports under the pattern run's scope (``probe``/``manual``: span only)."""
    wanted = [s for s in SCOPES if s in scope_features and s in spec.scopes]
    if spec.engine in {"probe", "manual"}:
        return ["span"] if wanted else []
    return wanted


# ---------------------------------------------------------------------------
# Content validation (also usable by library lint)
# ---------------------------------------------------------------------------


def semgrep_rules(rule_text: str) -> list[dict[str, Any]]:
    """The rule dicts of a Semgrep detector's ``rule`` (a config with ``rules:``, a list, or one rule)."""
    data = yaml.safe_load(rule_text)
    if isinstance(data, dict) and isinstance(data.get("rules"), list):
        rules = data["rules"]
    elif isinstance(data, list):
        rules = data
    elif isinstance(data, dict):
        rules = [data]
    else:
        msg = "a semgrep rule must be a mapping, a list of rules or a config with 'rules'"
        raise ValueError(msg)  # noqa: TRY004 - a content error, reported as ValueError
    for i, rule in enumerate(rules):
        if not isinstance(rule, dict):
            msg = f"semgrep rule {i} is not a mapping"
            raise ValueError(msg)  # noqa: TRY004 - a content error, reported as ValueError
        if not rule.get("languages"):
            msg = f"semgrep rule {i} has no languages"
            raise ValueError(msg)
        if not rule.get("severity"):
            # Required by Semgrep; never defaulted here (tool_severity must be the rule's own label).
            msg = f"semgrep rule {i} has no severity"
            raise ValueError(msg)
    return [dict(rule) for rule in rules]


def validate_content(content: Mapping[str, Any]) -> list[str]:
    """Problems with a detector's ``rule`` for its engine (empty when valid)."""
    engine = content.get("engine")
    rule = str(content.get("rule") or "")
    problems: list[str] = []
    if engine == "regex":
        try:
            re.compile(rule, re.MULTILINE)
        except re.error as exc:
            problems.append(f"regex does not compile: {exc}")
        if not rule:
            problems.append("a regex detector needs a rule")
    elif engine == "ts_query":
        langs = [norm.canonical_language(lang) for lang in content.get("languages") or []]
        if not rule or not langs:
            problems.append("a ts_query detector needs a rule and at least one language")
        for lang in langs:
            try:
                _compile_query(lang, rule)
            except Exception as exc:  # noqa: BLE001 - tree-sitter raises several error types
                problems.append(f"query does not compile for {lang}: {exc}")
    elif engine == "semgrep":
        try:
            semgrep_rules(rule)
        except (ValueError, yaml.YAMLError) as exc:
            problems.append(f"semgrep rule invalid: {exc}")
    elif engine == "probe":
        if rule.startswith(PROBE_KIND_PREFIX):
            from typing import get_args

            from backend.generation.contracts.library import ProbeKind

            kind = rule.removeprefix(PROBE_KIND_PREFIX).strip()
            if kind not in get_args(ProbeKind):
                problems.append(f"unknown probe kind {kind!r}")
        elif not rule and not content.get("cwe"):
            problems.append("a probe detector needs a rule (kind:<kind>, a probe id or glob) or a CWE")
    elif engine == "manual":
        pass
    else:
        problems.append(f"unknown engine {engine!r}")
    return problems


# ---------------------------------------------------------------------------
# In-process engines
# ---------------------------------------------------------------------------


def _offset_line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _match(path: str, start: int, end: int, snippet: str, message: str = "") -> dict[str, Any]:
    return {
        "path": path,
        "start_line": start,
        "end_line": max(start, end),
        "snippet_sha": identity.text_sha(snippet),
        "message": message,
    }


def _scoped(variant: VariantInput, scopes: Sequence[str], matches: list[dict[str, Any]]) -> list[Verdict]:
    out: list[Verdict] = []
    for scope in scopes:
        chosen = (
            matches if scope == "file" else [m for m in matches if variant.in_span(m["start_line"], m["end_line"])]
        )
        out.append(Verdict(scope=scope, status="present" if chosen else "absent", matches=chosen[:MAX_MATCHES]))
    return out


def _all(scopes: Sequence[str], status: str, reason: str = "", message: str = "") -> list[Verdict]:
    return [Verdict(scope=s, status=status, error_reason=reason, message=message) for s in scopes]


def run_regex(spec: DetectorSpec, variant: VariantInput, scopes: Sequence[str]) -> list[Verdict]:
    if variant.file_text is None:
        return _all(scopes, "error", "tool_error", "the assembled file could not be read")
    try:
        compiled = re.compile(spec.rule, re.MULTILINE)
    except re.error as exc:
        return _all(scopes, "error", "tool_error", f"regex does not compile: {exc}")
    text = variant.file_text
    matches = [
        _match(
            variant.path,
            _offset_line(text, m.start()),
            _offset_line(text, max(m.start(), m.end() - 1)),
            m.group(0),
        )
        for m in compiled.finditer(text)
    ]
    return _scoped(variant, scopes, matches)


def _compile_query(language: str, rule: str) -> Any:
    from tree_sitter import Query

    return Query(norm.language_for(norm.canonical_language(language)), rule)


def run_ts_query(spec: DetectorSpec, variant: VariantInput, scopes: Sequence[str]) -> list[Verdict]:
    from tree_sitter import QueryCursor

    if variant.file_text is None:
        return _all(scopes, "error", "tool_error", "the assembled file could not be read")
    if variant.file_parse_error or variant.parse_reason == "parse_error":
        return _all(scopes, "error", "file_unparsed", "the slot file does not parse")
    try:
        query = _compile_query(variant.language, spec.rule)
        tree = norm.parse(variant.file_text, variant.language)
    except norm.NormalizationError as exc:
        return _all(scopes, "error", "tool_error", str(exc))
    except Exception as exc:  # noqa: BLE001 - QueryError and friends
        return _all(scopes, "error", "tool_error", f"query does not compile: {exc}")
    if tree.root_node.has_error:
        return _all(scopes, "error", "file_unparsed", "the slot file does not parse")
    matches: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for _pattern, captures in QueryCursor(query).matches(tree.root_node):
        nodes = captures.get("match") or [n for group in captures.values() for n in group]
        if not nodes:
            continue
        node = max(nodes, key=lambda n: n.end_byte - n.start_byte)
        key = (node.start_byte, node.end_byte)
        if key in seen:
            continue
        seen.add(key)
        matches.append(
            _match(
                variant.path,
                node.start_point[0] + 1,
                node.end_point[0] + 1,
                node.text.decode("utf-8", errors="replace"),
            ),
        )
    matches.sort(key=lambda m: (m["start_line"], m["end_line"]))
    return _scoped(variant, scopes, matches)


def _probe_selected(spec: DetectorSpec, probe: Mapping[str, Any], linked: Sequence[str]) -> bool:
    probe_id = str(probe.get("id") or "")
    if linked and probe_id not in linked:
        return False
    if spec.rule.startswith(PROBE_KIND_PREFIX):
        return str(probe.get("kind") or "") == spec.rule.removeprefix(PROBE_KIND_PREFIX).strip()
    if spec.rule:
        return fnmatch.fnmatchcase(probe_id, spec.rule)
    cwes = {str(c).lower() for c in spec.content.get("cwe") or []}
    return str(probe.get("cwe") or "").lower() in cwes


def run_probe(spec: DetectorSpec, variant: VariantInput, scopes: Sequence[str]) -> list[Verdict]:  # noqa: PLR0911
    if not scopes:
        return []
    if variant.probes is None:
        return _all(scopes, "not_applicable", message="probes not measured")
    selected = [p for p in variant.probes if _probe_selected(spec, p, variant.linked_probes)]
    if not selected:
        return _all(scopes, "not_applicable", message="no matching probe ran")
    statuses = [str(p.get("status")) for p in selected]
    if "exploited" in statuses:
        evidence = [p for p in selected if p.get("status") == "exploited"]
        matches = [
            {
                "path": variant.path,
                "start_line": variant.span[0] if variant.span else 1,
                "end_line": variant.span[1] if variant.span else 1,
                "snippet_sha": None,
                "message": f"probe {p.get('id')} exploited",
            }
            for p in evidence
        ]
        return [Verdict(scope=s, status="present", matches=matches[:MAX_MATCHES]) for s in scopes]
    if "error" in statuses:
        return _all(scopes, "error", "tool_error", "a matching probe errored")
    if "not_exploited" in statuses:
        return _all(scopes, "absent")
    reasons = sorted({str(p.get("skip_reason") or "skipped") for p in selected})
    return _all(scopes, "not_applicable", message="probe skipped: " + ", ".join(reasons))


def resolve_labels(labels: Sequence[Label]) -> tuple[str, list[str]]:
    """``(status, labelers)``: the majority of present/absent codes; ties and only-unsure are n/a."""
    present = [label.labeler for label in labels if label.value == "present"]
    absent = [label.labeler for label in labels if label.value == "absent"]
    if len(present) > len(absent):
        return "present", present
    if len(absent) > len(present):
        return "absent", absent
    return "not_applicable", [label.labeler for label in labels]


def run_manual(spec: DetectorSpec, variant: VariantInput, scopes: Sequence[str]) -> list[Verdict]:
    if not scopes:
        return []
    if not variant.normalized_sha:
        return _all(scopes, "not_applicable", message="unparsable variant: no cluster key to label")
    labels = variant.labels.get(spec.ref, [])
    if not labels:
        return _all(scopes, "not_applicable", message="not coded")
    status, labelers = resolve_labels(labels)
    message = "manual coding by " + ", ".join(sorted(set(labelers)))
    matches: list[dict[str, Any]] = []
    if status == "present":
        start, end = variant.span if variant.span else (1, max(1, (variant.file_text or "").count("\n") + 1))
        matches = [
            {"path": variant.path, "start_line": start, "end_line": end, "snippet_sha": None, "message": message},
        ]
    return [Verdict(scope=s, status=status, matches=matches, message=message) for s in scopes]


_IN_PROCESS = {"regex": run_regex, "ts_query": run_ts_query, "probe": run_probe, "manual": run_manual}


def run_in_process(spec: DetectorSpec, variant: VariantInput, scopes: Sequence[str]) -> list[Verdict]:
    """One in-process detector over one variant; a language the detector does not cover is n/a."""
    if not scopes:
        return []
    if not spec.applies_to(variant.language):
        return _all(scopes, "not_applicable", message=f"detector does not cover {variant.language}")
    try:
        return _IN_PROCESS[spec.engine](spec, variant, scopes)
    except Exception as exc:  # noqa: BLE001 - an engine crash is an error verdict, never absent
        return _all(scopes, "error", "tool_error", f"{spec.engine} failed: {exc}"[:300])


# ---------------------------------------------------------------------------
# Semgrep (batched through the analysis rulepack)
# ---------------------------------------------------------------------------


def _error_reason(text: str) -> str:
    lowered = text.lower()
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if "syntax" in lowered or "parse" in lowered:
        return "parse_error"
    return "tool_error"


def _dotted(data: Mapping[str, Any], path: str) -> Any:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _tool_severity(spec: DetectorSpec, result: Mapping[str, Any]) -> str:
    """The rule's own label, verbatim (``extra.severity`` or ``tool_severity_field``)."""
    path = spec.content.get("tool_severity_field") or "extra.severity"
    value = _dotted(result, str(path))
    return "" if value is None else str(value)[:32]


@dataclass
class SemgrepBatch:
    """The outcome of one rule-pack run: verdicts by ``(run_id, detector ref)`` and the tool version."""

    verdicts: dict[tuple[str, str], list[Verdict]]
    version: str = ""
    error: str = ""


def run_semgrep(
    specs: Sequence[DetectorSpec],
    variants: Sequence[VariantInput],
    scope_features: Sequence[str],
    *,
    user: Any = None,
    runner: Any = None,
) -> SemgrepBatch:
    """Every Semgrep detector over every variant's assembled file in one rule-pack invocation."""
    from backend.analysis.services import rulepack

    verdicts: dict[tuple[str, str], list[Verdict]] = {}
    rules: list[dict[str, Any]] = []
    broken: dict[str, str] = {}
    for d_index, spec in enumerate(specs):
        try:
            for r_index, source in enumerate(semgrep_rules(spec.rule)):
                rule = dict(source)
                rule["id"] = f"llmlab-d{d_index}-r{r_index}"
                rule.setdefault("message", spec.content.get("description", spec.feature_key))
                rules.append(rule)
        except (ValueError, yaml.YAMLError) as exc:
            broken[spec.ref] = f"semgrep rule invalid: {exc}"[:300]

    files: dict[str, str] = {}
    keys: dict[str, str] = {}
    for variant in variants:
        if variant.file_text is not None:
            key = f"{variant.run_id}/{variant.path}"
            files[key] = variant.file_text
            keys[variant.run_id] = key
    run = rulepack.run_semgrep_rules(files, rules, user=user, runner=runner) if rules and files else None

    for d_index, spec in enumerate(specs):
        for variant in variants:
            scopes = requested_scopes(spec, scope_features)
            if not scopes:
                continue
            out_key = (variant.run_id, spec.ref)
            if not spec.applies_to(variant.language):
                verdicts[out_key] = _all(
                    scopes,
                    "not_applicable",
                    message=f"detector does not cover {variant.language}",
                )
                continue
            if spec.ref in broken:
                verdicts[out_key] = _all(scopes, "error", "tool_error", broken[spec.ref])
                continue
            if variant.file_text is None:
                verdicts[out_key] = _all(scopes, "error", "tool_error", "the assembled file could not be read")
                continue
            if variant.file_parse_error or variant.parse_reason == "parse_error":
                verdicts[out_key] = _all(scopes, "error", "file_unparsed", "the slot file does not parse")
                continue
            if run is None:
                verdicts[out_key] = _all(scopes, "error", "tool_error", "semgrep did not run")
                continue
            key = keys[variant.run_id]
            problem = run.file_error(key)
            if problem:
                verdicts[out_key] = _all(scopes, "error", _error_reason(problem), problem)
                continue
            mine = []
            for result in run.results_for(key):
                m = _RULE_ID_RE.search(str(result.get("check_id", "")))
                if m is not None and int(m["d"]) == d_index:
                    mine.append(result)
            mine.sort(key=lambda r: (r.get("start", {}).get("line", 0), r.get("end", {}).get("line", 0)))
            matches = [
                {
                    "path": variant.path,
                    "start_line": max(1, int(r.get("start", {}).get("line", 1))),
                    "end_line": max(1, int(r.get("end", {}).get("line", r.get("start", {}).get("line", 1)))),
                    "snippet_sha": identity.text_sha(str(r.get("extra", {}).get("lines", "")))
                    if r.get("extra", {}).get("lines")
                    else None,
                    "message": str(r.get("extra", {}).get("message", ""))[:300],
                    "_label": _tool_severity(spec, r),
                }
                for r in mine
            ]
            out: list[Verdict] = []
            for scope in scopes:
                chosen = (
                    matches
                    if scope == "file"
                    else [m for m in matches if variant.in_span(m["start_line"], m["end_line"])]
                )
                first_label = chosen[0]["_label"] if chosen else ""
                clean = [{k: v for k, v in m.items() if k != "_label"} for m in chosen[:MAX_MATCHES]]
                out.append(
                    Verdict(
                        scope=scope,
                        status="present" if chosen else "absent",
                        matches=clean,
                        tool_severity=first_label,
                    ),
                )
            verdicts[out_key] = out
    return SemgrepBatch(verdicts=verdicts, version=run.version if run else "", error=run.error if run else "")


def run_all(
    specs: Sequence[DetectorSpec],
    variants: Sequence[VariantInput],
    scope_features: Sequence[str],
    *,
    user: Any = None,
    runner: Any = None,
) -> tuple[dict[tuple[str, str], list[Verdict]], dict[str, str]]:
    """Every detector over every variant: ``(verdicts by (run_id, ref), tool versions by engine)``."""
    verdicts: dict[tuple[str, str], list[Verdict]] = {}
    versions: dict[str, str] = {}
    semgrep_specs = [s for s in specs if s.engine == "semgrep"]
    if semgrep_specs and variants:
        batch = run_semgrep(semgrep_specs, variants, scope_features, user=user, runner=runner)
        verdicts.update(batch.verdicts)
        versions["semgrep"] = batch.version or "unknown"
    for spec in specs:
        if spec.engine == "semgrep":
            continue
        if spec.engine == "ts_query":
            import tree_sitter

            versions["ts_query"] = f"tree-sitter {getattr(tree_sitter, '__version__', 'unknown')}"
        elif spec.engine == "regex":
            versions["regex"] = "python re"
        for variant in variants:
            scopes = requested_scopes(spec, scope_features)
            if scopes:
                verdicts[(variant.run_id, spec.ref)] = run_in_process(spec, variant, scopes)
    return verdicts, versions
