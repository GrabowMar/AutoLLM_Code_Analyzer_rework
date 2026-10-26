from django.db import migrations, models


def backfill_catalog_version(apps, schema_editor):
    # Existing installs predate version tracking; assume they match the
    # current catalog so they don't all show up as needing an update.
    InstalledTool = apps.get_model("analysis", "InstalledTool")
    for it in InstalledTool.objects.select_related("tool").filter(status="installed"):
        it.catalog_version = it.tool.version or ""
        it.save(update_fields=["catalog_version"])


class Migration(migrations.Migration):

    dependencies = [
        ("analysis", "0004_analyzertool_output_kind_toolresult_metrics"),
    ]

    operations = [
        migrations.AddField(
            model_name="analyzertool",
            name="uninstall_cmd",
            field=models.TextField(blank=True, default="", help_text="Best-effort removal of the package from a running workspace.", verbose_name="uninstall command"),
        ),
        migrations.AddField(
            model_name="installedtool",
            name="catalog_version",
            field=models.CharField(blank=True, default="", help_text="The catalog tool version this install was made from.", max_length=50, verbose_name="catalog version"),
        ),
        migrations.AlterField(
            model_name="installedtool",
            name="status",
            field=models.CharField(choices=[("installing", "Installing"), ("updating", "Updating"), ("installed", "Installed"), ("failed", "Failed")], default="installing", max_length=20, verbose_name="status"),
        ),
        migrations.AlterField(
            model_name="analyzertool",
            name="category",
            field=models.CharField(choices=[("security", "Security"), ("lint", "Lint / quality"), ("performance", "Performance"), ("secrets", "Secrets"), ("dependencies", "Dependencies"), ("ai", "AI review")], db_index=True, default="lint", max_length=20, verbose_name="category"),
        ),
        migrations.AlterField(
            model_name="analyzertool",
            name="target_language",
            field=models.CharField(choices=[("python", "Python"), ("javascript", "JavaScript / TypeScript"), ("html", "HTML"), ("css", "CSS"), ("shell", "Shell"), ("any", "Any")], default="any", max_length=20, verbose_name="target language"),
        ),
        migrations.RunPython(backfill_catalog_version, migrations.RunPython.noop),
    ]
