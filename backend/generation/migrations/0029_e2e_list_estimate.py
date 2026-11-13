"""E2E-13: list-price estimates for ``cap_basis: list`` studies.

* ``StudyCell.estimate_list_usd``: the cell's expected list-price cost, which the ``max_cost_overrun`` pilot
  gate and the ``cost_overrun`` stop rule of a list-basis study compare the runs' list cost with.
* ``CostPrior.list_cost_p10`` / ``list_cost_p90``: the list spread of a prior (subscription runs bill 0).

Existing cells are backfilled from their plan's ``estimate.by_condition`` (the condition's expected list
total divided by its cell count): the per-cell estimates are not stored outside the plan, and the gate and
the stop rule only use a condition's mean estimate, which this reproduces.
"""

from decimal import Decimal

from django.db import migrations
from django.db import models


def backfill(apps, schema_editor):
    StudyCell = apps.get_model("generation", "StudyCell")
    StudyPlan = apps.get_model("generation", "StudyPlan")
    for plan in StudyPlan.objects.exclude(estimate=None).iterator():
        estimate = plan.estimate if isinstance(plan.estimate, dict) else {}
        per_cell = {}
        for row in estimate.get("by_condition") or []:
            n = row.get("n_cells") or 0
            value = row.get("expected_list_usd") or 0
            if n and value:
                per_cell[row.get("condition_index")] = Decimal(str(round(value / n, 6)))
        if not per_cell:
            continue
        cells = StudyCell.objects.filter(plan_id=plan.pk, estimate_list_usd=0).select_related("condition")
        batch = []
        for cell in cells.iterator():
            value = per_cell.get(cell.condition.index)
            if value:
                cell.estimate_list_usd = value
                batch.append(cell)
        StudyCell.objects.bulk_update(batch, ["estimate_list_usd"], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("generation", "0028_wave3_runfile_mode_range"),
    ]

    operations = [
        migrations.AddField(
            model_name="studycell",
            name="estimate_list_usd",
            field=models.DecimalField(
                decimal_places=6,
                default=Decimal("0"),
                max_digits=10,
                verbose_name="estimate (list USD)",
            ),
        ),
        migrations.AddField(
            model_name="costprior",
            name="list_cost_p10",
            field=models.FloatField(blank=True, null=True, verbose_name="list cost p10"),
        ),
        migrations.AddField(
            model_name="costprior",
            name="list_cost_p90",
            field=models.FloatField(blank=True, null=True, verbose_name="list cost p90"),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
