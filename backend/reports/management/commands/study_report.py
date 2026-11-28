"""Generate the study report from the shell and write it as Markdown, LaTeX or JSON.

Usage:
    python manage.py study_report --study <uuid> --out results/study.md [--format md|tex|json]
        [--user <email>] [--seed 0] [--include-ai] [--exclude-truncated] [--no-bootstrap] [--save]

    python manage.py study_report --latest-unique --studies <uuid,uuid,...> --out results/final.md
        [--models <model_id,...>] [--tasks <slug@version|slug,...>] [--common-tasks]

Without ``--study`` the report covers every sample run in scope. Without ``--user`` it covers every
user's runs (staff view); with ``--study`` and no ``--user`` it acts as the study's owner.
``--latest-unique`` pools several studies (``--studies``; default: every study in scope) and keeps the
newest usable run per (model, task) (``reports.services.cohort_selection``).
``--save`` also stores it as a Report row, so it appears in the UI.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.utils import timezone

from backend.reports.models import Report
from backend.reports.services.renderers import render_latex
from backend.reports.services.renderers import render_markdown
from backend.reports.services.report_service import _make_summary
from backend.reports.services.study_report import generate_study_report


class Command(BaseCommand):
    help = "Write the study report (funnel, issues, correlations, rankings, validity) to a file."

    def add_arguments(self, parser):
        parser.add_argument("--out", required=True)
        parser.add_argument("--format", choices=("md", "tex", "json"), default=None, help="Default: from --out suffix")
        parser.add_argument("--study", default=None, help="Study id: its current runs (main stage)")
        parser.add_argument("--user", default=None, help="Scope to this account email's runs")
        parser.add_argument("--seed", type=int, default=0)
        parser.add_argument("--include-ai", action="store_true")
        parser.add_argument(
            "--eval-suite",
            default=None,
            help="One versioned suite for every run; default: each run's planned suite",
        )
        parser.add_argument(
            "--exclude-truncated",
            action="store_true",
            help="Sensitivity analysis: leave truncated runs out (a locked analysis plan's rule wins)",
        )
        parser.add_argument("--no-bootstrap", action="store_true")
        parser.add_argument("--save", action="store_true", help="Also store a Report row")
        parser.add_argument(
            "--latest-unique",
            action="store_true",
            help="Cross-study sample: the newest usable run per (model, task) of the pooled studies",
        )
        parser.add_argument("--studies", default=None, help="Comma-separated study ids to pool (--latest-unique)")
        parser.add_argument("--models", default=None, help="Comma-separated model ids to keep (--latest-unique)")
        parser.add_argument(
            "--tasks",
            default=None,
            help="Comma-separated task refs (slug@version or slug) to keep (--latest-unique)",
        )
        parser.add_argument(
            "--common-tasks",
            action="store_true",
            help="Complete cases: only tasks every group has a selected run for (--latest-unique)",
        )
        parser.add_argument(
            "--evidence",
            default=None,
            help="JSON file of completed supplemental experiments bound to these source runs",
        )

    def handle(self, *args, **opts):
        selection = self._selection(opts)
        user = None
        if opts["user"]:
            try:
                model = get_user_model()
                user = model.objects.get(**{model.USERNAME_FIELD: opts["user"]})
            except get_user_model().DoesNotExist as exc:
                msg = f"No user {opts['user']!r}"
                raise CommandError(msg) from exc
        elif opts["study"]:
            # owned_study needs a user who may see it: act as its owner.
            from django.core.exceptions import ValidationError

            from backend.generation.models import Study

            try:
                study = Study.objects.filter(id=opts["study"]).select_related("created_by").first()
            except ValidationError:
                study = None
            if study is None:
                msg = f"No study {opts['study']}"
                raise CommandError(msg)
            user = study.created_by

        config = {
            "study_id": opts["study"],
            "seed": opts["seed"],
            "include_ai": opts["include_ai"],
            "bootstrap": not opts["no_bootstrap"],
            "eval_suite": opts["eval_suite"],
        }
        if selection:
            del config["study_id"]
            config.update(selection)
        if opts["exclude_truncated"]:
            config["exclude_truncated"] = True
        if opts["evidence"]:
            try:
                config["supplemental_evidence"] = json.loads(Path(opts["evidence"]).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise CommandError(str(exc)) from exc
        try:
            data = generate_study_report(config, user=user)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        # The study's name, not its uuid, when the report covers one.
        scope = ((data.get("protocol") or {}).get("study") or {}).get("name")
        if selection:
            # The studies the sample draws runs from (the pool can hold studies that contribute none).
            pooled = len(((data.get("protocol") or {}).get("scope") or {}).get("studies_contributing") or [])
            scope = f"newest unique runs ({pooled} {'study' if pooled == 1 else 'studies'})"
        scope = scope or opts["study"] or "all apps"
        report = Report(
            report_type=Report.Type.STUDY_REPORT,
            title=f"Study report: {scope}",
            config=config,
            report_data=data,
            summary=_make_summary("study_report", data),
            status=Report.Status.COMPLETED,
            created_by=user,
            completed_at=timezone.now(),
        )
        if opts["save"]:
            report.save()

        out = Path(opts["out"])
        fmt = opts["format"] or {".md": "md", ".tex": "tex", ".json": "json"}.get(out.suffix, "md")
        body = {
            "md": render_markdown,
            "tex": render_latex,
            "json": lambda r: json.dumps(r.report_data, indent=2, default=str),
        }[fmt](report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")
        warnings = [v for v in data["validity"] if v["level"] == "warning"]
        self.stdout.write(self.style.SUCCESS(f"Wrote {out} ({fmt}); {len(warnings)} validity warning(s)."))
        for v in warnings:
            self.stdout.write(f"  - {v['message']}")

    @staticmethod
    def _selection(opts) -> dict:
        """The cross-study selection keys of the config (empty without ``--latest-unique``)."""

        def items(key):
            return [v.strip() for v in (opts[key] or "").split(",") if v.strip()]

        given = [f"--{k.replace('_', '-')}" for k in ("studies", "models", "tasks", "common_tasks") if opts[k]]
        if not opts["latest_unique"]:
            if given:
                msg = f"{', '.join(given)} need --latest-unique"
                raise CommandError(msg)
            return {}
        if opts["study"]:
            msg = "--study cannot be combined with --latest-unique; pool studies with --studies"
            raise CommandError(msg)
        return {
            "selection": "latest_unique",
            "study_ids": items("studies"),
            "models": items("models"),
            "tasks": items("tasks"),
            "common_tasks_only": bool(opts["common_tasks"]),
        }
