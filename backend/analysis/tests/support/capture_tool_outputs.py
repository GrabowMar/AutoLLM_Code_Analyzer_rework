"""Capture golden tool outputs from the pinned analyzer image (re-runnable).

Every case resolves a sample, lets the tool's adapter select files and build
the invocation, runs it through the production sandbox (``sandbox.run_volume``
+ ``sandbox.run_tool``: read-only /src volume, cwd /tmp tmpfs, network none,
uid 10001, read-only rootfs, fresh /out volume) and stores the complete
``ExecOutcome`` as ``tests/fixtures/tool_outputs/<slug>/<case>.json``. The
adapter unit tests replay these outcomes, so parsers are tested against what
the pinned tools really print, failure modes included.

Run inside the project container with the apps daemon reachable::

    python -m backend.analysis.tests.support.capture_tool_outputs [--tool ruff ...] [--case findings ...]

Resources are labelled ``llmlab.analysis-dev`` instead of the production run
label: other processes on the shared daemon reap ``llmlab.analysis-run``
resources whose run is not live, which would kill a capture mid-run.

The ``pylint_messages.json`` map (symbol -> message id of the pinned pylint)
used to re-materialize inline-suppressed messages is regenerated as well.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import sys
import uuid
from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any
from unittest import mock

ANALYSIS_DIR = Path(__file__).resolve().parents[2]
CANARY_DIR = ANALYSIS_DIR / "data" / "canary"
FIXTURE_DIR = ANALYSIS_DIR / "tests" / "fixtures" / "tool_outputs"
PYLINT_MESSAGES = ANALYSIS_DIR / "engine" / "adapters" / "pylint_messages.json"
DEV_LABEL = "llmlab.analysis-dev"
FIXTURE_SCHEMA = 1

PYTHON_TOOLS = ("ruff", "bandit", "pylint", "mypy", "vulture", "radon", "flake8", "perflint")


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


def canary_files(*dirs: str) -> dict[str, str]:
    """``{"vulnerable/...": text}`` for the given canary fixture directories."""
    files: dict[str, str] = {}
    for name in dirs or ("vulnerable", "clean"):
        for path in sorted((CANARY_DIR / name).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                files[path.relative_to(CANARY_DIR).as_posix()] = path.read_text(encoding="utf-8")
    return files


def canary_expectations() -> dict[str, Any]:
    """``data/canary/expectations.yaml`` as written (the canary module normalizes it for evaluation)."""
    import yaml

    return yaml.safe_load((CANARY_DIR / "expectations.yaml").read_text(encoding="utf-8"))


# Files whose only purpose is to configure / silence the scanners of the app
# they ship with. None of them may change any tool's findings.
HOSTILE_CONFIG_FILES: dict[str, str] = {
    "pyproject.toml": """[project]
name = "inventory"
version = "1.0.0"

[tool.ruff]
exclude = ["*"]
[tool.ruff.lint]
ignore = ["ALL"]

[tool.mypy]
ignore_errors = true

[tool.pylint."messages control"]
disable = ["all"]

[tool.bandit]
skips = ["B105", "B201", "B301", "B307", "B324", "B403", "B404", "B506", "B602", "B608"]

[tool.vulture]
min_confidence = 100

[tool.codespell]
ignore-words-list = "recieve,seperate"
skip = "*"
""",
    "setup.cfg": "[mypy]\nignore_errors = True\n\n[codespell]\nskip = *\n\n[radon]\nexclude = *\n",
    "mypy.ini": "[mypy]\nignore_errors = True\n",
    ".pylintrc": "[MESSAGES CONTROL]\ndisable=all\n",
    "ruff.toml": 'exclude = ["*"]\n[lint]\nignore = ["ALL"]\n',
    ".bandit": "[bandit]\nskips: B105,B201,B301,B307,B324,B403,B404,B506,B602,B608\n",
    ".semgrepignore": "*\n",
    ".gitleaks.toml": "title = \"silence\"\n[allowlist]\npaths = ['''.*''']\n",
    ".gitleaksignore": (
        "vulnerable/backend/app.py:aws-access-token:25\n"
        "/src/vulnerable/backend/app.py:aws-access-token:25\n"
        "/tmp/scan/vulnerable/backend/app.py:aws-access-token:25\n"
        "vulnerable/backend/app.py:generic-api-key:26\n"
        "/src/vulnerable/backend/app.py:generic-api-key:26\n"
        "/tmp/scan/vulnerable/backend/app.py:generic-api-key:26\n"
    ),
    ".secrets.baseline": '{"version": "1.5.0", "plugins_used": [], "filters_used": [], "results": {}}\n',
    ".jscpd.json": '{"ignore": ["**/*"], "minTokens": 100000}\n',
    "eslint.config.js": "export default [{ ignores: ['**/*'] }, { rules: { 'no-eval': 'off' } }];\n",
    ".eslintrc.json": '{"root": true, "rules": {"no-eval": "off", "no-unused-vars": "off"}}\n',
    ".eslintignore": "*\n",
    ".codespellrc": "[codespell]\nskip = *\nignore-words-list = recieve,seperate\n",
    ".hadolint.yaml": "ignored: [DL3007, DL3008, DL3009, DL3015, DL3020, DL3025]\n",
    "vulnerable/.hadolint.yaml": "ignored: [DL3007, DL3008, DL3009, DL3015, DL3020, DL3025]\n",
    ".gitignore": "*\n",
    "whitelizard.txt": "classify_order\n",
    ".flake8": "[flake8]\nselect = E9\nexclude = *\n",
    "tox.ini": "[flake8]\nignore = F,E,W\n",
    "vulnerable/backend/pyproject.toml": (
        '[project]\nname = "inventory"\nversion = "1.0.0"\ndependencies = ["requests", "pyyaml"]\n\n'
        '[tool.deptry]\nignore = ["DEP001", "DEP002", "DEP003", "DEP004"]\nexclude = [".*"]\n'
    ),
    "vulnerable/backend/.gitignore": "*.py\n",
    "vulnerable/frontend/.depcheckrc": '{"ignores": ["*"], "skip-missing": true}\n',
    "vulnerable/frontend/.gitignore": "src/\n",
    "vulnerable/frontend/.htmlhintrc": '{"tag-pair": false, "id-unique": false, "alt-require": false}\n',
    "vulnerable/frontend/.stylelintrc.json": '{"rules": {"property-no-unknown": null}, "ignoreFiles": ["**/*"]}\n',
    "vulnerable/frontend/.stylelintignore": "*\n",
    "vulnerable/scripts/.shellcheckrc": "disable=all\n",
}

# Inline suppressions of every kind; each line still contains its defect.
SUPPRESSED_FILES: dict[str, str] = {
    "suppressed/app.py": """import json  # noqa: F401  # pylint: disable=unused-import


def calculate(expression):
    return eval(expression)  # nosec B307  # noqa: S307  # nosemgrep  # pylint: disable=eval-used


API_PASSWORD = "Wr4ngle-Canary-Pass-91"  # nosec  # noqa: S105  # pragma: allowlist secret
""",
    "suppressed/settings.py": (
        'AWS_ACCESS_KEY_ID = "AKIAZ7QX3LKD5PMW2N4J"  # gitleaks:allow  # pragma: allowlist secret\n'
    ),
    "suppressed/run.js": """// eslint-disable-next-line no-eval
export const run = (code) => eval(code);
/* eslint-disable */
export const leftover = () => { const unused = 1; return 0; };
""",
    "suppressed/Raw.vue": """<template>
  <!-- eslint-disable -->
  <div v-html="html"></div>
</template>

<script setup>
defineProps({ html: { type: String, default: "" } });
</script>
""",
    "suppressed/Dockerfile": "# hadolint ignore=DL3007\nFROM python:latest\n",
    "suppressed/perf.py": """def order_lines(orders: list[dict]) -> list[str]:
    lines = []
    for order in orders:  # pylint: disable=use-list-copy
        lines.append(str(order))
    return lines
""",
    "suppressed/page.html": """<!-- htmlhint alt-require:false -->
<!DOCTYPE html>
<html lang="en"><head><title>Page</title></head><body><img src="/a.png"></body></html>
""",
    "suppressed/site.css": "/* stylelint-disable */\n.card {\n  colr: red;\n}\n",
    "suppressed/run.sh": "#!/bin/sh\n# shellcheck disable=SC2086\necho $1\n",
}

SYNTAX_FILES: dict[str, dict[str, str]] = {
    "python": {
        "svc/ok.py": 'import os\n\n\ndef read_setting(name):\n    return os.environ.get(name, "")\n',
        # truncated LLM output: the signature never closes
        "svc/broken.py": "def handler(request:\n    return request\n",
    },
    "eslint": {
        "web/ok.jsx": "export default function Ok({ title }) {\n  return <h1>{title}</h1>;\n}\n",
        "web/broken.jsx": "export default function Broken() {\n  return <div>;\n}\n",
        "web/broken.ts": "export const total: = 1;\n",
        "web/Broken.vue": "<template>\n  <p>{{ msg }}</p>\n</template>\n\n<script setup>\nconst msg = ;\n</script>\n",
    },
    "semgrep": {
        "svc/ok.py": "import subprocess\n\n\ndef run(cmd):\n    return subprocess.call(cmd, shell=True)\n",
        "web/broken.js": "export function f( {\n  return eval(x;\n}}}\n",
        "svc/broken.py": "def handler(request:\n    return request\n",
    },
    "hadolint": {
        "Dockerfile": "FROM python:3.12-slim\nRUN pip install flask\n",
        "docker/Dockerfile": "FROM\nRUN \n",
    },
    "deptry": {
        "svc/requirements.txt": "flask==3.0.3\n",
        "svc/ok.py": "import flask\n\napp = flask.Flask(__name__)\n",
        "svc/broken.py": "def handler(request:\n    return request\n",
        "tools/orphan.py": "import yaml\n",
    },
    "depcheck": {
        "web/package.json": '{"name": "web", "private": true, "dependencies": {"react": "18.3.1"}}\n',
        "web/ok.jsx": 'import { useState } from "react";\n\nexport const useFlag = () => useState(false);\n',
        "web/broken.jsx": "export default function Broken() {\n  return <div>;\n}\n",
        "lib/orphan.js": 'import left from "left-pad";\n\nexport default left;\n',
    },
    "stylelint": {
        "web/ok.css": ".a {\n  color: red;\n}\n",
        "web/broken.css": ".a {\n  color: red;\n",
        "web/theme.scss": "$c: red;\n// a comment\n.a { color: $c; }\n",
    },
    "shellcheck": {
        "bin/ok.sh": '#!/bin/sh\necho "$1"\n',
        "bin/broken.sh": "#!/bin/bash\nif [ x ; then\n",
        "bin/prompt.zsh": "#!/bin/zsh\necho $1\n",
    },
}


@dataclass
class Case:
    """One capture: which files, which config, which resource overrides."""

    name: str
    files: dict[str, str]
    config: dict[str, Any] = field(default_factory=dict)
    timeout_s: int | None = None
    mem_limit: str | None = None
    from_canary: bool = False


NON_DEFAULT_CONFIG: dict[str, dict[str, Any]] = {
    "ruff": {"select": "F", "target_version": "py310"},
    "bandit": {"severity": "high", "confidence": "high"},
    "semgrep": {"rulesets": ["python"], "per_file_timeout": 10},
    "pylint": {"include_refactor": True, "report_suppressed": False, "extra_disable": "unused-import"},
    "mypy": {"check_untyped_defs": False, "python_version": "3.11"},
    "vulture": {"min_confidence": 60},
    "eslint": {"opt_in_rules": ["security/detect-object-injection", "security/detect-non-literal-fs-filename"]},
    "gitleaks": {},
    "detect-secrets": {"base64_limit": 5.5, "hex_limit": 4.0},
    "codespell": {"builtin": ["clear"]},
    "hadolint": {"ignore_rules": "DL3007,DL3008"},
    "jscpd": {"min_tokens": 30, "min_lines": 3, "mode": "strict"},
    "radon": {"finding_min_rank": "C"},
    "lizard": {"ccn_threshold": 5, "modified_ccn": True},
    "flake8": {"select": "F,E9,C90", "max_line_length": 100, "max_complexity": 10},
    "perflint": {"report_suppressed": False, "extra_disable": "loop-invariant-statement"},
    "deptry": {"ignore": "DEP002"},
    "depcheck": {"skip_missing": True},
    "htmlhint": {},
    "stylelint": {},
    "shellcheck": {"severity": "warning", "exclude": "SC2164"},
}


def cases_for(slug: str) -> list[Case]:
    """The captures of one tool."""
    canary = canary_files("vulnerable", "clean")
    cases = [
        Case("findings", canary, from_canary=True),
        Case("clean", canary_files("clean"), from_canary=True),
        Case("hostile", {**canary_files("vulnerable"), **HOSTILE_CONFIG_FILES}, from_canary=True),
        Case("config", canary, config=NON_DEFAULT_CONFIG[slug], from_canary=True),
    ]
    if slug in PYTHON_TOOLS:
        cases.append(Case("syntax", SYNTAX_FILES["python"]))
    elif slug in SYNTAX_FILES:
        cases.append(Case("syntax", SYNTAX_FILES[slug]))
    suppressible = (
        "ruff",
        "bandit",
        "semgrep",
        "pylint",
        "eslint",
        "gitleaks",
        "detect-secrets",
        "hadolint",
        "mypy",
        "flake8",
        "perflint",
        "htmlhint",
        "stylelint",
        "shellcheck",
    )
    # vulture: "import json  # noqa: F401" (its noqa parser is disabled in the image).
    if slug in (*suppressible, "vulture"):
        cases.append(Case("suppressed", SUPPRESSED_FILES))
    if slug == "semgrep":
        # Real failure outcomes: a time limit shorter than semgrep's start-up and a memory
        # limit below what semgrep-core needs.
        cases.append(Case("timeout", canary, timeout_s=1, from_canary=True))
        cases.append(Case("oom", canary, mem_limit="48m", from_canary=True))
    if slug == "eslint":
        cases.append(Case("oom", canary, mem_limit="40m", from_canary=True))
    return cases


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------


@contextmanager
def dev_labels() -> Iterator[None]:
    """Label sandbox resources ``llmlab.analysis-dev`` (not reaped by other processes)."""
    from backend.analysis.engine import sandbox

    with mock.patch.object(sandbox, "LABEL_RUN", DEV_LABEL):
        yield


def make_sample(files: dict[str, str]):
    from backend.analysis.engine import sample as sample_mod

    return sample_mod.resolve_inline(files)


def run_case(slug: str, case: Case, *, image: str) -> dict[str, Any] | None:
    """Run one case end to end and return the fixture document (None: nothing to scan)."""
    from backend.analysis.engine import sandbox
    from backend.analysis.engine.adapters import get_adapter

    adapter = get_adapter(slug)
    sample = make_sample(case.files)
    config = adapter.effective_config(case.config)
    files = adapter.select_files(sample, config)
    if not files:
        return None
    invocation = adapter.invocation(files, config)
    if case.timeout_s is not None:
        invocation.timeout_s = case.timeout_s
    if case.mem_limit is not None:
        invocation.mem_limit = case.mem_limit
    run_id = f"capture-{slug}-{case.name}-{uuid.uuid4().hex[:8]}"
    volume_files = {f.path: f.content for f in sample.files if f.included}
    with dev_labels(), sandbox.run_volume(run_id, volume_files, image=image) as volume:
        outcome = sandbox.run_tool(image=image, volume=volume, invocation=invocation, run_id=run_id, tool_slug=slug)
    if outcome.infra_error:
        msg = f"{slug}/{case.name}: infrastructure error: {outcome.infra_error}"
        raise RuntimeError(msg)
    return {
        "schema": FIXTURE_SCHEMA,
        "tool": slug,
        "case": case.name,
        "image": image,
        "tool_version": adapter.spec.version,
        "captured_at": datetime.datetime.now(tz=datetime.UTC).isoformat(timespec="seconds"),
        "config": config,
        "files": [_file_entry(f, embed=not (case.from_canary and _is_canary_file(f))) for f in files],
        "invocation": {
            "argv": invocation.argv,
            "timeout_s": invocation.timeout_s,
            "mem_limit": invocation.mem_limit,
            "env": invocation.env,
            "report_files": list(invocation.report_files),
        },
        "outcome": {
            "exit_code": outcome.exit_code,
            "stdout": _blob(outcome.stdout),
            "stderr": _blob(outcome.stderr),
            "out_files": {name: _blob(data) for name, data in sorted(outcome.out_files.items())},
            "timed_out": outcome.timed_out,
            "oom_killed": outcome.oom_killed,
            "cancelled": outcome.cancelled,
            "wall_seconds": outcome.wall_seconds,
            "infra_error": outcome.infra_error,
        },
    }


def _is_canary_file(file) -> bool:
    """True when *file* is a canary fixture file with the same content (replayed from data/canary)."""
    path = CANARY_DIR / file.path
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == file.sha256


def _file_entry(file, *, embed: bool) -> dict[str, Any]:
    entry = {"path": file.path, "sha256": file.sha256, "language": file.language, "kind": file.kind}
    if embed:
        entry["content"] = file.content.decode("utf-8")
    return entry


def _blob(data: bytes) -> dict[str, str] | str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return {"base64": base64.b64encode(data).decode()}


# --------------------------------------------------------------------------
# Replay (used by the unit tests)
# --------------------------------------------------------------------------


def fixture_path(slug: str, case: str) -> Path:
    return FIXTURE_DIR / slug / f"{case}.json"


def load_fixture(slug: str, case: str) -> dict[str, Any]:
    return json.loads(fixture_path(slug, case).read_text(encoding="utf-8"))


def _unblob(value: dict[str, str] | str) -> bytes:
    if isinstance(value, dict):
        return base64.b64decode(value["base64"])
    return value.encode("utf-8")


def replay_files(fixture: dict[str, Any]) -> list:
    """The selected SampleFiles of a fixture (canary content checked against the recorded sha256)."""
    from backend.analysis.engine.types import SampleFile

    out = []
    for entry in fixture["files"]:
        if "content" in entry:
            content = entry["content"].encode("utf-8")
        else:
            content = (CANARY_DIR / entry["path"]).read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != entry["sha256"]:
            msg = f"{entry['path']} changed since the fixture was captured; re-run capture_tool_outputs"
            raise AssertionError(msg)
        out.append(
            SampleFile(
                path=entry["path"],
                content=content,
                sha256=digest,
                size=len(content),
                language=entry["language"],
                kind=entry["kind"],
                origin="authored",
            ),
        )
    return out


def replay_outcome(fixture: dict[str, Any], **overrides: Any):
    """The recorded ExecOutcome (fields can be overridden to derive failure modes)."""
    from backend.analysis.engine.types import ExecOutcome

    recorded = fixture["outcome"]
    values = {
        "exit_code": recorded["exit_code"],
        "stdout": _unblob(recorded["stdout"]),
        "stderr": _unblob(recorded["stderr"]),
        "out_files": {name: _unblob(blob) for name, blob in recorded["out_files"].items()},
        "timed_out": recorded["timed_out"],
        "oom_killed": recorded["oom_killed"],
        "cancelled": recorded["cancelled"],
        "wall_seconds": recorded["wall_seconds"],
        "infra_error": recorded["infra_error"],
    }
    values.update(overrides)
    return ExecOutcome(**values)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def refresh_pylint_messages(image: str) -> None:
    """Regenerate engine/adapters/pylint_messages.json from the image's ``pylint --list-msgs``."""
    from backend.analysis.engine import sandbox
    from backend.analysis.engine.catalog import get_spec
    from backend.analysis.engine.types import Invocation

    run_id = f"capture-pylint-msgs-{uuid.uuid4().hex[:8]}"
    with dev_labels(), sandbox.run_volume(run_id, {"empty.py": b""}, image=image) as volume:
        outcome = sandbox.run_tool(
            image=image,
            volume=volume,
            invocation=Invocation(argv=["/opt/analyzer/bin/pylint", "--list-msgs"], timeout_s=60, mem_limit="256m"),
            run_id=run_id,
            tool_slug="pylint",
        )
    if outcome.exit_code != 0:
        msg = f"pylint --list-msgs failed: {outcome.stderr[-500:]!r}"
        raise RuntimeError(msg)
    pairs = re.findall(r"^:([a-z0-9-]+) \(([A-Z]\d{4})\)", outcome.stdout.decode(), re.MULTILINE)
    payload = {
        "pylint": get_spec("pylint").version,
        "source": "pylint --list-msgs (llmlab analyzer image)",
        "messages": dict(sorted(pairs)),
    }
    PYLINT_MESSAGES.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None, *, log: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--image", default="", help="analyzer image tag (default: sandbox.image_tag())")
    parser.add_argument("--tool", action="append", default=[], help="tool slug (repeatable; default: all)")
    parser.add_argument("--case", action="append", default=[], help="case name (repeatable; default: all)")
    args = parser.parse_args(argv)

    from backend.analysis.engine import catalog
    from backend.analysis.engine import sandbox

    image = args.image or sandbox.image_tag()
    slugs = args.tool or [s.slug for s in catalog.static_specs()]
    if "pylint" in slugs and not args.case:
        refresh_pylint_messages(image)
        log("pylint_messages.json refreshed")
    for slug in slugs:
        for case in cases_for(slug):
            if args.case and case.name not in args.case:
                continue
            fixture = run_case(slug, case, image=image)
            if fixture is None:
                log(f"{slug:15s} {case.name:10s} not applicable (no files selected)")
                continue
            path = fixture_path(slug, case.name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(fixture, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            outcome = fixture["outcome"]
            log(
                f"{slug:15s} {case.name:10s} exit={outcome['exit_code']} timed_out={outcome['timed_out']} "
                f"oom={outcome['oom_killed']} {outcome['wall_seconds']}s",
            )
    return 0


if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    import django

    django.setup()
    sys.exit(main())
