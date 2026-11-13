"""Refresh every MSS v2 input in order (the lead's one-shot before ``select_models``).

Usage:
    python manage.py refresh_mss_inputs [--user ID] [--skip-sync] [--dry-run]

Steps:
1. OpenRouter catalog sync (new rows such as kimi-k3.1-pro, hy4);
2. ``enrich_models --no-hf`` (models.dev records; links local route rows to their catalog twins);
3. ``import_list_prices`` (first-party list prices);
4. ``import_external_covariates --frame`` (LMArena and Epoch through the curated map);
5. ``import_popularity`` (OpenRouter Data API, 28 days).

Network: openrouter.ai (sync, Data API, credit-free), models.dev, Hugging Face
datasets-server (LMArena), epoch.ai. ``--dry-run`` skips the sync and the
enrichment (neither can dry-run) and passes ``--dry-run`` to the other steps.
"""

from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from backend.rankings.services.openrouter_api import openrouter_key


class Command(BaseCommand):
    help = "Refresh the MSS v2 inputs: catalog sync, enrichment, list prices, LMArena/Epoch covariates, popularity."

    def add_arguments(self, parser):
        parser.add_argument("--user", type=int, default=1, help="User whose stored OpenRouter key is used.")
        parser.add_argument("--skip-sync", action="store_true", help="Skip the OpenRouter catalog sync.")
        parser.add_argument("--dry-run", action="store_true", help="No writes (the sync is skipped).")

    def handle(self, *args, **options):
        dry = options["dry_run"]
        if options["skip_sync"] or dry:
            self.stdout.write("1/5 catalog sync skipped")
        else:
            from backend.llm_models.services import sync_models_from_openrouter

            key = openrouter_key(options["user"])
            if not key:
                msg = f"user {options['user']} has no stored OpenRouter key"
                raise CommandError(msg)
            result = sync_models_from_openrouter(key)
            self.stdout.write(f"1/5 catalog sync: {result}")
        if dry:
            self.stdout.write("2/5 enrich_models skipped (dry run)")
        else:
            self.stdout.write("2/5 enrich_models --no-hf")
            call_command("enrich_models", "--no-hf", stdout=self.stdout)
        extra = ["--dry-run"] if dry else []
        self.stdout.write("3/5 import_list_prices")
        call_command("import_list_prices", *extra, stdout=self.stdout)
        self.stdout.write("4/5 import_external_covariates --frame")
        call_command("import_external_covariates", "--frame", *extra, stdout=self.stdout)
        self.stdout.write("5/5 import_popularity")
        call_command("import_popularity", "--user", str(options["user"]), *extra, stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS("MSS inputs refreshed" + (" (dry run)" if dry else "")))
