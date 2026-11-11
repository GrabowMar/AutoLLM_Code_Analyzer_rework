"""TaskSpec to ``AcceptancePlan`` compiler (spec sections 8.1-8.4; ``COMPILER_VERSION = 2``).

``compile(task_version, auth_mode, stack)`` turns the structured endpoints of a task into tester checks
(table of 8.1), merges the task's hand-written ``acceptance`` (an ``id`` collision means the hand-written
check wins, 8.2), tags every check with the prose variants that state it (``library.lint.tag_checks``; the
hand-written ones read ``TaskSpecVersion.derived.check_statedness``) and compiles the task's probes with
their preconditions and positive controls (8.3). The output is a ``TesterPlan``: the ``AcceptancePlan``
contract plus the facts the tester needs that the contract does not carry (signed webhook routes, the health
path, the mock server host). It never contains a secret: ``ADMIN_PASSWORD`` and ``WEBHOOK_SECRET`` reach the
tester through its environment.

Generated check ids (stable, the slot linker and results key on them):

=========================  ============  ==========================================================
``health``                 infra         ``GET <run_contract.health_path>`` -> 200
``index_served``           infra         ``GET /`` -> 200 text/html (backend-served frontend only)
``spa_fallback``           infra         ``GET /llmlab/client/route`` -> 200 text/html (same)
``<ep>.ok``                functional    success status and response schema (actor by ``auth``)
``<ep>.anon_401``          auth          no credentials -> 401 (``user``/``owner``/``admin``)
``<ep>.other_user``        ownership     user_b on user_a's resource -> 403/404 (``owner``)
``<ep>.non_admin_403``     auth          user_a -> 403 (``admin``)
``<ep>.invalid.missing``   validation    a required body field dropped -> 400/422
``<ep>.invalid.too_long``  validation    a string one past ``maxLength`` -> 400/422
``<ep>.invalid.enum``      validation    a value outside ``enum`` -> 400/422
``<ep>.duplicate``         negative      the same unique value twice -> 409 (when 409 is listed)
``<ep>.oversize``          negative      a 12 MiB body -> 413 (when 413 is listed)
``<ep>.page_size``         pagination    ``page_size=2`` with three items -> exactly two
``<ep>.filter.<param>``    filter        an enum/bool filter returns only matching items
``<ep>.search``            filter        a search term finds the item that holds it
``<ep>.persists``          persistence   create, restart the app, read it back
=========================  ============  ==========================================================

Every generated check ``depends_on`` ``health``. A check that needs a resource gets setup steps that create
it through the collection's ``POST`` endpoint (recursively for nested paths) and capture the path
parameters; a check whose resource cannot be created this way is not generated (the hand-written
acceptance covers it). ``checks_for_endpoints`` lists the check ids linked to endpoints (slot targets).

Compiler v2 (the instrument fix; the check ids, categories and expectations of v1 are unchanged):

* **Valid requests are valid.** A request value comes from the request schema and what the field is
  (``_field_class``: its ``format`` and its name): an ISBN has a correct checksum, a time is ``HH:MM``, a phone
  number is one, an "image" is a real PNG, an end date is later than the start date, a number lies inside its
  bounds. Values that must differ between requests are tester placeholders (``PLACEHOLDER_EXAMPLES``; the
  authoritative list with formats is ``GENERATORS.md`` of the instrument fix).
* **One fault per negative check.** ``.invalid.*`` start from the valid body and break only the tested field;
  the too-long field is a free-text one when the body has one, and a too-long URL / e-mail is still a URL /
  an e-mail.
* **Repeated creates differ.** The creates of ``page_size`` / ``filter`` / ``search`` setups are ``fresh``: the
  tester renders them with a step-local ``rand``, so a unique field does not collide with the previous create.
* **A setup is not an assertion.** A setup create accepts 200 and 201 (``_created``) and is sent by the admin
  when only the admin may create; ``<ep>.ok`` still asserts the exact status.
* **Order.** ``.invalid.*`` checks nobody depends on run last, so a request an app wrongly accepted cannot
  change what a later list / filter / search check sees.
* **Deterministic.** The content is canonicalised (sorted keys) first: the plan of a task does not depend on
  the key order of the YAML file or of the JSON column it was read from.

Round 2 of the instrument fix (same ``COMPILER_VERSION``; the check ids and expectations are unchanged):

* ``.invalid.too_long`` breaks every length-limited field whose limit a prose variant states, one request
  each (``also`` of the compiled check; it passes when all are refused), in a fixed order
  (``_too_long_fields``). An over-long base64 value is valid base64, an over-long XML document well-formed,
  an over-long password still the valid password, an over-long file name keeps its extension.
* ``.invalid.enum`` sends a plain lower-case word (``_not_an_option``).
* ``.oversize`` of an upload sends a valid file of the same kind that is only too large (``_OVERSIZE_FILES``).
* A hand-written setup create accepts 200 and 201 where a prose variant the check is stated in leaves the
  status open (``_normalise_setups``; each change is listed in ``plan.adjusted``).
* Probes: the positive control accepts any 2xx of a 2xx endpoint (``_answered``); a rate-limit probe on an
  endpoint other than sign-in repeats that endpoint's valid request; a stored-XSS probe whose view is public
  creates its payload publicly visible; an overlay may replace a probe's setup steps (``probes.<id>``).
* Statedness: ``library.lint`` rule table 2 (a limit, an option list, a status counts as stated in a variant
  only when that variant gives the value).

Round 3 (same ``COMPILER_VERSION``; uniformity: a repair is a rule for every task or it does not exist):

* **Default-valued query parameters are sent** (``_default_query``): a parameter whose default only some
  prose variants state (``page``, ``page_size``, ``count``, a "default today" date) is part of every valid
  request, with the declared default as its value. Not an option-valued parameter, not one the prose defines
  only together with another optional parameter.
* **Listed references** (``_reference_sources``): a body field that names an existing resource of another
  entity (``product_id``, ``answers[].question_id``) is the id the app itself lists on that entity's public
  list endpoint, read by a leading setup step of the check or probe (``{listed_<field>}``); a literal id in a
  hand-written request that must succeed is replaced the same way.
* **Reference id types** (``_reference_id_types``): a response property of a ``type: ref`` field accepts the
  id types the task gives the referenced entity (plus ``null`` where it was nullable).
* **Hand-written requests are completed** (``_complete_handwritten``): the properties (and default-valued
  query parameters) a hand-written ``POST`` / ``PUT`` body omits get the compiler's valid values; nothing the
  check sets is changed, a property the check's expectation is about is left out.
* An over-long URL keeps its file extension; a rate-limit probe on an endpoint without a "too many requests"
  answer in the spec counts the task's outbox messages (``_outbox_observation``) or is not measurable.
* Statedness: ``library.lint`` rule table 3 (a sent option value, an exact 200 of a ``POST``).

Every change of the three rules that touch an expectation or a hand-written request is an entry of
``plan.adjusted`` (``reference_types``, ``completed``).

Instrument overlay: ``data/tasks/<slug>/tester.yaml`` (never part of the hashed task content)::

    schema: 1
    notes: where each value comes from (required)
    samples: {field: value}              # the valid sample of that request field, wherever it is synthesised
    endpoints: {<endpoint id>: {json|form|query|files: {...}}}   # merged over the endpoint's valid request
    checks:
      <check id>:
        why: required, spec-based
        request: {...}     # deep-merged into the compiled request (null removes a key)
        setup: [...]       # replaces the setup steps
        expect: {...}      # deep-merged into expect; recorded in ``plan.adjusted``
        stated_in: [...]   # prose variants added to the check's statedness; recorded in ``plan.adjusted``
        skip: true         # the check is dropped and recorded in ``plan.skipped``
    probes:
      <probe id>:
        why: required, spec-based
        setup: [...]       # replaces the probe's setup steps (same captures); recorded in ``plan.adjusted``

Precedence of a request field: ``checks.<id>.request`` > ``endpoints.<id>`` > ``samples.<field>`` > the
sampler. Loading is strict (``OverlayError``; ``overlay_problems`` for the lint): unknown keys, unknown
endpoint / check ids, a patch without ``why``, an unknown placeholder, a sample that violates a request schema
or that is constant for a unique field. Every check the overlay changed carries ``overlay: true``.
``fixtures_digest()`` is the digest over all overlays of the tree (suites pin it).
"""

from __future__ import annotations

import contextvars
import copy
import dataclasses
import hashlib
import json
import math
import re
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

import yaml
from pydantic import Field
from pydantic import ValidationError

from backend.generation.contracts.evaluation import AcceptancePlan
from backend.generation.contracts.evaluation import AuthDriverConfig
from backend.generation.contracts.evaluation import CompiledCheck
from backend.generation.contracts.evaluation import CompiledProbe
from backend.generation.contracts.evaluation import CompiledSetupStep

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Mapping

    from backend.generation.models import StackProfile
    from backend.generation.models import TaskSpecVersion

__all__ = [
    "AUTH_MODES",
    "COMPILER_VERSION",
    "OVERLAY_FILE",
    "PER_CONTEXT",
    "PLACEHOLDER_EXAMPLES",
    "UPSTREAM_HOSTS",
    "Overlay",
    "OverlayError",
    "TesterPlan",
    "auth_config",
    "checks_for_endpoints",
    "compile",
    "compile_content",
    "fixtures_digest",
    "known_placeholder",
    "load_overlay",
    "overlay_path",
    "overlay_problems",
    "parse_overlay",
    "plan_json",
    "schema_violations",
]

COMPILER_VERSION: Final = 2
AUTH_MODES: Final = ("contract_bearer", "cookie_session", "spec_defined")
DEFAULT_BASE_URL: Final = "http://app:8000"
_PARAM: Final = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SETUP_DEPTH: Final = 3
# Shares no token with the sampled username (``llmlab{rand}``): a "password too similar to the username" rule is a
# reasonable implementation choice. ``{alpha}`` is stable inside one context (register, then log in).
_TEST_PASSWORD: Final = "Tk7-{alpha}-Wq9!"  # noqa: S105 - a throwaway test credential


class TesterPlan(AcceptancePlan):
    """``/in/plan.json``: the ``AcceptancePlan`` contract plus tester facts it does not carry."""

    health_path: str = "/api/health"
    signed_routes: list[dict[str, str]] = Field(default_factory=list)  # webhook routes the tester signs
    mock_host: str = "mock.internal"
    mock_port: int = 9009
    has_frontend: bool = False


# =====================================================================================
# Auth drivers: 8.4
# =====================================================================================


def _endpoint_map(content: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(ep["id"]): ep for ep in content.get("endpoints") or []}


def auth_config(content: Mapping[str, Any], auth_mode: str) -> AuthDriverConfig:
    """The tester's driver for ``auth_mode`` (contract modes are fixed; ``spec_defined`` follows ``auth_flow``)."""
    if auth_mode == "contract_bearer":
        return AuthDriverConfig(mode="contract_bearer")
    if auth_mode == "cookie_session":
        return AuthDriverConfig(
            mode="cookie_session",
            credential="cookie",
            token_json_path=None,
            csrf="double_submit_cookie",
        )
    if auth_mode != "spec_defined":
        msg = f"unknown auth mode {auth_mode!r}"
        raise ValueError(msg)
    flow = dict(content.get("auth_flow") or {})
    endpoints = _endpoint_map(content)

    def path_of(endpoint_id: str | None) -> str | None:
        return str(endpoints[endpoint_id]["path"]) if endpoint_id and endpoint_id in endpoints else None

    credential = flow.get("credential") or "bearer"
    return AuthDriverConfig.model_validate(
        {
            "mode": "spec_defined",
            "register_path": path_of(flow.get("register")),
            "login_path": path_of(flow.get("login")) or "/api/auth/login",
            "logout_path": path_of(flow.get("logout")),
            "credential": credential,
            "token_json_path": flow.get("token_json_path") or ("token" if credential == "bearer" else None),
            "csrf": flow.get("csrf") or "none",
            "auth_flow": flow or None,
        },
    )


# =====================================================================================
# Tester placeholders (mirror of images/eval-tester/tester.py; authoritative list: GENERATORS.md)
# =====================================================================================

# name -> an example rendering. Overlay samples are validated against the request schemas with these values, and
# string lengths are estimated with them (the real values have the same shape and length).
PLACEHOLDER_EXAMPLES: Final[dict[str, str]] = {
    "rand": "0a1b2c3d",
    "alpha": "qwertzui",
    "uuid": "3f2b8c1e-5d4a-4e6f-8a9b-0c1d2e3f4a5b",
    "isbn13": "9780306406157",
    "isbn10": "0306406152",
    "today": "2030-06-03",
    "now": "2030-06-03T12:00:00Z",
    "future_date": "2030-09-10",
    "future_date2": "2030-09-17",
    "past_date": "2029-11-06",
    "future_datetime": "2030-09-10T10:30:00Z",
    "future_datetime2": "2030-09-10T11:30:00Z",
    "past_datetime": "2029-11-06T10:30:00Z",
    "time_hhmm": "10:30",
    "time_hhmm2": "11:30",
    "png_b64": "iVBORw0KGgo=",
    "jpeg_b64": "/9j/4AAQSkY=",
    "gif_b64": "R0lGODlhAQA=",
    "pdf_b64": "JVBERi0xLjQ=",
    "wav_b64": "UklGRkQDAAA=",
    "png_oversize_b64": "iVBORw0KGgo=",
    "wav_oversize_b64": "UklGRkQDAAA=",
    "oversize": "x",
    "user_a_username": "usera0a1b2c3d",
    "user_b_username": "userb0a1b2c3d",
    "user_c_username": "userc0a1b2c3d",
    "user_a_password": "Pw-0a1b2c3d4e5f-9z!",
    "user_b_password": "Pw-0a1b2c3d4e5f-9z!",
    "user_c_password": "Pw-0a1b2c3d4e5f-9z!",
    "admin_username": "admin",
    "admin_password": "admin-password",
    "upstream_base": "http://example.com",
    "mock_base": "http://mock.internal:9009",
}
# Generators whose value differs between check contexts (and between ``fresh`` setup steps): a sample for a
# field the spec marks unique must contain one of them (``digitsN`` counts too).
PER_CONTEXT: Final = frozenset(
    {
        "rand",
        "alpha",
        "uuid",
        "isbn13",
        "isbn10",
        "future_date",
        "future_date2",
        "past_date",
        "future_datetime",
        "future_datetime2",
        "past_datetime",
        "time_hhmm",
        "time_hhmm2",
    },
)
_DIGITS: Final = re.compile(r"digits([1-9]|1[0-8])")
# Placeholders whose rendered length is not the length of the example (never used for length arithmetic).
_VARIABLE_LENGTH: Final = frozenset(
    {
        "oversize",
        "png_oversize_b64",
        "wav_oversize_b64",
        "upstream_base",
        "mock_base",
        "admin_username",
        "admin_password",
    },
)


def known_placeholder(name: str) -> bool:
    return name in PLACEHOLDER_EXAMPLES or _DIGITS.fullmatch(name) is not None


def placeholder_names(value: Any) -> set[str]:
    """Every ``{name}`` in the strings of ``value`` (deep)."""
    if isinstance(value, str):
        return set(_PARAM.findall(value))
    if isinstance(value, dict):
        return set().union(*(placeholder_names(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(placeholder_names(v) for v in value)) if value else set()
    return set()


def render_example(value: Any) -> Any:
    """``value`` with every known placeholder replaced by its example (unknown names stay as written)."""

    def repl(m: re.Match[str]) -> str:
        name = m.group(1)
        digits = _DIGITS.fullmatch(name)
        if digits:
            return "1234567890123456789"[: int(digits.group(1))]
        return PLACEHOLDER_EXAMPLES.get(name, m.group(0))

    if isinstance(value, str):
        return _PARAM.sub(repl, value)
    if isinstance(value, dict):
        return {k: render_example(v) for k, v in value.items()}
    if isinstance(value, list):
        return [render_example(v) for v in value]
    return value


def rendered_len(value: str) -> int | None:
    """The length ``value`` has once the tester substituted it (``None``: not known at compile time)."""
    names = placeholder_names(value)
    if any(n in _VARIABLE_LENGTH or not known_placeholder(n) for n in names):
        return None
    return len(render_example(value))


_JSON_TYPES: Final[dict[str, Any]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: not isinstance(v, bool) and (isinstance(v, int) or (isinstance(v, float) and v.is_integer())),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}
_FORMATS: Final[dict[str, re.Pattern[str]]] = {
    "date": re.compile(r"\d{4}-\d{2}-\d{2}"),
    "date-time": re.compile(r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?([Zz]|[+-]\d{2}:?\d{2})?"),
    "time": re.compile(r"\d{2}:\d{2}(:\d{2})?"),
    "email": re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+"),
    "uri": re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:\S+"),
    "url": re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:\S+"),
    "uuid": re.compile(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"),
}


def schema_violations(value: Any, schema: Any, where: str = "value") -> list[str]:
    """Why ``value`` is not valid under the JSON Schema subset the task specs use (empty: valid)."""
    if isinstance(schema, list) and schema and all(isinstance(t, str) for t in schema):
        schema = {"type": schema}  # shorthand of the task specs: a bare list of type names
    if not isinstance(schema, dict):
        return []
    declared = schema.get("type")
    allowed = [declared] if isinstance(declared, str) else [str(t) for t in declared or []]
    if allowed and not any(_JSON_TYPES.get(t, lambda _v: True)(value) for t in allowed):
        return [f"{where}: {value!r} is not of type {'/'.join(allowed)}"]
    out: list[str] = []
    if isinstance(schema.get("enum"), list) and value not in schema["enum"]:
        out.append(f"{where}: {value!r} is not one of {schema['enum']!r}")
    if isinstance(value, str):
        if isinstance(schema.get("minLength"), int) and len(value) < schema["minLength"]:
            out.append(f"{where}: shorter than minLength {schema['minLength']}")
        if isinstance(schema.get("maxLength"), int) and len(value) > schema["maxLength"]:
            out.append(f"{where}: longer than maxLength {schema['maxLength']}")
        if isinstance(schema.get("pattern"), str) and re.search(schema["pattern"], value) is None:
            out.append(f"{where}: does not match pattern {schema['pattern']!r}")
        fmt = _FORMATS.get(str(schema.get("format") or ""))
        if fmt is not None and fmt.fullmatch(value) is None:
            out.append(f"{where}: {value!r} is not a valid {schema['format']}")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        for key, bad in (
            ("minimum", lambda limit: value < limit),
            ("maximum", lambda limit: value > limit),
            ("exclusiveMinimum", lambda limit: value <= limit),
            ("exclusiveMaximum", lambda limit: value >= limit),
        ):
            limit = schema.get(key)
            if isinstance(limit, (int, float)) and not isinstance(limit, bool) and bad(limit):
                out.append(f"{where}: {value!r} violates {key} {limit!r}")
    elif isinstance(value, list):
        if isinstance(schema.get("minItems"), int) and len(value) < schema["minItems"]:
            out.append(f"{where}: fewer than minItems {schema['minItems']}")
        if isinstance(schema.get("maxItems"), int) and len(value) > schema["maxItems"]:
            out.append(f"{where}: more than maxItems {schema['maxItems']}")
        for index, item in enumerate(value):
            out += schema_violations(item, schema.get("items"), f"{where}[{index}]")
    elif isinstance(value, dict):
        out += [f"{where}: required {name!r} is missing" for name in schema.get("required") or [] if name not in value]
        for name, sub in (schema.get("properties") or {}).items():
            if name in value:
                out += schema_violations(value[name], sub, f"{where}.{name}")
    return out


# =====================================================================================
# Instrument overlay: data/tasks/<slug>/tester.yaml (compiler v2)
# =====================================================================================

OVERLAY_FILE: Final = "tester.yaml"
OVERLAY_SCHEMA: Final = 1
UPSTREAM_HOSTS: Final = ("example.com", "www.example.com", "example.org")
UPSTREAM_PORT: Final = 80
_OVERLAY_KEYS: Final = frozenset({"schema", "notes", "samples", "endpoints", "checks", "probes"})
# What an overlay may change of a compiled probe (add-only key of overlay schema 1): the setup steps that
# create what the probe attacks, never the attack, the control's expectation or the verdict rule.
_PROBE_PATCH_KEYS: Final = frozenset({"why", "setup"})
_ENDPOINT_PARTS: Final = frozenset({"json", "form", "query", "files"})
_PATCH_KEYS: Final = frozenset({"why", "request", "setup", "expect", "skip", "stated_in"})
_PROSE_VARIANTS: Final = ("minimal", "standard", "exhaustive")
# Infra checks exist only for stacks with a backend-served frontend: patching them is never an unknown id.
_INFRA_IDS: Final = frozenset({"health", "index_served", "spa_fallback"})


class OverlayError(ValueError):
    """A task's ``tester.yaml`` is not usable: the plan is not compiled (never a silently ignored overlay)."""

    def __init__(self, slug: str | None, issues: Iterable[str]) -> None:
        self.slug = slug
        self.issues = list(issues)
        super().__init__(f"instrument overlay of task {slug or '(unnamed)'}: " + "; ".join(self.issues))


@dataclasses.dataclass(frozen=True)
class Overlay:
    """A parsed ``tester.yaml`` (module docstring). The default instance is "no overlay"."""

    slug: str | None = None
    notes: str = ""
    samples: Mapping[str, Any] = dataclasses.field(default_factory=dict)
    endpoints: Mapping[str, Mapping[str, Any]] = dataclasses.field(default_factory=dict)
    checks: Mapping[str, Mapping[str, Any]] = dataclasses.field(default_factory=dict)
    probes: Mapping[str, Mapping[str, Any]] = dataclasses.field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.samples or self.endpoints or self.checks or self.probes)


_NO_OVERLAY: Final = Overlay()
_ACTIVE: Final[contextvars.ContextVar[Overlay]] = contextvars.ContextVar("acceptance_overlay", default=_NO_OVERLAY)
# Request fields that name an existing resource of another entity (``_reference_sources``), for the task being
# compiled: field name -> {placeholder, request, capture}.
_REFS: Final[contextvars.ContextVar[Mapping[str, Mapping[str, Any]]]] = contextvars.ContextVar(
    "acceptance_references",
    default={},  # noqa: B039 - never mutated
)


def _data_dir(data_dir: Path | str | None = None) -> Path:
    if data_dir is not None:
        return Path(data_dir)
    from django.conf import settings

    return Path(settings.GENERATION_LIBRARY_DATA_DIR)


def overlay_path(slug: str, data_dir: Path | str | None = None) -> Path:
    return _data_dir(data_dir) / "tasks" / slug / OVERLAY_FILE


def fixtures_digest(data_dir: Path | str | None = None) -> str:
    """sha256 (64 lower-case hex) over every ``data/tasks/<slug>/tester.yaml``: sorted slugs, the file bytes.

    The digest of a tree without any overlay is a constant, so a suite can pin "no fixtures" too. Deterministic:
    nothing but the slugs and the bytes on disk enters it.
    """
    digest = hashlib.sha256(b"llmlab-tester-fixtures/1\n")
    root = _data_dir(data_dir) / "tasks"
    paths = sorted(root.glob(f"*/{OVERLAY_FILE}"), key=lambda p: p.parent.name) if root.is_dir() else []
    for path in paths:
        data = path.read_bytes()
        digest.update(f"{path.parent.name}\0{len(data)}\0".encode())
        digest.update(data)
    return digest.hexdigest()


def _patch_issues(check_id: str, patch: Any) -> list[str]:
    where = f"checks.{check_id}"
    if not isinstance(patch, dict):
        return [f"{where}: must be a mapping"]
    out = [f"{where}: unknown key {key!r}" for key in sorted(set(patch) - _PATCH_KEYS)]
    why = patch.get("why")
    if not isinstance(why, str) or not why.strip():
        out.append(f"{where}: `why` is required (a written, spec-based justification)")
    if not set(patch) & (_PATCH_KEYS - {"why"}):
        out.append(f"{where}: the patch changes nothing")
    if "skip" in patch and patch["skip"] is not True:
        out.append(f"{where}.skip: must be `true` when present")
    if patch.get("skip") is True and set(patch) & {"request", "setup", "expect", "stated_in"}:
        out.append(f"{where}: a skipped check takes no other patch")
    if "request" in patch and not isinstance(patch["request"], dict):
        out.append(f"{where}.request: must be a mapping")
    if "expect" in patch and not isinstance(patch["expect"], dict):
        out.append(f"{where}.expect: must be a mapping")
    setup = patch.get("setup")
    if "setup" in patch and not (isinstance(setup, list) and all(isinstance(s, dict) for s in setup)):
        out.append(f"{where}.setup: must be a list of setup steps")
    if "stated_in" in patch:
        stated = patch["stated_in"]
        if not isinstance(stated, list) or any(v not in _PROSE_VARIANTS for v in stated):
            out.append(f"{where}.stated_in: must be a list of {'/'.join(_PROSE_VARIANTS)}")
    return out


def _probe_patch_issues(probe_id: str, patch: Any) -> list[str]:
    where = f"probes.{probe_id}"
    if not isinstance(patch, dict):
        return [f"{where}: must be a mapping"]
    out = [f"{where}: unknown key {key!r}" for key in sorted(set(patch) - _PROBE_PATCH_KEYS)]
    why = patch.get("why")
    if not isinstance(why, str) or not why.strip():
        out.append(f"{where}: `why` is required (a written, spec-based justification)")
    setup = patch.get("setup")
    if "setup" not in patch:
        out.append(f"{where}: the patch changes nothing")
    elif not (isinstance(setup, list) and setup and all(isinstance(s, dict) for s in setup)):
        out.append(f"{where}.setup: must be a non-empty list of setup steps")
    out += [
        f"{where}: unknown placeholder {{{unknown}}}"
        for unknown in sorted(placeholder_names([s.get("request") for s in setup or [] if isinstance(s, dict)]))
        if not known_placeholder(unknown) and not _captured(setup or [], unknown)
    ]
    return out


def _captured(steps: Iterable[Any], name: str) -> bool:
    return any(isinstance(step, dict) and name in (step.get("capture") or {}) for step in steps)


def parse_overlay(raw: Any, slug: str | None = None) -> tuple[Overlay, list[str]]:
    """``(overlay, structural issues)`` of a loaded ``tester.yaml`` (ids are checked when the plan compiles)."""
    if not isinstance(raw, dict):
        return Overlay(slug=slug), ["the overlay must be a mapping"]
    issues = [f"unknown key {key!r}" for key in sorted(set(map(str, raw)) - _OVERLAY_KEYS)]
    if raw.get("schema") != OVERLAY_SCHEMA or isinstance(raw.get("schema"), bool):
        issues.append(f"schema: must be {OVERLAY_SCHEMA}")
    notes = raw.get("notes")
    if not isinstance(notes, str) or not notes.strip():
        issues.append("notes: required (where each value comes from)")
    parts: dict[str, dict[str, Any]] = {}
    for key in ("samples", "endpoints", "checks", "probes"):
        value = raw.get(key)
        if value is None:
            value = {}
        if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
            issues.append(f"{key}: must be a mapping with string keys")
            value = {}
        parts[key] = value
    for name, value in parts["samples"].items():
        if value is None:
            issues.append(f"samples.{name}: a sample cannot be null (remove a field with endpoints.<id>)")
        issues += [
            f"samples.{name}: unknown placeholder {{{unknown}}}"
            for unknown in sorted(placeholder_names(value))
            if not known_placeholder(unknown)
        ]
    for endpoint_id, patch in parts["endpoints"].items():
        if not isinstance(patch, dict):
            issues.append(f"endpoints.{endpoint_id}: must be a mapping")
            continue
        issues += [f"endpoints.{endpoint_id}: unknown key {key!r}" for key in sorted(set(patch) - _ENDPOINT_PARTS)]
        issues += [
            f"endpoints.{endpoint_id}.{part}: must be a mapping"
            for part in sorted(set(patch) & _ENDPOINT_PARTS)
            if not isinstance(patch[part], dict)
        ]
    for check_id, patch in parts["checks"].items():
        issues += _patch_issues(check_id, patch)
    for probe_id, patch in parts["probes"].items():
        issues += _probe_patch_issues(probe_id, patch)
    overlay = Overlay(
        slug=slug,
        notes=notes if isinstance(notes, str) else "",
        samples=parts["samples"],
        endpoints={k: v for k, v in parts["endpoints"].items() if isinstance(v, dict)},
        checks={k: v for k, v in parts["checks"].items() if isinstance(v, dict)},
        probes={k: v for k, v in parts["probes"].items() if isinstance(v, dict)},
    )
    return overlay, issues


def load_overlay(slug: str | None, data_dir: Path | str | None = None) -> Overlay:
    """The overlay of task ``slug`` (no file: no overlay). A file that does not parse raises ``OverlayError``."""
    if not slug:
        return _NO_OVERLAY
    path = overlay_path(slug, data_dir)
    if not path.is_file():
        return _NO_OVERLAY
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise OverlayError(slug, [f"{OVERLAY_FILE} does not parse: {exc}"]) from exc
    overlay, issues = parse_overlay(raw, slug)
    if issues:
        raise OverlayError(slug, issues)
    return overlay


def _strip_nulls(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_nulls(v) for k, v in value.items() if v is not None}
    return copy.deepcopy(value)


def _merge(base: Any, patch: Any) -> Any:
    """Deep merge of an overlay patch: mappings merge, a ``null`` removes the key, anything else replaces."""
    if not isinstance(base, dict) or not isinstance(patch, dict):
        return _strip_nulls(patch)
    out = dict(base)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = _merge(out.get(key), value)
    return out


def _sample_schemas(content: Mapping[str, Any], field: str) -> list[tuple[str, dict[str, Any]]]:
    """``(where, schema)`` of every request field named ``field`` (bodies, nested objects, query parameters)."""
    found: list[tuple[str, dict[str, Any]]] = []

    def walk(schema: Any, where: str) -> None:
        if not isinstance(schema, dict):
            return
        for name, sub in (schema.get("properties") or {}).items():
            if not isinstance(sub, dict):
                continue
            if name == field:
                found.append((f"{where}.{name}", sub))
            else:
                walk(sub, f"{where}.{name}")
                walk(sub.get("items"), f"{where}.{name}[]")

    for ep in content.get("endpoints") or []:
        walk(_request_schema(ep, content), str(ep["id"]))
        for q in ep.get("query") or []:
            if str(q.get("name")) == field:
                schema: dict[str, Any] = {"type": _qtype(q)}
                if q.get("enum"):
                    schema["enum"] = list(q["enum"])
                found.append((f"{ep['id']}?{field}", schema))
    return found


def _static_issues(content: Mapping[str, Any], overlay: Overlay) -> list[str]:
    """Overlay issues that need the task but not the compiled checks: samples and endpoint patches."""
    issues: list[str] = []
    unique = _unique_fields(content)
    for name, value in overlay.samples.items():
        schemas = _sample_schemas(content, name)
        if not schemas:
            issues.append(f"samples.{name}: no endpoint of the task has a request field of that name")
        rendered = render_example(value)
        for where, schema in schemas:
            issues += [f"samples.{name} violates {m}" for m in schema_violations(rendered, schema, where)]
        names = placeholder_names(value)
        if name in unique and not (names & PER_CONTEXT or any(_DIGITS.fullmatch(n) for n in names)):
            issues.append(
                f"samples.{name}: the spec marks {name!r} unique, the sample needs a per-context generator",
            )
    endpoints = _endpoint_map(content)
    for endpoint_id, patch in overlay.endpoints.items():
        endpoint = endpoints.get(endpoint_id)
        if endpoint is None:
            issues.append(f"endpoints.{endpoint_id}: the task has no such endpoint")
            continue
        props = ((_request_schema(endpoint, content) or {}).get("properties")) or {}
        for part in ("json", "form"):
            for name, value in (patch.get(part) or {}).items():
                if value is None or name not in props:
                    continue
                where = f"{endpoint_id}.{name}"
                issues += [
                    f"endpoints.{endpoint_id}.{part}.{name} violates {m}"
                    for m in schema_violations(render_example(value), props[name], where)
                ]
    return issues


# =====================================================================================
# Sample values from request schemas (compiler v2: the generic semantic sampler)
# =====================================================================================


def _types(schema: Mapping[str, Any]) -> list[str]:
    value = schema.get("type")
    if isinstance(value, list):
        return [str(t) for t in value if t != "null"] or ["null"]
    return [str(value)] if value else ["string"]


def _tokens(name: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    return [t for t in re.split(r"[^a-z0-9]+", spaced.lower()) if t]


_END_TOKENS: Final = frozenset({"end", "ends", "until", "finish", "finishes", "checkout", "expires", "expiry"})
_PAST_TOKENS: Final = frozenset(
    {"occurred", "birth", "born", "birthday", "dob", "recorded", "measured", "observed", "logged"},
)
_CURRENT_TOKENS: Final = frozenset({"current", "old", "existing"})
# Field classes whose valid value has a format: never the target of ``.invalid.too_long`` or of a search needle
# while a free-text field exists, never asserted by ``.persists``.
_FREE_TEXT: Final = "text"


def _field_class(name: str, schema: Mapping[str, Any]) -> str:  # noqa: PLR0911
    """What a string field holds, from its ``format`` and its name (``text``: free text)."""
    fmt = str(schema.get("format") or "")
    lowered = name.lower()
    tokens = _tokens(name)
    have = set(tokens)
    if fmt in ("date-time", "date", "time", "uuid"):
        return {"date-time": "datetime"}.get(fmt, fmt)
    if fmt == "email" or "email" in lowered:
        return "email"
    if fmt in ("uri", "url", "uri-reference", "iri") or have & {"url", "link", "website", "homepage", "uri"}:
        return "url"
    if schema.get("contentEncoding") == "base64" or have & {"encoded", "base64", "b64"}:
        return "base64"
    if "password" in have or lowered in ("passwd", "pwd"):
        return "password"
    if have & {"phone", "mobile", "telephone", "tel", "fax"}:
        return "phone"
    if "isbn" in have:
        return "isbn"
    if lowered != "username" and tokens[-1:] == ["username"]:
        return "account"
    if "xml" in have:
        return "xml"
    if have & {"host", "hostname", "domain"}:
        return "host"
    if "sku" in have:
        return "sku"
    if "currency" in have:
        return "currency"
    if lowered.endswith("_at") or have & {"datetime", "timestamp"}:
        return "datetime"
    if have & {"date", "deadline", "birthday", "dob"}:
        return "date"
    if tokens[-1:] == ["time"]:
        return "time"
    if have & {"tag", "tags", "labels", "keywords"}:
        return "tag"
    return _FREE_TEXT


def _number(name: str, schema: Mapping[str, Any], *, integer: bool) -> int | float:
    """An interior value: 1 (a year: 2001), moved into ``minimum``/``maximum`` (exclusive bounds included)."""

    def bound(key: str) -> float | None:
        value = schema.get(key)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    value: float = 2001 if "year" in _tokens(name) else 1
    low, high = bound("minimum"), bound("maximum")
    xlow, xhigh = bound("exclusiveMinimum"), bound("exclusiveMaximum")
    if low is not None:
        value = max(value, math.ceil(low) if integer else low)
    if xlow is not None and value <= xlow:
        value = math.floor(xlow) + 1
    if high is not None:
        value = min(value, math.floor(high) if integer else high)
    if xhigh is not None and value >= xhigh:
        value = (math.ceil(xhigh) - 1) if integer else (xhigh + (low if low is not None else xlow or 0)) / 2
    return int(value) if integer else float(value)


def _fit(value: str, name: str, schema: Mapping[str, Any], *, unique: bool) -> str:
    """``value`` when it fits ``minLength``/``maxLength``, else the nearest value that does."""
    max_len = schema.get("maxLength")
    min_len = schema.get("minLength")
    size = rendered_len(value)
    if isinstance(max_len, int) and size is not None and size > max_len:
        if value == "{isbn13}" and max_len >= len(PLACEHOLDER_EXAMPLES["isbn10"]):
            return "{isbn10}"
        fits = unique and max_len >= len(PLACEHOLDER_EXAMPLES["rand"])
        short = "{rand}" if fits else "x" * max(int(min_len or 1), 1)
        return short[:max_len] if "{" not in short else short
    if isinstance(min_len, int) and size is not None and size < min_len and _field_class(name, schema) == _FREE_TEXT:
        return value + "x" * (min_len - size)
    return value


def _string(name: str, schema: Mapping[str, Any], *, unique: bool) -> str:  # noqa: PLR0911
    """A valid string for a field: the most conventional representative of what its name/format says it holds."""
    kind = _field_class(name, schema)
    have = set(_tokens(name))
    second = "2" if have & _END_TOKENS else ""
    past = bool(have & _PAST_TOKENS)
    if kind == "datetime":
        return "{past_datetime}" if past else f"{{future_datetime{second}}}"
    if kind == "date":
        return "{past_date}" if past else f"{{future_date{second}}}"
    if kind == "time":
        return f"{{time_hhmm{second}}}"
    if kind == "uuid":
        return "{uuid}"
    if kind == "email":
        return "llmlab{rand}@example.com"
    if kind == "url":
        return "https://example.com/llmlab/{rand}"
    if kind == "base64":
        return "bGxtbGFiIGRhdGE="  # "llmlab data"
    if kind == "password":
        return _TEST_PASSWORD
    if kind == "phone":
        return "+1202555{digits4}"
    if kind == "isbn":
        return "{isbn13}"
    if kind == "account":
        return "{user_c_username}"  # an existing account that never acts in the plan
    if kind == "xml":
        return "<llmlab><item>llmlab</item></llmlab>"
    if kind == "host":
        return "llmlab-{rand}.example.com"
    if kind == "sku":
        return "SKU-{digits9}"
    if kind == "currency":
        return "USD"
    if kind == "tag":
        return "llmlab{rand}" if unique else "llmlab"
    base = f"llmlab {name} {{rand}}" if unique else f"llmlab {name}"
    if unique and name in ("username", "login", "name", "slug"):
        base = f"llmlab{name[:4]}{{rand}}"
    return base


def _sample(name: str, schema: Mapping[str, Any], *, unique: bool = False, item: bool = False) -> Any:  # noqa: PLR0911
    """A valid value for ``schema`` (deterministic; per-context values are tester placeholders).

    Precedence: the overlay's ``samples.<name>`` (never for the items of an array), ``enum``, ``default``, then
    the type and, for strings, the field class (``_field_class``).
    """
    samples = _ACTIVE.get().samples
    if not item and name in samples:
        return copy.deepcopy(samples[name])
    reference = _REFS.get().get(name)
    if reference is not None and not schema.get("enum") and set(_types(schema)) & {"integer", "string", "number"}:
        # the id of a resource the app already holds, read from its public list (``_reference_sources``)
        return "{" + str(reference["placeholder"]) + "}"
    if schema.get("enum"):
        options = [v for v in schema["enum"] if v is not None]
        return options[0] if options else None
    if schema.get("default") is not None and not unique:
        return schema["default"]
    kind = _types(schema)[0]
    if kind in ("integer", "number"):
        return _number(name, schema, integer=kind == "integer")
    if kind == "boolean":
        return False
    if kind == "array":
        items = schema.get("items") if isinstance(schema.get("items"), dict) else {"type": "string"}
        first = _sample(name, items, item=True)
        count = max(int(schema.get("minItems") or 1), 1)
        # ``minItems`` distinct values (two poll options); non-strings repeat
        more = [f"{first} {i}" if isinstance(first, str) else copy.deepcopy(first) for i in range(2, count + 1)]
        return [first, *more]
    if kind == "object":
        return _body(schema, unique_fields=set())
    return _fit(_string(name, schema, unique=unique), name, schema, unique=unique)


# A JSON upload: a ``filename`` next to one of these string fields means "the file's bytes, base64".
_UPLOAD_DATA_FIELDS: Final = ("data", "content", "file", "image", "bytes", "payload", "file_data", "image_data")


def _body(schema: Mapping[str, Any] | None, *, unique_fields: set[str]) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {}
    out: dict[str, Any] = {}
    props = schema.get("properties") or {}
    for name, sub in props.items():
        if not isinstance(sub, dict) or sub.get("format") == "binary" or sub.get("readOnly"):
            continue
        out[name] = _sample(name, sub, unique=name in unique_fields)
    samples = _ACTIVE.get().samples
    upload = next((n for n in _UPLOAD_DATA_FIELDS if n in out and "string" in _types(props[n])), None)
    if "filename" in out and upload is not None and "string" in _types(props["filename"]):
        # a real, small, valid PNG and a matching name
        if upload not in samples:
            out[upload] = "{png_b64}"
        if "filename" not in samples:
            out["filename"] = "llmlab-{rand}.png"
        for mime in ("content_type", "mime_type", "mimetype", "media_type"):
            if mime in out and mime not in samples and not props[mime].get("enum"):
                out[mime] = "image/png"
    return out


def _file_fields(schema: Mapping[str, Any] | None) -> list[str]:
    props = (schema or {}).get("properties") or {}
    return [
        n
        for n, s in props.items()
        if isinstance(s, dict) and (s.get("format") == "binary" or s.get("contentMediaType"))
    ]


def _file_sample(name: str, schema: Mapping[str, Any] | None) -> dict[str, Any]:
    """The upload of one multipart file field: a real small file of the kind its name / media type says."""
    sub = ((schema or {}).get("properties") or {}).get(name) or {}
    media = str(sub.get("contentMediaType") or "")
    have = set(_tokens(name))
    if media.startswith("image/") or have & {"image", "photo", "avatar", "picture", "logo", "thumbnail", "img"}:
        kinds = {"image/jpeg": ("jpg", "{jpeg_b64}"), "image/gif": ("gif", "{gif_b64}")}
        ext, data = kinds.get(media, ("png", "{png_b64}"))
        ctype = media if media in kinds else "image/png"
        return {"filename": f"llmlab.{ext}", "content_b64": data, "content_type": ctype}
    if media == "application/pdf" or "pdf" in have:
        return {"filename": "llmlab.pdf", "content_b64": "{pdf_b64}", "content_type": "application/pdf"}
    if media.startswith("audio/") or have & {"audio", "sound", "track", "recording"}:
        return {"filename": "llmlab.wav", "content_b64": "{wav_b64}", "content_type": "audio/wav"}
    return {"filename": "llmlab.txt", "content": "llmlab file {rand}\n", "content_type": "text/plain"}


def _unique_fields(content: Mapping[str, Any]) -> set[str]:
    return {str(f["name"]) for e in content.get("entities") or [] for f in e.get("fields") or [] if f.get("unique")}


def _qtype(q: Mapping[str, Any]) -> str:
    return {"int": "integer", "float": "number", "bool": "boolean", "enum": "string"}.get(str(q.get("type")), "string")


_FIELD_TYPES: Final = {
    "string": "string",
    "text": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "enum": "string",
    "date": "string",
    "email": "string",
    "url": "string",
}


def _entity_for(endpoint: Mapping[str, Any], content: Mapping[str, Any]) -> dict[str, Any] | None:
    """The entity an endpoint writes: ``User`` for register/login, else the one its collection segment names.

    v2: ``owner_entity`` alone no longer makes an endpoint a writer of that entity. An action on a resource
    (``POST /api/previews/{id}/refresh``, ``POST /api/files/{id}/shares``, ``POST /api/auth/logout``) has no
    stated body, and sending the entity's fields to it is not a valid request of the task.
    """
    entities = {str(e["name"]).lower(): e for e in content.get("entities") or []}
    path = str(endpoint["path"])
    if path.startswith("/api/auth/"):
        return entities.get("user") if path.rstrip("/").endswith(("/register", "/login")) else None
    segments = [s for s in path.strip("/").split("/") if s and not _PARAM.fullmatch(s)]
    if not segments:
        return None
    name = segments[-1].lower()
    for candidate in (
        name,
        name[:-3] + "y" if name.endswith("ies") else name,
        name[:-2] if name.endswith("es") else name,
        name[:-1],
    ):
        if candidate in entities:
            return entities[candidate]
    return None


def _entity_schema(endpoint: Mapping[str, Any], content: Mapping[str, Any]) -> dict[str, Any] | None:
    """A request schema derived from the written entity's fields (tasks without ``request_schema``)."""
    entity = _entity_for(endpoint, content)
    if entity is None:
        return None
    props: dict[str, Any] = {}
    required: list[str] = []
    for f in entity.get("fields") or []:
        kind = str(f.get("type"))
        if kind not in _FIELD_TYPES or f.get("ref"):
            continue  # datetimes and references are server-side
        prop: dict[str, Any] = {"type": _FIELD_TYPES[kind]}
        if f.get("max_length"):
            prop["maxLength"] = int(f["max_length"])
        if f.get("enum"):
            prop["enum"] = list(f["enum"])
        if kind == "date":
            prop["format"] = "date"
        if f.get("default") is not None:
            prop["default"] = f["default"]
        props[str(f["name"])] = prop
        if f.get("required"):
            required.append(str(f["name"]))
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required and endpoint.get("method") != "PATCH":
        schema["required"] = required
    return schema


def _request_schema(endpoint: Mapping[str, Any], content: Mapping[str, Any]) -> dict[str, Any] | None:
    """The body schema of an endpoint: its ``request_schema``, else the one derived from the entity it writes."""
    schema = endpoint.get("request_schema")
    if isinstance(schema, dict) and schema:
        return schema
    return _entity_schema(endpoint, content)


def _default_query(endpoint: Mapping[str, Any], content: Mapping[str, Any]) -> dict[str, Any]:
    """The optional query parameters every valid request of ``endpoint`` sends (round 3, every task alike).

    A parameter whose omission means "use a default value" (``default`` in the task spec, or a default a prose
    variant states for it on the line of the endpoint) is sent explicitly whenever some prose variant names
    the endpoint without stating that default: an app built from such a variant cannot know the default, and
    one that asks for the parameter is a reasonable implementation of it (``GET /api/availability`` "lists,
    for one day", ``GET /api/quiz`` "returns ``count`` questions", a paged list). The value is the declared
    default (the same answer as the omission for an app that has the default) or, where the default is given
    in words only ("default today"), the sampler's valid value.

    Not sent: a parameter without any default (a filter or a search term: its omission means "all" in every
    variant); an option-valued parameter (``sort``, ``period``): sending an option relies on a vocabulary
    the same variants do not list either, so there the omission is the conventional request; and a parameter
    the prose defines only together with another optional parameter ("with `lat` and `lng` ... within
    `radius` kilometres"): on its own it has no stated meaning and a strict app may refuse it.
    """
    from backend.generation.library import lint

    prose = [str(text) for text in (content.get("prose") or {}).values()]
    out: dict[str, Any] = {}
    for q in endpoint.get("query") or []:
        name = str(q.get("name"))
        if q.get("required") or q.get("enum") or str(q.get("type")) == "enum":
            continue
        declared = q.get("default") is not None
        worded = [text for text in prose if lint.default_stated(text, endpoint, name)]
        if not declared and not worded:
            continue
        if prose and not any(lint.endpoint_mentioned(text, endpoint) and text not in worded for text in prose):
            continue  # every variant that describes the endpoint states the default
        if any(lint.qualifies_other_parameter(text, endpoint, name) for text in prose):
            continue  # only defined together with another optional parameter: never sent on its own
        schema: dict[str, Any] = {"type": _qtype(q)}
        if declared:
            schema["default"] = q["default"]
        if str(q.get("type")) == "date":
            schema["format"] = "date"
        out[name] = _sample(name, schema)
    return out


def _get(endpoint: Mapping[str, Any], content: Mapping[str, Any], **query: Any) -> dict[str, Any]:
    """``GET <endpoint path>`` with the default query parameters and ``query`` (which wins)."""
    merged = {**_default_query(endpoint, content), **query}
    req: dict[str, Any] = {"method": "GET", "path": str(endpoint["path"])}
    if merged:
        req["query"] = merged
    return req


def _entity_named(name: str, content: Mapping[str, Any]) -> dict[str, Any] | None:
    wanted = re.sub(r"[^a-z0-9]", "", name.lower())
    return next(
        (e for e in content.get("entities") or [] if re.sub(r"[^a-z0-9]", "", str(e["name"]).lower()) == wanted),
        None,
    )


def _item_schema(schema: Any) -> tuple[str, dict[str, Any]] | None:
    """``(capture path of the first item's id, item schema)`` of a list response, else ``None``."""
    if not isinstance(schema, dict):
        return None
    if "array" in _types(schema):
        items, path = schema.get("items"), "$[0].id"
    else:
        key = _list_key(schema)
        if key is None:
            return None
        items, path = schema["properties"][key].get("items"), f"$.{key}[0].id"
    if not isinstance(items, dict) or "id" not in (items.get("properties") or {}):
        return None
    return path, items


def _public_list(entity: Mapping[str, Any], content: Mapping[str, Any]) -> tuple[dict[str, Any], str] | None:
    """The public list endpoint of ``entity`` and the capture path of its first item's id.

    ``GET``, ``auth: public``, no path parameter, a list response whose items carry ``id``; it lists the
    entity when its path names it (``/api/products`` -> ``Product``) or, failing that, when every other
    property of its items is a field of the entity and of no other entity (``/api/quiz`` -> ``Question``).
    """
    entities = list(content.get("entities") or [])
    fields = {str(e["name"]): {str(f["name"]) for f in e.get("fields") or []} for e in entities}
    by_name: list[tuple[dict[str, Any], str]] = []
    by_shape: list[tuple[dict[str, Any], str]] = []
    for ep in content.get("endpoints") or []:
        if ep.get("method") != "GET" or ep.get("auth") != "public" or _PARAM.search(str(ep["path"])):
            continue
        found = _item_schema(ep.get("response_schema"))
        if found is None:
            continue
        path, items = found
        named = _entity_for(ep, content)
        if named is not None and named.get("name") == entity.get("name"):
            by_name.append((ep, path))
            continue
        shown = set(items.get("properties") or {}) - {"id"}
        matches = [name for name, have in fields.items() if shown and shown <= have]
        if named is None and matches == [str(entity.get("name"))]:
            by_shape.append((ep, path))
    return (by_name or by_shape or [None])[0]


def _reference_sources(content: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Request fields that name an existing resource of another entity, and where its id is read from.

    A body field is a reference when the spec types it ``ref`` or when it is named ``<entity>_id`` for an
    entity of the task (at any depth of a request schema: ``answers[].question_id``). When that entity has a
    public list endpoint (``_public_list``), a valid request does not guess an id (v2 sent the sampled
    integer ``1``; the specs type ids ``[integer, string]`` and promise no particular value): the check or
    probe gets a setup step that reads the list and captures the first item's id, and the field is that value
    (``_with_reference_steps``). A reference without a public list keeps the sampled value.
    """
    targets: dict[str, set[str]] = {}
    for entity in content.get("entities") or []:
        for field in entity.get("fields") or []:
            if field.get("ref") and _entity_named(str(field["ref"]), content) is not None:
                targets.setdefault(str(field["name"]), set()).add(str(field["ref"]))

    def walk(schema: Any) -> None:
        if not isinstance(schema, dict):
            return
        for name, sub in (schema.get("properties") or {}).items():
            if str(name).endswith("_id") and _entity_named(str(name)[:-3], content) is not None:
                targets.setdefault(str(name), set()).add(str(_entity_named(str(name)[:-3], content)["name"]))
            walk(sub)
        walk(schema.get("items"))

    for ep in content.get("endpoints") or []:
        if ep.get("method") in ("POST", "PUT", "PATCH"):
            walk(ep.get("request_schema"))
    out: dict[str, dict[str, Any]] = {}
    for name, entities in sorted(targets.items()):
        if len(entities) != 1:
            continue  # the same field name for two entities: not decidable, the sampled value stays
        entity = _entity_named(next(iter(entities)), content)
        source = _public_list(entity, content) if entity is not None else None
        if source is None:
            continue
        endpoint, capture = source
        out[name] = {
            "placeholder": f"listed_{name}",
            "entity": str(entity["name"]),
            "endpoint": str(endpoint["id"]),
            "request": _get(endpoint, content),
            "capture": capture,
        }
    return out


def _with_reference_steps(setup: list[dict[str, Any]], uses: Any) -> list[dict[str, Any]]:
    """``setup`` with one leading step per referenced resource that ``uses`` (requests, deep) or the setup
    itself names and no step captures yet: read the public list as an anonymous client, capture the first id."""
    wanted = placeholder_names([uses, [step.get("request") for step in setup]])
    steps: list[dict[str, Any]] = []
    for reference in _REFS.get().values():
        name = str(reference["placeholder"])
        if name in wanted and not _captured(setup, name):
            steps.append(
                {
                    "actor": "anon",
                    "request": copy.deepcopy(reference["request"]),
                    "expect_status": 200,
                    "capture": {name: reference["capture"]},
                },
            )
    return [*steps, *setup]


def _request_for(endpoint: Mapping[str, Any], content: Mapping[str, Any]) -> dict[str, Any]:
    """A valid request for ``endpoint`` (path parameters left as ``{name}`` placeholders).

    Field values: the overlay's ``samples`` (inside ``_sample``), else the semantic sampler, else the rules
    that need the endpoint (a body naming a user, the caller's current password). The overlay's
    ``endpoints.<id>`` parts are merged last.
    """
    overlay = _ACTIVE.get()
    method = str(endpoint["method"])
    path = str(endpoint["path"])
    req: dict[str, Any] = {"method": method, "path": path}
    query = {}
    for q in endpoint.get("query") or []:
        if q.get("required"):
            schema: dict[str, Any] = {"type": _qtype(q)}
            if q.get("enum"):
                schema["enum"] = q["enum"]
            if q.get("default") is not None:
                schema["default"] = q["default"]
            query[str(q["name"])] = _sample(str(q["name"]), schema)
    for name, value in _default_query(endpoint, content).items():
        query.setdefault(name, value)
    if query:
        req["query"] = query
    if method in ("POST", "PUT", "PATCH"):
        schema_body = _request_schema(endpoint, content)
        props = (schema_body or {}).get("properties") or {}
        auth_path = path.startswith("/api/auth/")
        unique = _unique_fields(content)
        if auth_path:
            unique |= {"username", "email"}
        body = _body(schema_body, unique_fields=unique)
        for name in body:
            if name in overlay.samples or not isinstance(props.get(name), dict) or "string" not in _types(props[name]):
                continue
            if name == "password":
                body[name] = _TEST_PASSWORD
            elif name == "username" and not auth_path:
                body[name] = "{user_b_username}"  # a body naming a user names an existing one (user_b)
            elif _field_class(name, props[name]) == "password" and set(_tokens(name)) & _CURRENT_TOKENS:
                # the caller's own current password (the tester then signs in with the new one)
                body[name] = "{admin_password}" if endpoint.get("auth") == "admin" else "{user_a_password}"
        ctype = endpoint.get("content_type", "json")
        if ctype == "multipart":
            files = _file_fields(schema_body) or ["file"]
            req["files"] = {name: _file_sample(name, schema_body) for name in files}
            req["form"] = {k: v for k, v in body.items() if not isinstance(v, (dict, list))}
        elif ctype == "form":
            req["form"] = body
        elif ctype != "none":
            req["json"] = body
    for part, patch in (overlay.endpoints.get(str(endpoint["id"])) or {}).items():
        merged = _merge(req.get(part) or {}, patch)
        if merged or part in req:
            req[part] = merged
    return req


# =====================================================================================
# Resource setup: create the resource a path needs
# =====================================================================================


def _collection(path: str) -> tuple[str, str] | None:
    """``/api/todos/{id}`` -> (``/api/todos``, ``id``): the collection path and the item parameter."""
    parts = path.rstrip("/").split("/")
    for i in range(len(parts) - 1, 0, -1):
        m = _PARAM.fullmatch(parts[i])
        if m:
            return "/".join(parts[:i]) or "/", m.group(1)
    return None


def _create_endpoint(collection: str, endpoints: list[dict[str, Any]]) -> dict[str, Any] | None:
    posts = [ep for ep in endpoints if ep["method"] == "POST" and not str(ep["path"]).startswith("/api/auth/")]
    for ep in posts:
        if str(ep["path"]).rstrip("/") == collection.rstrip("/"):
            return ep
    last = collection.rstrip("/").rsplit("/", 1)[-1]
    if not last or _PARAM.fullmatch(last):
        return None
    for ep in posts:
        if str(ep["path"]).rstrip("/").rsplit("/", 1)[-1] == last:
            return ep
    return None


def _created(endpoint: Mapping[str, Any]) -> int | list[int]:
    """The status a *setup* create must answer: 200 and 201 both mean "created" there.

    A setup step only prepares the resource a check needs; the exact success status of the create endpoint is
    asserted once, by its own ``<ep>.ok`` check. (v1 failed every dependent check of an app that answers a
    create with 200 where the spec says 201, or the reverse: one deviation counted many times.)
    """
    status = endpoint.get("success_status", 200)
    return [200, 201] if status in (200, 201) else status


def _create_step(
    create: Mapping[str, Any],
    content: Mapping[str, Any],
    actor: str,
    *,
    capture: dict[str, str] | None = None,
    fresh: bool = False,
) -> dict[str, Any]:
    """The setup step that creates a resource through ``create`` (as the admin when only the admin may)."""
    step: dict[str, Any] = {
        "actor": "admin" if create.get("auth") == "admin" else actor,
        "request": _request_for(create, content),
        "expect_status": _created(create),
    }
    if capture:
        step["capture"] = capture
    if fresh:
        step["fresh"] = True
    return step


def _capture_path(param: str, create: Mapping[str, Any]) -> str:
    props = ((create.get("response_schema") or {}).get("properties")) or {}
    return f"$.{param}" if param in props else "$.id"


def _setup_for(path: str, actor: str, content: Mapping[str, Any], *, depth: int = 0) -> list[dict[str, Any]] | None:  # noqa: PLR0911
    """Setup steps that create the resources ``path``'s parameters name, else ``None`` (cannot generate)."""
    if not _PARAM.search(path):
        return []
    if depth >= _SETUP_DEPTH:
        return None
    found = _collection(path)
    if found is None:
        return None
    collection, param = found
    create = _create_endpoint(collection, list(content.get("endpoints") or []))
    if create is None:
        return None
    outer = set(_PARAM.findall(collection))
    if outer - set(_PARAM.findall(str(create["path"]))):
        return None  # the create path does not name every parameter the outer path needs
    steps = _setup_for(str(create["path"]), actor, content, depth=depth + 1)
    if steps is None:
        return None
    return [*steps, _create_step(create, content, actor, capture={param: _capture_path(param, create)})]


# =====================================================================================
# The compiler table: 8.1
# =====================================================================================


def _actor_for(endpoint: Mapping[str, Any]) -> str:
    return {"public": "anon", "user": "user_a", "owner": "user_a", "admin": "admin"}[str(endpoint["auth"])]


def _schema_or_none(endpoint: Mapping[str, Any]) -> dict[str, Any] | None:
    if endpoint.get("success_status") == 204 or endpoint.get("content_type") == "none":
        return None
    schema = endpoint.get("response_schema")
    return copy.deepcopy(schema) if isinstance(schema, dict) else None


def _check(
    cid: str,
    category: str,
    endpoint: Mapping[str, Any] | None,
    request: dict[str, Any],
    status: int | list[int],
    *,
    actor: str = "user_a",
    setup: list[dict[str, Any]] | None = None,
    schema: dict[str, Any] | None = None,
    contains: Any = None,
    headers: dict[str, str] | None = None,
    restart: bool = False,
    depends: list[str] | None = None,
) -> dict[str, Any]:
    expect: dict[str, Any] = {"status": status}
    if schema is not None:
        expect["json_schema"] = schema
    if contains is not None:
        expect["json_contains"] = contains
    if headers:
        expect["header_contains"] = headers
    return {
        "id": cid,
        "category": category,
        "endpoint": endpoint["id"] if endpoint else None,
        "actor": actor,
        "setup": copy.deepcopy(setup or []),
        "request": copy.deepcopy(request),
        "expect": expect,
        "depends_on": depends if depends is not None else ["health"],
        "restart_before": restart,
        "source": "compiler",
    }


def _auth_endpoint_check(ep: Mapping[str, Any], content: Mapping[str, Any]) -> dict[str, Any] | None:
    """``auth.register.ok`` / ``auth.login.ok``: the auth endpoints themselves, as an anonymous client."""
    req = _request_for(ep, content)
    body = req.get("json") if isinstance(req.get("json"), dict) else {}
    if "username" in body:
        body["username"] = "llmlab{rand}"
    if str(ep["path"]).endswith("/login"):
        register = next(
            (
                e
                for e in content.get("endpoints") or []
                if str(e["path"]).endswith("/register") and e["method"] == "POST"
            ),
            None,
        )
        if register is None:
            return None
        reg_req = _request_for(register, content)
        reg_req["json"] = {
            **(reg_req.get("json") or {}),
            **{k: body[k] for k in ("username", "password") if k in body},
        }
        setup = [{"actor": "anon", "request": reg_req, "expect_status": [200, 201]}]
        return _check(
            f"{ep['id']}.ok",
            "functional",
            ep,
            req,
            ep.get("success_status", 200),
            actor="anon",
            setup=setup,
        )
    return _check(
        f"{ep['id']}.ok",
        "functional",
        ep,
        req,
        ep.get("success_status", 200),
        actor="anon",
        schema=_schema_or_none(ep),
    )


def _token_schema(path: str) -> dict[str, Any] | None:
    """A JSON schema requiring a non-empty string at a dotted ``token_json_path`` (``None`` for indexed paths)."""
    names = path.removeprefix("$").lstrip(".").split(".")
    if not names or any(not n or "[" in n for n in names):
        return None
    schema: dict[str, Any] = {"type": "string", "minLength": 1}
    for name in reversed(names):
        schema = {"type": "object", "required": [name], "properties": {name: schema}}
    return schema


def _with_login_credential(check: dict[str, Any], auth: AuthDriverConfig) -> dict[str, Any]:
    """``<login>.ok`` must hand out the credential of the auth mode, not merely answer 2xx.

    Without this, an app whose catch-all route answers every request with the SPA's ``index.html`` (200)
    passes the login check (found on the Django minimal starter in the wave-3 conformance run). Bearer: a
    non-empty string at ``token_json_path``; cookie: a ``Set-Cookie`` header.
    """
    request = check.get("request") or {}
    if (
        check.get("source") != "compiler"
        or not str(check.get("id", "")).endswith(".ok")
        or request.get("method") != "POST"
        or request.get("path") != auth.login_path
    ):
        return check
    expect = dict(check["expect"])
    if auth.credential == "bearer":
        schema = _token_schema(auth.token_json_path or "token")
        if schema is None:
            return check
        expect["json_schema"] = {"allOf": [expect["json_schema"], schema]} if expect.get("json_schema") else schema
    else:
        expect["header_contains"] = {**(expect.get("header_contains") or {}), "Set-Cookie": ""}
    return {**check, "expect": expect}


def _with_body(req: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(req)
    if "json" in out:
        out["json"] = body
    elif "form" in out:
        out["form"] = body
    return out


def _too_long(name: str, sub: Mapping[str, Any], valid: Any, *, unique: bool = False) -> str:  # noqa: PLR0911
    """A value past ``maxLength`` that is wrong in nothing but its length.

    Free text: ``y`` repeated, one past the limit. A URL keeps being a URL (the valid value, padded), an
    e-mail an e-mail, an XML document well-formed XML, base64 valid base64 (the next multiple of four above
    the limit): an app that validates the format first answers 400/422 for the format either way, so only a
    value that passes every other rule measures the length rule. The value of a unique field ends in
    ``{rand}``: an app that wrongly stored it once must not answer the next request with a conflict.
    """
    limit = int(sub["maxLength"])
    kind = _field_class(name, sub)
    if kind in ("url", "password") and isinstance(valid, str) and valid:
        # the valid value, padded: still a URL / still a password that meets a strength rule
        size = rendered_len(valid)
        padding = "y" * (limit + 1 - size if size is not None and size <= limit else limit + 1)
        extension = _URL_EXTENSION.fullmatch(valid) if kind == "url" else None
        if extension is not None:
            # the padding goes before the file extension of the path: an address an app accepts for its
            # extension (".mp3" for an audio address) must not be refused for having lost it
            return extension.group(1) + padding + extension.group(2)
        return valid + padding
    if kind == "email":
        tail = "@example.com"
        return "y" * max(limit + 1 - len(tail), 1) + tail
    if name.lower() in ("filename", "file_name") and isinstance(valid, str) and "." in valid:
        extension = valid[valid.rindex(".") :]  # a too-long file name is still a file of the same type
        return "y" * max(limit + 1 - len(extension), 1) + extension
    if kind == "base64":
        return "eXl5" * (limit // 4 + 1)  # base64 of "yyy", repeated: valid, and limit < length <= limit + 4
    if kind == "xml":
        head, tail = "<llmlab>", "</llmlab>"
        return head + "y" * max(limit + 1 - len(head) - len(tail), 1) + tail
    if unique:
        tail = "{rand}"
        return "y" * max(limit + 1 - len(PLACEHOLDER_EXAMPLES["rand"]), 1) + tail
    return "y" * (limit + 1)


# ``scheme://host/path/name.ext``: (everything before the extension, the extension of the last path segment)
_URL_EXTENSION: Final = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://[^/?#]+/[^?#]*)(\.[A-Za-z0-9]{1,5})")


def _field_order(name: str) -> tuple[int, str]:
    """A total order of field names that does not depend on the key order of the content: shortest name
    first, then alphabetical. It is the order PostgreSQL returns the keys of a stored ``jsonb`` object in, so
    "the first field" is the field compiler v1 picked from a stored task.
    """
    return len(name), name


# Field classes for which a value can be too long and valid in everything else. A date, a time, a UUID, an
# ISBN, a phone number or a currency code has a fixed shape: an over-long one breaks that shape too, so a
# 400 for it says nothing about the length rule.
_LENGTH_ONLY: Final = frozenset({_FREE_TEXT, "url", "email", "base64", "xml", "password", "sku", "tag"})


def _bounded_fields(schema: Mapping[str, Any], body: Mapping[str, Any]) -> list[str]:
    props = schema.get("properties") or {}
    return [
        n
        for n, s in props.items()
        if isinstance(s, dict)
        and isinstance(s.get("maxLength"), int)
        and not isinstance(s.get("maxLength"), bool)
        and "string" in _types(s)
        and not s.get("enum")
        and n in body
    ]


def _too_long_field(schema: Mapping[str, Any], body: Mapping[str, Any]) -> str | None:
    """The one field ``.invalid.too_long`` breaks when no prose states a limit: free text before URL before
    e-mail before anything else.

    Within a class: required fields in the order the schema lists them (a list, so the choice does not depend
    on the key order of ``properties``), then ``_field_order``.
    """
    props = schema.get("properties") or {}
    required = [str(r) for r in schema.get("required") or []]

    def rank(name: str) -> tuple[int, int, tuple[int, str]]:
        kind = _field_class(name, props[name])
        order = required.index(name) if name in required else len(required)
        if kind == _FREE_TEXT:
            value = body.get(name)
            return (0 if isinstance(value, str) and not placeholder_names(value) else 1), order, _field_order(name)
        return {"url": 2, "email": 3}.get(kind, 4), order, _field_order(name)

    names = _bounded_fields(schema, body)
    return min(names, key=rank) if names else None


def _too_long_fields(schema: Mapping[str, Any], body: Mapping[str, Any], content: Mapping[str, Any]) -> list[str]:
    """The fields ``.invalid.too_long`` breaks, one request each (the check passes when all are refused).

    Every length-limited field whose limit a prose variant of the task states and whose value can be too
    long without being wrong otherwise (``_LENGTH_ONLY``), in a fixed order: required fields as the schema
    lists them, then ``_field_order``. (v1 broke the first limited field only and v2 at first another one:
    which limit was measured depended on the compiler, and a missing limit on any other field went unseen.)
    When no prose states a limit (the limits live in the request schema only) the check keeps one field,
    ``_too_long_field``.
    """
    from backend.generation.library.lint import limit_stated

    props = schema.get("properties") or {}
    required = [str(r) for r in schema.get("required") or []]
    prose = [str(text) for text in (content.get("prose") or {}).values()]
    stated = [
        name
        for name in _bounded_fields(schema, body)
        if _field_class(name, props[name]) in _LENGTH_ONLY
        and any(limit_stated(text, name, int(props[name]["maxLength"])) for text in prose)
    ]
    if not stated:
        one = _too_long_field(schema, body)
        return [one] if one is not None else []
    return sorted(stated, key=lambda n: (required.index(n) if n in required else len(required), _field_order(n)))


# The out-of-enum value of ``.invalid.enum``: a plain lower-case word, so that it is wrong in nothing but
# not being an option (v2 at first sent ``llmlab-not-an-option``, which an app without any option list but
# with an identifier-shape rule refused for its hyphens: a pass for the wrong reason).
_NOT_AN_OPTION: Final = "notlisted"


def _not_an_option(sub: Mapping[str, Any]) -> str:
    value = _NOT_AN_OPTION
    limit = sub.get("maxLength")
    if isinstance(limit, int) and not isinstance(limit, bool) and 0 < limit < len(value):
        value = value[:limit]
    while value in [str(option) for option in sub.get("enum") or []]:
        value = value[:-1] + ("x" if not value.endswith("x") else "q")
    return value


def _validation_checks(
    ep: Mapping[str, Any],
    req: dict[str, Any],
    actor: str,
    setup: list[dict[str, Any]],
    content: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """``.invalid.*``: the valid request with exactly one fault (a missing, a too long or an out-of-enum field)."""
    schema = ep.get("request_schema") or {}
    props = schema.get("properties") or {}
    body = req.get("json") if isinstance(req.get("json"), dict) else req.get("form")
    if not schema:
        return []  # no stated request schema: the task promises no validation rules to check
    if not isinstance(body, dict) or ep.get("content_type") == "multipart":
        return []
    out: list[dict[str, Any]] = []

    def broken(cid: str, changed: dict[str, Any]) -> dict[str, Any]:
        return _check(
            f"{ep['id']}.invalid.{cid}",
            "validation",
            ep,
            _with_body(req, changed),
            [400, 422],
            actor=actor,
            setup=setup,
        )

    required = [r for r in schema.get("required") or [] if r in body]
    if required and ep["method"] != "PATCH":
        out.append(broken("missing", {k: v for k, v in body.items() if k != required[0]}))
    unique = _unique_fields(content)
    targets = _too_long_fields(schema, body, content)
    if targets:

        def over(name: str) -> dict[str, Any]:
            return {**body, name: _too_long(name, props[name], body[name], unique=name in unique)}

        check = broken("too_long", over(targets[0]))
        if len(targets) > 1:
            check["label"] = targets[0]
            check["also"] = [{"label": name, "request": _with_body(req, over(name))} for name in targets[1:]]
        out.append(check)
    for name in sorted(props, key=_field_order):
        sub = props[name]
        if isinstance(sub, dict) and isinstance(sub.get("enum"), list) and sub["enum"]:
            out.append(broken("enum", {**body, name: _not_an_option(sub)}))
            break
    return out


def _list_key(schema: Mapping[str, Any] | None) -> str | None:
    if not isinstance(schema, dict) or "array" in _types(schema):
        return None
    props = schema.get("properties") or {}
    return next((k for k in ("items", "results", "data") if isinstance(props.get(k), dict)), None)


def _list_shape(schema: Mapping[str, Any] | None, inner: dict[str, Any]) -> dict[str, Any] | None:
    """A response schema that constrains the result list with ``inner`` (a list or ``{items: [...]}``)."""
    if not isinstance(schema, dict):
        return None
    if "array" in _types(schema):
        return {"type": "array", **inner}
    key = _list_key(schema)
    if key is None:
        return None
    return {"type": "object", "required": [key], "properties": {key: {"type": "array", **inner}}}


_SEARCH_NEEDLE: Final = "llmlabfind{rand}"


def _list_checks(ep: Mapping[str, Any], content: Mapping[str, Any], actor: str) -> list[dict[str, Any]]:
    """Pagination, filter and search checks of a ``GET`` collection endpoint.

    Every create after the first of a setup is ``fresh``: it gets its own ``rand``, so the unique fields of
    the created resources differ (v1 sent the same body three times and failed on the second create).
    """
    if ep["method"] != "GET" or _PARAM.search(str(ep["path"])) or actor == "anon":
        return []
    create = _create_endpoint(str(ep["path"]), list(content.get("endpoints") or []))
    if create is None or create.get("content_type") == "multipart":
        return []
    step = _create_step(create, content, actor)
    base = step["request"]
    if "json" not in base:
        return []

    def created(changes: dict[str, Any] | None = None, *, fresh: bool = False) -> dict[str, Any]:
        out_step = copy.deepcopy(step)
        if changes:
            out_step["request"]["json"] = {**base["json"], **changes}
        if fresh:
            out_step["fresh"] = True
        return out_step

    out: list[dict[str, Any]] = []
    query = {str(q["name"]): q for q in ep.get("query") or []}
    page_size = next((q for q in query.values() if q.get("semantics") == "page_size"), None)
    if page_size is not None:
        shape = _list_shape(ep.get("response_schema"), {"minItems": 2, "maxItems": 2})
        if shape is not None:
            req = _get(ep, content, **{str(page_size["name"]): 2})
            out.append(
                _check(
                    f"{ep['id']}.page_size",
                    "pagination",
                    ep,
                    req,
                    200,
                    actor=actor,
                    setup=[created(fresh=True), created(fresh=True), created(fresh=True)],
                    schema=shape,
                ),
            )
    create_schema = _request_schema(create, content) or {}
    create_props = create_schema.get("properties") or {}
    for q in query.values():
        name = str(q["name"])
        if q.get("semantics") != "filter" or name not in create_props:
            continue
        values: list[Any] = [v for v in (q.get("enum") or []) if v is not None][:2]
        if not values and str(q.get("type")) == "bool":
            values = [True, False]
        if len(values) < 2:
            continue
        want, other = values
        setup = [created({name: want}), created({name: other}, fresh=True)]
        inner = {"minItems": 1, "items": {"type": "object", "properties": {name: {"const": want}}}}
        shape = _list_shape(ep.get("response_schema"), inner)
        if shape is None:
            continue
        req = _get(ep, content, **{name: want})
        out.append(_check(f"{ep['id']}.filter.{name}", "filter", ep, req, 200, actor=actor, setup=setup, schema=shape))
    search = next((q for q in query.values() if q.get("semantics") == "search"), None)

    def searchable(name: str) -> bool:
        """A required free-text field that can hold the needle (never an ISBN, a date, an e-mail, ...)."""
        sub = create_props.get(name) or {}
        limit = sub.get("maxLength")
        return (
            isinstance(base["json"].get(name), str)
            and placeholder_names(base["json"][name]) <= {"rand"}  # not a field that names an account
            and "string" in _types(sub)
            and not sub.get("enum")
            and _field_class(name, sub) == _FREE_TEXT
            and not (isinstance(limit, int) and limit < (rendered_len(_SEARCH_NEEDLE) or 0))
        )

    text_field = next((n for n in create_schema.get("required") or [] if searchable(n)), None)
    if search is not None and text_field is not None:
        setup = [created({text_field: _SEARCH_NEEDLE}), created(fresh=True)]
        key = _list_key(ep.get("response_schema"))
        req = _get(ep, content, **{str(search["name"]): _SEARCH_NEEDLE})
        if key is not None:  # json_contains needs an object; a bare-list response cannot express "contains"
            contains = {key: [{text_field: _SEARCH_NEEDLE}]}
            out.append(
                _check(f"{ep['id']}.search", "filter", ep, req, 200, actor=actor, setup=setup, contains=contains),
            )
    return out


# A public (anon) check reads a resource that user_a created: its setup must create it visible to everyone. The
# sample value of a boolean is ``false``, which hides a resource behind a visibility flag (an unpublished note, an
# inactive product), so the setup sets the flags it sends to ``true``.
_VISIBILITY_FLAGS: Final = frozenset(
    {
        "published",
        "is_published",
        "public",
        "is_public",
        "visible",
        "is_visible",
        "active",
        "is_active",
        "listed",
        "is_listed",
        "shared",
        "is_shared",
    },
)


def _publicly_visible(setup: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for step in setup:
        body = (step.get("request") or {}).get("json")
        if isinstance(body, dict) and any(k in _VISIBILITY_FLAGS and body[k] is False for k in body):
            body = {k: (True if k in _VISIBILITY_FLAGS and v is False else v) for k, v in body.items()}
            out.append({**step, "request": {**step["request"], "json": body}})
        else:
            out.append(step)
    return out


def _endpoint_checks(ep: Mapping[str, Any], content: Mapping[str, Any]) -> list[dict[str, Any]]:
    path = str(ep["path"])
    if path.startswith("/api/auth/"):
        if ep["method"] == "POST" and path.endswith(("/register", "/login")):
            check = _auth_endpoint_check(ep, content)
            return [check] if check else []
        return []
    actor = _actor_for(ep)
    owner_actor = "user_a" if actor == "anon" else actor
    setup = _setup_for(path, owner_actor, content)
    if setup is None:
        return []
    if actor == "anon":
        setup = _publicly_visible(setup)
    req = _request_for(ep, content)
    status = ep.get("success_status", 200)
    auth = str(ep["auth"])
    out = [
        _check(f"{ep['id']}.ok", "functional", ep, req, status, actor=actor, setup=setup, schema=_schema_or_none(ep)),
    ]
    if auth in ("user", "owner", "admin"):
        out.append(_check(f"{ep['id']}.anon_401", "auth", ep, req, 401, actor="anon", setup=setup))
    if auth == "owner" and setup:
        listed = sorted({int(e) for e in ep.get("errors") or []} & {403, 404})
        want: int | list[int] = listed[0] if len(listed) == 1 else [403, 404]
        out.append(_check(f"{ep['id']}.other_user", "ownership", ep, req, want, actor="user_b", setup=setup))
    if auth == "admin":
        out.append(_check(f"{ep['id']}.non_admin_403", "auth", ep, req, 403, actor="user_a", setup=setup))
    if ep["method"] in ("POST", "PUT", "PATCH") and ep.get("request_schema"):
        out += _validation_checks(ep, req, actor, setup, content)
    props = ((_request_schema(ep, content) or {}).get("properties")) or {}
    errors = {int(e) for e in ep.get("errors") or []}
    if 409 in errors and ep["method"] == "POST" and isinstance(req.get("json"), dict):
        if any(f in req["json"] for f in _unique_fields(content)):
            dup_setup = [*setup, {"actor": actor, "request": req, "expect_status": _created(ep)}]
            out.append(_check(f"{ep['id']}.duplicate", "negative", ep, req, 409, actor=actor, setup=dup_setup))
    if 413 in errors and ep["method"] in ("POST", "PUT", "PATCH"):
        big = copy.deepcopy(req)
        changed = False
        if big.get("files"):
            first = next(iter(big["files"]))
            spec = dict(big["files"][first])
            if spec.get("content_b64") in _OVERSIZE_FILES:
                # a valid file of the same kind, only larger: the size is the one thing wrong with the upload
                big["files"][first] = {**spec, "content_b64": _OVERSIZE_FILES[spec["content_b64"]]}
            else:
                spec.pop("content_b64", None)
                big["files"][first] = {**spec, "content": "{oversize}"}
            changed = True
        elif isinstance(big.get("json"), dict) and big["json"]:
            strings = sorted((n for n, v in big["json"].items() if isinstance(v, str)), key=_field_order)
            upload = next((n for n in strings if big["json"][n] in _OVERSIZE_FILES), None)
            if upload is not None:
                # a base64 upload: the same kind of file, valid, only larger
                big["json"][upload] = _OVERSIZE_FILES[big["json"][upload]]
            else:
                # the oversized value goes into a free-text field: every other field stays valid
                text = [n for n in strings if _field_class(n, props.get(n) or {}) == _FREE_TEXT]
                big["json"][(text or strings or sorted(big["json"], key=_field_order))[0]] = "{oversize}"
            changed = True
        if changed:
            out.append(_check(f"{ep['id']}.oversize", "negative", ep, big, 413, actor=actor, setup=setup))
    out += _list_checks(ep, content, actor)
    return out


# The valid small file of an upload -> the valid oversized file of the same kind (tester generators). Kinds
# without one (JPEG, GIF, PDF, text) keep ``{oversize}``: 12 MiB of ``x``.
_OVERSIZE_FILES: Final = {"{png_b64}": "{png_oversize_b64}", "{wav_b64}": "{wav_oversize_b64}"}


def _persisted_field(created: Mapping[str, Any], collection: str, content: Mapping[str, Any]) -> dict[str, Any] | None:
    """The one created field ``<ep>.persists`` reads back: a free-text field the app stores as sent.

    Required fields first (in the order the schema lists them), then ``_field_order``; a URL only when the body
    has no free text. Never a password, an e-mail, a phone number, a date or a number: an app may normalise or
    hide those.
    """
    create = _create_endpoint(collection, list(content.get("endpoints") or []))
    schema = (_request_schema(create, content) if create is not None else None) or {}
    props = schema.get("properties") or {}
    required = [str(r) for r in schema.get("required") or []]

    def names(kind: str, allowed: set[str]) -> list[str]:
        found = [
            n
            for n, v in created.items()
            if isinstance(v, str)
            and v
            and _field_class(n, props.get(n) or {}) == kind
            and placeholder_names(v) <= allowed
        ]
        return sorted(found, key=lambda n: (required.index(n) if n in required else len(required), _field_order(n)))

    picked = names(_FREE_TEXT, {"rand"}) or names("url", {"rand", "upstream_base"})
    return {picked[0]: created[picked[0]]} if picked else None


def _persistence_checks(content: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for get in content.get("endpoints") or []:
        if get["method"] != "GET" or get["auth"] not in ("user", "owner"):
            continue
        path = str(get["path"])
        found = _collection(path)
        if found is None or not path.rstrip("/").endswith("{" + found[1] + "}"):
            continue
        setup = _setup_for(path, "user_a", content)
        if not setup:
            continue
        contains = _persisted_field(setup[-1]["request"].get("json") or {}, found[0], content)
        out.append(
            _check(
                f"{get['id']}.persists",
                "persistence",
                get,
                _get(get, content),
                200,
                actor="user_a",
                setup=setup,
                contains=contains,
                restart=True,
            ),
        )
    return out


def _infra_checks(stack_content: Mapping[str, Any], has_frontend: bool) -> list[dict[str, Any]]:
    contract = stack_content.get("run_contract") or {}
    health = str(contract.get("health_path") or "/api/health")
    out = [_check("health", "infra", None, {"method": "GET", "path": health}, 200, actor="anon", depends=[])]
    if has_frontend and contract.get("static_serving", "backend") == "backend":
        html = {"Content-Type": "text/html"}
        out.append(
            _check("index_served", "infra", None, {"method": "GET", "path": "/"}, 200, actor="anon", headers=html),
        )
        spa = {"method": "GET", "path": "/llmlab/client/route"}
        out.append(_check("spa_fallback", "infra", None, spa, 200, actor="anon", headers=html))
    return out


# =====================================================================================
# Probes: spec section 8.3
# =====================================================================================


def _read_endpoint(endpoint: Mapping[str, Any], content: Mapping[str, Any]) -> dict[str, Any] | None:
    path = str(endpoint["path"])
    return next(
        (ep for ep in content.get("endpoints") or [] if ep["method"] == "GET" and str(ep["path"]) == path),
        None,
    )


def _answered(endpoint: Mapping[str, Any]) -> int | None:
    """What a probe's positive control expects of the benign request: any 2xx (``None``) for an endpoint whose
    success status is a 2xx one, else that exact status (a redirecting endpoint must redirect).

    The control only has to show that the endpoint works before the attack is judged. Which 2xx status the
    app picked (200 where the spec says 201 or 202) is asserted by the endpoint's ``.ok`` check and says
    nothing about whether the endpoint can be attacked (v2 at first skipped such probes:
    ``positive_control_failed``).
    """
    status = int(endpoint.get("success_status", 200))
    return None if 200 <= status < 300 else status


def _visible_request(request: dict[str, Any]) -> dict[str, Any]:
    """``request`` with the visibility flags its body sends as ``false`` set to ``true`` (``_publicly_visible``)."""
    return _publicly_visible([{"request": request}])[0]["request"]


def _probe_with_endpoint(
    probe: dict[str, Any],
    params: dict[str, Any],
    endpoint: Mapping[str, Any],
    content: Mapping[str, Any],
    checks: Mapping[str, Any],
    auth: AuthDriverConfig,
) -> tuple[list[str] | None, dict[str, Any] | None]:
    """Defaults of a probe that names an endpoint: ``(requires_checks default, positive control)``."""
    kind = str(probe["kind"])
    endpoints = _endpoint_map(content)
    params.setdefault("setup", _setup_for(str(endpoint["path"]), "user_a", content) or [])
    target = _request_for(endpoint, content)
    target["actor"] = "admin" if endpoint["auth"] == "admin" else "user_a"
    params.setdefault("target", target)
    requires = [cid for cid in (f"{endpoint['id']}.ok",) if cid in checks]
    reached = _answered(endpoint)
    control: dict[str, Any] | None = {**copy.deepcopy(target), "expect_status": reached}
    read = _read_endpoint(endpoint, content)
    if kind in ("idor_enumeration", "mass_assignment") and read is not None:
        params.setdefault("read", {**_request_for(read, content), "actor": "user_a"})
        if endpoint["method"] in ("DELETE", "PUT", "PATCH"):
            control = {**_request_for(read, content), "actor": "user_a", "expect_status": _answered(read)}
    if kind in ("sqli_boolean", "sqli_error", "sqli_time", "xss_reflected", "open_redirect", "path_traversal"):
        if not params.get("param") and not params.get("field"):
            sem = (
                ("search", "filter")
                if kind.startswith("sqli") or kind == "xss_reflected"
                else ("other", "search", "filter")
            )
            param = next((q["name"] for q in endpoint.get("query") or [] if q.get("semantics") in sem), None)
            if param:
                params["param"] = param
    if kind == "sqli_boolean" and endpoint["method"] == "GET" and not _PARAM.search(str(endpoint["path"])):
        create = _create_endpoint(str(endpoint["path"]), list(content.get("endpoints") or []))
        if create is not None and create.get("content_type") != "multipart":
            params["setup"] = [*params["setup"], _create_step(create, content, "user_a")]
    if kind == "xss_stored":
        view = endpoints.get(str(params.get("view_endpoint") or ""))
        if view is not None:
            params.setdefault("view", _get(view, content))
            found = _collection(str(view["path"]))
            captured = {name for step in params.get("setup") or [] for name in step.get("capture") or {}}
            if found is not None and found[1] not in captured:  # never overwrite an id the setup captured
                params.setdefault("capture", {found[1]: _capture_path(found[1], endpoint)})
            if view.get("auth") == "public":
                # The view is what anyone sees: the stored payload must be created visible to everyone, or a
                # correct app answers 404 for it and nothing is measured (same rule as the anonymous checks).
                params["setup"] = _publicly_visible(params.get("setup") or [])
                if params.get("target") is target:
                    params["target"] = _visible_request(target)
                    control = {**copy.deepcopy(params["target"]), "expect_status": reached}
    if kind == "rate_limit_missing" and str(endpoint["path"]) == auth.login_path:
        # sign-in: repeated wrong passwords for an existing account; the benign request is one refused sign-in
        body = {"username": "{user_a_username}", "password": "wrong-{rand}"}
        params["target"] = {"method": "POST", "path": endpoint["path"], "json": body, "actor": "anon"}
        control = {**params["target"], "expect_status": [400, 401, 403, 404]}
        requires = [cid for cid in ("auth.login.ok",) if cid in checks]
    elif kind == "rate_limit_missing":
        # Any other endpoint: its valid request, repeated. The benign request is that request answered with the
        # endpoint's success status (a sign-in style control, "a wrong password is refused", cannot hold for an
        # endpoint that has no password: a conforming app answers 2xx and the probe was lost for every app).
        # A body that names an account names user_a, the account the sign-in variant attacks: a limiter kept per
        # account is exercised like one kept per client.
        repeated = {**_request_for(endpoint, content), "actor": _actor_for(endpoint)}
        if isinstance(repeated.get("json"), dict) and isinstance(repeated["json"].get("username"), str):
            repeated["json"] = {**repeated["json"], "username": "{user_a_username}"}
        params["target"] = repeated
        control = copy.deepcopy(repeated)
        if not {int(e) for e in endpoint.get("errors") or []} & {423, 429}:
            # The spec gives this endpoint no "too many requests" answer (a reset request must answer the
            # same whoever asks): a limiter may drop the work and answer as always, so "no 429 seen" alone
            # is not a finding. The tester then needs something else to count: the messages the requests
            # put in the task's outbox, when it has one.
            params["silent_limit"] = True
            observed = _outbox_observation(repeated, content)
            if observed is not None:
                steps, params["observe"] = observed
                params["setup"] = [*(params.get("setup") or []), *steps]
        requires = [cid for cid in (f"{endpoint['id']}.ok",) if cid in checks] or [
            cid for cid in ("auth.login.ok",) if cid in checks
        ]
    return requires, control


def _outbox_observation(
    repeated: Mapping[str, Any],
    content: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
    """``(setup steps, request)`` to count the outbox messages of the account a repeated request names.

    The task's outbox (``GET .../outbox`` with a ``to`` parameter) stands in for a mailbox. The repeated
    request names user_a; the tester registers its accounts with a username and a password only, so the
    setup first gives user_a an address through the endpoint that changes the signed-in account's ``email``.
    ``None`` when the task has no outbox or no such endpoint (the probe is then not measurable on an endpoint
    that may be limited silently).
    """
    body = repeated.get("json") if isinstance(repeated.get("json"), dict) else {}
    if body.get("username") != "{user_a_username}":
        return None
    endpoints = list(content.get("endpoints") or [])
    outbox = next(
        (
            ep
            for ep in endpoints
            if ep["method"] == "GET"
            and str(ep["path"]).rstrip("/").rsplit("/", 1)[-1] == "outbox"
            and any(str(q.get("name")) == "to" for q in ep.get("query") or [])
        ),
        None,
    )
    setter = next(
        (
            ep
            for ep in endpoints
            if ep["method"] in ("PATCH", "PUT")
            and ep.get("auth") == "user"
            and not _PARAM.search(str(ep["path"]))
            and "email" in ((ep.get("request_schema") or {}).get("properties") or {})
        ),
        None,
    )
    if outbox is None or setter is None:
        return None
    change = _request_for(setter, content)
    address = (change.get("json") or {}).get("email")
    if not isinstance(address, str) or "rand" not in placeholder_names(address):
        return None
    step = {"actor": "user_a", "request": change, "expect_status": [200, 204]}
    look = _request_for(outbox, content)
    look["query"] = {**(look.get("query") or {}), "to": address}
    return [step], {**look, "actor": _actor_for(outbox)}


def _probe(
    spec: Mapping[str, Any],
    content: Mapping[str, Any],
    checks: Mapping[str, Mapping[str, Any]],
    auth: AuthDriverConfig,
    *,
    enable_unvalidated: bool = False,
) -> dict[str, Any]:
    """A compiled probe: default ``requires_checks``, setup, positive control and attack target per kind."""
    from backend.generation.evaluation import probes as probe_catalog

    probe = {k: copy.deepcopy(v) for k, v in spec.items()}
    params: dict[str, Any] = dict(probe.get("params") or {})
    endpoint = _endpoint_map(content).get(str(probe.get("endpoint") or ""))
    kind = str(probe["kind"])
    login_checks = [cid for cid in ("auth.login.ok",) if cid in checks]
    requires: list[str] | None
    control: dict[str, Any] | None
    if endpoint is not None:
        requires, control = _probe_with_endpoint(probe, params, endpoint, content, checks, auth)
    else:
        requires, control = login_checks, None
        if kind == "default_credential":
            control = {
                "method": "POST",
                "path": auth.login_path,
                "actor": "anon",
                "json": {"username": "{admin_username}", "password": "{admin_password}"},
                "expect_status": 200,
            }
        elif kind == "jwt_none_alg":
            protected = next(
                (
                    e
                    for e in content.get("endpoints") or []
                    if e["auth"] == "user" and e["method"] == "GET" and not _PARAM.search(str(e["path"]))
                ),
                None,
            )
            if protected is not None:
                params.setdefault("target", {**_request_for(protected, content), "actor": "user_a"})
                benign = _request_for(protected, content)
                control = {**benign, "actor": "user_a", "expect_status": _answered(protected)}
                if f"{protected['id']}.ok" in checks:
                    requires = [f"{protected['id']}.ok"]
        elif kind == "rate_limit_missing":
            body = {"username": "{user_a_username}", "password": "wrong-{rand}"}
            params.setdefault("target", {"method": "POST", "path": auth.login_path, "json": body, "actor": "anon"})
            control = {**params["target"], "expect_status": [400, 401, 403, 404]}
    if spec.get("requires_checks") is not None:
        requires = list(spec["requires_checks"])
    if spec.get("positive_control") is not None:
        control = copy.deepcopy(dict(spec["positive_control"]))
    if not enable_unvalidated and not probe_catalog.kind_validated(kind):
        params["disabled"] = True
        params["disabled_reason"] = "probe_unvalidated: no passing mutant pair for this kind"
    probe["params"] = params
    probe["requires_checks"] = requires or []
    probe["positive_control"] = control
    _probe_reference_steps(probe)
    return probe


def _probe_reference_steps(probe: dict[str, Any]) -> None:
    """A probe whose requests name a listed resource reads its id in a leading setup step (in place)."""
    params = probe.get("params") or {}
    if not _REFS.get():
        return
    uses = [probe.get("positive_control"), *(params.get(k) for k in ("target", "read", "view", "attack", "observe"))]
    steps = _with_reference_steps(list(params.get("setup") or []), uses)
    if steps != list(params.get("setup") or []):
        probe["params"] = {**params, "setup": steps}


# =====================================================================================
# Public API
# =====================================================================================


def _statedness(
    task_version: TaskSpecVersion | None,
    content: Mapping[str, Any],
    checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    from backend.generation.library.lint import tag_checks

    compiled = [c for c in checks if c.get("source") == "compiler"]
    tagged = {c["id"]: c for c in tag_checks(content, compiled)}
    derived = ((getattr(task_version, "derived", None) or {}).get("check_statedness")) or {}
    out: list[dict[str, Any]] = []
    for check in checks:
        if check.get("source") == "compiler":
            out.append(tagged[check["id"]])
        else:
            out.append({**check, "stated_in": list(derived.get(check["id"], check.get("stated_in") or []))})
    return out


def _canonical(content: Mapping[str, Any]) -> dict[str, Any]:
    """``content`` with sorted keys: the same task gives the same plan whatever its key order was."""
    return json.loads(json.dumps(content, sort_keys=True))


def _compiled_checks(
    content: Mapping[str, Any],
    stack_content: Mapping[str, Any],
    auth: AuthDriverConfig,
    task_version: TaskSpecVersion | None,
    *,
    has_frontend: bool,
) -> dict[str, dict[str, Any]]:
    """Every check of the task by id, in order: infra, per endpoint, persistence, then the hand-written ones."""
    checks: list[dict[str, Any]] = _infra_checks(stack_content, has_frontend)
    for ep in content.get("endpoints") or []:
        checks += _endpoint_checks(ep, content)
    checks += _persistence_checks(content)
    checks = [_with_login_credential(check, auth) for check in checks]
    by_id: dict[str, dict[str, Any]] = {}
    for check in checks:
        by_id.setdefault(check["id"], check)
    for hand in content.get("acceptance") or []:
        merged = {**copy.deepcopy(dict(hand)), "source": "acceptance"}
        if not merged.get("depends_on") and merged.get("category") != "infra":
            merged["depends_on"] = ["health"]
        by_id[str(hand["id"])] = merged  # an id collision: the hand-written check wins
    return {str(c["id"]): c for c in _statedness(task_version, content, list(by_id.values()))}


# =====================================================================================
# Round 3: rules that apply to every task alike (reference ids, completed hand-written requests)
# =====================================================================================

_REF_TYPES_WHY: Final = (
    "The task spec declares `{name}` as a reference to {entity} and types the id of a {entity} as "
    "{id_types} in its own response schemas, but types the reference as {before}: a `{name}` is that "
    "{entity}'s id, so the response schema accepts the id types of the referenced entity (plus null where the "
    "property was nullable). An app with string ids could not answer any other value (P4: the written "
    "expectation contradicts the task spec itself)."
)


def _declared_id_types(schema: Any) -> list[str]:
    """The types a response schema gives ``id`` (of the object itself and of the items of a list)."""
    found: list[str] = []
    listed = _item_schema(schema)
    for candidate in (schema, listed[1] if listed is not None else None):
        sub = ((candidate or {}).get("properties") or {}).get("id") if isinstance(candidate, dict) else None
        if isinstance(sub, list):
            sub = {"type": sub}
        if isinstance(sub, dict) and sub.get("type"):
            declared = sub["type"]
            found += [str(t) for t in ([declared] if isinstance(declared, str) else declared)]
    return found


def _id_types(entity: Mapping[str, Any], content: Mapping[str, Any]) -> list[str]:
    """The JSON types the task spec gives the ``id`` of ``entity`` in the responses of its own endpoints.

    When no endpoint path names the entity (``/api/orgs`` for ``Organization``): the types the task gives
    every ``id``, provided all its response schemas agree.
    """
    endpoints = list(content.get("endpoints") or [])
    own = [ep for ep in endpoints if (_entity_for(ep, content) or {}).get("name") == entity.get("name")]
    found = [t for ep in own for t in _declared_id_types(ep.get("response_schema"))]
    if not found:
        declared = [_declared_id_types(ep.get("response_schema")) for ep in endpoints]
        every = {tuple(sorted(set(types))) for types in declared if types}
        found = list(next(iter(every))) if len(every) == 1 else []
    return [t for t in ("integer", "string", "number") if t in found]


def _reference_types(content: Mapping[str, Any]) -> dict[str, tuple[str, list[str]]]:
    """``{field: (entity, id types)}`` for every entity field of ``type: ref`` (one target entity per name)."""
    targets: dict[str, set[str]] = {}
    for entity in content.get("entities") or []:
        for field in entity.get("fields") or []:
            if field.get("ref"):
                targets.setdefault(str(field["name"]), set()).add(str(field["ref"]))
    out: dict[str, tuple[str, list[str]]] = {}
    for name, entities in targets.items():
        entity = _entity_named(next(iter(entities)), content) if len(entities) == 1 else None
        types = _id_types(entity, content) if entity is not None else []
        if types:
            out[name] = (str(entity["name"]), types)
    return out


def _widen_references(schema: Any, types: Mapping[str, tuple[str, list[str]]], changed: dict[str, Any]) -> None:
    """Give every reference property in ``schema`` (deep, in place) the id types of its entity."""
    if isinstance(schema, list):
        for item in schema:
            _widen_references(item, types, changed)
        return
    if not isinstance(schema, dict):
        return
    props = schema.get("properties")
    if isinstance(props, dict):
        for name, sub in list(props.items()):
            if name in types:
                bare = isinstance(sub, list)
                declared = sub if bare else (sub.get("type") if isinstance(sub, dict) else None)
                before = [declared] if isinstance(declared, str) else [str(t) for t in declared or []]
                if before:
                    after = [*types[name][1], *[t for t in before if t not in types[name][1]]]
                    if sorted(after) != sorted(before):
                        props[name] = after if bare else {**sub, "type": after}
                        changed[name] = {"before": before, "after": after}
                    continue
            _widen_references(sub, types, changed)
    for key, value in schema.items():
        if key != "properties":
            _widen_references(value, types, changed)


def _reference_id_types(by_id: dict[str, dict[str, Any]], content: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A response property of a ``type: ref`` field accepts the referenced entity's id types (in place).

    Applies to the response schemas of the compiled checks of every task. Never narrows: the types the task
    wrote stay accepted. Returns the ``plan.adjusted`` entries (one per changed check).
    """
    types = _reference_types(content)
    adjusted: list[dict[str, Any]] = []
    if not types:
        return adjusted
    for check in by_id.values():
        schema = (check.get("expect") or {}).get("json_schema")
        if check.get("source") != "compiler" or schema is None:
            continue
        changed: dict[str, Any] = {}
        _widen_references(schema, types, changed)
        if changed:
            name = sorted(changed)[0]
            why = _REF_TYPES_WHY.format(
                name=name,
                entity=types[name][0],
                id_types="/".join(types[name][1]),
                before="/".join(changed[name]["before"]),
            )
            adjusted.append({"id": check["id"], "why": why, "reference_types": changed})
    return adjusted


_COMPLETED_WHY: Final = (
    "A valid request is valid for every prose variant: this hand-written check sent a request without "
    "properties (or query parameters) that only the request schema or the exhaustive prose mark as optional "
    "or defaulted, so it also required the app to treat them so. The omitted ones are completed with the "
    "compiler's valid values; no value the check sets is changed, and a property the check's own expectation "
    "is about is left out."
)
_REFERENCED_WHY: Final = (
    " A literal id of a seeded resource is replaced by the id the app itself lists (the spec promises no "
    "particular id value)."
)
_SHAPED_START: Final = {
    "datetime": re.compile(r"(\d{4}-\d{2}-\d{2})([Tt ])(\d{2}):(\d{2})(.*)", re.DOTALL),
    "date": re.compile(r"(\d{4})-(\d{2})-(\d{2})"),
    "time": re.compile(r"(\d{2}):(\d{2})(.*)", re.DOTALL),
}
_PAIRED: Final = {
    "{future_datetime2}": "{future_datetime}",
    "{future_date2}": "{future_date}",
    "{time_hhmm2}": "{time_hhmm}",
}


def _later(start: Any, kind: str) -> str | None:
    """A literal one hour (a date: one day) after the literal ``start``, in the same notation, else ``None``."""
    import datetime as dt

    m = _SHAPED_START[kind].fullmatch(start) if isinstance(start, str) and kind in _SHAPED_START else None
    if m is None:
        return None
    try:
        if kind == "date":
            day = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))) + dt.timedelta(days=1)
            return day.isoformat()
        if kind == "time":
            hour = int(m.group(1)) + 1
            return f"{hour:02d}:{m.group(2)}{m.group(3)}" if hour < 24 else None
        moment = dt.datetime.fromisoformat(f"{m.group(1)}T{m.group(3)}:{m.group(4)}") + dt.timedelta(hours=1)
    except ValueError:
        return None
    return f"{moment.date().isoformat()}{m.group(2)}{moment.hour:02d}:{moment.minute:02d}{m.group(5)}"


def _keys(value: Any) -> set[str]:
    """Every mapping key inside ``value`` (deep)."""
    if isinstance(value, dict):
        return {str(k) for k in value}.union(*(_keys(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(_keys(v) for v in value)) if value else set()
    return set()


def _schema_names(schema: Any) -> set[str]:
    """Every property name and ``required`` name inside a JSON schema (deep)."""
    if isinstance(schema, list):
        return set().union(*(_schema_names(v) for v in schema)) if schema else set()
    if not isinstance(schema, dict):
        return set()
    out = {str(name) for name in schema.get("required") or [] if isinstance(name, str)}
    props = schema.get("properties")
    if isinstance(props, dict):
        out |= {str(name) for name in props}
    return out.union(*(_schema_names(v) for v in schema.values()))


def _about(expect: Mapping[str, Any]) -> set[str]:
    """The response properties an expectation is about: the keys of ``json_contains``, the properties and
    required names of ``json_schema``."""
    return _keys(expect.get("json_contains")) | _schema_names(expect.get("json_schema"))


def _without_setup_notes(adjusted: list[dict[str, Any]], replaced: set[str]) -> list[dict[str, Any]]:
    """``adjusted`` without the compiler's notes about setup steps an overlay then replaced."""
    out: list[dict[str, Any]] = []
    for entry in adjusted:
        if entry["id"] not in replaced:
            out.append(entry)
        elif "completed" in entry:
            kept = [change for change in entry["completed"] if change.get("step") == "request"]
            if kept:
                out.append({**entry, "completed": kept})
        elif "setup_status" not in entry:
            out.append(entry)
    return out


def _completion(
    body: Mapping[str, Any],
    valid: Mapping[str, Any],
    props: Mapping[str, Any],
    about: set[str],
    unique_seen: set[str],
    unique: set[str],
) -> tuple[dict[str, Any], dict[str, str]]:
    """``(properties to add to a hand-written body, properties left out and why)``."""
    added: dict[str, Any] = {}
    left: dict[str, str] = {}
    for name, sampled in valid.items():
        value = sampled
        if name in body:
            continue
        if name in about:
            left[name] = "the check's expectation is about this property"
            continue
        if name in unique:
            if name in unique_seen:
                left[name] = "unique: an earlier step of the check already sends the sampled value"
                continue
            unique_seen.add(name)
        sub = props.get(name) if isinstance(props.get(name), dict) else {}
        kind = _field_class(name, sub) if isinstance(value, str) else ""
        if kind in _SHAPED_START:
            ends = bool(set(_tokens(name)) & _END_TOKENS)
            partners = [
                other
                for other in body
                if isinstance(props.get(other), dict)
                and isinstance(body[other], str)
                and _field_class(other, props[other]) == kind
                and bool(set(_tokens(other)) & _END_TOKENS) != ends
            ]
            if partners and ends and value in _PAIRED:
                # the end of a span whose start the check sets: later than that start, in its notation
                start = body[partners[0]]
                if start != _PAIRED[value]:
                    later = _later(start, kind)
                    if later is None:
                        left[name] = f"no value later than the check's own {partners[0]} can be derived"
                        continue
                    value = later
            elif partners and not ends:
                left[name] = f"the check sets {partners[0]}; a sampled start could lie after it"
                continue
        added[name] = copy.deepcopy(value)
    return added, left


def _complete_handwritten(by_id: dict[str, dict[str, Any]], content: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Hand-written requests and setup steps are completed with the compiler's valid values (in place).

    One rule for every task (round 3; it replaces per-check overlay repairs):

    * **Body** of a ``POST`` / ``PUT`` whose endpoint is known: every property of the endpoint's valid request
      (``_request_for``: request schema, overlay samples) the hand-written body omits is added. A value the
      check sets is never changed. Not completed: a ``PATCH`` (a partial update by definition: an added
      property would change the resource), an ``/api/auth/`` request (the body is the identity under test), a
      multipart upload, a request without a JSON / form body, a request that must be refused as invalid
      (every accepted status is 400 / 422: the omission may be the fault under test), and a property the
      check's expectation is about (``_keys`` of ``expect``: the check measures its default). The end of a
      span whose start the check sets is derived from that start (``_later``).
    * **Query**: the default-valued parameters of ``_default_query`` the request does not set (same
      exceptions).
    * **References**: a literal value of a reference field (``_reference_sources``) in a request that must
      succeed becomes the id the app lists.

    Returns the ``plan.adjusted`` entries (one per changed check).
    """
    from backend.generation.library import lint

    unique = _unique_fields(content)
    refs = _REFS.get()
    adjusted: list[dict[str, Any]] = []
    for check in by_id.values():
        if check.get("source") != "acceptance":
            continue
        about = _about(check.get("expect") or {})
        final = lint._expected_statuses(check)  # noqa: SLF001 - one reading of ``expect.status`` for both
        steps: list[tuple[Any, dict[str, Any], list[int]]] = []
        for index, step in enumerate(check.get("setup") or []):
            status = step.get("expect_status")
            wanted = [int(c) for c in (status if isinstance(status, list) else [status] if status is not None else [])]
            steps.append((index, step.get("request") or {}, wanted))
        steps.append(("request", check.get("request") or {}, final))
        changes: list[dict[str, Any]] = []
        unique_seen: set[str] = set()
        replaced = False
        for where, request, statuses in steps:
            endpoint = lint._request_endpoint(request, content)  # noqa: SLF001
            if endpoint is None or (statuses and set(statuses) <= {400, 422}):
                continue
            entry: dict[str, Any] = {}
            query = {
                name: value
                for name, value in _default_query(endpoint, content).items()
                if name not in (request.get("query") or {}) and name not in about
            }
            if query:
                request["query"] = {**(request.get("query") or {}), **query}
                entry["query"] = query
            part = next((key for key in ("json", "form") if isinstance(request.get(key), dict)), None)
            method = str(request.get("method", "GET")).upper()
            succeeds = bool(statuses) and all(200 <= code < 300 for code in statuses)
            if part is not None and succeeds:
                for name, value in list(request[part].items()):
                    literal = not (isinstance(value, str) and placeholder_names(value))
                    if name in refs and literal and not isinstance(value, (dict, list, bool)) and value is not None:
                        request[part][name] = "{" + str(refs[name]["placeholder"]) + "}"
                        entry.setdefault("references", {})[name] = {"before": value, "after": request[part][name]}
                        replaced = True
            auth_path = str(endpoint.get("path", "")).startswith("/api/auth/")
            if part is not None and method in ("POST", "PUT") and not auth_path and not request.get("files"):
                if endpoint.get("content_type", "json") != "multipart":
                    valid = _request_for(endpoint, content).get(part)
                    props = ((_request_schema(endpoint, content) or {}).get("properties")) or {}
                    if isinstance(valid, dict):
                        added, left = _completion(request[part], valid, props, about, unique_seen, unique)
                        if added:
                            request[part] = {**request[part], **added}
                            entry["added"] = added
                        if left:
                            entry["left_out"] = left
            if entry:
                changes.append({"step": where, **entry})
        if changes:
            why = _COMPLETED_WHY + (_REFERENCED_WHY if replaced else "")
            adjusted.append({"id": check["id"], "why": why, "completed": changes})
    return adjusted


def _reference_steps(by_id: dict[str, dict[str, Any]]) -> None:
    """Every check that names a listed resource gets the setup step that reads its id (in place)."""
    if not _REFS.get():
        return
    for check in by_id.values():
        uses = [check.get("request"), [extra.get("request") for extra in check.get("also") or []]]
        check["setup"] = _with_reference_steps(list(check.get("setup") or []), uses)


_SETUP_NORMALISED_WHY: Final = (
    "A setup is not an assertion: this hand-written setup step required exactly {before} from "
    "{method} {path}, but the {variants} prose, in which the check is stated, gives no success status for that "
    "endpoint. 200 and 201 both mean the resource was created (as in every compiled setup); the exact status is "
    "asserted once, by {endpoint}.ok."
)


def _normalise_setups(by_id: dict[str, dict[str, Any]], content: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Hand-written setup creates accept 200 and 201 where the prose leaves the status open (in place).

    Compiled setups always do (``_created``). A hand-written step that demands exactly 201 (or 200) from a
    create endpoint is kept as written only when every prose variant the check is stated in gives that status
    for the endpoint; otherwise an app that follows that prose and answers the other success status would
    fail a check that measures something else. Returns the ``plan.adjusted`` entries (one per changed step).
    """
    from backend.generation.library import lint

    prose = {str(k): str(v) for k, v in (content.get("prose") or {}).items()}
    adjusted: list[dict[str, Any]] = []
    for check in by_id.values():
        if check.get("source") != "acceptance":
            continue
        variants = [v for v in (check.get("stated_in") or _PROSE_VARIANTS) if v in prose]
        for index, step in enumerate(check.get("setup") or []):
            request = step.get("request") or {}
            status = step.get("expect_status")
            if request.get("method") != "POST" or isinstance(status, bool) or status not in (200, 201):
                continue
            endpoint = lint._request_endpoint(request, content)  # noqa: SLF001 - one matching rule for both
            if endpoint is None or endpoint.get("success_status", 200) not in (200, 201):
                continue
            if str(endpoint.get("path", "")).rstrip("/") in lint._AUTH_CONTRACT_PATHS:  # noqa: SLF001
                continue  # stated to every variant by the auth contract
            open_in = [v for v in variants if not lint.status_stated(prose[v], endpoint, int(status))]
            if not open_in:
                continue
            step["expect_status"] = [200, 201]
            adjusted.append(
                {
                    "id": check["id"],
                    "why": _SETUP_NORMALISED_WHY.format(
                        before=status,
                        method=request.get("method"),
                        path=request.get("path"),
                        variants="/".join(open_in),
                        endpoint=endpoint.get("id"),
                    ),
                    "setup_status": {"step": index, "before": status, "after": [200, 201]},
                },
            )
    return adjusted


def _apply_probe_patches(
    probes: list[dict[str, Any]],
    overlay: Overlay,
    content: Mapping[str, Any],
    *,
    compiled: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply ``probes.<id>`` of the overlay in place: ``(adjusted, issues)``.

    Only the setup steps of a probe can be replaced (``_PROBE_PATCH_KEYS``): the new steps must capture every
    name the compiled ones captured, so the attack, the control and the verdict rule stay as compiled.
    """
    adjusted: list[dict[str, Any]] = []
    issues: list[str] = []
    by_id = {str(p["id"]): p for p in probes}
    known = {str(p.get("id")) for p in content.get("probes") or []}
    for probe_id, patch in overlay.probes.items():
        probe = by_id.get(probe_id)
        if probe is None:
            if probe_id not in known or compiled:
                issues.append(f"probes.{probe_id}: the compiled plan has no such probe")
            continue
        params = dict(probe.get("params") or {})
        before = params.get("setup") or []
        steps = _strip_nulls(patch["setup"])
        listed = {str(reference["placeholder"]) for reference in _REFS.get().values()}  # re-added after the patch
        was = {name for step in before for name in step.get("capture") or {}} - listed
        now = {name for step in steps for name in step.get("capture") or {}}
        if was - now:
            issues.append(f"probes.{probe_id}.setup: must capture {sorted(was - now)} like the compiled setup")
        params["setup"] = steps
        probe["params"] = params
        try:
            for step in steps:
                CompiledSetupStep.model_validate(step)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            issues.append(f"probes.{probe_id}.setup: not a valid setup step ({where}: {first['msg']})")
        adjusted.append({"id": probe_id, "why": str(patch.get("why") or "").strip(), "probe_setup": True})
    return adjusted, issues


def _apply_check_patches(
    by_id: dict[str, dict[str, Any]],
    overlay: Overlay,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Apply ``checks.<id>`` of the overlay in place: ``(skipped, adjusted, issues)``."""
    skipped: list[dict[str, Any]] = []
    adjusted: list[dict[str, Any]] = []
    issues: list[str] = []
    for check_id, patch in overlay.checks.items():
        check = by_id.get(check_id)
        if check is None:
            if check_id not in _INFRA_IDS:
                issues.append(f"checks.{check_id}: the compiled plan has no such check")
            continue
        why = str(patch.get("why") or "").strip()
        if patch.get("skip") is True:
            skipped.append({"id": check_id, "why": why})
            del by_id[check_id]
            continue
        before = copy.deepcopy(check)
        if "request" in patch:
            check["request"] = _merge(check.get("request") or {}, patch["request"])
        if "setup" in patch:
            check["setup"] = _strip_nulls(patch["setup"])
        if "expect" in patch:
            check["expect"] = _merge(check.get("expect") or {}, patch["expect"])
        if "stated_in" in patch:
            stated = set(check.get("stated_in") or []) | set(patch["stated_in"])
            check["stated_in"] = [v for v in _PROSE_VARIANTS if v in stated]
        check["overlay"] = True
        entry: dict[str, Any] = {"id": check_id, "why": why}
        for key in ("expect", "stated_in"):
            if before.get(key) != check.get(key):
                entry[key] = {"before": before.get(key), "after": copy.deepcopy(check.get(key))}
        if len(entry) > 2:
            adjusted.append(entry)
        try:
            CompiledCheck.model_validate(check)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            issues.append(f"checks.{check_id}: the patched check is not a valid check ({where}: {first['msg']})")
    return skipped, adjusted, issues


def _drop_skipped(by_id: dict[str, dict[str, Any]], probes: list[dict[str, Any]], skipped: set[str]) -> None:
    """A skipped check is nobody's precondition any more (its dependants are measured on their own)."""
    if not skipped:
        return
    for check in by_id.values():
        check["depends_on"] = [d for d in check.get("depends_on") or [] if d not in skipped]
    for probe in probes:
        probe["requires_checks"] = [r for r in probe.get("requires_checks") or [] if r not in skipped]


def _run_order(checks: list[dict[str, Any]], probes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The checks with the compiler's ``.invalid.*`` moved to the end (module docstring: Order)."""
    needed = {d for c in checks for d in c.get("depends_on") or []}
    needed |= {r for p in probes for r in p.get("requires_checks") or []}

    def last(check: Mapping[str, Any]) -> bool:
        return check.get("source") == "compiler" and ".invalid." in str(check["id"]) and check["id"] not in needed

    return [c for c in checks if not last(c)] + [c for c in checks if last(c)]


def compile_content(
    content: Mapping[str, Any],
    auth_mode: str,
    stack_content: Mapping[str, Any] | None = None,
    *,
    task_version: TaskSpecVersion | None = None,
    probes: str = "task",
    base_url: str = DEFAULT_BASE_URL,
    only: list[str] | None = None,
    enable_unvalidated: bool = False,
    slug: str | None = None,
    overlay: Overlay | None = None,
) -> TesterPlan:
    """``compile`` over raw content (also used by the API preview and the snapshot tests).

    ``enable_unvalidated`` compiles probe kinds without a passing mutant pair as runnable (only
    ``oracle_validate --kit`` does this, to validate them). ``slug`` names the task whose instrument overlay
    (``data/tasks/<slug>/tester.yaml``) applies; it defaults to the task of ``task_version``, and content
    compiled without either gets no overlay. ``overlay`` passes a parsed overlay instead of reading the file.
    An unusable overlay raises ``OverlayError``: a plan is never compiled with a half-applied overlay.
    """
    content = _canonical(content)
    stack_content = stack_content or {}
    if slug is None:
        slug = getattr(getattr(task_version, "task", None), "slug", None)
    if overlay is None:
        overlay = load_overlay(slug)
    issues = _static_issues(content, overlay) if overlay else []
    if issues:
        raise OverlayError(slug, issues)
    auth = auth_config(content, auth_mode)
    frontend = (stack_content.get("frontend") or {}).get("framework", "none")
    has_frontend = bool(content.get("has_frontend", True)) and frontend not in ("none", None)

    def build() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        by_id = _compiled_checks(content, stack_content, auth, task_version, has_frontend=has_frontend)
        normalised = _reference_id_types(by_id, content)
        normalised += _complete_handwritten(by_id, content)
        normalised += _normalise_setups(by_id, content)
        _reference_steps(by_id)
        compiled_probes: list[dict[str, Any]] = []
        if probes != "none":
            from backend.generation.evaluation import probes as probe_catalog

            specs = [dict(p) for p in content.get("probes") or []]
            if probes == "all_applicable":
                specs += probe_catalog.applicable(content, existing=specs)
            compiled_probes = [
                _probe(spec, content, by_id, auth, enable_unvalidated=enable_unvalidated) for spec in specs
            ]
        return by_id, compiled_probes, normalised

    references = _REFS.set(_reference_sources(content))
    try:
        token = _ACTIVE.set(overlay)
        try:
            by_id, compiled_probes, adjusted = build()
        finally:
            _ACTIVE.reset(token)
        skipped: list[dict[str, Any]] = []
        if overlay:
            token = _ACTIVE.set(_NO_OVERLAY)
            try:
                plain, _, _ = build()
            finally:
                _ACTIVE.reset(token)
            for check_id, check in by_id.items():
                if plain.get(check_id) != check:
                    check["overlay"] = True  # a sample or an endpoint patch changed its request or its setup
            skipped, patched, issues = _apply_check_patches(by_id, overlay)
            # an overlay that replaces the setup of a check supersedes the compiler's notes about that setup
            replaced = {check_id for check_id, patch in overlay.checks.items() if "setup" in patch}
            adjusted = [*_without_setup_notes(adjusted, replaced), *patched]
            probe_adjusted, probe_issues = _apply_probe_patches(
                compiled_probes,
                overlay,
                content,
                compiled=probes != "none",
            )
            adjusted += probe_adjusted
            issues += probe_issues
            if issues:
                raise OverlayError(slug, issues)
            gone = {entry["id"] for entry in skipped}
            adjusted = [entry for entry in adjusted if entry["id"] not in gone]
            _drop_skipped(by_id, compiled_probes, gone)
            # a patched request or setup may name a listed resource: its id is still read first
            _reference_steps(by_id)
            for probe in compiled_probes:
                _probe_reference_steps(probe)
    finally:
        _REFS.reset(references)
    ordered = _run_order(list(by_id.values()), compiled_probes)
    signed = [
        {"method": str(ep["method"]), "path": str(ep["path"])}
        for ep in content.get("endpoints") or []
        if "webhook" in (ep.get("tags") or [])
    ]
    contract = stack_content.get("run_contract") or {}
    # the public-looking mock upstream is attached only for a plan that names it (CONTRACT section 5)
    uses_upstream = "upstream_base" in placeholder_names([ordered, compiled_probes])
    return TesterPlan(
        compiler_version=COMPILER_VERSION,
        base_url=base_url,
        auth=auth,
        actors=["anon", "user_a", "user_b", "admin"],
        checks=[CompiledCheck.model_validate(c) for c in ordered],
        probes=[CompiledProbe.model_validate(p) for p in compiled_probes],
        only=only,
        skipped=skipped,
        adjusted=adjusted,
        upstream={"hosts": list(UPSTREAM_HOSTS), "port": UPSTREAM_PORT} if uses_upstream else None,
        health_path=str(contract.get("health_path") or "/api/health"),
        signed_routes=signed,
        has_frontend=has_frontend,
    )


def overlay_problems(slug: str, content: Mapping[str, Any], data_dir: Path | str | None = None) -> list[str]:
    """Everything wrong with the instrument overlay of task ``slug`` (empty: usable, or there is none).

    The lint's view of the strict loader: the file itself, its samples and endpoint patches against the
    task's request schemas, and its check ids against the plan of every auth mode.
    """
    try:
        overlay = load_overlay(slug, data_dir)
    except OverlayError as exc:
        return list(exc.issues)
    if not overlay_path(slug, data_dir).is_file():
        return []
    found: list[str] = []
    for mode in AUTH_MODES:
        try:
            compile_content(content, mode, {}, probes="none", slug=slug, overlay=overlay)
        except OverlayError as exc:
            found += [issue for issue in exc.issues if issue not in found]
        except (KeyError, TypeError, ValueError) as exc:
            issue = f"the plan does not compile ({mode}): {exc}"
            if issue not in found:
                found.append(issue)
    return found


def compile(  # noqa: A001 - the spec's name
    task_version: TaskSpecVersion,
    auth_mode: str,
    stack: StackProfile | None,
    *,
    probes: str = "task",
    base_url: str = DEFAULT_BASE_URL,
    only: list[str] | None = None,
) -> TesterPlan:
    """The tester plan of one (task, auth mode, stack), with the task's instrument overlay."""
    return compile_content(
        task_version.content or {},
        auth_mode,
        (stack.content if stack is not None else None) or {},
        task_version=task_version,
        probes=probes,
        base_url=base_url,
        only=only,
    )


def plan_json(plan: AcceptancePlan) -> dict[str, Any]:
    """The JSON written to ``/in/plan.json`` (aliases: ``request.json``)."""
    return plan.model_dump(mode="json", by_alias=True)


def checks_for_endpoints(
    task_version: TaskSpecVersion,
    endpoint_ids: Iterable[str],
    *,
    categories: Iterable[str] | None = None,
) -> list[str]:
    """Check ids linked to ``endpoint_ids`` (slot targets): compiled and hand-written, sorted.

    ``categories`` restricts the result (e.g. ``["functional"]``). Infra checks are never linked.
    """
    wanted = {str(e) for e in endpoint_ids}
    allowed = set(categories) if categories is not None else None
    plan = compile_content(task_version.content or {}, "contract_bearer", {}, task_version=task_version, probes="none")
    return sorted(
        {
            check.id
            for check in plan.checks
            if check.endpoint in wanted
            and check.category != "infra"
            and (allowed is None or check.category in allowed)
        },
    )
