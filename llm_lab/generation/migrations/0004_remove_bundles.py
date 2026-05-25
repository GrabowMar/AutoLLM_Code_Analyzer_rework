from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0003_templatebundle_is_auto_generated"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="generationjob",
            name="template_bundle",
        ),
        migrations.DeleteModel(
            name="TemplateBundle",
        ),
        migrations.DeleteModel(
            name="ContentBlock",
        ),
    ]
