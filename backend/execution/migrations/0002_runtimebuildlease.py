import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("execution", "0001_initial"),
        ("runtime", "0004_stack_backend_base_image_stack_frontend_base_image_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="RuntimeBuildLease",
            fields=[
                (
                    "action",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        serialize=False,
                        to="runtime.containeraction",
                    ),
                ),
                ("owner", models.CharField(db_index=True, max_length=80)),
                ("expires_at", models.DateTimeField(db_index=True)),
            ],
        ),
    ]
