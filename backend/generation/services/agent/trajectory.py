"""Agent trajectory analytics: what an agent *did*, rebuilt from the run evidence.

No LLM call, no rerun: everything comes from files every agent run already
leaves behind.

* **The gateway call log** (``llm_calls.jsonl.gz``; the runner has the same
  list in memory at ingestion) is the primary source. Each record holds the
  full request (all messages so far, so every tool result, plus the tool
  specs), the response (content, reasoning, tool calls, usage, provider,
  finish reason), ``ts`` (call start, epoch seconds) and ``duration_s``.
  Counts derived from it do not depend on the harness reporting itself, so
  they are comparable across tool-calling harnesses (OpenCode,
  mini-swe-agent).
* **OpenCode's event stream** (``harness_events.jsonl.gz``) adds, per tool
  call, the status, the bash exit code, the error text and start/end times.
* **mini-swe-agent's** ``traj.json`` (inside ``harness_report.tar.gz``, only
  for runs after the ``-o`` fix) adds nothing the gateway log lacks except
  per-message timestamps; it is recorded as a source but not needed. mini's
  tool results carry their ``returncode`` in the gateway log already.

Outputs
-------
``result_data.agent.trajectory`` (:func:`build_record`): the full record,
``schema_version`` :data:`TRAJECTORY_SCHEMA_VERSION`, ``parser_version``
:data:`PARSER_VERSION`. ``metrics.behaviour`` (:func:`behaviour_summary`):
the scalar summary statistics read (contract C2). ``metrics.list_cost`` /
``metrics.billing_source``: list-price equivalent of subscription-billed
(OpenCode Go) runs; ``metrics.cost`` stays the billed value.

Every field is a heuristic over logged text unless it is a plain count, so:

* action categories come from the versioned rule table
  (:data:`ACTION_RULES` + :func:`rule_table`); bump :data:`PARSER_VERSION`
  on any rule change and compare categories within a harness;
* a value that could not be measured is ``None``, never 0;
* Aider answers in edit blocks without tool calls (single-turn edit format),
  so its action fields are ``None`` and ``comparable`` is false.

Harness self-reports (``metrics.harness_steps`` / ``metrics.tool_calls`` /
``agent.harness_report``) are never overwritten; the record keeps them next
to the gateway counts (``harness_counters``).
"""

from __future__ import annotations

import difflib
import gzip
import hashlib
import io
import json
import logging
import posixpath
import re
import shlex
import statistics
import tarfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.generation.services.agent.harnesses.base import IGNORED_DIRS
from backend.generation.services.agent.harnesses.base import WORKSPACE_DIR

logger = logging.getLogger(__name__)

TRAJECTORY_SCHEMA_VERSION = 1
# Bump on any change to a rule, a category or a derived field's definition.
# 1.1: quoted strings are data (``pkill -f 'python3 app.py'`` is not a server
# start), *.sh scripts count only when run, test summaries with zero failures
# are not failures, Python edit scripts' string literals are not scanned,
# FastAPI TestClient is a probe, ``node -e`` file writes count, unreported
# token counts are left out of the context series and the ratio denominators,
# failed_actions leaves out harness policy denials (policy_denials has them).
# 1.2: tool_calls_gateway is None (not 0) for a harness without native tool
# calls (Aider) or a run without logged calls, like actions_total.
PARSER_VERSION = "1.2"

# ── action categories ─────────────────────────────────────────────────────

# One primary category per action (the first matching label in this order:
# what the action was *for*); every matching label is also counted in
# ``activity_counts`` (an action can install, build and probe at once).
CATEGORY_PRIORITY = (
    "submit",
    "test",
    "build",
    "run_server",
    "probe_http",
    "write_file",
    "edit",
    "install",
    "registry_lookup",
    "syntax_check",
    "git",
    "todo",
    "process",
    "explore",
    "other",
)

CATEGORY_HELP = {
    "submit": "the harness's own end-of-task command (mini-swe-agent)",
    "test": "ran a test runner or a test script (pytest, unittest, vitest, jest, npm test, *test*.py, *test*.sh)",
    "build": "built the frontend/app (npm/yarn/pnpm run build, vite build, tsc, webpack)",
    "run_server": "started the app or a dev server (python app.py, flask run, uvicorn, gunicorn, npm run dev/start)",
    "probe_http": (
        "sent HTTP requests to the app (curl, wget, urllib/requests/fetch, Flask test_client, FastAPI TestClient)"
    ),
    "write_file": "wrote a whole workspace file (write tool, cat/tee heredoc, redirect, python open(...,'w'))",
    "edit": "edited a workspace file in place (edit tool, sed -i, python read-replace-write)",
    "install": "installed dependencies (npm/yarn/pnpm install, pip install, apt-get install)",
    "registry_lookup": "looked up package versions (npm view/info/pack, pip index versions, pip download)",
    "syntax_check": "checked syntax or lint (py_compile, ast.parse, node --check, tsc --noEmit, eslint, ruff, flake8)",
    "git": "ran git",
    "todo": "updated the harness's todo list (OpenCode todowrite/todoread)",
    "process": "managed processes (kill, pkill, ps, pgrep, fuser, lsof, ss, sleep)",
    "explore": "read or listed files / environment (read, glob, grep tools; cat, ls, find, grep, head, sed -n, ...)",
    "other": "anything else (cd/echo/mkdir/cp/rm only, subagent task, unknown tools)",
}

# Native (non-bash) tool names → category. OpenCode 1.18.x tool set.
TOOL_CATEGORIES = {
    "read": "explore",
    "glob": "explore",
    "grep": "explore",
    "list": "explore",
    "ls": "explore",
    "view": "explore",
    "write": "write_file",
    "create": "write_file",
    "edit": "edit",
    "multiedit": "edit",
    "patch": "edit",
    "apply_patch": "edit",
    "str_replace_editor": "edit",
    "todowrite": "todo",
    "todoread": "todo",
}
SHELL_TOOLS = frozenset({"bash", "shell", "run_shell_command", "execute_bash"})

# Regex rules applied to each shell segment (pipeline head and its pipe
# tail, heredoc bodies removed). ``(label, pattern, description)``.
ACTION_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "test",
        re.compile(
            r"\bpytest\b|\bpython3?\s+-m\s+(?:pytest|unittest)\b|\b(?:npx\s+)?(?:vitest|jest|mocha)\b"
            r"|\bplaywright\s+test\b|\b(?:npm|pnpm|yarn)\s+(?:run\s+)?test\b|\bnode\s+--test\b"
            r"|\bpython3?\s+(?:-\w+\s+)*\S*(?:test|smoke|e2e)\w*\.py\b|\bnode\s+\S*(?:test|smoke|spec)[\w.-]*\.[cm]?js\b",
        ),
        "test runners and *test*/*smoke* scripts",
    ),
    (
        "build",
        re.compile(
            r"\b(?:npm|pnpm|yarn)\s+(?:--prefix\s+\S+\s+)?(?:run\s+)?build\b|\bvite\s+build\b"
            r"|\btsc\b(?![^\n]*--noEmit)|\bwebpack\b|\bnext\s+build\b|\besbuild\b[^\n]*--bundle",
        ),
        "npm/pnpm/yarn [run] build, vite build, tsc (emitting), webpack, next build, esbuild --bundle",
    ),
    (
        "run_server",
        re.compile(
            r"\bpython3?\s+(?:-u\s+)?(?:\S*/)?(?:app|main|server|run|wsgi|serve)\.py\b"
            r"|\bflask\s+(?:--app\s+\S+\s+)?run\b|\b(?:uvicorn|gunicorn|hypercorn|waitress-serve)\b"
            r"|\b(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:dev|start|preview|serve)\b|\bvite\s+(?:preview|dev)\b"
            r"|\bnode\s+(?:\S*/)?(?:server|index|app|main)\.[cm]?js\b|\bpython3?\s+-m\s+http\.server\b",
        ),
        "python app.py/main.py/server.py, flask run, uvicorn/gunicorn, npm run dev/start/preview, node server.js "
        "(start*/restart*/run_server*.sh scripts: see the script rule)",
    ),
    (
        "probe_http",
        re.compile(
            r"\bcurl\b|\bwget\b|\bhttpie\b|\bhttp\s+(?:GET|POST|PUT|DELETE|PATCH|:\d)"
            r"|urllib\.request|\brequests\.(?:get|post|put|delete|patch|head|Session)\b|\bhttp\.client\b"
            r"|\bhttpx\b|\.test_client\(|\bTestClient\(|\bfetch\(\s*['\"`]https?://",
        ),
        "curl/wget/httpie, python urllib/requests/http.client/httpx, Flask test_client, FastAPI TestClient, "
        "fetch('http...')",
    ),
    (
        "install",
        re.compile(
            r"\b(?:npm|pnpm|yarn)\s+(?:--prefix\s+\S+\s+)?(?:install|i|ci|add)\b"
            r"|\b(?:pip3?|python3?\s+-m\s+pip)\s+install\b(?![^\n|;&]*==(?:\s|$|['\";&|]))"
            r"|\bapt(?:-get)?\s+install\b|\buv\s+(?:pip\s+install|add|sync)\b|\bpoetry\s+(?:install|add)\b",
        ),
        "npm/yarn/pnpm install|ci|add, pip install (not the 'pkg==' version probe), apt-get install",
    ),
    (
        "registry_lookup",
        re.compile(
            r"\b(?:npm|yarn)\s+(?:view|info|show|v|pack|search|outdated|dist-tags)\b"
            r"|\bpip3?\s+(?:index\s+versions|download)\b|\bpip3?\s+install\s+[\w.\[\]-]+==(?:\s|$|['\";&|])",
        ),
        "npm view/info/pack/outdated, pip index versions, pip download, pip install pkg== (version probe)",
    ),
    (
        "syntax_check",
        re.compile(
            r"\bpy_compile\b|\bcompileall\b|\bast\.parse\b|\bnode\s+(?:--check|-c)\b|\btsc\b[^\n]*--noEmit"
            r"|\b(?:eslint|ruff|flake8|pyflakes|pylint|mypy|black\s+--check)\b"
            r"|\bpython3?\s+-c\s+['\"]\s*(?:import\s+app\b|from\s+app\s+import\b)",
        ),
        "py_compile/compileall/ast.parse, node --check, tsc --noEmit, eslint/ruff/flake8/pyflakes/pylint/mypy, "
        "python -c 'import app' (import smoke check)",
    ),
    ("git", re.compile(r"(?:^|[\s;&|(])git\s+\S"), "git <subcommand>"),
)

# A segment that installs or looks up packages only names tools (uvicorn,
# vitest, ruff, ...): it gets no run/test/build label from those names.
_PACKAGE_LABELS = frozenset({"install", "registry_lookup"})
_EXPLORE_WORDS = frozenset(
    (
        *("cat", "ls", "find", "grep", "egrep", "rg", "head", "tail", "nl", "wc", "tree", "pwd", "which", "file"),
        *("stat", "du", "less", "more", "diff", "realpath", "readlink", "whereis", "printenv", "jq", "test", "["),
        *("awk", "sed", "type", "env", "uname", "id", "whoami", "df", "free"),
    ),
)
_PROCESS_WORDS = frozenset(
    {"kill", "pkill", "killall", "fuser", "ps", "pgrep", "lsof", "ss", "netstat", "sleep", "wait", "jobs", "disown"},
)
_VERSION_PROBE = re.compile(
    r"^(?:node|python3?|npm|pip3?|npx|yarn|pnpm)\s+(?:-v|-V|--version)\b"
    r"|^(?:npm|pnpm|yarn)\s+(?:ls|list|why|config\s+get|root|prefix)\b"
    r"|^node\s+(?:--[\w-]+(?:=\S+)?\s+)*-[ep]\s",  # node -e "console.log(require('x/package.json')...)"
)
_PIP_INSPECT = re.compile(r"^(?:pip3?|python3?\s+-m\s+pip)\s+(?:show|list|freeze)\b")
_SUBMIT = re.compile(r"^\s*echo\s+['\"]?COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT['\"]?\s*$")
# A shell script *run* as the command (``bash /tmp/restart.sh``, ``./scripts/smoke_test.sh``,
# ``nohup /tmp/start.sh &``); writing, chmod-ing or listing one is not running it.
SCRIPT_RUN = re.compile(r"^(?:(?:ba|da|z)?sh\s+(?:-\w+\s+)*|source\s+|\.\s+)?(?:\S*/)?([\w.-]+)\.sh\b")
SCRIPT_LABELS = (
    ("test", re.compile(r"test|smoke|e2e", re.IGNORECASE)),
    ("run_server", re.compile(r"start|restart|run_server|serve", re.IGNORECASE)),
)
# Inline code whose quoted argument is itself a command or a script: rules read
# that quoted text too (everywhere else a quoted string is data).
_INLINE_CODE = re.compile(
    r"\b(?:python3?\s+(?:-\w+\s+)*-c|node\s+(?:-[^\s=]*(?:=\S*)?\s+)*-[ep]|(?:ba|da|z)?sh\s+(?:-\w+\s+)*-c|eval)\s",
)
_NODE_INLINE = re.compile(r"\bnode\s+(?:-[^\s=]*(?:=\S*)?\s+)*-[ep]\s")
# Python string literals (masked before scanning an edit script for test/probe/lint calls).
_PY_STRING = re.compile(r"""(?s)'''.*?'''|\"\"\".*?\"\"\"|'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*\"""")

# Shell plumbing.
_SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\n|(?<![>&|<])&(?![>&])")
_PIPE_SPLIT = re.compile(r"(?<!\|)\|(?!\|)")
_ASSIGN_PREFIX = re.compile(r"""^(?:\w+=(?:'[^']*'|"[^"]*"|\$\([^)]*\)|\S*)\s+)+""")
_WRAPPERS = re.compile(
    r"^(?:nohup|setsid|exec|time|sudo|command|builtin|stdbuf\s+\S+|nice(?:\s+-n\s*\S+)?"
    r"|timeout\s+(?:-\S+(?:\s+\S+)?\s+)*\S+|env(?:\s+-\S+)*(?:\s+\w+=\S+)*)\s+",
)
_HEREDOC = re.compile(r"(?<!<)<<(?!<)(-?)\s*(['\"]?)([A-Za-z_][\w.-]*)\2")
_REDIRECT = re.compile(r"(?<![<>&\d])(\d?)>{1,2}\s*([^\s;&|<>()]+)")
_CAT_WRITE = re.compile(r"\bcat\b[^\n|;&]*?>{1,2}\s*([^\s;&|<>()]+)")
_TEE = re.compile(r"\btee\s+(?:-a\s+)?([^\s;&|<>()]+)")
_SED_INPLACE = re.compile(r"^(?:sed|perl)\s+(?:-\w*i\w*|-i\S*|--in-place)\b")
# The interpreter reading the heredoc (``python3 - <<EOF``, ``.venv/bin/python <<EOF``), not a
# file name ending in it: ``cat > /tmp/restart.sh <<EOF`` writes a script, it does not run one.
_SCRIPT_HEAD = re.compile(r"(?<![\w.-])(?:\S*/)?(python3?|node|bash|sh)\b(?:\s+-\w*)*\s*(?:-\s*)?<<")
_PY_INLINE = re.compile(r"\bpython3?\s+-c\s")
_CD = re.compile(r"^cd\s+(\"[^\"]+\"|'[^']+'|\S+)\s*$")
_NOT_A_FILE = re.compile(r"^(?:/dev/|&|/proc/)|\.(?:log|pid|out|err)$")

# Python script bodies (``python3 - <<EOF`` / ``python3 -c``).
_PY_OPEN_WRITE = re.compile(r"""open\(\s*(?:(['"])([^'"]+)\1|(\w+))\s*,\s*(?:mode\s*=\s*)?['"][wa]""")
_PY_WRITE_TEXT = re.compile(r"""(?:Path\(\s*(['"])([^'"]+)\1\s*\)|\b(\w+))\.write_(?:text|bytes)\(""")
_PY_ASSIGN = re.compile(r"""^\s*(\w+)\s*=\s*(?:Path\(\s*)?(['"])([^'"]+)\2""", re.MULTILINE)
_PY_REPLACE = re.compile(r"\.replace\(|\bre\.sub\(|\.read_text\(\)|\.read\(\)")
_NODE_PROBE = re.compile(r"\bfetch\(|\bhttp\.request\(|\baxios\b")
_NODE_WRITE = re.compile(r"""writeFileSync\(\s*(['"`])([^'"`]+)\1""")

# Tool outcomes.
_OPENCODE_TIMEOUT = re.compile(r"shell tool terminated command after exceeding timeout")
_POLICY_DENIAL = re.compile(r"rule which prevents you from using this specific tool call")
_EDIT_NO_MATCH = re.compile(
    r"Could not find oldString|oldString and newString are identical|Found multiple matches for oldString"
    r"|oldString not found|No changes to apply",
)
_NO_MATCH_HEADS = frozenset({"grep", "egrep", "rg", "diff", "test", "["})
_KILL_CODES = frozenset({-9, -15, 124, 137, 143})
_BUILD_OK = re.compile(r"✓ built in|\bbuilt in \d|Compiled successfully|compiled successfully")
_BUILD_FAIL = re.compile(
    r"error during build|Build failed|npm (?:error|ERR!)|ERR_MODULE_NOT_FOUND|RollupError|Rollup failed"
    r"|Could not resolve|error TS\d{3,5}|Transform failed|failed to load config|\[vite\]:? .*error",
    re.IGNORECASE,
)
# A test summary only fails with a non-zero count: "4 failed", "1 INTERACTIVE CHECKS FAILED",
# "FAILED: 2", "failures=1", an upper-case FAIL/FAILED word ("FAIL  src/app.test.jsx",
# "IMPORT FAILED") or a ✗ result line. "TOTAL: 31, FAILED: 0", "passed: 69  failed: 0",
# "(0 FAILED)" and "FAILURES: none" are clean runs.
_TEST_FAIL = re.compile(
    r"(?<![\w.:=])[1-9]\d*[ \t]+(?:[A-Za-z]+[ \t]+){0,2}?(?:failed|failing|failures?)\b(?![ \t]*[:=])"
    r"|\b(?:failed|failing|failures?)[ \t]*[:=][ \t]*[1-9]"
    r"|(?<!\b0[ \t])(?-i:\bFAIL(?:ED)?\b)(?![:= \t]*0\b)"
    r"|^[ \t]*(?:✗|✘|❌)"
    r"|AssertionError",
    re.MULTILINE | re.IGNORECASE,
)
_TEST_OK = re.compile(r"\b\d+\s+passed\b|\bOK\b|Tests?:\s+\d+\s+passed")
FAILED_OUTCOMES = frozenset(
    {"nonzero_exit", "killed", "policy_denial", "edit_no_match", "not_found", "tool_error", "output_error"},
)
# Failed outcomes the platform's harness policy causes: left out of ``failed_actions``.
POLICY_OUTCOMES = frozenset({"policy_denial"})

# Call roles, by the first system message (prefix match).
ROLE_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "summarizer",
        re.compile(r"^\s*\*Briefly\* summarize this partial conversation", re.IGNORECASE),
        "Aider's chat-history summarizer (weak model = run model)",
    ),
    (
        "compaction",
        re.compile(r"^\s*You are a helpful AI assistant tasked with summari[sz]ing conversations", re.IGNORECASE),
        "OpenCode's compaction agent",
    ),
    ("title", re.compile(r"^\s*You are a title generator", re.IGNORECASE), "OpenCode's session-title agent"),
    (
        "subagent",
        re.compile(r"^\s*You are a file search specialist", re.IGNORECASE),
        "OpenCode's explore subagent (task tool)",
    ),
)
_FORMAT_ERROR = re.compile(
    r"^\s*Tool call error:|No tool calls found in the response|did not conform to the edit format|^\s*Format error",
    re.IGNORECASE,
)
# Run-specific lines OpenCode appends to its frozen prompt (model name, date):
# replaced before hashing ``system_prompt_template_sha256``.
_VARYING_PROMPT_LINES = (
    (
        re.compile(r"^You are powered by the model named .*$", re.MULTILINE),
        "You are powered by the model named <model>",
    ),
    (re.compile(r"^\s*Today's date: .*$", re.MULTILINE), "Today's date: <date>"),
)

COMPARABLE_HARNESSES_WITHOUT_TOOLS = frozenset({"aider"})
FIRST_PASS_MAX_LINES = 4000
SUMMARY_CHARS = 300


# ── helpers ───────────────────────────────────────────────────────────────


def _text(content: Any) -> str:
    """Message content as text (OpenAI string or list-of-parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(part.get("text") or "") if isinstance(part, dict) else str(part) for part in content)
    return "" if content is None else str(content)


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat() if ts is not None else None


def _ratio(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def _call_failed(call: dict) -> bool:
    return call.get("status") != 200 or bool((call.get("response") or {}).get("error"))


def parse_jsonl(data: bytes | str | None) -> list[dict]:
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else (data or "")
    rows: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        return {"_raw": raw}
    return value if isinstance(value, dict) else {"_raw": raw}


def system_prompt(messages: list[dict]) -> str:
    return "\n".join(_text(m.get("content")) for m in messages if isinstance(m, dict) and m.get("role") == "system")


def classify_role(messages: list[dict]) -> str:
    head = system_prompt(messages)[:600]
    for role, pattern, _ in ROLE_RULES:
        if pattern.search(head):
            return role
    return "main"


def _ws_path(path: str, cwd: str) -> str | None:
    """Workspace-relative path for *path* (resolved against *cwd*), or None outside the workspace."""
    path = path.strip().strip("'\"")
    if not path or "$" in path or "*" in path or "`" in path:
        return None
    if path.startswith("~"):
        return None
    full = posixpath.normpath(path if path.startswith("/") else posixpath.join(cwd or WORKSPACE_DIR, path))
    prefix = WORKSPACE_DIR.rstrip("/") + "/"
    if not full.startswith(prefix):
        return None
    rel = full[len(prefix) :]
    parts = rel.split("/")
    if not rel or any(p in IGNORED_DIRS for p in parts[:-1]) or parts[-1] in IGNORED_DIRS:
        return None
    return rel


# ── shell parsing ─────────────────────────────────────────────────────────


def split_heredocs(command: str) -> list[tuple[str, list[str]]]:
    """``[(shell line, [heredoc bodies opened on that line])]`` for a bash command."""
    lines = command.split("\n")
    items: list[tuple[str, list[str]]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        bodies: list[str] = []
        for dash, _quote, delim in _HEREDOC.findall(line):
            body: list[str] = []
            while i < len(lines):
                candidate = lines[i].lstrip("\t") if dash else lines[i]
                if candidate.strip() == delim:
                    i += 1
                    break
                body.append(lines[i])
                i += 1
            bodies.append("\n".join(body))
        items.append((line, bodies))
    return items


def _substitution_end(text: str, start: int) -> int:
    """Index just past the ``$(...)`` or backtick substitution opening at *start*."""
    if text[start] == "`":
        end = text.find("`", start + 1)
        return len(text) if end < 0 else end + 1
    depth, j, quote = 0, start + 1, ""
    while j < len(text):
        ch = text[j]
        if quote:
            if ch == "\\" and quote == '"':
                j += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return len(text)


def mask_quotes(text: str, *, keep_substitutions: bool = False) -> str:
    """*text* with the inside of quoted strings blanked (same length; newlines inside quotes become spaces).

    Segment splitting, pipes and redirects are then read from shell syntax
    only: a multi-line ``python3 -c "..."`` stays one segment, and ``|`` or
    ``>`` inside a quoted sed/python expression is not taken for a pipe or a
    redirect. Quote characters themselves are kept. With *keep_substitutions*
    a ``$(...)`` / backtick command inside double quotes stays visible
    (``echo "health: $(curl -s ...)"`` runs curl): the text the action rules read.
    """
    out = list(text)
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if keep_substitutions and quote == '"' and (ch == "`" or text.startswith("$(", i)):
                i = _substitution_end(text, i)
                continue
            if ch == "\\" and quote == '"' and i + 1 < len(text):
                out[i] = out[i + 1] = "_"
                i += 2
                continue
            if ch == quote:
                quote = ""
            else:
                out[i] = " " if ch == "\n" else "_"
        elif ch == "\\" and i + 1 < len(text):
            i += 2
            continue
        elif ch in ("'", '"'):
            quote = ch
        i += 1
    return "".join(out)


def _segments(raw: str, masked: str) -> list[tuple[str, str]]:
    """``[(raw segment, masked segment)]`` split on ``&& || ; & newline`` outside quotes."""
    parts: list[tuple[str, str]] = []
    start = 0
    for m in _SEGMENT_SPLIT.finditer(masked):
        parts.append((raw[start : m.start()], masked[start : m.start()]))
        start = m.end()
    parts.append((raw[start:], masked[start:]))
    out: list[tuple[str, str]] = []
    for r, m in parts:
        lead, end = len(r) - len(r.lstrip()), len(r.rstrip())
        if end > lead:
            out.append((r[lead:end], m[lead:end]))
    return out


def _head_word(segment: str, masked: str | None = None) -> tuple[str, str]:
    """(first command word, command text without env assignments / wrappers) of a pipeline head."""
    masked = masked if masked is not None else mask_quotes(segment)
    pipe = _PIPE_SPLIT.search(masked)
    head = (segment[: pipe.start()] if pipe else segment).strip().lstrip("({ ").strip()
    for _ in range(4):
        stripped = _WRAPPERS.sub("", _ASSIGN_PREFIX.sub("", head)).strip()
        if stripped == head:
            break
        head = stripped
    word = head.split(None, 1)[0] if head else ""
    return word.rsplit("/", 1)[-1], head


def _last_arg(head: str) -> str | None:
    try:
        parts = shlex.split(head)
    except ValueError:
        parts = head.split()
    if len(parts) < 3 or parts[-1].startswith("-"):
        return None
    return parts[-1]


@dataclass
class Write:
    path: str
    kind: str  # "write" (whole file) | "edit"
    content: str | None = None


@dataclass
class ShellAnalysis:
    labels: list[str] = field(default_factory=list)
    writes: list[Write] = field(default_factory=list)
    last_head: str = ""


def _python_writes(body: str, cwd: str) -> list[Write]:
    """Workspace writes of a Python script body (``open(..., 'w')``, ``Path(...).write_text``)."""
    names = {m.group(1): m.group(3) for m in _PY_ASSIGN.finditer(body)}
    kind = "edit" if _PY_REPLACE.search(body) else "write"
    writes: list[Write] = []
    for pattern in (_PY_OPEN_WRITE, _PY_WRITE_TEXT):
        for m in pattern.finditer(body):
            target = m.group(2) or names.get(m.group(3) or "")
            rel = _ws_path(target, cwd) if target else None
            if rel:
                writes.append(Write(rel, kind))
    return writes


def _heredoc_segment(seg: str, bodies: list[str], cwd: str, labels: set[str], out: ShellAnalysis) -> None:
    """A segment that opens heredoc(s): whole-file writes (cat/tee) or script bodies (python/node/bash)."""
    script = _SCRIPT_HEAD.search(seg)
    target = _CAT_WRITE.search(seg) or _TEE.search(seg)
    body = "\n".join(bodies)
    if script and script.group(1).startswith("python"):
        writes = _python_writes(body, cwd)
        out.writes.extend(writes)
        if writes:
            labels.add("edit" if any(w.kind == "edit" for w in writes) else "write_file")
        # Calls only: the text an edit script writes ('vitest run', a curl line in a
        # smoke script, an eslint comment) sits in string literals.
        code = _PY_STRING.sub(lambda m: " " * len(m.group(0)), body)
        for label, pattern, _ in ACTION_RULES:
            if label in ("probe_http", "syntax_check", "test") and pattern.search(code):
                labels.add(label)
    elif script and script.group(1) == "node":
        for m in _NODE_WRITE.finditer(body):
            rel = _ws_path(m.group(2), cwd)
            if rel:
                out.writes.append(Write(rel, "write"))
                labels.add("write_file")
        if _NODE_PROBE.search(body):
            labels.add("probe_http")
    elif script:  # bash/sh heredoc: its body is more shell
        inner = analyze_shell(body, workdir=cwd)
        labels.update(inner.labels)
        out.writes.extend(inner.writes)
    elif target:
        rel = _ws_path(target.group(1), cwd)
        if rel:
            appending = ">>" in seg or " -a " in seg
            out.writes.append(Write(rel, "edit" if appending else "write", None if appending else body))
            labels.add("edit" if appending else "write_file")


def analyze_shell(command: str, workdir: str | None = None) -> ShellAnalysis:
    """Labels and workspace writes of one bash command (see :data:`ACTION_RULES`)."""
    out = ShellAnalysis()
    if _SUBMIT.match(command or ""):
        out.labels = ["submit"]
        return out
    labels: set[str] = set()
    cwd = workdir or WORKSPACE_DIR
    items = split_heredocs(command or "")
    raw = "\n".join(line for line, _ in items)
    pending = [body for _, bodies in items for body in bodies]
    for seg, seg_masked in _segments(raw, mask_quotes(raw)):
        m = _CD.match(seg)
        if m:
            cwd = posixpath.normpath(posixpath.join(cwd, m.group(1).strip("'\"")))
            continue
        word, head = _head_word(seg, seg_masked)
        if not word:
            continue
        out.last_head = word
        # A quoted string is data (``pkill -f 'python3 app.py'``, ``grep "npm run build"``,
        # ``git commit -m "add pytest"``) unless inline code runs it (``python3 -c``, ``bash -c``).
        rule_text = seg if _INLINE_CODE.search(seg_masked) else mask_quotes(seg, keep_substitutions=True)
        found = {label for label, pattern, _ in ACTION_RULES if pattern.search(rule_text)}
        script = SCRIPT_RUN.match(head)
        if script:
            found |= {label for label, pattern in SCRIPT_LABELS if pattern.search(script.group(1))}
        if found & _PACKAGE_LABELS:
            # 'pip install flask uvicorn httpx' / 'npm i -D vitest': package names, not runs.
            found &= _PACKAGE_LABELS
        labels |= found
        opened = len(_HEREDOC.findall(seg_masked))
        if opened:
            taken, pending = pending[:opened], pending[opened:]
            _heredoc_segment(seg, taken, cwd, labels, out)
            continue
        if _PY_INLINE.search(seg):
            writes = _python_writes(seg, cwd)
            out.writes.extend(writes)
            if writes:
                labels.add("edit" if any(w.kind == "edit" for w in writes) else "write_file")
            continue
        if _NODE_INLINE.search(seg_masked):
            node_writes = [rel for m in _NODE_WRITE.finditer(seg) if (rel := _ws_path(m.group(2), cwd))]
            if node_writes:  # node -e "...fs.writeFileSync('package.json', ...)": a write, not a lookup
                out.writes.extend(Write(rel, "write") for rel in node_writes)
                labels.add("write_file")
                continue
        if _SED_INPLACE.match(head):
            labels.add("edit")
            target = _last_arg(head)
            rel = _ws_path(target, cwd) if target else None
            if rel:
                out.writes.append(Write(rel, "edit"))
        elif word == "tee":
            m2 = _TEE.search(seg_masked)
            quoted = m2 is not None and ("'" in m2.group(1) or '"' in m2.group(1))
            rel = _ws_path(m2.group(1), cwd) if m2 and not quoted else None
            if rel:
                out.writes.append(Write(rel, "write"))
                labels.add("write_file")
        elif word in _EXPLORE_WORDS or _VERSION_PROBE.match(head) or _PIP_INSPECT.match(head):
            labels.add("explore")
        elif word in _PROCESS_WORDS:
            labels.add("process")
        # stdout redirects into workspace files, read from shell syntax only (quotes masked).
        for fd, target in _REDIRECT.findall(seg_masked):
            if fd == "2" or _NOT_A_FILE.search(target) or "'" in target or '"' in target:
                continue
            rel = _ws_path(target, cwd)
            if rel:
                out.writes.append(Write(rel, "write"))
                labels.add("write_file")
    out.labels = [c for c in CATEGORY_PRIORITY if c in labels]
    return out


# ── per-run parsing ───────────────────────────────────────────────────────


@dataclass
class Action:
    index: int  # 1-based, over every tool call of the run in call order
    seq: int
    call_id: str
    name: str
    args: dict[str, Any]
    category: str
    labels: list[str]
    summary: str
    writes: list[Write] = field(default_factory=list)
    outcome: str = "unknown"
    exit_code: int | None = None
    duration_s: float | None = None
    build_ok: bool | None = None
    tests_ok: bool | None = None
    error: str | None = None
    result: str | None = None

    @property
    def failed(self) -> bool:
        return self.outcome in FAILED_OUTCOMES


def summarize_args(name: str, args: dict[str, Any]) -> str:
    if "_raw" in args:
        text = str(args["_raw"])
    elif name in SHELL_TOOLS or "command" in args:
        text = str(args.get("command") or "")
        if args.get("workdir"):
            text = f"[{args['workdir']}] {text}"
    elif args.get("filePath") or args.get("file_path") or args.get("path"):
        text = str(args.get("filePath") or args.get("file_path") or args.get("path"))
        if args.get("pattern"):
            text = f"{args['pattern']} in {text}"
    elif args.get("pattern"):
        text = str(args["pattern"])
    elif isinstance(args.get("todos"), list):
        text = f"{len(args['todos'])} todos"
    elif args.get("description") or args.get("prompt"):
        text = str(args.get("description") or args.get("prompt"))
    else:
        text = json.dumps(args, ensure_ascii=False, default=str)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= SUMMARY_CHARS else text[: SUMMARY_CHARS - 1] + "…"


def classify_action(name: str, args: dict[str, Any]) -> tuple[str, list[str], list[Write], str]:
    """(primary category, all labels, workspace writes, last shell command word) of one tool call."""
    tool = (name or "").lower()
    if tool in SHELL_TOOLS or (tool not in TOOL_CATEGORIES and isinstance(args.get("command"), str)):
        shell = analyze_shell(str(args.get("command") or ""), workdir=args.get("workdir"))
        labels = shell.labels or ["other"]
        return labels[0], labels, shell.writes, shell.last_head
    category = TOOL_CATEGORIES.get(tool, "other")
    writes: list[Write] = []
    path = args.get("filePath") or args.get("file_path") or args.get("path")
    if category in ("write_file", "edit") and isinstance(path, str):
        rel = _ws_path(path, WORKSPACE_DIR)
        if rel:
            content = (
                args.get("content") if category == "write_file" and isinstance(args.get("content"), str) else None
            )
            writes.append(Write(rel, "write" if category == "write_file" else "edit", content))
    return category, [category], writes, ""


def _opencode_tool_events(events: Iterable[dict]) -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    for event in events:
        if event.get("type") != "tool_use":
            continue
        part = event.get("part") or {}
        call_id = part.get("callID")
        if call_id:
            by_id[str(call_id)] = part
    return by_id


def _tool_results(calls: list[dict]) -> dict[str, str]:
    results: dict[str, str] = {}
    for call in calls:
        for message in (call.get("request") or {}).get("messages") or []:
            if isinstance(message, dict) and message.get("role") == "tool" and message.get("tool_call_id"):
                results[str(message["tool_call_id"])] = _text(message.get("content"))
    return results


def _build_outcome(output: str) -> bool | None:
    ok = [m.end() for m in _BUILD_OK.finditer(output)]
    fail = [m.end() for m in _BUILD_FAIL.finditer(output)]
    if not ok and not fail:
        return None
    return bool(ok) and (not fail or max(ok) > max(fail))


def _tests_outcome(output: str) -> bool | None:
    if _TEST_FAIL.search(output):
        return False
    if _TEST_OK.search(output):
        return True
    return None


def _set_outcome(action: Action, result: str | None, event: dict | None, last_head: str) -> None:
    """Outcome of one action from OpenCode's event (preferred) or the tool result in the gateway log."""
    output = result or ""
    if event is not None:
        state = event.get("state") or {}
        meta = state.get("metadata") or {}
        times = state.get("time") or {}
        start, end = _float(times.get("start")), _float(times.get("end"))
        if start is not None and end is not None and end >= start:
            action.duration_s = round((end - start) / 1000, 3)
        if state.get("status") == "error":
            error = str(state.get("error") or "")
            action.error = error[:2000]
            if _POLICY_DENIAL.search(error):
                action.outcome = "policy_denial"
            elif _EDIT_NO_MATCH.search(error):
                action.outcome = "edit_no_match"
            else:
                action.outcome = "tool_error"
            return
        output = str(state.get("output") or output)
        if action.name in SHELL_TOOLS:
            exit_code = _int(meta.get("exit"))
            action.exit_code = exit_code
            if exit_code is None:
                action.outcome = "killed" if _OPENCODE_TIMEOUT.search(output) else "unknown"
            else:
                action.outcome = _exit_outcome(exit_code, last_head)
        elif state.get("status") == "completed":
            action.outcome = "ok"
    elif result is not None:
        parsed = None
        if output.lstrip().startswith("{") and '"returncode"' in output[:200]:
            try:
                parsed = json.loads(output)
            except ValueError:
                parsed = None
        if isinstance(parsed, dict) and "returncode" in parsed:  # mini-swe-agent
            rc = _int(parsed.get("returncode"))
            action.exit_code = rc
            info = str(parsed.get("exception_info") or "")
            output = str(parsed.get("output") or "")
            if rc == -1 and "not executed" in info:
                action.outcome = "not_executed"
            elif rc == -1 and info:
                action.outcome = "tool_error"
                action.error = info[:2000]
            else:
                action.outcome = _exit_outcome(rc, last_head) if rc is not None else "unknown"
        elif _POLICY_DENIAL.search(output):
            action.outcome = "policy_denial"
        elif _OPENCODE_TIMEOUT.search(output):
            action.outcome = "killed"
        elif _EDIT_NO_MATCH.search(output[:500]):
            action.outcome = "edit_no_match"
        else:
            action.outcome = "unknown"
    # Piped commands report the pipe's exit code (``npm run build | tail``): read the output.
    if "build" in action.labels:
        action.build_ok = _build_outcome(output)
        if action.build_ok is None and action.exit_code is not None and action.category == "build":
            action.build_ok = action.exit_code == 0 if action.outcome != "killed" else False
        if action.build_ok is False and action.outcome in ("ok", "no_match"):
            action.outcome = "output_error"
    if "test" in action.labels:
        action.tests_ok = _tests_outcome(output)
        if action.tests_ok is None and action.exit_code is not None and action.category == "test":
            action.tests_ok = action.exit_code == 0
        if action.tests_ok is False and action.outcome in ("ok", "no_match") and action.category == "test":
            action.outcome = "output_error"
    action.result = output


def _exit_outcome(code: int, last_head: str) -> str:
    if code == 0:
        return "ok"
    if code in _KILL_CODES:
        return "killed"
    if code == 127:
        return "not_found"
    if code == 1 and last_head in _NO_MATCH_HEADS:
        return "no_match"
    return "nonzero_exit"


@dataclass
class ParsedRun:
    calls: list[dict]  # sorted by seq
    roles: dict[int, str]
    actions: list[Action]
    actions_by_seq: dict[int, list[Action]]
    format_error_seqs: set[int]
    harness_events: bool
    mini_traj: bool


def parse_run(
    calls: list[dict],
    *,
    harness_events: list[dict] | None = None,
    mini_traj: dict | None = None,
    keep_results: bool = False,
) -> ParsedRun:
    """Parse the call log (+ native events) into ordered, classified actions."""
    ordered = sorted((c for c in calls if isinstance(c, dict)), key=lambda c: _int(c.get("seq")) or 0)
    events = _opencode_tool_events(harness_events or [])
    results = _tool_results(ordered)
    roles: dict[int, str] = {}
    actions: list[Action] = []
    by_seq: dict[int, list[Action]] = {}
    format_errors: set[int] = set()
    for call in ordered:
        seq = _int(call.get("seq")) or 0
        messages = (call.get("request") or {}).get("messages") or []
        roles[seq] = classify_role(messages)
        if messages and isinstance(messages[-1], dict) and messages[-1].get("role") == "user":
            if _FORMAT_ERROR.search(_text(messages[-1].get("content"))[:400]):
                format_errors.add(seq)
        response = call.get("response") or {}
        for tool_call in response.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            name = str(tool_call.get("name") or (tool_call.get("function") or {}).get("name") or "unknown")
            args = parse_arguments(
                tool_call.get("arguments")
                if "arguments" in tool_call
                else (tool_call.get("function") or {}).get("arguments"),
            )
            category, labels, writes, last_head = classify_action(name, args)
            action = Action(
                index=len(actions) + 1,
                seq=seq,
                call_id=str(tool_call.get("id") or ""),
                name=name,
                args=args,
                category=category,
                labels=labels,
                summary=summarize_args(name, args),
                writes=writes,
            )
            _set_outcome(action, results.get(action.call_id), events.get(action.call_id), last_head)
            if not keep_results:
                action.result = None
            actions.append(action)
            by_seq.setdefault(seq, []).append(action)
    return ParsedRun(
        calls=ordered,
        roles=roles,
        actions=actions,
        actions_by_seq=by_seq,
        format_error_seqs=format_errors,
        harness_events=bool(events),
        mini_traj=bool(mini_traj),
    )


# ── record ────────────────────────────────────────────────────────────────


def _usage(call: dict) -> dict[str, Any]:
    return (call.get("response") or {}).get("usage") or {}


def _cached(usage: dict) -> int | None:
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict) and "cached_tokens" in details:
        return _int(details.get("cached_tokens"))
    return None


def _reasoning(usage: dict) -> int | None:
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict) and "reasoning_tokens" in details:
        return _int(details.get("reasoning_tokens"))
    return None


def _time_section(calls: list[dict], *, events: list[dict] | None, run_started: float | None, wall: float | None):
    timed = sorted(
        ((t, _float(c.get("duration_s")) or 0.0) for c in calls if (t := _float(c.get("ts"))) is not None),
    )
    llm_s = round(sum(_float(c.get("duration_s")) or 0.0 for c in calls), 2)
    section: dict[str, Any] = {
        "llm_s": llm_s,
        "between_calls_s": None,
        "llm_share": None,
        "tool_exec_s": None,
        "first_call_at": None,
        "last_call_at": None,
        "head_s": None,
        "tail_s": None,
    }
    if timed:
        gaps = 0.0
        end = timed[0][0] + timed[0][1]
        for start, duration in timed[1:]:
            gaps += max(0.0, start - end)
            end = max(end, start + duration)
        section["between_calls_s"] = round(gaps, 2)
        section["llm_share"] = _ratio(llm_s, llm_s + gaps)
        section["first_call_at"] = _iso(timed[0][0])
        section["last_call_at"] = _iso(timed[-1][0])
        if run_started is not None:
            section["head_s"] = round(max(0.0, timed[0][0] - run_started), 2)
            if wall:
                section["tail_s"] = round(max(0.0, run_started + float(wall) - end), 2)
    tool_events = [e for e in events or [] if e.get("type") == "tool_use"]
    if tool_events:
        total = 0.0
        for event in tool_events:
            times = ((event.get("part") or {}).get("state") or {}).get("time") or {}
            start, stop = _float(times.get("start")), _float(times.get("end"))
            if start is not None and stop is not None and stop >= start:
                total += (stop - start) / 1000
        section["tool_exec_s"] = round(total, 2)
    return section


def _observed_inputs(first: dict | None) -> dict[str, Any] | None:
    if first is None:
        return None
    request = first.get("request") or {}
    messages = request.get("messages") or []
    prompt = system_prompt(messages)
    template = prompt
    for pattern, placeholder in _VARYING_PROMPT_LINES:
        template = pattern.sub(placeholder, template)
    tools = request.get("tools") or []
    names = sorted(
        str((t.get("function") or {}).get("name") or t.get("name") or "") for t in tools if isinstance(t, dict)
    )
    spec = json.dumps(tools, sort_keys=True, separators=(",", ":"), ensure_ascii=False) if tools else ""
    return {
        "seq": _int(first.get("seq")),
        "system_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest() if prompt else None,
        "system_prompt_template_sha256": hashlib.sha256(template.encode()).hexdigest() if prompt else None,
        "system_prompt_chars": len(prompt),
        "system_messages": sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "system"),
        "tools_offered": names,
        "tool_spec_sha256": hashlib.sha256(spec.encode()).hexdigest() if spec else None,
        "tool_spec_chars": len(spec),
        "first_call_prompt_tokens": _int(_usage(first).get("prompt_tokens")),
        "first_message_count": len(messages),
    }


def _first_pass(writes: list[tuple[int, Write]], final_files: dict[str, str] | None) -> dict[str, Any] | None:
    if not final_files:
        return None
    first: dict[str, str] = {}
    for _, write in writes:
        if write.path not in first and write.kind == "write" and write.content is not None:
            first[write.path] = write.content
    known = {p: c for p, c in first.items() if p in final_files}
    if not known:
        return {
            "method": "first whole-file write (write tool / cat-tee heredoc) vs final file; difflib line ratio",
            "files_with_first_version": 0,
            "final_files": len(final_files),
            "coverage": 0.0,
            "unchanged": None,
            "mean_similarity": None,
        }
    sims: list[float] = []
    unchanged = 0
    for path, content in known.items():
        final = final_files[path]
        if content.rstrip("\n") == final.rstrip("\n"):
            unchanged += 1
            sims.append(1.0)
            continue
        a, b = content.splitlines(), final.splitlines()
        if len(a) > FIRST_PASS_MAX_LINES or len(b) > FIRST_PASS_MAX_LINES:
            continue
        sims.append(difflib.SequenceMatcher(None, a, b, autojunk=False).ratio())
    return {
        "method": "first whole-file write (write tool / cat-tee heredoc) vs final file; difflib line ratio",
        "files_with_first_version": len(known),
        "final_files": len(final_files),
        "coverage": _ratio(len(known), len(final_files)),
        "unchanged": unchanged,
        "mean_similarity": round(statistics.fmean(sims), 4) if sims else None,
    }


def list_cost_summary(calls: list[dict]) -> dict[str, Any] | None:
    """List-price equivalent of a subscription-billed run (OpenCode Go ``usage.opencode``), or None."""
    total = 0.0
    seen = 0
    sources: Counter[str] = Counter()
    for call in calls:
        extra = _usage(call).get("opencode")
        if not isinstance(extra, dict):
            continue
        value = _float(extra.get("list_cost"))
        if value is None:
            continue
        seen += 1
        total += value
        if extra.get("billing_source"):
            sources[str(extra["billing_source"])] += 1
    if not seen:
        return None
    return {
        "list_cost": round(total, 8),
        "billing_source": sources.most_common(1)[0][0] if sources else None,
        "calls_priced": seen,
    }


def rule_table() -> dict[str, Any]:
    """The published classification rules (for the UI / methods section)."""
    return {
        "parser_version": PARSER_VERSION,
        "priority": list(CATEGORY_PRIORITY),
        "categories": CATEGORY_HELP,
        "tool_categories": TOOL_CATEGORIES,
        "shell_rules": [
            *({"label": label, "pattern": p.pattern, "description": d} for label, p, d in ACTION_RULES),
            *(
                {
                    "label": label,
                    "pattern": f"{SCRIPT_RUN.pattern} with the script name matching {p.pattern}",
                    "description": "a *.sh script run as the command (bash x.sh, ./x.sh, nohup x.sh &)",
                }
                for label, p in SCRIPT_LABELS
            ),
        ],
        "roles": [{"role": role, "pattern": p.pattern, "description": d} for role, p, d in ROLE_RULES],
        "failed_outcomes": sorted(FAILED_OUTCOMES),
        "notes": [
            "Shell commands are split on && || ; & and newlines; heredoc bodies are removed before matching "
            "(cat/tee heredocs count as whole-file writes, python/node heredoc bodies are scanned separately, "
            "a Python script's string literals excluded).",
            "Quoted strings are data (pkill -f 'python3 app.py' does not start a server) unless inline code "
            "runs them (python3 -c, node -e, bash -c).",
            "Piped commands report the pipe's exit code, so builds/tests are judged from their output "
            "(vite '✓ built in' vs 'error during build'; a test summary fails only with a non-zero failure "
            "count or a FAIL line).",
            "Exit code 1 from grep/rg/diff/test is 'no_match', not a failure.",
            "failed_actions leaves out harness policy denials (the sandbox's permission rules, e.g. OpenCode's "
            "external_directory deny): they are counted in policy_denials.",
            "Killed = exit -9/-15/124/137/143 or OpenCode's shell-tool timeout (120 s default, or the "
            "timeout the agent set).",
        ],
    }


def build_record(
    calls: list[dict],
    *,
    harness: str,
    harness_version: str | None = None,
    harness_events: list[dict] | None = None,
    mini_traj: dict | None = None,
    gateway_events: list[dict] | None = None,
    wall_seconds: float | None = None,
    harness_report: dict | None = None,
    final_files: dict[str, str] | None = None,
    parsed: ParsedRun | None = None,
) -> dict[str, Any]:
    """The trajectory record of one agent run (``result_data.agent.trajectory``)."""
    run = parsed or parse_run(calls, harness_events=harness_events, mini_traj=mini_traj)
    ok_calls = [c for c in run.calls if not _call_failed(c)]
    comparable = harness not in COMPARABLE_HARNESSES_WITHOUT_TOOLS and bool(run.calls)
    reason = None
    if harness in COMPARABLE_HARNESSES_WITHOUT_TOOLS:
        reason = "single-turn edit format: the model answers with edit blocks, not tool calls"
    elif not run.calls:
        reason = "no LLM calls recorded"

    actions = run.actions
    work = [a for a in actions if a.category != "submit"]
    categories = Counter(a.category for a in work)
    activity = Counter(label for a in work for label in a.labels)
    outcomes = Counter(a.outcome for a in work)
    failure_kinds = {k: v for k, v in outcomes.items() if k in FAILED_OUTCOMES}
    builds = [a for a in work if "build" in a.labels]
    probes = [a for a in work if "probe_http" in a.labels]
    first_build = builds[0].index if builds else None

    writes = [(a.index, w) for a in work for w in a.writes]
    per_file = Counter(w.path for _, w in writes)
    after_build = sum(1 for idx, _ in writes if first_build is not None and idx > first_build)

    roles = Counter(run.roles.values())
    main_ok = [c for c in ok_calls if run.roles.get(_int(c.get("seq")) or 0) == "main"]
    # Unreported counts are left out (never read as 0): a call without usage is not
    # a 0-token context, and a call without cached/reasoning details does not enter
    # that ratio's denominator.
    series = [x for c in main_ok if (x := _int(_usage(c).get("prompt_tokens"))) is not None]
    cache_pairs = [
        (x, _int(_usage(c).get("prompt_tokens")) or 0) for c in ok_calls if (x := _cached(_usage(c))) is not None
    ]
    reasoning_pairs = [
        (x, _int(_usage(c).get("completion_tokens")) or 0)
        for c in ok_calls
        if (x := _reasoning(_usage(c))) is not None
    ]
    run_started = next(
        (_float(e.get("ts")) for e in gateway_events or [] if e.get("event") == "started"),
        None,
    )
    report = harness_report or {}
    report_steps = _int(report.get("steps"))
    report_tools = _int(report.get("tool_call_total"))
    if report_tools is None and isinstance(report.get("tool_calls"), dict):
        report_tools = sum(_int(v) or 0 for v in report["tool_calls"].values())
    first_main = next((c for c in run.calls if run.roles.get(_int(c.get("seq")) or 0) == "main"), None)
    no_tool = [
        c for c in main_ok[:-1] if not (c.get("response") or {}).get("tool_calls")
    ]  # the final answer legitimately has none

    return {
        "schema_version": TRAJECTORY_SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
        "computed_at": datetime.now(tz=UTC).isoformat(),
        "harness": harness,
        "harness_version": harness_version,
        "source": {
            "calls": "gateway",
            "n_calls": len(run.calls),
            "n_ok_calls": len(ok_calls),
            "harness_events": run.harness_events,
            "mini_traj": run.mini_traj,
        },
        "comparable": comparable,
        "comparable_reason": reason,
        # A harness without native tool calls (Aider) has no tool calls to count: not a measured 0.
        "tool_calls_gateway": len(actions) if comparable else None,
        "actions_total": len(work) if comparable else None,
        "tool_names": dict(Counter(a.name for a in actions).most_common()),
        "action_categories": dict(categories.most_common()),
        "activity_counts": dict(activity.most_common()),
        "outcomes": dict(outcomes.most_common()),
        # A harness policy denial is the sandbox's rule, not the agent's failure:
        # counted in policy_denials (and failure_kinds), not here.
        "failed_actions": sum(v for k, v in failure_kinds.items() if k not in POLICY_OUTCOMES) if comparable else None,
        "failure_kinds": failure_kinds,
        "killed_actions": outcomes.get("killed", 0) if comparable else None,
        "policy_denials": outcomes.get("policy_denial", 0) if comparable else None,
        "edit_no_match": outcomes.get("edit_no_match", 0) if comparable else None,
        "outcome_unknown": outcomes.get("unknown", 0),
        "format_errors": len(run.format_error_seqs),
        "no_tool_responses": len(no_tool),
        "verification": {
            "built": bool(builds) if comparable else None,
            "ran_server": (activity.get("run_server", 0) > 0) if comparable else None,
            "probed_http": bool(probes) if comparable else None,
            "ran_tests": (activity.get("test", 0) > 0) if comparable else None,
            "syntax_checked": (activity.get("syntax_check", 0) > 0) if comparable else None,
            "n_builds": len(builds) if comparable else None,
            "builds_ok": sum(1 for a in builds if a.build_ok is True),
            "builds_failed": sum(1 for a in builds if a.build_ok is False),
            "last_build_ok": builds[-1].build_ok if builds else None,
            "first_build_action": first_build,
            "first_probe_action": probes[0].index if probes else None,
        },
        "files_written": len(per_file) if comparable else None,
        "write_events": len(writes) if comparable else None,
        "max_writes_per_file": (max(per_file.values()) if per_file else 0) if comparable else None,
        "writes_after_first_build": after_build if first_build is not None else None,
        "most_rewritten": [{"path": p, "writes": n} for p, n in per_file.most_common(5) if n > 1],
        "first_pass": _first_pass(writes, final_files) if comparable else None,
        "time": _time_section(run.calls, events=harness_events, run_started=run_started, wall=wall_seconds),
        "context": {
            "prompt_tokens_first": series[0] if series else None,
            "prompt_tokens_last": series[-1] if series else None,
            "prompt_tokens_max": max(series) if series else None,
            "series": series,
            "series_scope": "successful main-role calls that report prompt tokens, in call order",
        },
        "cache_hit_ratio": _ratio(sum(x for x, _ in cache_pairs), sum(n for _, n in cache_pairs))
        if cache_pairs
        else None,
        "reasoning_share": _ratio(sum(x for x, _ in reasoning_pairs), sum(n for _, n in reasoning_pairs))
        if reasoning_pairs
        else None,
        "call_roles": dict(roles.most_common()),
        "model_turns": len(main_ok),
        "observed_inputs": _observed_inputs(first_main),
        "harness_counters": {
            "steps": report_steps,
            "tool_calls": report_tools,
            "report_missing": bool(actions) and not report_steps and not report_tools,
        },
        "list_cost": list_cost_summary(run.calls),
    }


BEHAVIOUR_KEYS = (
    "parser_version",
    "actions_total",
    "tool_calls_gateway",
    "failed_actions",
    "killed_actions",
    "policy_denials",
    "builds",
    "ran_server",
    "probed_http",
    "ran_tests",
    "files_written",
    "writes_after_first_build",
    "llm_seconds",
    "between_calls_seconds",
    "context_tokens_first",
    "context_tokens_max",
    "cache_hit_ratio",
    "reasoning_share",
    "first_build_action_index",
    "comparable",
)


def behaviour_summary(record: dict[str, Any]) -> dict[str, Any]:
    """``metrics.behaviour`` (contract C2): scalar summary of a trajectory record."""
    verification = record.get("verification") or {}
    time_section = record.get("time") or {}
    context = record.get("context") or {}
    failed = record.get("failed_actions")
    actions = record.get("actions_total")
    # Outcomes unknowable for every action (no exit codes anywhere): not a measured 0.
    if failed is not None and actions and record.get("outcome_unknown") == actions:
        failed = None
    return {
        "parser_version": record.get("parser_version"),
        "actions_total": actions,
        # A record built before parser 1.2 stored 0 for a harness without tool calls.
        "tool_calls_gateway": record.get("tool_calls_gateway") if record.get("comparable") else None,
        "failed_actions": failed,
        "killed_actions": record.get("killed_actions"),
        "policy_denials": record.get("policy_denials"),
        "builds": verification.get("n_builds"),
        "ran_server": verification.get("ran_server"),
        "probed_http": verification.get("probed_http"),
        "ran_tests": verification.get("ran_tests"),
        "files_written": record.get("files_written"),
        "writes_after_first_build": record.get("writes_after_first_build"),
        "llm_seconds": time_section.get("llm_s"),
        "between_calls_seconds": time_section.get("between_calls_s"),
        "context_tokens_first": context.get("prompt_tokens_first"),
        "context_tokens_max": context.get("prompt_tokens_max"),
        "cache_hit_ratio": record.get("cache_hit_ratio"),
        "reasoning_share": record.get("reasoning_share"),
        "first_build_action_index": verification.get("first_build_action"),
        "comparable": bool(record.get("comparable")),
    }


# ── job wiring ────────────────────────────────────────────────────────────


def _mini_traj(data: bytes | None) -> dict | None:
    if not data:
        return None
    try:
        value = json.loads(data)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def apply_record(job, record: dict[str, Any]) -> None:
    """Write the record, ``metrics.behaviour`` and the list cost onto *job* (in memory; caller saves).

    Additive: harness self-reports, ``cost`` and every measured value stay as they are.
    """
    result = job.result_data if isinstance(job.result_data, dict) else {}
    agent = result.get("agent")
    if not isinstance(agent, dict):
        return
    agent["trajectory"] = record
    agent.pop("trajectory_error", None)
    metrics = dict(job.metrics or {})
    metrics["behaviour"] = behaviour_summary(record)
    list_cost = record.get("list_cost")
    if list_cost:
        metrics["list_cost"] = list_cost["list_cost"]
        metrics["billing_source"] = list_cost["billing_source"]
    job.metrics = metrics


def attach_trajectory(
    job,
    calls: list[dict],
    *,
    events_text: bytes | str | None = None,
    mini_traj: bytes | None = None,
    gateway_events: list[dict] | None = None,
) -> dict[str, Any] | None:
    """Ingestion hook: build and attach the record; never raises (stores ``trajectory_error``)."""
    agent = (job.result_data or {}).get("agent") if isinstance(job.result_data, dict) else None
    if not isinstance(agent, dict):
        return None
    try:
        record = build_record(
            calls,
            harness=str(agent.get("harness") or ""),
            harness_version=agent.get("harness_version"),
            harness_events=parse_jsonl(events_text) if agent.get("harness") == "opencode" else None,
            mini_traj=_mini_traj(mini_traj),
            gateway_events=gateway_events,
            wall_seconds=_float(agent.get("wall_seconds")),
            harness_report=agent.get("harness_report"),
            final_files=(job.result_data or {}).get("files"),
        )
        apply_record(job, record)
    except Exception as exc:  # analytics must never change the job outcome
        logger.exception("Trajectory analytics failed for job %s", getattr(job, "id", "?"))
        agent["trajectory_error"] = f"{type(exc).__name__}: {exc}"[:500]
        return None
    return record


# ── run record on disk ────────────────────────────────────────────────────


class RunRecordUnavailable(Exception):  # noqa: N818 - a state, not a bug
    """The job's run-record files are missing, moved or changed since the run."""


@dataclass
class RunFiles:
    calls: list[dict]
    harness_events: list[dict]
    mini_traj: dict | None
    gateway_events: list[dict]


def run_record_root(job) -> Path:
    """The verified directory holding *job*'s run record (same rule as the download endpoint)."""
    from django.conf import settings

    agent = (job.result_data or {}).get("agent") or {}
    record = agent.get("run_record") or {}
    root = Path(settings.GENERATION_RUNS_ROOT) / str(job.id)
    recorded = Path(record.get("_dir") or root)
    if recorded != root and root not in recorded.parents:
        msg = "run record directory is outside the job's directory"
        raise RunRecordUnavailable(msg)
    return recorded


def _read(root: Path, name: str, record: dict, *, verify: bool) -> bytes | None:
    entry = record.get(name)
    path = root / name
    if not isinstance(entry, dict) or not path.is_file():
        return None
    data = path.read_bytes()
    if verify and entry.get("sha256") and hashlib.sha256(data).hexdigest() != entry["sha256"]:
        msg = f"{name} does not match its recorded sha256"
        raise RunRecordUnavailable(msg)
    return data


def calls_raw_from_disk(job, *, verify: bool = True) -> bytes:
    """The uncompressed gateway call log of *job* (``RunRecordUnavailable`` when missing or changed)."""
    record = ((job.result_data or {}).get("agent") or {}).get("run_record") or {}
    data = _read(run_record_root(job), "llm_calls.jsonl.gz", record, verify=verify)
    if data is None:
        msg = "llm_calls.jsonl.gz is not on disk"
        raise RunRecordUnavailable(msg)
    try:
        return gzip.decompress(data)
    except (OSError, EOFError) as exc:
        msg = f"llm_calls.jsonl.gz is unreadable: {exc}"
        raise RunRecordUnavailable(msg) from exc


def calls_from_disk(job, *, verify: bool = True) -> list[dict]:
    return parse_jsonl(calls_raw_from_disk(job, verify=verify))


def harness_events_from_disk(job, *, verify: bool = True) -> list[dict]:
    """OpenCode's event stream (``[]`` for other harnesses or when it is missing)."""
    agent = (job.result_data or {}).get("agent") or {}
    if agent.get("harness") != "opencode":
        return []
    raw = _read(run_record_root(job), "harness_events.jsonl.gz", agent.get("run_record") or {}, verify=verify)
    if not raw:
        return []
    try:
        return parse_jsonl(gzip.decompress(raw))
    except (OSError, EOFError):
        return []


def _traj_from_report(report: bytes | None) -> dict | None:
    if not report:
        return None
    try:
        with tarfile.open(fileobj=io.BytesIO(report), mode="r:*") as tar:
            member = next((m for m in tar.getmembers() if m.name.removeprefix("./") == "traj.json"), None)
            if member is None or not member.isfile():
                return None
            handle = tar.extractfile(member)
            return _mini_traj(handle.read() if handle else None)
    except (tarfile.TarError, OSError, EOFError):
        return None


def load_run_files(job, *, verify: bool = True) -> RunFiles:
    """Every input :func:`build_record` reads, from *job*'s run record on disk."""
    agent = (job.result_data or {}).get("agent") or {}
    record = agent.get("run_record") or {}
    root = run_record_root(job)
    calls = calls_from_disk(job, verify=verify)
    events = harness_events_from_disk(job, verify=verify)
    traj = _traj_from_report(_read(root, "harness_report.tar.gz", record, verify=verify))
    gateway_events = parse_jsonl(_read(root, "gateway_events.jsonl", record, verify=verify))
    return RunFiles(calls=calls, harness_events=events, mini_traj=traj, gateway_events=gateway_events)


def record_from_disk(job, files: RunFiles | None = None, parsed: ParsedRun | None = None) -> dict[str, Any]:
    agent = (job.result_data or {}).get("agent") or {}
    files = files or load_run_files(job)
    return build_record(
        files.calls,
        harness=str(agent.get("harness") or ""),
        harness_version=agent.get("harness_version"),
        harness_events=files.harness_events,
        mini_traj=files.mini_traj,
        gateway_events=files.gateway_events,
        wall_seconds=_float(agent.get("wall_seconds")),
        harness_report=agent.get("harness_report"),
        final_files=(job.result_data or {}).get("files"),
        parsed=parsed,
    )


# ── per-step views (endpoints) ────────────────────────────────────────────

STEP_TEXT_CHARS = 20_000
MAX_STEPS = 500


def _clip(value: Any, limit: int = STEP_TEXT_CHARS) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"\n… [{len(value) - limit} chars truncated]"
    return value


def step_rows(parsed: ParsedRun, *, limit: int = MAX_STEPS) -> tuple[list[dict[str, Any]], bool]:
    """One compact row per LLM call (timeline), at most *limit*."""
    first_ts = next((t for c in parsed.calls if (t := _float(c.get("ts"))) is not None), None)
    rows: list[dict[str, Any]] = []
    for call in parsed.calls[:limit]:
        seq = _int(call.get("seq")) or 0
        response = call.get("response") or {}
        usage = response.get("usage") or {}
        ts = _float(call.get("ts"))
        rows.append(
            {
                "seq": seq,
                "ts": _iso(ts),
                "offset_s": round(ts - first_ts, 2) if ts is not None and first_ts is not None else None,
                "duration_s": _float(call.get("duration_s")),
                "role": parsed.roles.get(seq, "main"),
                "status": _int(call.get("status")),
                "failed": _call_failed(call),
                "prompt_tokens": _int(usage.get("prompt_tokens")),
                "completion_tokens": _int(usage.get("completion_tokens")),
                "cached_tokens": _cached(usage),
                "reasoning_tokens": _reasoning(usage),
                "finish_reason": response.get("finish_reason"),
                "provider": response.get("provider"),
                "upstream_retries": _int(call.get("upstream_retries")),
                "format_error_feedback": seq in parsed.format_error_seqs,
                "has_reasoning": bool(response.get("reasoning")),
                "content_chars": len(response.get("content") or ""),
                "tool_calls": [
                    {
                        "index": a.index,
                        "name": a.name,
                        "category": a.category,
                        "labels": a.labels,
                        "summary": a.summary,
                        "outcome": a.outcome,
                        "failed": a.failed,
                        "exit_code": a.exit_code,
                    }
                    for a in parsed.actions_by_seq.get(seq, [])
                ],
            },
        )
    return rows, len(parsed.calls) > limit


def steps_from_artifacts(artifacts: Iterable) -> list[dict[str, Any]]:
    """Timeline rows from the compact ``agent_call_NNNN`` artifacts (when the run record is gone)."""
    rows: list[dict[str, Any]] = []
    for art in artifacts:
        match = re.fullmatch(r"agent_call_(\d+)", art.stage or "")
        if not match:
            continue
        response = art.response_payload or {}
        usage = response.get("usage") or {}
        tool_calls = []
        for tc in response.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            name = str(tc.get("name") or "unknown")
            args = parse_arguments(tc.get("arguments"))
            category, labels, _, _ = classify_action(name, args)
            tool_calls.append(
                {
                    "index": None,
                    "name": name,
                    "category": category,
                    "labels": labels,
                    "summary": summarize_args(name, args),
                    "outcome": "unknown",
                    "failed": False,
                    "exit_code": None,
                },
            )
        status = _int(response.get("status"))
        telemetry = getattr(art, "telemetry", None) or {}
        rows.append(
            {
                "seq": int(match.group(1)),
                "ts": telemetry.get("started_at") if isinstance(telemetry, dict) else None,
                "offset_s": None,
                "duration_s": _float(response.get("duration_s")),
                "role": None,  # the compact artifact has no system prompt to classify
                "status": status,
                "failed": status != 200 or bool(response.get("error")),
                "prompt_tokens": _int(usage.get("prompt_tokens")),
                "completion_tokens": _int(usage.get("completion_tokens")),
                "cached_tokens": _cached(usage),
                "reasoning_tokens": _reasoning(usage),
                "finish_reason": response.get("finish_reason"),
                "provider": response.get("provider"),
                "upstream_retries": None,
                "format_error_feedback": False,
                "has_reasoning": bool(response.get("reasoning")),
                "content_chars": len(response.get("content") or ""),
                "tool_calls": tool_calls,
            },
        )
    rows.sort(key=lambda r: r["seq"])
    return rows


_SEQ_PREFIX = re.compile(rb'^\{\s*"seq"\s*:\s*(\d+)')


def iter_call_lines(data: bytes) -> Iterable[tuple[int | None, bytes]]:
    """(seq or None, raw line) for every line of an uncompressed call log, without parsing JSON."""
    for line in data.splitlines():
        if not line.strip():
            continue
        match = _SEQ_PREFIX.match(line)
        yield (int(match.group(1)) if match else None), line


def step_detail(calls_raw: bytes, seq: int, *, harness_events: list[dict] | None = None) -> dict[str, Any] | None:
    """Everything about one call: inputs since the previous turn, response, tool calls and their results."""
    lines = list(iter_call_lines(calls_raw))
    target = None
    position = None
    for i, (line_seq, line) in enumerate(lines):
        if line_seq == seq or (line_seq is None and f'"seq": {seq},'.encode() in line[:200]):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if _int(row.get("seq")) == seq:
                target, position = row, i
                break
    if target is None or position is None:
        return None
    request = target.get("request") or {}
    response = target.get("response") or {}
    messages = request.get("messages") or []
    last_assistant = max(
        (i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == "assistant"),
        default=-1,
    )
    trailing = [m for m in messages[last_assistant + 1 :] if isinstance(m, dict) and m.get("role") != "system"][-12:]
    tool_calls = [tc for tc in response.get("tool_calls") or [] if isinstance(tc, dict)]
    wanted = {str(tc.get("id")) for tc in tool_calls if tc.get("id")}
    results: dict[str, str] = {}
    for _, line in lines[position + 1 :]:
        if not wanted - set(results):
            break
        if not any(w.encode() in line for w in wanted - set(results)):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        for message in (row.get("request") or {}).get("messages") or []:
            if (
                isinstance(message, dict)
                and message.get("role") == "tool"
                and str(message.get("tool_call_id")) in wanted
            ):
                results[str(message["tool_call_id"])] = _text(message.get("content"))
    events = _opencode_tool_events(harness_events or [])
    detail_calls = []
    for tc in tool_calls:
        name = str(tc.get("name") or "unknown")
        args = parse_arguments(tc.get("arguments"))
        category, labels, _, last_head = classify_action(name, args)
        action = Action(
            index=0,
            seq=seq,
            call_id=str(tc.get("id") or ""),
            name=name,
            args=args,
            category=category,
            labels=labels,
            summary=summarize_args(name, args),
        )
        _set_outcome(action, results.get(action.call_id), events.get(action.call_id), last_head)
        raw_args = tc.get("arguments")
        pretty = json.dumps(args, indent=2, ensure_ascii=False) if "_raw" not in args else str(raw_args)
        detail_calls.append(
            {
                "id": action.call_id,
                "name": name,
                "category": category,
                "labels": labels,
                "summary": action.summary,
                "arguments": _clip(pretty),
                "outcome": action.outcome,
                "failed": action.failed,
                "exit_code": action.exit_code,
                "duration_s": action.duration_s,
                "error": _clip(action.error),
                "result": _clip(action.result) if action.result is not None else None,
                "result_source": "harness_events"
                if action.call_id in events
                else ("gateway" if action.call_id in results else None),
            },
        )
    tools = request.get("tools") or []
    return {
        "seq": seq,
        "ts": _iso(_float(target.get("ts"))),
        "duration_s": _float(target.get("duration_s")),
        "status": _int(target.get("status")),
        "role": classify_role(messages),
        "upstream_retries": _int(target.get("upstream_retries")),
        "policy_diff": target.get("policy_diff"),
        "request": {
            "model": request.get("model"),
            "params": {k: v for k, v in request.items() if k not in ("messages", "tools")},
            "message_count": len(messages),
            "tools_offered": [
                str((t.get("function") or {}).get("name") or t.get("name") or "") for t in tools if isinstance(t, dict)
            ],
            "system_prompt": _clip(system_prompt(messages)),
            "new_messages": [
                {
                    "role": m.get("role"),
                    "name": m.get("name"),
                    "tool_call_id": m.get("tool_call_id"),
                    "content": _clip(_text(m.get("content"))),
                }
                for m in trailing
            ],
        },
        "response": {
            "id": response.get("id"),
            "model": response.get("model"),
            "provider": response.get("provider"),
            "finish_reason": response.get("finish_reason"),
            "content": _clip(response.get("content")),
            "reasoning": _clip(response.get("reasoning")),
            "usage": response.get("usage"),
            "error": response.get("error") or target.get("error"),
            "tool_calls": detail_calls,
        },
    }


def step_detail_from_artifact(art) -> dict[str, Any]:
    """Step detail from a compact ``agent_call_NNNN`` artifact (texts truncated at ingestion)."""
    request = art.request_payload or {}
    response = art.response_payload or {}
    calls = []
    for tc in response.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        name = str(tc.get("name") or "unknown")
        args = parse_arguments(tc.get("arguments"))
        category, labels, _, _ = classify_action(name, args)
        pretty = json.dumps(args, indent=2, ensure_ascii=False) if "_raw" not in args else str(tc.get("arguments"))
        calls.append(
            {
                "id": tc.get("id"),
                "name": name,
                "category": category,
                "labels": labels,
                "summary": summarize_args(name, args),
                "arguments": _clip(pretty),
                "outcome": "unknown",
                "failed": False,
                "exit_code": None,
                "duration_s": None,
                "error": None,
                "result": None,
                "result_source": None,
            },
        )
    last = request.get("last_message")
    match = re.fullmatch(r"agent_call_(\d+)", art.stage or "")
    return {
        "seq": int(match.group(1)) if match else None,
        "ts": None,
        "duration_s": _float(response.get("duration_s")),
        "status": _int(response.get("status")),
        "role": None,
        "upstream_retries": None,
        "policy_diff": request.get("policy_diff"),
        "request": {
            "model": request.get("model"),
            "params": request.get("params") or {},
            "message_count": request.get("message_count"),
            "tools_offered": [],
            "system_prompt": None,
            "new_messages": [{"role": None, "name": None, "tool_call_id": None, "content": last}] if last else [],
        },
        "response": {
            "id": response.get("id"),
            "model": response.get("model"),
            "provider": response.get("provider"),
            "finish_reason": response.get("finish_reason"),
            "content": response.get("content"),
            "reasoning": response.get("reasoning"),
            "usage": response.get("usage"),
            "error": response.get("error"),
            "tool_calls": calls,
        },
    }
