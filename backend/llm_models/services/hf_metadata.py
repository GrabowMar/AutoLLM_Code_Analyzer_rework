"""Pure parsers for Hugging Face Hub metadata (no I/O; ``enrichment.fetch_hf_info`` does the fetching).

Everything here turns one Hub payload into the small, documented record
stored under ``LLMModel.metadata["huggingface"]`` (or ``metadata["params"]``).
Rules: an unknown or unparseable value is ``None`` — never a guessed number —
and every derived figure says where it came from.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

# ``expand[]`` fields of GET /api/models/<id> (verified live 2026-11-02). Asking
# for them explicitly is what returns downloadsAllTime, childrenModelCount,
# trendingScore, evalResults, inferenceProviderMapping and baseModels.
HF_EXPAND_FIELDS = (
    "author",
    "baseModels",
    "cardData",
    "childrenModelCount",
    "config",
    "createdAt",
    "downloads",
    "downloadsAllTime",
    "evalResults",
    "gated",
    "inferenceProviderMapping",
    "lastModified",
    "library_name",
    "likes",
    "pipeline_tag",
    "safetensors",
    "sha",
    "spaces",
    "tags",
    "trendingScore",
    "usedStorage",
)

# The Hub truncates a model's ``spaces`` list at this length.
HF_SPACES_CAP = 100
MAX_PAPERS = 5
MAX_EVAL_RESULTS = 60
# tokenizer_config ``model_max_length`` sentinel for "no limit" (int(1e30)).
_NO_LIMIT_SENTINEL = 10**12
_CHILDREN_KINDS = ("adapter", "merge", "quantized", "finetune")
_GENERATION_KEYS = ("temperature", "top_p", "top_k", "min_p", "repetition_penalty", "do_sample", "max_new_tokens")
_WEIGHT_FORMATS = (  # first match wins
    ("safetensors", (".safetensors",)),
    ("gguf", (".gguf",)),
    ("pytorch_bin", (".bin",)),
    ("pytorch_pth", (".pth", ".pt")),
)
_FINGERPRINT_FILES = (
    "config.json",
    "generation_config.json",
    "tokenizer_config.json",
    "chat_template.jinja",
    "tokenizer.json",
)
# Packed integer dtypes: the safetensors element count is not the logical parameter count.
_PACKED_DTYPES = frozenset({"I8", "U8", "I4", "U4"})
_PACKED_SHARE_LIMIT = 0.01


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _num(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _first(mapping: dict, *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


# ── /api/models/<id> (expanded) ────────────────────────────────────────────


def hf_children(info: dict) -> dict[str, int] | None:
    raw = info.get("childrenModelCount")
    if not isinstance(raw, dict):
        return None
    return {kind: _int(raw.get(kind)) or 0 for kind in _CHILDREN_KINDS}


def hf_base_relation(info: dict) -> tuple[str | None, list[str]]:
    """``(relation, base model ids)`` from ``expand[]=baseModels`` (``finetune``, ``quantized``, ...)."""
    raw = info.get("baseModels")
    if not isinstance(raw, dict):
        return None, []
    models = [str(m.get("id")) for m in raw.get("models") or [] if isinstance(m, dict) and m.get("id")]
    return _str(raw.get("relation")), models


def hf_inference_providers(info: dict) -> list[dict[str, Any]] | None:
    raw = info.get("inferenceProviderMapping")
    if isinstance(raw, dict):
        items = [{"provider": name, **(value if isinstance(value, dict) else {})} for name, value in raw.items()]
    elif isinstance(raw, list):
        items = [value for value in raw if isinstance(value, dict)]
    else:
        return None
    out = [
        {
            "provider": str(item.get("provider") or ""),
            "status": _str(item.get("status")),
            "task": _str(item.get("task")),
            "provider_model_id": _str(item.get("providerId")),
        }
        for item in items
        if item.get("provider")
    ]
    return sorted(out, key=lambda p: p["provider"])


def _eval_from_eval_results(raw: list) -> list[dict[str, Any]]:
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        data = item.get("data") if isinstance(item.get("data"), dict) else item
        dataset = data.get("dataset") if isinstance(data.get("dataset"), dict) else {}
        source = data.get("source") if isinstance(data.get("source"), dict) else {}
        value = _num(data.get("value"))
        if value is None:
            continue
        out.append(
            {
                "task": _str(dataset.get("task_id")),
                "dataset": _str(dataset.get("id")) or _str(dataset.get("name")),
                "metric": _str(data.get("metric")) or _str(dataset.get("task_id")),
                "value": value,
                "date": _str(data.get("date")),
                "source": "model card (self-reported)",
                "source_name": _str(source.get("name")),
                "source_url": _str(source.get("url")),
                "notes": (_str(data.get("notes")) or "")[:500] or None,
                "verified": bool(item.get("verified")),
            },
        )
    return out


def _eval_from_model_index(card: dict) -> list[dict[str, Any]]:
    out = []
    for entry in card.get("model-index") or []:
        if not isinstance(entry, dict):
            continue
        for result in entry.get("results") or []:
            if not isinstance(result, dict):
                continue
            task = result.get("task") if isinstance(result.get("task"), dict) else {}
            dataset = result.get("dataset") if isinstance(result.get("dataset"), dict) else {}
            source = result.get("source") if isinstance(result.get("source"), dict) else {}
            for metric in result.get("metrics") or []:
                if not isinstance(metric, dict) or _num(metric.get("value")) is None:
                    continue
                out.append(
                    {
                        "task": _str(task.get("name")) or _str(task.get("type")),
                        "dataset": _str(dataset.get("name")) or _str(dataset.get("type")),
                        "metric": _str(metric.get("name")) or _str(metric.get("type")),
                        "value": metric["value"],
                        "date": None,
                        "source": "model card (self-reported)",
                        "source_name": _str(source.get("name")),
                        "source_url": _str(source.get("url")),
                        "notes": None,
                        "verified": bool(metric.get("verified")),
                    },
                )
    return out


def _all_eval_results(info: dict) -> list[dict[str, Any]]:
    raw = info.get("evalResults")
    out = _eval_from_eval_results(raw) if isinstance(raw, list) else []
    card = info.get("cardData") if isinstance(info.get("cardData"), dict) else {}
    return out + _eval_from_model_index(card)


def hf_eval_results(info: dict) -> list[dict[str, Any]]:
    """Self-reported benchmark scores (``evalResults`` expand + card ``model-index``), as published.

    At most ``MAX_EVAL_RESULTS`` are kept; ``eval_results_total`` in the record says how many there were.
    """
    return _all_eval_results(info)[:MAX_EVAL_RESULTS]


def hf_record_extras(info: dict) -> dict[str, Any]:
    """The keys ``expand[]`` adds to the stored Hub record (``None`` when the Hub sent nothing)."""
    config = info.get("config") if isinstance(info.get("config"), dict) else {}
    quant = config.get("quantization_config") if isinstance(config.get("quantization_config"), dict) else {}
    spaces = info.get("spaces")
    relation, base_ids = hf_base_relation(info)
    return {
        "sha": _str(info.get("sha")),
        "used_storage_bytes": _int(info.get("usedStorage")),
        "trending_score": _num(info.get("trendingScore")),
        "children": hf_children(info),
        "base_model_relation": relation,
        "base_models_expanded": base_ids,
        "inference_providers": hf_inference_providers(info),
        "native_quant_method": _str(quant.get("quant_method")),
        "spaces_count_capped": isinstance(spaces, list) and len(spaces) >= HF_SPACES_CAP,
        "eval_results": hf_eval_results(info),
        # Published count before the storage cap (the page says "60 of N" instead of a silent cut).
        "eval_results_total": len(_all_eval_results(info)),
    }


# ── config.json ─────────────────────────────────────────────────────────────


def _text_config(config: dict) -> tuple[dict, bool]:
    """The language-model part of a config (multimodal repos nest it) and whether it was nested."""
    for key in ("text_config", "language_config", "llm_config", "text_model_config"):
        nested = config.get(key)
        if isinstance(nested, dict) and nested:
            return nested, True
    return config, False


def _rope(text: dict) -> dict[str, Any] | None:
    scaling = text.get("rope_scaling")
    params = text.get("rope_parameters")
    if not isinstance(scaling, dict) and isinstance(params, dict):
        if "rope_type" in params or "type" in params:
            scaling = params
        else:  # per-layer-type rope (Gemma 4): the full-attention layers carry the long-context rope
            per_type = params.get("full_attention") or next((v for v in params.values() if isinstance(v, dict)), None)
            scaling = per_type if isinstance(per_type, dict) else None
    scaling = scaling if isinstance(scaling, dict) else {}
    theta = _num(text.get("rope_theta")) or _num(scaling.get("rope_theta"))
    rope_type = _str(scaling.get("rope_type")) or _str(scaling.get("type"))
    out = {
        "theta": theta,
        "scaling_type": rope_type if rope_type and rope_type != "default" else None,
        "factor": _num(scaling.get("factor")),
        "original_max_position_embeddings": _int(scaling.get("original_max_position_embeddings")),
    }
    return out if any(v is not None for v in out.values()) else None


def _attention(text: dict) -> dict[str, Any]:
    heads = _int(text.get("num_attention_heads"))
    kv_heads = _int(text.get("num_key_value_heads"))
    kv_lora = _int(text.get("kv_lora_rank"))
    layer_types = text.get("layer_types")
    counts = dict(Counter(str(t) for t in layer_types)) if isinstance(layer_types, list) and layer_types else None
    if kv_lora:
        variant = "MLA"
    elif heads and kv_heads == 1:
        variant = "MQA"
    elif heads and kv_heads and kv_heads < heads:
        variant = "GQA"
    elif heads and kv_heads == heads:
        variant = "MHA"
    else:
        variant = None
    return {
        "variant": variant,
        "kv_lora_rank": kv_lora,
        "q_lora_rank": _int(text.get("q_lora_rank")),
        "sliding_window": _int(text.get("sliding_window")) if text.get("use_sliding_window") is not False else None,
        "layer_types": counts,
    }


def _moe(text: dict) -> dict[str, Any] | None:
    experts = _int(
        _first(text, "n_routed_experts", "num_experts", "num_local_experts", "moe_num_experts", "n_experts"),
    )
    per_token = _int(
        _first(
            text,
            "num_experts_per_tok",
            "num_experts_per_token",
            "experts_per_token",
            "moe_topk",
            "top_k_experts",
            "num_selected_experts",
        ),
    )
    if not experts or experts <= 1:
        return None
    shared = _int(_first(text, "n_shared_experts", "num_shared_experts"))
    if shared is None and _int(text.get("shared_expert_intermediate_size")):
        shared = 1
    return {
        "num_experts": experts,
        "experts_per_token": per_token,
        "num_shared_experts": shared,
        "moe_intermediate_size": _int(
            _first(text, "moe_intermediate_size", "expert_intermediate_size", "moe_ffn_hidden_size"),
        ),
        "first_k_dense_replace": _int(text.get("first_k_dense_replace")),
        "num_nextn_predict_layers": _int(text.get("num_nextn_predict_layers")),
        "decoder_sparse_step": _int(text.get("decoder_sparse_step")),
        "mlp_only_layers": len(text["mlp_only_layers"]) if isinstance(text.get("mlp_only_layers"), list) else None,
        "sparse_layers": (
            sum(1 for t in text["mlp_layer_types"] if str(t) == "sparse")
            if isinstance(text.get("mlp_layer_types"), list)
            else None
        ),
    }


def _quantization(config: dict, text: dict) -> dict[str, Any] | None:
    quant = config.get("quantization_config") or text.get("quantization_config")
    if not isinstance(quant, dict):
        return None
    out = {
        "quant_method": _str(quant.get("quant_method")),
        "bits": _int(_first(quant, "bits", "weight_bits", "w_bit")),
        "fmt": _str(quant.get("fmt")) or _str(quant.get("scale_fmt")),
        "weight_block_size": quant.get("weight_block_size")
        if isinstance(quant.get("weight_block_size"), list)
        else None,
        "expert_dtype": _str(quant.get("expert_dtype")),
    }
    return {k: v for k, v in out.items() if v is not None} or None


_TRIVIAL_VALUES = (None, False, 0, 1, [], {}, "")
_ENCODER_CONFIG_KEYS = ("vision_config", "audio_config", "vision_tower_config", "speech_config", "encoder_config")


def _has_expert_keys(mapping: dict, depth: int = 0) -> bool:
    """Any ``*expert*`` key with a real value, nested blocks included (``{"text": {"moe": {...}}}``)."""
    for key, value in mapping.items():
        if "expert" in str(key).lower() and value not in _TRIVIAL_VALUES:
            return True
        if isinstance(value, dict) and depth < 3 and _has_expert_keys(value, depth + 1):
            return True
    return False


def parse_architecture(config: Any) -> dict[str, Any] | None:
    """Structural facts from a repo's ``config.json`` (``None`` when it is not a config)."""
    if not isinstance(config, dict) or not config:
        return None
    text, nested = _text_config(config)
    moe = _moe(text)
    rope = _rope(text)
    max_pos = _int(text.get("max_position_embeddings"))
    native_context = rope["original_max_position_embeddings"] if rope and rope.get("scaling_type") else None
    torch_dtype = _str(_first(config, "torch_dtype", "dtype")) or _str(_first(text, "torch_dtype", "dtype"))
    out = {
        "model_type": _str(config.get("model_type")),
        "text_model_type": _str(text.get("model_type")) if nested else None,
        "architectures": [str(a) for a in config.get("architectures") or [] if a],
        # Nested text config, or a flat one carrying an encoder block (Qwen2.5-VL's vision_config).
        "multimodal": nested or any(isinstance(config.get(k), dict) for k in _ENCODER_CONFIG_KEYS),
        "num_hidden_layers": _int(text.get("num_hidden_layers")),
        "hidden_size": _int(text.get("hidden_size")),
        "num_attention_heads": _int(text.get("num_attention_heads")),
        "num_key_value_heads": _int(text.get("num_key_value_heads")),
        "head_dim": _int(text.get("head_dim")) or None,
        "intermediate_size": _int(text.get("intermediate_size")),
        "vocab_size": _int(text.get("vocab_size")),
        "max_position_embeddings": max_pos,
        "native_context": native_context or max_pos,
        "rope": rope,
        "attention": _attention(text),
        "tie_word_embeddings": (
            config.get("tie_word_embeddings")
            if isinstance(config.get("tie_word_embeddings"), bool)
            else text.get("tie_word_embeddings")
            if isinstance(text.get("tie_word_embeddings"), bool)
            else None
        ),
        "torch_dtype": torch_dtype,
        "quantization_config": _quantization(config, text),
        "is_moe": moe is not None,
        "moe": moe,
        # Any expert-ish key with a real value: a config we do not parse may still be MoE,
        # so "dense" (active = checkpoint) is only claimed when this is False.
        "has_expert_keys": _has_expert_keys(text),
        "uncovered_features": sorted(
            {
                key.split("_", 1)[0]
                for key in text
                if key.startswith(("engram_", "dspark_"))  # DeepSeek V4.1 Engram memory / dspark heads
            },
        ),
    }
    if not any(out[k] for k in ("model_type", "num_hidden_layers", "hidden_size", "architectures")):
        return None
    return out


# ── generation_config.json / tokenizer_config.json ─────────────────────────


def parse_generation_config(cfg: Any, *, commit: str | None = None) -> dict[str, Any] | None:
    """The authors' recommended sampling (``generation_config.json``); never what a provider used."""
    if not isinstance(cfg, dict):
        return None
    out: dict[str, Any] = {key: cfg.get(key) for key in _GENERATION_KEYS}
    out = {k: v for k, v in out.items() if isinstance(v, (int, float, bool))}
    out["from_model_config"] = bool(cfg.get("_from_model_config"))
    out["commit"] = commit
    return out


def _token_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        return _str(value.get("content"))
    return None


def _chat_template_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, list):  # [{"name": "default", "template": "..."}, ...]
        parts = [str(t.get("template") or "") for t in value if isinstance(t, dict)]
        joined = "\n".join(p for p in parts if p)
        return joined or None
    return None


def parse_tokenizer(cfg: Any, api_config: dict | None = None) -> dict[str, Any] | None:
    """Tokenizer facts from ``tokenizer_config.json``, falling back to the Hub record's ``config``.

    The chat template is fingerprinted (sha256), never stored: it is what
    formats every agent turn, so its revision is provenance.
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    api_config = api_config if isinstance(api_config, dict) else {}
    hub_cfg = api_config.get("tokenizer_config") if isinstance(api_config.get("tokenizer_config"), dict) else {}
    if not cfg and not hub_cfg and not api_config.get("chat_template_jinja"):
        return None
    template, source = _chat_template_text(cfg.get("chat_template")), "tokenizer_config.json"
    if template is None:
        template, source = _chat_template_text(hub_cfg.get("chat_template")), "hub_config"
    if template is None:
        template, source = _chat_template_text(api_config.get("chat_template_jinja")), "chat_template.jinja"
    max_len = _int(cfg.get("model_max_length"))
    return {
        "tokenizer_class": _str(cfg.get("tokenizer_class")) or _str(hub_cfg.get("tokenizer_class")),
        "model_max_length": max_len if max_len and max_len < _NO_LIMIT_SENTINEL else None,
        "has_chat_template": template is not None,
        "chat_template_sha256": hashlib.sha256(template.encode("utf-8")).hexdigest() if template else None,
        "chat_template_source": source if template else None,
        "bos_token": _token_text(cfg.get("bos_token")) or _token_text(hub_cfg.get("bos_token")),
        "eos_token": _token_text(cfg.get("eos_token")) or _token_text(hub_cfg.get("eos_token")),
        "source": "tokenizer_config.json" if cfg else "hub_config",
    }


# ── repo tree ───────────────────────────────────────────────────────────────


_SHARD_NAME_RE = re.compile(r"^(?P<prefix>.+?)-\d+-of-\d+\.[^.]+$")


def _weight_series(files: list[dict]) -> list[dict]:
    """The files of one copy of the weights when a repo ships the same weights twice.

    Mistral repos store ``consolidated*.safetensors`` (their native format)
    next to the transformers ``model-*.safetensors`` shards, and some repos
    keep an older sharded series (``modelv2-*``) beside ``model-*``: summing
    every file doubles the byte total. When a ``model`` series exists, other
    sharded series and ``consolidated*`` files are dropped; single extra
    files (e.g. ``mtp.safetensors``) are real components and stay.
    """
    groups: dict[str, list[dict]] = {}
    sharded: set[str] = set()
    for f in files:
        name = f["path"].rsplit("/", 1)[-1]
        match = _SHARD_NAME_RE.match(name)
        key = match.group("prefix") if match else name.split(".", 1)[0]
        if match:
            sharded.add(key)
        groups.setdefault(key, []).append(f)
    if "model" not in groups or len(groups) == 1:
        return files
    return [
        f
        for key, members in groups.items()
        if key == "model" or (key not in sharded and not key.startswith("consolidated"))
        for f in members
    ]


def parse_tree(entries: Any) -> dict[str, Any] | None:
    """Weight bytes / format / shards and config-file oids from ``/tree/<rev>?recursive=true``."""
    if not isinstance(entries, list):
        return None
    files = [e for e in entries if isinstance(e, dict) and e.get("type") == "file" and isinstance(e.get("path"), str)]
    if not files:
        return None
    by_format: dict[str, list[dict]] = {}
    for name, suffixes in _WEIGHT_FORMATS:
        matched = [f for f in files if f["path"].lower().endswith(suffixes)]
        if matched:
            by_format[name] = matched
    weight_format = next(iter(by_format), None)
    chosen = by_format.get(weight_format, []) if weight_format else []
    root_level = [f for f in chosen if "/" not in f["path"]]
    if root_level:
        chosen = root_level
    chosen = _weight_series(chosen)
    sizes = [_int(f.get("size")) for f in chosen]
    weights_bytes = sum(s for s in sizes if s) if chosen and all(s is not None for s in sizes) else None
    if weight_format == "gguf" and len(chosen) > 1:
        # GGUF repos usually hold alternative quantizations, not shards of one model.
        weights_bytes = None
    shard_match = [re.search(r"-of-(\d+)\.", f["path"]) for f in chosen]
    shard_counts = {int(m.group(1)) for m in shard_match if m}
    oids = {f["path"]: f.get("oid") for f in files if f["path"] in _FINGERPRINT_FILES and f.get("oid")}
    return {
        "weight_format": weight_format,
        "weight_files": len(chosen),
        "weights_bytes": weights_bytes,
        "shard_count": max(shard_counts) if shard_counts else (len(chosen) or None),
        "other_weight_formats": [name for name in by_format if name != weight_format],
        "repo_files": len(files),
        "file_oids": oids,
        "present": sorted(p for p in (f["path"] for f in files) if p in _FINGERPRINT_FILES),
    }


# ── papers ──────────────────────────────────────────────────────────────────


def parse_paper(paper: Any, arxiv_id: str) -> dict[str, Any]:
    paper = paper if isinstance(paper, dict) else {}
    return {
        "id": arxiv_id,
        "title": _str(paper.get("title")),
        "published_at": _str(paper.get("publishedAt")),
        "upvotes": _int(paper.get("upvotes")),
        "github_repo": _str(paper.get("githubRepo")),
    }


# ── active / total parameters ──────────────────────────────────────────────

# Hand-checked figures for repos our jobs use, keyed by lower-cased HF repo id.
# Quotes are from each repo's README (model card) as of 2026-11-02.
CURATED_PARAMS: dict[str, dict[str, Any]] = {
    "deepseek-ai/deepseek-v4.1-flash": {
        "reported_total": 552_000_000_000,
        "active": 16_000_000_000,
        "note": (
            "Model card: '# Backbone Params 552B', '# Activated Params 8B / 16B' (prefill / decode); "
            "Engram n-gram embeddings (~196B per Epoch AI) are not counted. Active = decode figure."
        ),
    },
    "deepseek-ai/deepseek-v4-pro": {
        "reported_total": 1_600_000_000_000,
        "active": 49_000_000_000,
        "note": "Model card: 'DeepSeek-V4-Pro with 1.6T parameters (49B activated)'.",
    },
    "zai-org/glm-5.3-flash": {
        "reported_total": 320_000_000_000,
        "active": 18_000_000_000,
        "note": "Model card: 'With 320B total parameters and just 18B active parameters'.",
    },
    "qwen/qwen3.8-flash-next": {
        "reported_total": 125_000_000_000,
        "active": 6_000_000_000,
        "note": (
            "Model card: 'Number of Parameters: 125B with 6B activated, plus 51B n-gram embedding and 4B MTP' "
            "(language model; vision encoder not included)."
        ),
    },
}

_TOTAL_ACTIVE_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)([BT])-A(\d+(?:\.\d+)?)B(?![a-z0-9])", re.IGNORECASE)
_ACTIVE_RE = re.compile(r"-A(\d+(?:\.\d+)?)B(?![a-z0-9])", re.IGNORECASE)
_UNIT = {"B": 1_000_000_000, "T": 1_000_000_000_000}


def params_from_name(*names: str) -> dict[str, Any] | None:
    """``30B-A3B`` -> total 30e9 / active 3e9; ``-A3B`` alone -> active only (values as rounded in the name)."""
    for name in names:
        if not name:
            continue
        match = _TOTAL_ACTIVE_RE.search(name)
        if match:
            return {
                "reported_total": round(float(match.group(1)) * _UNIT[match.group(2).upper()]),
                "active": round(float(match.group(3)) * _UNIT["B"]),
                "matched": match.group(0),
                "name": name,
            }
        match = _ACTIVE_RE.search(name)
        if match:
            return {
                "reported_total": None,
                "active": round(float(match.group(1)) * _UNIT["B"]),
                "matched": match.group(0),
                "name": name,
            }
    return None


def packed_share(by_dtype: Any) -> float:
    """Share of safetensors elements stored in packed integer dtypes (0 when unknown)."""
    if not isinstance(by_dtype, dict):
        return 0.0
    total = sum(v for v in by_dtype.values() if isinstance(v, (int, float)))
    packed = sum(v for k, v in by_dtype.items() if str(k).upper() in _PACKED_DTYPES and isinstance(v, (int, float)))
    return packed / total if total else 0.0


def _moe_layers(arch: dict) -> int | None:
    moe = arch.get("moe") or {}
    layers = arch.get("num_hidden_layers")
    if not layers:
        return None
    if moe.get("sparse_layers") is not None:
        sparse = moe["sparse_layers"]
    else:
        dense_first = moe.get("first_k_dense_replace") or 0
        step = moe.get("decoder_sparse_step") or 1
        sparse = sum(1 for i in range(dense_first, layers) if (i + 1) % step == 0)
        sparse -= moe.get("mlp_only_layers") or 0
    return max(0, sparse) + (moe.get("num_nextn_predict_layers") or 0)


def estimate_active_params(checkpoint_total: int | None, arch: dict | None, by_dtype: Any = None) -> int | None:
    """Config-based active-parameter estimate for standard (SwiGLU, DeepSeek/Qwen/GLM-layout) MoEs.

    ``active ~ checkpoint_total - L_moe x (E - k) x 3 x hidden x moe_intermediate`` with
    ``L_moe`` the MoE layers incl. MTP heads. ``None`` when the architecture is not
    covered (packed dtypes, Engram-style memory, missing fields): never a guess.
    Validated within ~3% on Qwen3-Coder-30B-A3B (3.4B vs 3.3B official).
    """
    if not checkpoint_total or not arch or not arch.get("is_moe") or arch.get("uncovered_features"):
        return None
    if packed_share(by_dtype) > _PACKED_SHARE_LIMIT:
        return None
    moe = arch.get("moe") or {}
    experts, per_token = moe.get("num_experts"), moe.get("experts_per_token")
    hidden, inter = arch.get("hidden_size"), moe.get("moe_intermediate_size")
    layers = _moe_layers(arch)
    if not all((experts, per_token, hidden, inter, layers)) or per_token >= experts:
        return None
    inactive = layers * (experts - per_token) * 3 * hidden * inter
    active = checkpoint_total - inactive
    if active <= 0 or active >= checkpoint_total:
        return None
    return int(active)


def compute_params(
    hf: dict | None,
    *,
    names: tuple[str, ...] = (),
    open_weights: bool | None = None,
) -> dict[str, Any] | None:
    """``metadata.params``: checkpoint vs reported total and active parameters, each with its source.

    Order for ``active``: curated model-card entry -> name pattern (``-A3B``) ->
    dense architecture (all checkpoint tensors) -> config estimate. ``active``
    stays ``None`` when none applies (never an unlabelled guess).
    """
    hf = hf if isinstance(hf, dict) else {}
    hf_id = str(hf.get("id") or "")
    arch = hf.get("architecture") if isinstance(hf.get("architecture"), dict) else None
    checkpoint = _int(hf.get("parameters"))
    by_dtype = hf.get("parameters_by_dtype")
    if not hf_id and checkpoint is None and arch is None:
        return None
    estimate = estimate_active_params(checkpoint, arch, by_dtype)
    out: dict[str, Any] = {
        "checkpoint_total": checkpoint,
        "checkpoint_note": (
            "safetensors element count of the linked repo: every stored tensor "
            "(MTP heads, vision encoder, extra embeddings included)"
            + ("; packed int dtypes make it differ from the logical count" if packed_share(by_dtype) > 0 else "")
        )
        if checkpoint
        else None,
        "reported_total": None,
        "active": None,
        "active_source": None,
        "active_note": None,
        "config_estimate": estimate,
        "is_moe": arch.get("is_moe") if arch else None,
        "hf_id": hf_id or None,
    }
    curated = CURATED_PARAMS.get(hf_id.lower())
    by_name = params_from_name(hf_id, *names)
    if curated:
        out.update(
            reported_total=curated.get("reported_total"),
            active=curated.get("active"),
            active_source="model_card_manual",
            active_note=curated["note"],
        )
    elif by_name:
        out.update(
            reported_total=by_name["reported_total"],
            active=by_name["active"],
            active_source="name_pattern",
            active_note=f"From '{by_name['matched']}' in '{by_name['name']}' (rounded as in the name).",
        )
    elif (
        arch
        and arch.get("is_moe") is False
        and not arch.get("has_expert_keys")
        # Only a config we actually read: a non-standard layout (moondream3's nested
        # "text" block, a MoE) parses to no layers / width and must not be called dense.
        and arch.get("num_hidden_layers")
        and arch.get("hidden_size")
        and checkpoint
        and packed_share(by_dtype) <= _PACKED_SHARE_LIMIT
    ):
        names = arch.get("architectures") or []
        encoder = arch.get("multimodal") or any("ConditionalGeneration" in str(a) for a in names)
        out.update(
            active=checkpoint,
            active_source="dense",
            active_note=(
                "Dense architecture (config.json): active = every checkpoint parameter"
                + (
                    "; the checkpoint also holds a vision/audio encoder that only runs on such inputs, "
                    "so text-only tokens use somewhat fewer (upper bound)."
                    if encoder
                    else ", all used per token."
                )
            ),
        )
    elif estimate is not None:
        out.update(
            active=estimate,
            active_source="config_estimate",
            active_note=(
                "Estimate: checkpoint total minus inactive routed experts "
                "(L_moe x (E - k) x 3 x hidden x moe_intermediate); assumes SwiGLU experts."
            ),
        )
    elif arch is None:
        config_status = (hf.get("fetch_status") or {}).get("config")
        out["active_note"] = (
            "Not estimated: config.json is gated for the fetching account."
            if config_status == "gated_inaccessible"
            else "Not estimated: no readable config.json."
        )
    elif arch.get("is_moe"):
        reason = (
            f"architecture has {', '.join(arch['uncovered_features'])} components the estimate does not cover"
            if arch.get("uncovered_features")
            else "packed int dtypes or missing config fields"
        )
        out["active_note"] = f"Not estimated: {reason}."
    elif checkpoint:
        if packed_share(by_dtype) > _PACKED_SHARE_LIMIT:
            reason = "packed int dtypes make the checkpoint count differ from the logical parameter count"
        elif arch.get("has_expert_keys"):
            reason = "config.json has expert keys this parser does not read, so it cannot be called dense"
        else:
            reason = "config.json layout not recognised (no layer count / width parsed)"
        out["active_note"] = f"Not estimated: {reason}."
    if open_weights is False and hf_id:
        out["mapping_note"] = (
            f"models.dev lists this API model as closed-weights; figures describe the linked repo {hf_id}, "
            "which may not be the served weights."
        )
    return out
