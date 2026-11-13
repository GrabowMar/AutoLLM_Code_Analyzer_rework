"""Resolve first-party list prices for the MSS v2 frame and store them in ``metadata["list_price"]``.

Usage:
    python manage.py import_list_prices [--model CANONICAL_ID ...] [--as-of YYYY-MM-DD]
                                        [--no-endpoints] [--dry-run] [--json]

Order (docs/design/MSS_V2.md §2.2): the vendor's models.dev record, the
OpenCode Go record, the vendor's own OpenRouter endpoint, the fp8+ endpoint
median (discounts undone). ``/api/v1/models`` pricing is never used. Network:
one models.dev download (60 s timeout) and, only for models without a
models.dev price, the OpenRouter endpoints of that model (20 s timeout each).
``--dry-run`` downloads and resolves but writes nothing.
"""

from __future__ import annotations

import datetime as dt
import json

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.utils import timezone

from backend.rankings.services.frame import build_frame
from backend.rankings.services.frame import load_frame_config
from backend.rankings.services.frame import load_rows
from backend.rankings.services.metadata import set_metadata
from backend.rankings.services.pricing import cheapest_openrouter
from backend.rankings.services.pricing import download_models_dev_raw
from backend.rankings.services.pricing import resolve_list_price
from backend.rankings.services.pricing import run_cost

# Filters a model must pass before it is worth pricing (E5-E7 depend on price-independent facts too,
# but popular excluded models still enter the market's spend total, so they are priced as well).
PRICED_STEPS = (None, "E5", "E6", "E7")


def _in_scope(entry: dict) -> bool:
    """Priced: models passing E1-E4, plus local-only models failing E2/E3 for lack of a catalog row.

    A local route row stores no context window or tool flag; the first-party
    record stored with the price supplies both (frame E2/E3), so such a model
    is priced before the filters can judge it.
    """
    step = (entry["exclusion"] or {}).get("step")
    if step in PRICED_STEPS:
        return True
    local_only = all(str(m["model_id"]).startswith("local-") for m in entry["members"])
    return step in ("E2", "E3") and local_only


class Command(BaseCommand):
    help = "Store first-party list prices (models.dev / OpenRouter endpoints) for the MSS v2 frame."

    def add_arguments(self, parser):
        parser.add_argument("--model", action="append", default=[], help="Limit to this canonical id (repeatable).")
        parser.add_argument("--as-of", default=None, help="Snapshot date (default today, UTC).")
        parser.add_argument("--no-endpoints", action="store_true", help="Do not query OpenRouter endpoints.")
        parser.add_argument("--dry-run", action="store_true", help="Resolve but write nothing.")
        parser.add_argument("--json", action="store_true", help="Print the records as JSON.")

    def handle(self, *args, **options):
        as_of = dt.date.fromisoformat(options["as_of"]) if options["as_of"] else timezone.now().date()
        config = load_frame_config()
        rows = load_rows()
        frame = build_frame(rows, as_of, include_previews=True, config=config)
        wanted = set(options["model"])
        scope = [e for e in frame["candidates"] if (not wanted and _in_scope(e)) or e["canonical_id"] in wanted]
        missing = sorted(wanted - {e["canonical_id"] for e in scope})
        if missing:
            msg = f"not canonical models of the frame: {', '.join(missing)}"
            raise CommandError(msg)
        raw = download_models_dev_raw(use_cache=False)
        if not raw:
            msg = "models.dev is unreachable; nothing resolved"
            raise CommandError(msg)

        from backend.llm_models.models import LLMModel
        from backend.llm_models.services.enrichment import fetch_provider_endpoints

        by_id = {r["model_id"]: r for r in rows}
        fetched_at = timezone.now().isoformat()
        go_meta = {k: v for k, v in (config.go_limits or {}).items() if k in ("plan", "source", "fetched")}
        out = []
        for entry in scope:
            catalog_member = next(
                (
                    m
                    for m in [entry["pinned_version"], *entry["members"]]
                    if not str(m["model_id"]).startswith("local-")
                ),
                None,
            )

            def endpoints(member=catalog_member):
                if member is None:
                    return []
                model = LLMModel.objects.filter(model_id=member["model_id"]).first()
                return fetch_provider_endpoints(model).get("endpoints", []) if model else []

            go = entry["routes"].get("go") or {}
            anchor_meta = by_id[entry["anchor_model_id"]]["metadata"]
            pinned_meta = (by_id.get(entry["pinned_version"]["model_id"]) or {}).get("metadata") or {}
            record = resolve_list_price(
                entry,
                raw=raw,
                vendors=config.vendors,
                endpoints=None if options["no_endpoints"] else endpoints,
                fetched_at=fetched_at,
                go_limit=config.go_limit(go.get("served_name")) if go else None,
                go_limit_source={"limit_" + k: v for k, v in go_meta.items()} if go else None,
                cheapest=cheapest_openrouter(pinned_meta or anchor_meta),
            )
            out.append({"canonical_id": entry["canonical_id"], "anchor": entry["anchor_model_id"], "record": record})
            if record is not None and not options["dry_run"]:
                set_metadata(by_id[entry["anchor_model_id"]]["pk"], "list_price", record)

        if options["json"]:
            self.stdout.write(json.dumps(out, indent=2, default=str))
            return
        priced = [o for o in out if o["record"]]
        for o in sorted(priced, key=lambda o: o["canonical_id"]):
            r = o["record"]
            cost = run_cost(r)
            self.stdout.write(
                f"{o['canonical_id']:48s} {r['input']:>8.3f}/{r['output']:>7.3f}"
                f" cr={r['cache_read'] if r['cache_read'] is not None else '-'}"
                f" cw={r['cache_write'] if r['cache_write'] is not None else '-'}"
                f"  $/run={cost:.4f}  [{r['source']}]",
            )
        unpriced = sorted(o["canonical_id"] for o in out if not o["record"])
        if unpriced:
            self.stdout.write(self.style.WARNING(f"no price source for {len(unpriced)}: {', '.join(unpriced)}"))
        verb = "would write" if options["dry_run"] else "wrote"
        self.stdout.write(
            self.style.SUCCESS(f"{'DRY RUN — ' if options['dry_run'] else ''}{verb} {len(priced)} list prices"),
        )
