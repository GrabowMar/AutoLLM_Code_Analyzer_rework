"""Recompute the empirical cost priors (``CostPrior``) from finished runs (spec section 4.4).

Run nightly (and whenever a batch of runs finished outside the executor); the executor also refreshes the
keys of each run as it finishes. ``--key`` limits the refresh to named keys, ``--window-days`` changes the
history window (default ``budget.constants.PRIOR_WINDOW_DAYS``), ``--dry-run`` prints what would be written.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from backend.generation.budget import constants
from backend.generation.budget import priors


class Command(BaseCommand):
    help = "Recompute the cost priors used by the study estimator (kind|harness|model|size|tier keys)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--key", action="append", dest="keys", help="Refresh only this key (repeatable)")
        parser.add_argument("--window-days", type=int, default=constants.PRIOR_WINDOW_DAYS)
        parser.add_argument("--dry-run", action="store_true", help="List the keys and their n without writing")

    def handle(self, *args: Any, **options: Any) -> None:
        window = options["window_days"]
        if options["dry_run"]:
            observations = priors.observations(window_days=window)
            counts: dict[str, int] = {}
            for obs in observations:
                for key in obs.keys:
                    counts[key] = counts.get(key, 0) + 1
            for key in sorted(counts):
                if options["keys"] and key not in options["keys"]:
                    continue
                usable = "usable" if counts[key] >= constants.PRIOR_MIN_N else "below n_min"
                self.stdout.write(f"{key}\tn={counts[key]}\t{usable}")
            self.stdout.write(f"{len(observations)} observation(s) in the last {window} day(s); nothing written")
            return
        written = priors.refresh_priors(keys=options["keys"], window_days=window)
        self.stdout.write(self.style.SUCCESS(f"refreshed {written} cost prior row(s) (window {window} days)"))
