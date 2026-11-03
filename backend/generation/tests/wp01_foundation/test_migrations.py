"""v3 migrations (spec section 3.12): the wipe guard, the graph order, reversibility and a clean state.

The guard's counting is tested against throwaway v2-named tables inside the test transaction (fast). One
``MigrationExecutor`` round trip (leaf -> v2 leaf -> leaf, about two minutes because Django renders the
whole project state) checks that the guard fires on real v2 rows, that seed rows pass, and that 0024 is
reversible. Until WP-02a/WP-02b land, run with ``--ds=backend.generation.tests.wp01_foundation.settings_u1``.
"""

from __future__ import annotations

import importlib
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

V2_LEAF = ("generation", "0022_artifact_telemetry")
GUARD = ("generation", "0023_v3_wipe_guard")
V3_MODELS = ("generation", "0024_v3_models")

guard_module = importlib.import_module("backend.generation.migrations.0023_v3_wipe_guard")


def _graph():
    return MigrationLoader(None, ignore_no_migrations=True).graph


def _executor() -> MigrationExecutor:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return executor


def _create_table(name: str, columns: str = "") -> None:
    with connection.cursor() as cursor:
        cursor.execute(f"CREATE TABLE {name} (id serial PRIMARY KEY{columns})")


def _insert(table: str, values: str = "DEFAULT VALUES") -> None:
    with connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO {table} {values}")


# =====================================================================================
# Graph and state (no schema changes)
# =====================================================================================


def test_the_test_database_is_fully_migrated(db):
    executor = _executor()
    assert executor.migration_plan(executor.loader.graph.leaf_nodes()) == []


def test_models_match_migrations(db):
    out = StringIO()
    call_command("makemigrations", check=True, dry_run=True, skip_checks=True, stdout=out, verbosity=1)
    assert "No changes detected" in out.getvalue()


def test_runtime_remove_stack_runs_after_the_v3_models_and_the_buildkit_wip():
    parents = _graph().node_map[("runtime", "0006_v3_remove_stack")].parents
    assert {node.key for node in parents} >= {V3_MODELS, ("runtime", "0005_sample_build_artifact")}


def test_v3_models_rename_after_every_historical_reference():
    """Every app that referenced GenerationJob/Experiment in v2 is migrated before the renames."""
    ancestors = set(_graph().forwards_plan(V3_MODELS))
    assert {
        GUARD,
        ("analysis", "0009_run_batch_tool_timing_tool_severity"),
        ("execution", "0002_runtimebuildlease"),
        ("human_eval", "0001_initial"),
        ("inference", "0002_llmrequest"),
        ("reports", "0005_study_report"),
        ("runtime", "0005_sample_build_artifact"),
    } <= ancestors


def test_v3_tables_replace_the_v2_tables(db):
    tables = set(connection.introspection.table_names())
    assert {"generation_run", "generation_study", "generation_studycondition", "generation_blob"} <= tables
    assert not {"generation_generationjob", "generation_experiment", "runtime_stack"} & tables


# =====================================================================================
# The guard's counting (throwaway v2-named tables, rolled back with the test transaction)
# =====================================================================================


def test_guard_is_a_noop_without_v2_tables(db):
    assert guard_module.v2_row_counts(connection) == {}
    guard_module.refuse_v2_data(None, connection.schema_editor())


def test_guard_ignores_empty_tables_and_seed_rows(db):
    _create_table("generation_generationjob")
    _create_table("generation_contentblock", ", is_system boolean NOT NULL")
    _create_table("generation_apprequirementtemplate", ", is_default boolean NOT NULL")
    _insert("generation_contentblock", "(is_system) VALUES (true)")
    _insert("generation_apprequirementtemplate", "(is_default) VALUES (true)")
    assert guard_module.v2_row_counts(connection) == {}


@pytest.mark.parametrize(
    ("table", "columns", "values"),
    [
        ("generation_generationjob", "", "DEFAULT VALUES"),
        ("generation_copilotiteration", "", "DEFAULT VALUES"),
        ("generation_experiment_app_requirements", "", "DEFAULT VALUES"),
        ("generation_contentblock", ", is_system boolean NOT NULL", "(is_system) VALUES (false)"),
        ("generation_apprequirementtemplate", ", is_default boolean NOT NULL", "(is_default) VALUES (false)"),
        ("generation_runpreset", ", is_system boolean NOT NULL", "(is_system) VALUES (false)"),
    ],
)
def test_guard_refuses_v2_data(db, table, columns, values):
    _create_table(table, columns)
    _insert(table, values)
    _insert(table, values)
    assert guard_module.v2_row_counts(connection) == {table: 2}
    with pytest.raises(RuntimeError, match=rf"wipe_generation_v2 --yes.*{table} \(2\)"):
        guard_module.refuse_v2_data(None, connection.schema_editor())


# =====================================================================================
# One real round trip through the v2 schema
# =====================================================================================


def test_round_trip_through_v2_guard_fires_on_v2_rows_and_0024_is_reversible(transactional_db):
    executor = _executor()
    executor.migrate([V2_LEAF])  # unapplies runtime 0006, generation 0024 and 0023
    try:
        tables = set(connection.introspection.table_names())
        assert {"generation_generationjob", "generation_experiment", "runtime_stack"} <= tables
        assert "generation_run" not in tables

        apps = _executor().loader.project_state(V2_LEAF).apps  # historical models, not the v3 state
        blocks = apps.get_model("generation", "ContentBlock")
        blocks.objects.create(block_type="prompt_tone", slug="seed", name="seed", content="x", is_system=True)
        user_block = blocks.objects.create(block_type="prompt_tone", slug="mine", name="mine", content="x")
        with pytest.raises(RuntimeError, match=r"generation_contentblock \(1\)"):
            _executor().migrate([GUARD])
        assert GUARD not in _executor().loader.applied_migrations  # the failed step rolled back
        user_block.delete()  # the system seed row stays: the guard lets it through
    finally:
        with connection.cursor() as cursor:
            if "generation_contentblock" in connection.introspection.table_names():
                cursor.execute("DELETE FROM generation_contentblock WHERE is_system = false")
        executor = _executor()
        executor.migrate(executor.loader.graph.leaf_nodes())
    tables = set(connection.introspection.table_names())
    assert {"generation_run", "generation_study", "generation_blob"} <= tables
    assert not {"generation_generationjob", "generation_contentblock", "runtime_stack"} & tables
