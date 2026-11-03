# WP-10: the study cap resolved at launch (Study.cap_usd / cap_tokens). Additive; an integrator renumbers it.

from django.db import migrations
from django.db import models


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0024_v3_models"),
    ]

    operations = [
        migrations.AddField(
            model_name="study",
            name="cap_usd",
            field=models.DecimalField(
                blank=True,
                decimal_places=6,
                help_text="Resolved at launch from the budget preset (auto -> estimate) or the launch request",
                max_digits=12,
                null=True,
                verbose_name="study cap (USD)",
            ),
        ),
        migrations.AddField(
            model_name="study",
            name="cap_tokens",
            field=models.BigIntegerField(
                blank=True,
                help_text="The budget preset's study.max_total_tokens at launch; NULL = no token cap",
                null=True,
                verbose_name="study cap (tokens)",
            ),
        ),
    ]
