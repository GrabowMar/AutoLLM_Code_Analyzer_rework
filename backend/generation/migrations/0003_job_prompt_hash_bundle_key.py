"""Add denormalized slicing keys and backfill them from resolved_bundle snapshots.

prompt_hash is recomputed here rather than copied: the original definition
included the per-job random seed and llm config, which made every job's hash
unique and useless for grouping. The new definition (prompt templates + app
requirement + block versions) is derivable from the stored snapshot, so old
and new jobs end up on one consistent scheme.
"""

import hashlib
import json

from django.db import migrations
from django.db import models


def _prompt_hash(snapshot: dict) -> str:
    payload = {
        "prompt_templates": snapshot.get("prompt_templates"),
        "app_requirement": snapshot.get("app_requirement"),
        "blocks": [{"slug": b["slug"], "version": b["version"]} for b in snapshot.get("blocks", [])],
    }
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


def _bundle_key(snapshot: dict) -> str:
    slug = snapshot.get("bundle_slug") or ""
    if not slug:
        return ""
    return f"{slug}@{snapshot.get('bundle_version') or 1}"


def backfill_slicing_keys(apps, schema_editor):
    generation_job = apps.get_model("generation", "GenerationJob")
    batch = []
    qs = generation_job.objects.exclude(resolved_bundle={}).only("id", "resolved_bundle")
    for job in qs.iterator(chunk_size=500):
        snapshot = job.resolved_bundle
        if not isinstance(snapshot, dict) or not snapshot:
            continue
        job.prompt_hash = _prompt_hash(snapshot)
        job.bundle_key = _bundle_key(snapshot)
        batch.append(job)
        if len(batch) >= 500:
            generation_job.objects.bulk_update(batch, ["prompt_hash", "bundle_key"])
            batch = []
    if batch:
        generation_job.objects.bulk_update(batch, ["prompt_hash", "bundle_key"])


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0002_content_blocks_and_bundles"),
    ]

    operations = [
        migrations.AddField(
            model_name="generationjob",
            name="prompt_hash",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text=(
                    "Hash of prompt material (templates + app spec + block versions); "
                    "shared across repeats/models"
                ),
                max_length=16,
                verbose_name="prompt hash",
            ),
        ),
        migrations.AddField(
            model_name="generationjob",
            name="bundle_key",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text='Denormalized "bundle-slug@version" slicing key',
                max_length=220,
                verbose_name="bundle key",
            ),
        ),
        migrations.RunPython(backfill_slicing_keys, migrations.RunPython.noop),
    ]
