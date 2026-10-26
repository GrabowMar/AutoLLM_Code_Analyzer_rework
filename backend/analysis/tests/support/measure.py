"""Builders for the measure-stage tests (sample, normalize, dedup, measurement).

Job fixtures are sanitized, shrunken copies of the real result_data shapes of
the dev database's agent jobs (RESULT_SCHEMA_VERSION 2: ``files`` + the
``agent.workspace.manifest`` written by ``generation.services.agent.workspace.collect``).
Agent jobs are built through the generator's own ``seed_files``/``collect``
on the test database's seeded flask-react Stack row, so origins and the
scaffold baseline are exactly what a real run would record.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from typing import TYPE_CHECKING
from typing import Any

from backend.analysis.engine import sample as sample_mod
from backend.analysis.engine.types import RawFinding
from backend.analysis.engine.types import Sample
from backend.analysis.models import AnalysisRun
from backend.analysis.models import Finding
from backend.analysis.models import ReviewSample
from backend.analysis.models import ToolResult
from backend.analysis.tests.factories import AnalysisRunFactory
from backend.analysis.tests.factories import FindingFactory
from backend.analysis.tests.factories import ReviewSampleFactory
from backend.analysis.tests.factories import ToolResultFactory
from backend.generation.tests.factories import GenerationJobFactory

if TYPE_CHECKING:
    from backend.generation.models import GenerationJob

PROTECTED = ("Dockerfile", ".dockerignore", "docker-compose.yml")

# Model-written additions (trimmed from real agent output of job 6bdd2996).
APP_PY_ADDITION = '''

VALID_PRIORITIES = {"low", "medium", "high"}


def _priority(value):
    """Normalise a priority string."""
    value = (value or "medium").lower()
    # Unknown priorities fall back to medium.
    return value if value in VALID_PRIORITIES else "medium"


@app.route("/api/todos/<int:todo_id>", methods=["DELETE"])
def delete_todo(todo_id):
    db.execute(f"DELETE FROM todos WHERE id = {todo_id}")  # noqa: S608
    return jsonify({"ok": True})
'''

TODO_ITEM_JSX = """import { useState } from 'react';
import PriorityBadge from './PriorityBadge';

export default function TodoItem({ todo, onToggle }) {
  // Local edit state for the inline editor.
  const [editing, setEditing] = useState(false);
  return (
    <li className="todo">
      {/* checkbox */}
      <input type="checkbox" checked={todo.done} onChange={() => onToggle(todo.id)} />
      <span>{todo.title}</span>
      <PriorityBadge priority={todo.priority} />
      {editing && <button onClick={() => setEditing(false)}>Save</button>}
    </li>
  );
}
"""

API_JS = """const BASE = '/api';

export async function listTodos(token) {
  const res = await fetch(`${BASE}/todos`, { headers: { Authorization: `Bearer ${token}` } });
  return res.json();
}
"""

MODEL_FILES = {"frontend/src/components/TodoItem.jsx": TODO_ITEM_JSX, "frontend/src/api.js": API_JS}

LOCKFILE = json.dumps(
    {"name": "frontend", "lockfileVersion": 3, "packages": {"": {"name": "frontend"}}},
    indent=2,
)


def agent_bundle(*, stack_version: int | None, harness: str = "aider", version: str = "0.86.2") -> dict[str, Any]:
    """resolved_bundle subset an agent job snapshot carries (bundle_schema_version 2)."""
    return {
        "mode": "agent",
        "seed": 1957945272,
        "llm": {
            "model_id": 71,
            "model_slug": "deepseek/deepseek-v4.1-flash",
            "model_canonical_slug": "deepseek_deepseek-v4.1-flash-20260910",
        },
        "harness": {"name": harness, "version": version},
        "brief": {"protected_files": list(PROTECTED), "version": 2},
        "stack": {"slug": "flask-react", "version": stack_version, "content_hash": "x"},
        "scaffolding_slug": "flask-react",
        "gateway": {"dropped_params": {}},
        "contract": {"version": 1},
        "prompt_hash": "2e12352e60e0a784",
        "method_key": f"agent:{harness}@{version}",
        "app_requirement": {"slug": "crud_todo_list", "name": "CRUD Todo List"},
        "bundle_schema_version": 2,
    }


def builtin_stack_version(slug: str = "flask-react") -> int | None:
    from backend.runtime.models import Stack

    row = Stack.objects.filter(slug=slug, is_archived=False).order_by("-version").first()
    return row.version if row else None


def make_agent_job(
    *,
    user=None,
    modify_app: bool = True,
    include_model_files: bool = True,
    add_files: dict[str, str] | None = None,
    lockfile: bool = True,
    agent_overrides: dict[str, Any] | None = None,
    functional: dict[str, Any] | None = None,
    evaluation_status: str | None = "passed",
    status: str = "completed",
) -> GenerationJob:
    """A completed agent job whose result_data is produced by the generator's own collect()."""
    from backend.generation.services.agent.workspace import collect
    from backend.generation.services.agent.workspace import seed_files

    kwargs: dict[str, Any] = {"mode": "agent", "status": status, "stack_slug": "flask-react"}
    if user is not None:
        kwargs["created_by"] = user
    job = GenerationJobFactory(
        **kwargs,
        resolved_bundle=agent_bundle(stack_version=builtin_stack_version()),
        prompt_hash="2e12352e60e0a784",
        bundle_key="agent:aider@0.86.2",
        experiment_seed=1957945272,
        repeat_index=0,
    )
    seed = seed_files(job)
    final = dict(seed)
    if modify_app:
        final["app.py"] = seed["app.py"] + APP_PY_ADDITION.encode()
    files = dict(MODEL_FILES) if include_model_files else {}
    files.update(add_files or {})
    for path, text in files.items():
        final[path] = text.encode()
    if lockfile:
        final["frontend/package-lock.json"] = LOCKFILE.encode()
    ws = collect(final, seed, protected=PROTECTED, has_frontend=True)
    agent: dict[str, Any] = {
        "harness": "aider",
        "harness_version": "0.86.2",
        "termination": "completed",
        "exit_code": 0,
        "budget_exhausted": None,
        "llm_calls": 3,
        "llm_calls_failed": 0,
        "truncated_calls": 0,
        "served_models": {"deepseek/deepseek-v4.1-flash": 3},
        "egress": {"allowed_connects": 3, "denied_connects": [], "denied_paths": []},
        "harness_report": {"steps": 0, "finish_reasons": {}},
        "workspace": {
            "origins": ws.counts(),
            "manifest": ws.manifest,
            "skipped": ws.skipped,
            "deleted_scaffold_files": ws.deleted_scaffold_files,
            "protected_modified": ws.protected_modified,
        },
        "run_record": {},
        "validity": {"valid": True, "notes": []},
    }
    agent.update(agent_overrides or {})
    result: dict[str, Any] = {
        "result_schema_version": 2,
        "materialize": "verbatim",
        "files": ws.files,
        "backend_entry": "app.py",
        "backend_code": ws.files.get("app.py", ""),
        "frontend_code": ws.files.get("frontend/src/App.jsx", ""),
        "backend_truncated": False,
        "frontend_truncated": False,
        "agent": agent,
    }
    if evaluation_status is not None:
        result["evaluation"] = {"status": evaluation_status, "summary": {"pass_rate": 1.0}, "evaluator_version": 1}
    job.result_data = result
    job.metrics = {
        "cost": 0.0123,
        "total_tokens": 4567,
        "duration_seconds": 321.5,
        "functional": functional
        if functional is not None
        else {"source": "conformance", "status": "passed", "pass_rate": 1.0, "evaluator_version": 1},
    }
    job.save()
    return job


def write_call_log(root, job: GenerationJob, finish_reasons: list[str]) -> str:
    """Write a run-record ``llm_calls.jsonl.gz`` under *root*; returns its sha256."""
    rows = [
        {"seq": i, "status": 200, "response": {"finish_reason": reason, "error": None}}
        for i, reason in enumerate(finish_reasons)
    ]
    raw = gzip.compress("\n".join(json.dumps(r) for r in rows).encode())
    target = root / str(job.pk)
    target.mkdir(parents=True, exist_ok=True)
    (target / "llm_calls.jsonl.gz").write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def build_sample(
    files: list[tuple[str, str, str]],
    *,
    baseline: dict[str, bytes] | None = None,
    scope: dict[str, Any] | None = None,
    source: str = "agent",
) -> Sample:
    """A Sample from ``(path, text, origin)`` triples through the real resolver pipeline."""
    specs = [sample_mod._Spec(path, text.encode(), origin) for path, text, origin in files]
    return sample_mod._assemble(specs, source=source, scope=scope, warnings=[], baseline=baseline)


def raw(rule: str, path: str, line: int | None = 1, **kwargs: Any) -> RawFinding:
    return RawFinding(
        rule_id=rule,
        message=kwargs.pop("message", f"{rule} finding"),
        file_path=path,
        line=line,
        **kwargs,
    )


# --------------------------------------------------------------------------
# Persisted runs (measurement)
# --------------------------------------------------------------------------


def manifest_for(sample: Sample) -> dict[str, Any]:
    return sample.manifest()


def make_run(
    *,
    sample: Sample | None = None,
    plan: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    **kwargs: Any,
) -> AnalysisRun:
    return AnalysisRunFactory(
        plan=plan or {"schema_version": 1, "tools": [], "outcomes": {"min_sloc_for_density": 0}},
        sample_manifest=sample.manifest() if sample is not None else {},
        sample_hash=sample.sample_hash if sample is not None else "",
        target_meta=meta if meta is not None else {},
        **kwargs,
    )


def add_result(
    run: AnalysisRun,
    slug: str,
    *,
    status: str = ToolResult.Status.SUCCEEDED,
    required: bool = True,
    kind: str = "static",
    category: str = "lint",
    **kwargs: Any,
) -> ToolResult:
    return ToolResultFactory(
        run=run,
        tool_slug=slug,
        tool_kind=kind,
        category=category,
        status=status,
        required=required,
        **kwargs,
    )


def add_finding(result: ToolResult, **kwargs: Any) -> Finding:
    defaults: dict[str, Any] = {
        "severity": "medium",
        "category": "bug",
        "file_path": "app.py",
        "line_number": 1,
        "counted": True,
        "in_measured_scope": True,
        "file_origin": "added",
        "issue_key": "",
        "rule_id": "R1",
    }
    defaults.update(kwargs)
    return FindingFactory(result=result, **defaults)


def add_review_sample(result: ToolResult, index: int, **kwargs: Any) -> ReviewSample:
    return ReviewSampleFactory(result=result, index=index, **kwargs)
