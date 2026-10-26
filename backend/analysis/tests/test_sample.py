"""Sample resolution (sample-v1): every job mode, attribution, scope, target_meta, eligibility."""

from __future__ import annotations

import hashlib

import pytest

from backend.analysis.engine import sample as resolver
from backend.analysis.engine import sloc
from backend.analysis.engine.types import SampleUnavailable
from backend.analysis.tests.support import measure as fx
from backend.generation.tests.factories import GenerationJobFactory

# --------------------------------------------------------------------------
# Language / kind / scope
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "language"),
    [
        ("app.py", "python"),
        ("pkg/types.pyi", "python"),
        ("frontend/src/main.jsx", "jsx"),
        ("frontend/src/api.js", "javascript"),
        ("frontend/vite.config.mjs", "javascript"),
        ("src/a.ts", "typescript"),
        ("src/App.tsx", "tsx"),
        ("src/App.vue", "vue"),
        ("src/App.svelte", "svelte"),
        ("templates/index.html", "html"),
        ("templates/base.jinja2", "html"),
        ("src/App.css", "css"),
        ("src/theme.scss", "css"),
        ("frontend/package.json", "json"),
        ("docker-compose.yml", "yaml"),
        ("pyproject.toml", "toml"),
        ("Pipfile", "toml"),
        ("README.md", "markdown"),
        ("LICENSE", "text"),
        ("requirements.txt", "text"),
        ("run.sh", "shell"),
        ("Dockerfile", "dockerfile"),
        ("deploy/Dockerfile.prod", "dockerfile"),
        (".env", "env"),
        (".env.example", "env"),
        (".gitignore", "text"),
        ("Makefile", "other"),
        ("schema.sql", "other"),
        ("logo.png", "other"),
    ],
)
def test_detect_language(path, language):
    assert resolver.detect_language(path) == language


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("frontend/package-lock.json", "lockfile"),
        ("yarn.lock", "lockfile"),
        ("pnpm-lock.yaml", "lockfile"),
        ("poetry.lock", "lockfile"),
        ("Pipfile.lock", "lockfile"),
        ("uv.lock", "lockfile"),
        ("static/app.min.js", "build"),
        ("frontend/dist/index.js", "build"),
        ("frontend/src/App.js.map", "build"),
        ("__pycache__/app.cpython-312.pyc", "build"),
        ("public/logo.png", "asset"),
        ("public/icon.svg", "asset"),
        ("fonts/a.woff2", "asset"),
        (".env.example", "env"),
        ("frontend/package.json", "manifest"),
        ("requirements.txt", "manifest"),
        ("requirements-dev.txt", "manifest"),
        ("pyproject.toml", "manifest"),
        ("frontend/vite.config.js", "config"),
        ("frontend/tailwind.config.js", "config"),
        ("frontend/.eslintrc.cjs", "config"),
        ("tsconfig.json", "config"),
        ("docker-compose.yml", "config"),
        ("Dockerfile", "config"),
        ("setup.cfg", "manifest"),
        (".gitignore", "config"),
        ("app.py", "source"),
        ("frontend/src/config.js", "source"),
        ("frontend/src/App.jsx", "source"),
        ("frontend/index.html", "source"),
        ("frontend/src/App.css", "source"),
        ("scripts/run.sh", "source"),
        ("README.md", "doc"),
        ("LICENSE", "doc"),
        ("Makefile", "other"),
    ],
)
def test_detect_kind(path, kind):
    assert resolver.detect_kind(path, resolver.detect_language(path)) == kind


def test_detect_kind_minified_content():
    minified = ("var a=1;" * 300).encode()
    assert resolver.detect_kind("static/bundle.js", "javascript", content=minified) == "build"
    normal = b"const a = 1;\n" * 50
    assert resolver.detect_kind("src/bundle.js", "javascript", content=normal) == "source"


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("node_modules/x/index.js", "**/node_modules/**", True),
        ("frontend/node_modules/x/index.js", "**/node_modules/**", True),
        ("frontend/src/node_modules.js", "**/node_modules/**", False),
        ("a/b/c.min.js", "**/*.min.js", True),
        ("c.min.js", "**/*.min.js", True),
        ("static/assets/index-abc.js", "static/assets/**", True),
        ("frontend/static/assets/x.js", "static/assets/**", False),
        ("app.py", "*.py", True),
        ("src/app.py", "*.py", False),
    ],
)
def test_glob_match(path, pattern, expected):
    assert resolver.glob_match(path, pattern) is expected


def test_effective_scope_overlays_defaults():
    scope = resolver.effective_scope({"measured_origins": ["added"], "max_file_bytes": None})
    assert scope["measured_origins"] == ["added"]
    assert scope["max_file_bytes"] == resolver.DEFAULT_SCOPE["max_file_bytes"]
    assert scope["exclude_kinds"] == ["lockfile", "build", "asset"]
    # The defaults are never mutated through the returned copy.
    scope["exclude_kinds"].append("doc")
    assert "doc" not in resolver.DEFAULT_SCOPE["exclude_kinds"]


def test_clean_path():
    assert resolver.clean_path("./src/./a.py") == "src/a.py"
    assert resolver.clean_path("src\\b.py") == "src/b.py"
    assert resolver.clean_path("/etc/passwd") is None
    assert resolver.clean_path("../x.py") is None
    assert resolver.clean_path("a/../../x.py") is None
    assert resolver.clean_path("") is None
    assert resolver.clean_path(None) is None


def test_changed_lines_and_suppressions():
    base = "a\nb\nc\nd\n"
    final = "a\nB\nc\nnew\nd\ntail\n"
    assert resolver.changed_lines(base, final) == [2, 4, 6]
    assert resolver.changed_lines(base, base) == []
    text = (
        "import os  # noqa: F401\n"
        "x = 1  # nosec B101\n"
        "y: int = 'a'  # type: ignore[assignment]\n"
        "# pylint: disable=invalid-name\n"
        "// eslint-disable-next-line no-eval\n"
        "/* eslint-disable */\n"
        "k = 'AKIA...'  # nosemgrep  gitleaks:allow\n"
        "s = 'x'  # pragma: allowlist secret\n"
        "// @ts-ignore\n"
        "z = 2\n"
    )
    assert resolver.count_suppressions(text) == 10
    assert resolver.count_suppressions(text, {1, 7}) == 3


@pytest.mark.parametrize(
    ("line", "markers"),
    [
        ("# mypy: ignore-errors", 1),
        ("#mypy: disable-error-code=attr-defined", 1),
        ("def f():  # lizard forgives", 1),
        ("// #lizard forgive global", 1),
        ("# #lizard forgives(complexity)", 1),
        ("// jscpd:ignore-start", 1),
        ("// jscpd:ignore-end", 0),  # closes a region: not a suppression of its own
        ("teh = 1  # codespell:ignore teh", 1),
        ("# codespell:ignore-next-line", 1),
        ("x = 1  # noqa  # mypy: strict", 2),
        ("mypy = 'typing'  # a mypy: note in prose has no '#' before it", 0),
    ],
)
def test_scanner_configuration_markers_are_counted(line, markers):
    """Markers the adapters neutralize (mypy inline config, lizard, jscpd, codespell) still count
    as suppression attempts on the model's lines."""
    assert resolver.count_suppressions(line + "\n") == markers


# --------------------------------------------------------------------------
# Inline
# --------------------------------------------------------------------------


def test_resolve_inline_scope_and_hash():
    files = {
        "app.py": "import os\n\n# comment\nprint(os.getcwd())  # noqa: T201\n",
        "frontend/package-lock.json": '{"lockfileVersion": 3}\n',
        "frontend/src/App.jsx": "export default function App() {\n  return <p/>;\n}\n",
        "static/app.min.js": "var a=1;",
        "node_modules/react/index.js": "module.exports = 1;\n",
        "logo.png": "not really an image",
        "Dockerfile": "FROM python:3.12\n",
        "../evil.py": "print(1)\n",
    }
    sample = resolver.resolve_inline(files)
    by_path = sample.by_path()
    assert sample.source == "inline"
    assert "../evil.py" not in by_path
    assert any("unsafe path" in w for w in sample.warnings)

    app = by_path["app.py"]
    assert (app.origin, app.kind, app.language, app.included, app.measured) == (
        "authored",
        "source",
        "python",
        True,
        True,
    )
    assert app.sloc == 2
    assert app.measured_sloc == 2
    assert app.suppression_markers == 1
    assert by_path["frontend/package-lock.json"].exclude_reason == "lockfile"
    assert by_path["static/app.min.js"].exclude_reason == "minified"
    assert by_path["node_modules/react/index.js"].exclude_reason == "vendored"
    assert by_path["logo.png"].exclude_reason == "asset"
    docker = by_path["Dockerfile"]
    assert (docker.origin, docker.included, docker.measured) == ("platform", True, False)

    included = sorted((f.path, f.sha256) for f in sample.files if f.included)
    expected = hashlib.sha256("".join(f"{p}\0{h}\n" for p, h in included).encode()).hexdigest()
    assert sample.sample_hash == expected
    # Hash is order-independent and ignores excluded files.
    reordered = dict(reversed(list(files.items())))
    reordered["frontend/package-lock.json"] = '{"lockfileVersion": 4}\n'
    assert resolver.resolve_inline(reordered).sample_hash == sample.sample_hash

    manifest = sample.manifest()
    assert manifest["totals"]["measured"] == 2
    assert manifest["totals"]["measured_sloc"] == app.sloc + by_path["frontend/src/App.jsx"].sloc
    assert sample.meta["empty_output"] is False
    assert sample.meta["mode"] == "inline"


def test_resolve_inline_limits():
    big = "x = 1\n" * 200
    sample = resolver.resolve_inline({"big.py": big, "bin.py": "a\x00b"}, {"max_file_bytes": 100})
    by_path = sample.by_path()
    assert by_path["big.py"].exclude_reason == "too_large"
    assert by_path["bin.py"].exclude_reason == "binary"
    assert by_path["bin.py"].sloc == 0
    assert sample.meta["empty_output"] is True
    with pytest.raises(SampleUnavailable) as exc:
        resolver.resolve_inline({})
    assert exc.value.code == "no_code"


def test_measured_origins_from_scope():
    sample = fx.build_sample(
        [("a.py", "x = 1\n", "added"), ("b.py", "y = 2\n", "scaffold"), ("c.py", "z = 3\n", "unknown")],
        scope={"measured_origins": ["added"]},
    )
    flags = {f.path: f.measured for f in sample.files}
    assert flags == {"a.py": True, "b.py": False, "c.py": False}


def test_measured_kinds_from_scope():
    from backend.analysis.engine import normalize
    from backend.analysis.engine.types import RawFinding

    files = {
        "app.py": "import os\n",
        "vite.config.js": "export default {};\n",
        ".env": "SECRET_KEY=hunter2\n",
        "README.md": "# demo\n",
    }
    default = resolver.resolve_inline(files)
    assert {f.path: (f.kind, f.measured) for f in default.files} == {
        "app.py": ("source", True),
        "vite.config.js": ("config", False),
        ".env": ("env", False),
        "README.md": ("doc", False),
    }
    scope = {"measured_kinds": ["source", "config", "env"]}
    wider = resolver.resolve_inline(files, scope)
    assert {f.path for f in wider.files if f.measured} == {"app.py", "vite.config.js", ".env"}
    assert wider.manifest()["totals"]["measured_sloc"] == 2  # env lines are not code (sloc-v1)
    # A secret in a model-written .env counts only when the study measures env files.
    leak = RawFinding(rule_id="generic-api-key", message="secret", file_path=".env", line=1)
    assert normalize.normalize("gitleaks", leak, default, {}).counted is False
    assert normalize.normalize("gitleaks", leak, wider, scope).counted is True
    # empty_output stays "no model-written source code", whatever is measured.
    only_config = resolver.resolve_inline({"vite.config.js": "export default {};\n"}, scope)
    assert only_config.meta["empty_output"] is True


# --------------------------------------------------------------------------
# Agent jobs (built through the generator's own seed + collect)
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_agent_job_origins_and_line_attribution(user):
    job = fx.make_agent_job(user=user)
    sample = resolver.resolve_job(job)
    by_path = sample.by_path()
    assert sample.source == "agent"
    assert sample.warnings == []

    app = by_path["app.py"]
    assert app.origin == "modified"
    assert app.changed_lines is not None
    seed_lines = len(app.text.splitlines()) - len(fx.APP_PY_ADDITION.splitlines())
    assert min(app.changed_lines) > seed_lines - 1  # only the appended block is the model's
    # Attributed SLOC is exactly the code lines of the addition.
    addition_code = sloc.count(fx.APP_PY_ADDITION, "python")
    assert app.measured_sloc == addition_code
    assert app.measured_sloc < app.sloc
    assert app.suppression_markers == 1  # the agent's own S608 suppression
    assert app.line_origin(1) == "scaffold"
    assert app.line_origin(app.changed_lines[-1]) == "modified"

    lock = by_path["frontend/package-lock.json"]
    assert (lock.kind, lock.origin, lock.included, lock.exclude_reason) == ("lockfile", "added", False, "lockfile")
    item = by_path["frontend/src/components/TodoItem.jsx"]
    assert (item.origin, item.measured, item.changed_lines) == ("added", True, None)
    assert item.measured_sloc == item.sloc
    main = by_path["frontend/src/main.jsx"]
    assert (main.origin, main.measured) == ("scaffold", False)
    assert "Dockerfile" not in by_path  # protected files are not collected

    manifest = sample.manifest()
    assert manifest["totals"]["measured_sloc"] == app.measured_sloc + item.sloc + by_path["frontend/src/api.js"].sloc
    entry = next(e for e in manifest["files"] if e["path"] == "app.py")
    assert entry["changed_lines"] == len(app.changed_lines)
    # The stored manifest carries the attribution itself (measurement checks findings against it).
    assert entry["attribution"] == "line"
    assert resolver.expand_ranges(entry["changed_line_ranges"]) == set(app.changed_lines)
    assert entry["sloc_by_origin"] == {"modified": app.measured_sloc, "scaffold": app.sloc - app.measured_sloc}
    assert entry["baseline_sha256"]
    assert manifest["attribution"]["mode"] == "line_level"
    assert sample.meta["attribution"] == manifest["attribution"]

    # Stable across resolutions (the executor re-resolves and compares).
    assert resolver.resolve_job(job).sample_hash == sample.sample_hash


@pytest.mark.django_db
def test_agent_target_meta_and_eligibility(user):
    job = fx.make_agent_job(user=user)
    meta = resolver.resolve_job(job).meta
    assert meta["job_id"] == str(job.pk)
    assert meta["mode"] == "agent"
    assert meta["model_id"] == "deepseek/deepseek-v4.1-flash"
    assert meta["model_canonical_slug"] == "deepseek_deepseek-v4.1-flash-20260910"
    assert meta["generator_family"] == "deepseek"
    assert (meta["harness"], meta["harness_version"]) == ("aider", "0.86.2")
    assert meta["stack_slug"] == "flask-react"
    assert meta["app_slug"] == "crud_todo_list"
    assert meta["prompt_hash"] == "2e12352e60e0a784"
    assert meta["seed"] == 1957945272
    assert meta["termination"] == "completed"
    assert meta["truncated"] is False
    assert meta["truncation"] == {"truncated_calls": 0, "evidence": "result_data"}
    assert meta["empty_output"] is False
    assert meta["validity"]["valid"] is True
    assert meta["validity"]["source"] == "recomputed"
    assert meta["functional"]["pass_rate"] == 1.0
    assert meta["generation"] == {"cost": 0.0123, "total_tokens": 4567, "duration_seconds": 321.5}
    eligibility = resolver.check_eligibility(meta, None)
    assert eligibility == {"eligible": True, "reasons": []}


@pytest.mark.django_db
def test_agent_empty_truncated_sample_is_flagged(user, settings, tmp_path):
    """The shape of job 774ae13e: every file scaffold, one length-truncated call, no truncated_calls key."""
    settings.GENERATION_RUNS_ROOT = str(tmp_path)
    job = fx.make_agent_job(user=user, modify_app=False, include_model_files=False, lockfile=False)
    result = job.result_data
    del result["agent"]["truncated_calls"]
    digest = fx.write_call_log(tmp_path, job, ["length"])
    result["agent"]["run_record"] = {"llm_calls.jsonl.gz": {"sha256": digest, "bytes": 1}}
    job.save()

    sample = resolver.resolve_job(job)
    assert {f.origin for f in sample.files} == {"scaffold"}
    meta = sample.meta
    assert meta["empty_output"] is True
    assert meta["truncated"] is True
    assert meta["truncation_reasons"] == ["truncated_calls"]
    assert meta["truncation"] == {"truncated_calls": 1, "evidence": "run_record"}
    # Validity is recomputed with the backfilled count: the generator's own rule fires.
    assert "output_truncated" in {n["code"] for n in meta["validity"]["notes"]}
    eligibility = resolver.check_eligibility(meta, sample)
    assert eligibility["eligible"] is False
    excluded = {r["code"] for r in eligibility["reasons"] if r["severity"] == "exclude"}
    assert excluded == {"empty_output", "truncated"}
    # Rules switched off downgrade to warnings instead of dropping the flag.
    relaxed = resolver.check_eligibility(meta, sample, {"exclude_empty_output": False, "exclude_truncated": False})
    assert relaxed["eligible"] is True
    assert {"empty_output", "truncated"} <= {r["code"] for r in relaxed["reasons"] if r["severity"] == "warn"}


@pytest.mark.django_db
def test_run_record_with_wrong_sha_is_ignored(user, settings, tmp_path):
    settings.GENERATION_RUNS_ROOT = str(tmp_path)
    job = fx.make_agent_job(user=user)
    agent = job.result_data["agent"]
    del agent["truncated_calls"]
    fx.write_call_log(tmp_path, job, ["length", "stop"])
    agent["run_record"] = {"llm_calls.jsonl.gz": {"sha256": "0" * 64}}
    job.save()
    meta = resolver.target_meta(job)
    assert meta["truncation"] == {"truncated_calls": None, "evidence": "unavailable"}
    assert meta["truncated"] is False
    reasons = resolver.check_eligibility(meta, None)["reasons"]
    assert ("truncation_unknown", "warn") in {(r["code"], r["severity"]) for r in reasons}

    agent["harness_report"] = {"finish_reasons": {"tool-calls": 4, "stop": 1}}
    job.save()
    assert resolver.target_meta(job)["truncation"] == {"truncated_calls": 0, "evidence": "harness_report"}


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("overrides", "reasons", "stops"),
    [
        ({"termination": "timeout"}, [], ["termination:timeout"]),
        (
            {"termination": "budget_exhausted", "budget_exhausted": "max_llm_requests"},
            [],
            ["termination:budget_exhausted", "budget_exhausted:max_llm_requests"],
        ),
        (
            {"termination": "budget_exhausted", "budget_exhausted": "max_cost_usd"},
            [],
            ["termination:budget_exhausted", "budget_exhausted:max_cost_usd"],
        ),
        ({"termination": "resource_limit"}, [], ["termination:resource_limit"]),
        ({"truncated_calls": 2}, ["truncated_calls"], []),
        ({"termination": "harness_error"}, [], []),
    ],
)
def test_agent_truncation_reasons(user, overrides, reasons, stops):
    """Only output cut at the output cap is truncation; a run that ended at a run-level limit is a
    valid sample (generation keeps it to avoid favouring fast models): run_stopped, eligible."""
    job = fx.make_agent_job(user=user, agent_overrides=overrides)
    meta = resolver.target_meta(job)
    assert meta["truncation_reasons"] == reasons
    assert meta["truncated"] is bool(reasons)
    assert meta["run_stop_reasons"] == stops
    assert meta["run_stopped"] is bool(stops)
    eligibility = resolver.check_eligibility(meta, None)
    codes = {(r["code"], r["severity"]) for r in eligibility["reasons"]}
    assert eligibility["eligible"] is not bool(reasons)
    assert (("run_stopped", "info") in codes) is bool(stops)
    if stops:
        opted = resolver.check_eligibility(meta, None, {"exclude_run_limits": True})
        assert opted["eligible"] is False
        assert ("run_stopped", "exclude") in {(r["code"], r["severity"]) for r in opted["reasons"]}


def test_old_target_meta_rows_with_run_stops_as_truncation():
    """Rows stored before the split listed run stops in truncation_reasons (and set truncated)."""
    legacy = {
        "status": "completed",
        "truncated": True,
        "truncation_reasons": ["termination:timeout"],
        "functional": {"pass_rate": 1.0},
    }
    flags = resolver.truncation_flags(legacy)
    assert flags == {
        "truncated": False,
        "truncation_reasons": [],
        "run_stopped": True,
        "run_stop_reasons": ["termination:timeout"],
    }
    assert resolver.check_eligibility(legacy, None)["eligible"] is True
    both = {**legacy, "truncation_reasons": ["truncated_calls", "budget_exhausted:max_total_tokens"]}
    assert resolver.truncation_flags(both)["truncation_reasons"] == ["truncated_calls"]
    assert resolver.check_eligibility(both, None)["eligible"] is False
    # A truncated flag without any reason code stays truncated (unknown cause, never "clean").
    assert resolver.truncation_flags({"truncated": True})["truncated"] is True


@pytest.mark.django_db
def test_agent_invalid_sample_and_functional_none_safety(user):
    job = fx.make_agent_job(
        user=user,
        evaluation_status="infra_error",
        functional={"source": "conformance", "status": "infra_error", "pass_rate": None, "evaluator_version": 1},
    )
    meta = resolver.target_meta(job)
    assert meta["validity"]["exclude"] is True
    assert meta["validity"]["valid"] is False
    assert meta["functional"]["pass_rate"] is None
    eligibility = resolver.check_eligibility(meta, None)
    assert eligibility["eligible"] is False
    assert ("invalid", "exclude") in {(r["code"], r["severity"]) for r in eligibility["reasons"]}
    relaxed = resolver.check_eligibility(meta, None, {"exclude_invalid": False})
    assert relaxed["eligible"] is True


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("functional", "expected_status", "expected_rate"),
    [
        ({"status": "not_applicable", "pass_rate": 0.0, "source": "conformance"}, "not_applicable", None),
        ({"status": "infra_error", "pass_rate": None, "source": "conformance"}, "infra_error", None),
        ({"status": "failed", "pass_rate": 0.2667, "source": "conformance"}, "failed", 0.2667),
        ({"status": "build_failed", "pass_rate": 0.0, "source": "conformance"}, "build_failed", 0.0),
        ({"status": "passed", "pass_rate": "1.0", "source": "conformance"}, "passed", None),
        # No source: the runtime smoke probe's record, never a conformance result.
        ({"status": "passed", "pass_rate": 1.0}, "not_evaluated", None),
        ({}, "not_evaluated", None),
    ],
)
def test_functional_from_job(user, functional, expected_status, expected_rate):
    job = GenerationJobFactory(created_by=user, metrics={"functional": functional} if functional else {})
    result = resolver.functional_from_job(job)
    assert result["status"] == expected_status
    assert result["pass_rate"] == expected_rate


@pytest.mark.django_db
def test_functional_ignores_the_smoke_probe_that_overwrote_the_metrics(user):
    """Launching an app runs runtime.services.smoke, which replaces metrics['functional'] with a
    source-less GET probe; the conformance evaluation in result_data stays the only authority."""
    smoke = {"passed": True, "health_ok": True, "endpoints_checked": 4, "endpoints_ok": 4, "pass_rate": 1.0}
    evaluation = {
        "status": "failed",
        "summary": {"total": 8, "passed": 2, "failed": 6, "pass_rate": 0.25},
        "evaluator_version": 3,
        "contract_stated_in_prompt": True,
    }
    job = GenerationJobFactory(
        created_by=user,
        status="completed",
        result_data={"evaluation": evaluation},
        metrics={"functional": smoke},
    )
    assert resolver.functional_from_job(job) == {
        "status": "failed",
        "pass_rate": 0.25,
        "source": "conformance",
        "evaluator_version": 3,
        "contract_stated_in_prompt": True,
    }
    # A smoke-only job (launched, never evaluated) has no functional result at all.
    job = GenerationJobFactory(created_by=user, status="completed", result_data={}, metrics={"functional": smoke})
    assert resolver.functional_from_job(job)["status"] == "not_evaluated"
    assert resolver.functional_from_job(job)["pass_rate"] is None


@pytest.mark.django_db
def test_agent_baseline_mismatch_falls_back_to_whole_file(user, monkeypatch):
    job = fx.make_agent_job(user=user)
    real = resolver.scaffold_baseline(job)
    tampered = dict(real)
    tampered["frontend/src/main.jsx"] = b"// a different scaffold\n"
    monkeypatch.setattr(resolver, "scaffold_baseline", lambda _job: tampered)
    sample = resolver.resolve_job(job)
    app = sample.by_path()["app.py"]
    assert app.changed_lines is None
    assert app.measured_sloc == app.sloc
    assert any("does not reproduce" in w and "main.jsx" in w for w in sample.warnings)


@pytest.mark.django_db
def test_agent_baseline_unavailable(user, monkeypatch):
    job = fx.make_agent_job(user=user)
    attributed = resolver.resolve_job(job)
    monkeypatch.setattr(resolver, "scaffold_baseline", lambda _job: None)
    sample = resolver.resolve_job(job)
    assert sample.by_path()["app.py"].changed_lines is None
    assert any("baseline unavailable" in w for w in sample.warnings)
    # The fallback is recorded, not silent: the manifest, target_meta and eligibility say so,
    # and the attribution digest differs although the content (sample_hash) is the same.
    assert sample.sample_hash == attributed.sample_hash
    summary = sample.manifest()["attribution"]
    assert (summary["mode"], summary["whole_file"]) == ("whole_file", ["app.py"])
    assert summary["digest"] != attributed.manifest()["attribution"]["digest"]
    assert attributed.manifest()["attribution"]["mode"] == "line_level"
    assert attributed.manifest()["attribution"]["baseline_sha256"]
    assert sample.meta["attribution"]["mode"] == "whole_file"
    reasons = resolver.check_eligibility(sample.meta, None)["reasons"]
    assert ("whole_file_attribution", "warn") in {(r["code"], r["severity"]) for r in reasons}
    stored = resolver.check_eligibility({"status": "completed"}, None, manifest=sample.manifest())
    assert "whole_file_attribution" in {r["code"] for r in stored["reasons"]}
    # What the executor can check when it re-resolves a submitted run (same sample_hash).
    assert resolver.attribution_changed(attributed.manifest(), attributed) == ""
    change = resolver.attribution_changed(attributed.manifest(), sample)
    assert "line_level -> whole_file" in change
    assert "app.py" in change
    assert resolver.attribution_changed({}, sample) == ""  # a manifest without the record
    # Line attribution off: no baseline needed and no warning.
    calls = []
    monkeypatch.setattr(resolver, "scaffold_baseline", lambda _job: calls.append(1))
    sample = resolver.resolve_job(job, {"line_attribution": False})
    assert calls == []
    assert sample.by_path()["app.py"].changed_lines is None
    assert sample.warnings == []
    assert sample.manifest()["attribution"]["mode"] == "disabled"
    assert "whole_file_attribution" not in {
        r["code"] for r in resolver.check_eligibility(sample.meta, None)["reasons"]
    }


@pytest.fixture
def count_seeds(monkeypatch):
    """``count_seeds(seed=real)`` empties the baseline cache and starts counting the generator's
    seed_files calls behind scaffold_baseline (call it after building jobs, which seed too)."""
    from backend.generation.services.agent import workspace

    calls: list = []

    def install(seed=workspace.seed_files):
        def counting(job):
            calls.append(job.pk)
            return seed(job)

        resolver.clear_baseline_cache()
        monkeypatch.setattr(workspace, "seed_files", counting)
        return calls

    yield install
    resolver.clear_baseline_cache()


@pytest.mark.django_db
def test_scaffold_baseline_is_seeded_once_per_pinned_stack(user, count_seeds):
    from backend.generation.tests.factories import AppRequirementTemplateFactory

    a, b, c = (fx.make_agent_job(user=user) for _ in range(3))
    c.app_requirement = AppRequirementTemplateFactory()  # its slug is substituted into the seed
    c.save()
    calls = count_seeds()
    base_a = resolver.scaffold_baseline(a)
    assert resolver.scaffold_baseline(b) == base_a
    assert calls == [a.pk]
    # Callers own their copy.
    base_a["app.py"] = b"mutated"
    assert resolver.scaffold_baseline(b)["app.py"] != b"mutated"
    # resolve_job (preflight, launch, the executor) reuses the cached seed.
    assert resolver.resolve_job(b).by_path()["app.py"].changed_lines is not None
    assert calls == [a.pk]
    resolver.scaffold_baseline(c)
    assert calls == [a.pk, c.pk]


@pytest.mark.django_db
def test_scaffold_baseline_cache_skips_unpinned_jobs_and_failures_and_is_bounded(user, count_seeds, monkeypatch):
    from backend.generation.services.agent import workspace
    from backend.generation.tests.factories import AppRequirementTemplateFactory

    def agent_job(**kwargs):
        return GenerationJobFactory(created_by=user, mode="agent", stack_slug="flask-react", **kwargs)

    pinned = agent_job(resolved_bundle=fx.agent_bundle(stack_version=fx.builtin_stack_version()))
    unpinned = agent_job(resolved_bundle={})
    real = workspace.seed_files
    failures = {"left": 1}

    def flaky(job):
        if failures["left"]:
            failures["left"] -= 1
            msg = "transient"
            raise OSError(msg)
        return real(job)

    calls = count_seeds(flaky)
    assert resolver.scaffold_baseline(pinned) is None
    assert resolver.scaffold_baseline(pinned)  # a failure is never cached
    assert resolver.scaffold_baseline(pinned)
    assert calls == [pinned.pk, pinned.pk]
    # Without an exact pin the seed is the *latest* stack version, which moves: never cached.
    resolver.scaffold_baseline(unpinned)
    resolver.scaffold_baseline(unpinned)
    assert calls[2:] == [unpinned.pk, unpinned.pk]

    monkeypatch.setattr(resolver, "BASELINE_CACHE_SIZE", 1)
    other = agent_job(
        resolved_bundle=fx.agent_bundle(stack_version=fx.builtin_stack_version()),
        app_requirement=AppRequirementTemplateFactory(),
    )
    resolver.scaffold_baseline(other)
    assert len(resolver._baseline_cache) == 1
    resolver.scaffold_baseline(pinned)  # evicted (least recently used)
    assert calls[-1] == pinned.pk


@pytest.mark.django_db
def test_agent_manifest_problems_are_warnings(user):
    job = fx.make_agent_job(user=user)
    result = job.result_data
    result["files"]["frontend/src/api.js"] += "// edited after collection\n"
    result["files"]["notes/unlisted.py"] = "x = 1\n"
    result["agent"]["workspace"]["skipped"] = [{"path": "big.bin", "reason": "too_large"}]
    job.save()
    sample = resolver.resolve_job(job)
    warnings = "\n".join(sample.warnings)
    assert "frontend/src/api.js: content sha256 differs" in warnings
    assert "notes/unlisted.py: not in the workspace manifest" in warnings
    assert "big.bin: not collected by the generator (too_large)" in warnings
    unlisted = sample.by_path()["notes/unlisted.py"]
    assert (unlisted.origin, unlisted.measured) == ("unknown", False)


@pytest.mark.django_db
def test_agent_without_manifest_classifies_against_baseline(user):
    job = fx.make_agent_job(user=user)
    del job.result_data["agent"]["workspace"]["manifest"]
    job.save()
    sample = resolver.resolve_job(job)
    origins = {f.path: f.origin for f in sample.files}
    assert origins["app.py"] == "modified"
    assert origins["frontend/src/main.jsx"] == "scaffold"
    assert origins["frontend/src/api.js"] == "added"
    assert sample.by_path()["app.py"].changed_lines is not None
    assert any("no workspace manifest" in w for w in sample.warnings)


@pytest.mark.django_db
def test_lockfile_only_agent_output_is_empty(user):
    job = fx.make_agent_job(user=user, modify_app=False, include_model_files=False, lockfile=True)
    assert resolver.target_meta(job)["empty_output"] is True
    assert resolver.resolve_job(job).meta["empty_output"] is True


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["failed", "cancelled", "running", "pending"])
def test_unfinished_jobs_are_unavailable(user, status):
    job = fx.make_agent_job(user=user, status=status)
    with pytest.raises(SampleUnavailable) as exc:
        resolver.resolve_job(job)
    assert exc.value.code == "not_completed"
    # target_meta still works for preflight listings.
    meta = resolver.target_meta(job)
    assert meta["status"] == status
    assert resolver.check_eligibility(meta, None)["eligible"] is False


@pytest.mark.django_db
def test_failed_shapes_never_become_files(user):
    job = GenerationJobFactory(
        created_by=user,
        mode="scaffolding",
        status="completed",
        result_data={"error_detail": "boom", "error_remediation": "retry"},
    )
    with pytest.raises(SampleUnavailable) as exc:
        resolver.resolve_job(job)
    assert exc.value.code == "unknown_shape"
    job.result_data = {}
    job.save()
    with pytest.raises(SampleUnavailable) as exc:
        resolver.resolve_job(job)
    assert exc.value.code == "no_code"
    agent = GenerationJobFactory(created_by=user, mode="agent", status="completed", result_data={"agent": {}})
    with pytest.raises(SampleUnavailable):
        resolver.resolve_job(agent)


# --------------------------------------------------------------------------
# Scaffolding / custom / legacy jobs
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_scaffolding_job(user):
    job = GenerationJobFactory(
        created_by=user,
        mode="scaffolding",
        status="completed",
        stack_slug="flask-react",
        result_data={
            "result_schema_version": 2,
            "files": {"app.py": "from flask import Flask\napp = Flask(__name__)\n", "frontend/src/App.jsx": "x;\n"},
            "backend_entry": "app.py",
            "backend_code": "from flask import Flask\napp = Flask(__name__)\n",
            "frontend_code": "x;\n",
            "backend_truncated": True,
            "frontend_truncated": False,
            "backend_dependencies": ["flask"],
        },
    )
    sample = resolver.resolve_job(job)
    assert sample.source == "scaffolding"
    assert sorted(f.path for f in sample.files) == ["app.py", "frontend/src/App.jsx"]
    assert {f.origin for f in sample.files} == {"authored"}
    assert sample.meta["truncated"] is True
    assert sample.meta["truncation_reasons"] == ["backend_truncated"]
    assert sample.meta["validity"]["source"] == "not_applicable"


CUSTOM_CONTENT = """Here is the app.

```python:app.py
from flask import Flask
app = Flask(__name__)
```

```jsx title="frontend/src/App.jsx"
export default function App() { return <p/>; }
```

```requirements.txt
flask==3.0.0
```

```python
print("no file name")
```

```js:../../etc/passwd
bad
```

```python:app.py
from flask import Flask
app = Flask(__name__)  # second version wins
```
"""


@pytest.mark.django_db
def test_custom_job_code_blocks(user):
    job = GenerationJobFactory(
        created_by=user,
        mode="custom",
        status="completed",
        result_data={"content": CUSTOM_CONTENT, "truncated": False, "finish_reason": "length"},
    )
    sample = resolver.resolve_job(job)
    assert sample.source == "custom"
    paths = sorted(f.path for f in sample.files)
    assert paths == ["app.py", "frontend/src/App.jsx", "requirements.txt"]
    assert "second version wins" in sample.by_path()["app.py"].text
    warnings = "\n".join(sample.warnings)
    assert "has no file name: skipped" in warnings
    assert "unsafe file name" in warnings
    assert "duplicate file app.py" in warnings
    assert sample.meta["truncation_reasons"] == ["finish_reason:length"]
    # Never the v1 behaviour of turning result keys into files.
    assert "content.py" not in paths
    assert "finish_reason.py" not in paths


@pytest.mark.django_db
def test_custom_job_without_named_blocks(user):
    job = GenerationJobFactory(
        created_by=user,
        mode="custom",
        status="completed",
        result_data={"content": "```python\nprint(1)\n```\n", "truncated": True},
    )
    with pytest.raises(SampleUnavailable) as exc:
        resolver.resolve_job(job)
    assert exc.value.code == "no_code"
    meta = resolver.target_meta(job)
    assert meta["empty_output"] is True
    assert meta["truncation_reasons"] == ["output_truncated"]


@pytest.mark.django_db
def test_legacy_job(user):
    job = GenerationJobFactory(
        created_by=user,
        mode="scaffolding",
        status="completed",
        stack_slug="flask-react",
        result_data={"backend_code": "print('hi')\n", "frontend_code": "export default 1;\n"},
    )
    sample = resolver.resolve_job(job)
    assert sample.source == "legacy"
    assert sorted(f.path for f in sample.files) == ["app.py", "frontend/src/App.jsx"]
    meta = sample.meta
    assert meta["truncation"]["evidence"] == "unavailable"
    reasons = resolver.check_eligibility(meta, sample)["reasons"]
    assert "truncation_unknown" in {r["code"] for r in reasons}


def test_check_eligibility_with_manifest_and_min_sloc():
    meta = {"status": "completed", "functional": {"pass_rate": None, "status": "not_evaluated"}}
    manifest = {"totals": {"measured_sloc": 40}}
    result = resolver.check_eligibility(meta, None, {"min_measured_sloc": 50}, manifest=manifest)
    assert result["eligible"] is False
    assert ("below_min_sloc", "exclude") in {(r["code"], r["severity"]) for r in result["reasons"]}
    assert ("functional_unknown", "info") in {(r["code"], r["severity"]) for r in result["reasons"]}
    required = resolver.check_eligibility(meta, None, {"require_functional": True}, manifest=manifest)
    assert ("functional_unknown", "exclude") in {(r["code"], r["severity"]) for r in required["reasons"]}


@pytest.mark.django_db
def test_target_meta_reads_model_and_condition_rows(user):
    from backend.generation.tests.factories import ExperimentConditionFactory
    from backend.llm_models.tests.factories import LLMModelFactory

    job = fx.make_agent_job(user=user)
    job.model = LLMModelFactory(model_id="openai/gpt-x", provider="openai", model_name="GPT X")
    condition = ExperimentConditionFactory(label="gpt-x / aider")
    job.experiment = condition.experiment
    job.condition = condition
    job.save()
    meta = resolver.target_meta(job)
    assert (meta["model_id"], meta["model_name"], meta["model_provider"]) == ("openai/gpt-x", "GPT X", "openai")
    assert meta["generator_family"] == "openai"
    # The snapshot's slug stays the generation-time identity.
    assert meta["model_slug"] == "deepseek/deepseek-v4.1-flash"
    assert meta["condition_label"] == "gpt-x / aider"
    assert meta["experiment_id"] == str(condition.experiment_id)
    assert meta["condition_id"] == str(condition.pk)


@pytest.mark.django_db
def test_copilot_job_classified_against_placeholder_seed(user):
    job = fx.make_agent_job(user=user)
    files = job.result_data["files"]
    job.mode = "copilot"
    job.result_data = {"files": files}
    job.save()
    sample = resolver.resolve_job(job)
    assert sample.source == "legacy"
    origins = {f.path: f.origin for f in sample.files}
    assert origins["app.py"] == "modified"
    assert origins["frontend/src/api.js"] == "added"
    assert origins["frontend/src/main.jsx"] == "scaffold"
    assert sample.by_path()["app.py"].changed_lines is not None


@pytest.mark.django_db
def test_legacy_frontend_with_markdown_fences_is_flagged(user):
    job = GenerationJobFactory(
        created_by=user,
        mode="scaffolding",
        status="completed",
        stack_slug="flask-react",
        result_data={"frontend_code": "```jsx\nexport default 1;\n```\n", "frontend_truncated": True},
    )
    sample = resolver.resolve_job(job)
    assert [f.path for f in sample.files] == ["frontend/src/App.jsx"]
    assert any("markdown fences" in w for w in sample.warnings)
    assert sample.meta["truncation_reasons"] == ["frontend_truncated"]


def test_resolve_inline_rejects_non_maps():
    with pytest.raises(resolver.SampleUnavailable) as exc:
        resolver.resolve_inline(["app.py"])
    assert exc.value.code == "unknown_shape"
    sample = resolver.resolve_inline({"a.py": "x = 1\n", "b.py": 3})
    assert [f.path for f in sample.files] == ["a.py"]
    assert any("not text" in w for w in sample.warnings)


# --------------------------------------------------------------------------
# Per-line measured scope and line numbering (regressions)
# --------------------------------------------------------------------------


def test_measured_scope_is_per_line_for_any_origin_set():
    """With 'scaffold' measured, the unchanged lines of a modified file enter the denominator,
    exactly as normalize puts their findings in the numerator."""
    from backend.analysis.engine import normalize

    base = "import os\n\napp = None\n"
    final = "import os\nimport sys\n\napp = None\nx = 1  # noqa: E701\n"
    every = {"measured_origins": ["authored", "added", "modified", "scaffold"]}
    default = fx.build_sample([("app.py", final, "modified")], baseline={"app.py": base.encode()})
    wide = fx.build_sample([("app.py", final, "modified")], baseline={"app.py": base.encode()}, scope=every)
    only_scaffold = fx.build_sample(
        [("app.py", final, "modified")],
        baseline={"app.py": base.encode()},
        scope={"measured_origins": ["scaffold"]},
    )
    for smp, measured_sloc, markers in ((default, 2, 1), (wide, 4, 1), (only_scaffold, 2, 0)):
        app = smp.by_path()["app.py"]
        assert app.changed_lines == [2, 5]
        assert app.measured is True
        assert (app.measured_sloc, app.suppression_markers) == (measured_sloc, markers)
        assert app.sloc_by_origin == {"modified": 2, "scaffold": 2}
    finding = fx.raw("F401", "app.py", 1)
    scaffold_line = normalize.normalize("ruff", finding, wide, every)
    assert (scaffold_line.file_origin, scaffold_line.in_measured_scope, scaffold_line.counted) == (
        "scaffold",
        True,
        True,
    )
    assert normalize.normalize("ruff", finding, default, {}).counted is False
    entry = wide.manifest()["files"][0]
    assert entry["changed_line_ranges"] == [[2, 2], [5, 5]]


def test_line_numbering_matches_the_tools():
    """Form feeds, U+2028 and the like do not start a line for ruff/bandit/the reviewer:
    a model-written line after one is attributed (and counted) on its real number."""
    from backend.analysis.engine import normalize

    base = "import os\nSEP = '\u2028'\nx = 1\n"
    final = base + "y = eval(input())\n"
    assert resolver.changed_lines(base, final) == [4]
    smp = fx.build_sample([("app.py", final, "modified")], baseline={"app.py": base.encode()})
    app = smp.by_path()["app.py"]
    assert app.measured_sloc == 1
    f = normalize.normalize("bandit", fx.raw("B307", "app.py", 4, native_severity="MEDIUM"), smp, {})
    assert (f.file_origin, f.counted, f.snippet) == ("modified", True, "y = eval(input())")
    assert resolver.count_suppressions("a = 1\x0c\nb = 2  # noqa\n", {2}) == 1
