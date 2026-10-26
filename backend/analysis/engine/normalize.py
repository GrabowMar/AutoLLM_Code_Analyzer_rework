"""Map tool-native findings onto the shared vocabulary (owner: sample/measure stage).

Severity, category and CWE come from versioned mapping tables in
``data/mappings/`` (``severity.yaml``, ``categories.yaml``, ``cwe.yaml``,
``rule_equivalence.yaml``); their hashes are part of the frozen plan. Parse
errors are coverage, never findings (adapters report them in
ToolParse.coverage); :func:`is_parse_error` recognises the ones that slip
through and :func:`normalize_all` drops them.

Tables are loaded once per process and revalidated on every use (file
stat signature); :func:`mapping_hashes` reports the sha256 of the bytes the
process is actually mapping with, and every finding records the combined
digest it was mapped under (``extra["mapping_digest"]``), so a plan frozen
under other tables is detectable (``measurement`` flags ``mapping_drift``).

Per finding:

* **severity** — impact level from the tool's table (never a configuration
  level such as ESLint's 2/1); a ``message_rules`` entry (the tool's own
  context in its message, e.g. ruff S602 "seems safe" vs "identified")
  wins; else ruff ``S###`` takes the bandit-equivalent default severity
  (``extra["severity_basis"] = "bandit_default"``: a proxy that a merged
  bandit finding's context-aware severity overrides in ``dedup``); semgrep's
  impact x likelihood matrix wins when both are declared; secrets on
  example/test files or with placeholder values drop to ``low``.
* **category** — by tool and rule (prefix) table; **CWE** — the tool's own,
  else the curated table.
* **file_origin** — ``SampleFile.line_origin(line)`` (scaffold for unchanged
  lines of agent-modified files); **in_measured_scope** — the file is
  measured and the line's origin is a measured origin.
* **counted** — static: in scope, not a parse error, and not an uncounted
  category (style unless ``outcomes.count_style``; complexity/duplication
  unless ``outcomes.count_metric_findings``). AI: always False here, only
  consensus may count an AI finding.
* **fingerprint** — sha256 of tool, rule family, path and the
  whitespace-normalised source line (no line number: stable under shifts).
* **redaction** — a ``secrets`` finding never carries code: its snippet is
  dropped (even one the tool supplied, e.g. bandit's context lines) and the
  string literals of the flagged lines, and quoted values in its texts, are
  masked (bandit B105-B107 put the password in the message). Any other
  finding's snippet has the quoted values of credential-looking lines
  masked, so exports never carry a plaintext secret through a snippet.

AI findings (``tool_slug == "ai_review"``) are expected as a RawFinding with
``native_severity`` = the model's severity, ``extra["category"]`` (or
``rule_id``) = the model's category, ``cwe`` = ``["CWE-n"]`` or ``[]``,
``native_confidence`` = the model's 0..1 confidence as a string, and
optionally ``extra["grounding"]`` / ``extra["sample_index"]``.
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from backend.analysis.engine.catalog import AI_TOOL_SLUG
from backend.analysis.engine.sample import effective_scope
from backend.analysis.engine.sample import glob_match
from backend.analysis.engine.sloc import split_lines
from backend.analysis.engine.types import SEVERITIES
from backend.analysis.engine.types import NormalizedFinding
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import Sample
from backend.analysis.engine.types import SampleFile

MAPPINGS_DIR = Path(__file__).resolve().parent.parent / "data" / "mappings"
MAPPING_FILES = {
    "severity": "severity.yaml",
    "categories": "categories.yaml",
    "cwe": "cwe.yaml",
    "rule_equivalence": "rule_equivalence.yaml",
}
FINDING_CATEGORIES = (
    "security",
    "secrets",
    "bug",
    "type",
    "maintainability",
    "dead_code",
    "duplication",
    "complexity",
    "style",
    "container",
    "dependency",
    "other",
)
GROUNDINGS = ("exact", "relocated", "ungrounded", "invalid_file", "out_of_range")
_COUNT_FLAGS = ("count_style", "count_metric_findings")
_CWE_RE = re.compile(r"CWE[-_ :]?(\d+)", re.IGNORECASE)
_TITLE_MAX = 500


# --------------------------------------------------------------------------
# Mapping tables
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Loaded:
    signature: tuple[int, ...]
    sha256: str
    data: dict[str, Any]


_LOADED: dict[str, _Loaded] = {}
_LOAD_LOCK = threading.Lock()


def _signature(path: Path) -> tuple[int, ...]:
    st = path.stat()
    return (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino)


def _loaded(name: str, *, verify: bool = False) -> _Loaded:
    """The table *name* as currently on disk: parsed once, re-read when the file changes.

    A long-lived worker therefore never maps with tables other than the ones
    :func:`mapping_hashes` reports (a plan frozen now records exactly the
    bytes this process maps with), and an edited table takes effect at once
    instead of whenever an unrelated code change restarts the process. The
    stat signature is checked on every use; *verify* also compares the bytes
    (for :func:`mapping_hashes`, so two edits within one timestamp tick are
    caught where it matters).
    """
    path = MAPPINGS_DIR / MAPPING_FILES[name]
    signature = _signature(path)
    current = _LOADED.get(name)
    if current is not None and current.signature == signature and not verify:
        return current
    with _LOAD_LOCK:
        current = _LOADED.get(name)
        if current is not None and current.signature == signature and not verify:
            return current
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if current is not None and current.sha256 == digest:
            loaded = _Loaded(signature, digest, current.data)
        else:
            data = yaml.safe_load(raw) or {}
            if not isinstance(data, dict):
                msg = f"mapping table {name} is not a mapping"
                raise TypeError(msg)
            loaded = _Loaded(signature, digest, data)
        _LOADED[name] = loaded
        return loaded


def _table(name: str) -> dict[str, Any]:
    return _loaded(name).data


# Values derived from a table (compiled regexes, indexes), keyed by the
# table's sha256 so an edited table never serves stale derivations.
_DERIVED: dict[tuple[str, ...], Any] = {}
_DERIVED_MAX = 16384


def _derived(key: tuple[str, ...], build) -> Any:
    if key in _DERIVED:
        return _DERIVED[key]
    value = build()
    if len(_DERIVED) >= _DERIVED_MAX:
        _DERIVED.clear()
    _DERIVED[key] = value
    return value


def mapping_hashes() -> dict[str, str]:
    """sha256 of each mapping table as this process maps with it (frozen into plans).

    The bytes are re-read and the tables reloaded if they changed, so the
    hashes always equal both the files on disk and the tables in use.
    """
    return {name: _loaded(name, verify=True).sha256 for name in MAPPING_FILES}


def _loaded_hashes() -> dict[str, str]:
    return {name: _loaded(name).sha256 for name in MAPPING_FILES}


def mapping_digest(hashes: dict[str, Any] | None = None) -> str:
    """One digest over the mapping tables (*hashes*: e.g. a plan's ``mapping_hashes``;
    default: the tables this process maps with). Stamped on every finding."""
    hashes = _loaded_hashes() if hashes is None else hashes
    material = "".join(f"{name}={hashes.get(name)}\n" for name in sorted(hashes))
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def mapping_drift(expected: dict[str, Any] | None) -> list[str]:
    """Tables whose loaded sha256 differs from *expected* (a plan's ``mapping_hashes``).

    For the executor to refuse, before normalizing, a run frozen under other
    tables; [] when *expected* is empty (plans without the record).
    """
    if not isinstance(expected, dict) or not expected:
        return []
    loaded = mapping_hashes()
    return sorted(name for name in set(loaded) | set(expected) if loaded.get(name) != expected.get(name))


def _tool_table(name: str, tool_slug: str) -> dict[str, Any]:
    tools = _table(name).get("tools") or {}
    table = tools.get(tool_slug)
    return table if isinstance(table, dict) else {}


def _rule_lookup(table: dict[str, Any], rule: str) -> Any:
    """``rules`` exact match, else the longest matching ``prefixes`` key; None if neither."""
    rules = table.get("rules") or {}
    if rule in rules:
        return rules[rule]
    best = None
    for prefix, value in (table.get("prefixes") or {}).items():
        if not rule.startswith(prefix) or (best is not None and len(prefix) <= len(best[0])):
            continue
        # A letter prefix names a whole linter code: ruff "S" covers S608 but
        # not SIM108 (that is "SIM"), "E" covers E501 but not ERA001.
        if prefix[-1].isalpha() and len(rule) > len(prefix) and rule[len(prefix)].isalpha():
            continue
        best = (prefix, value)
    return best[1] if best else None


def _bandit_code(tool_slug: str, rule: str) -> str | None:
    """The bandit test a ruff ``S###`` rule re-implements (``S608`` -> ``B608``)."""
    if tool_slug == "ruff" and re.fullmatch(r"S\d{3}", rule):
        return "B" + rule[1:]
    if tool_slug == "bandit" and re.fullmatch(r"B\d{3}", rule):
        return rule
    return None


# --------------------------------------------------------------------------
# Severity / category / CWE / confidence
# --------------------------------------------------------------------------


def _band(bands: list[dict[str, Any]], value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    for band in bands or []:
        if value >= band.get("min", 0):
            return band.get("severity")
    return None


def _metadata(raw: RawFinding) -> dict[str, Any]:
    meta = raw.extra.get("metadata") if isinstance(raw.extra, dict) else None
    return meta if isinstance(meta, dict) else {}


def _message_severity(table: dict[str, Any], rule: str, raw: RawFinding) -> str | None:
    """``message_rules[rule]``: the first ``{pattern, severity}`` whose regex matches the tool's message."""
    rules = (table.get("message_rules") or {}).get(rule)
    if not isinstance(rules, list):
        return None
    text = " ".join(part for part in (raw.message, raw.title, raw.description) if part)
    for entry in rules:
        if not isinstance(entry, dict):
            continue
        pattern = entry.get("pattern")
        severity = entry.get("severity")
        if isinstance(pattern, str) and severity in SEVERITIES and re.search(pattern, text, re.IGNORECASE):
            return severity
    return None


def _native_severity(tool_slug: str, raw: RawFinding) -> str:
    return _severity(tool_slug, raw)[0]


def _severity(tool_slug: str, raw: RawFinding) -> tuple[str, str]:  # noqa: PLR0911 - one lookup step per return
    """(severity, basis): basis ``bandit_default`` marks a ruff S### value proxied from bandit's
    default table (no context); every other basis is the tool's own evidence or table."""
    table = _tool_table("severity", tool_slug)
    rule = raw.rule_id or ""
    native = (raw.native_severity or "").strip()
    by_message = _message_severity(table, rule, raw) if table else None
    if by_message:
        return by_message, "message"
    if tool_slug == "semgrep":
        meta = _metadata(raw)
        impact = str(meta.get("impact") or "").upper()
        likelihood = str(meta.get("likelihood") or "").upper()
        matrix = table.get("impact_likelihood") or {}
        if impact in matrix and likelihood in (matrix.get(impact) or {}):
            return matrix[impact][likelihood], "impact_likelihood"
    for slug, key, value_key in (
        ("vulture", "confidence_bands", "confidence"),
        ("lizard", "ccn_bands", "ccn"),
        ("jscpd", "lines_bands", "lines"),
    ):
        if tool_slug == slug:
            value = _band(table.get(key) or [], _int(raw.extra.get(value_key)))
            if value:
                return value, "band"
    if table:
        value = _rule_lookup(table, rule)
        if value == "bandit_equivalent":
            code = _bandit_code(tool_slug, rule)
            value = (_table("severity").get("bandit_rules") or {}).get(code) if code else None
            return (value if value in SEVERITIES else "medium"), "bandit_default"
        if value in SEVERITIES:
            return value, "rule"
        natives = table.get("native") or {}
        for token in (native, native.upper(), native.lower()):
            if token in natives:
                return natives[token], "native"
        default = table.get("default")
        if default in SEVERITIES:
            return default, "default"
    generic = _table("severity").get("generic") or {}
    value = (generic.get("native") or {}).get(native.lower())
    if value:
        return value, "native"
    return (generic.get("default") or "medium"), "default"


def _category(tool_slug: str, raw: RawFinding, cwe: list[str]) -> str:  # noqa: PLR0911 - one lookup step per return
    rule = raw.rule_id or ""
    if tool_slug == AI_TOOL_SLUG:
        value = raw.extra.get("category") if isinstance(raw.extra, dict) else None
        value = value or rule
        return value if value in FINDING_CATEGORIES else "other"
    table = _tool_table("categories", tool_slug)
    if tool_slug == "semgrep":
        return _semgrep_category(table, raw, cwe)
    value = _rule_lookup(table, rule) if table else None
    if value in FINDING_CATEGORIES:
        return value
    native = (raw.native_severity or "").strip().lower()
    natives = table.get("native") or {}
    if native in natives:
        return natives[native]
    if table.get("core_default") and rule and "/" not in rule:
        return table["core_default"]
    default = table.get("default")
    if default in FINDING_CATEGORIES:
        return default
    return "security" if cwe else "other"


def _semgrep_category(table: dict[str, Any], raw: RawFinding, cwe: list[str]) -> str:
    meta = _metadata(raw)
    rule = (raw.rule_id or "").lower()
    if "CWE-798" in cwe or ".secrets." in f".{rule}." or "secret" in rule.rsplit(".", 1)[-1]:
        return "secrets"
    category = str(meta.get("category") or "").lower()
    by_meta = table.get("metadata") or {}
    if category in by_meta:
        return by_meta[category]
    segments = rule.split(".")
    for key, value in (table.get("id_segments") or {}).items():
        if key in segments:
            return value
    if cwe:
        return table.get("with_cwe") or "security"
    return table.get("default") or "maintainability"


def normalize_cwe(values: Any) -> list[str]:
    """``["CWE-89: Improper ...", "cwe-89", 89]`` -> ``["CWE-89"]`` (sorted, unique)."""
    if isinstance(values, (str, int)):
        values = [values]
    out: set[int] = set()
    for value in values or []:
        if isinstance(value, int) and not isinstance(value, bool):
            out.add(value)
            continue
        for match in _CWE_RE.finditer(str(value)):
            out.add(int(match.group(1)))
    return [f"CWE-{n}" for n in sorted(out)]


def _cwe(tool_slug: str, raw: RawFinding) -> list[str]:
    own = normalize_cwe(raw.cwe) or normalize_cwe(_metadata(raw).get("cwe"))
    if own or tool_slug == AI_TOOL_SLUG:
        return own
    table = _tool_table("cwe", tool_slug)
    rule = raw.rule_id or ""
    if rule in table:
        return normalize_cwe(table[rule])
    code = _bandit_code(tool_slug, rule)
    if code:
        bandit = _tool_table("cwe", "bandit")
        if code in bandit:
            return normalize_cwe(bandit[code])
    for pattern, value in table.items():
        if any(ch in pattern for ch in "*?[") and fnmatch.fnmatchcase(rule, pattern):
            return normalize_cwe(value)
    return []


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        m = re.search(r"\d+", value)
        return int(m.group(0)) if m else None
    return None


def _confidence(tool_slug: str, raw: RawFinding) -> str:
    native = (raw.native_confidence or "").strip()
    if tool_slug == AI_TOOL_SLUG:
        try:
            value = float(native)
        except ValueError:
            value = None
        if value is not None:
            return "high" if value >= 0.8 else "medium" if value >= 0.5 else "low"
    if tool_slug == "vulture":
        pct = _int(raw.extra.get("confidence"))
        if pct is not None:
            return "high" if pct >= 90 else "medium" if pct >= 70 else "low"
    if tool_slug == "semgrep" and not native:
        native = str(_metadata(raw).get("confidence") or "")
    lowered = native.lower()
    return lowered if lowered in ("high", "medium", "low") else "medium"


# --------------------------------------------------------------------------
# Secrets
# --------------------------------------------------------------------------


def _placeholder_regex() -> re.Pattern[str] | None:
    loaded = _loaded("severity")
    return _derived(
        ("placeholders", loaded.sha256),
        lambda: _compile_placeholders((loaded.data.get("secrets") or {}).get("placeholder_patterns") or []),
    )


def _compile_placeholders(patterns: list[str]) -> re.Pattern[str] | None:
    if not patterns:
        return None
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE)


def secret_downgrade_reason(path: str, line_text: str) -> str:
    """Why a secret finding is documentation rather than a leak ("" if it is not)."""
    config = _table("severity").get("secrets") or {}
    for pattern in config.get("example_path_globs") or []:
        if glob_match(path, pattern):
            return "example_file"
    regex = _placeholder_regex()
    if regex is not None and line_text and regex.search(line_text):
        return "placeholder_value"
    return ""


# --------------------------------------------------------------------------
# Parse errors
# --------------------------------------------------------------------------


def is_parse_error(tool_slug: str, raw: RawFinding) -> bool:  # noqa: PLR0911 - one rule per tool
    """A tool's report that a file could not be parsed (coverage, never a finding)."""
    if isinstance(raw.extra, dict) and (raw.extra.get("parse_error") or raw.extra.get("fatal")):
        return True
    rule = (raw.rule_id or "").strip()
    message = (raw.message or "").lower()
    if tool_slug == "ruff":
        return not rule or rule.upper().startswith("E9") or rule.lower() in ("invalid-syntax", "syntax-error")
    if tool_slug == "pylint":
        return rule in ("E0001", "syntax-error", "F0001", "fatal", "F0002", "astroid-error", "F0010", "parse-error")
    if tool_slug == "eslint":
        return not rule
    if tool_slug == "mypy":
        return rule == "syntax"
    if tool_slug == "vulture":
        return "syntax" in rule.lower() or message.startswith("invalid syntax")
    return False


# --------------------------------------------------------------------------
# Fingerprint
# --------------------------------------------------------------------------


def rule_family(tool_slug: str, rule_id: str, *, category: str = "", cwe: list[str] | None = None) -> str:
    """The rule identity a fingerprint keys on (stable across rule-pack namespaces)."""
    if tool_slug == AI_TOOL_SLUG:
        return f"ai:{category or 'other'}:{(cwe or ['-'])[0]}"
    if tool_slug == "semgrep" and rule_id:
        # Vendored packs prefix ids with their directory; the rule name is the last segment.
        return rule_id.rsplit(".", 1)[-1]
    return rule_id or ""


def fingerprint(*, tool_slug: str, rule_family: str, path: str, line_text: str) -> str:
    """Content-based, line-shift-stable fingerprint (sha256 hex)."""
    normalized = " ".join((line_text or "").split())
    material = "\x1f".join((tool_slug, rule_family, path, normalized))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Equivalence (used by dedup)
# --------------------------------------------------------------------------


def _equivalence_index(loaded: _Loaded) -> dict[str, list[tuple[str, str]]]:
    """tool -> [(pattern, class)] from rule_equivalence.yaml (per table digest)."""

    def build() -> dict[str, list[tuple[str, str]]]:
        index: dict[str, list[tuple[str, str]]] = {}
        for name, members in (loaded.data.get("classes") or {}).items():
            for member in members or []:
                tool, _, pattern = str(member).partition(":")
                if tool and pattern:
                    index.setdefault(tool, []).append((pattern.lower(), name))
        return index

    return _derived(("equivalence", loaded.sha256), build)


def equivalence_classes(tool_slug: str, rule_id: str) -> frozenset[str]:
    """Equivalence classes a (tool, rule) belongs to (case-insensitive globs)."""
    loaded = _loaded("rule_equivalence")
    key = ("classes", loaded.sha256, tool_slug, rule_id or "")
    cached = _DERIVED.get(key)
    if cached is not None:
        return cached
    rule = (rule_id or "").lower()
    value = frozenset(
        name for pattern, name in _equivalence_index(loaded).get(tool_slug, []) if fnmatch.fnmatchcase(rule, pattern)
    )
    return _derived(key, lambda: value)


def emitted_categories(tool_slug: str, tool_category: str = "") -> list[str]:
    """Finding categories a tool can emit (for per-category availability)."""
    table = _table("categories")
    emits = (table.get("emits") or {}).get(tool_slug)
    if emits:
        return list(emits)
    return list((table.get("tool_category_emits") or {}).get(tool_category) or [])


def uncounted_categories(outcomes: dict[str, Any] | None) -> set[str]:
    """Categories not counted under the plan's outcome rules."""
    flags = outcomes or {}
    rules = _table("categories").get("uncounted") or {}
    return {category for category, flag in rules.items() if not flags.get(flag)}


# --------------------------------------------------------------------------
# normalize
# --------------------------------------------------------------------------


def finding_path(path: str) -> str:
    """A tool-reported path relative to the sample root (``/src/`` and ``./`` stripped)."""
    p = (path or "").replace("\\", "/").strip()
    for prefix in ("/src/", "src://"):
        if p.startswith(prefix):
            p = p[len(prefix) :]
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _line_text(file: SampleFile | None, line: int | None) -> str:
    """Line *line* of *file* in the tools' numbering (``sloc.split_lines``: ``\\n`` only)."""
    if file is None or line is None or line < 1:
        return ""
    lines = split_lines(file.text)
    return lines[line - 1] if line <= len(lines) else ""


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------

_MASK = "***"
_MIN_SECRET_CHARS = 4
_QUOTED_RE = re.compile(r"""(['"])((?:\\.|(?!\1).)*)\1""")
_LITERAL_RE = re.compile(r"""(['"`])((?:\\.|(?!\1).)*)\1""")
# A credential-looking name assigned (``=``, ``:``) a value: DB_PASSWORD = "x",
# "api_key": "x", secretKey: 'x', login(password="x").
_CREDENTIAL_ASSIGN_RE = re.compile(
    r"(?:(?i:pass(?:word|wd)?|pwd|secret|token|credentials?|api[_-]?key|[a-z0-9]+_key)|[a-z0-9]+Key)['\"`]?\s*[=:]",
)
# Well-known credential formats, masked wherever they appear.
_TOKEN_RE = re.compile(
    r"AKIA[0-9A-Z]{16}"
    r"|[sr]k_(?:live|test)_[0-9A-Za-z]{8,}"
    r"|gh[pousr]_[0-9A-Za-z]{20,}"
    r"|github_pat_[0-9A-Za-z_]{20,}"
    r"|xox[abpr]-[0-9A-Za-z-]{10,}"
    r"|AIza[0-9A-Za-z_-]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----",
)


def _mask_quoted(text: str, pattern: re.Pattern[str] = _QUOTED_RE) -> str:
    return pattern.sub(
        lambda m: f"{m.group(1)}{_MASK}{m.group(1)}" if len(m.group(2)) >= _MIN_SECRET_CHARS else m.group(0),
        text,
    )


def redact_snippet(snippet: str) -> str:
    """*snippet* with the quoted values of credential-looking lines, and known token formats, masked."""
    if not snippet:
        return snippet
    out = []
    for line in snippet.split("\n"):
        masked = _mask_quoted(line, _LITERAL_RE) if _CREDENTIAL_ASSIGN_RE.search(line) else line
        out.append(_TOKEN_RE.sub(_MASK, masked))
    return "\n".join(out)


def _flagged_literals(file: SampleFile | None, line: int | None, end_line: int | None) -> list[str]:
    """String literal values (>= 4 chars) on the flagged lines, longest first."""
    if file is None or line is None:
        return []
    lines = split_lines(file.text)
    values: set[str] = set()
    for number in range(line, min(end_line or line, line + 20, len(lines)) + 1):
        for match in _LITERAL_RE.finditer(lines[number - 1]):
            if len(match.group(2)) >= _MIN_SECRET_CHARS:
                values.add(match.group(2))
    return sorted(values, key=len, reverse=True)


def _redact_secret_text(text: str, literals: list[str], *, mask_quoted: bool) -> str:
    """A secrets finding's text with its secret values masked: the string literals of the
    flagged lines, known token formats and (*mask_quoted*, for tool-templated messages such
    as bandit's ``Possible hardcoded password: '<value>'``) every quoted value."""
    if not text:
        return text
    for value in literals:
        text = text.replace(value, _MASK)
    if mask_quoted:
        text = _mask_quoted(text)
    return _TOKEN_RE.sub(_MASK, text)


def normalize(
    tool_slug: str,
    raw: RawFinding,
    sample: Sample,
    scope: dict[str, Any],
    *,
    outcomes: dict[str, Any] | None = None,
) -> NormalizedFinding:
    """Normalize one finding: severity, category, CWE, confidence, file origin
    (line-level), measured scope, counted (in scope and not style unless the
    plan counts style), fingerprint.

    *outcomes* is the plan's outcome config (``count_style``,
    ``count_metric_findings``). The same flags are also read from *scope*
    (the executor folds ``outcomes.count_style`` into the scope it passes);
    *outcomes* wins. Absent everywhere means both are off.
    """
    eff = effective_scope(scope)
    count_flags = {key: scope[key] for key in _COUNT_FLAGS if isinstance(scope, dict) and key in scope}
    count_flags.update({key: outcomes[key] for key in _COUNT_FLAGS if outcomes and key in outcomes})
    path = finding_path(raw.file_path)
    file = sample.by_path().get(path)
    line = raw.line if isinstance(raw.line, int) and raw.line >= 1 else None
    end_line = raw.end_line if isinstance(raw.end_line, int) and raw.end_line >= (line or 1) else line
    is_ai = tool_slug == AI_TOOL_SLUG
    extra: dict[str, Any] = dict(raw.extra) if isinstance(raw.extra, dict) else {}
    if raw.message:
        extra.setdefault("message", raw.message)

    cwe = _cwe(tool_slug, raw)
    category = _category(tool_slug, raw, cwe)
    severity, basis = _severity(tool_slug, raw)
    if basis in ("bandit_default", "message"):
        extra["severity_basis"] = basis
    extra["mapping_digest"] = mapping_digest()
    confidence = _confidence(tool_slug, raw)
    text = _line_text(file, line)

    if not is_ai and category == ((_table("severity").get("secrets") or {}).get("category") or "secrets"):
        reason = secret_downgrade_reason(path, text)
        if reason:
            downgrade = (_table("severity").get("secrets") or {}).get("downgrade_to") or "low"
            if SEVERITIES.index(downgrade) > SEVERITIES.index(severity):
                extra["severity_before_downgrade"] = severity
                severity = downgrade
            extra["secret_downgrade"] = reason
            confidence = "low"

    origin = file.line_origin(line) if file is not None else "unknown"
    if file is None:
        extra["unknown_file"] = True
    in_scope = bool(
        file is not None and file.included and file.measured and origin in (eff.get("measured_origins") or ()),
    )
    parse_error = not is_ai and is_parse_error(tool_slug, raw)
    if parse_error:
        extra["parse_error"] = True
        category = "other"
        severity = "info"
    counted = (not is_ai) and in_scope and not parse_error and category not in uncounted_categories(count_flags)

    family = rule_family(tool_slug, raw.rule_id or "", category=category, cwe=cwe)
    extra["rule_family"] = family
    title = (raw.title or raw.message or raw.rule_id or tool_slug)[:_TITLE_MAX]
    description = raw.description or raw.message or ""
    suggestion = raw.suggestion or ""
    snippet = raw.snippet or ""
    if snippet.strip() == "requires login":
        snippet = ""  # semgrep CE without login: a placeholder, not code
    if category == "secrets":
        # A secret finding never carries code, not even a snippet the tool
        # supplied (bandit's context lines), and its texts lose the secret
        # values (bandit B105-B107 quote the password in the message): secret
        # scanners redact on purpose, and findings are exported.
        snippet = ""
        literals = _flagged_literals(file, line, end_line)
        # A model's prose quotes identifiers (os.environ['SECRET_KEY']): only
        # tool messages get every quoted value masked.
        templated = not is_ai
        title = _redact_secret_text(title, literals, mask_quoted=templated)
        description = _redact_secret_text(description, literals, mask_quoted=templated)
        suggestion = _redact_secret_text(suggestion, literals, mask_quoted=templated)
        if isinstance(extra.get("message"), str):
            extra["message"] = _redact_secret_text(extra["message"], literals, mask_quoted=templated)
    else:
        if not snippet:
            # Tools without snippets get the flagged source line.
            snippet = text.strip()
        snippet = redact_snippet(snippet)
    grounding = str(extra.get("grounding") or "") if is_ai else ""
    sample_index = _int(extra.get("sample_index")) if is_ai else None

    return NormalizedFinding(
        tool_slug=tool_slug,
        rule_id=raw.rule_id or "",
        title=title,
        severity=severity,
        native_severity=raw.native_severity or "",
        category=category,
        cwe=cwe,
        confidence=confidence,
        file_path=path,
        line=line,
        end_line=end_line,
        column=raw.column if isinstance(raw.column, int) else None,
        description=description,
        suggestion=suggestion,
        snippet=snippet,
        file_origin=origin,
        in_measured_scope=in_scope,
        counted=counted,
        fingerprint=fingerprint(tool_slug=tool_slug, rule_family=family, path=path, line_text=text),
        is_ai=is_ai,
        grounding=grounding if grounding in GROUNDINGS else "",
        sample_index=sample_index,
        extra=extra,
    )


def normalize_all(
    tool_slug: str,
    raws: list[RawFinding],
    sample: Sample,
    scope: dict[str, Any],
    *,
    outcomes: dict[str, Any] | None = None,
) -> tuple[list[NormalizedFinding], list[dict[str, Any]]]:
    """Normalize a tool's findings, routing parse errors to coverage.

    Returns (findings, unparseable) where *unparseable* is
    ``[{path, reason}]`` for ToolResult.coverage.
    """
    findings: list[NormalizedFinding] = []
    unparseable: list[dict[str, Any]] = []
    for raw in raws:
        if tool_slug != AI_TOOL_SLUG and is_parse_error(tool_slug, raw):
            unparseable.append({"path": finding_path(raw.file_path), "reason": (raw.message or raw.rule_id)[:300]})
            continue
        findings.append(normalize(tool_slug, raw, sample, scope, outcomes=outcomes))
    return findings, unparseable
