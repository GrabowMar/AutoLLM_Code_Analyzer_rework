# MSS v2: raw source snapshots, selection snapshots, BenchmarkResult removed (it held 0 rows).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("rankings", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.DeleteModel(name="BenchmarkResult"),
        migrations.CreateModel(
            name="SourceSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("openrouter_rankings_daily", "OpenRouter rankings-daily"),
                            ("openrouter_task_classifications", "OpenRouter task classifications"),
                            ("openrouter_models_index", "OpenRouter models index"),
                            ("openrouter_popularity_market", "Derived popularity market (per day, per model)"),
                        ],
                        db_index=True,
                        max_length=64,
                    ),
                ),
                ("fetched_at", models.DateTimeField(db_index=True)),
                ("as_of", models.CharField(blank=True, default="", max_length=64)),
                ("window_start", models.DateField(blank=True, null=True)),
                ("window_end", models.DateField(blank=True, null=True)),
                ("params", models.JSONField(blank=True, default=dict)),
                ("payload", models.JSONField()),
                ("sha256", models.CharField(max_length=64)),
                ("license", models.CharField(blank=True, default="", max_length=64)),
                ("citation", models.CharField(blank=True, default="", max_length=300)),
            ],
            options={
                "ordering": ["-fetched_at", "-pk"],
                "get_latest_by": "fetched_at",
            },
        ),
        migrations.AlterModelOptions(
            name="rankingsnapshot",
            options={"get_latest_by": "created_at", "ordering": ["-created_at", "-pk"]},
        ),
        migrations.AddField(
            model_name="rankingsnapshot",
            name="kind",
            field=models.CharField(
                choices=[("rankings", "Rankings"), ("selection", "Study selection")],
                db_index=True,
                default="rankings",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="rankingsnapshot",
            name="params",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="rankingsnapshot",
            name="inputs_sha256",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="rankingsnapshot",
            name="status",
            field=models.CharField(
                choices=[("draft", "Draft"), ("confirmed", "Confirmed"), ("superseded", "Superseded")],
                db_index=True,
                default="draft",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="rankingsnapshot",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="rankingsnapshot",
            name="confirmed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="rankingsnapshot",
            name="confirmed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="rankingsnapshot",
            name="confirmation",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="rankingsnapshot",
            name="code_version",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
