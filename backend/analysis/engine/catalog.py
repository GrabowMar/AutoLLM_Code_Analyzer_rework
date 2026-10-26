"""Tool catalog: ``data/tools/<slug>.yaml`` -> :class:`ToolSpec` (owner: toolchain stage).

A spec says what a tool is (kind, category, languages, file kinds), how it is
configured (``config_schema`` / ``default_config``), its resources and its
canary expectations. How it is *invoked and interpreted* lives in the
matching adapter (``engine/adapters/<slug>.py``).

Spec files are validated strictly when loaded: an unknown key, a wrong type
or a default that does not satisfy its own field is a :class:`SpecError`,
because a silently misread spec would change what a study measures.

``config_schema`` fields (the profile editor renders them; profiles store the
effective values)::

    - name: select            # identifier, unique within the tool
      type: string            # string | number | integer | boolean | select | multiselect
      label: Rule selection
      description: ...
      default: "E9,F,B,S,PLE"
      applies_to: argv        # optional: argv (default) | interpretation
      # string: pattern (full-match regex), min_length, max_length, placeholder
      # number/integer: min, max, step
      # select/multiselect: options: [{value, label}] (or plain values)
      # multiselect: min_items, max_items

Hashes (frozen into every plan, so a change is visible as a different plan):

* ``spec_hash`` covers what changes a measurement: slug, kind, category,
  languages, file kinds, output kind, version, adapter version, resources,
  config schema and default config. Presentation fields (name, description,
  icon, display order, docs URL) are excluded so editing a description does
  not make earlier runs incomparable. ``adapter_version`` is bumped whenever
  an adapter's argv construction or interpretation changes.
* :func:`config_hash` covers the effective config + tool version + spec hash.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import yaml

TOOLS_DIR = Path(__file__).resolve().parent.parent / "data" / "tools"
AI_TOOL_SLUG = "ai_review"

KINDS = ("static", "metrics", "ai")
CATEGORIES = (
    "security",
    "secrets",
    "lint",
    "types",
    "dead_code",
    "complexity",
    "duplication",
    "style",
    "container",
    "ai",
)
OUTPUT_KINDS = ("findings", "metrics", "mixed")
FILE_KINDS = ("source", "config", "manifest", "lockfile", "doc", "asset", "build", "env", "other")
LANGUAGES = (
    "python",
    "javascript",
    "jsx",
    "typescript",
    "tsx",
    "vue",
    "svelte",
    "html",
    "css",
    "json",
    "yaml",
    "toml",
    "markdown",
    "dockerfile",
    "shell",
    "text",
    "env",
    "other",
)
FIELD_TYPES = ("string", "number", "integer", "boolean", "select", "multiselect")

# Spec keys and whether they are part of spec_hash.
_SPEC_KEYS: dict[str, bool] = {
    "slug": True,
    "name": False,
    "kind": True,
    "category": True,
    "languages": True,
    "file_kinds": True,
    "output_kind": True,
    "version": True,
    "adapter_version": True,
    "description": False,
    "docs_url": False,
    "icon": False,
    "display_order": False,
    "default_enabled": False,
    "default_required": False,
    "mem_limit": True,
    "timeout_s": True,
    "config_schema": True,
    "default_config": True,
    "canary": False,
    "notes": False,
}
_REQUIRED_KEYS = ("slug", "name", "kind", "category", "languages", "file_kinds", "output_kind")
_FIELD_KEYS: dict[str, tuple[str, ...]] = {
    "string": ("pattern", "min_length", "max_length", "placeholder"),
    "number": ("min", "max", "step"),
    "integer": ("min", "max", "step"),
    "boolean": (),
    "select": ("options",),
    "multiselect": ("options", "min_items", "max_items"),
}
_COMMON_FIELD_KEYS = ("name", "type", "label", "description", "default", "applies_to")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_MEM_RE = re.compile(r"^\d+(?:\.\d+)?[kmg]$")


class ConfigError(ValueError):
    """Invalid configuration; ``field_errors`` maps a dotted field path to a message."""

    def __init__(self, field_errors: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in field_errors.items()))
        self.field_errors = field_errors


class SpecError(ValueError):
    """A ``data/tools/*.yaml`` file is malformed (a catalog data bug, not user input)."""


@dataclass(frozen=True)
class ToolSpec:
    slug: str
    name: str
    kind: str  # static | metrics | ai
    category: str  # models.ToolCategory value
    languages: tuple[str, ...]  # SampleFile.language values; ("*",) = any text file
    file_kinds: tuple[str, ...]  # SampleFile.kind values it scans; ("*",) = any kind
    output_kind: str  # findings | metrics | mixed
    description: str = ""
    docs_url: str = ""
    icon: str = ""
    display_order: int = 100
    default_enabled: bool = True
    default_required: bool = True
    mem_limit: str = "768m"
    timeout_s: int = 180
    config_schema: tuple[dict[str, Any], ...] = ()
    default_config: dict[str, Any] = field(default_factory=dict)
    canary: dict[str, Any] = field(default_factory=dict)
    spec_hash: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def version(self) -> str:
        """The pinned version declared by the spec (the image manifest is authoritative)."""
        return str(self.raw.get("version") or "")

    @property
    def adapter_version(self) -> int:
        return int(self.raw.get("adapter_version") or 0)

    def schema_field(self, name: str) -> dict[str, Any] | None:
        return next((f for f in self.config_schema if f["name"] == name), None)


_lock = threading.Lock()
_cache: dict[str, ToolSpec] | None = None


def load_specs(*, refresh: bool = False) -> dict[str, ToolSpec]:
    """All specs keyed by slug (cached; ``refresh`` re-reads the YAML)."""
    global _cache  # noqa: PLW0603 - process-wide read-only cache
    with _lock:
        if _cache is None or refresh:
            specs: dict[str, ToolSpec] = {}
            for path in sorted(TOOLS_DIR.glob("*.yaml")):
                spec = _load_file(path)
                specs[spec.slug] = spec
            if AI_TOOL_SLUG not in specs:
                msg = f"the catalog has no {AI_TOOL_SLUG}.yaml"
                raise SpecError(msg)
            _cache = specs
        return dict(_cache)


def get_spec(slug: str) -> ToolSpec:
    """Spec for *slug*; ``KeyError`` if unknown."""
    specs = load_specs()
    if slug not in specs:
        raise KeyError(slug)
    return specs[slug]


def static_specs() -> list[ToolSpec]:
    """Non-AI specs in display order."""
    return sorted((s for s in load_specs().values() if s.kind != "ai"), key=lambda s: (s.display_order, s.slug))


def validate_tool_config(slug: str, config: dict[str, Any] | None) -> dict[str, Any]:
    """Effective config: defaults merged with *config*, type-checked against
    ``config_schema`` (unknown keys rejected). Raises :class:`ConfigError`."""
    try:
        spec = get_spec(slug)
    except KeyError:
        raise ConfigError({"slug": f"unknown tool {slug!r}"}) from None
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ConfigError({"config": "must be an object"})
    errors: dict[str, str] = {}
    fields = {f["name"]: f for f in spec.config_schema}
    effective = copy.deepcopy(spec.default_config)
    for key in sorted(config, key=str):
        if key not in fields:
            errors[str(key)] = "unknown option"
            continue
        try:
            effective[key] = coerce_value(fields[key], config[key])
        except ValueError as exc:
            errors[key] = str(exc)
    if errors:
        raise ConfigError(errors)
    return effective


def config_hash(slug: str, config: dict[str, Any], tool_version: str = "") -> str:
    """sha256 over canonical JSON of (slug, effective config, version, spec_hash)."""
    spec = get_spec(slug)
    effective = validate_tool_config(slug, config)
    return _sha256({"slug": slug, "config": effective, "version": tool_version, "spec_hash": spec.spec_hash})


def coerce_value(spec_field: dict[str, Any], value: Any) -> Any:
    """Type-check one config value against its field; returns the normalized value.

    Raises ``ValueError`` with a user-facing message. Integral floats are
    accepted for integer fields (JSON clients send ``80.0``); multiselect values
    are de-duplicated and put in option order so equal selections hash equally.
    """
    kind = spec_field["type"]
    if kind == "boolean":
        if not isinstance(value, bool):
            msg = "must be a boolean"
            raise ValueError(msg)
        return value
    if kind in ("number", "integer"):
        return _coerce_number(spec_field, value)
    if kind == "string":
        return _coerce_string(spec_field, value)
    values = [opt["value"] for opt in spec_field["options"]]
    if kind == "select":
        if isinstance(value, bool) or value not in values:
            msg = f"must be one of {', '.join(map(str, values))}"
            raise ValueError(msg)
        return value
    # multiselect
    if not isinstance(value, list | tuple):
        msg = "must be a list"
        raise ValueError(msg)  # noqa: TRY004 - ValueError carries the user-facing field message
    unknown = [v for v in value if isinstance(v, bool) or v not in values]
    if unknown:
        msg = f"unknown value {unknown[0]!r}; allowed: {', '.join(map(str, values))}"
        raise ValueError(msg)
    selected = [v for v in values if v in value]
    if "min_items" in spec_field and len(selected) < spec_field["min_items"]:
        msg = f"select at least {spec_field['min_items']}"
        raise ValueError(msg)
    if "max_items" in spec_field and len(selected) > spec_field["max_items"]:
        msg = f"select at most {spec_field['max_items']}"
        raise ValueError(msg)
    return selected


def _coerce_number(spec_field: dict[str, Any], value: Any) -> int | float:
    integer = spec_field["type"] == "integer"
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = "must be an integer" if integer else "must be a number"
        raise ValueError(msg)  # noqa: TRY004 - ValueError carries the user-facing field message
    if isinstance(value, float) and not math.isfinite(value):
        msg = "must be a finite number"
        raise ValueError(msg)
    if integer and isinstance(value, float):
        if not value.is_integer():
            msg = "must be an integer"
            raise ValueError(msg)
        value = int(value)
    if "min" in spec_field and value < spec_field["min"]:
        msg = f"must be >= {spec_field['min']}"
        raise ValueError(msg)
    if "max" in spec_field and value > spec_field["max"]:
        msg = f"must be <= {spec_field['max']}"
        raise ValueError(msg)
    return value


def _coerce_string(spec_field: dict[str, Any], value: Any) -> str:
    if not isinstance(value, str):
        msg = "must be a string"
        raise ValueError(msg)  # noqa: TRY004 - ValueError carries the user-facing field message
    if "min_length" in spec_field and len(value) < spec_field["min_length"]:
        msg = f"must be at least {spec_field['min_length']} characters"
        raise ValueError(msg)
    if "max_length" in spec_field and len(value) > spec_field["max_length"]:
        msg = f"must be at most {spec_field['max_length']} characters"
        raise ValueError(msg)
    if spec_field.get("pattern") and not re.fullmatch(spec_field["pattern"], value):
        msg = f"must match {spec_field['pattern']}"
        raise ValueError(msg)
    return value


# --------------------------------------------------------------------------
# Loading / validation
# --------------------------------------------------------------------------


def _sha256(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def spec_hash(raw: dict[str, Any]) -> str:
    """Hash of the measurement-relevant part of a normalized spec (see module docstring)."""
    return _sha256({key: raw[key] for key, hashed in _SPEC_KEYS.items() if hashed and key in raw})


def _load_file(path: Path) -> ToolSpec:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"{path.name}: invalid YAML: {exc}"
        raise SpecError(msg) from exc
    return parse_spec(data, source=path.name, expected_slug=path.stem)


def parse_spec(data: Any, *, source: str = "<spec>", expected_slug: str | None = None) -> ToolSpec:
    """Validate and normalize one spec mapping (raises :class:`SpecError`)."""
    if not isinstance(data, dict):
        msg = f"{source}: a tool spec must be a mapping"
        raise SpecError(msg)
    problems: list[str] = []
    unknown = sorted(set(map(str, data)) - set(_SPEC_KEYS))
    if unknown:
        problems.append(f"unknown keys {unknown}")
    problems += [f"missing key {key!r}" for key in _REQUIRED_KEYS if key not in data]
    if problems:
        raise SpecError(f"{source}: " + "; ".join(problems))

    slug = data["slug"]
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        problems.append(f"slug {slug!r} is not a valid slug")
    elif expected_slug is not None and slug != expected_slug:
        problems.append(f"slug {slug!r} does not match the file name")
    _check(problems, isinstance(data["name"], str) and data["name"].strip(), "name must be a non-empty string")
    _check(problems, data["kind"] in KINDS, f"kind must be one of {list(KINDS)}")
    _check(problems, data["category"] in CATEGORIES, f"category must be one of {list(CATEGORIES)}")
    _check(problems, data["output_kind"] in OUTPUT_KINDS, f"output_kind must be one of {list(OUTPUT_KINDS)}")
    languages = _string_list(data["languages"], "languages", problems)
    if languages and languages != ["*"]:
        bad = [lang for lang in languages if lang not in LANGUAGES]
        _check(problems, not bad, f"unknown languages {bad}")
    file_kinds = _string_list(data["file_kinds"], "file_kinds", problems)
    if file_kinds and file_kinds != ["*"]:
        bad = [kind for kind in file_kinds if kind not in FILE_KINDS]
        _check(problems, not bad, f"unknown file kinds {bad}")

    for key in ("description", "docs_url", "icon", "version", "notes"):
        _check(problems, isinstance(data.get(key, ""), str), f"{key} must be a string")
    for key in ("default_enabled", "default_required"):
        _check(problems, isinstance(data.get(key, True), bool), f"{key} must be a boolean")
    display_order = data.get("display_order", 100)
    _check(problems, _is_int(display_order), "display_order must be an integer")
    adapter_version = data.get("adapter_version", 0)
    _check(problems, _is_int(adapter_version) and adapter_version >= 0, "adapter_version must be an integer >= 0")
    mem_limit = data.get("mem_limit", "768m")
    _check(problems, isinstance(mem_limit, str) and bool(_MEM_RE.match(mem_limit)), "mem_limit must look like 512m")
    timeout_s = data.get("timeout_s", 180)
    _check(problems, _is_int(timeout_s) and 1 <= timeout_s <= 7200, "timeout_s must be an integer in 1..7200")
    canary = data.get("canary") or {}
    _check(problems, isinstance(canary, dict), "canary must be a mapping")

    schema = _parse_schema(data.get("config_schema") or [], problems)
    defaults = _defaults(schema, data.get("default_config"), problems)
    if problems:
        raise SpecError(f"{source}: " + "; ".join(problems))

    raw = {
        "slug": slug,
        "name": data["name"].strip(),
        "kind": data["kind"],
        "category": data["category"],
        "languages": languages,
        "file_kinds": file_kinds,
        "output_kind": data["output_kind"],
        "version": data.get("version", ""),
        "adapter_version": adapter_version,
        "description": " ".join(str(data.get("description", "")).split()),
        "docs_url": data.get("docs_url", ""),
        "icon": data.get("icon", ""),
        "display_order": display_order,
        "default_enabled": data.get("default_enabled", True),
        "default_required": data.get("default_required", True),
        "mem_limit": mem_limit,
        "timeout_s": timeout_s,
        "config_schema": schema,
        "default_config": defaults,
        "canary": canary,
        "notes": " ".join(str(data.get("notes", "")).split()),
    }
    return ToolSpec(
        slug=slug,
        name=raw["name"],
        kind=raw["kind"],
        category=raw["category"],
        languages=tuple(languages),
        file_kinds=tuple(file_kinds),
        output_kind=raw["output_kind"],
        description=raw["description"],
        docs_url=raw["docs_url"],
        icon=raw["icon"],
        display_order=display_order,
        default_enabled=raw["default_enabled"],
        default_required=raw["default_required"],
        mem_limit=mem_limit,
        timeout_s=timeout_s,
        config_schema=tuple(schema),
        default_config=copy.deepcopy(defaults),
        canary=canary,
        spec_hash=spec_hash(raw),
        raw=raw,
    )


def _defaults(schema: list[dict[str, Any]], declared: Any, problems: list[str]) -> dict[str, Any]:
    """Field defaults, overridden by a declared ``default_config`` (which must type-check)."""
    defaults = {f["name"]: copy.deepcopy(f["default"]) for f in schema}
    if declared is None:
        return defaults
    if not isinstance(declared, dict):
        problems.append("default_config must be a mapping")
        return defaults
    extra = sorted(set(declared) - set(defaults))
    _check(problems, not extra, f"default_config has keys without a config_schema field: {extra}")
    fields = {f["name"]: f for f in schema}
    for key, value in declared.items():
        if key in fields:
            try:
                defaults[key] = coerce_value(fields[key], value)
            except ValueError as exc:
                problems.append(f"default_config.{key} {exc}")
    return defaults


def _parse_schema(raw: Any, problems: list[str]) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        problems.append("config_schema must be a list")
        return []
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        parsed = _parse_field(item, f"config_schema[{i}]", seen, problems)
        if parsed is not None:
            fields.append(parsed)
    return fields


class _FieldProblem(Exception):  # noqa: N818 - internal control flow of _parse_field
    """One problem with one config_schema field (collected, not raised to callers)."""


def _parse_field(item: Any, where: str, seen: set[str], problems: list[str]) -> dict[str, Any] | None:
    """A normalized config_schema field, or None after appending its problem to *problems*."""
    try:
        return _field(item, where, seen)
    except _FieldProblem as exc:
        problems.append(str(exc))
        return None


def _require(ok: Any, message: str) -> None:
    if not ok:
        raise _FieldProblem(message)


def _field(item: Any, where: str, seen: set[str]) -> dict[str, Any]:
    _require(isinstance(item, dict), f"{where} must be a mapping")
    name, kind = item.get("name"), item.get("type")
    _require(isinstance(name, str) and _NAME_RE.match(name), f"{where}.name must be a lower_snake identifier")
    where = f"config_schema.{name}"
    _require(name not in seen, f"{where} is duplicated")
    seen.add(name)
    _require(kind in FIELD_TYPES, f"{where}.type must be one of {list(FIELD_TYPES)}")
    unknown = sorted(set(item) - set(_COMMON_FIELD_KEYS) - set(_FIELD_KEYS[kind]))
    _require(not unknown, f"{where} has unknown keys {unknown}")
    _require("default" in item, f"{where} needs a default")
    normalized: dict[str, Any] = {
        "name": name,
        "type": kind,
        "label": str(item.get("label") or name.replace("_", " ").capitalize()),
        "description": " ".join(str(item.get("description", "")).split()),
        "applies_to": item.get("applies_to", "argv"),
    }
    _require(
        normalized["applies_to"] in ("argv", "interpretation"),
        f"{where}.applies_to must be argv or interpretation",
    )
    for key in _FIELD_KEYS[kind]:
        if key in item:
            normalized[key] = item[key]
    if kind in ("select", "multiselect"):
        option_problems: list[str] = []
        normalized["options"] = _parse_options(item.get("options"), where, option_problems)
        _require(not option_problems, "; ".join(option_problems))
    if kind in ("number", "integer"):
        bounds = [normalized[k] for k in ("min", "max", "step") if k in normalized]
        _require(
            all(not isinstance(b, bool) and isinstance(b, int | float) for b in bounds),
            f"{where} min/max/step must be numbers",
        )
    if kind == "string":
        lengths = [normalized[k] for k in ("min_length", "max_length") if k in normalized]
        _require(all(_is_int(n) and n >= 0 for n in lengths), f"{where} min_length/max_length must be integers >= 0")
        if "pattern" in normalized:
            try:
                re.compile(normalized["pattern"])
            except (re.error, TypeError) as exc:
                msg = f"{where}.pattern is not a valid regex: {exc}"
                raise _FieldProblem(msg) from exc
    if kind == "multiselect":
        counts = [normalized[k] for k in ("min_items", "max_items") if k in normalized]
        _require(all(_is_int(n) and n >= 0 for n in counts), f"{where} min_items/max_items must be integers >= 0")
    try:
        normalized["default"] = coerce_value(normalized, item["default"])
    except ValueError as exc:
        msg = f"{where}.default {exc}"
        raise _FieldProblem(msg) from exc
    return normalized


def _parse_options(raw: Any, where: str, problems: list[str]) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        problems.append(f"{where}.options must be a non-empty list")
        return []
    options: list[dict[str, Any]] = []
    for opt in raw:
        entry = opt if isinstance(opt, dict) else {"value": opt, "label": str(opt)}
        value = entry.get("value")
        if isinstance(value, bool) or not isinstance(value, str | int | float):
            problems.append(f"{where}.options values must be strings or numbers")
            return []
        option = {"value": value, "label": str(entry.get("label") or value)}
        if entry.get("description"):
            option["description"] = " ".join(str(entry["description"]).split())
        options.append(option)
    if len({json.dumps(o["value"]) for o in options}) != len(options):
        problems.append(f"{where}.options has duplicate values")
        return []
    return options


def _string_list(value: Any, key: str, problems: list[str]) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) and v for v in value):
        problems.append(f"{key} must be a non-empty list of strings")
        return []
    if len(set(value)) != len(value):
        problems.append(f"{key} has duplicates")
    if "*" in value and len(value) > 1:
        problems.append(f"{key}: '*' must stand alone")
    return list(value)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check(problems: list[str], ok: Any, message: str) -> None:
    if not ok:
        problems.append(message)
