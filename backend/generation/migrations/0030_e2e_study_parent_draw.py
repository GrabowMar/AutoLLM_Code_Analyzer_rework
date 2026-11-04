"""E2E-14: ``Study.parent_draw`` pins the parents a ``parent_selection`` draw picked at the first freeze."""

from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0029_e2e_list_estimate"),
    ]

    operations = [
        migrations.AddField(
            model_name="study",
            name="parent_draw",
            field=models.JSONField(blank=True, default=dict, verbose_name="pinned parent draw"),
        ),
    ]
