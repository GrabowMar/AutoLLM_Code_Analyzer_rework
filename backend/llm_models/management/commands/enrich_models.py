"""Attach models.dev and Hugging Face metadata to the OpenRouter catalog.

Usage:
    python manage.py enrich_models [--no-hf] [--force-hf]
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from backend.llm_models.services.enrichment import enrich_catalog


class Command(BaseCommand):
    help = "Enrich catalog models with models.dev and Hugging Face metadata."

    def add_arguments(self, parser):
        parser.add_argument("--no-hf", action="store_true", help="Skip Hugging Face lookups.")
        parser.add_argument("--force-hf", action="store_true", help="Refetch Hugging Face data even when fresh.")

    def handle(self, *args, **options):
        result = enrich_catalog(include_hf=not options["no_hf"], force_hf=options["force_hf"])
        self.stdout.write(self.style.SUCCESS(f"Enrichment done: {result}"))
