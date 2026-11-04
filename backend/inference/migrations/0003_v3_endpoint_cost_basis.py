"""v3 (spec section 3.10): ``LLMEndpoint.cost_basis`` (billed, list_price or free).

Existing ollama endpoints are local, so they are accounted in tokens only (``free``); every other endpoint
starts as ``billed``. Subscription endpoints (e.g. OpenCode Go) must be switched to ``list_price`` by
their owner: their provider reports no billed cost.
"""

from django.db import migrations
from django.db import models


def local_endpoints_are_free(apps, schema_editor):
    endpoint = apps.get_model("inference", "LLMEndpoint")
    endpoint.objects.using(schema_editor.connection.alias).filter(kind="ollama").update(cost_basis="free")


class Migration(migrations.Migration):
    dependencies = [
        ("inference", "0002_llmrequest"),
    ]

    operations = [
        migrations.AddField(
            model_name="llmendpoint",
            name="cost_basis",
            field=models.CharField(
                choices=[("billed", "Billed"), ("list_price", "List price"), ("free", "Free (tokens only)")],
                default="billed",
                max_length=10,
            ),
        ),
        migrations.RunPython(local_endpoints_are_free, reverse_code=migrations.RunPython.noop),
    ]
