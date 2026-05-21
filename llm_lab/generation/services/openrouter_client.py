"""OpenRouter chat completions API client."""

import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

OPENROUTER_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_TIMEOUT = 600
MAX_RETRIES = 3
RETRY_BACKOFF = 2

HTTP_429_RATE_LIMITED = 429
HTTP_500_SERVER_ERROR = 500
HTTP_200_OK = 200
HTTP_408_TIMEOUT = 408


class OpenRouterError(Exception):
    """Base error for OpenRouter API calls."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        *,
        user_facing_message: str | None = None,
        remediation: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.user_facing_message = user_facing_message
        self.remediation = remediation

    def display(self) -> str:
        """Build a user-facing one-liner combining message + remediation."""
        parts = [self.user_facing_message or str(self)]
        if self.remediation:
            parts.append(self.remediation)
        return " ".join(parts)


_AUTH_HINT_FRAGMENTS = (
    "user not found",
    "invalid api key",
    "no auth credentials",
    "no user found",
)


def _build_auth_error(status_code: int, body: str) -> OpenRouterError:
    """Build a friendly :class:`OpenRouterError` for an auth failure."""
    return OpenRouterError(
        f"API error {status_code}: {body[:500]}",
        status_code=status_code,
        user_facing_message=("OpenRouter rejected the API key used for this request."),
        remediation=("Update or rotate your OpenRouter API key in Settings → API Access."),
    )


class OpenRouterClient:
    """Client for OpenRouter chat completions API."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.OPENROUTER_API_KEY
        if not self.api_key:
            msg = "OPENROUTER_API_KEY not configured"
            raise OpenRouterError(msg)

    def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict],
        temperature: float = 0.3,
        max_tokens: int = 32000,
        top_p: float | None = None,
        frequency_penalty: float | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> dict:
        """Send a chat completion request to OpenRouter.

        Args:
            model: OpenRouter model ID (e.g. "anthropic/claude-3-5-sonnet")
            messages: List of message dicts with "role" and "content" keys
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens in the response
            top_p: Nucleus sampling parameter
            frequency_penalty: Frequency penalty parameter
            timeout: Request timeout in seconds

        Returns:
            Full API response dict with choices, usage, etc.

        Raises:
            OpenRouterError: On API errors or timeouts
        """
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if top_p is not None:
            payload["top_p"] = top_p
        if frequency_penalty is not None:
            payload["frequency_penalty"] = frequency_penalty

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": settings.OPENROUTER_HTTP_REFERER,
            "X-Title": "LLM Eval Lab",
        }

        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                logger.info(
                    "OpenRouter request: model=%s attempt=%d/%d",
                    model,
                    attempt + 1,
                    MAX_RETRIES,
                )
                response = requests.post(
                    OPENROUTER_COMPLETIONS_URL,
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                )

                if response.status_code == HTTP_429_RATE_LIMITED:
                    # Rate limited — back off and retry
                    wait = RETRY_BACKOFF ** (attempt + 1)
                    logger.warning("Rate limited, waiting %ds", wait)
                    time.sleep(wait)
                    continue

                if response.status_code >= HTTP_500_SERVER_ERROR:
                    wait = RETRY_BACKOFF ** (attempt + 1)
                    logger.warning(
                        "Server error %d, retrying in %ds",
                        response.status_code,
                        wait,
                    )
                    time.sleep(wait)
                    continue

                if response.status_code != HTTP_200_OK:
                    error_body = response.text[:500]
                    if response.status_code == 401 or (
                        response.status_code == 403 and any(f in error_body.lower() for f in _AUTH_HINT_FRAGMENTS)
                    ):
                        raise _build_auth_error(response.status_code, error_body)
                    msg = f"API error {response.status_code}: {error_body}"
                    raise OpenRouterError(
                        msg,
                        status_code=response.status_code,
                    )

            except requests.exceptions.Timeout:
                last_error = OpenRouterError(
                    f"Request timed out after {timeout}s",
                    status_code=HTTP_408_TIMEOUT,
                )
                logger.warning("Timeout on attempt %d", attempt + 1)
            except requests.exceptions.ConnectionError as exc:
                last_error = OpenRouterError(
                    f"Connection error: {exc}",
                )
                wait = RETRY_BACKOFF ** (attempt + 1)
                logger.warning("Connection error, retrying in %ds", wait)
                time.sleep(wait)
            else:
                data = response.json()
                usage = data.get("usage", {})
                logger.info(
                    "OpenRouter response: model=%s prompt_tokens=%s completion_tokens=%s finish_reason=%s",
                    model,
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                    data.get("choices", [{}])[0].get("finish_reason"),
                )
                return data

        if last_error is None:
            msg = "All retry attempts failed"
            last_error = OpenRouterError(msg)
        raise last_error

    @staticmethod
    def extract_content(response: dict) -> str:
        """Extract the text content from a chat completion response."""
        choices = response.get("choices", [])
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content", "")

    @staticmethod
    def extract_usage(response: dict) -> dict:
        """Extract token usage from a response."""
        usage = response.get("usage", {})
        return {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }

    @staticmethod
    def is_truncated(response: dict) -> bool:
        """Check if the response was truncated (hit token limit)."""
        choices = response.get("choices", [])
        if not choices:
            return False
        return choices[0].get("finish_reason") == "length"

    @staticmethod
    def estimate_cost(
        prompt_tokens: int,
        completion_tokens: int,
        input_price_per_token: float,
        output_price_per_token: float,
    ) -> float:
        """Calculate estimated cost for a request."""
        return (prompt_tokens * input_price_per_token) + (completion_tokens * output_price_per_token)
