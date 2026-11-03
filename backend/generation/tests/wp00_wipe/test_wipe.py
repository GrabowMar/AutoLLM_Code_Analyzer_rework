"""WP-00: ``manage.py wipe_generation_v2`` against a real v2 schema (SAMPLE_GEN_V3 §3.12).

The data tests migrate the generation app to its v2 leaf with ``MigrationExecutor``, insert rows
through the historical models of that state, run the command, and migrate forward to the leaf
again. Nothing here imports a current model, so the file runs unchanged on the v3 branch, where
the leaf is v3 and "forward" crosses the v3 migrations.
"""

from __future__ import annotations

import re
from datetime import timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from typing import Any

import docker.errors
import pytest
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from backend.generation.management.commands import wipe_generation_v2 as wipe

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterator

    from django.apps.registry import Apps

V2_LEAF = ("generation", "0022_artifact_telemetry")

# Tables the v2 post_migrate seeder fills with system rows; their expected counts are read back.
SEEDED_TABLES = ("generation_runpreset", "generation_generationprofile", "generation_contentblock")
FULLY_WIPED = (
    "generation_copilotiteration",
    "generation_generationartifact",
    "generation_generationjob",
    "generation_generationbatch",
    "generation_experimentschedule",
    "generation_experimentcondition",
    "generation_experiment_app_requirements",
    "generation_experimentpostrun",
    "generation_experiment",
    *SEEDED_TABLES,
)

_ROW = re.compile(r"^  ([a-z0-9_.,]+) +(delete|set NULL) +(\d+)", re.MULTILINE)


# -- helpers ------------------------------------------------------------------------------------


def run_wipe(*args: str) -> str:
    out = StringIO()
    call_command("wipe_generation_v2", *args, stdout=out, stderr=StringIO())
    return out.getvalue()


def row_counts(output: str) -> dict[str, int]:
    return {label: int(rows) for label, _action, rows in _ROW.findall(output)}


def count(table: str, where: str = "TRUE") -> int:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}")  # noqa: S608 — test constants
        return cursor.fetchone()[0]


def ids(table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT id::text FROM {table}")  # noqa: S608 — test constants
        return {row[0] for row in cursor.fetchall()}


def column(table: str, name: str, row_id: Any) -> Any:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {name} FROM {table} WHERE id = %s", [row_id])  # noqa: S608 — test constants
        return cursor.fetchone()[0]


def snapshot() -> dict[str, int]:
    return {step.table: count(step.table) for step in wipe.STEPS if step.table in _tables()}


def _tables() -> set[str]:
    with connection.cursor() as cursor:
        return wipe.existing_tables(cursor)


def _v2_nodes(executor: MigrationExecutor) -> list[tuple[str, str]]:
    """Every migration except the generation migrations after the v2 leaf and their dependents."""
    graph = executor.loader.graph
    later = sorted(child.key for child in graph.node_map[V2_LEAF].children if child.key[0] == V2_LEAF[0])
    v3 = {node for key in later for node in graph.backwards_plan(key)}
    return [node for node in graph.nodes if node not in v3]


def migrate_to_leaf() -> None:
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    executor.migrate(targets)
    executor = MigrationExecutor(connection)
    assert executor.migration_plan(executor.loader.graph.leaf_nodes()) == []


class FakeDocker:
    """``docker_client()`` stand-in: records stops; names in ``missing`` raise NotFound."""

    def __init__(self, missing: tuple[str, ...] = ()) -> None:
        self.missing = set(missing)
        self.stopped: list[str] = []
        self.closed = False
        self.containers = self

    def get(self, name: str) -> SimpleNamespace:
        if name in self.missing:
            msg = f"No such container: {name}"
            raise docker.errors.NotFound(msg)
        return SimpleNamespace(stop=lambda timeout: self.stopped.append(name))

    def close(self) -> None:
        self.closed = True


# -- fixtures -----------------------------------------------------------------------------------


@pytest.fixture
def v2_apps(transactional_db: None) -> Iterator[Apps]:
    """The historical apps at the generation v2 leaf; the database is migrated forward afterwards."""
    executor = MigrationExecutor(connection)
    nodes = _v2_nodes(executor)
    executor.migrate([V2_LEAF])
    executor.loader.build_graph()
    try:
        yield executor.loader.project_state(nodes).apps
    finally:
        migrate_to_leaf()


@pytest.fixture
def build_lease(v2_apps: Apps) -> Iterator[Callable[[Any], None]]:
    """Insert an ``execution_runtimebuildlease`` row (the BuildKit WIP table; created raw when absent)."""
    try:
        model = v2_apps.get_model("execution", "RuntimeBuildLease")
    except LookupError:
        model = None
    if model is None:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS execution_runtimebuildlease")
            cursor.execute(
                "CREATE TABLE execution_runtimebuildlease ("
                " action_id uuid PRIMARY KEY REFERENCES runtime_containeraction (id) DEFERRABLE INITIALLY DEFERRED,"
                " owner varchar(80) NOT NULL, expires_at timestamptz NOT NULL)",
            )

    def create(action_id: Any) -> None:
        if model is not None:
            model.objects.create(action_id=action_id, owner="wp00", expires_at=timezone.now())
            return
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO execution_runtimebuildlease VALUES (%s, 'wp00', now())", [action_id])

    try:
        yield create
    finally:
        if model is None:
            with connection.cursor() as cursor:
                cursor.execute("DROP TABLE IF EXISTS execution_runtimebuildlease")


@pytest.fixture
def empty_schema(db: None) -> Iterator[str]:
    """A schema with no tables at all, made current for the test (rolled back with the test)."""
    with connection.cursor() as cursor:
        cursor.execute("CREATE SCHEMA wp00_empty")
        cursor.execute("SET search_path TO wp00_empty")
    try:
        yield "wp00_empty"
    finally:
        with connection.cursor() as cursor:
            cursor.execute("RESET search_path")


@pytest.fixture
def runs_root(settings: Any, tmp_path: Path) -> Path:
    root = tmp_path / "generation_runs"
    (root / "11111111-job").mkdir(parents=True)
    (root / "11111111-job" / "llm_calls.jsonl.gz").write_bytes(b"\x1f\x8b")
    (root / "stray.txt").write_text("x")
    settings.GENERATION_RUNS_ROOT = str(root)
    return root


# -- v2 data ------------------------------------------------------------------------------------


def populate(apps: Apps, media_root: Path) -> SimpleNamespace:
    """One of everything the wipe touches, plus unrelated rows it must keep."""
    get = apps.get_model
    owner = get("users", "User").objects.create(email="wp00-owner@example.com", password=make_password(None))
    other = get("users", "User").objects.create(email="wp00-other@example.com", password=make_password(None))
    endpoint = get("inference", "LLMEndpoint").objects.create(
        slug="wp00-local",
        name="WP00 local",
        kind="ollama",
        connection="direct",
        owner=owner,
    )
    model = get("llm_models", "LLMModel").objects.create(
        model_id="wp00/model",
        canonical_slug="wp00_model",
        provider="wp00",
        model_name="WP00 model",
    )

    # the v2 generation tables
    template = get("generation", "AppRequirementTemplate")
    system_template = template.objects.create(name="WP00 system", slug="wp00-system", is_default=True)
    user_template = template.objects.create(name="WP00 user", slug="wp00-user", is_default=False)
    get("generation", "ContentBlock").objects.create(
        block_type="prompt_tone",
        slug="wp00-block",
        name="b",
        content="c",
    )
    # explicit stack: the v2 field default reads a setting v3 removes
    profile = get("generation", "GenerationProfile").objects.create(
        name="WP00",
        slug="wp00-profile",
        scaffolding_slug="flask-react",
    )
    preset = get("generation", "RunPreset")
    harness = preset.objects.create(kind="harness", slug="wp00-harness", name="h")
    sampling = preset.objects.create(kind="sampling", slug="wp00-sampling", name="s")
    budget = preset.objects.create(kind="budget", slug="wp00-budget", name="b")
    experiment_model = get("generation", "Experiment")
    experiment = experiment_model.objects.create(
        name="WP00",
        slug="wp00",
        created_by=owner,
        sampling_preset=sampling,
        budget_preset=budget,
    )
    clone = experiment_model.objects.create(
        name="WP00 clone",
        slug="wp00-clone",
        created_by=owner,
        cloned_from=experiment,
    )
    experiment.app_requirements.add(user_template, system_template)
    condition = get("generation", "ExperimentCondition").objects.create(
        experiment=experiment,
        profile=profile,
        model=model,
        harness_preset=harness,
    )
    schedule = get("generation", "ExperimentSchedule").objects.create(
        experiment=experiment,
        action="clone_and_run",
        cron="0 3 * * *",
        created_by=owner,
    )
    # the FK cycles the wipe has to break
    experiment.baseline_condition = condition
    experiment.save(update_fields=["baseline_condition"])
    clone.created_by_schedule = schedule
    clone.save(update_fields=["created_by_schedule"])
    batch = get("generation", "GenerationBatch").objects.create(mode="agent", created_by=owner, experiment=experiment)
    job_model = get("generation", "GenerationJob")
    job = job_model.objects.create(
        mode="agent",
        created_by=owner,
        batch=batch,
        model=model,
        app_requirement=user_template,
        profile=profile,
        experiment=experiment,
        condition=condition,
        harness_preset=harness,
        sampling_preset=sampling,
        budget_preset=budget,
        endpoint=endpoint,
    )
    old_job = job_model.objects.create(mode="scaffolding", created_by=other, app_requirement=system_template)
    get("generation", "GenerationArtifact").objects.create(job=job, stage="agent_call_0001")
    get("generation", "CopilotIteration").objects.create(job=old_job, iteration_number=1, action="generate")

    # inference: telemetry stays, unlinked
    llm_request = get("inference", "LLMRequest")
    deadline = timezone.now() + timedelta(hours=1)
    llm_request.objects.create(
        endpoint=endpoint,
        job=job,
        source="agent_proxy",
        path="/v1/chat/completions",
        deadline_at=deadline,
    )
    llm_request.objects.create(endpoint=endpoint, source="probe", path="/api/chat", deadline_at=deadline)

    # analysis: the job's run goes, an upload-only run stays
    run_model = get("analysis", "AnalysisRun")
    result_model = get("analysis", "ToolResult")
    finding_model = get("analysis", "Finding")
    job_run = run_model.objects.create(created_by=owner, generation_job=job)
    job_finding = finding_model.objects.create(
        result=result_model.objects.create(run=job_run, tool_slug="bandit"),
        title="f",
    )
    free_run = run_model.objects.create(created_by=owner)
    free_finding = finding_model.objects.create(
        result=result_model.objects.create(run=free_run, tool_slug="bandit"),
        title="kept",
    )

    # reports: by job, by wiped analysis run, by v2 post-run; one unrelated report stays
    report = get("reports", "Report")
    report.objects.create(report_type="generation_analytics", title="job", generation_job=job)
    report.objects.create(report_type="tool_analysis", title="job run", analysis_run=job_run)
    post_run_report = report.objects.create(report_type="experiment_report", title="post-run")
    get("generation", "ExperimentPostRun").objects.create(
        experiment=experiment,
        trigger="completed",
        config={},
        report=post_run_report,
    )
    kept_report = report.objects.create(report_type="tool_analysis", title="kept", analysis_run=free_run)

    # runtime: two job containers (one already gone from docker) and an unrelated one
    container = get("runtime", "ContainerInstance")
    action = get("runtime", "ContainerAction")
    port = get("runtime", "PortAllocation")
    job_container = container.objects.create(name="llm-wp00-job", generation_job=job, status="running")
    container.objects.create(name="llm-wp00-gone", generation_job=old_job, status="stopped")
    free_container = container.objects.create(name="wp00-free", status="running")
    job_action = action.objects.create(action_id="wp00-build", container=job_container, action_type="build")
    action.objects.create(action_id="wp00-free-start", container=free_container, action_type="start")
    port.objects.create(app_port=19001, container=job_container)
    port.objects.create(app_port=19002, container=free_container)
    workspace = get("analysis", "AnalyzerWorkspace").objects.create(user=other, container=job_container)

    # human evaluation: a task over v2 apps (wiped whole) and an unrelated task (kept)
    task_model = get("human_eval", "EvalTask")
    app_model = get("human_eval", "EvalTaskApp")
    item_model = get("human_eval", "EvalItem")
    participant_model = get("human_eval", "Participant")
    assignment_model = get("human_eval", "Assignment")
    observation_model = get("human_eval", "Observation")
    attachment_model = get("human_eval", "Attachment")
    verdict_model = get("human_eval", "FindingVerdict")

    task = task_model.objects.create(title="v2 task", experiment=experiment)
    app_a = app_model.objects.create(task=task, job=job, label="App 01", container=job_container)
    app_b = app_model.objects.create(task=task, label="App 02")
    item = item_model.objects.create(task=task, app_a=app_a, app_b=app_b)
    invite = get("human_eval", "EvalInvite").objects.create(task=task)
    participant = participant_model.objects.create(task=task, number=1, kind="invite", invite=invite)
    assignment = assignment_model.objects.create(task=task, participant=participant, item=item, position=1)
    get("human_eval", "Response").objects.create(assignment=assignment, block_id="rubric")
    observation = observation_model.objects.create(assignment=assignment, task_app=app_a, kind="bug", title="o")
    attachment_model.objects.create(
        observation=observation,
        file="human_eval/wp00-wiped.png",
        content_type="image/png",
        size=2,
    )
    verdict_model.objects.create(
        assignment=assignment,
        task_app=app_a,
        finding=job_finding,
        finding_key=str(job_finding.id),
        verdict="tp",
    )
    get("human_eval", "HttpExchange").objects.create(assignment=assignment, task_app=app_a, method="GET", path="/")

    kept_task = task_model.objects.create(title="kept task")
    kept_app = app_model.objects.create(task=kept_task, label="App 01", container=job_container)
    kept_item = item_model.objects.create(task=kept_task, app_a=kept_app)
    kept_participant = participant_model.objects.create(task=kept_task, number=1, kind="code")
    kept_assignment = assignment_model.objects.create(
        task=kept_task,
        participant=kept_participant,
        item=kept_item,
        position=1,
    )
    kept_observation = observation_model.objects.create(
        assignment=kept_assignment,
        task_app=kept_app,
        kind="note",
        title="kept",
    )
    attachment_model.objects.create(
        observation=kept_observation,
        file="human_eval/wp00-kept.png",
        content_type="image/png",
        size=2,
    )
    kept_verdict = verdict_model.objects.create(
        assignment=kept_assignment,
        task_app=kept_app,
        finding=job_finding,
        finding_key=str(job_finding.id),
        verdict="fp",
    )
    for name in ("wp00-wiped.png", "wp00-kept.png"):
        (media_root / "human_eval").mkdir(parents=True, exist_ok=True)
        (media_root / "human_eval" / name).write_bytes(b"\x89P")

    return SimpleNamespace(
        job_action=job_action.id,
        free_run=str(free_run.id),
        free_finding=str(free_finding.id),
        kept_report=str(kept_report.id),
        free_container=str(free_container.id),
        workspace=workspace.id,
        kept_task=str(kept_task.id),
        kept_app=kept_app.id,
        kept_verdict=kept_verdict.id,
    )


def populate_minimal(apps: Apps) -> None:
    """A job with a running container."""
    owner = apps.get_model("users", "User").objects.create(email="wp00-min@example.com", password=make_password(None))
    job = apps.get_model("generation", "GenerationJob").objects.create(mode="agent", created_by=owner)
    apps.get_model("runtime", "ContainerInstance").objects.create(
        name="llm-wp00-min",
        generation_job=job,
        status="running",
    )


# -- tests --------------------------------------------------------------------------------------


def test_wipe_dry_run_then_yes_then_idempotent(
    v2_apps: Apps,
    build_lease: Callable[[Any], None],
    runs_root: Path,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media_root = Path(settings.MEDIA_ROOT)
    world = populate(v2_apps, media_root)
    build_lease(world.job_action)
    fake = FakeDocker(missing=("llm-wp00-gone",))
    monkeypatch.setattr(wipe, "docker_client", lambda: fake)

    expected = {
        "human_eval_attachment": 1,
        "human_eval_response": 1,
        "human_eval_httpexchange": 1,
        "human_eval_findingverdict": 1,
        "human_eval_findingverdict.finding_id": 1,
        "human_eval_observation": 1,
        "human_eval_assignment": 1,
        "human_eval_participant": 1,
        "human_eval_evalinvite": 1,
        "human_eval_evalitem": 1,
        "human_eval_evaltaskapp.container_id": 1,
        "human_eval_evaltaskapp": 2,
        "human_eval_evaltask": 1,
        "analysis_finding": 1,
        "analysis_toolresult": 1,
        "generation_experimentpostrun": 1,
        "reports_report": 3,
        "analysis_analysisrun": 1,
        "execution_runtimebuildlease": 1,
        "runtime_containeraction": 1,
        "runtime_portallocation": 1,
        "analysis_analyzerworkspace.container_id": 1,
        "runtime_containerinstance": 2,
        "inference_llmrequest.job_id": 1,
        "generation_copilotiteration": 1,
        "generation_generationartifact": 1,
        "generation_generationjob": 2,
        "generation_generationbatch": 1,
        "generation_experimentschedule": 1,
        "generation_experimentcondition": 1,
        "generation_experiment_app_requirements": 2,
        "generation_experiment": 2,
        # seeded system rows go too; only the system app requirement templates stay
        **{table: count(table) for table in SEEDED_TABLES},
        "generation_apprequirementtemplate": count("generation_apprequirementtemplate", "is_default = false"),
    }
    assert count("generation_runpreset") >= 3
    assert expected["generation_apprequirementtemplate"] >= 1
    before = snapshot()

    # dry run (the default): counts, no change, no docker
    dry = run_wipe()
    assert "Dry run" in dry
    assert row_counts(dry) == expected
    assert snapshot() == before
    assert fake.stopped == []
    assert "llm-wp00-job (db status running)" in dry
    assert "--yes would delete 2 entries under" in dry
    assert "attachment files: --yes would delete 1." in dry
    assert sorted(p.name for p in runs_root.iterdir()) == ["11111111-job", "stray.txt"]

    # --yes: the same counts, applied
    applied = run_wipe("--yes")
    assert row_counts(applied) == expected
    assert "Committed." in applied
    assert fake.stopped == ["llm-wp00-job"]
    assert fake.closed
    assert "llm-wp00-gone (db status stopped): not found" in applied
    assert "llm-wp00-job (db status running): stopped" in applied
    for table in FULLY_WIPED:
        assert count(table) == 0, table
    assert count("generation_apprequirementtemplate", "is_default = false") == 0
    assert count("generation_apprequirementtemplate", "slug = 'wp00-system'") == 1
    # telemetry history kept, unlinked
    assert count("inference_llmrequest") == 2
    assert count("inference_llmrequest", "job_id IS NOT NULL") == 0
    # unrelated rows survive, their references to wiped rows set NULL
    assert ids("analysis_analysisrun") == {world.free_run}
    assert ids("analysis_finding") == {world.free_finding}
    assert ids("reports_report") == {world.kept_report}
    assert ids("runtime_containerinstance") == {world.free_container}
    assert count("runtime_containeraction") == 1
    assert count("runtime_portallocation", "app_port = 19002") == count("runtime_portallocation") == 1
    assert count("execution_runtimebuildlease") == 0
    assert column("analysis_analyzerworkspace", "container_id", world.workspace) is None
    assert ids("human_eval_evaltask") == {world.kept_task}
    assert column("human_eval_evaltaskapp", "container_id", world.kept_app) is None
    assert column("human_eval_findingverdict", "finding_id", world.kept_verdict) is None
    assert count("human_eval_observation") == count("human_eval_attachment") == 1
    # files: the run root is emptied (but kept), only the wiped task's attachment is deleted
    assert runs_root.is_dir()
    assert list(runs_root.iterdir()) == []
    assert "Run files: deleted 2 entries" in applied
    assert not (media_root / "human_eval" / "wp00-wiped.png").exists()
    assert (media_root / "human_eval" / "wp00-kept.png").exists()

    # a second --yes deletes nothing
    survivors = snapshot()
    again = run_wipe("--yes")
    assert set(row_counts(again).values()) == {0}
    assert snapshot() == survivors
    assert fake.stopped == ["llm-wp00-job"]
    assert "Containers of generation jobs (runtime_containerinstance): none." in again

    # and the wiped database migrates forward to the leaf
    migrate_to_leaf()


def test_docker_failure_is_not_fatal(v2_apps: Apps, runs_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    populate_minimal(v2_apps)

    def no_daemon() -> None:
        msg = "Cannot connect to the Docker daemon"
        raise ConnectionError(msg)

    monkeypatch.setattr(wipe, "docker_client", no_daemon)
    out = run_wipe("--yes")
    assert "llm-wp00-min (db status running): skipped, docker unavailable (Cannot connect" in out
    assert count("generation_generationjob") == count("runtime_containerinstance") == 0
    assert list(runs_root.iterdir()) == []


def test_skip_containers_never_calls_docker(v2_apps: Apps, runs_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    populate_minimal(v2_apps)
    monkeypatch.setattr(wipe, "docker_client", lambda: pytest.fail("docker must not be called"))
    out = run_wipe("--yes", "--skip-containers")
    assert "skipped (--skip-containers):\n  llm-wp00-min (db status running)" in out
    assert count("runtime_containerinstance") == 0


def test_a_failing_statement_rolls_back_the_whole_wipe(
    v2_apps: Apps,
    runs_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Constraints are checked per statement, so a step out of FK order fails and nothing is deleted."""
    populate_minimal(v2_apps)
    jobs_first = sorted(wipe.STEPS, key=lambda step: step.table != "generation_generationjob")
    monkeypatch.setattr(wipe, "STEPS", tuple(jobs_first))
    monkeypatch.setattr(wipe, "docker_client", FakeDocker)
    with pytest.raises(IntegrityError):
        run_wipe("--yes")
    assert count("generation_generationjob") == count("runtime_containerinstance") == 1
    assert len(list(runs_root.iterdir())) == 2


def test_refuses_an_unhandled_foreign_key(v2_apps: Apps, runs_root: Path) -> None:
    populate_minimal(v2_apps)
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TABLE wp00_extra (id serial PRIMARY KEY,"
            " job_id uuid REFERENCES generation_generationjob (id) DEFERRABLE INITIALLY DEFERRED)",
        )
    try:
        with pytest.raises(CommandError, match=re.escape("wp00_extra(job_id) -> generation_generationjob")):
            run_wipe("--yes")
    finally:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE wp00_extra")
    assert count("generation_generationjob") == 1
    assert len(list(runs_root.iterdir())) == 2


def test_no_op_on_a_schema_without_v2_tables(empty_schema: str, runs_root: Path) -> None:
    for args in ((), ("--yes",)):
        out = run_wipe(*args)
        assert f"No v2 generation tables in schema '{empty_schema}'" in out
        assert "nothing to wipe" in out
    assert len(list(runs_root.iterdir())) == 2


def test_refuses_a_schema_with_v2_and_v3_tables(empty_schema: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("CREATE TABLE generation_generationjob (id uuid PRIMARY KEY)")
        cursor.execute("CREATE TABLE generation_run (id uuid PRIMARY KEY)")
    with pytest.raises(CommandError, match="both v2"):
        run_wipe()


def test_missing_tables_are_skipped(
    empty_schema: str,
    runs_root: Path,
    django_capture_on_commit_callbacks: Any,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute("CREATE TABLE generation_generationjob (id uuid PRIMARY KEY)")
        cursor.execute("INSERT INTO generation_generationjob VALUES (gen_random_uuid()), (gen_random_uuid())")
    with django_capture_on_commit_callbacks(execute=True):
        out = run_wipe("--yes")
    assert row_counts(out) == {"generation_generationjob": 2}
    assert "human_eval_evaltask" in out
    assert "(table absent)" in out
    assert count("generation_generationjob") == 0
    assert list(runs_root.iterdir()) == []


@pytest.mark.parametrize("flag", ["--y", "--ye", "--yes=1"])
def test_yes_must_be_given_literally(flag: str) -> None:
    with pytest.raises(CommandError):
        call_command("wipe_generation_v2", flag, stdout=StringIO())


def test_plan_is_well_formed() -> None:
    """Plain table names, unique scope names, and one output label per counted step."""
    names = [step.table for step in wipe.STEPS] + [scope.table for scope in wipe.SCOPES]
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in names)
    assert len({scope.name for scope in wipe.SCOPES}) == len(wipe.SCOPES)
    labels = [step.label for step in wipe.STEPS if not step.internal]
    assert len(labels) == len(set(labels))
