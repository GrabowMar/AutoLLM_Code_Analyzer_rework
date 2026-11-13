"""Import OpenRouter usage (Data API ``rankings-daily``) as the MSS v2 popularity input.

Usage:
    python manage.py import_popularity [--window-days 28] [--end YYYY-MM-DD] [--user ID]
                                       [--no-classifications] [--dry-run] [--json]

Fetches the 28 complete UTC days ending ``--end`` (default: yesterday) in
7-day chunks (4 requests; the Data API allows 30/min, 500/day), the public
``/models`` index and the 7-day ``classifications/task`` (code tags). The key
is the stored OpenRouter credential of ``--user`` (default 1); it is never
printed and is sent only to openrouter.ai. Every request has a timeout and
retries 429/5xx with backoff.

Without ``--dry-run`` the raw responses are kept in ``SourceSnapshot`` and
``metadata["popularity"]["openrouter"]`` is written on each canonical model's
anchor row (stale records elsewhere are removed). ``--dry-run`` fetches and
computes but writes nothing.
"""

from __future__ import annotations

import datetime as dt
import json

from django.core.management.base import BaseCommand
from django.core.management.base import CommandError
from django.db import transaction
from django.utils import timezone

from backend.rankings.services.constants import POPULARITY_WINDOW_DAYS
from backend.rankings.services.frame import build_frame
from backend.rankings.services.frame import load_rows
from backend.rankings.services.metadata import set_metadata
from backend.rankings.services.openrouter_api import CITATION
from backend.rankings.services.openrouter_api import LICENSE
from backend.rankings.services.openrouter_api import OpenRouterError
from backend.rankings.services.openrouter_api import fetch_models_index
from backend.rankings.services.openrouter_api import fetch_rankings_daily
from backend.rankings.services.openrouter_api import fetch_task_classifications
from backend.rankings.services.openrouter_api import openrouter_key
from backend.rankings.services.openrouter_api import trim_models_index
from backend.rankings.services.popularity import compute_openrouter_popularity
from backend.rankings.services.popularity import payload_sha256


class Command(BaseCommand):
    help = "Import OpenRouter Data API usage as the MSS v2 popularity input."

    def add_arguments(self, parser):
        parser.add_argument("--window-days", type=int, default=POPULARITY_WINDOW_DAYS)
        parser.add_argument("--end", default=None, help="Last day of the window (default: yesterday, UTC).")
        parser.add_argument("--user", type=int, default=1, help="User whose stored OpenRouter key is used.")
        parser.add_argument("--no-classifications", action="store_true", help="Skip classifications/task.")
        parser.add_argument("--dry-run", action="store_true", help="Fetch and compute, write nothing.")
        parser.add_argument("--json", action="store_true", help="Print the computed records as JSON.")

    def handle(self, *args, **options):
        end = dt.date.fromisoformat(options["end"]) if options["end"] else timezone.now().date() - dt.timedelta(days=1)
        start = end - dt.timedelta(days=options["window_days"] - 1)
        key = openrouter_key(options["user"])
        if not key:
            msg = f"user {options['user']} has no stored OpenRouter key"
            raise CommandError(msg)
        fetched_at = timezone.now()
        try:
            chunks = fetch_rankings_daily(start, end, key=key)
            index_raw = fetch_models_index()
            classifications = None if options["no_classifications"] else fetch_task_classifications(key=key)
        except OpenRouterError as exc:
            raise CommandError(str(exc)) from exc
        models_index = trim_models_index(index_raw["body"])

        rows = load_rows()
        frame = build_frame(rows, end + dt.timedelta(days=1), include_previews=True)
        records, market = compute_openrouter_popularity(
            chunks,
            candidates=frame["candidates"],
            models_index=models_index,
            window=(start, end),
            fetched_at=fetched_at.isoformat(),
            classifications=classifications,
        )
        by_canon = {e["canonical_id"]: e for e in frame["candidates"]}
        if options["json"]:
            self.stdout.write(json.dumps({"records": records, "market": market}, indent=2, default=str))
        else:
            self._summary(records, market, start, end)
        if options["dry_run"]:
            self.stdout.write(
                self.style.SUCCESS(f"DRY RUN — would write {sum(c in by_canon for c in records)} records"),
            )
            return

        from backend.rankings.models import SourceSnapshot

        as_of = market.get("as_of") or ""
        citation = CITATION.format(as_of=as_of or fetched_at.isoformat())
        with transaction.atomic():
            ids = []
            for chunk in chunks:
                snap = SourceSnapshot.objects.create(
                    source=SourceSnapshot.Source.RANKINGS_DAILY,
                    fetched_at=fetched_at,
                    as_of=str((chunk["body"].get("meta") or {}).get("as_of") or ""),
                    window_start=dt.date.fromisoformat(chunk["params"]["start_date"]),
                    window_end=dt.date.fromisoformat(chunk["params"]["end_date"]),
                    params=chunk["params"],
                    payload=chunk["body"],
                    sha256=payload_sha256(chunk["body"]),
                    license=LICENSE,
                    citation=citation,
                )
                ids.append(snap.pk)
            if classifications is not None:
                body = classifications["body"]
                ids.append(
                    SourceSnapshot.objects.create(
                        source=SourceSnapshot.Source.TASK_CLASSIFICATIONS,
                        fetched_at=fetched_at,
                        as_of=str((body.get("data") or {}).get("as_of") or ""),
                        params=classifications["params"],
                        payload=body,
                        sha256=payload_sha256(body),
                        license=LICENSE,
                        citation=citation,
                    ).pk,
                )
            ids.append(
                SourceSnapshot.objects.create(
                    source=SourceSnapshot.Source.MODELS_INDEX,
                    fetched_at=fetched_at,
                    params={},
                    payload={"data": models_index},
                    sha256=payload_sha256(models_index),
                    license="",
                    citation="https://openrouter.ai/api/v1/models (public)",
                ).pk,
            )
            market["source_snapshot_ids"] = list(ids)
            SourceSnapshot.objects.create(
                source=SourceSnapshot.Source.POPULARITY_MARKET,
                fetched_at=fetched_at,
                as_of=as_of,
                window_start=start,
                window_end=end,
                params={"window_days": options["window_days"]},
                payload=market,
                sha256=payload_sha256(market),
                license=LICENSE,
                citation=citation,
            )
            written = set()
            for canon, record in records.items():
                entry = by_canon.get(canon)
                if entry is None:
                    continue
                record["source_snapshot_ids"] = ids
                pk = next(r["pk"] for r in rows if r["model_id"] == entry["anchor_model_id"])
                set_metadata(pk, "popularity", record, subkey="openrouter")
                written.add(pk)
            stale = [
                r["pk"]
                for r in rows
                if r["pk"] not in written and (r["metadata"].get("popularity") or {}).get("openrouter")
            ]
            for pk in stale:
                set_metadata(pk, "popularity", None, subkey="openrouter")
        self.stdout.write(
            self.style.SUCCESS(
                f"wrote {len(written)} popularity records, removed {len(stale)} stale, snapshots {ids}",
            ),
        )

    def _summary(self, records, market, start, end) -> None:
        self.stdout.write(
            f"window {start} .. {end}; as_of {market.get('as_of')}; {len(market.get('days') or {})} days returned",
        )
        top = sorted(records.items(), key=lambda kv: -(kv[1]["volume_share"] or 0))[:40]
        for canon, r in top:
            self.stdout.write(
                f"  {canon:48s} vol={100 * (r['volume_share'] or 0):7.3f}%  listed"
                f" {r['days_listed']}/{r['days_available']}"
                f"  censored {r['days_censored']}",
            )
