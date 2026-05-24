"""Add suppression fields to Finding."""

from django.conf import settings
from django.db import migrations
from django.db import models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("analysis", "0003_rename_analyzerconfig_to_analysisprofile"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="finding",
            name="suppressed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="finding",
            name="suppression_reason",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="finding",
            name="suppressed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="suppressed_findings",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddIndex(
            model_name="finding",
            index=models.Index(fields=["suppressed"], name="analysis_fi_suppres_idx"),
        ),
        migrations.AddIndex(
            model_name="finding",
            index=models.Index(
                fields=["result", "suppressed"],
                name="analysis_fi_result_suppres_idx",
            ),
        ),
    ]
