"""Wave 3 (WP-01 follow-up, review round 1 MINOR): ``RunFile.mode`` holds permission bits only."""

from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0027_wave2_plan_hash_per_study"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="runfile",
            constraint=models.CheckConstraint(
                condition=models.Q(("mode__gte", 0), ("mode__lte", 4095)),
                name="generation_runfile_mode_permission_bits",
            ),
        ),
    ]
