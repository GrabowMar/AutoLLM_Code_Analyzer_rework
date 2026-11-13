"""v3 cut-over guard (spec section 3.12): refuse to migrate a database that still holds v2 generation data.

The v2 rows are wiped on the v2 deployment, before v3 is deployed, by ``manage.py wipe_generation_v2
--yes`` (WP-00, raw SQL against the v2 schema). With that order this step never fires; it exists so that a
mistake fails loudly instead of migrating half-populated data into the v3 tables. On an empty or fresh
database it is a no-op.

Seed rows are not research data and are not counted: system requirement templates (``is_default``), and
system content blocks, profiles and run presets (``is_system``) are re-created by the v2 post-migrate
seeding whenever the v2 web container restarts between the wipe and the v3 deploy, and
``0024_v3_models`` deletes those tables anyway.
"""

from django.db import migrations

# (table, extra WHERE clause excluding seed rows)
V2_TABLES = (
    ("generation_generationjob", ""),
    ("generation_generationbatch", ""),
    ("generation_experiment", ""),
    ("generation_experiment_app_requirements", ""),
    ("generation_experimentcondition", ""),
    ("generation_experimentschedule", ""),
    ("generation_experimentpostrun", ""),
    ("generation_generationartifact", ""),
    ("generation_copilotiteration", ""),
    ("generation_apprequirementtemplate", "is_default = false"),
    ("generation_contentblock", "is_system = false"),
    ("generation_generationprofile", "is_system = false"),
    ("generation_runpreset", "is_system = false"),
)

MESSAGE = (
    "generation v2 data present: run `manage.py wipe_generation_v2 --yes` on the v2 deployment first "
    "(docs/design/V3_CUTOVER.md). Non-empty: {tables}"
)


def v2_row_counts(connection) -> dict[str, int]:
    """Rows that are not seed data, per v2 table that exists and is non-empty."""
    existing = set(connection.introspection.table_names())
    counts = {}
    with connection.cursor() as cursor:
        for table, where in V2_TABLES:
            if table not in existing:
                continue
            sql = f"SELECT COUNT(*) FROM {connection.ops.quote_name(table)}"  # noqa: S608 - fixed table names
            if where:
                sql += f" WHERE {where}"
            cursor.execute(sql)
            (count,) = cursor.fetchone()
            if count:
                counts[table] = count
    return counts


def refuse_v2_data(apps, schema_editor):
    counts = v2_row_counts(schema_editor.connection)
    if counts:
        tables = ", ".join(f"{table} ({count})" for table, count in sorted(counts.items()))
        raise RuntimeError(MESSAGE.format(tables=tables))


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0022_artifact_telemetry"),
    ]

    operations = [
        migrations.RunPython(refuse_v2_data, reverse_code=migrations.RunPython.noop, elidable=False),
    ]
