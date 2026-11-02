"""Attach models.dev and Hugging Face metadata to the catalog; link local endpoint models to their twin.

Usage:
    python manage.py enrich_models [--no-hf] [--force-hf] [--hf-user ID]
                                   [--model ID_OR_MODEL_ID ...] [--no-link-local] [--dry-run]

``--hf-user`` uses that user's stored Hugging Face token (else
``HUGGINGFACE_API_TOKEN``, else anonymous). ``--model`` limits the run (pk or
model id, repeatable). ``--dry-run`` lists what would be fetched and makes no
request.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db.models import Q

from backend.llm_models.models import LLMModel
from backend.llm_models.services.enrichment import _hf_is_fresh
from backend.llm_models.services.enrichment import enrich_catalog
from backend.llm_models.services.hf import client_for
from backend.llm_models.services.hf import hf_token_for

# Rough upper bound of Hub requests per model (record, tree, three files with redirect hops, papers).
REQUESTS_PER_MODEL_ESTIMATE = 10


class Command(BaseCommand):
    help = "Enrich catalog models with models.dev and Hugging Face metadata; link local models to catalog twins."

    def add_arguments(self, parser):
        parser.add_argument("--no-hf", action="store_true", help="Skip Hugging Face lookups.")
        parser.add_argument("--force-hf", action="store_true", help="Refetch Hugging Face data even when fresh.")
        parser.add_argument("--hf-user", type=int, default=None, help="Use this user's stored Hugging Face token.")
        parser.add_argument(
            "--model",
            action="append",
            default=[],
            help="Only this model (pk or model id); repeatable.",
        )
        parser.add_argument("--no-link-local", action="store_true", help="Do not link local models to catalog twins.")
        parser.add_argument("--dry-run", action="store_true", help="List what would be fetched; no requests.")

    def _model_ids(self, selectors: list[str]) -> list[int] | None:
        if not selectors:
            return None
        query = Q()
        for selector in selectors:
            query |= Q(pk=int(selector)) if selector.isdigit() else Q(model_id=selector)
        ids = list(LLMModel.objects.filter(query).values_list("pk", flat=True))
        if not ids:
            msg = f"No model matches {', '.join(selectors)}"
            raise CommandError(msg)
        return ids

    def handle(self, *args, **options):
        model_ids = self._model_ids(options["model"])
        user = None
        if options["hf_user"] is not None:
            user = get_user_model().objects.filter(pk=options["hf_user"]).first()
            if user is None:
                msg = f"No user with id {options['hf_user']}"
                raise CommandError(msg)
        include_hf = not options["no_hf"]

        if options["dry_run"]:
            self._dry_run(model_ids, user=user, include_hf=include_hf, force_hf=options["force_hf"])
            return

        result = enrich_catalog(
            include_hf=include_hf,
            force_hf=options["force_hf"],
            hf_client=client_for(user),
            model_ids=model_ids,
            link_local=not options["no_link_local"],
        )
        self.stdout.write(self.style.SUCCESS(f"Enrichment done: {result}"))

    def _dry_run(self, model_ids, *, user, include_hf: bool, force_hf: bool) -> None:
        catalog = LLMModel.objects.filter(endpoint__isnull=True)
        local = LLMModel.objects.filter(endpoint__isnull=False, endpoint__is_system=False)
        if model_ids:
            catalog, local = catalog.filter(pk__in=model_ids), local.filter(pk__in=model_ids)
        to_fetch = []
        for model in catalog.order_by("model_id"):
            hf_id = (model.capabilities_json or {}).get("hugging_face_id")
            if include_hf and hf_id and (force_hf or not _hf_is_fresh(model.metadata or {})):
                to_fetch.append((model.pk, model.model_id, hf_id))
        from backend.credentials.models import Provider
        from backend.credentials.services.resolver import get_user_credential_for

        if user is not None and get_user_credential_for(user, Provider.HUGGINGFACE) is not None:
            token = f"user #{user.pk}'s stored token"
        else:
            token = "HUGGINGFACE_API_TOKEN" if hf_token_for(None) else "anonymous"
        self.stdout.write(f"Catalog models: {catalog.count()} (models.dev: one download)")
        self.stdout.write(f"Hugging Face fetches: {len(to_fetch)} via {token}")
        for pk, model_id, hf_id in to_fetch[:50]:
            self.stdout.write(f"  #{pk} {model_id} -> {hf_id}")
        if len(to_fetch) > 50:
            self.stdout.write(f"  … {len(to_fetch) - 50} more")
        self.stdout.write(f"Estimated Hub requests: <= {len(to_fetch) * REQUESTS_PER_MODEL_ESTIMATE}")
        self.stdout.write(f"Local models to link: {local.count()}")
        self.stdout.write(self.style.WARNING("Dry run: nothing fetched or written."))
