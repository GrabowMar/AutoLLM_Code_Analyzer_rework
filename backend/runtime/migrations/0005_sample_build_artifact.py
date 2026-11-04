import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("runtime", "0004_stack_backend_base_image_stack_frontend_base_image_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SampleBuildArtifact",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("build_key", models.CharField(db_index=True, max_length=64)),
                ("image_tag", models.CharField(max_length=250, unique=True)),
                ("image_id", models.CharField(max_length=100)),
                ("image_bytes", models.PositiveBigIntegerField(default=0)),
                ("platform", models.CharField(max_length=80)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("built_at", models.DateTimeField()),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("last_used_at", models.DateTimeField(db_index=True)),
                ("reserved_until", models.DateTimeField(db_index=True)),
                (
                    "owner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="sample_build_artifacts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-built_at"]},
        ),
    ]
