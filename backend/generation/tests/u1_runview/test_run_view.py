"""``RunView`` accessors (spec section 3.11; WP-02a tests in section 12.3)."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from backend.generation import blobs
from backend.generation.models import GenerationRun
from backend.generation.models import choices
from backend.generation.runs import view as view_module
from backend.generation.runs.view import PARENT_ORIGIN
from backend.generation.runs.view import current_runs
from backend.generation.runs.view import run_view
from backend.generation.runs.view import run_views
from backend.generation.runs.view import run_views_code
from backend.generation.runs.view import run_views_files
from backend.generation.runs.view import source_loc
from backend.generation.testing import factories

pytestmark = pytest.mark.django_db

O = choices.FileOrigin  # noqa: E741


def add_file(run, path, text, origin=O.AUTHORED, **extra):
    """A RunFile with a real blob; ``loc`` is the source LOC of ``text`` unless given."""
    extra.setdefault("loc", source_loc(path, text))
    if origin == O.DELETED:
        return factories.RunFileFactory(run=run, path=path, blob="", size=0, origin=origin, loc=0)
    sha = blobs.put_text(text)
    return factories.RunFileFactory(run=run, path=path, blob=sha, size=len(text), origin=origin, **extra)


@pytest.fixture
def app_run():
    run = factories.GenerationRunFactory(tested=True)
    add_file(run, "Dockerfile", "FROM python:3.13-slim\n", origin=O.STARTER, excluded="non_source")
    add_file(run, "backend/app.py", "import flask\n\n# starter\napp = flask.Flask(__name__)\n", origin=O.STARTER)
    add_file(run, "backend/routes.py", "def a():\n    return 1\n\n\ndef b():\n    return 2\n", origin=O.AUTHORED)
    add_file(run, "backend/models.py", "x = 1\ny = 2\n", origin=O.MODIFIED)
    add_file(run, "backend/tests/test_routes.py", "def test_a():\n    assert True\n", origin=O.AUTHORED)
    add_file(run, "frontend/package-lock.json", '{\n  "lockfileVersion": 3\n}\n', excluded="lockfile")
    add_file(run, "frontend/dist/app.js", "console.log(1)\n", origin=O.ARTIFACT)
    add_file(run, "vendor/lib.js", "var x = 1;\n", origin=O.AUTHORED, excluded="vendored")
    add_file(run, "backend/old.py", "", origin=O.DELETED)
    return run


# -- code_files / file_rows -------------------------------------------------------------------------------


def test_model_scope_excludes_starter_artifact_and_excluded_rows(app_run):
    files = run_view(app_run).code_files("model")
    assert set(files) == {"backend/routes.py", "backend/models.py", "backend/tests/test_routes.py"}
    assert files["backend/models.py"] == "x = 1\ny = 2\n"


def test_app_scope_is_the_whole_non_artifact_tree(app_run):
    view = run_view(app_run)
    files = view.code_files("app")
    assert set(files) == {
        "Dockerfile",
        "backend/app.py",
        "backend/routes.py",
        "backend/models.py",
        "backend/tests/test_routes.py",
        "frontend/package-lock.json",
        "vendor/lib.js",
    }
    origins = {entry.path: entry.origin for entry in view.files("app")}
    assert origins["backend/app.py"] == O.STARTER
    assert PARENT_ORIGIN not in origins.values()


def test_slot_scope_is_empty_for_an_app_run(app_run):
    assert run_view(app_run).code_files("slot") == {}


def test_unknown_scope_is_refused(app_run):
    with pytest.raises(ValueError, match="scope"):
        run_view(app_run).code_files("everything")


def test_file_rows_agree_with_files_for_every_scope(app_run):
    view = run_view(app_run)
    for scope in ("model", "app", "slot"):
        rows = list(view.file_rows(scope))
        assert [row.path for row in rows] == [entry.path for entry in view.files(scope)]
        assert all(row.view_origin == row.origin for row in rows)


def test_binary_rows_are_in_the_tree_but_have_no_text(app_run):
    add_file(app_run, "frontend/logo.png", "PNG", origin=O.AUTHORED, excluded="binary")
    view = run_view(app_run)
    assert "frontend/logo.png" in {entry.path for entry in view.files("app")}
    assert "frontend/logo.png" not in view.code_files("app")


# -- slot variants: parent overlay -----------------------------------------------------------------------


@pytest.fixture
def variant(app_run):
    target = factories.SlotTargetFactory(
        slot_set=factories.SlotSetFactory(parent_run=app_run),
        path="backend/routes.py",
    )
    run = factories.GenerationRunFactory(slot_single_shot=True, slot_target=target)
    add_file(run, "backend/routes.py", "def a():\n    return 42\n", origin=O.SLOT)
    return run


def test_variant_app_scope_overlays_parent_rows_as_parent(variant, app_run):
    view = run_view(variant)
    entries = {entry.path: entry for entry in view.files("app")}
    assert set(entries) == set(run_view(app_run).code_files("app"))
    assert entries["backend/routes.py"].origin == O.SLOT
    assert entries["backend/routes.py"].run_id == str(variant.pk)
    others = [entry for path, entry in entries.items() if path != "backend/routes.py"]
    assert others
    assert {entry.origin for entry in others} == {PARENT_ORIGIN}
    assert {entry.run_id for entry in others} == {str(app_run.pk)}
    assert view.code_files("app")["backend/routes.py"] == "def a():\n    return 42\n"


def test_variant_model_and_slot_scopes_are_only_its_slot_rows(variant):
    view = run_view(variant)
    assert view.code_files("model") == {"backend/routes.py": "def a():\n    return 42\n"}
    assert view.code_files("slot") == view.code_files("model")


def test_variant_file_rows_annotate_parent_origin(variant, app_run):
    view = run_view(variant)
    rows = list(view.file_rows("app"))
    assert [row.path for row in rows] == [entry.path for entry in view.files("app")]
    by_path = {row.path: row for row in rows}
    assert by_path["backend/routes.py"].run_id == variant.pk
    assert by_path["backend/routes.py"].view_origin == O.SLOT
    assert by_path["backend/app.py"].view_origin == PARENT_ORIGIN


def test_variant_inherits_stack_and_auth_mode_from_parent(app_run):
    stack = factories.StackProfileFactory()
    GenerationRun.objects.filter(pk=app_run.pk).update(stack_profile=stack, auth_mode="contract_bearer")
    app_run.refresh_from_db()
    target = factories.SlotTargetFactory(slot_set=factories.SlotSetFactory(parent_run=app_run))
    variant = factories.GenerationRunFactory(slot_agent=True, slot_target=target)
    view = run_view(variant)
    assert view.stack is not None
    assert view.stack.ref == stack.ref
    assert view.auth_mode == "contract_bearer"


# -- loc --------------------------------------------------------------------------------------------------


def test_loc_excludes_tests_lockfiles_and_excluded_rows(app_run):
    view = run_view(app_run)
    # routes.py: 4 code lines; models.py: 2; the test file, lockfile, vendored and artifact rows count 0.
    assert view.loc("model") == 6
    # app adds the starter's app.py (2 code lines; the comment and blank line are not counted).
    assert view.loc("app") == 8


def test_loc_ignores_wrong_stored_loc_on_tests_and_lockfiles(app_run):
    add_file(app_run, "frontend/src/App.test.tsx", "it('x', () => {})\n", loc=50)
    add_file(app_run, "requirements.txt", "flask==3\n", loc=50)
    assert run_view(app_run).loc("model") == 6


def test_loc_is_none_without_a_collected_tree():
    run = factories.GenerationRunFactory()
    assert run_view(run).loc("model") is None
    assert run_view(run).code_files("app") == {}


def test_loc_is_zero_for_a_collected_tree_without_model_code():
    run = factories.GenerationRunFactory(tested=True)
    add_file(run, "backend/app.py", "x = 1\n", origin=O.STARTER)
    assert run_view(run).loc("model") == 0


def test_variant_loc_counts_its_slot_file_and_the_parent_tree(variant):
    view = run_view(variant)
    assert view.loc("slot") == 2
    assert view.loc("app") == 2 + 2 + 2  # slot routes.py + parent models.py + parent starter app.py


def test_source_loc_rules():
    assert source_loc("backend/a.py", "a = 1\n# c\n\nb = 2\n") == 2
    assert source_loc("backend/tests/test_a.py", "a = 1\n") == 0
    assert source_loc("package-lock.json", "{}\n") == 0
    assert source_loc("frontend/src/App.spec.ts", "x\n") == 0


# -- bulk reader ------------------------------------------------------------------------------------------


def test_run_views_code_matches_single_reader(app_run, variant):
    other = factories.GenerationRunFactory()
    runs = GenerationRun.objects.filter(pk__in=[app_run.pk, variant.pk, other.pk])
    for scope in ("model", "app", "slot"):
        bulk = run_views_code(runs, scope)
        assert bulk[str(other.pk)] == {}
        assert bulk[str(app_run.pk)] == run_view(app_run).code_files(scope)
        assert bulk[str(variant.pk)] == run_view(variant).code_files(scope)


def test_run_views_code_uses_one_row_query_and_reads_each_blob_once(app_run, variant, monkeypatch):
    reads: list[str] = []
    real = blobs.get_bytes
    monkeypatch.setattr(blobs, "get_bytes", lambda sha: reads.append(sha) or real(sha))
    runs = [app_run, variant]
    with CaptureQueriesContext(connection) as ctx:
        run_views_code(runs, "app")
    assert len(ctx.captured_queries) == 1
    assert len(reads) == len(set(reads))


def test_run_views_files_matches_single_reader_with_one_query(app_run, variant):
    other = factories.GenerationRunFactory()
    runs = GenerationRun.objects.filter(pk__in=[app_run.pk, variant.pk, other.pk])
    for scope in ("model", "app", "slot"):
        with CaptureQueriesContext(connection) as ctx:
            bulk = run_views_files(runs, scope)
        assert len(ctx.captured_queries) == 2  # the run triples, then one RunFile query (parents included)
        assert bulk[str(other.pk)] == []
        assert bulk[str(app_run.pk)] == run_view(app_run).files(scope)
        assert bulk[str(variant.pk)] == run_view(variant).files(scope)
    assert any(entry.origin == PARENT_ORIGIN for entry in run_views_files([variant], "app")[str(variant.pk)])


def test_source_loc_is_the_reports_rule():
    from backend.reports.services import loc

    for path, text in (("app/main.py", "import os\n# c\n\nx = 1\n"), ("tests/test_a.py", "x = 1\n")):
        assert source_loc(path, text) == loc.source_loc(path, text)


# -- run_views ----------------------------------------------------------------------------------------------


def test_run_views_has_no_n_plus_one():
    runs = []
    for _ in range(3):
        run = factories.GenerationRunFactory(tested=True)
        factories.RunManifestFactory(run=run)
        runs.append(run)
    qs = GenerationRun.objects.filter(pk__in=[run.pk for run in runs])
    with CaptureQueriesContext(connection) as ctx:
        views = run_views(qs)
        for view in views:
            view.manifest()
            view.labels()
            assert view.task is not None
            assert view.model_slug
    assert len(ctx.captured_queries) == 1
    assert {view.id for view in views} == {str(run.pk) for run in runs}


def test_run_views_accepts_a_list():
    run = factories.GenerationRunFactory()
    assert [view.id for view in run_views([run])] == [str(run.pk)]


# -- identity, task, manifest, labels ---------------------------------------------------------------------


def test_view_fields(app_run):
    style = factories.StyleDirectiveFactory()
    GenerationRun.objects.filter(pk=app_run.pk).update(
        metrics={"cost_usd": 0.12, "total_tokens": 3400},
        style=style,
        prose_variant="minimal",
    )
    app_run.refresh_from_db()
    manifest = factories.RunManifestFactory(run=app_run)
    view = run_view(app_run)
    assert view.id == str(app_run.pk)
    assert view.kind == choices.RunKind.APP_AGENTIC
    assert view.protocol == "app@1"
    assert view.usable is True
    assert view.model_slug == manifest.manifest["model"]["slug"]
    assert view.cost_usd == 0.12
    assert view.total_tokens == 3400
    assert view.style_ref == style.ref
    assert view.fingerprints == app_run.fingerprints
    assert view.task is not None
    assert view.task.ref == app_run.task_version.ref
    assert view.task.variant == "minimal"
    assert view.task.features == ["auth", "ownership", "search"]
    assert view.unit_key == app_run.task_version.unit_key
    assert view.manifest() == manifest.manifest


def test_missing_cost_is_none_not_zero():
    view = run_view(factories.GenerationRunFactory())
    assert view.cost_usd is None
    assert view.total_tokens is None
    assert view.manifest() == {}


def test_task_dict_has_structure_and_chosen_prose():
    run = factories.GenerationRunFactory(prose_variant="standard")
    task = run_view(run).task_dict()
    assert task is not None
    assert task["ref"] == run.task_version.ref
    assert task["variant"] == "standard"
    assert task["prose"] == run.task_version.content["prose"]["standard"]
    assert "prose" not in task["structured"]
    assert task["structured"]["title"] == task["title"]


def test_task_dict_is_none_for_a_reference_run_without_task():
    run = factories.GenerationRunFactory(reference=True, task_version=None)
    assert run_view(run).task_dict() is None


def test_labels_cover_columns_and_manifest_factors(app_run):
    stack = factories.StackProfileFactory()
    GenerationRun.objects.filter(pk=app_run.pk).update(stack_profile=stack, auth_mode="cookie_session")
    app_run.refresh_from_db()
    labels = run_view(app_run).labels()
    assert labels["model"] == app_run.model.model_id
    assert labels["stack_profile"] == stack.ref
    assert labels["auth_mode"] == "cookie_session"


def test_labels_prefer_manifest_factor_values(monkeypatch, app_run):
    monkeypatch.setattr(
        view_module.RunView,
        "manifest",
        lambda self: {"factors": {"style": {"ref": "tailwind@2#abcdef012345"}, "network_policy": {"value": "none"}}},
    )
    labels = run_view(app_run).labels()
    assert labels["style"] == "tailwind@2"
    assert labels["network_policy"] == "none"


# -- latest_evaluation ------------------------------------------------------------------------------------


def test_latest_evaluation_per_suite_ignores_retests(app_run):
    suite = factories.EvalSuiteFactory()
    other = factories.EvalSuiteFactory()
    first = factories.RunEvaluationFactory(run=app_run, eval_suite=suite, measured=True)
    second = factories.RunEvaluationFactory(run=app_run, eval_suite=suite, measured=True)
    factories.RunEvaluationFactory(run=app_run, eval_suite=suite, measured=True, retest_of=second)
    latest_other = factories.RunEvaluationFactory(run=app_run, eval_suite=other)
    view = run_view(app_run)
    assert view.latest_evaluation(suite.ref) == second
    assert view.latest_evaluation(f"{suite.slug}@{suite.version + 1}") is None
    assert view.latest_evaluation(other.ref) == latest_other
    # No manifest: the latest row of any suite.
    assert view.latest_evaluation() == latest_other
    assert first.pk != second.pk


def test_latest_evaluation_defaults_to_the_planned_suite(app_run):
    planned = factories.EvalSuiteFactory(slug="eval-v2")
    other = factories.EvalSuiteFactory()
    factories.RunManifestFactory(run=app_run)  # planned_evaluation.eval_suite.ref == "eval-v2@1"
    mine = factories.RunEvaluationFactory(run=app_run, eval_suite=planned)
    factories.RunEvaluationFactory(run=app_run, eval_suite=other)
    assert run_view(app_run).latest_evaluation() == mine


def test_latest_evaluation_is_none_when_never_evaluated(app_run):
    assert run_view(app_run).latest_evaluation() is None


# -- current runs (runs.queries) -----------------------------------------------------------------------------------


def test_current_runs_is_runs_queries(monkeypatch):
    from backend.generation.runs import queries

    seen = []
    monkeypatch.setattr(queries, "current_runs", lambda study, stage="main": seen.append((study, stage)) or "qs")
    assert current_runs("s", stage="pilot") == "qs"
    assert seen == [("s", "pilot")]
    assert not hasattr(view_module, "_current_runs_tmp")
    assert not hasattr(view_module, "_reports_loc")


def test_current_runs_takes_the_first_usable_attempt_per_cell():
    cell = factories.StudyCellFactory()
    study = cell.study

    def attempt(n, outcome, **extra):
        return factories.GenerationRunFactory(
            study=study,
            condition=cell.condition,
            cell=cell,
            cell_attempt=n,
            status=choices.RunStatus.COMPLETED,
            outcome=outcome,
            **extra,
        )

    attempt(1, choices.Outcome.INFRA_ERROR)
    usable = attempt(2, choices.Outcome.TESTED)
    attempt(3, choices.Outcome.TESTED)
    attempt(4, choices.Outcome.TESTED, beyond_plan=True)

    failing_cell = factories.StudyCellFactory(condition=cell.condition)
    factories.GenerationRunFactory(
        study=study,
        condition=cell.condition,
        cell=failing_cell,
        cell_attempt=1,
        outcome=choices.Outcome.INFRA_ERROR,
    )
    last_failure = factories.GenerationRunFactory(
        study=study,
        condition=cell.condition,
        cell=failing_cell,
        cell_attempt=2,
        outcome=choices.Outcome.INFRA_ERROR,
    )

    current = set(current_runs(study).values_list("pk", flat=True))
    assert current == {usable.pk, last_failure.pk}
    assert not current_runs(study, stage="pilot").exists()
