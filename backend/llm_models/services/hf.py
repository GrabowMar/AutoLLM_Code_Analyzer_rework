"""Hugging Face Hub access: which token to use, and a paced, redirect-safe HTTP client.

Token precedence (:func:`hf_token_for`): the given user's stored
``huggingface`` credential, then ``settings.HUGGINGFACE_API_TOKEN``, then
anonymous (``None``). A token raises the Hub's API quota (observed
1000 requests / 300 s with a token vs 500 anonymous); it does not unlock
gated repos whose license the token's account has not accepted.

:class:`HfClient` sends the ``Authorization`` header only to
``huggingface.co`` (redirects are followed by hand so a CDN hop never sees
it), paces requests, honours ``429 Retry-After`` and the ``RateLimit``
header, and reports 401/403 as ``gated_inaccessible`` instead of failing.
The token is never logged, stored or returned.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any
from urllib.parse import urljoin
from urllib.parse import urlsplit

import requests
from django.conf import settings

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

HF_HOST = "huggingface.co"
HF_BASE_URL = f"https://{HF_HOST}"

# Pause between requests: >= 0.35 s with a token (1000 / 300 s), wider anonymous (500 / 300 s).
MIN_INTERVAL_WITH_TOKEN = 0.35
MIN_INTERVAL_ANONYMOUS = 0.65
REQUEST_TIMEOUT = 20
MAX_REDIRECTS = 5
DEFAULT_RETRY_AFTER = 60.0
# Proactive back-off once the window has this few requests left.
RATE_LIMIT_LOW_WATER = 2

STATUS_OK = "ok"
STATUS_NOT_FOUND = "not_found"
STATUS_GATED = "gated_inaccessible"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_ERROR = "error"

_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_RETRYABLE_CODES = frozenset({500, 502, 503, 504})
_KEPT_HEADERS = ("x-repo-commit", "etag", "link", "x-linked-size", "x-error-code")
_RATELIMIT_RE = re.compile(r"\br=(\d+)\b.*?\bt=(\d+)\b")


def hf_token_for(user=None) -> str | None:
    """The Hugging Face token to use for ``user`` (never logged or stored).

    The user's own ``huggingface`` credential first, then the deployment-wide
    ``HUGGINGFACE_API_TOKEN`` setting, else ``None`` (anonymous).
    """
    if user is not None:
        from backend.credentials.models import Provider
        from backend.credentials.services.resolver import get_user_credential_for

        cred = get_user_credential_for(user, Provider.HUGGINGFACE)
        if cred is not None:
            try:
                secret = (cred.get_secret() or "").strip()
            except Exception:  # noqa: BLE001 — an undecryptable row means "no usable token"
                secret = ""
            if secret:
                return secret
    token = str(getattr(settings, "HUGGINGFACE_API_TOKEN", "") or "").strip()
    return token or None


def is_hf_url(url: str) -> bool:
    """True for https URLs on huggingface.co (or a subdomain): the only hosts that may see the token."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host == HF_HOST or host.endswith(f".{HF_HOST}"))


@dataclass
class HfResult:
    """One Hub response, already classified (``status`` is one of the ``STATUS_*`` values)."""

    status: str
    http_status: int | None = None
    data: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    retry_after: float | None = None

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    @property
    def repo_commit(self) -> str | None:
        return self.headers.get("x-repo-commit") or None


def _float_header(value: Any) -> float | None:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


class HfClient:
    """Paced GETs against the Hub; see the module docstring for the guarantees.

    ``max_retry_wait`` bounds how long one 429 may block (interactive callers
    pass a few seconds; batch enrichment waits out a whole window).
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        min_interval: float | None = None,
        max_retry_wait: float = 310.0,
        max_retries: int = 2,
        timeout: float = REQUEST_TIMEOUT,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._token = (token or "").strip() or None
        self.min_interval = (
            min_interval
            if min_interval is not None
            else (MIN_INTERVAL_WITH_TOKEN if self._token else MIN_INTERVAL_ANONYMOUS)
        )
        self.max_retry_wait = max_retry_wait
        self.max_retries = max_retries
        self.timeout = timeout
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._next_allowed = 0.0
        self.requests_made = 0
        self.rate_limited = 0
        self.last_rate_limit: dict[str, int] | None = None

    @property
    def authenticated(self) -> bool:
        return self._token is not None

    def __repr__(self) -> str:  # never show the token
        return f"HfClient(authenticated={self.authenticated}, requests={self.requests_made})"

    # ── pacing ──────────────────────────────────────────────────────────

    def _wait(self, seconds: float) -> None:
        if seconds > 0:
            self._sleep(seconds)

    def _pace(self) -> None:
        now = self._clock()
        if now < self._next_allowed:
            self._wait(self._next_allowed - now)
        self._next_allowed = self._clock() + self.min_interval

    def _note_rate_limit(self, headers) -> None:
        """Read ``RateLimit: "api";r=<remaining>;t=<reset s>`` and back off before the window runs dry."""
        raw = headers.get("RateLimit") or headers.get("ratelimit") or ""
        match = _RATELIMIT_RE.search(str(raw))
        if not match:
            return
        remaining, reset = int(match.group(1)), int(match.group(2))
        self.last_rate_limit = {"remaining": remaining, "reset_seconds": reset}
        if remaining <= RATE_LIMIT_LOW_WATER:
            wait = min(float(reset) + 1.0, self.max_retry_wait)
            self._next_allowed = max(self._next_allowed, self._clock() + wait)

    # ── requests ────────────────────────────────────────────────────────

    def _headers_for(self, url: str, accept: str) -> dict[str, str]:
        headers = {"Accept": accept, "User-Agent": "llm-lab-model-enrichment"}
        if self._token and is_hf_url(url):
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _send(self, url: str, params: dict | list | None, accept: str):
        """GET with redirects followed by hand (the token is re-decided per hop)."""
        current, current_params = url, params
        for _ in range(MAX_REDIRECTS + 1):
            self._pace()  # per hop: a resolve redirect lands on /api/resolve-cache, which counts too
            resp = requests.get(
                current,
                params=current_params,
                headers=self._headers_for(current, accept),
                timeout=self.timeout,
                allow_redirects=False,
            )
            self.requests_made += 1
            location = resp.headers.get("Location") if resp.headers else None
            if resp.status_code in _REDIRECT_CODES and location:
                current, current_params = urljoin(current, location), None
                continue
            return resp
        msg = "too many redirects"
        raise requests.TooManyRedirects(msg)

    def get_json(self, url: str, params: dict | list | None = None) -> HfResult:  # noqa: PLR0911
        """GET ``url`` and parse JSON; never raises (failures are classified into ``status``)."""
        retries_left = self.max_retries
        while True:
            try:
                resp = self._send(url, params, "application/json")
            except requests.RequestException as exc:
                logger.warning("Hugging Face request failed: %s (%s)", url, type(exc).__name__)
                return HfResult(STATUS_ERROR)
            headers = {k: str(resp.headers.get(k)) for k in _KEPT_HEADERS if resp.headers.get(k)}
            self._note_rate_limit(resp.headers or {})
            code = resp.status_code
            if code == 429:
                self.rate_limited += 1
                wait = _float_header(resp.headers.get("Retry-After"))
                if wait is None and self.last_rate_limit:
                    wait = float(self.last_rate_limit["reset_seconds"]) + 1.0
                wait = DEFAULT_RETRY_AFTER if wait is None else wait
                if retries_left > 0 and wait <= self.max_retry_wait:
                    retries_left -= 1
                    logger.info("Hugging Face rate limit hit; waiting %.0f s", wait)
                    self._wait(wait)
                    continue
                # Not waited out here: hold the next request back instead of hammering the window.
                self._next_allowed = max(self._next_allowed, self._clock() + min(wait, self.max_retry_wait))
                return HfResult(STATUS_RATE_LIMITED, code, headers=headers, retry_after=wait)
            if code in _RETRYABLE_CODES and retries_left > 0:
                retries_left -= 1
                self._wait(2.0)
                continue
            if code in (401, 403):
                # The Hub answers 401 for missing repos too (it hides private ones).
                status = STATUS_NOT_FOUND if headers.get("x-error-code") == "RepoNotFound" else STATUS_GATED
                return HfResult(status, code, headers=headers)
            if code == 404:
                return HfResult(STATUS_NOT_FOUND, code, headers=headers)
            if code >= 400:
                logger.warning("Hugging Face request failed: %s (HTTP %s)", url, code)
                return HfResult(STATUS_ERROR, code, headers=headers)
            try:
                data = resp.json()
            except ValueError:
                return HfResult(STATUS_ERROR, code, headers=headers)
            return HfResult(STATUS_OK, code, data=data, headers=headers)


def client_for(user=None, **kwargs) -> HfClient:
    """An :class:`HfClient` carrying ``user``'s token (or the settings token, or none)."""
    return HfClient(hf_token_for(user), **kwargs)
