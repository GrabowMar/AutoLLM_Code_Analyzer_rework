"""``data/tasks/vocab.yaml`` (spec 11.3): the closed feature list, the 60 CWE surfaces, probe tags."""

from __future__ import annotations

from typing import get_args

from backend.generation.contracts.library import ProbeKind
from backend.generation.tests.wp04_content import content

SPEC_FEATURES = {
    "auth", "ownership", "roles", "admin", "pagination", "search", "filter", "sort", "upload-multipart",
    "upload-base64", "download", "render-html", "render-markdown", "redirect", "outbound-http", "xml-parse",
    "websocket", "sse", "csv-export", "password-reset", "email-mock", "rate-limit", "csrf", "webhook-signature",
    "scheduling", "money", "geo", "realtime",
}  # fmt: skip


def test_features_are_exactly_the_spec_list():
    assert set(content.vocabulary()["features"]) == SPEC_FEATURES
    assert all(item["description"] for item in content.vocabulary()["features"].values())


def test_surfaces_are_sixty_cwe_ids_with_names():
    surfaces = content.vocabulary()["surfaces"]
    assert len(surfaces) == 60
    for key, item in surfaces.items():
        assert key.startswith("cwe-"), key
        assert key[4:].isdigit(), key
        assert not key[4:].startswith("0"), key
        assert item["name"]


def test_probe_requires_covers_every_probe_kind_with_known_tags():
    vocab = content.vocabulary()
    requires = vocab["probe_requires"]
    assert set(requires) == set(get_args(ProbeKind))
    for kind, tags in requires.items():
        assert set(tags["features_any"]) <= set(vocab["features"]), kind
        assert set(tags["surfaces_any"]) <= set(vocab["surfaces"]), kind


def test_wp03_reads_the_vocabulary():
    from backend.generation.library import lint as library_lint

    vocab = library_lint.load_vocabulary(content.DATA_ROOT)
    assert not vocab.fallback
    assert set(vocab.features) == SPEC_FEATURES
    assert vocab.surfaces is not None
    assert set(vocab.surfaces) == set(content.vocabulary()["surfaces"])
