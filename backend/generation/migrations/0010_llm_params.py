"""Add layered LLM params: job.llm_params + experiment.llm_defaults.

The experiment temperature/max_tokens/top_p columns are folded into
``llm_defaults`` (backfilled) and dropped — ``services/llm_params.py`` is now
the single vocabulary for sampling params.
"""

from django.db import migrations
from django.db import models


def backfill_llm_defaults(apps, schema_editor):
    Experiment = apps.get_model("generation", "Experiment")
    for experiment in Experiment.objects.all().iterator():
        defaults = {
            "temperature": experiment.temperature,
            "max_tokens": experiment.max_tokens,
        }
        if experiment.top_p is not None:
            defaults["top_p"] = experiment.top_p
        experiment.llm_defaults = defaults
        experiment.save(update_fields=["llm_defaults"])


def restore_param_columns(apps, schema_editor):
    Experiment = apps.get_model("generation", "Experiment")
    for experiment in Experiment.objects.all().iterator():
        defaults = experiment.llm_defaults or {}
        experiment.temperature = defaults.get("temperature", 0.3)
        experiment.max_tokens = defaults.get("max_tokens", 32000)
        experiment.top_p = defaults.get("top_p")
        experiment.save(update_fields=["temperature", "max_tokens", "top_p"])


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0009_alter_generationprofile_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="generationjob",
            name="llm_params",
            field=models.JSONField(blank=True, default=dict, verbose_name="LLM param overrides"),
        ),
        migrations.AddField(
            model_name="experiment",
            name="llm_defaults",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text=(
                    "Sampling params applied to every condition unless overridden (see llm_params.SAMPLING_KEYS)"
                ),
                verbose_name="LLM defaults",
            ),
        ),
        migrations.RunPython(backfill_llm_defaults, restore_param_columns),
        migrations.RemoveField(model_name="experiment", name="temperature"),
        migrations.RemoveField(model_name="experiment", name="max_tokens"),
        migrations.RemoveField(model_name="experiment", name="top_p"),
    ]
