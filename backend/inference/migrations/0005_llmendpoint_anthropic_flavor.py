"""The ``anthropic`` API flavor: Claude models through Anthropic's native Messages API (prompt caching).

Valid only for a direct ``openai_compatible`` endpoint (the relay cannot carry ``/v1/messages``), so the
``llmendpoint_native_only_ollama`` constraint becomes ``llmendpoint_flavor_by_kind``. The data step switches
the existing direct endpoints on ``api.anthropic.com`` to it (their ``cost_basis`` is unchanged); the models'
list prices are set by the next inventory refresh (``inference.anthropic_prices``), not here.
"""

from urllib.parse import urlsplit

from django.db import migrations
from django.db import models

ANTHROPIC_HOST = "api.anthropic.com"


def _anthropic_endpoints(apps, schema_editor):
    endpoint = apps.get_model("inference", "LLMEndpoint")
    rows = endpoint.objects.using(schema_editor.connection.alias).filter(
        kind="openai_compatible",
        connection="direct",
    )
    return [row.pk for row in rows if (urlsplit(row.base_url or "").hostname or "").lower() == ANTHROPIC_HOST]


def to_anthropic_flavor(apps, schema_editor):
    endpoint = apps.get_model("inference", "LLMEndpoint")
    pks = _anthropic_endpoints(apps, schema_editor)
    endpoint.objects.using(schema_editor.connection.alias).filter(pk__in=pks, api_flavor="openai").update(
        api_flavor="anthropic",
    )


def to_openai_flavor(apps, schema_editor):
    endpoint = apps.get_model("inference", "LLMEndpoint")
    endpoint.objects.using(schema_editor.connection.alias).filter(api_flavor="anthropic").update(
        api_flavor="openai",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("inference", "0004_llmrequest_source_slot_call"),
    ]

    operations = [
        migrations.AlterField(
            model_name="llmendpoint",
            name="api_flavor",
            field=models.CharField(
                choices=[("native", "Native"), ("openai", "OpenAI"), ("anthropic", "Anthropic Messages")],
                default="native",
                max_length=12,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="llmendpoint",
            name="llmendpoint_native_only_ollama",
        ),
        # Between the two constraints, so the reverse resets the flavor before the old constraint returns.
        migrations.RunPython(to_anthropic_flavor, to_openai_flavor),
        migrations.AddConstraint(
            model_name="llmendpoint",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("api_flavor", "openai"),
                    models.Q(("api_flavor", "native"), ("kind", "ollama")),
                    models.Q(("api_flavor", "anthropic"), ("connection", "direct"), ("kind", "openai_compatible")),
                    _connector="OR",
                ),
                name="llmendpoint_flavor_by_kind",
            ),
        ),
    ]
