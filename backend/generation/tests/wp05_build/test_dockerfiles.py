"""FROM parsing, the free-choice allowlist and base image pins (spec 6.10, 11.4; ``base_not_allowed``)."""

from __future__ import annotations

import pytest

from backend.generation import identity
from backend.generation.build import dockerfiles

PY_DIGEST = "sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1"
NODE_DIGEST = "sha256:c86ecead9e55a5210ac1727de38a4ca840bf03dcaf34dc9749addbf383b1920c"


def test_base_images_are_all_digest_pinned_debian_family():
    images = dockerfiles.base_images()
    assert {"python", "node"} <= set(images)
    for spec in images.values():
        assert spec.digest.startswith("sha256:")
        assert len(spec.digest) == 71
        assert "alpine" not in spec.ref
    assert images["python"].pinned == f"python:3.13-slim@{PY_DIGEST}"


def test_allowed_bases_record_is_stable_and_commits_to_digests(tmp_path, settings):
    record = dockerfiles.allowed_bases_record()
    assert record["tags"][:2] == ["python:3.13-slim", "node:24-slim"]
    assert record["refs"][0] == f"python:3.13-slim@{PY_DIGEST}"  # rendered and frozen digest-pinned
    assert all("@sha256:" in ref for ref in record["refs"])
    assert record == dockerfiles.allowed_bases_record()
    custom = tmp_path / "allowed.yaml"
    custom.write_text(f'bases:\n  - {{ref: "python:3.13-slim", digest: "{PY_DIGEST}"}}\n')
    settings.SAMPLE_BUILD_ALLOWED_BASES = str(custom)
    one = dockerfiles.allowed_bases_record()
    assert one["refs"] == [f"python:3.13-slim@{PY_DIGEST}"]
    assert one["sha"] == identity.H([{"ref": "python:3.13-slim", "digest": PY_DIGEST}])
    custom.write_text(f'bases:\n  - {{ref: "python:3.13-slim", digest: "sha256:{"1" * 64}"}}\n')
    assert dockerfiles.allowed_bases_record()["sha"] != one["sha"]


def test_allowlist_rejects_duplicates_bad_digests_and_empty(tmp_path):
    path = tmp_path / "a.yaml"
    path.write_text('bases:\n  - {ref: "python:3.13-slim", digest: "sha256:abc"}\n')
    with pytest.raises(ValueError, match="digest"):
        dockerfiles.allowed_bases(path)
    path.write_text(
        f'bases:\n  - {{ref: "python:3.13-slim", digest: "{PY_DIGEST}"}}\n'
        f'  - {{ref: "docker.io/library/python:3.13-slim", digest: "{PY_DIGEST}"}}\n',
    )
    with pytest.raises(ValueError, match="duplicate"):
        dockerfiles.allowed_bases(path)
    path.write_text("bases: []\n")
    with pytest.raises(ValueError, match="no allowed"):
        dockerfiles.allowed_bases(path)


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        ("python:3.13-slim", ("python:3.13-slim", None)),
        ("docker.io/library/python:3.13-slim", ("python:3.13-slim", None)),
        (f"python:3.13-slim@{PY_DIGEST}", ("python:3.13-slim", PY_DIGEST)),
        ("node", ("node:latest", None)),
        ("ghcr.io/org/img:1.2", ("ghcr.io/org/img:1.2", None)),
        ("localhost:5000/img", ("localhost:5000/img:latest", None)),
    ],
)
def test_normalize_image(image, expected):
    assert dockerfiles.normalize_image(image) == expected


def test_parse_from_lines_handles_flags_stages_args_and_continuations():
    text = (
        "# syntax comment\n"
        "ARG BASE=python:3.13-slim\n"
        "from --platform=linux/amd64 node:24-slim AS Build\n"
        "RUN echo hi \\\n"
        "  # inner comment\n"
        "  && echo there\n"
        "FROM \\\n  python:3.13-slim\n"
        "FROM build AS again\n"
        "FROM ${BASE}\n"
    )
    lines = dockerfiles.parse_from_lines(text)
    assert [(line.line, line.ref, line.stage, line.is_stage_ref, line.unresolvable) for line in lines] == [
        (3, "node:24-slim", "Build", False, False),
        (7, "python:3.13-slim", None, False, False),
        (9, None, "again", True, False),
        (10, None, None, False, True),
    ]


def test_check_bases_verdicts():
    ok = dockerfiles.check_bases("FROM python:3.13-slim AS b\nFROM b\nFROM node:24-slim\n")
    assert ok.ok
    assert not ok.violations
    assert next(b["expected_digest"] for b in ok.bases if not b.get("stage_ref")) == PY_DIGEST
    pinned = dockerfiles.check_bases(f"FROM python:3.13-slim@{PY_DIGEST}\n")
    assert pinned.ok
    cases = {
        "FROM alpine:3.22\n": "not_allowlisted",
        f"FROM python:3.13-slim@sha256:{'1' * 64}\n": "digest_mismatch",
        "ARG B\nFROM ${B}\n": "unresolvable",
        "RUN echo no from\n": "no_from",
        "FROM python:3.12-slim\n": "not_allowlisted",
    }
    for text, reason in cases.items():
        check = dockerfiles.check_bases(text)
        assert not check.ok, text
        assert [v["reason"] for v in check.violations] == [reason], text


def test_pinned_bases_dedupes_and_refuses_unpinned():
    text = f"FROM python:3.13-slim@{PY_DIGEST} AS a\nFROM python:3.13-slim@{PY_DIGEST}\nFROM a\n"
    assert dockerfiles.pinned_bases(text) == [
        {"ref": "python:3.13-slim", "digest": f"python:3.13-slim@{PY_DIGEST}"},
    ]
    with pytest.raises(ValueError, match="not digest-pinned"):
        dockerfiles.pinned_bases("FROM python:3.13-slim\n")


def test_tag_only_allowlisted_from_is_pinned_to_the_allowlisted_digest():
    """Appendix A #1 / 6.10: the build never re-resolves a tag (BuildKit named contexts pin it)."""
    text = f"FROM docker.io/library/python:3.13-slim AS app\nFROM node:24-slim@{NODE_DIGEST}\nFROM app\n"
    check = dockerfiles.check_bases(text)
    assert check.ok
    assert check.pins == {"python:3.13-slim": f"python:3.13-slim@{PY_DIGEST}"}  # the pinned FROM needs none
    assert dockerfiles.build_context_args(check.pins) == [
        "--build-context",
        f"python:3.13-slim=docker-image://python:3.13-slim@{PY_DIGEST}",
    ]
    assert check.to_dict()["pins"] == check.pins


def test_frozen_refs_round_trip_and_are_verified():
    record = dockerfiles.allowed_bases_record()
    frozen = dockerfiles.allowed_bases_from_refs(record["refs"], sha=record["sha"])
    assert [base.pinned for base in frozen] == record["refs"]
    with pytest.raises(ValueError, match="digest"):
        dockerfiles.allowed_bases_from_refs(record["tags"])
    with pytest.raises(ValueError, match="sha"):
        dockerfiles.allowed_bases_from_refs(record["refs"][:1], sha=record["sha"])
