# Generated manually for Phase B

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("generation", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="apprequirementtemplate",
            name="admin_api_endpoints",
            field=models.JSONField(
                blank=True,
                default=list,
                verbose_name="admin API endpoints",
            ),
        ),
        migrations.CreateModel(
            name="ContentBlock",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "block_type",
                    models.CharField(
                        choices=[
                            ("requirement", "Requirement"),
                            ("api_schema", "API schema"),
                            ("prompt_tone", "Prompt tone"),
                            ("prompt_rules", "Prompt rules"),
                            ("scaffold_hint", "Scaffold hint"),
                            ("validation", "Validation"),
                            ("eval_rubric", "Eval rubric"),
                            ("prompt_stage", "Prompt stage"),
                        ],
                        max_length=30,
                        verbose_name="block type",
                    ),
                ),
                ("slug", models.SlugField(max_length=200, verbose_name="slug")),
                ("version", models.PositiveIntegerField(default=1, verbose_name="version")),
                ("name", models.CharField(max_length=200, verbose_name="name")),
                (
                    "description",
                    models.TextField(blank=True, default="", verbose_name="description"),
                ),
                ("content", models.TextField(verbose_name="content")),
                (
                    "metadata",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text='e.g. {"stage": "backend", "role": "system"} for prompt_stage blocks',
                        verbose_name="metadata",
                    ),
                ),
                (
                    "is_system",
                    models.BooleanField(
                        default=False,
                        help_text="Shipped defaults; readable by all users",
                        verbose_name="system block",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="content_blocks",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Content Block",
                "verbose_name_plural": "Content Blocks",
                "ordering": ["block_type", "slug", "-version"],
            },
        ),
        migrations.CreateModel(
            name="TemplateBundle",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=200, verbose_name="name")),
                ("slug", models.SlugField(max_length=200, unique=True, verbose_name="slug")),
                (
                    "description",
                    models.TextField(blank=True, default="", verbose_name="description"),
                ),
                (
                    "scaffolding_slug",
                    models.SlugField(
                        default="flask-react",
                        help_text="Canonical stack slug from runtime/scaffolding/manifest.json",
                        max_length=200,
                        verbose_name="scaffolding slug",
                    ),
                ),
                (
                    "block_refs",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text='List of {"type", "slug", "version"} objects',
                        verbose_name="block references",
                    ),
                ),
                (
                    "llm_config",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        verbose_name="LLM config defaults",
                    ),
                ),
                (
                    "is_system",
                    models.BooleanField(default=False, verbose_name="system bundle"),
                ),
                (
                    "is_default",
                    models.BooleanField(default=False, verbose_name="default bundle"),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="template_bundles",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Template Bundle",
                "verbose_name_plural": "Template Bundles",
                "ordering": ["-is_default", "-is_system", "name"],
            },
        ),
        migrations.AddConstraint(
            model_name="contentblock",
            constraint=models.UniqueConstraint(
                fields=("slug", "version"),
                name="generation_contentblock_slug_version_uniq",
            ),
        ),
        migrations.AddField(
            model_name="generationjob",
            name="experiment_seed",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                verbose_name="experiment seed",
            ),
        ),
        migrations.AddField(
            model_name="generationjob",
            name="resolved_bundle",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Immutable snapshot of blocks, prompts, and app spec at job creation",
                verbose_name="resolved bundle",
            ),
        ),
        migrations.AddField(
            model_name="generationjob",
            name="template_bundle",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="jobs",
                to="generation.templatebundle",
            ),
        ),
    ]
