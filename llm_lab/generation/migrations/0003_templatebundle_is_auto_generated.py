from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0002_content_blocks_and_bundles"),
    ]

    operations = [
        migrations.AddField(
            model_name="templatebundle",
            name="is_auto_generated",
            field=models.BooleanField(
                default=False,
                help_text="Set by the seeder for clone bundles; hidden from the UI picker.",
                verbose_name="auto-generated",
            ),
        ),
    ]
