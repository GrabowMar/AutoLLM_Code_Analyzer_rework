"""Manual coding engine (spec section 7.9 step 3): cluster labels apply to every member of an exact cluster.

A ``ClusterLabel`` codes one exact cluster key (``normalized_sha``) of one target for one ``manual``
detector, per labeler. Labels are stable across PatternRuns (the key is the normalized code, not a cluster
row) and never alter an existing PatternRun: they are read when a run executes (``detectors.run_manual``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from backend.generation import identity

if TYPE_CHECKING:
    from backend.generation.models import ClusterLabel
    from backend.generation.models import PatternDetector
    from backend.generation.models import SlotTarget

LABEL_VALUES = ("present", "absent", "unsure")


def label_cluster(
    *,
    user: Any,
    slot_target: SlotTarget,
    normalized_sha: str,
    detector: PatternDetector,
    value: str,
    note: str = "",
) -> ClusterLabel:
    """Create or replace ``user``'s code for the cluster key (one label per labeler, target, key, detector)."""
    from backend.generation.models import ClusterLabel

    if detector.engine != "manual":
        raise ValidationError({"detector": "cluster labels use a detector with engine 'manual'"})
    if value not in LABEL_VALUES:
        raise ValidationError({"value": f"value must be one of {', '.join(LABEL_VALUES)}"})
    if not identity.SHA256_RE.match(normalized_sha or ""):
        raise ValidationError({"normalized_sha": "an exact cluster key (a normalized sha) is required"})
    with transaction.atomic():
        label, _created = ClusterLabel.objects.select_for_update().get_or_create(
            slot_target=slot_target,
            normalized_sha=normalized_sha,
            detector=detector,
            labeler=user,
            defaults={"value": value, "note": note},
        )
        if not _created and (label.value != value or label.note != note):
            label.value = value
            label.note = note
            label.full_clean()
            label.save(update_fields=["value", "note"])
        elif _created:
            label.full_clean()
    return label


def labels_by_key(target_ids: list[str]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """``{(target id, normalized sha): [{detector, feature_key, value, labeler, note}]}`` for display."""
    from backend.generation.models import ClusterLabel

    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    rows = ClusterLabel.objects.filter(slot_target_id__in=target_ids).select_related("detector", "labeler")
    for row in rows.order_by("created_at", "pk"):
        out.setdefault((str(row.slot_target_id), row.normalized_sha), []).append(
            {
                "detector": row.detector.pinned_ref,
                "feature_key": row.detector.feature_key,
                "value": row.value,
                "labeler": row.labeler.get_username(),
                "note": row.note,
                "coding": "manual coding",
            },
        )
    return out
