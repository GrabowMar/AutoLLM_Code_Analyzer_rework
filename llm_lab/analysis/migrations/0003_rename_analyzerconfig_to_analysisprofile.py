"""Rename AnalyzerConfig to AnalysisProfile and rework its fields."""

from django.conf import settings
from django.db import migrations
from django.db import models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("analysis", "0002_add_query_indexes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameModel(
            old_name="AnalyzerConfig",
            new_name="AnalysisProfile",
        ),
        migrations.RemoveField(
            model_name="analysisprofile",
            name="analyzer_name",
        ),
        migrations.RemoveField(
            model_name="analysisprofile",
            name="enabled",
        ),
        migrations.RemoveField(
            model_name="analysisprofile",
            name="default_settings",
        ),
        migrations.AddField(
            model_name="analysisprofile",
            name="analyzers",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="List of analyzer names to run, e.g. ['bandit', 'eslint']",
            ),
        ),
        migrations.AddField(
            model_name="analysisprofile",
            name="settings",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Per-analyzer config overrides: {analyzer_name: {key: value}}",
            ),
        ),
        migrations.AddField(
            model_name="analysisprofile",
            name="is_default",
            field=models.BooleanField(
                default=False,
                help_text="Whether this is the user's default profile",
            ),
        ),
        migrations.AddField(
            model_name="analysisprofile",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="analysis_profiles",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterUniqueTogether(
            name="analysisprofile",
            unique_together={("created_by", "name")},
        ),
        migrations.AlterModelOptions(
            name="analysisprofile",
            options={"ordering": ["-is_default", "name"]},
        ),
    ]
