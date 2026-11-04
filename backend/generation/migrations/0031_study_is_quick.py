"""``Study.is_quick``: single apps launched from the "Generate app" page (hidden from the list by default)."""

from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0030_e2e_study_parent_draw"),
    ]

    operations = [
        migrations.AddField(
            model_name="study",
            name="is_quick",
            field=models.BooleanField(default=False, verbose_name="quick generation"),
        ),
    ]
