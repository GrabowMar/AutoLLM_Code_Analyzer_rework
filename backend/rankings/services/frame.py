"""MSS v2 sampling frame: one row per real model, then the hard eligibility filters.

Spec: docs/design/MSS_V2.md §1. Canonicalisation (K1-K6) collapses routers,
aliases, ``:batch`` rows, billing variants, dated snapshots, prerelease builds
and local route rows into one canonical model with a pinned version. The
eligibility filters (E1-E7; E8 "evidence" runs at scoring time in
:mod:`mss`) exclude a model with a reason code; a model that fails a filter is
never given a low score. Every step is counted (PRISMA-style flow).

The database is read once (:func:`load_rows`); everything after that is a pure
function of plain dicts, so a stored selection can be replayed without a
database. Rows are never merged in the database: this is a selection-time
mapping only.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from backend.rankings.services.constants import DEPRECATION_HORIZON_DAYS
from backend.rankings.services.constants import MIN_CONTEXT_TOKENS
from backend.rankings.services.constants import RECENCY_MAX_DAYS

FRAME_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "mss_frame.yaml"

GO_ENDPOINT = "opencode-go"
ANTHROPIC_ENDPOINT = "anthropic"
ROUTE_BY_ENDPOINT = {GO_ENDPOINT: "go", ANTHROPIC_ENDPOINT: "anthropic"}

PRERELEASE_TOKENS = frozenset({"preview", "exp", "experimental", "alpha", "beta", "contributor"})
# A local served name with one of these suffixes is a free / contributor build (K4).
_LOCAL_PRERELEASE_RE = re.compile(r"-(free|contributor)$|-contributor-free$")
_DATE_SUFFIX_RE = re.compile(r"^(?P<base>.+?)-(?P<date>\d{4}-\d{2}-\d{2}|\d{8}|\d{4})$")
_VERSION_TOKEN_RE = re.compile(r"^[vkmr]?\d+(\.\d+)*$")
_SIZE_TOKEN_RE = re.compile(r"^a?\d+(\.\d+)?[bmt]$")
_ALPHA_VERSION_RE = re.compile(r"^([a-z]+)\d+(\.\d+)*$")


# ── Config ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FrameConfig:
    """The curated ``mss_frame.yaml`` (vendors, Go limits, overrides)."""

    vendors: dict[str, dict[str, Any]] = field(default_factory=dict)
    go_limits: dict[str, Any] = field(default_factory=dict)
    lines: dict[str, str] = field(default_factory=dict)
    twins: dict[str, str] = field(default_factory=dict)
    include: dict[str, str] = field(default_factory=dict)
    exclude: dict[str, str] = field(default_factory=dict)
    open_weights: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> FrameConfig:
        raw = raw or {}
        return cls(
            vendors={str(k): dict(v or {}) for k, v in (raw.get("vendors") or {}).items()},
            go_limits=dict(raw.get("go_limits") or {}),
            lines={str(k): str(v) for k, v in (raw.get("lines") or {}).items()},
            twins={str(k): str(v) for k, v in (raw.get("twins") or {}).items()},
            include={str(k): str(v) for k, v in (raw.get("include") or {}).items()},
            exclude={str(k): str(v) for k, v in (raw.get("exclude") or {}).items()},
            open_weights={str(k): bool(v) for k, v in (raw.get("open_weights") or {}).items()},
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "vendors": self.vendors,
            "go_limits": self.go_limits,
            "lines": self.lines,
            "twins": self.twins,
            "include": self.include,
            "exclude": self.exclude,
            "open_weights": self.open_weights,
        }

    def go_limit(self, served_name: str | None) -> float | None:
        monthly = (self.go_limits or {}).get("monthly_usd") or {}
        value = monthly.get(served_name or "")
        return float(value) if isinstance(value, (int, float)) else None

    def origin(self, vendor: str) -> str:
        return str((self.vendors.get(vendor) or {}).get("origin") or "unknown")


@cache
def _load_config_cached(path: str) -> FrameConfig:
    with Path(path).open(encoding="utf-8") as fh:
        return FrameConfig.from_dict(yaml.safe_load(fh))


def load_frame_config(path: Path | str | None = None) -> FrameConfig:
    return _load_config_cached(str(path or FRAME_CONFIG_PATH))


# ── Id canonicalisation (pure) ─────────────────────────────────────────────


def _valid_date_token(token: str) -> bool:
    try:
        if len(token) == 10:  # YYYY-MM-DD
            dt.date.fromisoformat(token)
            return True
        if len(token) == 8:  # YYYYMMDD
            year = int(token[:4])
            dt.date(year, int(token[4:6]), int(token[6:]))
            return 2015 <= year <= 2040
        if len(token) == 4:  # MMDD
            dt.date(2000, int(token[:2]), int(token[2:]))
            return True
    except ValueError:
        return False
    return False


def split_variant(model_id: str) -> tuple[str, str | None]:
    """``"a/b:free"`` -> ``("a/b", "free")`` (K2)."""
    base, _, variant = model_id.partition(":")
    return base, (variant or None)


def strip_date(model_id: str) -> tuple[str, str | None]:
    """``"a/b-1017"`` -> ``("a/b", "1017")``; a non-date 4-digit tail (``-2407``: no month 24) is kept (K3)."""
    match = _DATE_SUFFIX_RE.match(model_id)
    if match and "/" in match.group("base") and _valid_date_token(match.group("date")):
        return match.group("base"), match.group("date")
    return model_id, None


def is_prerelease_id(model_id: str) -> bool:
    if model_id.startswith("stealth/"):
        return True
    slug = model_id.split("/", 1)[-1]
    return bool(set(slug.split("-")) & PRERELEASE_TOKENS)


def ga_sibling(model_id: str) -> str:
    """The id with its prerelease tokens removed (``tencent/hy4-preview`` -> ``tencent/hy4``)."""
    vendor, _, slug = model_id.partition("/")
    kept = [t for t in slug.split("-") if t not in PRERELEASE_TOKENS]
    return f"{vendor}/{'-'.join(kept)}" if kept else model_id


def canonical_id(model_id: str) -> str:
    """Canonical OpenRouter-style id of a catalog id: no ``:variant``, no date suffix (K2, K3)."""
    base, _ = split_variant(model_id)
    return strip_date(base)[0]


def line_key(canonical: str, overrides: dict[str, str] | None = None) -> str:
    """Product line of a canonical id: the slug with version tokens removed (E7).

    ``claude-opus-5.5`` -> ``claude-opus``, ``glm-5.3-flash`` -> ``glm-flash``,
    ``kimi-k3.1-pro`` -> ``kimi-pro``, ``qwen3.8-max`` -> ``qwen-max``; size
    tokens (``550b``, ``a55b``) are kept.
    """
    if overrides and canonical in overrides:
        return overrides[canonical]
    slug = canonical.split("/", 1)[-1]
    kept: list[str] = []
    for token in slug.split("-"):
        if _SIZE_TOKEN_RE.match(token):
            kept.append(token)
            continue
        if _VERSION_TOKEN_RE.match(token):
            continue
        alpha = _ALPHA_VERSION_RE.match(token)
        if alpha:
            kept.append(alpha.group(1))
            continue
        kept.append(token)
    return "-".join(kept) or slug


def vendor_of(canonical: str) -> str:
    return canonical.split("/", 1)[0] if "/" in canonical else ""


# ── Rows ───────────────────────────────────────────────────────────────────


def _as_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str) and value:
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _epoch_date(value: Any) -> dt.date | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return dt.datetime.fromtimestamp(value, tz=dt.UTC).date()
    return None


def row_from_model(m: Any) -> dict[str, Any]:
    """A plain snapshot of one ``LLMModel`` with the fields the frame reads."""
    payload = m.capabilities_json if isinstance(m.capabilities_json, dict) else {}
    endpoint = getattr(m, "endpoint", None)
    return {
        "pk": m.pk,
        "model_id": m.model_id,
        "model_name": m.model_name,
        "provider": m.provider,
        "endpoint": endpoint.slug if endpoint is not None else None,
        "served_name": m.served_model_name or "",
        "is_available": bool(m.is_available),
        "is_hidden": bool(m.is_hidden),
        "is_free": bool(m.is_free),
        "context_window": int(m.context_window or 0),
        "supports_tools": bool(m.supports_function_calling),
        "input_per_m": float(m.input_price_per_token or 0.0) * 1_000_000,
        "output_per_m": float(m.output_price_per_token or 0.0) * 1_000_000,
        "metadata": m.metadata if isinstance(m.metadata, dict) else {},
        "payload": {
            "canonical_slug": payload.get("canonical_slug"),
            "created": payload.get("created"),
            "expiration_date": payload.get("expiration_date"),
        },
    }


def load_rows() -> list[dict[str, Any]]:
    """Every ``LLMModel`` row as a plain dict (one query)."""
    from backend.llm_models.models import LLMModel

    return [row_from_model(m) for m in LLMModel.objects.select_related("endpoint").order_by("model_id")]


# ── Frame ──────────────────────────────────────────────────────────────────


def _member(row: dict[str, Any], *, kind: str, variant: str | None, date: str | None, prerelease: bool) -> dict:
    meta = row["metadata"]
    created = _epoch_date(meta.get("openrouter_created")) or _epoch_date(row["payload"].get("created"))
    return {
        "model_id": row["model_id"],
        "pk": row["pk"],
        "kind": kind,
        "variant": variant,
        "dated": date,
        "prerelease": prerelease,
        "available": row["is_available"] and not row["is_hidden"],
        # Local route rows (OpenCode Go subscription, the Anthropic key) store price 0 and
        # is_free; they are paid routes. Their free / contributor builds are prerelease (K4).
        "is_free": variant == "free" or (row["is_free"] and kind == "openrouter"),
        "served_name": row["served_name"] or None,
        "permaslug": row["payload"].get("canonical_slug"),
        "openrouter_created": created.isoformat() if created else None,
    }


def _route_kind(row: dict[str, Any]) -> str:
    if row["endpoint"] is None:
        return "openrouter"
    return ROUTE_BY_ENDPOINT.get(row["endpoint"], "local")


def _twin_target(row: dict[str, Any], config: FrameConfig) -> str | None:
    if row["model_id"] in config.twins:
        return config.twins[row["model_id"]]
    twin = row["metadata"].get("catalog_twin")
    if isinstance(twin, dict) and twin.get("model_id"):
        return str(twin["model_id"])
    return None


def _flow_step(step: str, label: str, count: int, excluded: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {"step": step, "label": label, "remaining": count, "excluded": excluded or []}


def build_frame(
    rows: list[dict[str, Any]],
    as_of: dt.date,
    *,
    include_previews: bool = False,
    config: FrameConfig | None = None,
) -> dict[str, Any]:
    """Canonicalise ``rows`` (K1-K6) and apply the eligibility filters E1-E7.

    Returns ``{"as_of", "candidates": [entry], "flow": [step], "unlinked_local": [...]}``;
    every canonical model is a candidate entry with ``eligible`` and, when
    excluded, ``exclusion = {"step", "code", "detail"}``.
    """
    config = config or load_frame_config()
    flow: list[dict[str, Any]] = [_flow_step("K0", "LLMModel rows", len(rows))]
    by_id = {r["model_id"]: r for r in rows}

    # K1: routers, aliases, :batch.
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, str]] = []
    for row in rows:
        mid = row["model_id"]
        if mid.startswith("openrouter/"):
            dropped.append({"id": mid, "code": "router"})
        elif mid.startswith("~") or mid.split(":", 1)[0].endswith("-latest"):
            dropped.append({"id": mid, "code": "alias"})
        elif mid.endswith(":batch"):
            dropped.append({"id": mid, "code": "batch_api"})
        else:
            kept.append(row)
    flow.append(_flow_step("K1", "Routers, aliases and :batch rows dropped", len(kept), dropped))

    # K2/K3 on catalog rows; K5 maps local rows onto canonical ids.
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unlinked: list[dict[str, str]] = []
    variant_merges = dated_merges = 0
    local_rows = []
    for row in kept:
        kind = _route_kind(row)
        if kind != "openrouter":
            local_rows.append(row)
            continue
        base, variant = split_variant(row["model_id"])
        canon, date = strip_date(base)
        variant_merges += variant is not None
        dated_merges += date is not None
        groups[canon].append(
            _member(row, kind=kind, variant=variant, date=date, prerelease=is_prerelease_id(canon)),
        )
    flow.append(
        _flow_step(
            "K2-K3",
            f"Variants ({variant_merges} rows) and dated snapshots ({dated_merges} rows) merged",
            len(groups),
        ),
    )

    for row in local_rows:
        kind = _route_kind(row)
        target = _twin_target(row, config)
        served = row["served_name"] or row["model_id"].split("/", 1)[-1]
        prerelease = bool(_LOCAL_PRERELEASE_RE.search(served))
        if not target:
            unlinked.append({"id": row["model_id"], "code": "unlinked_local"})
            continue
        canon = canonical_id(target)
        groups[canon].append(
            _member(row, kind=kind, variant=None, date=None, prerelease=prerelease or is_prerelease_id(canon)),
        )
    flow.append(
        _flow_step("K5", "Local route rows mapped to their canonical model", len(groups), unlinked),
    )

    # K4: a prerelease canonical model merges into its GA sibling when one exists.
    merged_prerelease: list[dict[str, str]] = []
    for canon in sorted(groups):
        if canon not in groups or not is_prerelease_id(canon):
            continue
        sibling = ga_sibling(canon)
        if sibling != canon and sibling in groups and not is_prerelease_id(sibling):
            for member in groups.pop(canon):
                member["prerelease"] = True
                groups[sibling].append(member)
            merged_prerelease.append({"id": canon, "code": f"merged_into:{sibling}"})
    flow.append(_flow_step("K4", "Prerelease builds merged into their GA model", len(groups), merged_prerelease))

    candidates: list[dict[str, Any]] = []
    for canon in sorted(groups):
        candidates.append(_entry(canon, groups[canon], by_id, as_of, config, include_previews=include_previews))
    flow.append(_flow_step("K6", "Canonical models with their routes", len(candidates)))

    # E1-E6: per-model filters, first failing filter wins.
    def _apply(step: str, label: str, test) -> None:
        excluded = []
        for entry in candidates:
            if entry["exclusion"] is not None:
                continue
            failure = test(entry)
            if failure:
                code, detail = failure
                entry["exclusion"] = {"step": step, "code": code, "detail": detail}
                excluded.append({"id": entry["canonical_id"], "code": code})
        remaining = sum(1 for e in candidates if e["exclusion"] is None)
        flow.append(_flow_step(step, label, remaining, excluded))

    _apply("E1", "Reachable on a study route now", _e1_route)
    _apply("E2", "Tool calling", _e2_tools)
    _apply("E3", f"Context window >= {MIN_CONTEXT_TOKENS // 1000}k tokens", _e3_context)
    _apply("E4", "Text input and text output", _e4_modality)
    _apply(
        "E5",
        "Generally available (not free-only, deprecated or prerelease)",
        lambda e: _e5_ga(e, as_of, config, include_previews=include_previews),
    )
    _apply("E6", f"Released within {RECENCY_MAX_DAYS} days", lambda e: _e6_recency(e, as_of))

    # E7: newest model per (vendor, line). Only a successor with the evidence E8 needs
    # (a list price and popularity or LMArena interactions) supersedes: otherwise a new
    # release without data would remove the whole line from the frame.
    by_line: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for entry in candidates:
        if entry["exclusion"] is None:
            by_line[(entry["vendor"], entry["line_key"])].append(entry)
    excluded = []
    for members in by_line.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda e: (e["release_date"] or "", e["canonical_id"]), reverse=True)
        with_evidence = [e for e in members if has_evidence(e)]
        if not with_evidence:
            continue
        newest = with_evidence[0]
        for older in members[members.index(newest) + 1 :]:
            older["exclusion"] = {
                "step": "E7",
                "code": "superseded",
                "detail": f"newer model of line {older['line_key']!r}: {newest['canonical_id']}",
            }
            excluded.append({"id": older["canonical_id"], "code": f"superseded_by:{newest['canonical_id']}"})
    flow.append(
        _flow_step(
            "E7",
            "Newest model of its product line",
            sum(e["exclusion"] is None for e in candidates),
            excluded,
        ),
    )

    for entry in candidates:
        entry["eligible"] = entry["exclusion"] is None
    return {
        "as_of": as_of.isoformat(),
        "include_previews": include_previews,
        "candidates": candidates,
        "flow": flow,
        "unlinked_local": unlinked,
    }


# ── Entry assembly ─────────────────────────────────────────────────────────


def _models_dev(row: dict[str, Any] | None) -> dict[str, Any]:
    md = (row or {}).get("metadata", {}).get("models_dev")
    return md if isinstance(md, dict) else {}


def _twin_record(row: dict[str, Any] | None) -> dict[str, Any]:
    twin = (row or {}).get("metadata", {}).get("catalog_twin")
    rec = twin.get("provider_record") if isinstance(twin, dict) else None
    return rec if isinstance(rec, dict) else {}


def _first_party(row: dict[str, Any] | None) -> dict[str, Any]:
    """The vendor's models.dev record summary stored by ``import_list_prices`` (rows without a catalog twin)."""
    price = (row or {}).get("metadata", {}).get("list_price")
    rec = price.get("first_party_record") if isinstance(price, dict) else None
    return rec if isinstance(rec, dict) else {}


def _anchor(canon: str, members: list[dict[str, Any]]) -> dict[str, Any]:
    """The row carrying the canonical model's imported inputs (popularity, list price)."""

    def rank(m: dict[str, Any]) -> tuple:
        return (
            m["model_id"] != canon,
            m["kind"] != "openrouter",
            m["prerelease"],
            m["variant"] is not None,
            -(dt.date.fromisoformat(m["openrouter_created"]).toordinal() if m["openrouter_created"] else 0),
            m["model_id"],
        )

    return sorted(members, key=rank)[0]


def _pinned(members: list[dict[str, Any]], include_previews: bool) -> dict[str, Any]:
    """The pinned version: the newest non-prerelease catalog member, else a GA route row."""
    pool = [m for m in members if include_previews or not m["prerelease"]] or members
    catalog = [m for m in pool if m["kind"] == "openrouter" and m["variant"] != "free"] or [
        m for m in pool if m["kind"] == "openrouter"
    ]
    if catalog:
        catalog.sort(key=lambda m: (m["openrouter_created"] or "", m["dated"] or "", m["model_id"]), reverse=True)
        return catalog[0]
    local = sorted(pool, key=lambda m: (m["kind"] != "go", m["model_id"]))
    return local[0]


def _release_date(pinned: dict, rows: dict[str, dict], members: list[dict]) -> tuple[str | None, str | None]:
    row = rows.get(pinned["model_id"])
    md = _models_dev(row)
    if _as_date(md.get("release_date")):
        return _as_date(md["release_date"]).isoformat(), f"models.dev:{pinned['model_id']}"
    if pinned["openrouter_created"]:
        return pinned["openrouter_created"], f"openrouter.created:{pinned['model_id']}"
    rec = _twin_record(row)
    if _as_date(rec.get("release_date")):
        return _as_date(rec["release_date"]).isoformat(), f"catalog_twin.provider_record:{pinned['model_id']}"
    for m in members:
        price = (rows.get(m["model_id"]) or {}).get("metadata", {}).get("list_price")
        first_party = (price or {}).get("first_party_record") if isinstance(price, dict) else None
        if isinstance(first_party, dict) and _as_date(first_party.get("release_date")):
            return _as_date(first_party["release_date"]).isoformat(), f"list_price.first_party:{m['model_id']}"
    return None, None


def _text_chat(inputs: list, outputs: list) -> bool:
    """E4: text in, and text as the only output (image / audio generators are not code models)."""
    return "text" in inputs and set(outputs) == {"text"}


def _modalities(rows: list[dict[str, Any]]) -> tuple[bool | None, str]:
    for row in rows:
        meta = row["metadata"]
        ins, outs = meta.get("architecture_input_modalities"), meta.get("architecture_output_modalities")
        if isinstance(ins, list) and isinstance(outs, list):
            return _text_chat(ins, outs), "openrouter.architecture"
    for row in rows:
        mods = _models_dev(row).get("modalities") or _twin_record(row).get("modalities")
        if isinstance(mods, dict) and isinstance(mods.get("input"), list) and isinstance(mods.get("output"), list):
            return _text_chat(mods["input"], mods["output"]), "models.dev"
    return None, "unknown"


def _identity_rows(pinned: dict, members: list[dict], by_id: dict[str, dict]) -> list[dict[str, Any]]:
    """Member rows in identity precedence: the pinned version, GA paid members, then preview / ``:free`` rows.

    Attributes such as open weights must come from the version we run, not from
    a merged preview build or a stale ``:free`` copy.
    """
    ordered = sorted(
        members,
        key=lambda m: (
            m["model_id"] != pinned["model_id"],
            m["prerelease"],
            m["variant"] is not None or m["is_free"],
            m["kind"] != "openrouter",
            m["model_id"],
        ),
    )
    return [by_id[m["model_id"]] for m in ordered]


def _open_weights(canon: str, rows: list[dict[str, Any]], config: FrameConfig) -> tuple[bool | None, str | None]:
    """Open weights of the canonical model; ``rows`` in :func:`_identity_rows` order.

    A curated ``open_weights`` entry in ``mss_frame.yaml`` wins (it records a
    checked first-party source). Otherwise the first row, in identity order,
    that carries any record decides: its models.dev record, its catalog twin's
    provider record, then its first-party list-price record. A preview or
    ``:free`` row is only consulted when no GA row says anything.
    """
    if canon in config.open_weights:
        return config.open_weights[canon], "mss_frame.yaml:open_weights"
    for row in rows:
        value = _models_dev(row).get("open_weights")
        if isinstance(value, bool):
            return value, f"models.dev:{row['model_id']}"
        value = _twin_record(row).get("open_weights")
        if isinstance(value, bool):
            return value, f"catalog_twin.provider_record:{row['model_id']}"
        value = _first_party(row).get("open_weights")
        if isinstance(value, bool):
            return value, f"list_price.first_party:{row['model_id']}"
    return None, None


def _lmarena(
    anchor: dict,
    members: list[dict],
    rows: dict[str, dict],
    pinned: dict | None = None,
) -> tuple[dict | None, str | None]:
    """The LMArena record of the configuration we call.

    The pinned version first (its arena entry is the version we run), then the
    anchor row, then the other GA members; a prerelease build only when the
    model has nothing else (a preview admitted with ``--include-previews``).
    """
    only_prerelease = all(m["prerelease"] for m in members)
    head = [m for m in (pinned, anchor) if m is not None]
    ordered = []
    for m in head + sorted(members, key=lambda m: (m["kind"] != "openrouter", m["model_id"])):
        if (m["prerelease"] and not only_prerelease) or any(m is o for o in ordered):
            continue
        ordered.append(m)
    for m in ordered:
        record = (rows.get(m["model_id"]) or {}).get("metadata", {}).get("lmarena")
        if isinstance(record, dict) and isinstance((record.get("categories") or {}).get("webdev"), dict):
            return record, m["model_id"]
    for m in ordered:
        record = (rows.get(m["model_id"]) or {}).get("metadata", {}).get("lmarena")
        if isinstance(record, dict):
            return record, m["model_id"]
    return None, None


def _entry(
    canon: str,
    members: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    as_of: dt.date,
    config: FrameConfig,
    *,
    include_previews: bool,
) -> dict[str, Any]:
    vendor = vendor_of(canon)
    # A curated include: entry admits this prerelease model as if --include-previews were given.
    include_previews = include_previews or canon in config.include
    rows = [by_id[m["model_id"]] for m in members]
    anchor = _anchor(canon, members)
    anchor_row = by_id[anchor["model_id"]]
    pinned = _pinned(members, include_previews)
    release_date, release_source = _release_date(pinned, by_id, members)
    usable = [m for m in members if include_previews or not m["prerelease"]]

    routes: dict[str, dict[str, Any]] = {}
    for m in sorted(members, key=lambda m: (m["prerelease"], m["variant"] is not None, m["model_id"])):
        kind = m["kind"]
        if kind not in ("openrouter", "go", "anthropic"):
            continue
        row = by_id[m["model_id"]]
        paid = (
            m["available"]
            and not m["is_free"]
            and (row["input_per_m"] > 0 or row["output_per_m"] > 0 or kind != "openrouter")
        )
        usable_member = include_previews or not m["prerelease"]
        current = routes.get(kind)
        better = current is None or (paid and usable_member and not current["paid"])
        if better:
            routes[kind] = {
                "model_id": m["model_id"],
                "available": m["available"],
                "paid": bool(paid and usable_member),
                "prerelease": m["prerelease"],
                "served_name": m["served_name"],
                "permaslug": m["permaslug"],
                "identity": "assumed (catalog twin)" if kind != "openrouter" else "catalog",
            }

    tools = any(
        "tools" in (r["metadata"].get("openrouter_supported_parameters") or [])
        or _models_dev(r).get("tool_call") is True
        or _twin_record(r).get("tool_call") is True
        or _first_party(r).get("tool_call") is True
        or (r["endpoint"] is not None and r["supports_tools"])
        for r in rows
    )
    context = max(
        [r["context_window"] for r in rows]
        + [int((_models_dev(r).get("limit") or {}).get("context") or 0) for r in rows]
        + [int((_twin_record(r).get("limit") or {}).get("context") or 0) for r in rows]
        + [int(_first_party(r).get("context") or 0) for r in rows],
    )
    identity_rows = _identity_rows(pinned, members, by_id)
    text_io, modality_source = _modalities(identity_rows)
    open_weights, open_source = _open_weights(canon, identity_rows, config)
    lmarena, lmarena_from = _lmarena(anchor, members, by_id, pinned)
    pinned_row = by_id.get(pinned["model_id"]) or {}
    expiration = _as_date(pinned_row.get("payload", {}).get("expiration_date"))
    status = _models_dev(pinned_row).get("status")
    created = sorted(m["openrouter_created"] for m in members if m["kind"] == "openrouter" and m["openrouter_created"])
    hf = anchor_row["metadata"].get("huggingface")

    return {
        "canonical_id": canon,
        "anchor_model_id": anchor["model_id"],
        "display_name": anchor_row.get("model_name") or canon,
        "vendor": vendor,
        "origin": config.origin(vendor),
        "line_key": line_key(canon, config.lines),
        "members": members,
        "routes": routes,
        "pinned_version": {
            "model_id": pinned["model_id"],
            "permaslug": pinned["permaslug"],
            "served_name": pinned["served_name"],
        },
        "release_date": release_date,
        "release_source": release_source,
        "first_openrouter_created": created[0] if created else None,
        "open_weights": open_weights,
        "open_weights_source": open_source,
        "context_window": context,
        "tools": tools,
        "text_io": text_io,
        "modality_source": modality_source,
        "prerelease": all(m["prerelease"] for m in members),
        "has_paid_route": any(r["paid"] for r in routes.values()),
        "any_route_available": any(m["available"] for m in usable or members if m["kind"] in ROUTE_KINDS),
        "deprecated_status": status,
        "expiration_date": expiration.isoformat() if expiration else None,
        "inputs": {
            "list_price": anchor_row["metadata"].get("list_price"),
            "popularity": (anchor_row["metadata"].get("popularity") or {}).get("openrouter"),
            "lmarena": lmarena,
            "lmarena_from": lmarena_from,
            "hf_downloads": hf.get("downloads") if isinstance(hf, dict) else None,
        },
        "eligible": False,
        "exclusion": None,
    }


ROUTE_KINDS = ("openrouter", "go", "anthropic")


def has_evidence(entry: dict[str, Any]) -> bool:
    """The inputs E8 requires are present: a list price, and popularity or LMArena interactions (proxy)."""
    inputs = entry.get("inputs") or {}
    price = inputs.get("list_price")
    if not (isinstance(price, dict) and price.get("input") is not None and price.get("output") is not None):
        return False
    popularity = inputs.get("popularity")
    if isinstance(popularity, dict) and popularity.get("daily"):
        return True
    cats = (inputs.get("lmarena") or {}).get("categories") if isinstance(inputs.get("lmarena"), dict) else None
    return isinstance(cats, dict) and any(
        isinstance((cats.get(name) or {}).get(key), (int, float)) and (cats.get(name) or {}).get(key)
        for name, key in (("webdev", "vote_count"), ("text_coding", "vote_count"), ("agent", "session_count"))
    )


# ── Filters ────────────────────────────────────────────────────────────────


def _e1_route(e: dict) -> tuple[str, str] | None:
    if not any(r["available"] for r in e["routes"].values()):
        return "no_route", "no available OpenRouter, OpenCode Go or Anthropic row"
    return None


def _e2_tools(e: dict) -> tuple[str, str] | None:
    return None if e["tools"] else ("no_tool_calling", "no member reports tool calling")


def _e3_context(e: dict) -> tuple[str, str] | None:
    if e["context_window"] < MIN_CONTEXT_TOKENS:
        return "context_too_small", f"context {e['context_window']} < {MIN_CONTEXT_TOKENS}"
    return None


def _e4_modality(e: dict) -> tuple[str, str] | None:
    if e["text_io"] is False:
        return "not_text_chat", f"no text input/output ({e['modality_source']})"
    return None


def _e5_ga(e: dict, as_of: dt.date, config: FrameConfig, *, include_previews: bool) -> tuple[str, str] | None:
    canon = e["canonical_id"]
    if canon in config.exclude:
        return "curated_exclude", config.exclude[canon]
    if e["prerelease"] and not include_previews and canon not in config.include:
        return "prerelease", "preview / experimental / contributor build (use --include-previews or include:)"
    if not e["has_paid_route"]:
        return "free_only", "no paid route (free tiers are rate-limited and promotional)"
    if e["deprecated_status"] == "deprecated":
        return "deprecated", "models.dev status deprecated"
    expiration = _as_date(e["expiration_date"])
    if expiration and (expiration - as_of).days <= DEPRECATION_HORIZON_DAYS:
        return "deprecated", f"OpenRouter expiration_date {expiration.isoformat()}"
    return None


def _e6_recency(e: dict, as_of: dt.date) -> tuple[str, str] | None:
    released = _as_date(e["release_date"])
    if released is None:
        return "no_release_date", "no release date for the pinned version"
    if (as_of - released).days > RECENCY_MAX_DAYS:
        return "too_old", f"released {released.isoformat()}"
    return None


def canonical_of(model_id: str, rows: list[dict[str, Any]] | None = None, config: FrameConfig | None = None) -> str:
    """Canonical id of any model id (local rows through their twin); used by insights and the aggregator."""
    config = config or load_frame_config()
    if model_id.startswith("local-"):
        row = next((r for r in rows or [] if r["model_id"] == model_id), None)
        target = _twin_target(row, config) if row else config.twins.get(model_id)
        return canonical_id(target) if target else model_id
    return canonical_id(model_id)
