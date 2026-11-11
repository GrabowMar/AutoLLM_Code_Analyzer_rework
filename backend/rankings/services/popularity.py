"""Popularity input of MSS v2: OpenRouter Data API ``rankings-daily`` (spec §2.1).

``compute_openrouter_popularity`` turns the daily top-50 rows into one record
per canonical model:

* permaslugs are mapped to catalog ids through ``/models[].canonical_slug``
  (else the ``-YYYYMMDD`` suffix is stripped) and then to the canonical model
  (K2-K4), so every dated version and merged prerelease build is summed;
* ``:free`` rows stay in the day's market total but never in a model's own
  numerator (the study population is paid use);
* exposure: only days on or after the model's first OpenRouter ``created``
  date count, so a model released mid-window is not penalised;
* a day the model is off the published list is left-censored: the record
  keeps ``tokens = None`` and that day's detection limit (the smallest listed
  model), and the score imputes ``CENSOR_FACTOR x limit``.

Value share needs prices, so it is computed at scoring time (:mod:`mss`) from
the stored daily series and the market block (every listed model per day).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from backend.rankings.services.constants import CENSOR_FACTOR
from backend.rankings.services.frame import canonical_id
from backend.rankings.services.frame import ga_sibling
from backend.rankings.services.frame import is_prerelease_id
from backend.rankings.services.frame import split_variant
from backend.rankings.services.openrouter_api import CITATION
from backend.rankings.services.openrouter_api import LICENSE

SOURCE_NAME = "OpenRouter Data API datasets/rankings-daily"
OTHER_ROW = "other"


def payload_sha256(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def permaslug_map(models_index: list[dict[str, Any]]) -> dict[str, str]:
    """``{canonical_slug: id}`` from the trimmed ``/models`` index."""
    out: dict[str, str] = {}
    for row in models_index:
        slug, model_id = row.get("canonical_slug"), row.get("id")
        if slug and model_id and ":" not in model_id:
            out.setdefault(str(slug), str(model_id))
    return out


def frame_resolver(candidates: list[dict[str, Any]]) -> Callable[[str], str | None]:
    """Catalog id -> canonical model of the frame (members' merges included, K4 siblings followed)."""
    member_to_canon: dict[str, str] = {}
    known = set()
    for entry in candidates:
        known.add(entry["canonical_id"])
        for member in entry["members"]:
            member_to_canon[canonical_id(member["model_id"])] = entry["canonical_id"]

    def resolve(model_id: str) -> str | None:
        canon = canonical_id(model_id)
        if canon in member_to_canon:
            return member_to_canon[canon]
        if canon in known:
            return canon
        if is_prerelease_id(canon) and ga_sibling(canon) in known:
            return ga_sibling(canon)
        return canon

    return resolve


def resolve_permaslug(permaslug: str, slug_to_id: dict[str, str], resolve: Callable[[str], str | None]) -> str:
    base, _ = split_variant(permaslug)
    model_id = slug_to_id.get(base) or base
    return resolve(model_id) or canonical_id(model_id)


def parse_daily_rows(
    chunks: list[dict[str, Any]],
    *,
    slug_to_id: dict[str, str],
    resolve: Callable[[str], str | None],
) -> tuple[dict[str, dict[str, Any]], dict[str, set[str]], str | None]:
    """``({date: {"total", "limit", "listed": {canon: tokens}}}, {canon: {permaslugs}}, as_of)``."""
    days: dict[str, dict[str, Any]] = {}
    permaslugs: dict[str, set[str]] = defaultdict(set)
    as_of = None
    for chunk in chunks:
        body = chunk.get("body") or {}
        meta = body.get("meta") or {}
        as_of = max(filter(None, [as_of, meta.get("as_of")]), default=None)
        for row in body.get("data") or []:
            date = str(row.get("date") or "")[:10]
            slug = str(row.get("model_permaslug") or "")
            try:
                tokens = int(row.get("total_tokens") or 0)
            except (TypeError, ValueError):
                continue
            if not date or not slug:
                continue
            day = days.setdefault(date, {"total": 0, "limit": None, "listed": {}})
            day["total"] += tokens
            if slug == OTHER_ROW:
                continue
            day["limit"] = tokens if day["limit"] is None else min(day["limit"], tokens)
            _, variant = split_variant(slug)
            if variant == "free":
                continue  # in the market total, never in a model's numerator
            canon = resolve_permaslug(slug, slug_to_id, resolve)
            day["listed"][canon] = day["listed"].get(canon, 0) + tokens
            permaslugs[canon].add(slug)
    return days, permaslugs, as_of


def _daterange(start: dt.date, end: dt.date) -> list[str]:
    return [(start + dt.timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def compute_openrouter_popularity(
    chunks: list[dict[str, Any]],
    *,
    candidates: list[dict[str, Any]],
    models_index: list[dict[str, Any]],
    window: tuple[dt.date, dt.date],
    fetched_at: str,
    classifications: dict[str, Any] | None = None,
    source_snapshot_ids: list[int] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Per canonical model popularity records, plus the market block (every listed model per day).

    Records are produced for every candidate with an OpenRouter presence
    (a catalog member or a permaslug in the window); a model without one gets
    no record (the LMArena proxy applies at scoring time).
    """
    start, end = window
    resolve = frame_resolver(candidates)
    slug_to_id = permaslug_map(models_index)
    days, permaslugs, as_of = parse_daily_rows(chunks, slug_to_id=slug_to_id, resolve=resolve)

    created_by_canon: dict[str, dt.date] = {}
    for entry in candidates:
        if entry.get("first_openrouter_created"):
            created_by_canon[entry["canonical_id"]] = dt.date.fromisoformat(entry["first_openrouter_created"])
    for row in models_index:
        created = row.get("created")
        if not isinstance(created, (int, float)) or not row.get("id") or ":" in row["id"]:
            continue
        canon = resolve(row["id"]) or canonical_id(row["id"])
        day = dt.datetime.fromtimestamp(created, tz=dt.UTC).date()
        if canon not in created_by_canon or day < created_by_canon[canon]:
            created_by_canon[canon] = day

    code_tags = _code_tags(classifications, slug_to_id=slug_to_id, resolve=resolve)
    window_days = _daterange(start, end)
    citation = CITATION.format(as_of=as_of or fetched_at)
    records: dict[str, dict[str, Any]] = {}
    present = set(created_by_canon) | set(permaslugs)
    for canon in sorted(present):
        exposure_start = created_by_canon.get(canon)
        listed_days = [d for d in window_days if canon in (days.get(d) or {}).get("listed", {})]
        if exposure_start is None and listed_days:
            exposure_start = dt.date.fromisoformat(listed_days[0])
        available = [d for d in window_days if exposure_start is None or d >= exposure_start.isoformat()]
        daily = []
        for d in available:
            day = days.get(d)
            if day is None:
                continue  # the API returned nothing for this day: not observed, not censored
            tokens = day["listed"].get(canon)
            daily.append(
                {
                    "date": d,
                    "tokens": tokens,
                    "total": day["total"],
                    "limit": day["limit"],
                    "censored": tokens is None,
                },
            )
        censored = sum(1 for x in daily if x["censored"])
        volume = [
            ((x["tokens"] if x["tokens"] is not None else CENSOR_FACTOR * (x["limit"] or 0)) / x["total"])
            for x in daily
            if x["total"]
        ]
        records[canon] = {
            "source": SOURCE_NAME,
            "license": LICENSE,
            "citation": citation,
            "as_of": as_of,
            "fetched_at": fetched_at,
            "window": {"start": start.isoformat(), "end": end.isoformat(), "days": len(window_days)},
            "exposure_start": exposure_start.isoformat() if exposure_start else None,
            "permaslugs": sorted(permaslugs.get(canon, set())),
            "days_available": len(daily),
            "days_listed": len(daily) - censored,
            "days_censored": censored,
            "tokens_listed": sum(x["tokens"] or 0 for x in daily),
            "daily": daily,
            "volume_share": (sum(volume) / len(volume)) if volume else None,
            "censor_factor": CENSOR_FACTOR,
            "short_exposure": len(daily) < 7,
            "code_tags": code_tags.get(canon, {}),
            "source_snapshot_ids": source_snapshot_ids or [],
        }
    market = {
        "source": SOURCE_NAME,
        "license": LICENSE,
        "citation": citation,
        "as_of": as_of,
        "fetched_at": fetched_at,
        "window": {"start": start.isoformat(), "end": end.isoformat(), "days": len(window_days)},
        "days": {d: days[d] for d in window_days if d in days},
    }
    return records, market


def _code_tags(
    classifications: dict[str, Any] | None,
    *,
    slug_to_id: dict[str, str],
    resolve: Callable[[str], str | None],
) -> dict[str, dict[str, float]]:
    """``{canon: {"code:<tag>": tag_token_share}}`` from the top-10 lists of the ``code:*`` tags."""
    data = ((classifications or {}).get("body") or {}).get("data") or {}
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for tag in data.get("classifications") or []:
        name = str(tag.get("tag") or "")
        if not name.startswith("code:"):
            continue
        for model in tag.get("models") or []:
            slug = str(model.get("id") or "")
            share = model.get("tag_token_share")
            if not slug or not isinstance(share, (int, float)):
                continue
            canon = resolve_permaslug(slug, slug_to_id, resolve)
            out[canon][name] = round(out[canon].get(name, 0.0) + float(share), 6)
    return out
