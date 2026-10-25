"""Discovery and inventory of local models (spec §D.7).

A relay pushes its inventory (``POST /api/relay/v1/inventory``); the direct
pump builds the same payload from its own upstream calls. Both end in
:func:`apply_inventory`, which validates the payload against hard caps
(nothing is applied on a violation) and upserts one ``LLMModel`` per served
model:

- ``model_id = local-<endpoint slug>/<sanitized served name>`` (a collision
  appends ``-<digest[7:13]>``), ``provider = endpoint.slug``, free, price 0;
- ``supports_function_calling`` from the ``/api/show`` capabilities
  (``openai_compatible``: ``endpoint.assume_tools``);
- models missing upstream become ``is_available=False`` (never deleted);
- a digest change records the old digest in ``metadata.digest_history`` and
  pauses experiments whose pending jobs froze the old digest
  (``experiment_automation.pause_for_digest_drift``).

Standby relay sessions only update their own ``models`` list.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from backend.inference.models import LLMEndpoint
from backend.inference.models import RelaySession

if TYPE_CHECKING:
    from backend.llm_models.models import LLMModel

logger = logging.getLogger(__name__)

NAME_RE = re.compile(r"^[\w.:/@+-]+$")
MAX_NAME = 300
MAX_DIGEST = 80
MAX_CAPABILITIES = 16
CAPABILITIES = frozenset({"completion", "tools", "insert", "vision", "embedding", "thinking"})
MAX_LOADED = 64
NEED_SHOW_PER_CALL = 50
MAX_INT = 2**53
MAX_CONTEXT = 10_000_000
_SANITIZE_RE = re.compile(r"[^a-z0-9._-]+")


class InventoryError(ValueError):
    """The payload violates a cap; ``status`` is 413 (too many items) or 422 (invalid)."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass
class InventoryModel:
    name: str
    digest: str = ""
    size: int | None = None
    family: str = ""
    parameter_size: str = ""
    quantization: str = ""
    context_length: int | None = None


@dataclass
class InventoryShow:
    context_length: int | None = None
    capabilities: list[str] = field(default_factory=list)
    template_sha: str = ""


@dataclass
class InventoryPayload:
    version: str
    models: list[InventoryModel]
    show: dict[str, InventoryShow]
    loaded: list[dict[str, Any]]


# ── Validation ──────────────────────────────────────────────────────────


def _str(value: Any, limit: int, what: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)):
        msg = f"{what} must be a string"
        raise InventoryError(msg)
    text = str(value)
    if len(text) > limit:
        msg = f"{what} is longer than {limit} characters"
        raise InventoryError(msg)
    return text


def _int(value: Any, what: str, *, maximum: int = MAX_INT) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        msg = f"{what} must be a number"
        raise InventoryError(msg)
    number = int(value)
    if number < 0 or number > maximum:
        msg = f"{what} is out of range"
        raise InventoryError(msg)
    return number


def _name(value: Any) -> str:
    name = _str(value, MAX_NAME, "model name")
    if not name or not NAME_RE.match(name):
        msg = f"invalid model name {name[:80]!r}"
        raise InventoryError(msg)
    return name


def parse_payload(payload: Any) -> InventoryPayload:
    """Validate an inventory payload (§E.2 inventory); raises :class:`InventoryError`."""
    if not isinstance(payload, dict):
        msg = "inventory must be an object"
        raise InventoryError(msg)
    raw_models = payload.get("models") or []
    if not isinstance(raw_models, list):
        msg = "models must be a list"
        raise InventoryError(msg)
    if len(raw_models) > int(settings.RELAY_INVENTORY_MAX_MODELS):
        msg = f"at most {settings.RELAY_INVENTORY_MAX_MODELS} models per endpoint"
        raise InventoryError(msg, status=413)
    models: list[InventoryModel] = []
    seen: set[str] = set()
    for item in raw_models:
        if not isinstance(item, dict):
            msg = "each model must be an object"
            raise InventoryError(msg)
        name = _name(item.get("name"))
        if name in seen:
            continue
        seen.add(name)
        models.append(
            InventoryModel(
                name=name,
                digest=_str(item.get("digest"), MAX_DIGEST, "digest"),
                size=_int(item.get("size"), "size"),
                family=_str(item.get("family"), 64, "family"),
                parameter_size=_str(item.get("parameter_size"), 32, "parameter_size"),
                quantization=_str(item.get("quantization"), 32, "quantization"),
                context_length=_int(item.get("context_length"), "context_length", maximum=MAX_CONTEXT),
            ),
        )
    raw_show = payload.get("show") or {}
    if not isinstance(raw_show, dict):
        msg = "show must be an object"
        raise InventoryError(msg)
    if len(raw_show) > int(settings.RELAY_INVENTORY_MAX_MODELS):
        msg = "too many show entries"
        raise InventoryError(msg, status=413)
    show: dict[str, InventoryShow] = {}
    for key, value in raw_show.items():
        name = _name(key)
        if not isinstance(value, dict):
            msg = "each show entry must be an object"
            raise InventoryError(msg)
        caps = value.get("capabilities") or []
        if not isinstance(caps, list) or len(caps) > MAX_CAPABILITIES:
            msg = f"capabilities must be a list of at most {MAX_CAPABILITIES} items"
            raise InventoryError(msg)
        show[name] = InventoryShow(
            context_length=_int(value.get("context_length"), "context_length", maximum=MAX_CONTEXT),
            capabilities=sorted({str(c) for c in caps if isinstance(c, str) and c in CAPABILITIES}),
            template_sha=_str(value.get("template_sha"), 80, "template_sha"),
        )
    raw_loaded = payload.get("loaded") or []
    if not isinstance(raw_loaded, list):
        msg = "loaded must be a list"
        raise InventoryError(msg)
    if len(raw_loaded) > MAX_LOADED:
        msg = f"at most {MAX_LOADED} loaded models"
        raise InventoryError(msg, status=413)
    return InventoryPayload(
        version=_str(payload.get("version"), 64, "version"),
        models=models,
        show=show,
        loaded=parse_loaded(raw_loaded),
    )


def parse_loaded(raw: Any) -> list[dict[str, Any]]:
    """Validate an ``/api/ps`` listing ``[{name, digest, size, size_vram, expires_at, context_length}]``."""
    if not isinstance(raw, list):
        return []
    loaded: list[dict[str, Any]] = []
    for item in raw[:MAX_LOADED]:
        if not isinstance(item, dict):
            msg = "each loaded entry must be an object"
            raise InventoryError(msg)
        loaded.append(
            {
                "name": _name(item.get("name")),
                "digest": _str(item.get("digest"), MAX_DIGEST, "digest"),
                "size": _int(item.get("size"), "size"),
                "size_vram": _int(item.get("size_vram"), "size_vram"),
                "expires_at": _str(item.get("expires_at"), 64, "expires_at"),
                "context_length": _int(item.get("context_length"), "context_length", maximum=MAX_CONTEXT),
            },
        )
    return loaded


# ── Identity ────────────────────────────────────────────────────────────


def sanitize_served_name(name: str) -> str:
    return _SANITIZE_RE.sub("-", name.lower()).strip("-") or "model"


def local_model_id(endpoint: LLMEndpoint, served_name: str) -> str:
    return f"local-{endpoint.slug}/{sanitize_served_name(served_name)}"


def _suffix(digest: str, name: str) -> str:
    if digest and len(digest) > 13:
        return digest[7:13]
    return hashlib.sha256(name.encode()).hexdigest()[:6]


def _unique_identity(endpoint: LLMEndpoint, item: InventoryModel) -> tuple[str, str]:
    from backend.llm_models.models import LLMModel
    from backend.llm_models.services.helpers import normalize_model_identifier

    model_id = local_model_id(endpoint, item.name)
    if LLMModel.objects.filter(model_id=model_id).exists():
        model_id = f"{model_id}-{_suffix(item.digest, item.name)}"
    canonical = normalize_model_identifier(model_id)
    if LLMModel.objects.filter(canonical_slug=canonical).exists():
        canonical = f"{canonical}-{hashlib.sha256(item.name.encode()).hexdigest()[:6]}"
    return model_id, canonical


# ── Apply ───────────────────────────────────────────────────────────────


@dataclass
class InventoryResult:
    accepted_models: int
    need_show: list[str]
    digest_changed: list[int]
    added: int = 0
    removed: int = 0


def _context_window(item: InventoryModel, show: InventoryShow | None, current: int) -> int:
    for value in (show.context_length if show else None, item.context_length):
        if value:
            return int(value)
    return current


def apply_inventory(endpoint: LLMEndpoint, session: RelaySession | None, payload: Any) -> InventoryResult:
    """Validate and apply an inventory push for ``endpoint`` (§D.7); raises :class:`InventoryError`."""
    data = parse_payload(payload)
    names = [{"name": m.name, "digest": m.digest} for m in data.models]
    if session is not None and session.role != RelaySession.Role.ACTIVE:
        RelaySession.objects.filter(pk=session.pk).update(models=names)
        return InventoryResult(accepted_models=len(data.models), need_show=[], digest_changed=[])

    from backend.llm_models.models import LLMModel

    now = timezone.now()
    ollama = endpoint.kind == LLMEndpoint.Kind.OLLAMA
    digest_changed: list[int] = []
    need_show: list[str] = []
    added = 0
    with transaction.atomic():
        LLMEndpoint.objects.select_for_update().filter(pk=endpoint.pk).first()
        existing = {m.served_model_name: m for m in LLMModel.objects.filter(endpoint=endpoint)}
        for item in data.models:
            show = data.show.get(item.name)
            row = existing.get(item.name)
            if row is None:
                model_id, canonical = _unique_identity(endpoint, item)
                row = LLMModel(
                    endpoint=endpoint,
                    served_model_name=item.name,
                    model_id=model_id,
                    canonical_slug=canonical,
                    model_name=item.name,
                    provider=endpoint.slug,
                    description="",
                    is_free=True,
                    max_output_tokens=0,
                    input_price_per_token=0.0,
                    output_price_per_token=0.0,
                    cost_efficiency=1.0,
                    supports_streaming=True,
                    metadata={},
                    capabilities_json={},
                )
                added += 1
            elif row.digest and item.digest and row.digest != item.digest:
                history = list((row.metadata or {}).get("digest_history") or [])
                history.append({"digest": row.digest, "replaced_at": now.isoformat()})
                row.metadata = {**(row.metadata or {}), "digest_history": history[-20:]}
                if row.pk:
                    digest_changed.append(row.pk)
            row.digest = item.digest
            row.family = item.family or row.family
            row.parameter_size = item.parameter_size or row.parameter_size
            row.quantization = item.quantization or row.quantization
            row.context_window = _context_window(item, show, row.context_window)
            row.is_available = True
            meta = dict(row.metadata or {})
            ollama_meta = dict(meta.get("ollama") or {})
            if item.size is not None:
                ollama_meta["size"] = item.size
            if show is not None:
                ollama_meta.update(
                    {
                        "capabilities": show.capabilities,
                        "template_sha": show.template_sha,
                        "show_digest": item.digest,
                    },
                )
            meta["ollama"] = ollama_meta
            row.metadata = meta
            caps = ollama_meta.get("capabilities")
            if ollama:
                if isinstance(caps, list):
                    row.supports_function_calling = "tools" in caps
                    row.supports_vision = "vision" in caps
                if ollama_meta.get("show_digest") != item.digest:
                    need_show.append(item.name)
            else:
                row.supports_function_calling = bool(endpoint.assume_tools) or row.supports_function_calling
            row.save()
        served = {m.name for m in data.models}
        removed = (
            LLMModel.objects.filter(endpoint=endpoint, is_available=True)
            .exclude(served_model_name__in=served)
            .update(is_available=False)
        )
        update: dict[str, Any] = {
            "last_inventory_at": now,
            "inventory_resync_requested": False,
            "loaded_models": [{**m, "at": now.isoformat()} for m in data.loaded],
        }
        if data.version:
            update["server_version"] = data.version
        LLMEndpoint.objects.filter(pk=endpoint.pk).update(**update)
        if session is not None:
            RelaySession.objects.filter(pk=session.pk).update(models=names)
    from backend.inference.events import publish_endpoint_state

    endpoint_id = endpoint.pk
    transaction.on_commit(lambda: publish_endpoint_state(endpoint_id))
    if digest_changed:
        changed = list(digest_changed)
        transaction.on_commit(lambda: _pause_for_drift(changed))
    return InventoryResult(
        accepted_models=len(data.models),
        need_show=need_show[:NEED_SHOW_PER_CALL],
        digest_changed=digest_changed,
        added=added,
        removed=removed,
    )


def update_loaded(endpoint: LLMEndpoint, loaded: Any) -> None:
    """Store a fresh ``/api/ps`` listing (sent with a relay poll)."""
    parsed = parse_loaded(loaded)
    now = timezone.now().isoformat()
    LLMEndpoint.objects.filter(pk=endpoint.pk).update(loaded_models=[{**m, "at": now} for m in parsed])


def _pause_for_drift(model_ids: list[int]) -> None:
    from backend.generation.services.experiment_automation import pause_for_digest_drift
    from backend.llm_models.models import LLMModel

    for model in LLMModel.objects.filter(pk__in=model_ids):
        try:
            pause_for_digest_drift(model)
        except NotImplementedError:
            logger.warning("pause_for_digest_drift is not implemented yet; %s changed digest", model.model_id)
        except Exception:
            logger.exception("Pausing experiments after a digest change of %s failed", model.model_id)


def models_for_display(endpoint: LLMEndpoint) -> list[LLMModel]:
    from backend.llm_models.models import LLMModel

    return list(LLMModel.objects.filter(endpoint=endpoint).order_by("served_model_name"))
