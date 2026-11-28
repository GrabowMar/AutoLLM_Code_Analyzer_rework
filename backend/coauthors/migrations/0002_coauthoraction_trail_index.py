from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("coauthors", "0001_initial"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="coauthoraction",
            index=models.Index(fields=["coauthorship", "-id"], name="coauthors_action_trail_idx"),
        ),
    ]
