"""Turn a scripted-upstream log into gateway-style ``calls.jsonl`` records (stdlib only).

The records carry the keys the adapters read (``request``, ``front_protocol``, ``request_class``,
``status``, ``usage``, ``native_system_prompt`` on the first main call, ...), so a smoke run becomes a
``parse_trajectory`` / ``observed_inputs`` fixture.

    python3 to_calls.py <logs/upstream.jsonl> <out/calls.jsonl>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from scripted_upstream import USAGE_IN
from scripted_upstream import USAGE_OUT
from scripted_upstream import _system

# The gateway mode each front runs in (Gemini CLI is translate-only: there is no native Gemini upstream).
_MODES = {"openai_chat": "native", "gemini_generate": "translate"}


def convert(lines: list[str]) -> list[dict]:
    calls = []
    first_main = True
    for line in lines:
        entry = json.loads(line)
        protocol = entry.get("protocol")
        if protocol not in {"openai_chat", "anthropic_messages", "openai_responses", "gemini_generate"}:
            continue
        headers = entry.get("headers") or {}
        header_class = headers.get("x-claude-code-request-class")
        request_class = header_class or "main"
        native = None
        if request_class == "main" and first_main:
            native = _system(protocol, entry["body"]) or None
            first_main = False
        calls.append(
            {
                "index": len(calls) + 1,
                "started": entry["ts"],
                "ended": entry["ts"] + 0.05,
                "duration_s": 0.05,
                "path": entry["path"],
                "front_protocol": protocol,
                "protocol_mode": _MODES.get(protocol, "passthrough"),
                "request_class": request_class,
                "request_class_header": header_class,
                "status": 200,
                "transport_error": False,
                "error": None,
                "finish_reason": "tool_calls" if "tool" in (entry.get("plan") or {}) else "stop",
                "usage": {"prompt_tokens": USAGE_IN, "completion_tokens": USAGE_OUT, "reasoning_tokens": 0},
                "native_system_prompt": native,
                "request": entry["body"],
            },
        )
    return calls


if __name__ == "__main__":
    with Path(sys.argv[1]).open(encoding="utf-8") as handle:
        records = convert(handle.readlines())
    with Path(sys.argv[2]).open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
