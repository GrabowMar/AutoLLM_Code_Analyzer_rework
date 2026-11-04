"""Print a translate-mode ``GatewayConfig`` for the offline gateway smoke run (``run_gateway_smoke.sh``).

Run inside the worktree test container (the contract is validated by WP-01's model):

    ./.wt-test.sh wp07 -- python backend/generation/tests/wp07_harnesses/smoke/gateway_config.py <harness> <out.json>

The upstream is the scripted stand-in (``scripted_upstream.py``) under the alias ``upstream``; nothing here
reaches a real model.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import django

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
django.setup()

from backend.generation.harnesses import registry  # noqa: E402
from backend.generation.testing.fake_gateway import gateway_config  # noqa: E402

MODEL = "deepseek/deepseek-v4.1-flash"


def main() -> None:
    harness, out = sys.argv[1], Path(sys.argv[2])
    adapter = registry.get(harness)
    mode = adapter.facts.protocol_modes[0]
    cfg = gateway_config(
        front_protocol=adapter.protocol,
        protocol_mode=mode,
        upstream={"kind": "openrouter", "base_url": "http://upstream:8080/api/v1"},
        model={"upstream_model": MODEL, "presented_names": [MODEL], "max_output": 32_000},
        budget={"max_cost_usd": 1.0, "max_llm_requests": 10, "max_aux_requests": 5, "max_total_tokens": 1_000_000},
        call={"max_output_tokens": 8192},
    )
    out.write_text(json.dumps(cfg.model_dump(mode="json", by_alias=True)))
    sys.stdout.write(f"{harness}: {adapter.protocol}/{mode} config -> {out}\n")


if __name__ == "__main__":
    main()
