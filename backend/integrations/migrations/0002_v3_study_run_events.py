"""v3 (SAMPLE_GEN_V3 §2.3): webhook events follow GenerationRun / Study.

``include_experiment_jobs`` becomes ``include_study_runs`` and stored event names are rewritten
(``generation.*`` -> ``run.*``, ``experiment.*`` -> ``study.*``). Delivered rows keep their history.
"""

from django.db import migrations
from django.db import models

RENAMES = {
    "generation.completed": "run.completed",
    "generation.failed": "run.failed",
    "generation.cancelled": "run.cancelled",
    "experiment.completed": "study.completed",
    "experiment.cancelled": "study.cancelled",
}


def _rewrite(apps, schema_editor, mapping):
    subscription = apps.get_model("integrations", "WebhookSubscription")
    for sub in subscription.objects.exclude(events=[]).only("pk", "events"):
        events = list(dict.fromkeys(mapping.get(e, e) for e in sub.events or []))
        if events != sub.events:
            subscription.objects.filter(pk=sub.pk).update(events=events)


def forwards(apps, schema_editor):
    _rewrite(apps, schema_editor, RENAMES)


def backwards(apps, schema_editor):
    reverse = {new: old for old, new in RENAMES.items()}
    reverse["study.stopped"] = "experiment.cancelled"
    _rewrite(apps, schema_editor, reverse)


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="webhooksubscription",
            old_name="include_experiment_jobs",
            new_name="include_study_runs",
        ),
        migrations.AlterField(
            model_name="webhooksubscription",
            name="include_study_runs",
            field=models.BooleanField(
                default=False,
                help_text="Also send run.* events for the individual runs of a study.",
                verbose_name="include study runs",
            ),
        ),
        migrations.RunPython(forwards, backwards),
    ]
