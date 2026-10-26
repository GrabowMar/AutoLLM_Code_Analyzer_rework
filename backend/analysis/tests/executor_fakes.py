"""In-memory fakes for the executor's module boundaries (sandbox, adapters, sample, normalize, dedup,
measurement, reviewer) so the run state machine is tested without Docker, LLMs or other stages."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from backend.analysis.engine import adapters
from backend.analysis.engine import catalog
from backend.analysis.engine import dedup
from backend.analysis.engine import executor
from backend.analysis.engine import measurement
from backend.analysis.engine import normalize
from backend.analysis.engine import plan as plan_mod
from backend.analysis.engine import reviewer
from backend.analysis.engine import sample as sample_mod
from backend.analysis.engine import sandbox
from backend.analysis.engine.reviewer import ReviewOutcome
from backend.analysis.engine.reviewer import SampleOutcome
from backend.analysis.engine.reviewer import agentic_sandbox
from backend.analysis.engine.types import ExecOutcome
from backend.analysis.engine.types import Invocation
from backend.analysis.engine.types import NormalizedFinding
from backend.analysis.engine.types import NotApplicable
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import Sample
from backend.analysis.engine.types import SampleFile
from backend.analysis.engine.types import SampleUnavailable
from backend.analysis.engine.types import ToolFailure
from backend.analysis.engine.types import ToolParse

IMAGE_TAG = "llmlab/analyzer:test00000000"
IMAGE_ID = "sha256:" + "ab" * 32
SCOPE = {
    "measured_origins": ["authored", "added", "modified"],
    "line_attribution": True,
    "exclude_globs": [],
    "exclude_kinds": ["lockfile", "build", "asset"],
    "max_file_bytes": 524288,
}
LANGUAGES = {".py": "python", ".js": "javascript", ".jsx": "jsx", ".md": "markdown"}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_sample(files: dict[str, str], *, source: str = "inline") -> Sample:
    sample_files = []
    for path, text in sorted(files.items()):
        data = text.encode()
        language = next((lang for ext, lang in LANGUAGES.items() if path.endswith(ext)), "text")
        lines = len([line for line in text.splitlines() if line.strip()])
        sample_files.append(
            SampleFile(
                path=path,
                content=data,
                sha256=_sha(data),
                size=len(data),
                language=language,
                kind="source" if language != "markdown" else "doc",
                origin="authored",
                sloc=lines,
                measured=language != "markdown",
                measured_sloc=lines,
            ),
        )
    digest = hashlib.sha256()
    for f in sorted(sample_files, key=lambda f: f.path):
        digest.update(f"{f.path}\0{f.sha256}\n".encode())
    return Sample(files=sample_files, source=source, sample_hash=digest.hexdigest(), meta={"source": source})


def current_spec_hash(slug: str) -> str:
    """The catalog's spec_hash for *slug* (a plan frozen now records it), or a placeholder for unknown tools."""
    try:
        return catalog.get_spec(slug).spec_hash
    except KeyError:
        return f"spec-{slug}"


def tool_entry(slug: str, *, required: bool = True, version: str = "1.0", source: str = "image_manifest") -> dict:
    return {
        "slug": slug,
        "name": slug.title(),
        "kind": "static",
        "category": "lint",
        "required": required,
        "config": {},
        "config_hash": f"cfg-{slug}",
        # The running catalog's hash: the executor refuses a plan whose spec drifted.
        "spec_hash": current_spec_hash(slug),
        "version": version,
        "version_source": source,
        "languages": ["python"],
    }


def reviewer_entry(*, required: bool = True, samples: int = 3) -> dict:
    return {
        "enabled": True,
        "required": required,
        "tool_slug": "ai_review",
        "engine": "single_shot",
        "harness": None,
        "samples": samples,
        "prompt_version": "review-v1",
        "model": {
            "slug": "deepseek/deepseek-v4.1-flash",
            "canonical_slug": "deepseek/deepseek-v4.1-flash-20260910",
            "pricing": {"input_per_token": 1e-7, "output_per_token": 2e-7, "snapshot_at": "2026-10-22T00:00:00Z"},
        },
    }


def make_plan(tools: list[dict] | None = None, *, reviewer_cfg: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "profile": None,
        "tools": tools if tools is not None else [tool_entry("ruff")],
        "reviewer": reviewer_cfg,
        "scope": copy.deepcopy(SCOPE),
        "outcomes": {"severity_weights": {}, "count_style": False, "min_sloc_for_density": 50},
        "analyzer_image": {"tag": IMAGE_TAG},
        # What a plan frozen by the running code records (the executor verifies both).
        "engine_versions": plan_mod.engine_versions(),
        "mapping_hashes": normalize.mapping_hashes(),
        "provenance": {},
        "notes": [],
    }


def raw(rule: str, path: str = "app.py", line: int = 1, severity: str = "high") -> RawFinding:
    return RawFinding(
        rule_id=rule,
        message=f"{rule} at {path}:{line}",
        file_path=path,
        line=line,
        native_severity=severity,
    )


class FakeAdapter:
    """Selects files by language; ``interpret`` returns ``findings`` unless the exit code is not 0/1."""

    def __init__(
        self,
        slug: str,
        *,
        languages: tuple[str, ...] = ("python",),
        findings: list[RawFinding] | None = None,
        interpret: Callable[..., ToolParse] | None = None,
        prepare_error: Exception | None = None,
    ) -> None:
        self.slug = slug
        self.languages = languages
        self.findings = findings or []
        self._interpret = interpret
        self.prepare_error = prepare_error

    def select_files(self, sample: Sample, config: dict[str, Any]) -> list[SampleFile]:
        if self.prepare_error is not None:
            raise self.prepare_error
        return [f for f in sample.included() if f.language in self.languages]

    def invocation(self, files: list[SampleFile], config: dict[str, Any]) -> Invocation:
        return Invocation(
            argv=[self.slug, *(f"/src/{f.path}" for f in files)],
            timeout_s=60,
            mem_limit="256m",
            inputs={"files.txt": "\n".join(f.path for f in files).encode()},
            report_files=("report.json",),
        )

    def interpret(self, outcome: ExecOutcome, files: list[SampleFile], config: dict[str, Any]) -> ToolParse:
        if self._interpret is not None:
            return self._interpret(outcome, files, config)
        if outcome.exit_code not in (0, 1):
            msg = f"{self.slug} exited {outcome.exit_code}"
            raise ToolFailure(reason="tool_error", message=msg)
        if "report.json" not in outcome.out_files:
            msg = "missing report"
            raise ToolFailure(reason="parse_error", message=msg)
        return ToolParse(
            findings=list(self.findings),
            coverage={"files_scanned": len(files), "scanned": [f.path for f in files], "unparseable": []},
            metrics={"exit_code": outcome.exit_code},
        )


def ok_outcome(**kwargs) -> ExecOutcome:
    values = {
        "exit_code": 0,
        "stdout": b"ok\n",
        "stderr": b"",
        "out_files": {"report.json": b"[]"},
        "wall_seconds": 0.2,
        "image_id": IMAGE_ID,
        "container_name": "llmlab-an-fake",
    }
    values.update(kwargs)
    return ExecOutcome(**values)


@dataclass
class FakeSandbox:
    """Replaces the sandbox module's daemon-facing functions."""

    outcomes: dict[str, list[Any]] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=lambda: {"tools": {}})
    image_error: Exception | None = None
    volume_error: Exception | None = None
    daemon_up: bool = True
    capacity_ok: bool = True
    calls: list[str] = field(default_factory=list)
    volumes: list[dict[str, bytes]] = field(default_factory=list)
    ensure_calls: list[dict[str, Any]] = field(default_factory=list)
    on_run: Callable[[str], None] | None = None

    def install(self, monkeypatch) -> FakeSandbox:
        monkeypatch.setattr(sandbox, "ensure_image", self.ensure_image)
        monkeypatch.setattr(sandbox, "image_manifest", self.image_manifest)
        monkeypatch.setattr(sandbox, "run_volume", self.run_volume)
        monkeypatch.setattr(sandbox, "run_tool", self.run_tool)
        monkeypatch.setattr(sandbox, "wait_for_capacity", self.wait_for_capacity)
        monkeypatch.setattr(sandbox, "daemon_ok", lambda: self.daemon_up)
        monkeypatch.setattr(sandbox, "reap", lambda **kwargs: {"containers": 0, "volumes": 0, "errors": 0})
        monkeypatch.setattr(
            agentic_sandbox,
            "reap_review_sandboxes",
            lambda **kwargs: {"containers": 0, "networks": 0},
        )
        return self

    def ensure_image(self, *, expect_tag: str = "", build: bool = True, heartbeat=None, log=None):
        self.ensure_calls.append({"expect_tag": expect_tag, "build": build})
        if self.image_error is not None:
            raise self.image_error
        return IMAGE_TAG, IMAGE_ID

    def image_manifest(self, image_ref: str) -> dict[str, Any]:
        return copy.deepcopy(self.manifest)

    @contextmanager
    def run_volume(self, run_id: str, files: dict[str, bytes], *, image: str = ""):
        if self.volume_error is not None:
            raise self.volume_error
        self.volumes.append(dict(files))
        yield f"llmlab-an-{run_id[:8]}"

    def wait_for_capacity(self, mem_bytes: int, *, is_cancelled, heartbeat, timeout_s: int) -> bool:
        heartbeat()
        return self.capacity_ok and not is_cancelled()

    def run_tool(self, *, image, volume, invocation, run_id, tool_slug, is_cancelled, heartbeat) -> ExecOutcome:
        self.calls.append(tool_slug)
        if self.on_run is not None:
            self.on_run(tool_slug)
        heartbeat()
        if is_cancelled():
            return ExecOutcome(exit_code=None, cancelled=True)
        queue = self.outcomes.get(tool_slug)
        spec = queue.pop(0) if queue else ok_outcome()
        if callable(spec):
            return spec(is_cancelled=is_cancelled, heartbeat=heartbeat, invocation=invocation)
        return copy.deepcopy(spec)


def fake_normalize(
    tool_slug: str,
    raw_finding: RawFinding,
    sample: Sample,
    scope: dict[str, Any],
    *,
    outcomes: dict[str, Any] | None = None,
) -> NormalizedFinding:
    return NormalizedFinding(
        tool_slug=tool_slug,
        rule_id=raw_finding.rule_id,
        title=raw_finding.message,
        severity=(raw_finding.native_severity or "medium").lower(),
        native_severity=raw_finding.native_severity,
        category="security",
        cwe=list(raw_finding.cwe),
        confidence="high",
        file_path=raw_finding.file_path,
        line=raw_finding.line,
        end_line=raw_finding.end_line,
        column=raw_finding.column,
        file_origin="authored",
        in_measured_scope=True,
        counted=True,
        fingerprint=_sha(f"{tool_slug}|{raw_finding.rule_id}|{raw_finding.file_path}|{raw_finding.line}".encode()),
    )


def fake_assign_issue_keys(findings: list[NormalizedFinding], *, line_window: int = 2) -> int:
    keys = set()
    for finding in findings:
        if finding.is_ai:
            finding.issue_key = "ai-" + finding.fingerprint[:12]
            continue
        finding.issue_key = _sha(f"{finding.file_path}|{finding.line}".encode())[:16]
        if finding.counted:
            keys.add(finding.issue_key)
    return len(keys)


def fake_compute(run) -> dict[str, Any]:
    results = list(run.results.all())
    return {
        "schema_version": 1,
        "plan_hash": run.plan_hash,
        "sample_hash": run.sample_hash,
        "tool_status": {r.tool_slug: r.status for r in results},
        "complete": all(r.status in ("succeeded", "not_applicable") for r in results if r.required),
    }


def fake_summarize(run) -> dict[str, Any]:
    summary = executor.basic_summary(run)
    summary["from_fake"] = True
    return summary


@dataclass
class Resolver:
    """sample.resolve_inline / resolve_job / target_meta over make_sample (jobs: result_data["files"])."""

    calls: int = 0
    meta_calls: int = 0

    def install(self, monkeypatch) -> Resolver:
        monkeypatch.setattr(sample_mod, "resolve_inline", self.resolve_inline)
        monkeypatch.setattr(sample_mod, "resolve_job", self.resolve_job)
        monkeypatch.setattr(sample_mod, "target_meta", self.target_meta)
        return self

    def target_meta(self, job, *, sample=None) -> dict[str, Any]:
        self.meta_calls += 1
        return {"job_id": str(job.pk), "mode": job.mode, "files": len(sample.files), "recomputed": True}

    def resolve_inline(self, files, scope=None, *, source: str = "inline") -> Sample:
        self.calls += 1
        if not files:
            msg = "no files"
            raise SampleUnavailable(msg)
        return make_sample(dict(files), source=source)

    def resolve_job(self, job, scope=None) -> Sample:
        self.calls += 1
        files = (job.result_data or {}).get("files") or {}
        if job.status != "completed" or not files:
            msg = "job has no code"
            raise SampleUnavailable(msg, code="no_code")
        sample = make_sample(files, source="scaffolding")
        # Like the real resolver: target_meta of this sample, under this scope.
        sample.meta = {"job_id": str(job.pk), "mode": job.mode, "files": len(sample.files), "scope": scope}
        return sample


def review_outcome(
    *,
    ok: int = 3,
    failed: int = 0,
    findings_per_sample: int = 1,
    cost: float = 0.01,
    status: str | None = None,
) -> ReviewOutcome:
    samples = []
    findings = []
    for index in range(ok + failed):
        succeeded = index < ok
        sample_findings = []
        if succeeded:
            for n in range(findings_per_sample):
                sample_findings.append(
                    NormalizedFinding(
                        tool_slug="ai_review",
                        rule_id="ai:security",
                        title=f"AI finding {n}",
                        severity="high",
                        native_severity="high",
                        category="security",
                        cwe=["CWE-89"],
                        confidence="medium",
                        file_path="app.py",
                        line=1 + n,
                        end_line=1 + n,
                        column=None,
                        is_ai=True,
                        grounding="exact",
                        is_consensus=True,
                        counted=True,
                        sample_index=index,
                        fingerprint=_sha(f"ai{index}{n}".encode()),
                    ),
                )
        findings.extend(sample_findings)
        samples.append(
            SampleOutcome(
                index=index,
                engine="single_shot",
                model_slug="deepseek/deepseek-v4.1-flash",
                model_canonical_slug="deepseek/deepseek-v4.1-flash-20260910",
                status="succeeded" if succeeded else "invalid_output",
                failure_reason="" if succeeded else "invalid_output",
                prompt_version="review-v1",
                prompt_sha256="p" * 64,
                schema_sha256="s" * 64,
                file_order_seed=index,
                files_reviewed=["app.py"],
                usage={"cost_usd": cost, "llm_calls": 1},
                findings=sample_findings,
                artifacts={"response.json": b'{"findings": []}'},
            ),
        )
    quorum = ok >= (ok + failed + 1) // 2
    return ReviewOutcome(
        status=status or ("succeeded" if quorum else "failed"),
        failure_reason="" if quorum else "invalid_output",
        message="" if quorum else "quorum not reached",
        samples=samples,
        findings=findings,
        agreement={"successful_samples": ok, "threshold": max(1, (ok + 1) // 2), "mean_pairwise_jaccard": 1.0},
        usage={"cost_usd": cost * (ok + failed)},
    )


@dataclass
class World:
    """Everything a state-machine test needs, installed on the module boundaries."""

    sandbox: FakeSandbox
    resolver: Resolver
    adapters: dict[str, FakeAdapter]
    review: dict[str, Any]
    published: list[tuple[str, dict]]
    enqueued: list[tuple[str, int]]


def install_world(monkeypatch, *, adapters_by_slug: dict[str, FakeAdapter] | None = None) -> World:
    fake_sandbox = FakeSandbox().install(monkeypatch)
    resolver = Resolver().install(monkeypatch)
    registry = dict(adapters_by_slug or {})

    def get_adapter(slug: str):
        if slug not in registry:
            raise KeyError(slug)
        return registry[slug]

    review: dict[str, Any] = {"outcome": review_outcome(), "calls": [], "error": None}

    def run_review(*, frozen, sample, api_key, run_id, is_cancelled, heartbeat, scope=None):
        review["calls"].append({"frozen": frozen, "api_key": api_key, "run_id": run_id, "scope": scope})
        heartbeat()
        if review["error"] is not None:
            raise review["error"]
        hook = review.get("during")
        if hook is not None:
            hook(is_cancelled)
        return copy.deepcopy(review["outcome"])

    published: list[tuple[str, dict]] = []
    enqueued: list[tuple[str, int]] = []
    monkeypatch.setattr(adapters, "get_adapter", get_adapter)
    monkeypatch.setattr(normalize, "normalize", fake_normalize)
    monkeypatch.setattr(dedup, "assign_issue_keys", fake_assign_issue_keys)
    monkeypatch.setattr(measurement, "compute", fake_compute)
    monkeypatch.setattr(measurement, "summarize", fake_summarize)
    monkeypatch.setattr(reviewer, "run_review", run_review)
    monkeypatch.setattr(executor, "_publish", lambda run_id, event: published.append((run_id, event)))
    monkeypatch.setattr(executor, "enqueue", lambda run_id, *, countdown=0: enqueued.append((str(run_id), countdown)))
    monkeypatch.setattr(executor, "CANCEL_POLL_SECONDS", 0.0)
    return World(fake_sandbox, resolver, registry, review, published, enqueued)


def not_applicable_adapter(slug: str) -> FakeAdapter:
    """An adapter for JavaScript only (nothing to scan in a Python sample)."""
    return FakeAdapter(slug, languages=("javascript", "jsx"))


def failing_prepare(slug: str, exc: Exception | None = None) -> FakeAdapter:
    return FakeAdapter(slug, prepare_error=exc or NotApplicable("nothing here"))
