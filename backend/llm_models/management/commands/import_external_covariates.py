"""Import Epoch AI and LMArena covariates for the models in the curated name map.

Usage:
    python manage.py import_external_covariates [--epoch] [--lmarena] [--dry-run]
        [--model MODEL_ID ...] [--run RUN_ID ...] [--study STUDY_ID ...] [--suggest] [--map PATH]

Without --epoch / --lmarena both sources are imported. Names are matched
exactly through backend/llm_models/data/external_model_map.yaml; --suggest
prints the closest external names for the models in scope (for curating the
map) and writes nothing.
"""

from __future__ import annotations

import uuid

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from backend.generation.models import GenerationRun
from backend.generation.models import Study
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
        parser.add_argument("--run", action="append", default=[], help="Limit to the model of this run (repeatable).")
        parser.add_argument(
            "--study",
            action="append",
            default=[],
            help="Limit to the models this study ran (repeatable).",
        )
        parser.add_argument(
            "--suggest",
            action="store_true",
            help="Print fuzzy candidates for curating the map (used + local models, or --model / --run / --study); "
            "no writes.",
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
        run_ids = _uuids(options["run"], "--run", "run")
        study_ids = _uuids(options["study"], "--study", "study")
        if run_ids:
            runs = GenerationRun.objects.filter(id__in=run_ids).select_related("model")
            _require_all(run_ids, {r.id for r in runs}, "run")
            model_ids |= {r.model.model_id for r in runs if r.model is not None}
        if study_ids:
            _require_all(study_ids, set(Study.objects.filter(id__in=study_ids).values_list("id", flat=True)), "study")
            model_ids |= set(
                GenerationRun.objects.filter(study_id__in=study_ids, model__isnull=False)
                .values_list("model__model_id", flat=True)
                .distinct(),
            )
        if not model_ids and not run_ids and not study_ids and not options["model"]:
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


def _uuids(raw_ids: list[str], flag: str, noun: str) -> list[uuid.UUID]:
    out = []
    for raw in raw_ids:
        try:
            out.append(uuid.UUID(raw))
        except ValueError as exc:
            msg = f"{flag} {raw!r} is not a {noun} id"
            raise CommandError(msg) from exc
    return out


def _require_all(wanted: list[uuid.UUID], found: set[uuid.UUID], noun: str) -> None:
    missing = [str(i) for i in wanted if i not in found]
    if missing:
        msg = f"unknown {noun} id(s): {', '.join(missing)}"
        raise CommandError(msg)
