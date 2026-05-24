"""Add profile FK and threshold_status to AnalysisTask."""

from django.db import migrations
from django.db import models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("analysis", "0004_finding_suppression"),
    ]

    operations = [
        migrations.AddField(
            model_name="analysistask",
            name="profile",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tasks",
                to="analysis.analysisprofile",
            ),
        ),
        migrations.AddField(
            model_name="analysistask",
            name="threshold_status",
            field=models.CharField(
                choices=[
                    ("not_configured", "Not configured"),
                    ("passed", "Passed"),
                    ("exceeded", "Exceeded"),
                ],
                default="not_configured",
                max_length=20,
            ),
        ),
    ]
