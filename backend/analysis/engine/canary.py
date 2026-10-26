"""Tool self-test: positive + negative controls on fixed fixtures (owner: API/research stage).

The fixture tree lives in ``data/canary/`` (``vulnerable/`` must produce the
expected rule ids per tool, ``clean/`` must produce no counted findings of the
tools' security/bug categories) with expectations in
``data/canary/expectations.yaml``. A self-test is an ordinary AnalysisRun
(purpose=canary) evaluated at the end, so it exercises the real pipeline.

Expectations (written by the toolchain stage)::

    version: 1
    fixture_dirs: {positive: vulnerable, negative: clean}
    tools:
      bandit:
        must_find:                       # every entry must match >= 1 finding of this tool
          - {rule: B608, path: vulnerable/backend/app.py}
          - {rule: "B6*"}                # glob on rule_id; path (glob) optional
        clean_categories: [security]     # no counted findings of these categories under clean/
        min_findings: 3                  # optional, findings under the positive dir

Verdict per tool: ``passed`` (succeeded and every expectation met),
``failed`` (the tool did not succeed, or an expectation was violated) or
``unverified`` (the tool has no expectations, or nothing to expect was
checked). Evaluations are computed from the stored run on demand, so they
always reflect the findings as persisted.
"""

from __future__ import annotations

import fnmatch
import functools
import hashlib
from pathlib import Path
from typing import Any

import yaml

from backend.analysis.engine import catalog
from backend.analysis.engine import executor
from backend.analysis.engine import plan as plan_mod
from backend.analysis.engine import sample as sample_mod
from backend.analysis.engine.catalog import AI_TOOL_SLUG
from backend.analysis.engine.catalog import ConfigError

CANARY_DIR = Path(__file__).resolve().parent.parent / "data" / "canary"
EXPECTATIONS_FILE = CANARY_DIR / "expectations.yaml"
EXPECTATIONS_VERSION = 1
DEFAULT_FIXTURE_DIRS = {"positive": "vulnerable", "negative": "clean"}
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}
SKIP_SUFFIXES = (".pyc", ".pyo")
MAX_FIXTURE_BYTES = 512 * 1024
# How many recent canary runs latest_per_tool() looks through.
PER_TOOL_LOOKBACK = 25


def _fixture_dirs(spec: dict[str, Any]) -> dict[str, str]:
    dirs = spec.get("fixture_dirs") or {}
    return {
        "positive": str(dirs.get("positive") or DEFAULT_FIXTURE_DIRS["positive"]).strip("/"),
        "negative": str(dirs.get("negative") or DEFAULT_FIXTURE_DIRS["negative"]).strip("/"),
    }


class ExpectationsError(ValueError):
    """``expectations.yaml`` is malformed (a toolchain-stage data bug)."""


def _must_find_entry(slug: str, raw: Any) -> dict[str, str]:
    entry = {"rule": raw} if isinstance(raw, str) else raw
    if not isinstance(entry, dict) or not entry.get("rule"):
        msg = f"canary expectations for {slug}: every must_find entry needs a 'rule'"
        raise ExpectationsError(msg)
    out = {"rule": str(entry["rule"])}
    if entry.get("path"):
        out["path"] = str(entry["path"])
    return out


@functools.cache
def _load_expectations(path: str, mtime_ns: int) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    tools = data.get("tools") if isinstance(data, dict) else None
    if not isinstance(tools, dict):
        msg = "canary expectations must be a mapping with a 'tools' mapping"
        raise ExpectationsError(msg)
    if data.get("version") != EXPECTATIONS_VERSION:
        msg = f"unsupported canary expectations version {data.get('version')!r} (expected {EXPECTATIONS_VERSION})"
        raise ExpectationsError(msg)
    normalized: dict[str, Any] = {}
    for slug, raw_spec in tools.items():
        spec = raw_spec or {}
        min_findings = spec.get("min_findings")
        normalized[str(slug)] = {
            "must_find": [_must_find_entry(str(slug), raw) for raw in spec.get("must_find") or []],
            "clean_categories": [str(c) for c in spec.get("clean_categories") or []],
            "min_findings": int(min_findings) if isinstance(min_findings, int) else None,
        }
    return {"version": EXPECTATIONS_VERSION, "fixture_dirs": _fixture_dirs(data), "tools": normalized}


def expectations() -> dict[str, Any]:
    """Normalized ``expectations.yaml`` (cached until the file changes)."""
    return _load_expectations(str(EXPECTATIONS_FILE), EXPECTATIONS_FILE.stat().st_mtime_ns)


def expectations_hash() -> str:
    """sha256 of the expectations file (provenance for canary results)."""
    return hashlib.sha256(EXPECTATIONS_FILE.read_bytes()).hexdigest()


def canary_files() -> dict[str, str]:
    """The positive and negative fixture trees as ``{"vulnerable/...": text, "clean/...": text}``."""
    dirs = _fixture_dirs(expectations())
    files: dict[str, str] = {}
    for name in (dirs["positive"], dirs["negative"]):
        root = CANARY_DIR / name
        if not root.is_dir():
            msg = f"canary fixture directory is missing: {root}"
            raise FileNotFoundError(msg)
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(CANARY_DIR)
            if not path.is_file() or set(rel.parts) & SKIP_DIRS or path.suffix in SKIP_SUFFIXES:
                continue
            data = path.read_bytes()
            if len(data) > MAX_FIXTURE_BYTES:
                continue
            try:
                files[rel.as_posix()] = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
    if not files:
        msg = f"no canary fixtures found under {CANARY_DIR}"
        raise FileNotFoundError(msg)
    return files


def selftest_config(tool_slugs: list[str] | None = None) -> dict[str, Any]:
    """The profile config of a self-test: the default profile's tool configs, AI off, every tool required."""
    static = [spec.slug for spec in catalog.static_specs()]
    slugs = list(dict.fromkeys(tool_slugs)) if tool_slugs else static
    if bad := [s for s in slugs if s not in static]:
        reason = "the AI reviewer is not self-tested" if AI_TOOL_SLUG in bad else "unknown or non-static tool"
        raise ConfigError({"tool_slugs": f"{reason}: {', '.join(bad)}"})
    config = plan_mod.default_profile_config()
    defaults = {t.get("slug"): t for t in config.get("tools", []) if isinstance(t, dict)}
    config["tools"] = [
        {"slug": slug, "required": True, "config": dict((defaults.get(slug) or {}).get("config") or {})}
        for slug in slugs
    ]
    config["reviewer"] = {**(config.get("reviewer") or {}), "enabled": False, "required": False}
    return plan_mod.validate_profile_config(config)


def run_selftest(*, user, tool_slugs: list[str] | None = None):
    """Submit a canary run for the given (default: all static) tools."""
    config = selftest_config(tool_slugs)
    frozen = plan_mod.freeze_plan(config=config)
    files = canary_files()
    smp = sample_mod.resolve_inline(files, frozen.get("scope"))
    return executor.submit_run(
        user=user,
        plan=frozen,
        source_code=files,
        sample=smp,
        purpose="canary",
        name="Tool self-test",
    )


def _under(path: str, directory: str) -> bool:
    return path == directory or path.startswith(f"{directory}/")


def _match(finding: dict[str, Any], entry: dict[str, Any]) -> bool:
    if not fnmatch.fnmatchcase(finding["rule_id"] or "", entry["rule"]):
        return False
    return not entry.get("path") or fnmatch.fnmatchcase(finding["file_path"] or "", entry["path"])


_FINDING_FIELDS = ("tool_slug", "rule_id", "file_path", "category", "counted")
# The run columns an evaluation reads (a canary run's source_code is ~12 KB of fixtures).
_RUN_FIELDS = ("id", "status", "plan", "plan_hash", "completed_at", "created_at")


def _plan_tools(run) -> dict[str, dict[str, Any]]:
    """``{slug: frozen plan tool entry}`` of a run, in plan order."""
    tools = (run.plan or {}).get("tools") if isinstance(run.plan, dict) else None
    return {t["slug"]: t for t in tools or [] if isinstance(t, dict) and t.get("slug")}


def evaluate(run) -> dict[str, Any]:
    """{passed: bool, tools: {slug: {passed, status, expected, found, missing, unexpected_in_clean}}}.

    Also: ``finished``, ``run_id``, ``run_status``, ``image_tag``,
    ``evaluated_at`` and per tool ``verdict`` (passed|failed|unverified),
    ``failure_reason``, ``version``, ``findings_total``, ``positive_findings``,
    ``min_findings`` and ``config_hash`` (the tool configuration the
    self-test ran, from the run's frozen plan).
    """
    results = {r.tool_slug: r for r in run.results.all()}
    findings = list(run.findings.values(*_FINDING_FIELDS))
    return _evaluate(run, results, findings)


def _evaluate(
    run,
    results: dict[str, Any],
    findings: list[dict[str, Any]],
    *,
    only: set[str] | None = None,
) -> dict[str, Any]:
    """:func:`evaluate` over preloaded tool results and finding rows (*only*: restrict to these tools)."""
    from backend.analysis.models import AnalysisRun

    spec = expectations()
    dirs = spec["fixture_dirs"]
    finished = run.status in AnalysisRun.TERMINAL_STATUSES
    by_tool: dict[str, list[dict[str, Any]]] = {}
    for f in findings:
        by_tool.setdefault(f["tool_slug"], []).append(f)
    plan_tools = _plan_tools(run)
    tools: dict[str, Any] = {}
    for slug in list(plan_tools) or sorted(results):
        if only is not None and slug not in only:
            continue
        result = results.get(slug)
        status = result.status if result else "missing"
        expected = spec["tools"].get(slug) or {"must_find": [], "clean_categories": [], "min_findings": None}
        tool_findings = by_tool.get(slug, [])
        found, missing = [], []
        for entry in expected["must_find"]:
            matches = [f for f in tool_findings if _match(f, entry)]
            if matches:
                found.append({**entry, "matched": sorted({f"{m['file_path']}:{m['rule_id']}" for m in matches})[:5]})
            else:
                missing.append(entry)
        clean = set(expected["clean_categories"])
        unexpected = [
            {"rule": f["rule_id"], "path": f["file_path"], "category": f["category"]}
            for f in tool_findings
            if _under(f["file_path"] or "", dirs["negative"]) and f["counted"] and f["category"] in clean
        ]
        positive = sum(1 for f in tool_findings if _under(f["file_path"] or "", dirs["positive"]))
        min_findings = expected["min_findings"]
        has_expectations = bool(expected["must_find"] or clean or min_findings is not None)
        if status != "succeeded":
            verdict = "failed"
        elif not has_expectations:
            verdict = "unverified"
        elif missing or unexpected or (min_findings is not None and positive < min_findings):
            verdict = "failed"
        else:
            verdict = "passed"
        tools[slug] = {
            "passed": verdict == "passed",
            "verdict": verdict,
            "status": status,
            "failure_reason": result.failure_reason if result else "missing",
            "version": result.tool_version if result else "",
            "config_hash": str((plan_tools.get(slug) or {}).get("config_hash") or ""),
            "expected": expected["must_find"],
            "found": found,
            "missing": missing,
            "unexpected_in_clean": unexpected[:50],
            "clean_categories": sorted(clean),
            "findings_total": len(tool_findings),
            "positive_findings": positive,
            "min_findings": min_findings,
            "has_expectations": has_expectations,
        }
    passed = finished and bool(tools) and all(t["verdict"] == "passed" for t in tools.values())
    return {
        "passed": passed,
        "finished": finished,
        "run_id": str(run.pk),
        "run_status": run.status,
        "image_tag": ((run.plan or {}).get("analyzer_image") or {}).get("tag") or "",
        "plan_hash": run.plan_hash,
        "evaluated_at": (run.completed_at or run.created_at).isoformat()
        if (run.completed_at or run.created_at)
        else None,
        "tools": tools,
    }


def _finished_canaries(image_tag: str | None):
    from backend.analysis.models import AnalysisRun

    qs = (
        AnalysisRun.objects.filter(
            purpose=AnalysisRun.Purpose.CANARY,
            # A cancelled self-test says nothing about the tools.
            status__in=(AnalysisRun.Status.COMPLETED, AnalysisRun.Status.PARTIAL, AnalysisRun.Status.FAILED),
        )
        .only(*_RUN_FIELDS)
        .order_by("-completed_at", "-created_at")
    )
    if image_tag:
        qs = qs.filter(plan__analyzer_image__tag=image_tag)
    return qs


def latest(image_tag: str | None = None) -> dict[str, Any] | None:
    """Most recent finished canary evaluation (optionally for one analyzer image)."""
    run = _finished_canaries(image_tag).first()
    return evaluate(run) if run is not None else None


def _run_tool_slugs(run) -> list[str]:
    planned = list(_plan_tools(run))
    if planned:
        return planned
    # A run without planned tools (never written by run_selftest) is evaluated over its results.
    return sorted(set(run.results.values_list("tool_slug", flat=True)))


def latest_per_tool(
    image_tag: str | None = None,
    *,
    config_hashes: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """``{slug: tool evaluation + run_id/image_tag/evaluated_at}`` from the newest canary run covering each tool.

    A self-test may cover only some tools, so readiness of a tool comes from
    the newest run that included it (on *image_tag* when given).

    *config_hashes* (``{slug: plan tool config_hash}``) asks for the controls
    of one plan: a tool's verdict then comes only from the newest run that
    self-tested *that exact configuration* (same config, tool version and
    spec). A positive control at another configuration (self-tests run the
    default profile's configs) says nothing about a narrowed one (e.g.
    ``severity: high`` or a skip list), so such a tool is reported with
    ``verdict: "config_untested"`` and ``passed: None`` plus an
    ``other_config`` reference to the run that did cover the tool. Tools
    never self-tested are absent. Every entry carries ``config_match``
    (True, or False for ``config_untested``) when *config_hashes* is given.

    Two queries for the evaluated runs' tool results and findings, whatever
    the number of runs looked at.
    """
    from backend.analysis.models import Finding
    from backend.analysis.models import ToolResult

    wanted = None if config_hashes is None else {str(k): str(v or "") for k, v in config_hashes.items()}
    chosen: dict[str, Any] = {}  # slug -> run whose evaluation of the slug is reported
    other: dict[str, Any] = {}  # slug -> newest run covering the slug at another configuration
    for run in _finished_canaries(image_tag)[:PER_TOOL_LOOKBACK]:
        plan_tools = _plan_tools(run)
        for slug in _run_tool_slugs(run):
            if slug in chosen or (wanted is not None and slug not in wanted):
                continue
            run_hash = str((plan_tools.get(slug) or {}).get("config_hash") or "")
            if wanted is None or (wanted[slug] and run_hash == wanted[slug]):
                chosen[slug] = run
            elif slug not in other:
                other[slug] = run
        if wanted is not None and len(chosen) == len(wanted):
            break
    if wanted is not None:
        other = {slug: run for slug, run in other.items() if slug not in chosen}
    needed: dict[Any, set[str]] = {}
    runs_by_pk: dict[Any, Any] = {}
    for source in (chosen, other):
        for slug, run in source.items():
            needed.setdefault(run.pk, set()).add(slug)
            runs_by_pk[run.pk] = run
    if not needed:
        return {}
    slugs = sorted({slug for group in needed.values() for slug in group})
    results: dict[Any, dict[str, Any]] = {}
    for result in ToolResult.objects.filter(run_id__in=list(needed), tool_slug__in=slugs):
        results.setdefault(result.run_id, {})[result.tool_slug] = result
    findings: dict[Any, list[dict[str, Any]]] = {}
    rows = Finding.objects.filter(run_id__in=list(needed), tool_slug__in=slugs).values("run_id", *_FINDING_FIELDS)
    for row in rows:
        findings.setdefault(row.pop("run_id"), []).append(row)
    evaluations = {
        pk: _evaluate(runs_by_pk[pk], results.get(pk, {}), findings.get(pk, []), only=group)
        for pk, group in needed.items()
    }

    def entry(run, slug: str) -> dict[str, Any]:
        evaluation = evaluations[run.pk]
        return {
            **evaluation["tools"][slug],
            "run_id": evaluation["run_id"],
            "image_tag": evaluation["image_tag"],
            "evaluated_at": evaluation["evaluated_at"],
        }

    out: dict[str, dict[str, Any]] = {}
    for slug, run in chosen.items():
        out[slug] = entry(run, slug)
        if wanted is not None:
            out[slug]["config_match"] = True
    for slug, run in other.items():
        tested = entry(run, slug)
        out[slug] = {
            "passed": None,
            "verdict": "config_untested",
            "config_match": False,
            # Expectation details belong to the other configuration: not this plan's control.
            "missing": [],
            "unexpected_in_clean": [],
            "run_id": None,
            "image_tag": tested["image_tag"],
            "evaluated_at": None,
            "other_config": {
                "run_id": tested["run_id"],
                "config_hash": tested["config_hash"],
                "verdict": tested["verdict"],
                "evaluated_at": tested["evaluated_at"],
            },
        }
    return out
