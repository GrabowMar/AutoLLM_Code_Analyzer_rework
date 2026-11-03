"""v3 clean break: the ``LLMRequest.source`` value ``scaffolding`` becomes ``slot_call`` (single-shot slot calls).

Existing ``scaffolding`` rows (v2 template-mode calls) are relabelled so the column only holds v3 choices.
"""

from django.db import migrations
from django.db import models


def relabel(apps, schema_editor):
    request = apps.get_model("inference", "LLMRequest")
    request.objects.using(schema_editor.connection.alias).filter(source="scaffolding").update(source="slot_call")


def unrelabel(apps, schema_editor):
    request = apps.get_model("inference", "LLMRequest")
    request.objects.using(schema_editor.connection.alias).filter(source="slot_call").update(source="scaffolding")


class Migration(migrations.Migration):
    dependencies = [
        ("inference", "0003_v3_endpoint_cost_basis"),
    ]

    operations = [
        migrations.AlterField(
            model_name="llmrequest",
            name="source",
            field=models.CharField(
                choices=[
                    ("slot_call", "Slot call"),
                    ("agent_proxy", "Agent proxy"),
                    ("analysis", "Analysis"),
                    ("warmup", "Warm-up"),
                    ("probe", "Probe"),
                    ("inventory", "Inventory"),
                ],
                max_length=16,
            ),
        ),
        migrations.RunPython(relabel, reverse_code=unrelabel),
    ]
