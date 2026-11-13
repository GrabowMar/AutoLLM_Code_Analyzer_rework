"""External model covariates: Epoch AI model data and LMArena leaderboards.

Two public, keyless, CC-BY 4.0 sources, joined to our models through the
hand-curated name map ``backend/llm_models/data/external_model_map.yaml``
(exact names only — nothing is fuzzy-matched at import time):

* **Epoch AI "All AI models"** (``https://epoch.ai/data/all_ai_models.csv``) —
  reported parameters (with the free-text notes they come from), training
  compute, training dataset size, accessibility and Epoch's confidence rating.
  Stored in ``metadata["epoch"]``.
* **LMArena leaderboards** (Hugging Face dataset
  ``lmarena-ai/leaderboard-dataset``, split ``latest``, read through the
  datasets-server API) — the webdev arena (overall, HTML, React), the text
  arena (overall, coding) and the agent arena. Stored in
  ``metadata["lmarena"]["categories"]``.

Both are stored with their attribution, license, fetch time and the curator's
match note next to every value. MSS v2 (``backend/rankings``, docs/design/MSS_V2.md)
reads the LMArena WebDev rating as its capability component and the arena
interaction counts as a popularity fallback / validity check; Epoch stays a
covariate.
The catalog sync keeps both keys (``enrichment.ENRICHMENT_KEYS``), and the
local endpoint inventory merges ``metadata``, so neither re-sync drops them.
"""

from __future__ import annotations

import csv
import difflib
import io
import logging
import re
import time
from dataclasses import dataclass
from dataclasses import field
from http import HTTPStatus
from pathlib import Path
from typing import Any

import requests
import yaml
from django.db import transaction
from django.utils import timezone

from backend.llm_models.models import LLMModel

logger = logging.getLogger(__name__)

MAP_PATH = Path(__file__).resolve().parent.parent / "data" / "external_model_map.yaml"

EPOCH_CSV_URL = "https://epoch.ai/data/all_ai_models.csv"
EPOCH_PAGE_URL = "https://epoch.ai/data/ai-models"
EPOCH_LICENSE = "CC-BY 4.0 (Epoch AI)"

LMARENA_DATASET = "lmarena-ai/leaderboard-dataset"
LMARENA_DATASET_URL = f"https://huggingface.co/datasets/{LMARENA_DATASET}"
LMARENA_ROWS_URL = "https://datasets-server.huggingface.co/rows"
LMARENA_STATS_URL = "https://datasets-server.huggingface.co/statistics"
LMARENA_PARQUET_URL = "https://huggingface.co/datasets/{dataset}/resolve/main/{config}/{split}-00000-of-00001.parquet"
LMARENA_SPLIT = "latest"
LMARENA_LICENSE = "CC-BY 4.0 (LMArena)"
LMARENA_PAGE_SIZE = 100

SOURCES = ("epoch", "lmarena")
ARENA_CONFIGS = ("webdev", "text", "agent")
MATCH_KINDS = ("exact", "variant", "assumed")

# (dataset config, arena category) -> key in metadata.lmarena.categories.
ARENA_CATEGORIES: dict[tuple[str, str], str] = {
    ("webdev", "webdev"): "webdev",
    ("webdev", "webdev-html"): "webdev_html",
    ("webdev", "webdev-react"): "webdev_react",
    ("text", "overall"): "text_overall",
    ("text", "coding"): "text_coding",
    ("agent", "overall"): "agent",
}
# The text config holds ~11k rows over 29 categories; only these are kept (paging stops once
# the dataset statistics say every row of them has been seen).
TEXT_CATEGORIES = ("overall", "coding")

REQUEST_TIMEOUT = 60
REQUEST_ATTEMPTS = 6  # the HF datasets-server answers bursts of 502/503
RETRY_PAUSE_SECONDS = 5.0
# The datasets-server rate-limits anonymous callers (429); wait it out instead of aborting the import.
RATE_LIMIT_PAUSE_SECONDS = 15.0
RATE_LIMIT_MAX_PAUSE_SECONDS = 60.0
USER_AGENT = "llm-lab-covariates/1 (+research; CC-BY attribution kept)"

NOTE_MAX_CHARS = 2000

_UNIT = {"t": 1e12, "trillion": 1e12, "b": 1e9, "billion": 1e9, "m": 1e6, "million": 1e6}
_NUM = r"(\d+(?:\.\d+)?)\s*(trillion|billion|million|T|B|M)\b"
_ACTIVE_WORD = r"(?:active|activated|activation)"
# A label such as "Activated Parameters: 32B" right after a number belongs to the next value,
# so "Total Parameters: 456B Activated Parameters: 45.9B" must not read 456B as active.
_NOT_A_LABEL = r"(?!\s*(?:parameters?|params)(?:\s+per\s+token)?\s*[:\t])"
# Number first: 18B active / 32B parameters active / 104 billion active parameters / 3B activated
_ACTIVE_AFTER = re.compile(
    _NUM + r"\s*(?:total\s+)?(?:parameters?|params)?\s*(?:are\s+)?" + _ACTIVE_WORD + _NOT_A_LABEL,
    re.IGNORECASE,
)
# Label first: Activated Parameters per Token: 45.9B / active parameters of 21B
_ACTIVE_BEFORE = re.compile(
    _ACTIVE_WORD + r"\s*(?:parameters?|params)?\s*(?:per\s+token)?\s*(?:[:=\t ]|of)\s*" + _NUM,
    re.IGNORECASE,
)
# Verb form: activates 14 billion parameters / activating only 12B parameters
_ACTIVATES = re.compile(
    r"activat(?:es|ing)\s+(?:only\s+)?(?:about\s+|approximately\s+|around\s+|~\s*)?" + _NUM,
    re.IGNORECASE,
)


def _now_iso() -> str:
    return timezone.now().isoformat()


# ── Name map ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MapEntry:
    """One curated row of the name map."""

    model_id: str
    epoch: str | None
    lmarena: dict[str, str]
    match: str
    note: str
    twin: str | None = None

    @property
    def mapped(self) -> bool:
        return bool(self.epoch or self.lmarena)


def _clean_name(value: Any, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        msg = f"{where}: expected a string or null, got {type(value).__name__}"
        raise ValueError(msg)  # noqa: TRY004 — every map error is a ValueError for the caller
    return value.strip() or None


def parse_model_map(raw: Any) -> dict[str, MapEntry]:
    """Validated map entries from the parsed YAML document (``ValueError`` on a malformed map)."""
    models = raw.get("models") if isinstance(raw, dict) else None
    if not isinstance(models, dict):
        msg = "external model map: top-level 'models' mapping is missing"
        raise ValueError(msg)  # noqa: TRY004 — a config error, reported as ValueError everywhere
    allowed = {"epoch", "lmarena", "match", "note", "twin"}
    entries: dict[str, MapEntry] = {}
    for model_id, spec in models.items():
        where = f"external model map [{model_id}]"
        if not isinstance(spec, dict):
            msg = f"{where}: expected a mapping"
            raise ValueError(msg)  # noqa: TRY004
        unknown = set(spec) - allowed
        if unknown:
            msg = f"{where}: unknown keys {sorted(unknown)}"
            raise ValueError(msg)
        arena_raw = spec.get("lmarena")
        if arena_raw is not None and not isinstance(arena_raw, dict):
            msg = f"{where}: 'lmarena' must be a mapping of {list(ARENA_CONFIGS)} or null"
            raise ValueError(msg)
        arena: dict[str, str] = {}
        for config, name in (arena_raw or {}).items():
            if config not in ARENA_CONFIGS:
                msg = f"{where}: unknown arena config {config!r} (use {list(ARENA_CONFIGS)})"
                raise ValueError(msg)
            cleaned = _clean_name(name, f"{where}.lmarena.{config}")
            if cleaned:
                arena[config] = cleaned
        # No default: a forgotten ``match`` must not silently claim "exact".
        match = str(spec.get("match") or "")
        if match not in MATCH_KINDS:
            msg = f"{where}: match is required and must be one of {list(MATCH_KINDS)}"
            raise ValueError(msg)
        entries[str(model_id)] = MapEntry(
            model_id=str(model_id),
            epoch=_clean_name(spec.get("epoch"), f"{where}.epoch"),
            lmarena=arena,
            match=match,
            note=" ".join(str(spec.get("note") or "").split()),
            twin=_clean_name(spec.get("twin"), f"{where}.twin"),
        )
    return entries


def load_model_map(path: Path | str | None = None) -> dict[str, MapEntry]:
    with Path(path or MAP_PATH).open(encoding="utf-8") as fh:
        return parse_model_map(yaml.safe_load(fh))


# ── HTTP ────────────────────────────────────────────────────────────────────


def _retry_after(resp: requests.Response) -> float | None:
    try:
        return max(0.0, min(float(resp.headers.get("Retry-After", "")), RATE_LIMIT_MAX_PAUSE_SECONDS))
    except ValueError:
        return None


def _http_get(url: str, *, params: dict | None = None) -> requests.Response:
    """GET with a few retries (transient 5xx and 429 rate limits); raises on final failure."""
    last: Exception | None = None
    for attempt in range(REQUEST_ATTEMPTS):
        pause = RETRY_PAUSE_SECONDS * (attempt + 1)
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
            if resp.status_code < 500 and resp.status_code != HTTPStatus.TOO_MANY_REQUESTS:
                resp.raise_for_status()
                return resp
            last = requests.HTTPError(f"{resp.status_code} from {url}")
            if resp.status_code == HTTPStatus.TOO_MANY_REQUESTS:
                pause = _retry_after(resp) or RATE_LIMIT_PAUSE_SECONDS * (attempt + 1)
        except requests.RequestException as exc:
            last = exc
        if attempt + 1 < REQUEST_ATTEMPTS:
            time.sleep(pause)
    raise last or requests.RequestException(url)


# ── Epoch AI ────────────────────────────────────────────────────────────────


def fetch_epoch_csv(url: str = EPOCH_CSV_URL) -> str:
    resp = _http_get(url)
    resp.encoding = "utf-8"
    return resp.text


def parse_epoch_csv(text: str) -> dict[str, dict[str, str]]:
    """Epoch rows keyed by their exact ``Model`` name (a name listed twice is dropped as ambiguous)."""
    rows: dict[str, dict[str, str]] = {}
    duplicates: set[str] = set()
    for row in csv.DictReader(io.StringIO(text)):
        name = (row.get("Model") or "").strip()
        if not name:
            continue
        if name in rows:
            duplicates.add(name)
        rows[name] = row
    for name in duplicates:
        logger.warning("Epoch CSV lists %r more than once; it is not matched", name)
        rows.pop(name, None)
    return rows


def _float(value: Any) -> float | None:
    try:
        text = str(value).strip().replace(",", "")
        return float(text) if text else None
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    number = _float(value)
    return round(number) if number is not None else None


def _text(value: Any, limit: int = NOTE_MAX_CHARS) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _first_url(value: Any) -> str | None:
    match = re.search(r"https?://\S+", str(value or ""))
    return match.group(0).rstrip(".,;)") if match else None


def parse_active_params(note: str | None) -> int | None:
    """Active parameter count stated in an Epoch ``Parameters notes`` text, only when unambiguous.

    Every "<n>B active" / "activated parameters: <n>B" phrase is read; one
    distinct value is returned, none or several give ``None`` (the raw note is
    always stored next to it, so a reader can check the parse).
    """
    if not note:
        return None
    values: set[int] = set()
    for pattern in (_ACTIVE_AFTER, _ACTIVE_BEFORE, _ACTIVATES):
        for number, unit in pattern.findall(note):
            values.add(round(float(number) * _UNIT[unit.lower()]))
    return values.pop() if len(values) == 1 else None


def normalize_epoch_row(row: dict[str, str], entry: MapEntry, *, fetched_at: str) -> dict[str, Any]:
    """The ``metadata["epoch"]`` record for one Epoch row."""
    notes = _text(row.get("Parameters notes"))
    active = parse_active_params(notes)
    return {
        "model": (row.get("Model") or "").strip(),
        "organization": _text(row.get("Organization"), 300),
        "publication_date": _text(row.get("Publication date"), 40),
        "parameters": _int(row.get("Parameters")),
        "parameters_notes": notes,
        "active_params_parsed": active,
        "active_params_source": "parsed from Epoch 'Parameters notes'" if active is not None else None,
        "training_compute_flop": _float(row.get("Training compute (FLOP)")),
        "training_compute_notes": _text(row.get("Training compute notes")),
        "training_dataset_size": _float(row.get("Training dataset size (total)")),
        "training_dataset_size_unit": "datapoints (tokens for language models), as reported by Epoch",
        "dataset_size_notes": _text(row.get("Dataset size notes")),
        "model_accessibility": _text(row.get("Model accessibility"), 200),
        "open_model_weights": _text(row.get("Open model weights?"), 40),
        "confidence": _text(row.get("Confidence"), 40),
        "domain": _text(row.get("Domain"), 200),
        "reference": _text(row.get("Reference"), 500),
        "link": _first_url(row.get("Link")),
        "epoch_last_modified": _text(row.get("Last modified"), 40),
        "source": "Epoch AI, 'All AI models' dataset",
        "source_url": EPOCH_CSV_URL,
        "source_page": EPOCH_PAGE_URL,
        "license": EPOCH_LICENSE,
        "fetched_at": fetched_at,
        "match": entry.match,
        "mapping_note": entry.note or None,
    }


# ── LMArena ─────────────────────────────────────────────────────────────────


def _arena_page(url: str, params: dict) -> dict:
    data = _http_get(url, params=params).json()
    if not isinstance(data, dict) or "rows" not in data:
        msg = f"unexpected datasets-server answer from {url}: {str(data)[:200]}"
        raise ValueError(msg)
    return data


def _category_counts(base: dict) -> dict[str, int]:
    """Rows per ``category`` from the datasets-server statistics (``{}`` when unavailable)."""
    try:
        data = _http_get(LMARENA_STATS_URL, params=base).json()
    except (requests.RequestException, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("partial"):
        # Partial statistics count only a prefix of the split; stopping on them would drop rows.
        return {}
    for column in data.get("statistics") or []:
        if isinstance(column, dict) and column.get("column_name") == "category":
            freq = ((column.get("column_statistics") or {}).get("frequencies")) or {}
            return {str(k): int(v) for k, v in freq.items() if isinstance(v, int)}
    return {}


def _arena_paged(base: dict, *, categories: tuple[str, ...] | None = None) -> list[dict]:
    """Every row of one config (``categories``: only those, stopping once all of them are complete)."""
    expected = {c: n for c, n in _category_counts(base).items() if c in categories} if categories else {}
    rows: list[dict] = []
    seen: dict[str, int] = {}
    offset = 0
    while True:
        data = _arena_page(LMARENA_ROWS_URL, {**base, "offset": offset, "length": LMARENA_PAGE_SIZE})
        page = [r.get("row") for r in data.get("rows") or [] if isinstance(r, dict) and isinstance(r.get("row"), dict)]
        for row in page:
            category = str(row.get("category") or "")
            if categories and category not in categories:
                continue
            rows.append(row)
            seen[category] = seen.get(category, 0) + 1
        offset += LMARENA_PAGE_SIZE
        total = data.get("num_rows_total")
        if not page or (isinstance(total, int) and offset >= total):
            return rows
        if categories and len(expected) == len(categories) and all(seen.get(c, 0) >= n for c, n in expected.items()):
            return rows


def _jsonable(value: Any) -> Any:
    """A parquet cell as the datasets-server would send it (dates and timestamps become ISO strings)."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _arena_parquet(config: str, *, categories: tuple[str, ...] | None = None) -> list[dict]:
    """The same rows read from the repo's ``<config>/latest-*.parquet`` (fallback when the datasets-server fails).

    Needs ``pyarrow``, which is not a project dependency: install it on demand outside the venv (e.g.
    ``uv pip install --target <dir> pyarrow`` plus ``PYTHONPATH=<dir>``). Without it the caller's error stands.
    """
    import pyarrow.parquet as pq

    url = LMARENA_PARQUET_URL.format(dataset=LMARENA_DATASET, config=config, split=LMARENA_SPLIT)
    table = pq.read_table(io.BytesIO(_http_get(url).content))
    rows = [{key: _jsonable(value) for key, value in row.items()} for row in table.to_pylist()]
    if categories:
        rows = [row for row in rows if str(row.get("category") or "") in categories]
    return rows


def fetch_lmarena(configs: tuple[str, ...] = ARENA_CONFIGS) -> dict[str, list[dict]]:
    """``{config: [row, …]}`` of the ``latest`` split (text: only the overall and coding categories).

    The datasets-server ``/rows`` API is tried first; when it keeps failing (it answers bursts of 502/503),
    the split's parquet file in the dataset repo is read instead (same rows, see :func:`_arena_parquet`).
    """
    out: dict[str, list[dict]] = {}
    for config in configs:
        categories = TEXT_CATEGORIES if config == "text" else None
        base = {"dataset": LMARENA_DATASET, "config": config, "split": LMARENA_SPLIT}
        try:
            out[config] = _arena_paged(base, categories=categories)
        except requests.RequestException as exc:
            try:
                out[config] = _arena_parquet(config, categories=categories)
            except ImportError:
                raise exc from None
            logger.warning("LMArena %s: datasets-server failed (%s); read the parquet split instead", config, exc)
    return out


def index_lmarena(rows_by_config: dict[str, list[dict]]) -> dict[str, dict[str, Any]]:
    """``{category key: {"rows": {model_name: row}, "ambiguous", "n_ranked", "min", "max", …}}``.

    ``n_ranked`` counts the category's rows and ``min``/``max`` its rating (or
    agent score) range, so a reader can place one model on the whole
    leaderboard. A name listed twice in one category is ambiguous and never
    matched.
    """
    index: dict[str, dict[str, Any]] = {}
    for config, rows in rows_by_config.items():
        for row in rows:
            key = ARENA_CATEGORIES.get((config, str(row.get("category") or "")))
            name = str(row.get("model_name") or "").strip()
            if key is None or not name:
                continue
            bucket = index.setdefault(
                key,
                {"config": config, "rows": {}, "ambiguous": set(), "count": 0, "values": [], "dates": set()},
            )
            bucket["count"] += 1
            if name in bucket["rows"] or name in bucket["ambiguous"]:
                bucket["rows"].pop(name, None)
                bucket["ambiguous"].add(name)
            else:
                bucket["rows"][name] = row
            value = _float(row.get("score" if config == "agent" else "rating"))
            if value is not None:
                bucket["values"].append(value)
            if row.get("leaderboard_publish_date"):
                bucket["dates"].add(str(row["leaderboard_publish_date"]))
    for bucket in index.values():
        values = bucket.pop("values")
        bucket["n_ranked"] = bucket.pop("count")
        bucket["min"] = min(values) if values else None
        bucket["max"] = max(values) if values else None
        bucket["publish_dates"] = sorted(bucket.pop("dates"))
    return index


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def normalize_arena_row(key: str, row: dict, bucket: dict[str, Any]) -> dict[str, Any]:
    """One ``metadata["lmarena"]["categories"][key]`` record."""
    common = {
        "arena_name": str(row.get("model_name") or "").strip(),
        "rank": _int(row.get("rank")),
        "n_ranked": bucket["n_ranked"],
        "publish_date": row.get("leaderboard_publish_date"),
        "organization": row.get("organization"),
        "arena_license": row.get("license"),
        "config": bucket["config"],
    }
    if bucket["config"] == "agent":
        return {
            **common,
            "metric": "score",
            "score": _round(_float(row.get("score")), 6),
            "score_lower": _round(_float(row.get("score_ci_lower")), 6),
            "score_upper": _round(_float(row.get("score_ci_upper")), 6),
            "observation_count": _int(row.get("observation_count")),
            "session_count": _int(row.get("session_count")),
            "category_min": _round(bucket["min"], 6),
            "category_max": _round(bucket["max"], 6),
        }
    return {
        **common,
        "metric": "rating",
        "rating": _round(_float(row.get("rating")), 2),
        "rating_lower": _round(_float(row.get("rating_lower")), 2),
        "rating_upper": _round(_float(row.get("rating_upper")), 2),
        "vote_count": _int(row.get("vote_count")),
        "category_min": _round(bucket["min"], 2),
        "category_max": _round(bucket["max"], 2),
    }


def lmarena_record(
    entry: MapEntry,
    index: dict[str, dict[str, Any]],
    *,
    fetched_at: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    """The ``metadata["lmarena"]`` record for one map entry and the arena names not found."""
    categories: dict[str, dict] = {}
    missing: list[str] = []
    for config, name in entry.lmarena.items():
        keys = [key for (cfg, _cat), key in ARENA_CATEGORIES.items() if cfg == config]
        found = False
        for key in keys:
            bucket = index.get(key)
            row = bucket["rows"].get(name) if bucket else None
            if row is not None:
                categories[key] = normalize_arena_row(key, row, bucket)
                found = True
        if not found and any(index.get(key) for key in keys):
            ambiguous = any(name in index[key]["ambiguous"] for key in keys if index.get(key))
            missing.append(f"{config}: {name!r}" + (" (listed twice: ambiguous)" if ambiguous else ""))
    if not categories:
        return None, missing
    dates = sorted({c["publish_date"] for c in categories.values() if c.get("publish_date")})
    return {
        "categories": categories,
        "names": dict(entry.lmarena),
        "publish_date": dates[-1] if dates else None,
        "dataset": LMARENA_DATASET,
        "split": LMARENA_SPLIT,
        "source": "LMArena leaderboard (Hugging Face dataset lmarena-ai/leaderboard-dataset)",
        "source_url": LMARENA_DATASET_URL,
        "license": LMARENA_LICENSE,
        "fetched_at": fetched_at,
        "match": entry.match,
        "mapping_note": entry.note or None,
    }, missing


# ── Import ──────────────────────────────────────────────────────────────────


@dataclass
class ImportReport:
    """What an import matched and changed (``rows`` per model, ``warnings`` for the curator)."""

    dry_run: bool
    sources: tuple[str, ...]
    rows: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def count(self, source: str, status: str) -> int:
        return sum(1 for r in self.rows if r.get(source) == status)


def used_model_ids() -> set[str]:
    """Model ids with at least one generation run (any user)."""
    return set(
        LLMModel.objects.filter(generation_runs__isnull=False).values_list("model_id", flat=True).distinct(),
    )


def frame_model_ids() -> set[str]:
    """Catalog rows of the MSS v2 frame models that pass the eligibility filters E1-E7.

    For each such canonical model: its anchor row and its pinned version (the
    rows the capability lookup reads first). Used by ``--frame`` so the
    "no map entry" warning covers the models the selection can pick, not only
    the ones that already ran.
    """
    from django.utils import timezone

    from backend.rankings.services.frame import build_frame
    from backend.rankings.services.frame import load_rows

    frame = build_frame(load_rows(), timezone.now().date())
    out: set[str] = set()
    for entry in frame["candidates"]:
        if entry["exclusion"] is None:
            out.add(entry["anchor_model_id"])
            out.add(entry["pinned_version"]["model_id"])
    return out


def _write(model_pk: int, changes: dict[str, Any]) -> None:
    """Set (or, for ``None``, remove) metadata keys under a row lock; other keys are untouched."""
    with transaction.atomic():
        model = LLMModel.objects.select_for_update().get(pk=model_pk)
        meta = dict(model.metadata or {})
        for key, value in changes.items():
            if value is None:
                meta.pop(key, None)
            else:
                meta[key] = value
        model.metadata = meta
        model.save(update_fields=["metadata", "updated_at"])


def _source_change(
    current: Any,
    new: dict | None,
    *,
    mapped: bool,
    same_mapping: bool = True,
) -> tuple[str, bool, dict | None]:
    """``(status, write?, value)`` for one source of one model.

    Unmapped (null in the map) removes a stored value; a mapped name the
    download lacks keeps the stored value (its ``fetched_at`` dates it) only
    when that value was matched under the same name(s) — a record matched
    under an earlier, since-corrected mapping is removed, never kept.
    """
    if not mapped:
        return ("unmapped", current is not None, None)
    if new is None:
        return ("missing_in_source", current is not None and not same_mapping, None)
    return ("matched", True, new)


def import_external_covariates(
    *,
    sources: tuple[str, ...] = SOURCES,
    model_ids: set[str] | None = None,
    dry_run: bool = False,
    entries: dict[str, MapEntry] | None = None,
    epoch_rows: dict[str, dict[str, str]] | None = None,
    arena_rows: dict[str, list[dict]] | None = None,
    fetched_at: str | None = None,
    coverage_ids: set[str] | None = None,
) -> ImportReport:
    """Copy the mapped Epoch / LMArena rows into ``metadata["epoch"]`` / ``metadata["lmarena"]``.

    ``epoch_rows`` / ``arena_rows`` are downloaded when not given (tests pass
    fixtures). A map entry set to null removes a stale value; a mapped name
    the download lacks keeps the stored one and is reported. ``coverage_ids``
    (default: models with generation runs) are the models whose missing map
    entry is warned about.
    """
    entries = entries if entries is not None else load_model_map()
    fetched_at = fetched_at or _now_iso()
    report = ImportReport(dry_run=dry_run, sources=tuple(sources))

    scope = {k: v for k, v in entries.items() if model_ids is None or k in model_ids}
    if model_ids:
        for missing_id in sorted(model_ids - set(entries)):
            report.warnings.append(f"{missing_id}: not in the name map (add it to external_model_map.yaml)")

    if "epoch" in sources and epoch_rows is None:
        epoch_rows = parse_epoch_csv(fetch_epoch_csv())
    arena_index: dict[str, dict[str, Any]] = {}
    if "lmarena" in sources:
        if arena_rows is None:
            arena_rows = fetch_lmarena()
        arena_index = index_lmarena(arena_rows)

    models = {m.model_id: m for m in LLMModel.objects.filter(model_id__in=list(scope))}
    twins = set(
        LLMModel.objects.filter(model_id__in=[e.twin for e in scope.values() if e.twin]).values_list(
            "model_id",
            flat=True,
        ),
    )

    for model_id, entry in sorted(scope.items()):
        row: dict[str, Any] = {"model_id": model_id, "match": entry.match}
        model = models.get(model_id)
        if model is None:
            row.update(dict.fromkeys(sources, "model_not_in_db"))
            report.rows.append(row)
            continue
        if entry.twin and entry.twin not in twins:
            report.warnings.append(f"{model_id}: twin {entry.twin!r} is not in the catalog")
        meta = model.metadata or {}
        changes: dict[str, Any] = {}

        if "epoch" in sources:
            source_row = (epoch_rows or {}).get(entry.epoch) if entry.epoch else None
            new = normalize_epoch_row(source_row, entry, fetched_at=fetched_at) if source_row else None
            current = meta.get("epoch")
            same = isinstance(current, dict) and current.get("model") == entry.epoch
            status, write, value = _source_change(current, new, mapped=bool(entry.epoch), same_mapping=same)
            row["epoch"] = status
            row["epoch_name"] = entry.epoch
            if status == "missing_in_source":
                report.warnings.append(f"{model_id}: Epoch has no model named {entry.epoch!r}")
                if write:
                    report.warnings.append(f"{model_id}: removed the Epoch record matched under an earlier mapping")
            if write:
                changes["epoch"] = value

        if "lmarena" in sources:
            new, missing = lmarena_record(entry, arena_index, fetched_at=fetched_at) if entry.lmarena else (None, [])
            current = meta.get("lmarena")
            same = isinstance(current, dict) and current.get("names") == entry.lmarena
            status, write, value = _source_change(current, new, mapped=bool(entry.lmarena), same_mapping=same)
            row["lmarena"] = status
            row["lmarena_categories"] = sorted((new or {}).get("categories", {}))
            report.warnings.extend(f"{model_id}: LMArena has no {m}" for m in missing)
            if status == "missing_in_source" and write:
                report.warnings.append(f"{model_id}: removed the LMArena record matched under an earlier mapping")
            if write:
                changes["lmarena"] = value

        row["changed"] = sorted(changes)
        report.rows.append(row)
        if changes and not dry_run:
            _write(model.pk, changes)

    if model_ids is None:
        if coverage_ids is None:
            uncovered = sorted(used_model_ids() - set(entries))
            report.warnings.extend(f"{mid}: has generation jobs but no entry in the name map" for mid in uncovered)
        else:
            uncovered = sorted(coverage_ids - set(entries))
            report.warnings.extend(f"{mid}: in the MSS v2 frame but no entry in the name map" for mid in uncovered)
    return report


# ── Curation helper ─────────────────────────────────────────────────────────


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "", text.lower())


def _queries(model: LLMModel) -> list[str]:
    tail = model.model_id.split("/", 1)[-1].split(":", 1)[0]
    queries = {tail, re.sub(r"-\d{8}$", "", tail), model.model_name, model.served_model_name}
    hf_id = str((model.capabilities_json or {}).get("hugging_face_id") or "")
    if hf_id:
        queries.add(hf_id.split("/", 1)[-1])
    return [q for q in (_norm(q) for q in queries if q) if q]


def suggest_matches(
    model: LLMModel,
    *,
    epoch_names: list[str],
    arena_names: dict[str, list[str]],
    limit: int = 5,
) -> dict[str, list[tuple[str, float]]]:
    """Closest external names for curating the map — printed for a human, never applied."""
    queries = _queries(model)

    def ranked(names: list[str]) -> list[tuple[str, float]]:
        scored = []
        for name in names:
            target = _norm(name)
            score = max((difflib.SequenceMatcher(None, q, target).ratio() for q in queries), default=0.0)
            scored.append((name, round(score, 3)))
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:limit]

    out = {"epoch": ranked(epoch_names)}
    for config, names in arena_names.items():
        out[f"lmarena.{config}"] = ranked(names)
    return out


def arena_names_by_config(arena_rows: dict[str, list[dict]]) -> dict[str, list[str]]:
    return {
        config: sorted({str(r.get("model_name") or "").strip() for r in rows if r.get("model_name")})
        for config, rows in arena_rows.items()
    }
