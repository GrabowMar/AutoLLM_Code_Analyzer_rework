from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("analysis", "0008_analysisrun_heartbeat_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="analysisrun",
            name="batch_id",
            field=models.UUIDField(blank=True, db_index=True, null=True, verbose_name="batch"),
        ),
        migrations.AddField(
            model_name="toolresult",
            name="started_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="started at"),
        ),
        migrations.AddField(
            model_name="toolresult",
            name="duration_seconds",
            field=models.FloatField(blank=True, null=True, verbose_name="duration (s)"),
        ),
        migrations.AddField(
            model_name="finding",
            name="tool_severity",
            field=models.CharField(
                blank=True,
                default="",
                help_text="The severity/level exactly as the tool printed it; empty when the tool rates nothing.",
                max_length=40,
                verbose_name="tool severity",
            ),
        ),
        migrations.AlterField(
            model_name="finding",
            name="severity",
            field=models.CharField(
                choices=[
                    ("critical", "Critical"),
                    ("high", "High"),
                    ("medium", "Medium"),
                    ("low", "Low"),
                    ("info", "Info"),
                ],
                db_index=True,
                default="info",
                help_text=(
                    "Normalized level for aggregate statistics; a heuristic for tools that rate nothing "
                    "(see tool_severity)."
                ),
                max_length=20,
                verbose_name="severity",
            ),
        ),
    ]
