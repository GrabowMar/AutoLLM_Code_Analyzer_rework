"""OpenRouter Data API client for the MSS v2 inputs (popularity, credit).

* ``datasets/rankings-daily`` and ``classifications/task`` are the licensed
  Data API (CC BY 4.0; cite "Source: OpenRouter (openrouter.ai/rankings), as of
  <meta.as_of>"). They need any OpenRouter key; limits are 30 requests/min and
  500/day, so a 28-day window is fetched in four 7-day chunks.
* ``/api/v1/models`` is public (no key sent): ``canonical_slug`` maps a
  permaslug to a model id, ``created`` dates the exposure window.
* ``/api/v1/credits`` gives the account's remaining credit (Amendment A1,
  B-openrouter budget).

The key is read from ``UserApiCredential`` (provider ``openrouter``) with
``get_secret()``. It is never printed or logged, and the ``Authorization``
header is attached only when the request host is ``openrouter.ai``. Every
request has a timeout; 429 and 5xx answers are retried with backoff.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://openrouter.ai/api/v1/"
OPENROUTER_HOST = "openrouter.ai"
LICENSE = "CC BY 4.0"
CITATION = "Source: OpenRouter (openrouter.ai/rankings), as of {as_of}"
TIMEOUT = (10, 60)  # connect, read seconds
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = 4.0
CHUNK_DAYS = 7


class OpenRouterError(RuntimeError):
    """A Data API request failed (the message never contains the key)."""


def openrouter_key(user_id: int = 1) -> str | None:
    """The stored OpenRouter key of ``user_id`` (``None`` when there is none)."""
    from backend.credentials.models import UserApiCredential

    cred = UserApiCredential.objects.filter(user_id=user_id, provider="openrouter").first()
    if cred is None:
        return None
    secret = cred.get_secret()
    return secret or None


def _headers(url: str, key: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    if key and (urlsplit(url).hostname or "").lower() == OPENROUTER_HOST:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def get_json(
    path: str,
    *,
    params: dict[str, Any] | None = None,
    key: str | None = None,
    session: Any = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """GET ``API_BASE + path`` and return the decoded JSON body (retries 429/5xx)."""
    url = path if path.startswith("https://") else API_BASE + path
    http = session or requests
    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = http.get(url, params=params, headers=_headers(url, key), timeout=TIMEOUT)
        except requests.RequestException as exc:
            last_error = type(exc).__name__
            logger.warning("OpenRouter request failed: %s (%s), attempt %s", url, last_error, attempt)
        else:
            if resp.status_code == 200:
                try:
                    body = resp.json()
                except ValueError as exc:
                    msg = f"{url}: response is not JSON"
                    raise OpenRouterError(msg) from exc
                if not isinstance(body, dict):
                    msg = f"{url}: unexpected response shape"
                    raise OpenRouterError(msg)
                return body
            last_error = f"HTTP {resp.status_code}"
            if resp.status_code != 429 and resp.status_code < 500:
                msg = f"{url}: {last_error}"
                raise OpenRouterError(msg)
            logger.warning("OpenRouter %s on %s, attempt %s", last_error, url, attempt)
        if attempt < MAX_ATTEMPTS:
            sleep(BACKOFF_SECONDS * attempt)
    msg = f"{url}: {last_error} after {MAX_ATTEMPTS} attempts"
    raise OpenRouterError(msg)


def window_chunks(start: dt.date, end: dt.date, days: int = CHUNK_DAYS) -> list[tuple[dt.date, dt.date]]:
    """Inclusive ``[start, end]`` split into consecutive chunks of at most ``days`` days."""
    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + dt.timedelta(days=days - 1))
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + dt.timedelta(days=1)
    return chunks


def fetch_rankings_daily(
    start: dt.date,
    end: dt.date,
    *,
    key: str,
    session: Any = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """``datasets/rankings-daily`` for ``[start, end]`` in 7-day chunks: one ``{params, body}`` per chunk."""
    out = []
    for chunk_start, chunk_end in window_chunks(start, end):
        params = {"start_date": chunk_start.isoformat(), "end_date": chunk_end.isoformat()}
        body = get_json("datasets/rankings-daily", params=params, key=key, session=session, sleep=sleep)
        out.append({"params": params, "body": body})
    return out


def fetch_task_classifications(
    *,
    key: str,
    window: str = "7d",
    session: Any = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    params = {"window": window}
    return {
        "params": params,
        "body": get_json("classifications/task", params=params, key=key, session=session, sleep=sleep),
    }


def fetch_models_index(*, session: Any = None, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """The public model list (no key sent)."""
    return {"params": {}, "body": get_json("models", session=session, sleep=sleep)}


def trim_models_index(body: dict[str, Any]) -> list[dict[str, Any]]:
    """``[{id, canonical_slug, created}]`` from a ``/models`` body (what the popularity import needs)."""
    rows = body.get("data") if isinstance(body, dict) else None
    out = []
    for row in rows or []:
        if isinstance(row, dict) and row.get("id"):
            out.append({"id": row["id"], "canonical_slug": row.get("canonical_slug"), "created": row.get("created")})
    return out


def fetch_credits(*, key: str, session: Any = None, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Remaining OpenRouter credit: ``{total_credits, total_usage, available, fetched_at}``."""
    body = get_json("credits", key=key, session=session, sleep=sleep)
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    try:
        total = float(data.get("total_credits"))
        usage = float(data.get("total_usage"))
    except (TypeError, ValueError) as exc:
        msg = "credits: unexpected response shape"
        raise OpenRouterError(msg) from exc
    return {
        "total_credits": total,
        "total_usage": usage,
        "available": round(total - usage, 4),
        "fetched_at": dt.datetime.now(tz=dt.UTC).isoformat(),
        "source": "GET https://openrouter.ai/api/v1/credits",
    }
