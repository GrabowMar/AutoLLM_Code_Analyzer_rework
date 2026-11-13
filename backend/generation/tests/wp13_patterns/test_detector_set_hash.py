"""``PatternRun.detector_set_hash`` is defined in ``identity`` and derived by the model (WP-01 follow-up #7)."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from backend.generation import identity
from backend.generation.models import PatternRun
from backend.generation.patterns import detectors as det
from backend.generation.testing.factories import PatternDetectorFactory
from backend.generation.testing.factories import StudyFactory

A_HASH = "a" * 64
B_HASH = "b" * 64


def test_identity_hash_formula_is_pinned():
    expected = identity.H({"version": 1, "detectors": [["x@1", A_HASH], ["y@2", B_HASH]]})
    assert identity.detector_set_hash([("y@2", B_HASH), ("x@1", A_HASH)]) == expected
    # pinned refs reduce to slug@version, duplicates collapse
    assert identity.detector_set_hash([("x@1#aaaaaaaaaaaa", A_HASH), ("y@2", B_HASH), ("x@1", A_HASH)]) == expected
    assert identity.detector_set_hash([("x@1", B_HASH), ("y@2", B_HASH)]) != expected


@pytest.mark.parametrize(("ref", "content_hash"), [("x", A_HASH), ("x@0", A_HASH), ("x@1", "short")])
def test_identity_hash_rejects_bad_inputs(ref, content_hash):
    with pytest.raises(ValueError, match=r"."):
        identity.detector_set_hash([(ref, content_hash)])


@pytest.mark.django_db
def test_model_derives_the_hash_from_the_pinned_detectors():
    rows = [PatternDetectorFactory(), PatternDetectorFactory()]
    specs = [det.DetectorSpec.from_row(r) for r in rows]
    run = PatternRun.objects.create(
        study=StudyFactory(),
        detectors=[r.pinned_ref for r in reversed(rows)],
        normalizer_version=1,
    )
    assert run.detector_set_hash == det.detector_set_hash(specs)
    assert run.detector_set_hash == identity.detector_set_hash((r.ref, r.content_hash) for r in rows)


@pytest.mark.django_db
def test_model_refuses_a_supplied_hash_that_does_not_match():
    row = PatternDetectorFactory()
    with pytest.raises(ValidationError) as exc:
        PatternRun.objects.create(
            study=StudyFactory(),
            detectors=[row.pinned_ref],
            detector_set_hash=A_HASH,
            normalizer_version=1,
        )
    assert "detector_set_hash" in exc.value.message_dict


@pytest.mark.django_db
def test_model_refuses_unresolvable_detectors():
    row = PatternDetectorFactory()
    with pytest.raises(ValidationError) as exc:
        PatternRun.objects.create(
            study=StudyFactory(),
            detectors=[f"{row.slug}@{row.version}#{'0' * 12}"],
            normalizer_version=1,
        )
    assert "detectors" in exc.value.message_dict
