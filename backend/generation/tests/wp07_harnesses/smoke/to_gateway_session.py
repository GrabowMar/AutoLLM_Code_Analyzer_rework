"""Turn a scripted-upstream smoke log into a WP-06 session fixture (``docs/GATEWAY.md#session-fixture-format``).

The upstream answers are re-encoded with the same functions ``scripted_upstream.py`` served them with, so
each call carries the native request, the native SSE events and the expected accounting.

    python3 to_gateway_session.py <harness> <logs/upstream.jsonl> <out.json>
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scripted_upstream as up

# What the gateway pins upstream in WP-06's replay test, so the native response echoes the pinned model.
UPSTREAM_MODEL = {"anthropic_messages": "anthropic/claude-sonnet-4.6", "openai_responses": "openai/gpt-5.1-codex"}
SOURCES = {"claude-code": "x-claude-code-request-class", "codex": "default:main"}
_SKIP_HEADERS = {"content-length", "host", "accept-encoding", "connection", "x-api-key", "authorization"}


class _Capture:
    """Just enough of BaseHTTPRequestHandler for the encoders: records the body they write."""

    def __init__(self) -> None:
        self.wfile = io.BytesIO()
        self.status = 0
        self.headers: dict[str, str] = {}
        self.close_connection = False

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, key: str, value: str) -> None:
        self.headers[key] = value

    def end_headers(self) -> None:
        return None


def main() -> None:
    harness, log, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    calls = []
    for line in log.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        protocol = entry.get("protocol")
        if protocol not in {"anthropic_messages", "openai_responses"}:
            continue
        capture = _Capture()
        encoder = up.anthropic_messages if protocol == "anthropic_messages" else up.openai_responses
        pinned = {**entry["body"], "model": UPSTREAM_MODEL[protocol]}
        encoder(capture, pinned, entry["plan"], entry["n"])  # type: ignore[arg-type]
        raw = capture.wfile.getvalue().decode()
        # Keepalive pings are the gateway's own business (it strips and re-emits them): leave them out.
        events = [chunk + "\n\n" for chunk in raw.split("\n\n") if chunk.strip() and "event: ping" not in chunk]
        headers = {k: v for k, v in entry["headers"].items() if k not in _SKIP_HEADERS}
        calls.append(
            {
                "path": entry["path"],
                "headers": headers,
                "body": entry["body"],
                "upstream": {"status": 200, "content_type": "text/event-stream", "events": events},
                "expect": {
                    "finish_reason": "tool_calls" if "tool" in entry["plan"] else "stop",
                    "prompt_tokens": up.USAGE_IN,
                    "completion_tokens": up.USAGE_OUT,
                    "request_class": headers.get("x-claude-code-request-class", "main"),
                    "cost": None,
                },
            },
        )
    session = {
        "harness": harness,
        "front_protocol": "anthropic_messages" if harness == "claude-code" else "openai_responses",
        "request_class_source": SOURCES[harness],
        "note": "WP-07 scripted-upstream smoke run (tests/wp07_harnesses/smoke); real native requests, scripted SSE",
        "calls": calls,
    }
    out.write_text(json.dumps(session, indent=1, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
