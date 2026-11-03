"""Delete every v2 sample-generation row before the v3 deploy (SAMPLE_GEN_V3 §3.12, WP-00).

v3 is a clean break. The v2 generation rows, and the rows of other apps that hang off them, are
deleted by this operator command on the **old** (v2) code and schema before v3 is deployed. The
command uses raw SQL only, so it depends on neither version's ORM models, and it looks every table
up in ``information_schema`` first:

- no ``generation_generationjob`` table (a v3 or fresh schema): a no-op that says so;
- otherwise it plans a delete over the v2 generation tables and the dependent rows of other apps
  (the ``STEPS`` below, in FK order), and refuses to run when the schema has a foreign key into a
  wiped table that the plan does not handle, so nothing can FK-block the delete.

Without ``--yes`` it is a dry run: it prints the per-table counts and changes nothing. With the
literal ``--yes`` it

1. stops the docker containers of the ``runtime_containerinstance`` rows that belong to a
   generation job (best effort and never fatal; each container is listed with its outcome;
   ``--skip-containers`` skips this step);
2. deletes in FK order inside one transaction, with every constraint checked immediately;
3. after the commit, deletes the contents of ``GENERATION_RUNS_ROOT`` and the files of the deleted
   human-evaluation attachments.

``inference_llmrequest.job_id`` is set to NULL instead (the rows stay), so the telemetry history
is kept. Cut-over runbook: ``docs/design/V3_CUTOVER.md``.

    manage.py wipe_generation_v2                    # dry run
    manage.py wipe_generation_v2 --yes
    manage.py wipe_generation_v2 --yes --skip-containers
"""

from __future__ import annotations

import contextlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import DEFAULT_DB_ALIAS
from django.db import connections
from django.db import transaction

if TYPE_CHECKING:
    from django.core.management.base import CommandParser
    from django.db.backends.utils import CursorWrapper

# Present on every v2 schema; renamed to ``generation_run`` by the v3 migrations.
V2_MARKER_TABLE = "generation_generationjob"
# Tables only v3 creates. Seeing one next to the v2 marker means a half-migrated schema.
V3_MARKER_TABLES = ("generation_run", "generation_study")
DOCKER_STOP_TIMEOUT_S = 10


def _scope_table(name: str) -> str:
    return f"pg_temp.wipe_v2_{name}"


def _in(column: str, scope: str) -> str:
    return f"{column} IN (SELECT id FROM {_scope_table(scope)})"  # noqa: S608 — module constants only


def _any(*predicates: str) -> str:
    return " OR ".join(f"({predicate})" for predicate in predicates)


@dataclass(frozen=True, slots=True)
class Scope:
    """An id set frozen (as a temp table) before anything is deleted.

    Steps select their rows through scopes, so a dry-run count and the real delete agree, and no
    step depends on rows an earlier step already removed. A scope over a missing table is empty.
    """

    name: str
    table: str
    where: str
    reads: tuple[str, ...] = ()  # other tables the predicate reads


@dataclass(frozen=True, slots=True)
class Step:
    """One DELETE (or UPDATE ... SET NULL) statement of the wipe."""

    table: str
    where: str = ""  # "" = every row
    null: tuple[str, ...] = ()  # set these columns NULL instead of deleting the rows
    # FK columns of ``table`` that this step clears for every wiped target row. A full delete
    # covers all of them. The coverage check refuses to run if a foreign key into a wiped table
    # is covered by no step.
    covers: tuple[str, ...] = ()
    note: str = ""
    internal: bool = False  # cycle breaking: executed with --yes, never counted

    @property
    def label(self) -> str:
        return f"{self.table}.{','.join(self.null)}" if self.null else self.table

    @property
    def action(self) -> str:
        return "set NULL" if self.null else "delete"


# A human-evaluation task is wiped whole when it evaluates v2 apps (an experiment link or any
# job-linked app): dropping only some of its apps would silently change its results.
_HE_ROWS_OF_WIPED_TASKS = _any(_in("assignment_id", "he_assignment"), _in("task_app_id", "he_app"))

SCOPES: tuple[Scope, ...] = (
    Scope(
        "he_task",
        "human_eval_evaltask",
        "experiment_id IS NOT NULL OR id IN (SELECT task_id FROM human_eval_evaltaskapp WHERE job_id IS NOT NULL)",
        reads=("human_eval_evaltaskapp",),
    ),
    Scope("he_app", "human_eval_evaltaskapp", _in("task_id", "he_task")),
    Scope(
        "he_item",
        "human_eval_evalitem",
        _any(_in("task_id", "he_task"), _in("app_a_id", "he_app"), _in("app_b_id", "he_app")),
    ),
    Scope("he_invite", "human_eval_evalinvite", _in("task_id", "he_task")),
    Scope("he_participant", "human_eval_participant", _any(_in("task_id", "he_task"), _in("invite_id", "he_invite"))),
    Scope(
        "he_assignment",
        "human_eval_assignment",
        _any(_in("task_id", "he_task"), _in("item_id", "he_item"), _in("participant_id", "he_participant")),
    ),
    Scope("he_observation", "human_eval_observation", _HE_ROWS_OF_WIPED_TASKS),
    Scope("analysisrun", "analysis_analysisrun", "generation_job_id IS NOT NULL"),
    Scope("toolresult", "analysis_toolresult", _in("run_id", "analysisrun")),
    Scope("finding", "analysis_finding", _in("result_id", "toolresult")),
    Scope(
        "report",
        "reports_report",
        _any(
            "generation_job_id IS NOT NULL",
            _in("analysis_run_id", "analysisrun"),
            # the experiment reports the v2 post-runs generated
            "id IN (SELECT report_id FROM generation_experimentpostrun WHERE report_id IS NOT NULL)",
        ),
        reads=("generation_experimentpostrun",),
    ),
    Scope("container", "runtime_containerinstance", "generation_job_id IS NOT NULL"),
    Scope("containeraction", "runtime_containeraction", _in("container_id", "container")),
)

# Children before parents; every constraint is checked statement by statement (--yes).
STEPS: tuple[Step, ...] = (
    # human_eval: whole tasks over v2 apps
    Step("human_eval_attachment", _in("observation_id", "he_observation"), covers=("observation_id",)),
    Step("human_eval_response", _in("assignment_id", "he_assignment"), covers=("assignment_id",)),
    Step("human_eval_httpexchange", _HE_ROWS_OF_WIPED_TASKS, covers=("assignment_id", "task_app_id")),
    Step("human_eval_findingverdict", _HE_ROWS_OF_WIPED_TASKS, covers=("assignment_id", "task_app_id")),
    # A kept task's verdict on a wiped finding keeps its finding_key and snapshot (model: SET_NULL).
    Step(
        "human_eval_findingverdict",
        f"{_in('finding_id', 'finding')} AND NOT ({_HE_ROWS_OF_WIPED_TASKS})",
        null=("finding_id",),
        covers=("finding_id",),
    ),
    Step("human_eval_observation", _in("id", "he_observation"), covers=("assignment_id", "task_app_id")),
    Step("human_eval_assignment", _in("id", "he_assignment"), covers=("task_id", "item_id", "participant_id")),
    Step("human_eval_participant", _in("id", "he_participant"), covers=("task_id", "invite_id")),
    Step("human_eval_evalinvite", _in("id", "he_invite"), covers=("task_id",)),
    Step("human_eval_evalitem", _in("id", "he_item"), covers=("task_id", "app_a_id", "app_b_id")),
    Step(
        "human_eval_evaltaskapp",
        f"{_in('container_id', 'container')} AND NOT {_in('id', 'he_app')}",
        null=("container_id",),
        covers=("container_id",),
    ),
    Step("human_eval_evaltaskapp", _in("id", "he_app"), covers=("task_id", "job_id")),
    Step("human_eval_evaltask", _in("id", "he_task"), covers=("experiment_id",)),
    # analysis results of v2 apps
    Step("analysis_finding", _in("id", "finding"), covers=("result_id",)),
    Step("analysis_toolresult", _in("id", "toolresult"), covers=("run_id",)),
    # before reports_report: a post-run references its report
    Step("generation_experimentpostrun"),
    Step("reports_report", _in("id", "report"), covers=("generation_job_id", "analysis_run_id")),
    Step("analysis_analysisrun", _in("id", "analysisrun"), covers=("generation_job_id",)),
    # runtime containers of v2 apps (execution_runtimebuildlease exists once the BuildKit WIP is in)
    Step("execution_runtimebuildlease", _in("action_id", "containeraction"), covers=("action_id",)),
    Step("runtime_containeraction", _in("id", "containeraction"), covers=("container_id",)),
    Step("runtime_portallocation", _in("container_id", "container"), covers=("container_id",)),
    Step(
        "analysis_analyzerworkspace",
        _in("container_id", "container"),
        null=("container_id",),
        covers=("container_id",),
    ),
    Step("runtime_containerinstance", _in("id", "container"), covers=("generation_job_id",)),
    # telemetry history is kept
    Step("inference_llmrequest", "job_id IS NOT NULL", null=("job_id",), covers=("job_id",)),
    # the v2 generation tables themselves
    Step("generation_copilotiteration"),
    Step("generation_generationartifact"),
    Step("generation_generationjob"),
    Step("generation_generationbatch"),
    Step(
        "generation_experiment",
        "baseline_condition_id IS NOT NULL OR created_by_schedule_id IS NOT NULL OR cloned_from_id IS NOT NULL",
        null=("baseline_condition_id", "created_by_schedule_id", "cloned_from_id"),
        internal=True,
    ),
    Step("generation_experimentschedule"),
    Step("generation_experimentcondition"),
    Step("generation_experiment_app_requirements"),
    Step("generation_experiment"),
    Step("generation_runpreset"),
    Step("generation_generationprofile"),
    Step("generation_contentblock"),
    Step("generation_apprequirementtemplate", "is_default = false", note="non-system rows"),
)

_TABLES_SQL = """
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'
"""

# pg_catalog rather than information_schema: constraint_column_usage only lists tables the
# current role owns, which would hide foreign keys from the coverage check.
_FOREIGN_KEYS_SQL = """
    SELECT src.relname, tgt.relname, array_agg(att.attname::text ORDER BY att.attnum)
    FROM pg_constraint con
    JOIN pg_class src ON src.oid = con.conrelid
    JOIN pg_class tgt ON tgt.oid = con.confrelid
    JOIN pg_namespace ns ON ns.oid = src.relnamespace
    JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey)
    WHERE con.contype = 'f' AND ns.nspname = current_schema()
    GROUP BY con.oid, src.relname, tgt.relname
"""


def docker_client() -> Any:
    """The apps-daemon docker client (``APPS_DOCKER_HOST``); patched in tests."""
    from backend.runtime.services.docker_manager import new_client

    return new_client()


def _is_not_found(exc: Exception) -> bool:
    try:
        from docker.errors import NotFound
    except ImportError:
        return False
    return isinstance(exc, NotFound)


def existing_tables(cursor: CursorWrapper) -> set[str]:
    cursor.execute(_TABLES_SQL)
    return {row[0] for row in cursor.fetchall()}


def uncovered_foreign_keys(cursor: CursorWrapper, tables: set[str]) -> list[str]:
    """Foreign keys into a table the wipe deletes from that no step clears, as ``src(cols) -> tgt``."""
    deleted = {step.table for step in STEPS if not step.null and step.table in tables}
    fully_deleted = {step.table for step in STEPS if not step.null and not step.where and step.table in tables}
    covered = {(step.table, column) for step in STEPS for column in step.covers}
    cursor.execute(_FOREIGN_KEYS_SQL)
    uncovered = []
    for source, target, columns in cursor.fetchall():
        if target not in deleted or source in fully_deleted:
            continue
        if not all((source, column) in covered for column in columns):
            uncovered.append(f"{source}({', '.join(columns)}) -> {target}")
    return sorted(uncovered)


class Command(BaseCommand):
    help = (
        "Delete all v2 sample-generation rows (and the analysis, report, runtime and human-evaluation rows that "
        "hang off them) before the v3 deploy. Dry run unless --yes is given."
    )

    def create_parser(self, prog_name: str, subcommand: str, **kwargs: Any) -> CommandParser:
        # "--yes" must be typed in full: no argparse prefix matching ("--y", "--ye").
        kwargs.setdefault("allow_abbrev", False)
        return super().create_parser(prog_name, subcommand, **kwargs)

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Really delete. Without it the command only prints what it would delete.",
        )
        parser.add_argument(
            "--skip-containers",
            action="store_true",
            help="Do not stop the docker containers of the wiped runtime_containerinstance rows.",
        )
        parser.add_argument(
            "--database",
            default=DEFAULT_DB_ALIAS,
            help="Database alias to wipe (default: %(default)s).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        alias: str = options["database"]
        apply: bool = options["yes"]
        connection = connections[alias]
        if connection.vendor != "postgresql":
            msg = f"wipe_generation_v2 supports PostgreSQL only (database {alias!r} is {connection.vendor})"
            raise CommandError(msg)

        with connection.cursor() as cursor:
            tables = existing_tables(cursor)
            cursor.execute("SELECT current_schema()")
            schema = cursor.fetchone()[0]
        if V2_MARKER_TABLE not in tables:
            self.stdout.write(
                f"No v2 generation tables in schema {schema!r} ({V2_MARKER_TABLE} is absent): nothing to wipe. "
                "This is expected on a v3 or a fresh database.",
            )
            return
        v3_tables = [table for table in V3_MARKER_TABLES if table in tables]
        if v3_tables:
            msg = (
                f"Schema {schema!r} has both v2 ({V2_MARKER_TABLE}) and v3 ({', '.join(v3_tables)}) generation "
                "tables; refusing to guess which rows are v2. Fix the migration state first."
            )
            raise CommandError(msg)
        with connection.cursor() as cursor:
            uncovered = uncovered_foreign_keys(cursor, tables)
            containers = self._job_containers(cursor, tables)
        if uncovered:
            msg = (
                "These foreign keys point into tables the wipe deletes from, and no wipe step clears them "
                f"(add a step to wipe_generation_v2.STEPS): {'; '.join(uncovered)}"
            )
            raise CommandError(msg)

        database = connection.settings_dict.get("NAME") or alias
        if apply:
            self.stdout.write(self.style.WARNING(f"Wiping the v2 generation data of database {database!r}."))
        else:
            self.stdout.write(
                f"Dry run on database {database!r}: nothing is changed. Re-run with the literal --yes to delete.",
            )
        self._containers(containers, apply=apply, skip=options["skip_containers"])

        with transaction.atomic(using=alias), connection.cursor() as cursor:
            if apply:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            self._create_scopes(cursor, tables)
            attachments = self._attachment_files(cursor, tables)
            counts = [(step, self._run_step(cursor, step, tables, apply=apply)) for step in STEPS]
            for scope in SCOPES:
                cursor.execute(f"DROP TABLE {_scope_table(scope.name)}")
            self._print_counts(counts, apply=apply)
            if apply:
                transaction.on_commit(lambda: self._after_commit(attachments), using=alias)
            else:
                self._print_files(attachments)
                transaction.set_rollback(True, using=alias)

    # -- database ---------------------------------------------------------------------------

    @staticmethod
    def _job_containers(cursor: CursorWrapper, tables: set[str]) -> list[tuple[str, str]]:
        if "runtime_containerinstance" not in tables:
            return []
        cursor.execute(
            "SELECT name, status FROM runtime_containerinstance WHERE generation_job_id IS NOT NULL ORDER BY name",
        )
        return [(name, status) for name, status in cursor.fetchall()]

    @staticmethod
    def _create_scopes(cursor: CursorWrapper, tables: set[str]) -> None:
        for scope in SCOPES:
            temp = _scope_table(scope.name)
            cursor.execute(f"DROP TABLE IF EXISTS {temp}")
            if scope.table in tables and all(table in tables for table in scope.reads):
                cursor.execute(
                    f"CREATE TEMP TABLE wipe_v2_{scope.name} ON COMMIT DROP AS "  # noqa: S608 — module constants only
                    f"SELECT id FROM {scope.table} WHERE {scope.where}",
                )
            else:
                cursor.execute(f"CREATE TEMP TABLE wipe_v2_{scope.name} (id uuid) ON COMMIT DROP")

    @staticmethod
    def _attachment_files(cursor: CursorWrapper, tables: set[str]) -> list[str]:
        if "human_eval_attachment" not in tables:
            return []
        cursor.execute(
            f"SELECT file FROM human_eval_attachment WHERE {_in('observation_id', 'he_observation')}",  # noqa: S608
        )
        return [name for (name,) in cursor.fetchall() if name]

    @staticmethod
    def _run_step(cursor: CursorWrapper, step: Step, tables: set[str], *, apply: bool) -> int | None:
        """Rows the step deletes or nulls (counted on a dry run); None when its table is absent."""
        if step.table not in tables:
            return None
        where = step.where or "TRUE"
        # Every identifier and predicate below comes from the module constants above.
        if not apply:
            if step.internal:
                return 0
            cursor.execute(f"SELECT COUNT(*) FROM {step.table} WHERE {where}")  # noqa: S608
            return cursor.fetchone()[0]
        if step.null:
            assignments = ", ".join(f"{column} = NULL" for column in step.null)
            cursor.execute(f"UPDATE {step.table} SET {assignments} WHERE {where}")  # noqa: S608
        else:
            cursor.execute(f"DELETE FROM {step.table} WHERE {where}")  # noqa: S608
        return cursor.rowcount

    # -- side effects outside the database ---------------------------------------------------

    def _containers(self, containers: list[tuple[str, str]], *, apply: bool, skip: bool) -> None:
        if not containers:
            self.stdout.write("Containers of generation jobs (runtime_containerinstance): none.")
            return
        if not apply or skip:
            reason = "skipped (--skip-containers)" if apply else "would stop"
            self.stdout.write(f"Containers of generation jobs (runtime_containerinstance), {reason}:")
            for name, status in containers:
                self.stdout.write(f"  {name} (db status {status})")
            return
        self.stdout.write(f"Stopping {len(containers)} container(s) of generation jobs (best effort):")
        for (name, status), outcome in zip(containers, self._stop([n for n, _ in containers]), strict=True):
            self.stdout.write(f"  {name} (db status {status}): {outcome}")

    @staticmethod
    def _stop(names: list[str]) -> list[str]:
        """Stop each container by name; never raises (the wipe must not depend on docker)."""
        try:
            client = docker_client()
        except Exception as exc:  # noqa: BLE001 — best effort: docker trouble never blocks the wipe
            return [f"skipped, docker unavailable ({exc})"] * len(names)
        outcomes = []
        try:
            for name in names:
                try:
                    client.containers.get(name).stop(timeout=DOCKER_STOP_TIMEOUT_S)
                except Exception as exc:  # noqa: BLE001
                    outcomes.append("not found" if _is_not_found(exc) else f"error ({exc})")
                else:
                    outcomes.append("stopped")
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):  # closing the client is cosmetic
                    close()
        return outcomes

    def _after_commit(self, attachments: list[str]) -> None:
        self.stdout.write(self.style.SUCCESS("Committed."))
        root = self._runs_root()
        if root is not None:
            removed, errors = self._clear_runs_root(root)
            self.stdout.write(f"Run files: deleted {removed} entr{'y' if removed == 1 else 'ies'} under {root}.")
            for error in errors:
                self.stderr.write(f"  could not delete {error}")
        deleted = 0
        for name in attachments:
            try:
                default_storage.delete(name)
            except OSError as exc:
                self.stderr.write(f"  could not delete attachment {name}: {exc}")
            else:
                deleted += 1
        self.stdout.write(f"Human-evaluation attachment files: deleted {deleted} of {len(attachments)}.")

    def _print_files(self, attachments: list[str]) -> None:
        root = self._runs_root()
        if root is not None:
            try:
                entries = sum(1 for _ in root.iterdir()) if root.is_dir() else 0
            except OSError as exc:
                self.stderr.write(f"Run files: cannot list {root}: {exc}")
            else:
                self.stdout.write(
                    f"Run files: --yes would delete {entries} entr{'y' if entries == 1 else 'ies'} under {root}.",
                )
        self.stdout.write(f"Human-evaluation attachment files: --yes would delete {len(attachments)}.")

    def _runs_root(self) -> Path | None:
        configured = getattr(settings, "GENERATION_RUNS_ROOT", "")
        if not configured:
            self.stdout.write("Run files: GENERATION_RUNS_ROOT is not set; nothing to delete.")
            return None
        root = Path(configured)
        if root == Path(root.anchor):
            self.stderr.write(f"Run files: refusing to clear GENERATION_RUNS_ROOT={root} (a filesystem root).")
            return None
        return root

    @staticmethod
    def _clear_runs_root(root: Path) -> tuple[int, list[str]]:
        """Delete everything inside ``root`` (the directory itself stays)."""
        if not root.is_dir():
            return 0, []
        try:
            children = sorted(root.iterdir())
        except OSError as exc:
            return 0, [f"{root}: {exc}"]
        removed, errors = 0, []
        for child in children:
            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError as exc:
                errors.append(f"{child}: {exc}")
            else:
                removed += 1
        return removed, errors

    # -- output -----------------------------------------------------------------------------

    def _print_counts(self, counts: list[tuple[Step, int | None]], *, apply: bool) -> None:
        self.stdout.write("Deleted in one transaction:" if apply else "--yes would change these rows:")
        self.stdout.write(f"  {'table':<52} {'action':<8} {'rows':>8}")
        deleted = nulled = 0
        for step, count in counts:
            if step.internal:
                continue
            if count is None:
                self.stdout.write(f"  {step.label:<52} {step.action:<8} {'-':>8}  (table absent)")
                continue
            note = f"  ({step.note})" if step.note else ""
            self.stdout.write(f"  {step.label:<52} {step.action:<8} {count:>8}{note}")
            if step.null:
                nulled += count
            else:
                deleted += count
        verb = "deleted" if apply else "to delete"
        self.stdout.write(f"Total: {deleted} rows {verb}, {nulled} references set NULL.")
