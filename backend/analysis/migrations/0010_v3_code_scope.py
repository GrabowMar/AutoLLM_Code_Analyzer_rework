"""v3 (spec sections 2.3 and 8.8): record which file set of a generation run was analysed."""

from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("analysis", "0009_run_batch_tool_timing_tool_severity"),
    ]

    operations = [
        migrations.AddField(
            model_name="analysisrun",
            name="code_scope",
            field=models.CharField(
                blank=True,
                choices=[
                    ("app", "App (whole non-artifact tree)"),
                    ("model", "Model-written files"),
                    ("slot", "Slot replacement"),
                ],
                default="",
                max_length=8,
                verbose_name="code scope",
            ),
        ),
        migrations.AddField(
            model_name="analysisrun",
            name="code_scope_paths_sha",
            field=models.CharField(blank=True, default="", max_length=64, verbose_name="code scope paths sha"),
        ),
    ]
