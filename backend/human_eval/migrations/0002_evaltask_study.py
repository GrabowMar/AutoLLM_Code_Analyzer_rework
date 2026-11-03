"""v3: ``EvalTask.experiment`` (FK to ``generation.Study``) is renamed ``study`` (column ``study_id``)."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0024_v3_models"),
        ("human_eval", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="evaltask",
            old_name="experiment",
            new_name="study",
        ),
    ]
