"""Rename TemplateBundle → GenerationProfile and the FKs that point at it.

Deliberately NOT renamed: ``GenerationJob.resolved_bundle``,
``GenerationJob.prompt_hash``, and ``GenerationJob.bundle_key``. Those columns
(and the JSON keys inside resolved_bundle) are frozen provenance shared with
every historical job row, and the statistics app slices by them.
"""

from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0007_remove_generationjob_backend_prompt_template_and_more"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="templatebundle",
            name="generation_templatebundle_slug_version_uniq",
        ),
        migrations.RemoveConstraint(
            model_name="experimentcondition",
            name="generation_experimentcondition_uniq",
        ),
        migrations.RenameModel(
            old_name="TemplateBundle",
            new_name="GenerationProfile",
        ),
        migrations.RenameField(
            model_name="generationjob",
            old_name="template_bundle",
            new_name="profile",
        ),
        migrations.RenameField(
            model_name="experimentcondition",
            old_name="template_bundle",
            new_name="profile",
        ),
        migrations.AddConstraint(
            model_name="generationprofile",
            constraint=models.UniqueConstraint(
                fields=["slug", "version"],
                name="generation_generationprofile_slug_version_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="experimentcondition",
            constraint=models.UniqueConstraint(
                fields=["experiment", "profile", "model"],
                name="generation_experimentcondition_uniq",
            ),
        ),
    ]
