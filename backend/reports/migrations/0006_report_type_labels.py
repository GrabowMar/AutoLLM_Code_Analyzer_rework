# Choices-only change: v3 labels for the task comparison and study conditions report types.
# The stored values (``template_comparison``, ``experiment_report``) are unchanged.

from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0005_study_report"),
    ]

    operations = [
        migrations.AlterField(
            model_name="report",
            name="report_type",
            field=models.CharField(
                choices=[
                    ("model_analysis", "Model analysis"),
                    ("template_comparison", "Task comparison"),
                    ("tool_analysis", "Tool analysis"),
                    ("generation_analytics", "Generation analytics"),
                    ("comprehensive", "Comprehensive"),
                    ("experiment_report", "Study conditions"),
                    ("study_report", "Study report"),
                ],
                max_length=40,
                verbose_name="type",
            ),
        ),
    ]
