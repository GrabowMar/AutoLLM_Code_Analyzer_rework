"""Per-kind library lint (replaces ``data_lint.py``): registered issues for one entity's content.

``lint(kind, content, ...)`` returns :class:`~backend.generation.contracts.issues.Issue` rows whose codes are
the ``lint_*`` codes of ``contracts/issues.py`` (WP-03 owns that tuple). Rules:

* every kind: the content validates against its contract (``lint_content_invalid``); every library ref in
  it resolves to an entity of the right kind and role (``lint_unknown_library_ref``,
  ``lint_library_ref_kind``, ``lint_fragment_role``, ``lint_prompt_role_mismatch``,
  ``lint_prompt_mode_mismatch``);
* tasks (sections 3.4, 8.2, 11.2, 11.3): vocabulary, ids, entity and endpoint references, prose variants,
  prose/structure consistency, forbidden phrases, the minimal-prose word budget and check statedness; the
  task's instrument overlay ``tester.yaml`` (:func:`overlay_issues`, the acceptance compiler's strict loader);
* prompt templates: syntax and the filter whitelist, declared versus used versus documented variables;
* prompt sets: the WP-01 content rules (:func:`prompt_set_issues`) plus the ref-resolving rules;
* stacks, styles, guidance: fragment refs and roles, the guidance fragment, style frontends;
* starters (section 11.4): every file blob present, the composer's forbidden-content check passed, and
  WP-05's rule list (``build.starter_rules.starter_issues``: forbidden content, denied security libraries,
  digest-pinned ``FROM`` lines, dockerfile and lockfiles present, the tree matching its stack);
* detectors: the rule compiles (regex) or parses (Semgrep YAML).

**Statedness** (section 8.2, :func:`check_statedness`): a check is *stated* in a prose variant when that
variant mentions the behaviour the check tests. The rule table below is versioned (``STATEDNESS_VERSION``);
``evaluation/acceptance.py`` (WP-09) tags its compiled checks with :func:`tag_checks`.

The vocabulary comes from ``data/tasks/vocab.yaml`` (WP-04); when it is missing the built-in 11.3 list is
used and ``lint_vocabulary_fallback`` says so. The starter rules are WP-05's ``build/starter_rules.py``
(deny list ``data/starters/security_deny.yaml``); this module reads the blobs and reports missing ones.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

import yaml
from django.conf import settings
from pydantic import ValidationError

from backend.generation.contracts.issues import make_issue
from backend.generation.contracts.library import APP_REQUIRED_FRAGMENT_KEYS
from backend.generation.contracts.library import CONTENT_MODELS
from backend.generation.contracts.library import PROSE_VARIANTS
from backend.generation.contracts.library import SCHEMA_VERSIONS
from backend.generation.contracts.library import PromptSetContent
from backend.generation.contracts.library import iter_content_refs
from backend.generation.prompts.context import FRAGMENT_VARIABLES
from backend.generation.prompts.context import variable_names
from backend.generation.prompts.engine import RenderError
from backend.generation.prompts.engine import find_variables
from backend.generation.prompts.engine import parse_template
from backend.generation.prompts.engine import used_filters

if TYPE_CHECKING:
    from backend.generation.contracts.issues import Issue

__all__ = [
    "DEFAULT_FEATURES",
    "FORBIDDEN_PROSE_PHRASES",
    "LINTER_VERSION",
    "MINIMAL_PROSE_MAX_WORDS",
    "SCHEMA_VERSIONS",
    "SLOT_ROLE_SETS",
    "STATEDNESS_VERSION",
    "LintOptions",
    "Vocabulary",
    "check_statedness",
    "editor_brief_issues",
    "endpoint_mentioned",
    "has_errors",
    "limit_stated",
    "lint",
    "load_vocabulary",
    "make_issue",
    "options_stated",
    "prompt_set_issues",
    "read_store_blob",
    "required_stated",
    "statedness_reasons",
    "status_of",
    "status_stated",
    "tag_checks",
    "unstated_reasons",
]

LINTER_VERSION: Final = 1
STATEDNESS_VERSION: Final = 3
MINIMAL_PROSE_MAX_WORDS: Final = 120
# Section 11.2 / WP-04 tests: prose never talks about evaluation or planted credentials.
FORBIDDEN_PROSE_PHRASES: Final = ("evaluat", "test suite", "graded", "admin/admin", "default password")
# Section 11.3 (fallback when data/tasks/vocab.yaml is missing).
DEFAULT_FEATURES: Final = frozenset(
    {
        "auth",
        "ownership",
        "roles",
        "admin",
        "pagination",
        "search",
        "filter",
        "sort",
        "upload-multipart",
        "upload-base64",
        "download",
        "render-html",
        "render-markdown",
        "redirect",
        "outbound-http",
        "xml-parse",
        "websocket",
        "sse",
        "csv-export",
        "password-reset",
        "email-mock",
        "rate-limit",
        "csrf",
        "webhook-signature",
        "scheduling",
        "money",
        "geo",
        "realtime",
    },
)
_KNOWN_FRONTENDS: Final = frozenset({"react", "vue", "svelte", "none", "free"})
# Slot prompt sets are single-shot (both slot_single_shot_* roles) or agent (slot_agent_brief).
SLOT_ROLE_SETS: Final = (
    frozenset({"slot_single_shot_system", "slot_single_shot_user"}),
    frozenset({"slot_agent_brief"}),
)
_CWE_RE: Final = re.compile(r"^cwe-[1-9][0-9]*$")
_WORD_RE: Final = re.compile(r"\S+")
# (kind, ref) -> the referenced entity's stored content, or None when it does not exist.
RefResolver = Callable[[str, str], Mapping[str, Any] | None]
# blob sha -> bytes, or None when the blob is missing.
BlobReader = Callable[[str], bytes | None]


# =====================================================================================
# Inputs: vocabulary, deny list, options
# =====================================================================================


@dataclass(frozen=True)
class Vocabulary:
    """The closed task vocabulary (``data/tasks/vocab.yaml``, section 11.3)."""

    features: frozenset[str]
    surfaces: frozenset[str] | None  # None: any ``cwe-<n>`` id is accepted
    fallback: bool = False


def _data_dir() -> Path:
    return Path(settings.GENERATION_LIBRARY_DATA_DIR)


def _names(items: Iterable[Any], key: str) -> frozenset[str]:
    names = set()
    for item in items or ():
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, Mapping) and item.get(key):
            names.add(str(item[key]))
    return frozenset(names)


@functools.lru_cache(maxsize=8)
def _vocabulary_from(path: str, mtime: float) -> Vocabulary:
    del mtime  # cache key only
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    features = _names(data.get("features") or [], "key") or DEFAULT_FEATURES
    surfaces = _names(data.get("surfaces") or [], "id") or None
    return Vocabulary(features=features, surfaces=surfaces)


def load_vocabulary(data_dir: Path | None = None) -> Vocabulary:
    """The vocabulary of ``data_dir`` (default ``settings.GENERATION_LIBRARY_DATA_DIR``), else the fallback.

    ``vocab.yaml`` holds ``features`` and ``surfaces`` lists (strings, or mappings with ``key`` / ``id``).
    """
    path = (data_dir or _data_dir()) / "tasks" / "vocab.yaml"
    if not path.is_file():
        return Vocabulary(features=DEFAULT_FEATURES, surfaces=None, fallback=True)
    return _vocabulary_from(str(path), path.stat().st_mtime)


def _db_resolver(kind: str, ref: str) -> Mapping[str, Any] | None:
    from backend.generation.library import service

    try:
        return service.resolve_ref(ref, kind=kind).content
    except service.LibraryError:
        return None


def read_store_blob(sha: str) -> bytes | None:
    """The blob's bytes from the store, or ``None`` when it is missing."""
    from backend.generation import blobs

    try:
        return blobs.get_bytes(sha)
    except (LookupError, ValueError, OSError):
        return None


@dataclass
class LintOptions:
    """Inputs of one lint call. ``resolve=None`` skips the ref-resolving rules (pure content lint)."""

    system: bool = False
    resolve: RefResolver | None = None
    read_blob: BlobReader | None = None
    vocabulary: Vocabulary | None = None


def has_errors(issues: Iterable[Issue]) -> bool:
    return any(issue.severity == "error" for issue in issues)


def status_of(issues: Iterable[Issue]) -> str:
    """``ok``, ``warn`` or ``error`` (``TaskLint.status``); ``info`` issues keep ``ok``."""
    severities = {issue.severity for issue in issues}
    if "error" in severities:
        return "error"
    return "warn" if "warn" in severities else "ok"


def _issue(code: str, message: str, path: str | None = None, **data: Any) -> Issue:
    return make_issue(code, message=message, path=path, data=data)


# =====================================================================================
# Entry point
# =====================================================================================


def lint(
    kind: str,
    content: Mapping[str, Any],
    *,
    system: bool = False,
    resolve: RefResolver | None | bool = True,
    read_blob: BlobReader | None = None,
    vocabulary: Vocabulary | None = None,
    slug: str | None = None,
) -> list[Issue]:
    """Registered issues for ``content`` of ``kind`` (codes from ``contracts.issues``).

    ``resolve=True`` resolves library refs from the database; pass a callable ``(kind, ref) -> content`` to
    resolve from elsewhere (seed files, packages), or ``False`` for content-only rules. ``read_blob`` reads
    starter file blobs (default: the blob store). ``system`` applies the stricter system-entity rules
    (all three prose variants). ``slug`` names the task (kind ``task``) whose instrument overlay
    ``data/tasks/<slug>/tester.yaml`` is checked too (:func:`overlay_issues`); without it the task is looked
    up among the bundled tasks that have an overlay, by title and summary.
    """
    if kind not in CONTENT_MODELS:
        msg = f"unknown library kind {kind!r}"
        raise KeyError(msg)
    resolver: RefResolver | None
    if resolve is True:
        resolver = _db_resolver
    elif resolve is False or resolve is None:
        resolver = None
    else:
        resolver = resolve
    options = LintOptions(
        system=system,
        resolve=resolver,
        read_blob=read_blob or read_store_blob,
        vocabulary=vocabulary,
    )
    try:
        parsed = CONTENT_MODELS[kind].model_validate(content)
    except ValidationError as exc:
        return [
            _issue(
                "lint_content_invalid",
                f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}",
                "content." + ".".join(str(part) for part in error["loc"]) if error["loc"] else "content",
            )
            for error in exc.errors()[:20]
        ]
    normalized = parsed.model_dump(mode="json")
    issues: list[Issue] = []
    if resolver is not None:
        issues += _ref_issues(kind, normalized, resolver)
    rule = _RULES.get(kind)
    if rule is not None:
        issues += rule(normalized, options)
    if kind == "task":
        task_slug = slug or _overlay_slug(normalized)
        if task_slug:
            issues += overlay_issues(task_slug, parsed.model_dump(mode="json", by_alias=True))
    return issues


def _overlay_slug(content: Mapping[str, Any], data_dir: Path | None = None) -> str | None:
    """The bundled task with an instrument overlay whose ``task.yaml`` has this title and summary."""
    root = (data_dir or _data_dir()) / "tasks"
    for overlay in sorted(root.glob("*/tester.yaml")):
        try:
            task = yaml.safe_load((overlay.parent / "task.yaml").read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if (
            isinstance(task, dict)
            and task.get("title") == content.get("title")
            and task.get("summary") == content.get("summary")
        ):
            return overlay.parent.name
    return None


def overlay_issues(slug: str, content: Mapping[str, Any], data_dir: Path | None = None) -> list[Issue]:
    """The instrument overlay of task ``slug`` (``data/tasks/<slug>/tester.yaml``) is usable, strictly.

    The acceptance compiler refuses a plan whose overlay has an unknown key, an unknown endpoint or check id, a
    check patch without ``why``, an unknown placeholder or a sample that violates the request schema; the lint
    reports the same problems (``lint_content_invalid``, path ``tester.yaml``) before anything is evaluated.
    """
    from backend.generation.evaluation import acceptance  # the compiler imports this module

    return [
        _issue("lint_content_invalid", f"tester.yaml: {problem}", "tester.yaml")
        for problem in acceptance.overlay_problems(slug, content, data_dir)
    ]


def _ref_issues(kind: str, content: Mapping[str, Any], resolve: RefResolver) -> list[Issue]:
    """Unknown refs, and fragment refs whose template is not a fragment."""
    issues: list[Issue] = []
    for location, target, ref in iter_content_refs(kind, content):
        found = resolve(target, ref)
        if found is None:
            issues.append(
                _issue("lint_unknown_library_ref", f"{location}: {target} {ref} does not exist", location, ref=ref),
            )
            continue
        if target == "prompt" and kind in {"stack", "style", "guidance"} and found.get("role") != "fragment":
            issues.append(
                _issue(
                    "lint_fragment_role",
                    f"{location}: {ref} is a {found.get('role')!r} template, not a fragment",
                    location,
                    ref=ref,
                ),
            )
    return issues


# =====================================================================================
# Tasks
# =====================================================================================


def _path_regex(path: str) -> str:
    parts = []
    for segment in path.strip("/").split("/"):
        if re.fullmatch(r"\{[^}]+\}|:[A-Za-z_]\w*|<[^>]+>", segment):
            parts.append(r"(?:\{[^}/\s]+\}|:[A-Za-z_]\w*|<[^>/\s]+>|[^\s/`(){}<>:]+)")
        else:
            parts.append(re.escape(segment))
    return "/" + "/".join(parts) + r"(?![\w/{}<>:-])"


@functools.lru_cache(maxsize=4096)
def _endpoint_pattern(method: str, path: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(method)}\b[^\n]{{0,40}}?{_path_regex(path)}")


def endpoint_mentioned(prose: str, endpoint: Mapping[str, Any]) -> bool:
    """``True`` when ``prose`` names the endpoint's method and path on one line (path parameters match any
    ``{name}``, ``:name`` or ``<name>`` placeholder or a concrete value)."""
    return bool(_endpoint_pattern(str(endpoint.get("method", "")), str(endpoint.get("path", "/"))).search(prose))


# Statedness rule table (STATEDNESS_VERSION 3; version 2 plus the two round-3 rules marked below).
#
# Version 1 tagged a check as stated in a prose variant when the endpoint was mentioned and, for validation
# checks, when a keyword of the expected status ("invalid", "validation", "required" ...) occurred anywhere in
# the prose. The standard prose of nearly every task has the frontend sentence "forms that show the API's
# validation messages", so length limits, option lists and success statuses that only the exhaustive prose (or
# only the request schema) gives were counted as stated in the standard variant.
#
# Version 2: a check is stated in variant V only when V states every *value* the check holds the app to:
#
# * the endpoint is mentioned (unchanged), and the category keywords for auth / ownership / pagination /
#   filter checks occur (unchanged);
# * ``too long``: V gives the limit of every over-long field of the check (the number, in the clause that
#   names the field);
# * ``not an option``, and an option list the response schema of the check demands: V lists all options of
#   that field.
# * a *valid* option any request of the check sends (the request under test, an ``also`` request or a setup
#   step; body fields at any depth and query parameters): V lists the options of that field. (Round 3: an app
#   built from a variant that never lists the options has to invent its own vocabulary, so whether it accepts
#   the instrument's ``priority: low`` is not something that variant states.)
# * ``missing``: V says the dropped field is required (in the clause that names it), or names it on the line
#   that describes the endpoint and says there that invalid input is answered 400/422;
# * an exact status other than 200 (201, 202, 204, 3xx; 404, 409, 413, 429 ...) that the check accepts nothing
#   else beside: V gives that number on a line that mentions the endpoint or, for an error status, on a line
#   that names the condition. For a ``POST`` an exact 200 is such a status too (round 3): 200 is the default
#   answer of a read, but a POST may as well answer 201 or 204, so "exactly 200" is stated only where the
#   variant gives it. ``POST /api/auth/register`` and ``/login`` are exempt: their statuses are
#   stated to every variant by the auth contract of the prompt. 400 and 422 are one tolerance ("invalid
#   input"): there the fault decides, and a 400/422 check without a recognisable fault keeps the version 1
#   rule (the number or a keyword). Auth / ownership / pagination / filter checks are decided by their
#   category keywords alone, as in version 1.
#
# The rule only decides ``stated_in`` (which pass rate a check counts in); it never changes a request or an
# expectation. A hand-written check with an explicit ``stated_in`` keeps its author's list.
_CATEGORY_WORDS: Final[Mapping[str, tuple[str, ...]]] = {
    "auth": (r"log ?in", r"sign ?in", r"authenticat", r"token", r"session", r"password", r"register"),
    "ownership": (r"\bown", r"only (?:the|their|his|her)", r"belong", r"other users?", r"another user"),
    "pagination": (r"paginat", r"\bpages?\b", r"page_size", r"\blimit\b", r"\boffset\b"),
    "filter": (r"filter", r"search", r"\bquery\b", r"\bsort", r"order(?:ed)? by"),
}
_STATUS_WORDS: Final[Mapping[int, tuple[str, ...]]] = {
    400: (r"invalid", r"validat", r"reject", r"bad request", r"required"),
    401: (r"unauthenticated", r"not (?:logged|signed) in", r"log ?in", r"authenticat"),
    403: (r"forbidden", r"not allowed", r"permission", r"only (?:the|their)", r"\bown"),
    404: (r"not found", r"does not exist", r"doesn't exist", r"unknown"),
    409: (r"conflict", r"already", r"duplicate", r"unique", r"taken", r"exists"),
    413: (r"too large", r"size limit", r"maximum size", r"exceeds", r"\bover\b", r"larger than", r"\b[KMG]i?B\b"),
    422: (r"unprocessable", r"invalid", r"validat"),
    429: (r"rate limit", r"too many", r"throttl", r"lock(?:ed)? out", r"lockout", r"attempts"),
}
_VALIDATION_CATEGORIES: Final = frozenset({"validation", "negative"})
_REQUIRED_WORDS: Final = (
    r"\brequired\b",
    r"\bmandatory\b",
    r"\bmust (?:be|have|include|contain|not be)\b",
    r"\b(?:may|can|must)(?: not|not) be (?:empty|blank|omitted|missing)\b",
    r"\bnon-empty\b",
    r"\bis needed\b",
)
_CLAUSE_END: Final = re.compile(r";|(?<=[.!?])\s+")
_AUTH_CONTRACT_PATHS: Final = ("/api/auth/register", "/api/auth/login")


def _words(prose: str, patterns: Iterable[str]) -> bool:
    return any(re.search(pattern, prose, re.I) for pattern in patterns)


def _expected_statuses(check: Mapping[str, Any]) -> list[int]:
    status = (check.get("expect") or {}).get("status")
    if isinstance(status, list):
        return [int(code) for code in status]
    return [int(status)] if status is not None else []


def _clauses(prose: str) -> list[str]:
    """The clauses of ``prose``: its lines, split at ``;`` and at sentence ends."""
    return [clause for line in prose.splitlines() for clause in _CLAUSE_END.split(line) if clause.strip()]


def _token(text: str) -> str:
    """A regex matching ``text`` as a whole token (not inside a longer word, number or identifier)."""
    return rf"(?<![\w.-]){re.escape(text)}(?![\w-]|\.\w)"


def _number(value: int) -> str:
    """A regex matching the number ``value`` (also written ``200,000`` / ``200 000``), not as part of another."""
    forms = {str(value), f"{value:,}", f"{value:,}".replace(",", " ")}
    return r"(?<![\w.,])(?:" + "|".join(sorted(map(re.escape, forms))) + r")(?![\w]|[.,]\d)"


def limit_stated(prose: str, field: str, limit: int) -> bool:
    """``prose`` gives the length limit of ``field``: one clause names the field and holds the number."""
    name, number = re.compile(_token(field)), re.compile(_number(int(limit)))
    return any(name.search(clause) and number.search(clause) for clause in _clauses(prose))


def options_stated(prose: str, options: Iterable[Any]) -> bool:
    """``prose`` lists every option of an ``enum`` (each value occurs as a whole token; ``null`` and booleans
    are not vocabulary and are not looked for)."""
    values = [str(option) for option in options if option is not None and not isinstance(option, bool)]
    return all(re.search(_token(value), prose) for value in values)


def required_stated(prose: str, field: str, endpoint: Mapping[str, Any] | None = None) -> bool:
    """``prose`` says a request without ``field`` is refused.

    Either one clause names the field and holds a requiredness word (``_REQUIRED_WORDS``), or the line that
    describes ``endpoint`` names the field as its input and says invalid input is answered 400/422 (an action
    endpoint: "changes the password; the body holds `current_password` and `new_password`. ... It answers 400
    for invalid input").
    """
    name = re.compile(_token(field))
    if any(name.search(clause) and _words(clause, _REQUIRED_WORDS) for clause in _clauses(prose)):
        return True
    if endpoint is None:
        return False
    refused = re.compile(r"(?<![\w.,])4(?:00|22)(?![\w]|[.,]\d)")
    return any(
        endpoint_mentioned(line, endpoint) and name.search(line) and refused.search(line)
        for line in prose.splitlines()
    )


def status_stated(prose: str, endpoint: Mapping[str, Any] | None, code: int) -> bool:
    """``prose`` gives status ``code`` for ``endpoint``: the number stands on a line that mentions the endpoint
    or, for an error status, on a line that names the condition (``_STATUS_WORDS``)."""
    number = re.compile(rf"(?<![\w.,]){int(code)}(?![\w]|[.,]\d)")
    for line in prose.splitlines():
        if not number.search(line):
            continue
        if endpoint is not None and endpoint_mentioned(line, endpoint):
            return True
        if code >= 400 and _words(line, _STATUS_WORDS.get(code, ())):
            return True
    return False


def default_stated(prose: str, endpoint: Mapping[str, Any], param: str) -> bool:
    """``prose`` states a default for query parameter ``param`` of ``endpoint``: on a line that mentions the
    endpoint, one clause names the parameter and holds the word "default" ("`page` is the 1-based page number
    (default 1)", "`date` is the day to show (default today)")."""
    name = re.compile(_token(param))
    return any(
        endpoint_mentioned(line, endpoint)
        and any(
            name.search(clause) and re.search(r"\bdefaults?\b", clause, re.I) for clause in _CLAUSE_END.split(line)
        )
        for line in prose.splitlines()
    )


def qualifies_other_parameter(prose: str, endpoint: Mapping[str, Any], param: str) -> bool:
    """``prose`` defines query parameter ``param`` only in combination with another optional parameter of
    ``endpoint``: on a line that mentions the endpoint, the clause that names ``param`` reads "with `<other>`
    ..." ("with `lat` and `lng` it keeps the stores within `radius` kilometres": a radius without a location
    has no stated meaning)."""
    name = re.compile(_token(param))
    others = [
        str(q.get("name"))
        for q in endpoint.get("query") or []
        if str(q.get("name")) != param and not q.get("required")
    ]
    if not others:
        return False
    leading = re.compile(r"\bwith `(?:" + "|".join(map(re.escape, others)) + r")`")
    # the closing "(query parameters: ...)" list of a standard-prose line names every parameter: not a clause
    return any(
        endpoint_mentioned(line, endpoint)
        and any(
            name.search(clause) and leading.search(clause)
            for clause in _CLAUSE_END.split(line.split("(query parameters:")[0])
        )
        for line in prose.splitlines()
    )


def _request_endpoint(request: Mapping[str, Any], content: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The endpoint a (setup or check) request addresses: same method, same path up to parameter names."""
    method = str(request.get("method", "GET")).upper()
    shape = re.sub(r"\{[^}/]+\}", "{}", str(request.get("path", "")).rstrip("/"))
    for endpoint in content.get("endpoints") or []:
        if str(endpoint.get("method", "")).upper() != method:
            continue
        if re.sub(r"\{[^}/]+\}", "{}", str(endpoint.get("path", "")).rstrip("/")) == shape:
            return endpoint
    return None


def _request_properties(endpoint: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    schema = endpoint.get("request_schema")
    props = (schema.get("properties") or {}) if isinstance(schema, Mapping) else {}
    return {str(name): sub for name, sub in props.items() if isinstance(sub, Mapping)}


def _check_requests(check: Mapping[str, Any]) -> list[tuple[bool, Mapping[str, Any]]]:
    """``(is the request under test, request)`` for the setup steps, the request and the ``also`` requests."""
    out: list[tuple[bool, Mapping[str, Any]]] = [
        (False, step.get("request") or {}) for step in check.get("setup") or [] if isinstance(step, Mapping)
    ]
    out.append((True, check.get("request") or {}))
    out += [(True, extra.get("request") or {}) for extra in check.get("also") or [] if isinstance(extra, Mapping)]
    return out


def _schema_options(schema: Any) -> list[list[Any]]:
    """Every ``enum`` list inside a JSON schema (deep)."""
    found: list[list[Any]] = []
    if isinstance(schema, Mapping):
        if isinstance(schema.get("enum"), list) and schema["enum"]:
            found.append(list(schema["enum"]))
        for value in schema.values():
            found += _schema_options(value)
    elif isinstance(schema, list):
        for value in schema:
            found += _schema_options(value)
    return found


def _sent_options(value: Any, schema: Any, name: str = "") -> list[tuple[str, list[Any]]]:
    """``(field, options)`` of every option-valued field for which ``value`` holds one of its options (deep:
    nested objects and the items of arrays). Booleans and ``null`` are not vocabulary."""
    if not isinstance(schema, Mapping):
        return []
    found: list[tuple[str, list[Any]]] = []
    options = schema.get("enum") if isinstance(schema.get("enum"), list) else None
    if options and value is not None and not isinstance(value, (bool, dict, list)) and value in options:
        found.append((name, list(options)))
    if isinstance(value, Mapping):
        props = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
        for key, sub in props.items():
            if key in value:
                found += _sent_options(value[key], sub, str(key))
    elif isinstance(value, list):
        for item in value:
            found += _sent_options(item, schema.get("items"), name)
    return found


def _value_rules(check: Mapping[str, Any], content: Mapping[str, Any]) -> list[tuple[str, str, Any]]:
    """The values a check holds the app to, as ``(kind, field, value)``.

    ``too_long`` (value: the limit), ``enum`` (the options a request breaks), ``missing`` (a required field
    the request under test drops), ``options`` (the options the response schema demands of a field) and
    ``sent`` (the options of a field for which a request or a setup step of the check sends a valid one).

    ``sent`` (round 3): a check whose requests rely on the option vocabulary of a field holds the app to that
    vocabulary. A variant that does not list the options leaves the vocabulary to the implementer, and an app
    with another one refuses the instrument's value: the check then measures something that variant does not
    state. (Round 2 left these checks in the stated set.)
    """
    from backend.generation.evaluation.acceptance import rendered_len  # the compiler imports this module

    rules: list[tuple[str, str, Any]] = []
    validation = str(check.get("category")) in _VALIDATION_CATEGORIES
    for under_test, request in _check_requests(check):
        endpoint = _request_endpoint(request, content)
        if endpoint is None:
            continue
        body = request.get("json") if isinstance(request.get("json"), Mapping) else request.get("form")
        props = _request_properties(endpoint)
        if isinstance(body, Mapping):
            for name, sub in props.items():
                if name not in body:
                    continue
                value = body[name]
                limit = sub.get("maxLength")
                options = sub.get("enum") if isinstance(sub.get("enum"), list) else None
                if options and not isinstance(value, bool):
                    if value not in options:
                        rules.append(("enum", name, list(options)))
                elif isinstance(limit, int) and not isinstance(limit, bool) and isinstance(value, str):
                    # the length the tester sends; ``None`` for the oversize placeholders (another rule: 413)
                    size = rendered_len(value)
                    if size is None and "oversize" not in value:
                        size = len(re.sub(r"\{\w+\}", "", value))  # at least its literal text
                    if size is not None and size > limit:
                        rules.append(("too_long", name, limit))
                rules += [("sent", field, found) for field, found in _sent_options(value, sub, name)]
            if under_test and validation and str(endpoint.get("method")) != "PATCH":
                schema = endpoint.get("request_schema") or {}
                rules += [("missing", str(name), None) for name in schema.get("required") or [] if name not in body]
        query = request.get("query") if isinstance(request.get("query"), Mapping) else {}
        for param in endpoint.get("query") or []:
            name = str(param.get("name"))
            options = param.get("enum") if isinstance(param.get("enum"), list) else None
            if options and name in query and not isinstance(query[name], bool) and query[name] in options:
                rules.append(("sent", name, list(options)))
    for options in _schema_options((check.get("expect") or {}).get("json_schema")):
        rules.append(("options", "", options))
    seen: list[tuple[str, str, Any]] = []
    for rule in rules:
        if rule not in seen:
            seen.append(rule)
    return seen


def unstated_reasons(
    check: Mapping[str, Any],
    prose: str,
    endpoints: Mapping[str, Mapping[str, Any]],
    content: Mapping[str, Any] | None = None,
) -> list[str]:
    """Why ``check`` is not stated in ``prose`` (empty: it is stated). Rule table ``STATEDNESS_VERSION``."""
    content = content if content is not None else {"endpoints": list(endpoints.values())}
    endpoint = endpoints.get(str(check.get("endpoint"))) if check.get("endpoint") else None
    if endpoint is not None and not endpoint_mentioned(prose, endpoint):
        return ["endpoint_not_mentioned"]
    reasons: list[str] = []
    category = str(check.get("category", "functional"))
    if category == "infra":
        return []
    if category in _CATEGORY_WORDS and not _words(prose, _CATEGORY_WORDS[category]):
        reasons.append(f"category:{category}")
    rules = _value_rules(check, content)
    faults = [rule for rule in rules if rule[0] in ("too_long", "enum", "missing")]
    for kind, name, value in rules:
        if kind == "too_long" and not limit_stated(prose, name, value):
            reasons.append(f"limit:{name}={value}")
        elif kind in ("enum", "options") and not options_stated(prose, value):
            reasons.append(f"options:{name}" if kind == "enum" else "response_options")
        elif kind == "sent" and not options_stated(prose, value):
            reasons.append(f"sent_options:{name}")
        elif kind == "missing" and not required_stated(prose, name, endpoint):
            reasons.append(f"required:{name}")
    statuses = _expected_statuses(check)
    # the endpoint the status belongs to: the check's, else the one its request addresses, else the request
    target = endpoint or _request_endpoint(check.get("request") or {}, content) or (check.get("request") or None)
    # 200 is the default answer of a read; of a POST it is one success status among 200 / 201 / 202 / 204
    posted = str((check.get("request") or {}).get("method") or (target or {}).get("method") or "").upper() == "POST"
    exact_200 = posted and statuses == [200]  # a tolerance such as [200, 201] is not an exact status
    specific = [code for code in statuses if (code != 200 or exact_200) and code not in (400, 422)]
    if category in _CATEGORY_WORDS or not statuses:
        pass  # auth / ownership / pagination / filter: decided by the category keywords above
    elif set(statuses) <= {400, 422}:
        # either status is accepted for invalid input: the fault decides; without a recognisable fault the
        # version 1 rule holds (the number or a keyword of the status, anywhere in the prose)
        if not faults and not any(
            re.search(rf"\b{code}\b", prose) or _words(prose, _STATUS_WORDS.get(code, ())) for code in statuses
        ):
            reasons.append("status:400")
    elif len(specific) == len(statuses):
        # every accepted status is an exact one other than 200: the variant must give it
        exempt = target is not None and str(target.get("path", "")).rstrip("/") in _AUTH_CONTRACT_PATHS
        if not exempt and not any(status_stated(prose, target, code) for code in specific):
            reasons.append("status:" + "/".join(str(code) for code in specific))
    return reasons


def _stated(
    check: Mapping[str, Any],
    prose: str,
    endpoints: Mapping[str, Mapping[str, Any]],
    content: Mapping[str, Any] | None = None,
) -> bool:
    return not unstated_reasons(check, prose, endpoints, content)


def check_statedness(
    content: Mapping[str, Any],
    checks: Iterable[Mapping[str, Any]] | None = None,
    *,
    explicit: bool = True,
) -> dict[str, list[str]]:
    """``{check_id: [prose variants that state it]}`` (section 8.2, rule table ``STATEDNESS_VERSION``).

    ``checks`` defaults to the task's hand-written ``acceptance``. A hand-written check whose author set
    ``stated_in`` keeps that list; ``explicit=False`` ignores the authors' lists and applies the rule table to
    every check (to list where the two disagree).
    """
    endpoints = {str(endpoint.get("id")): endpoint for endpoint in content.get("endpoints") or []}
    prose = content.get("prose") or {}
    tagged: dict[str, list[str]] = {}
    items = (content.get("acceptance") or []) if checks is None else checks
    for check in items:
        check_id = str(check.get("id"))
        declared = list(check.get("stated_in") or [])
        if declared and checks is None and explicit:
            tagged[check_id] = [variant for variant in PROSE_VARIANTS if variant in declared]
            continue
        tagged[check_id] = [
            variant
            for variant in PROSE_VARIANTS
            if variant in prose and _stated(check, str(prose[variant]), endpoints, content)
        ]
    return tagged


def statedness_reasons(
    content: Mapping[str, Any],
    checks: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, list[str]]]:
    """``{check_id: {variant: [why the check is not stated there]}}`` for the variants that do not state it."""
    endpoints = {str(endpoint.get("id")): endpoint for endpoint in content.get("endpoints") or []}
    prose = content.get("prose") or {}
    out: dict[str, dict[str, list[str]]] = {}
    for check in checks:
        rows = {
            variant: unstated_reasons(check, str(prose[variant]), endpoints, content)
            for variant in PROSE_VARIANTS
            if variant in prose
        }
        out[str(check.get("id"))] = {variant: reasons for variant, reasons in rows.items() if reasons}
    return out


def tag_checks(content: Mapping[str, Any], checks: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Copies of ``checks`` with ``stated_in`` filled (for compiled checks, ``evaluation/acceptance.py``)."""
    items = [dict(check) for check in checks]
    tags = check_statedness(content, items)
    for item in items:
        item["stated_in"] = tags.get(str(item.get("id")), [])
    return items


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    dupes: list[str] = []
    for value in values:
        if value in seen and value not in dupes:
            dupes.append(value)
        seen.add(value)
    return dupes


def _task_ref_issues(content: Mapping[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    entities = {str(entity["name"]): entity for entity in content.get("entities") or []}
    endpoint_ids = {str(endpoint["id"]) for endpoint in content.get("endpoints") or []}

    def endpoint_ref(value: Any, path: str) -> None:
        if value and str(value) not in endpoint_ids:
            issues.append(_issue("lint_unknown_endpoint_ref", f"{path}: no endpoint {value!r}", path, endpoint=value))

    for group, items, key in (
        ("entities", content.get("entities") or [], "name"),
        ("endpoints", content.get("endpoints") or [], "id"),
        ("acceptance", content.get("acceptance") or [], "id"),
        ("probes", content.get("probes") or [], "id"),
        ("ui", content.get("ui") or [], "id"),
    ):
        issues += [
            _issue("lint_duplicate_id", f"duplicate {group} id {dupe!r}", group, id=dupe)
            for dupe in _duplicates(str(item[key]) for item in items)
        ]
    for e_index, entity in enumerate(content.get("entities") or []):
        for f_index, entity_field in enumerate(entity.get("fields") or []):
            if entity_field.get("type") == "ref" and str(entity_field.get("ref")) not in entities:
                path = f"entities.{e_index}.fields.{f_index}.ref"
                issues.append(
                    _issue("lint_unknown_entity_ref", f"{path}: no entity {entity_field.get('ref')!r}", path),
                )
    for index, endpoint in enumerate(content.get("endpoints") or []):
        path = f"endpoints.{index}"
        owner = endpoint.get("owner_entity")
        if endpoint.get("auth") == "owner" and not owner:
            issues.append(
                _issue("lint_owner_entity_missing", f"{endpoint['id']}: owner auth needs owner_entity", path),
            )
        if owner:
            if owner not in entities:
                issues.append(_issue("lint_unknown_entity_ref", f"{path}.owner_entity: no entity {owner!r}", path))
            elif not entities[owner].get("owned_by_user"):
                issues.append(
                    _issue("lint_owner_entity_not_owned", f"{path}.owner_entity: {owner} is not owned_by_user", path),
                )
    for name in content.get("seed_data") or {}:
        if name not in entities:
            issues.append(_issue("lint_unknown_entity_ref", f"seed_data.{name}: no such entity", f"seed_data.{name}"))
    for index, check in enumerate(content.get("acceptance") or []):
        endpoint_ref(check.get("endpoint"), f"acceptance.{index}.endpoint")
    for index, probe in enumerate(content.get("probes") or []):
        endpoint_ref(probe.get("endpoint"), f"probes.{index}.endpoint")
    for index, item in enumerate(content.get("ui") or []):
        for ref in item.get("endpoints") or []:
            endpoint_ref(ref, f"ui.{index}.endpoints")
    flow = content.get("auth_flow")
    if flow:
        for key in ("register", "login", "logout", "csrf_endpoint"):
            endpoint_ref(flow.get(key), f"auth_flow.{key}")
    elif any(endpoint.get("auth") in {"user", "owner", "admin"} for endpoint in content.get("endpoints") or []):
        issues.append(
            _issue(
                "lint_auth_flow_missing",
                "the task has authenticated endpoints but no auth_flow (auth mode spec_defined is unavailable)",
                "auth_flow",
            ),
        )
    return issues


def _task_prose_issues(content: Mapping[str, Any], options: LintOptions) -> list[Issue]:
    issues: list[Issue] = []
    prose: Mapping[str, str] = content.get("prose") or {}
    if options.system:
        issues += [
            _issue("lint_prose_variant_missing", f"system tasks need the {variant} prose", f"prose.{variant}")
            for variant in PROSE_VARIANTS
            if not str(prose.get(variant, "")).strip()
        ]
    for variant, text in prose.items():
        lowered = str(text).lower()
        for phrase in FORBIDDEN_PROSE_PHRASES:
            if phrase in lowered:
                issues.append(
                    _issue(
                        "lint_prose_forbidden_phrase",
                        f"the {variant} prose mentions {phrase!r}",
                        f"prose.{variant}",
                        phrase=phrase,
                    ),
                )
    minimal = prose.get("minimal")
    if minimal is not None:
        words = len(_WORD_RE.findall(str(minimal)))
        if words > MINIMAL_PROSE_MAX_WORDS:
            issues.append(
                _issue(
                    "lint_prose_too_long",
                    f"the minimal prose has {words} words (at most {MINIMAL_PROSE_MAX_WORDS})",
                    "prose.minimal",
                    words=words,
                ),
            )
    for variant in ("standard", "exhaustive"):
        if variant not in prose:
            continue
        for endpoint in content.get("endpoints") or []:
            if not endpoint_mentioned(str(prose[variant]), endpoint):
                issues.append(
                    _issue(
                        "lint_prose_endpoint_unmentioned",
                        f"the {variant} prose does not mention {endpoint['method']} {endpoint['path']}",
                        f"prose.{variant}",
                        endpoint=endpoint["id"],
                    ),
                )
    return issues


def _task_rules(content: Mapping[str, Any], options: LintOptions) -> list[Issue]:
    vocabulary = options.vocabulary or load_vocabulary()
    issues: list[Issue] = []
    if vocabulary.fallback:
        issues.append(
            _issue("lint_vocabulary_fallback", "data/tasks/vocab.yaml not found: using the built-in vocabulary"),
        )
    for index, feature in enumerate(content.get("features") or []):
        if feature not in vocabulary.features:
            issues.append(
                _issue("lint_unknown_feature", f"feature {feature!r} is not in the vocabulary", f"features.{index}"),
            )
    for index, surface in enumerate(content.get("surfaces") or []):
        known = vocabulary.surfaces is None or surface in vocabulary.surfaces
        if not _CWE_RE.match(str(surface)) or not known:
            issues.append(
                _issue(
                    "lint_unknown_surface",
                    f"surface {surface!r} is not a known CWE id",
                    f"surfaces.{index}",
                ),
            )
    issues += _task_ref_issues(content)
    issues += _task_prose_issues(content, options)
    tags = check_statedness(content)
    issues += [
        _issue("lint_check_unstated", f"check {check_id} is stated in no prose variant", "acceptance", check=check_id)
        for check_id, variants in tags.items()
        if not variants
    ]
    return issues


# =====================================================================================
# Prompts and prompt sets
# =====================================================================================


def _prompt_rules(content: Mapping[str, Any], options: LintOptions) -> list[Issue]:
    del options
    template = str(content.get("template", ""))
    try:
        parse_template(template)
        used = find_variables(template)
        filters = used_filters(template)
    except RenderError as exc:
        return [_issue("lint_template_syntax", str(exc), "template", line=exc.line)]
    from backend.generation.prompts.engine import FILTERS

    issues: list[Issue] = [
        _issue("lint_template_syntax", f"filter {name!r} is not allowed", "template", filter=name)
        for name in sorted(filters - set(FILTERS))
    ]
    declared = list(content.get("variables") or [])
    modes = list(content.get("modes") or [])
    documented = variable_names(modes)
    if content.get("role") == "fragment":
        documented = documented & FRAGMENT_VARIABLES
    issues += [
        _issue("lint_undeclared_variable", f"the template reads undeclared variable {name!r}", "variables", name=name)
        for name in sorted(used - set(declared))
    ]
    issues += [
        _issue("lint_unused_variable", f"declared variable {name!r} is never used", "variables", name=name)
        for name in declared
        if name not in used
    ]
    issues += [
        _issue(
            "lint_unknown_variable",
            f"{name!r} is not a documented {'fragment ' if content.get('role') == 'fragment' else ''}"
            f"context variable for modes {modes}",
            "variables",
            name=name,
        )
        for name in declared
        if name not in documented
    ]
    return issues


def prompt_set_issues(content: Mapping[str, Any]) -> list[Issue]:
    """Content-only prompt-set rules: content validity, the roles its mode requires, the app fragment slots.

    An app-mode set must pin a fragment for every ``auth_contract.<mode>`` and ``network.<policy>`` key, so the
    renderer never falls back to a "latest by slug" lookup (Appendix A.1); ``budget_neutral`` is optional.
    """
    try:
        parsed = PromptSetContent.model_validate(content)
    except ValidationError as exc:
        return [make_issue("lint_content_invalid", message=str(exc), path="content")]
    issues: list[Issue] = []
    roles = set(parsed.roles)
    if parsed.mode == "app":
        if "brief" not in roles:
            issues.append(
                make_issue("lint_prompt_role_missing", message="app prompt sets need a brief", path="roles.brief"),
            )
        issues += [
            make_issue(
                "lint_prompt_fragment_missing",
                message=f"app prompt sets need the {key} fragment",
                path=f"fragments.{key}",
                data={"key": key},
            )
            for key in APP_REQUIRED_FRAGMENT_KEYS
            if key not in parsed.fragments
        ]
    elif not any(required <= roles for required in SLOT_ROLE_SETS):
        issues.append(
            make_issue(
                "lint_prompt_role_missing",
                message="slot prompt sets need both slot_single_shot_* roles or slot_agent_brief",
                path="roles",
            ),
        )
    return issues


def _prompt_set_rules(content: Mapping[str, Any], options: LintOptions) -> list[Issue]:
    issues = prompt_set_issues(content)
    if options.resolve is None:
        return issues
    mode = content.get("mode")
    for role, ref in (content.get("roles") or {}).items():
        found = options.resolve("prompt", ref)
        if found is None:
            continue  # reported by _ref_issues
        path = f"roles.{role}"
        if found.get("role") != role:
            issues.append(
                _issue(
                    "lint_prompt_role_mismatch",
                    f"{path}: {ref} is a {found.get('role')!r} template",
                    path,
                    ref=ref,
                ),
            )
        if mode not in (found.get("modes") or []):
            issues.append(
                _issue("lint_prompt_mode_mismatch", f"{path}: {ref} is not usable in {mode} mode", path, ref=ref),
            )
    for key, ref in (content.get("fragments") or {}).items():
        found = options.resolve("prompt", ref)
        if found is None:
            continue
        path = f"fragments.{key}"
        if found.get("role") != "fragment":
            issues.append(
                _issue("lint_fragment_role", f"{path}: {ref} is a {found.get('role')!r} template", path, ref=ref),
            )
        if mode not in (found.get("modes") or []):
            issues.append(
                _issue("lint_prompt_mode_mismatch", f"{path}: {ref} is not usable in {mode} mode", path, ref=ref),
            )
    return issues


def editor_brief_issues(*, editor_harness: bool, brief_content: Mapping[str, Any] | None) -> list[Issue]:
    """``editor_harness_agent_brief`` when a non-agentic editor (aider) gets a brief not tagged ``editor``."""
    if not editor_harness or brief_content is None:
        return []
    if "editor" in (brief_content.get("tags") or []):
        return []
    return [
        make_issue(
            "editor_harness_agent_brief",
            message="a non-agentic editor harness needs a brief tagged 'editor' (no claims about running code)",
        ),
    ]


# =====================================================================================
# Stacks, styles, guidance
# =====================================================================================


def _style_rules(content: Mapping[str, Any], options: LintOptions) -> list[Issue]:
    del options
    return [
        _issue(
            "lint_style_frontend_unknown",
            f"compatible_frontends names unknown frontend {name!r}",
            f"compatible_frontends.{index}",
        )
        for index, name in enumerate(content.get("compatible_frontends") or [])
        if name not in _KNOWN_FRONTENDS
    ]


def _guidance_rules(content: Mapping[str, Any], options: LintOptions) -> list[Issue]:
    del options
    if content.get("security_hint") != "none" and not content.get("fragment"):
        return [
            _issue(
                "lint_guidance_fragment_missing",
                f"security_hint {content.get('security_hint')!r} needs a fragment",
                "fragment",
            ),
        ]
    return []


# =====================================================================================
# Starters: the rule list of section 11.4 is WP-05's (``build/starter_rules.py``)
# =====================================================================================


def _starter_rules(content: Mapping[str, Any], options: LintOptions) -> list[Issue]:
    from backend.generation.build import starter_rules

    issues: list[Issue] = []
    read = options.read_blob or read_store_blob
    files: dict[str, bytes] = {}
    for path, entry in sorted((content.get("files") or {}).items()):
        data = read(str(entry.get("blob")))
        if data is None:
            issues.append(
                _issue("lint_starter_blob_missing", f"{path}: blob {entry.get('blob')} is missing", f"files.{path}"),
            )
        else:
            files[path] = data
    if not (content.get("forbidden_content_check") or {}).get("passed", False):
        issues.append(
            _issue(
                "lint_starter_forbidden_content",
                "the composer's forbidden-content check did not pass",
                "forbidden_content_check",
            ),
        )
    stack = None
    if options.resolve is not None and content.get("stack_profile"):
        stack = options.resolve("stack", str(content["stack_profile"]))
    if issues and len(files) < len(content.get("files") or {}):
        # Without every file the tree rules would report false positives (a "missing" Dockerfile).
        return issues
    return issues + starter_rules.starter_issues(content, files, stack=stack)


# =====================================================================================
# Detectors
# =====================================================================================


def _detector_rules(content: Mapping[str, Any], options: LintOptions) -> list[Issue]:
    del options
    engine = content.get("engine")
    rule = str(content.get("rule", ""))
    if engine == "regex":
        try:
            re.compile(rule)
        except re.error as exc:
            return [_issue("lint_detector_rule_invalid", f"the regex does not compile: {exc}", "rule")]
        if not rule:
            return [_issue("lint_detector_rule_invalid", "a regex detector needs a rule", "rule")]
    elif engine == "semgrep":
        try:
            data = yaml.safe_load(rule)
        except yaml.YAMLError as exc:
            return [_issue("lint_detector_rule_invalid", f"the Semgrep rule is not YAML: {exc}", "rule")]
        if not isinstance(data, Mapping) or not isinstance(data.get("rules"), list) or not data["rules"]:
            return [_issue("lint_detector_rule_invalid", "a Semgrep rule file needs a non-empty 'rules' list", "rule")]
    elif engine == "ts_query" and not rule.strip():
        return [_issue("lint_detector_rule_invalid", "a tree-sitter detector needs a query", "rule")]
    return []


_RULES: Final[Mapping[str, Callable[[Mapping[str, Any], LintOptions], list[Issue]]]] = {
    "task": _task_rules,
    "prompt": _prompt_rules,
    "prompt_set": _prompt_set_rules,
    "style": _style_rules,
    "guidance": _guidance_rules,
    "starter": _starter_rules,
    "detector": _detector_rules,
}
