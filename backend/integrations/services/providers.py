"""One-click cloud LLM providers: presets for OpenAI-compatible APIs and the connect flow.

A provider connection is an ordinary direct ``openai_compatible``
:class:`~backend.inference.models.LLMEndpoint` (staff only, like every direct
endpoint) whose ``base_url`` and caps come from a :class:`ProviderPreset`.
Keys are checked with ``GET /v1/models`` through :func:`inference.upstream.call`,
so the SSRF/TLS rules of direct endpoints apply unchanged. After the
endpoint is created its inventory is refreshed once and the models the
preset does not consider chat/code models (embeddings, TTS, image, ...) are
hidden. Later inventory refreshes (the pump, every 120 s) never touch
``is_hidden`` on existing rows, but models a provider adds later appear
visible.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field

from backend.common.exceptions import APIError
from backend.common.exceptions import NotFoundError
from backend.common.exceptions import ValidationFailed
from backend.inference import upstream
from backend.inference.models import LLMEndpoint

KEY_CHECK_TIMEOUT_S = 20.0
MAX_KEY_LENGTH = 1024
_KEY_RE = re.compile(r"^[\x21-\x7e]+$")  # printable ASCII, no whitespace (it goes into a header)

# Model ids that are never chat/code models, whatever the provider.
_NON_CHAT = re.compile(
    r"embed|\bbge-|\be5-|\bgte-|nv-?clip|rerank|whisper|transcri|tts|speech|audio|realtime|dall-?e|gpt-image|"
    r"imagen|image-gen|-image|flux|stable-diffusion|sdxl|playground-v|sora|veo|moderation|guard|"
    r"davinci|babbage|aqa|ocr|parakeet|riva|deplot|kosmos|neva|vila|paligemma|fuyu",
    re.IGNORECASE,
)


def _chat_filter(include: str | None = None, exclude: str | None = None) -> Callable[[str], bool]:
    """A predicate: not a known non-chat model, matches ``include`` (if set) and not ``exclude`` (if set)."""
    inc = re.compile(include, re.IGNORECASE) if include else None
    exc = re.compile(exclude, re.IGNORECASE) if exclude else None

    def accepts(model_id: str) -> bool:
        if _NON_CHAT.search(model_id):
            return False
        if inc is not None and not inc.search(model_id):
            return False
        return not (exc is not None and exc.search(model_id))

    return accepts


@dataclass(frozen=True)
class ProviderPreset:
    id: str
    name: str
    base_url: str
    description: str
    docs_url: str
    keys_url: str
    max_concurrency: int = 4
    request_timeout_s: int = 600
    is_chat_model: Callable[[str], bool] = field(default_factory=_chat_filter, repr=False)

    def chat_models(self, model_ids: list[str]) -> list[str]:
        return [m for m in model_ids if self.is_chat_model(m)]


PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        id="anthropic",
        name="Anthropic",
        base_url="https://api.anthropic.com/v1",
        description="Claude API / Claude Console: Claude models through Anthropic's OpenAI-compatible API.",
        docs_url="https://docs.claude.com/en/api/openai-sdk",
        keys_url="https://console.anthropic.com/settings/keys",
        max_concurrency=4,
        is_chat_model=_chat_filter(include=r"^claude"),
    ),
    ProviderPreset(
        id="openai",
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        description="OpenAI / ChatGPT API: GPT and o-series models.",
        docs_url="https://platform.openai.com/docs/api-reference/chat",
        keys_url="https://platform.openai.com/api-keys",
        max_concurrency=8,
        is_chat_model=_chat_filter(include=r"^(gpt-|o\d|chatgpt|codex)", exclude=r"-instruct|search|computer-use"),
    ),
    ProviderPreset(
        id="gemini",
        name="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        description="Gemini and Gemma models through the Gemini API's OpenAI compatibility layer.",
        docs_url="https://ai.google.dev/gemini-api/docs/openai",
        keys_url="https://aistudio.google.com/apikey",
        max_concurrency=4,
        is_chat_model=_chat_filter(include=r"gemini|gemma", exclude=r"live|native-audio"),
    ),
    ProviderPreset(
        id="mistral",
        name="Mistral AI",
        base_url="https://api.mistral.ai/v1",
        description="Mistral, Codestral and Devstral models from La Plateforme.",
        docs_url="https://docs.mistral.ai/api/",
        keys_url="https://console.mistral.ai/api-keys",
        max_concurrency=4,
    ),
    ProviderPreset(
        id="groq",
        name="Groq",
        base_url="https://api.groq.com/openai/v1",
        description="Fast inference of open-weight models (Llama, Qwen, GPT-OSS, Kimi) on Groq LPUs.",
        docs_url="https://console.groq.com/docs/openai",
        keys_url="https://console.groq.com/keys",
        max_concurrency=4,
    ),
    ProviderPreset(
        id="deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        description="DeepSeek chat and reasoner models.",
        docs_url="https://api-docs.deepseek.com/",
        keys_url="https://platform.deepseek.com/api_keys",
        max_concurrency=4,
        is_chat_model=_chat_filter(include=r"^deepseek"),
    ),
    ProviderPreset(
        id="xai",
        name="xAI",
        base_url="https://api.x.ai/v1",
        description="Grok models from xAI.",
        docs_url="https://docs.x.ai/docs/api-reference",
        keys_url="https://console.x.ai/",
        max_concurrency=4,
        is_chat_model=_chat_filter(include=r"^grok", exclude=r"imagine|vision-beta"),
    ),
    ProviderPreset(
        id="together",
        name="Together AI",
        base_url="https://api.together.xyz/v1",
        description="Hosted open-weight models (Llama, Qwen, DeepSeek, Mistral, ...) on Together AI.",
        docs_url="https://docs.together.ai/docs/openai-api-compatibility",
        keys_url="https://api.together.ai/settings/api-keys",
        max_concurrency=4,
        is_chat_model=_chat_filter(exclude=r"m2-bert|cartesia|retrieval|schnell|kontext|lora"),
    ),
    ProviderPreset(
        id="fireworks",
        name="Fireworks AI",
        base_url="https://api.fireworks.ai/inference/v1",
        description="Hosted open-weight models on Fireworks AI.",
        docs_url="https://docs.fireworks.ai/tools-sdks/openai-compatibility",
        keys_url="https://app.fireworks.ai/settings/users/api-keys",
        max_concurrency=4,
        is_chat_model=_chat_filter(exclude=r"playground|japanese-stable|ssd-1b|segmind"),
    ),
    ProviderPreset(
        id="cerebras",
        name="Cerebras",
        base_url="https://api.cerebras.ai/v1",
        description="Very fast inference of open-weight models on Cerebras hardware.",
        docs_url="https://inference-docs.cerebras.ai/resources/openai",
        keys_url="https://cloud.cerebras.ai/",
        max_concurrency=4,
    ),
    ProviderPreset(
        id="nvidia",
        name="NVIDIA NIM",
        base_url="https://integrate.api.nvidia.com/v1",
        description="NVIDIA-hosted NIM microservices for open-weight models (build.nvidia.com).",
        docs_url="https://docs.api.nvidia.com/nim/reference/llm-apis",
        keys_url="https://build.nvidia.com/settings/api-keys",
        max_concurrency=2,
        is_chat_model=_chat_filter(exclude=r"nemoretriever|reward|safety|cosmos|ising|-pii|detector|calibration"),
    ),
)

_BY_ID = {p.id: p for p in PRESETS}


def get_preset(preset_id: str) -> ProviderPreset:
    try:
        return _BY_ID[preset_id]
    except KeyError:
        msg = f"Unknown provider {preset_id!r}."
        raise NotFoundError(msg) from None


def _norm_url(url: str) -> str:
    return (url or "").strip().rstrip("/").lower()


def connected_endpoints(user, preset: ProviderPreset):
    """The user's own endpoints that point at ``preset``'s API (by base URL)."""
    target = _norm_url(preset.base_url)
    rows = LLMEndpoint.objects.filter(owner=user, is_system=False, connection=LLMEndpoint.Connection.DIRECT)
    return [e for e in rows.order_by("pk") if _norm_url(e.base_url) == target]


def require_staff(user) -> None:
    # Same rule as create_endpoint: direct endpoints are staff-only (the executor calls base_url).
    if not getattr(user, "is_staff", False):
        msg = "Only staff can connect cloud providers (they are direct endpoints)."
        raise APIError(msg, status_code=403)


def clean_key(api_key: str) -> str:
    key = (api_key or "").strip()
    if not key:
        msg = "An API key is required."
        raise ValidationFailed(msg)
    if len(key) > MAX_KEY_LENGTH or not _KEY_RE.match(key):
        msg = "The API key must be a single line of printable characters without spaces."
        raise ValidationFailed(msg)
    return key


def _redact(text: str, api_key: str) -> str:
    return text.replace(api_key, "[redacted]") if api_key else text


def validate_key(preset: ProviderPreset, api_key: str) -> tuple[bool, str, list[str]]:
    """``GET /v1/models`` with ``api_key`` → ``(ok, message, model_ids)``; ``model_ids`` lists every model.

    The message never contains the key.
    """
    key = clean_key(api_key)
    try:
        resp = upstream.call(preset.base_url, "GET", "/v1/models", timeout=KEY_CHECK_TIMEOUT_S, api_key=key)
    except upstream.UpstreamError as exc:
        return False, _redact(f"Cannot reach {preset.name}: {exc}", key), []
    if resp.status in (401, 403):
        return False, f"{preset.name} rejected this API key (HTTP {resp.status}).", []
    if resp.status >= 400:
        detail = _redact(resp.text[:200], key)
        return False, f"{preset.name} answered HTTP {resp.status}: {detail}", []
    data = resp.payload.get("data") if isinstance(resp.payload, dict) else None
    if not isinstance(data, list):
        return False, f"{preset.name} returned an unexpected model list.", []
    ids = [str(m["id"]) for m in data if isinstance(m, dict) and m.get("id")]
    chat = preset.chat_models(ids)
    if not chat:
        return True, f"The key works, but {preset.name} lists no chat models for it.", ids
    return True, f"The key works: {len(chat)} chat models available.", ids


@dataclass
class ConnectResult:
    endpoint: LLMEndpoint
    total_models: int
    visible_models: int
    hidden_models: int
    inventory_error: str


def connect(user, preset: ProviderPreset, api_key: str, name: str | None = None) -> ConnectResult:
    """Validate ``api_key``, create the direct endpoint, refresh its inventory and hide non-chat models.

    Raises ``APIError`` 403 for non-staff and ``ValidationFailed`` (400) for a rejected key.
    """
    from backend.inference.api.views import _set_api_key
    from backend.inference.api.views import _unique_slug
    from backend.inference.pump import refresh_inventory
    from backend.llm_models.models import LLMModel

    require_staff(user)
    key = clean_key(api_key)
    ok, message, _ids = validate_key(preset, key)
    if not ok:
        raise ValidationFailed(message)
    endpoint = LLMEndpoint(
        slug=_unique_slug(preset.id),
        name=(name or "").strip()[:120] or preset.name,
        kind=LLMEndpoint.Kind.OPENAI_COMPATIBLE,
        connection=LLMEndpoint.Connection.DIRECT,
        api_flavor=LLMEndpoint.ApiFlavor.OPENAI,
        base_url=preset.base_url,
        owner=user,
        visibility=LLMEndpoint.Visibility.PRIVATE,
        max_concurrency=preset.max_concurrency,
        request_timeout_s=preset.request_timeout_s,
        assume_tools=True,
    )
    _set_api_key(endpoint, key)
    endpoint.save()  # the pump manager picks it up within its next tick
    refresh_inventory(endpoint)
    rows = list(LLMModel.objects.filter(endpoint=endpoint, is_available=True))
    hide = [m.pk for m in rows if not preset.is_chat_model(m.served_model_name)]
    if hide:
        LLMModel.objects.filter(pk__in=hide).update(is_hidden=True)
    endpoint.refresh_from_db()
    return ConnectResult(
        endpoint=endpoint,
        total_models=len(rows),
        visible_models=len(rows) - len(hide),
        hidden_models=len(hide),
        inventory_error=_redact(endpoint.last_error, key),
    )
