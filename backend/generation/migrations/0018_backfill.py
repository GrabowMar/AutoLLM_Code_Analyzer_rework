"""Backfill the execution columns added in 0017 (spec §B.3).

1. ``GenerationBatch.experiment`` from the most common non-null
   ``jobs.experiment_id``; failing that, ``request["experiment_id"]`` when that
   experiment exists. Linked batches get ``kind="launch"``.
2. ``lane="sandbox"`` for agent jobs; ``endpoint`` stays NULL (every existing
   model is OpenRouter).
3. Legacy PENDING/RUNNING rows (``queued_at IS NULL``) are left alone: the
   executor ignores them and the UI offers "Requeue legacy jobs".

The reverse is a no-op (0017's reverse drops the columns).
"""

from collections import Counter

from django.db import migrations


def backfill(apps, schema_editor):
    alias = schema_editor.connection.alias
    Batch = apps.get_model("generation", "GenerationBatch")
    Job = apps.get_model("generation", "GenerationJob")
    Experiment = apps.get_model("generation", "Experiment")

    counts: dict = {}
    for batch_id, experiment_id in (
        Job.objects.using(alias)
        .filter(batch__isnull=False, experiment__isnull=False)
        .values_list("batch_id", "experiment_id")
        .iterator()
    ):
        counts.setdefault(batch_id, Counter())[experiment_id] += 1

    existing = {str(pk) for pk in Experiment.objects.using(alias).values_list("pk", flat=True)}
    for batch in Batch.objects.using(alias).filter(experiment__isnull=True).only("id", "request").iterator():
        experiment_id = None
        if batch.id in counts:
            experiment_id = counts[batch.id].most_common(1)[0][0]
        else:
            requested = (batch.request or {}).get("experiment_id") if isinstance(batch.request, dict) else None
            if requested and str(requested) in existing:
                experiment_id = requested
        if experiment_id is not None:
            Batch.objects.using(alias).filter(pk=batch.pk).update(experiment_id=experiment_id, kind="launch")

    Job.objects.using(alias).filter(mode="agent").update(lane="sandbox")


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0017_experiments_execution"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
