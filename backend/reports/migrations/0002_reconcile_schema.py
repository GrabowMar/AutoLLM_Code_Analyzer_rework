"""Reconcile schema drift: the original 0001_initial was edited after being applied,
so the live DB is missing the ``expires_at`` and ``generation_job_id`` columns that
``reports.Report`` (and the migration state) already declare.

This is a database-only migration: model state already reflects the desired schema,
so we use ``RunSQL`` (no state operations) and gate every statement on the column
not already existing, making the migration safe to re-apply on a freshly built DB.
"""

from __future__ import annotations

from django.db import migrations

ADD_EXPIRES_AT = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'reports_report' AND column_name = 'expires_at'
    ) THEN
        ALTER TABLE reports_report
            ADD COLUMN expires_at timestamp with time zone NULL;
    END IF;
END$$;
"""

ADD_GENERATION_JOB = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'reports_report' AND column_name = 'generation_job_id'
    ) THEN
        ALTER TABLE reports_report
            ADD COLUMN generation_job_id uuid NULL
            REFERENCES generation_generationjob(id)
            DEFERRABLE INITIALLY DEFERRED;
        CREATE INDEX reports_report_generation_job_id_idx
            ON reports_report (generation_job_id);
    END IF;
END$$;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0001_initial"),
        ("generation", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(ADD_EXPIRES_AT, reverse_sql=migrations.RunSQL.noop),
        migrations.RunSQL(ADD_GENERATION_JOB, reverse_sql=migrations.RunSQL.noop),
    ]
