"""v3 (spec sections 2.2 and 3.12): delete ``runtime.Stack``; stacks are ``generation.StackProfile`` now.

Depends on ``generation 0024_v3_models`` (the cut-over removes every v2 generation reference to stacks
first) and on the BuildKit WIP's ``0005_sample_build_artifact`` (section 12.4).
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("runtime", "0005_sample_build_artifact"),
        ("generation", "0024_v3_models"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="stack",
            name="runtime_stack_slug_version_uniq",
        ),
        migrations.DeleteModel(
            name="Stack",
        ),
    ]
