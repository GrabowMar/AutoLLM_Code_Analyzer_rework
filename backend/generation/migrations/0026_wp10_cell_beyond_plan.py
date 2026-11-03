# WP-10: replicate cells added after launch are marked beyond the plan. Additive; an integrator renumbers it.

from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0025_wp10_study_cap"),
    ]

    operations = [
        migrations.AddField(
            model_name="studycell",
            name="beyond_plan",
            field=models.BooleanField(
                default=False,
                help_text="A replicate added after launch (add_replicates): not part of the planned sample",
                verbose_name="beyond plan",
            ),
        ),
    ]
