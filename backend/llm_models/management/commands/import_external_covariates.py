"""Import Epoch AI and LMArena covariates for the models in the curated name map.

Usage:
    python manage.py import_external_covariates [--epoch] [--lmarena] [--dry-run]
        [--model MODEL_ID ...] [--job JOB_ID ...] [--suggest] [--map PATH]

Without --epoch / --lmarena both sources are imported. Names are matched
exactly through backend/llm_models/data/external_model_map.yaml; --suggest
prints the closest external names for the models in scope (for curating the
map) and writes nothing.
"""

from __future__ import annotations

import uuid

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from backend.generation.models import GenerationJob
from backend.llm_models.models import LLMModel
from backend.llm_models.services.external_sources import SOURCES
from backend.llm_models.services.external_sources import arena_names_by_config
from backend.llm_models.services.external_sources import fetch_epoch_csv
from backend.llm_models.services.external_sources import fetch_lmarena
from backend.llm_models.services.external_sources import import_external_covariates
from backend.llm_models.services.external_sources import load_model_map
from backend.llm_models.services.external_sources import parse_epoch_csv
from backend.llm_models.services.external_sources import suggest_matches
from backend.llm_models.services.external_sources import used_model_ids


class Command(BaseCommand):
    help = "Import Epoch AI / LMArena covariates into LLMModel.metadata through the curated name map."

    def add_arguments(self, parser):
        parser.add_argument("--epoch", action="store_true", help="Import Epoch AI data (default: both sources).")
        parser.add_argument("--lmarena", action="store_true", help="Import LMArena data (default: both sources).")
        parser.add_argument("--dry-run", action="store_true", help="Download and match, but write nothing.")
        parser.add_argument("--model", action="append", default=[], help="Limit to this model_id (repeatable).")
        parser.add_argument("--job", action="append", default=[], help="Limit to the model of this job (repeatable).")
        parser.add_argument(
            "--suggest",
            action="store_true",
            help="Print fuzzy candidates for curating the map (used + local models, or --model / --job); no writes.",
        )
        parser.add_argument("--map", default=None, help="Path to an alternative name map YAML.")

    def handle(self, *args, **options):
        sources = tuple(s for s in SOURCES if options[s]) or SOURCES
        try:
            entries = load_model_map(options["map"])
        except (OSError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        model_ids = self._scope(options)

        if options["suggest"]:
            self._suggest(model_ids, sources)
            return

        report = import_external_covariates(
            sources=sources,
            model_ids=model_ids,
            dry_run=options["dry_run"],
            entries=entries,
        )
        for row in report.rows:
            parts = [f"{row['model_id']} [{row['match']}]"]
            if "epoch" in sources:
                parts.append(
                    f"epoch={row.get('epoch')}" + (f" ({row['epoch_name']})" if row.get("epoch_name") else ""),
                )
            if "lmarena" in sources:
                cats = ",".join(row.get("lmarena_categories") or [])
                parts.append(f"lmarena={row.get('lmarena')}" + (f" ({cats})" if cats else ""))
            if row.get("changed"):
                parts.append(("would write " if report.dry_run else "wrote ") + ",".join(row["changed"]))
            self.stdout.write("  ".join(parts))
        for warning in report.warnings:
            self.stdout.write(self.style.WARNING(f"warning: {warning}"))

        used = used_model_ids()
        summary = [f"{'DRY RUN — ' if report.dry_run else ''}{len(report.rows)} mapped models"]
        for source in sources:
            matched = [r for r in report.rows if r.get(source) == "matched"]
            used_matched = sum(1 for r in matched if r["model_id"] in used)
            summary.append(f"{source}: {len(matched)} matched ({used_matched} of {len(used)} used models)")
        self.stdout.write(self.style.SUCCESS("; ".join(summary)))

    def _scope(self, options) -> set[str] | None:
        model_ids = {m.strip() for m in options["model"] if m.strip()}
        job_ids = []
        for raw in options["job"]:
            try:
                job_ids.append(uuid.UUID(raw))
            except ValueError as exc:
                msg = f"--job {raw!r} is not a job id"
                raise CommandError(msg) from exc
        if job_ids:
            jobs = GenerationJob.objects.filter(id__in=job_ids).select_related("model")
            found = {str(j.id) for j in jobs}
            missing = [str(j) for j in job_ids if str(j) not in found]
            if missing:
                msg = f"unknown job id(s): {', '.join(missing)}"
                raise CommandError(msg)
            model_ids |= {j.model.model_id for j in jobs if j.model is not None}
        if not model_ids and not job_ids and not options["model"]:
            return None
        return model_ids

    def _suggest(self, model_ids: set[str] | None, sources: tuple[str, ...]) -> None:
        if model_ids is None:
            model_ids = used_model_ids() | set(
                LLMModel.objects.filter(endpoint__isnull=False).values_list("model_id", flat=True),
            )
        epoch_names = sorted(parse_epoch_csv(fetch_epoch_csv())) if "epoch" in sources else []
        arena_names = arena_names_by_config(fetch_lmarena()) if "lmarena" in sources else {}
        for model in LLMModel.objects.filter(model_id__in=model_ids).order_by("model_id"):
            self.stdout.write(self.style.MIGRATE_HEADING(model.model_id))
            for source, candidates in suggest_matches(model, epoch_names=epoch_names, arena_names=arena_names).items():
                if not candidates:
                    continue
                listed = "; ".join(f"{name} ({score:.2f})" for name, score in candidates)
                self.stdout.write(f"  {source}: {listed}")
