"""Selection snapshots: create, replay, confirm (spec §5.3).

A selection snapshot stores the full payload of :func:`study_selection.run_selection`
(its inputs included), the parameters, ``inputs_sha256`` and the code version.
``replay_selection`` recomputes the payload from the stored inputs alone and
must reproduce it exactly. ``confirm_selection`` records the researcher's
final list, overrides (each with a reason) and notes, and supersedes any
earlier confirmed selection.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils import timezone

from backend.rankings.models import RankingSnapshot
from backend.rankings.services.constants import MSS_VERSION
from backend.rankings.services.study_selection import inputs_sha256
from backend.rankings.services.study_selection import run_selection

# Payload sections a replay must reproduce exactly.
REPLAYED_SECTIONS = (
    "flow",
    "tiers",
    "floor",
    "candidates",
    "selection",
    "blocked",
    "alternates",
    "constraint_status",
    "sum_optimal",
    "infeasible",
    "sensitivity",
)


RANKINGS_DIR = Path(__file__).resolve().parent.parent


def source_digest() -> str:
    """sha256 over the selection code and its curated data (``services/*.py`` + ``data/mss_frame.yaml``)."""
    digest = hashlib.sha256()
    files = [*sorted((RANKINGS_DIR / "services").glob("*.py")), RANKINGS_DIR / "data" / "mss_frame.yaml"]
    for path in files:
        if path.is_file():
            digest.update(path.relative_to(RANKINGS_DIR).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _git_head() -> str | None:
    """``<sha12>`` or ``<sha12>+dirty`` of the checkout, None where git cannot read it (e.g. in the container)."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=RANKINGS_DIR,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if head.returncode != 0 or not head.stdout.strip():
            return None
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],  # noqa: S607
            cwd=RANKINGS_DIR,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return head.stdout.strip()[:12] + ("+dirty" if dirty.returncode != 0 or dirty.stdout.strip() else "")


def code_version() -> str:
    """What produced a snapshot: the git commit (``GIT_SHA`` or ``git rev-parse``, ``+dirty`` with local
    changes) plus a digest of the selection code and data, which pins it even in an uncommitted tree."""
    git = (os.environ.get("GIT_SHA") or "")[:12] or _git_head()
    src = f"src:{source_digest()[:16]}"
    return f"git:{git} {src}" if git else src


def _normalized(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def create_selection_snapshot(
    payload: dict[str, Any],
    *,
    params: dict[str, Any],
    inputs: dict[str, Any],
    user: Any = None,
) -> RankingSnapshot:
    return RankingSnapshot.objects.create(
        kind=RankingSnapshot.Kind.SELECTION,
        version=MSS_VERSION,
        payload=_normalized(payload),
        params=_normalized({k: v for k, v in params.items() if not k.startswith("_")}),
        inputs_sha256=inputs_sha256(inputs, params),
        status=RankingSnapshot.Status.DRAFT,
        created_by=user if getattr(user, "is_authenticated", False) else None,
        code_version=code_version(),
    )


def replay_selection(snapshot: RankingSnapshot) -> dict[str, Any]:
    """Recompute the stored selection from its stored inputs; ``identical`` says whether it matches."""
    payload = snapshot.payload or {}
    inputs = payload.get("inputs") or {}
    sensitivity = payload.get("sensitivity") is not None
    fresh = _normalized(run_selection(inputs, dict(snapshot.params or {}), sensitivity=sensitivity))
    differences = [s for s in REPLAYED_SECTIONS if fresh.get(s) != payload.get(s)]
    return {
        "identical": not differences,
        "differences": differences,
        "inputs_sha256_matches": inputs_sha256(inputs, dict(snapshot.params or {})) == snapshot.inputs_sha256,
    }


def confirm_selection(
    snapshot: RankingSnapshot,
    user: Any,
    *,
    final_ids: list[str],
    overrides: list[dict[str, Any]] | None = None,
    notes: str = "",
) -> RankingSnapshot:
    """Record the confirmed study selection; earlier confirmations become ``superseded``."""
    if snapshot.kind != RankingSnapshot.Kind.SELECTION:
        msg = "only selection snapshots can be confirmed"
        raise ValueError(msg)
    if snapshot.status == RankingSnapshot.Status.SUPERSEDED:
        msg = "a superseded selection cannot be confirmed"
        raise ValueError(msg)
    selection = (snapshot.payload or {}).get("selection") or {}
    nominal = [p["canonical_id"] for p in selection.get("picks") or []]
    known = {c["canonical_id"] for c in (snapshot.payload or {}).get("candidates") or []}
    final = [str(x) for x in final_ids]
    if len(set(final)) != len(final):
        msg = "final_ids contains duplicates"
        raise ValueError(msg)
    unknown = sorted(set(final) - known)
    if unknown:
        msg = f"unknown model ids: {', '.join(unknown)}"
        raise ValueError(msg)
    clean_overrides = []
    for item in overrides or []:
        reason = str(item.get("reason") or "").strip()
        if not reason:
            msg = "every override needs a reason"
            raise ValueError(msg)
        clean_overrides.append({"replace": item.get("replace"), "with": item.get("with"), "reason": reason})
    changed = (set(final) ^ set(nominal)) if nominal else set(final)
    covered = {o["replace"] for o in clean_overrides} | {o["with"] for o in clean_overrides}
    uncovered = sorted(c for c in changed if c not in covered)
    if nominal and uncovered:
        msg = f"changes without an override and reason: {', '.join(uncovered)}"
        raise ValueError(msg)
    with transaction.atomic():
        RankingSnapshot.objects.filter(
            kind=RankingSnapshot.Kind.SELECTION,
            status=RankingSnapshot.Status.CONFIRMED,
        ).exclude(pk=snapshot.pk).update(status=RankingSnapshot.Status.SUPERSEDED)
        snapshot.status = RankingSnapshot.Status.CONFIRMED
        snapshot.confirmed_at = timezone.now()
        snapshot.confirmed_by = user if getattr(user, "is_authenticated", False) else None
        snapshot.confirmation = {
            "final_ids": final,
            "nominal_ids": nominal,
            "overrides": clean_overrides,
            "notes": notes,
        }
        snapshot.save(update_fields=["status", "confirmed_at", "confirmed_by", "confirmation"])
    return snapshot


def latest_selection() -> RankingSnapshot | None:
    """The confirmed selection if there is one, else the newest draft."""
    qs = RankingSnapshot.objects.filter(kind=RankingSnapshot.Kind.SELECTION)
    return (
        qs.filter(status=RankingSnapshot.Status.CONFIRMED).order_by("-confirmed_at", "-pk").first()
        or qs.filter(status=RankingSnapshot.Status.DRAFT).order_by("-created_at", "-pk").first()
    )
