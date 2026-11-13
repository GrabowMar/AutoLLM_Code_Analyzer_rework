"""v3 (WP-02b): ``AnalysisRun.batch_id`` is renamed ``launch_id`` (runs launched together).

A real column rename (clean break): the v2 column name ``batch_id`` does not survive.
"""

from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("analysis", "0010_v3_code_scope"),
    ]

    operations = [
        migrations.RenameField(model_name="analysisrun", old_name="batch_id", new_name="launch_id"),
        migrations.AlterField(
            model_name="analysisrun",
            name="launch_id",
            field=models.UUIDField(blank=True, db_index=True, null=True, verbose_name="launch"),
        ),
    ]
