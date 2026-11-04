"""One row of research covariates per model, for export and model-level analyses.

Everything comes from what enrichment and the external import stored on the
model (``metadata``: models.dev, Hugging Face incl. architecture / recommended
sampling, derived ``params``, ``catalog_twin``, ``epoch``, ``lmarena``) plus
the caller's job counts. Unknown values are ``None`` (CSV: empty cell), never
0. A local endpoint model has no catalog metadata of its own, so its
model-intrinsic covariates (openness, license, parameters, architecture,
popularity, recommended sampling, external scores) fall back to its catalog
twin and the row lists those groups in ``inherited_fields``; operational
fields (context window, price) are never inherited because the serving route
differs.
"""

from __future__ import annotations

import logging
from datetime import UTC
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from django.db.models import Count
from django.db.models import Q
from django.utils import timezone

from backend.llm_models.models import LLMModel
from backend.llm_models.services.external_sources import EPOCH_LICENSE
from backend.llm_models.services.external_sources import EPOCH_PAGE_URL
from backend.llm_models.services.external_sources import LMARENA_DATASET_URL
from backend.llm_models.services.external_sources import LMARENA_LICENSE
from backend.llm_models.services.external_sources import MAP_PATH
from backend.llm_models.services.external_sources import load_model_map

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser

logger = logging.getLogger(__name__)

COLUMNS: tuple[str, ...] = (
    # -- identity
    "id",
    "model_id",
    "model_name",
    "provider",
    "is_local",
    "endpoint",
    "served_model_name",
    "catalog_twin",
    "catalog_twin_source",
    "inherited_from",
    "inherited_fields",
    # -- openness, license, dates
    "open_weights",
    "open_weights_source",
    "hf_id",
    "license",
    "release_date",
    "release_date_source",
    "knowledge_cutoff",
    "knowledge_cutoff_source",
    "knowledge_cutoff_conflict",
    # -- limits and price of this route (never inherited)
    "context_window",
    "max_output_tokens",
    "input_usd_per_1m",
    "output_usd_per_1m",
    # -- size
    "params_checkpoint_total",
    "params_reported_total",
    "params_reported_source",
    "params_active",
    "params_active_source",
    # -- architecture, from the config file
    "arch_is_moe",
    "arch_num_experts",
    "arch_experts_per_token",
    "arch_num_hidden_layers",
    "arch_hidden_size",
    "arch_native_context",
    "arch_native_dtype",
    "arch_quant_method",
    "served_quantization",
    # -- Hugging Face popularity snapshot
    "hf_downloads_30d",
    "hf_downloads_all_time",
    "hf_likes",
    "hf_fetched_at",
    # -- Epoch AI
    "epoch_model",
    "epoch_publication_date",
    "epoch_parameters",
    "epoch_active_params",
    "epoch_training_compute_flop",
    "epoch_training_dataset_size",
    "epoch_confidence",
    "epoch_accessibility",
    "epoch_match",
    "epoch_fetched_at",
    # -- LMArena
    "lmarena_webdev_name",
    "lmarena_webdev_rating",
    "lmarena_webdev_rating_lower",
    "lmarena_webdev_rating_upper",
    "lmarena_webdev_votes",
    "lmarena_webdev_rank",
    "lmarena_webdev_n_ranked",
    "lmarena_text_coding_rating",
    "lmarena_text_coding_votes",
    "lmarena_agent_score",
    "lmarena_agent_score_lower",
    "lmarena_agent_score_upper",
    "lmarena_publish_date",
    "lmarena_match",
    "lmarena_fetched_at",
    # -- the authors' recommended sampling, not what a run used
    "recommended_temperature",
    "recommended_top_p",
    "recommended_top_k",
    "recommended_sampling_boilerplate",
    # -- this platform, the caller's jobs
    "n_jobs",
    "n_completed_jobs",
)

NOTES = (
    "One row per model; the unit of any model-level analysis is the model, not the job.",
    "Empty / null = not known (never 0).",
    "Local endpoint models inherit model-intrinsic covariates from their catalog twin (an assumption: same "
    "name, same weights); inherited_fields lists which. Context window and price are the route's own.",
    "params_checkpoint_total is the Hugging Face safetensors element count (all stored tensors), not the "
    "size the authors report; params_*_source says where reported/active counts come from.",
    "Epoch AI and LMArena values are matched by a hand-curated name map (epoch_match / lmarena_match: "
    "exact, variant = different reasoning effort or release than we call, assumed = identity assumed). "
    "An Epoch / LMArena record a local route inherits from its twin is always reported as assumed.",
    "recommended_* is the model authors' generation_config.json, not the sampling a run used.",
    "n_jobs / n_completed_jobs count the requesting user's generation jobs.",
)

SOURCES = (
    {"name": "Epoch AI, 'All AI models'", "license": EPOCH_LICENSE, "url": EPOCH_PAGE_URL},
    {"name": "LMArena leaderboard dataset", "license": LMARENA_LICENSE, "url": LMARENA_DATASET_URL},
    {"name": "models.dev", "license": "MIT", "url": "https://models.dev"},
    {"name": "Hugging Face Hub", "license": "per model (see license column)", "url": "https://huggingface.co"},
    {"name": "OpenRouter model catalog", "license": None, "url": "https://openrouter.ai/models"},
)


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _num(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _positive(value: Any) -> int | float | None:
    number = _num(value)
    return number if number is not None and number > 0 else None


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _year_month(value: Any) -> str | None:
    text = _str(value)
    return text[:7] if text else None


def _iso_from_unix(value: Any) -> str | None:
    number = _positive(value)
    if number is None:
        return None
    return datetime.fromtimestamp(number, tz=UTC).date().isoformat()


@lru_cache(maxsize=4)
def _map_twins(path: str, mtime: float) -> dict[str, str]:
    try:
        return {mid: e.twin for mid, e in load_model_map(path).items() if e.twin}
    except (OSError, ValueError):
        logger.warning("External model map unreadable; no manual twin fallback", exc_info=True)
        return {}


def map_twins(path: Path = MAP_PATH) -> dict[str, str]:
    """``{local model_id: catalog twin model_id}`` from the curated name map (cached per file mtime)."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    return _map_twins(str(path), mtime)


def _twin_ref(model: LLMModel, manual: dict[str, str]) -> tuple[str | None, str | None]:
    twin = _dict((model.metadata or {}).get("catalog_twin"))
    if _str(twin.get("model_id")):
        return str(twin["model_id"]), _str(twin.get("source")) or "metadata.catalog_twin"
    if model.model_id in manual:
        return manual[model.model_id], "external_model_map (manual)"
    return None, None


def _knowledge(model: LLMModel, md: dict, provider_record: dict) -> tuple[str | None, str | None, bool | None]:
    candidates = [
        (_str(md.get("knowledge")), "models.dev"),
        (_str((model.capabilities_json or {}).get("knowledge_cutoff")), "openrouter"),
        (_str(provider_record.get("knowledge")), "models.dev (serving provider record)"),
    ]
    present = [(value, source) for value, source in candidates if value]
    if not present:
        return None, None, None
    value, source = present[0]
    distinct = {_year_month(v) for v, _ in present}
    conflict = len(distinct) > 1 if len(present) > 1 else None
    return value, source, conflict


def _release(model: LLMModel, md: dict, provider_record: dict) -> tuple[str | None, str | None]:
    if _str(md.get("release_date")):
        return str(md["release_date"]), "models.dev"
    if _str(provider_record.get("release_date")):
        return str(provider_record["release_date"]), "models.dev (serving provider record)"
    created = _iso_from_unix((model.capabilities_json or {}).get("created"))
    if created:
        return created, "openrouter (added to catalog)"
    return None, None


def _price_per_million(model: LLMModel, value: float) -> float | None:
    if model.is_local or value is None or value < 0:
        return None
    return round(value * 1_000_000, 4)


def covariate_row(
    model: LLMModel,
    *,
    twin_model: LLMModel | None = None,
    twin_ref: tuple[str | None, str | None] = (None, None),
    n_jobs: int = 0,
    n_completed: int = 0,
) -> dict[str, Any]:
    """The covariate row of one model (``twin_model``: its catalog twin, used for local rows only)."""
    own = model.metadata or {}
    is_local = model.is_local
    twin_meta = (twin_model.metadata or {}) if (is_local and twin_model is not None) else {}
    inherited: list[str] = []

    def pick(key: str) -> dict:
        value = _dict(own.get(key))
        if value:
            return value
        fallback = _dict(twin_meta.get(key))
        if fallback:
            inherited.append(key)
        return fallback

    md = pick("models_dev")
    hf = pick("huggingface")
    params = pick("params")
    epoch = pick("epoch")
    arena = pick("lmarena")
    twin_record = _dict(own.get("catalog_twin"))
    provider_record = _dict(twin_record.get("provider_record"))

    arch = _dict(hf.get("architecture"))
    moe = _dict(arch.get("moe"))
    quant = _dict(arch.get("quantization_config"))
    gen = _dict(hf.get("generation_config"))
    categories = _dict(arena.get("categories"))
    webdev = _dict(categories.get("webdev"))
    coding = _dict(categories.get("text_coding"))
    agent = _dict(categories.get("agent"))

    open_weights = _bool(md.get("open_weights"))
    open_weights_source = "models.dev" if open_weights is not None else None
    if open_weights is None and _bool(provider_record.get("open_weights")) is not None:
        open_weights, open_weights_source = provider_record["open_weights"], "models.dev (serving provider record)"

    knowledge, knowledge_source, knowledge_conflict = _knowledge(model, md, provider_record)
    release, release_source = _release(model, md, provider_record)

    reported = _positive(params.get("reported_total"))
    reported_source = _str(params.get("active_source")) if reported is not None else None
    if reported is None and _positive(epoch.get("parameters")) is not None:
        reported, reported_source = epoch["parameters"], "epoch"
    active = _positive(params.get("active"))
    active_source = _str(params.get("active_source")) if active is not None else None
    if active is None and _positive(epoch.get("active_params_parsed")) is not None:
        active, active_source = epoch["active_params_parsed"], "epoch_notes"

    is_moe = _bool(arch.get("is_moe"))
    if is_moe is None:
        is_moe = _bool(params.get("is_moe"))

    # The curated match describes the twin's own route; for a route that inherits the record,
    # the identity itself is an assumption, so it never reports "exact" / "variant".
    epoch_match = _str(epoch.get("match"))
    if epoch_match and "epoch" in inherited:
        epoch_match = "assumed"
    lmarena_match = _str(arena.get("match"))
    if lmarena_match and "lmarena" in inherited:
        lmarena_match = "assumed"

    twin_id, twin_source = twin_ref
    return {
        "id": model.pk,
        "model_id": model.model_id,
        "model_name": model.model_name,
        "provider": model.provider,
        "is_local": is_local,
        "endpoint": model.endpoint.slug if model.endpoint_id else None,
        "served_model_name": _str(model.served_model_name),
        "catalog_twin": twin_id,
        "catalog_twin_source": twin_source,
        "inherited_from": twin_id if inherited else None,
        "inherited_fields": inherited,
        "open_weights": open_weights,
        "open_weights_source": open_weights_source,
        "hf_id": _str(hf.get("id")) or _str((model.capabilities_json or {}).get("hugging_face_id")),
        "license": _str(hf.get("license_name")) or _str(hf.get("license")),
        "release_date": release,
        "release_date_source": release_source,
        "knowledge_cutoff": knowledge,
        "knowledge_cutoff_source": knowledge_source,
        "knowledge_cutoff_conflict": knowledge_conflict,
        "context_window": _positive(model.context_window),
        "max_output_tokens": _positive(model.max_output_tokens),
        "input_usd_per_1m": _price_per_million(model, model.input_price_per_token),
        "output_usd_per_1m": _price_per_million(model, model.output_price_per_token),
        "params_checkpoint_total": _positive(params.get("checkpoint_total")) or _positive(hf.get("parameters")),
        "params_reported_total": reported,
        "params_reported_source": reported_source,
        "params_active": active,
        "params_active_source": active_source,
        "arch_is_moe": is_moe,
        "arch_num_experts": _positive(moe.get("num_experts")),
        "arch_experts_per_token": _positive(moe.get("experts_per_token")),
        "arch_num_hidden_layers": _positive(arch.get("num_hidden_layers")),
        "arch_hidden_size": _positive(arch.get("hidden_size")),
        "arch_native_context": _positive(arch.get("native_context")),
        "arch_native_dtype": _str(arch.get("torch_dtype")),
        "arch_quant_method": _str(quant.get("quant_method")) or _str(hf.get("native_quant_method")),
        "served_quantization": _str(model.quantization),
        "hf_downloads_30d": _num(hf.get("downloads")) if hf else None,
        "hf_downloads_all_time": _num(hf.get("downloads_all_time")),
        "hf_likes": _num(hf.get("likes")) if hf else None,
        "hf_fetched_at": _str(hf.get("fetched_at")),
        "epoch_model": _str(epoch.get("model")),
        "epoch_publication_date": _str(epoch.get("publication_date")),
        "epoch_parameters": _positive(epoch.get("parameters")),
        "epoch_active_params": _positive(epoch.get("active_params_parsed")),
        "epoch_training_compute_flop": _positive(epoch.get("training_compute_flop")),
        "epoch_training_dataset_size": _positive(epoch.get("training_dataset_size")),
        "epoch_confidence": _str(epoch.get("confidence")),
        "epoch_accessibility": _str(epoch.get("model_accessibility")),
        "epoch_match": epoch_match,
        "epoch_fetched_at": _str(epoch.get("fetched_at")),
        "lmarena_webdev_name": _str(webdev.get("arena_name")),
        "lmarena_webdev_rating": _num(webdev.get("rating")),
        "lmarena_webdev_rating_lower": _num(webdev.get("rating_lower")),
        "lmarena_webdev_rating_upper": _num(webdev.get("rating_upper")),
        "lmarena_webdev_votes": _num(webdev.get("vote_count")),
        "lmarena_webdev_rank": _num(webdev.get("rank")),
        "lmarena_webdev_n_ranked": _num(webdev.get("n_ranked")),
        "lmarena_text_coding_rating": _num(coding.get("rating")),
        "lmarena_text_coding_votes": _num(coding.get("vote_count")),
        "lmarena_agent_score": _num(agent.get("score")),
        "lmarena_agent_score_lower": _num(agent.get("score_lower")),
        "lmarena_agent_score_upper": _num(agent.get("score_upper")),
        "lmarena_publish_date": _str(arena.get("publish_date")),
        "lmarena_match": lmarena_match,
        "lmarena_fetched_at": _str(arena.get("fetched_at")),
        "recommended_temperature": _num(gen.get("temperature")),
        "recommended_top_p": _num(gen.get("top_p")),
        "recommended_top_k": _num(gen.get("top_k")),
        "recommended_sampling_boilerplate": _bool(gen.get("from_model_config")) if gen else None,
        "n_jobs": n_jobs,
        "n_completed_jobs": n_completed,
    }


def model_covariates(user: AbstractBaseUser | None, *, used_only: bool = True) -> dict[str, Any]:
    """Covariate rows for the models ``user`` may see (``used_only``: those with a job of theirs)."""
    authenticated = user is not None and getattr(user, "is_authenticated", False)
    mine = Q(generation_runs__created_by=user) if authenticated else Q(pk__in=[])
    qs = (
        LLMModel.objects.visible_to(user)
        .select_related("endpoint")
        .annotate(
            n_jobs=Count("generation_runs", filter=mine, distinct=True),
            n_completed=Count(
                "generation_runs",
                filter=mine & Q(generation_runs__status="completed"),
                distinct=True,
            ),
        )
        .order_by("provider", "model_id")
    )
    if used_only:
        qs = qs.filter(n_jobs__gt=0)
    models = list(qs)

    manual = map_twins()
    refs = {m.pk: _twin_ref(m, manual) if m.is_local else (None, None) for m in models}
    twin_ids = {ref[0] for ref in refs.values() if ref[0]}
    twins = {t.model_id: t for t in LLMModel.objects.filter(endpoint__isnull=True, model_id__in=twin_ids)}

    rows = [
        covariate_row(
            m,
            twin_model=twins.get(refs[m.pk][0]) if refs[m.pk][0] else None,
            twin_ref=refs[m.pk],
            n_jobs=m.n_jobs,
            n_completed=m.n_completed,
        )
        for m in models
    ]
    return {
        "columns": list(COLUMNS),
        "rows": rows,
        "n_models": len(rows),
        "used_only": used_only,
        "generated_at": timezone.now().isoformat(),
        "sources": list(SOURCES),
        "notes": list(NOTES),
    }
