"""MSS v2 sampling frame: canonicalisation (K1-K6) and eligibility (E1-E7). Pure, no database."""

from __future__ import annotations

import pytest

from backend.rankings.services.frame import build_frame
from backend.rankings.services.frame import canonical_id
from backend.rankings.services.frame import canonical_of
from backend.rankings.services.frame import ga_sibling
from backend.rankings.services.frame import is_prerelease_id
from backend.rankings.services.frame import line_key
from backend.rankings.services.frame import strip_date
from backend.rankings.tests.mss_helpers import AS_OF
from backend.rankings.tests.mss_helpers import CONFIG
from backend.rankings.tests.mss_helpers import anthropic_row
from backend.rankings.tests.mss_helpers import eligible_row
from backend.rankings.tests.mss_helpers import go_row
from backend.rankings.tests.mss_helpers import models_dev
from backend.rankings.tests.mss_helpers import row


def _frame(rows, **kwargs):
    return build_frame(rows, AS_OF, config=CONFIG, **kwargs)


def _by_id(frame):
    return {e["canonical_id"]: e for e in frame["candidates"]}


def _step(frame, name):
    return next(s for s in frame["flow"] if s["step"] == name)


# ── ids ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("deepseek/deepseek-v4.1-flash-1017", "deepseek/deepseek-v4.1-flash"),
        ("deepseek/deepseek-v4-pro-0813", "deepseek/deepseek-v4-pro"),
        ("anthropic/claude-opus-5.5-20260921", "anthropic/claude-opus-5.5"),
        ("x/y-2026-09-01", "x/y"),
        ("z-ai/glm-5.3:free", "z-ai/glm-5.3"),
        ("mistralai/mistral-large-2407", "mistralai/mistral-large-2407"),  # month 24 is not a date
        ("qwen/qwen3.8-max-0902", "qwen/qwen3.8-max"),
    ],
)
def test_canonical_id_strips_variants_and_dates(model_id, expected):
    assert canonical_id(model_id) == expected


def test_strip_date_reports_the_date_token():
    assert strip_date("deepseek/deepseek-v4.1-flash-1017") == ("deepseek/deepseek-v4.1-flash", "1017")
    assert strip_date("a/b") == ("a/b", None)


@pytest.mark.parametrize(
    ("canonical", "expected"),
    [
        ("anthropic/claude-opus-5.5", "claude-opus"),
        ("z-ai/glm-5.3-flash", "glm-flash"),
        ("openai/gpt-6-luna", "gpt-luna"),
        ("moonshotai/kimi-k3.1-pro", "kimi-pro"),
        ("qwen/qwen3.8-max", "qwen-max"),
        ("nvidia/nemotron-3-ultra-550b-a55b", "nemotron-ultra-550b-a55b"),
        ("z-ai/glm-5.3", "glm"),
        ("z-ai/glm-5.2", "glm"),
    ],
)
def test_line_key_examples(canonical, expected):
    assert line_key(canonical) == expected


def test_line_key_override():
    assert line_key("z-ai/glm-5.3", {"z-ai/glm-5.3": "special"}) == "special"


def test_prerelease_ids_and_ga_sibling():
    assert is_prerelease_id("tencent/hy4-preview")
    assert is_prerelease_id("stealth/anything")
    assert is_prerelease_id("meta/muse-spark-1.3-contributor")
    assert not is_prerelease_id("tencent/hy4")
    assert ga_sibling("tencent/hy4-preview") == "tencent/hy4"


# ── K1-K6 ──────────────────────────────────────────────────────────────────


def test_routers_aliases_and_batch_rows_are_dropped():
    frame = _frame(
        [
            row("openrouter/auto"),
            row("~anthropic/claude-latest"),
            row("openai/gpt-latest"),
            row("z-ai/glm-5.3:batch"),
            eligible_row("z-ai/glm-5.3"),
        ],
    )
    k1 = _step(frame, "K1")
    assert {e["code"] for e in k1["excluded"]} == {"router", "alias", "batch_api"}
    assert k1["remaining"] == 1
    assert list(_by_id(frame)) == ["z-ai/glm-5.3"]


def test_free_variant_merges_and_free_only_is_excluded_at_e5():
    frame = _frame(
        [
            eligible_row("z-ai/glm-5.3"),
            row("z-ai/glm-5.3:free", free=True),
            eligible_row("x/free-only:free", free=True),
        ],
    )
    by = _by_id(frame)
    assert {m["model_id"] for m in by["z-ai/glm-5.3"]["members"]} == {"z-ai/glm-5.3", "z-ai/glm-5.3:free"}
    assert by["z-ai/glm-5.3"]["eligible"]
    assert by["x/free-only"]["exclusion"]["code"] == "free_only"
    assert by["x/free-only"]["exclusion"]["step"] == "E5"


def test_dated_snapshots_collapse_and_the_newest_is_pinned():
    frame = _frame(
        [
            eligible_row(
                "deepseek/deepseek-v4.1-flash",
                canonical_slug="deepseek/deepseek-v4.1-flash-20260910",
                created=1789021285,
            ),
            eligible_row(
                "deepseek/deepseek-v4.1-flash-1017",
                canonical_slug="deepseek/deepseek-v4.1-flash-20261017",
                created=1792225624,
            ),
        ],
    )
    entry = _by_id(frame)["deepseek/deepseek-v4.1-flash"]
    assert len(entry["members"]) == 2
    assert entry["pinned_version"]["model_id"] == "deepseek/deepseek-v4.1-flash-1017"
    assert entry["pinned_version"]["permaslug"] == "deepseek/deepseek-v4.1-flash-20261017"
    # The anchor (where imported inputs live) is the undated row.
    assert entry["anchor_model_id"] == "deepseek/deepseek-v4.1-flash"


def test_prerelease_merges_into_its_ga_model():
    frame = _frame([eligible_row("tencent/hy4"), eligible_row("tencent/hy4-preview")])
    by = _by_id(frame)
    assert list(by) == ["tencent/hy4"]
    k4 = _step(frame, "K4")
    assert k4["excluded"] == [{"id": "tencent/hy4-preview", "code": "merged_into:tencent/hy4"}]
    assert by["tencent/hy4"]["pinned_version"]["model_id"] == "tencent/hy4"


def test_prerelease_without_ga_is_excluded_unless_previews_are_included():
    rows = [eligible_row("stepfun/step-5-preview")]
    excluded = _by_id(_frame(rows))["stepfun/step-5-preview"]
    assert excluded["exclusion"]["code"] == "prerelease"
    kept = _by_id(_frame(rows, include_previews=True))["stepfun/step-5-preview"]
    assert kept["eligible"]


def test_local_route_rows_map_to_their_twin_and_unlinked_ones_are_reported():
    frame = _frame(
        [
            eligible_row("z-ai/glm-5.3-flash"),
            go_row("glm-5.3-flash", "z-ai/glm-5.3-flash"),
            row("local-opencode-go/orphan", endpoint="opencode-go", served="orphan", free=True),
            row("local-opencode-go/no-twin-but-override", endpoint="opencode-go", served="glm-5.3", free=True),
        ],
    )
    by = _by_id(frame)
    entry = by["z-ai/glm-5.3-flash"]
    assert entry["routes"]["go"]["served_name"] == "glm-5.3-flash"
    # A subscription route row stores price 0 / is_free, yet it is a paid route.
    assert entry["routes"]["go"]["paid"] is True
    assert entry["routes"]["openrouter"]["paid"] is True
    assert frame["unlinked_local"] == [{"id": "local-opencode-go/orphan", "code": "unlinked_local"}]
    # The YAML twins: override links a row without a stored catalog_twin.
    assert "z-ai/glm-5.3" in by
    assert by["z-ai/glm-5.3"]["routes"]["go"]["model_id"] == "local-opencode-go/no-twin-but-override"


def test_local_free_builds_are_prerelease_members():
    frame = _frame([eligible_row("x/beam"), go_row("beam-free", "x/beam")])
    entry = _by_id(frame)["x/beam"]
    go = next(m for m in entry["members"] if m["kind"] == "go")
    assert go["prerelease"] is True
    assert entry["eligible"]  # the GA catalog member still carries it


def test_anthropic_key_route_and_canonical_of():
    rows = [eligible_row("anthropic/claude-opus-5.5"), anthropic_row("claude-opus-5-5", "anthropic/claude-opus-5.5")]
    entry = _by_id(_frame(rows))["anthropic/claude-opus-5.5"]
    assert entry["routes"]["anthropic"]["paid"] is True
    assert canonical_of("local-anthropic/claude-opus-5-5", rows, CONFIG) == "anthropic/claude-opus-5.5"
    assert canonical_of("deepseek/deepseek-v4.1-flash-1017:free", rows, CONFIG) == "deepseek/deepseek-v4.1-flash"


# ── E1-E7 ──────────────────────────────────────────────────────────────────


def test_each_filter_has_its_reason_code_and_flow_count():
    rows = [
        eligible_row("a/ok"),
        eligible_row("a/unavailable", available=False),  # E1
        eligible_row(
            "b/no-tools",
            metadata={"openrouter_supported_parameters": [], "models_dev": models_dev(tools=False)},
        ),  # E2
        eligible_row("c/small", metadata={"models_dev": models_dev(context=32_000)}, context=32_000),  # E3
        eligible_row(
            "d/image",
            metadata={"architecture_input_modalities": ["image"], "architecture_output_modalities": ["image"]},
        ),  # E4
        eligible_row("e/deprecated", metadata={"models_dev": models_dev(status="deprecated")}),  # E5
        eligible_row("e/expiring", expiration_date="2026-12-01"),  # E5 (within 60 days)
        eligible_row("f/old", release="2025-06-01"),  # E6
        eligible_row("g/model-1.0", release="2026-03-01"),  # E7 (superseded by g/model-2.0)
        eligible_row("g/model-2.0", release="2026-10-01"),
    ]
    frame = _frame(rows)
    codes = {e["canonical_id"]: (e["exclusion"] or {}).get("code") for e in frame["candidates"]}
    assert codes == {
        "a/ok": None,
        "a/unavailable": "no_route",
        "b/no-tools": "no_tool_calling",
        "c/small": "context_too_small",
        "d/image": "not_text_chat",
        "e/deprecated": "deprecated",
        "e/expiring": "deprecated",
        "f/old": "too_old",
        "g/model-1.0": "superseded",
        "g/model-2.0": None,
    }
    remaining = {s["step"]: s["remaining"] for s in frame["flow"]}
    assert remaining["K6"] == 10
    assert remaining["E1"] == 9
    assert remaining["E2"] == 8
    assert remaining["E3"] == 7
    assert remaining["E4"] == 6
    assert remaining["E5"] == 4
    assert remaining["E6"] == 3
    assert remaining["E7"] == 2
    e7 = _step(frame, "E7")
    assert e7["excluded"] == [{"id": "g/model-1.0", "code": "superseded_by:g/model-2.0"}]


def test_context_and_tools_come_from_the_twin_or_the_first_party_record_for_local_only_models():
    local = go_row("kimi-k3.1-pro", "moonshotai/kimi-k3.1-pro")
    entry = _by_id(_frame([local]))["moonshotai/kimi-k3.1-pro"]
    assert entry["exclusion"]["code"] == "context_too_small"  # local rows store context 0

    local["metadata"]["list_price"] = {
        "input": 2.0,
        "output": 10.0,
        "first_party_record": {
            "context": 262_144,
            "tool_call": True,
            "release_date": "2026-10-20",
            "open_weights": True,
        },
    }
    entry = _by_id(_frame([local]))["moonshotai/kimi-k3.1-pro"]
    assert entry["eligible"], entry["exclusion"]
    assert entry["release_date"] == "2026-10-20"
    assert entry["open_weights"] is True


def test_curated_exclude_and_include():
    config = type(CONFIG).from_dict(
        {**CONFIG.as_dict(), "exclude": {"a/ok": "test"}, "include": {"s/x-preview": "test"}},
    )
    frame = build_frame([eligible_row("a/ok"), eligible_row("s/x-preview")], AS_OF, config=config)
    by = _by_id(frame)
    assert by["a/ok"]["exclusion"]["code"] == "curated_exclude"
    assert by["s/x-preview"]["eligible"]


def test_lmarena_record_prefers_the_pinned_version():
    from backend.rankings.tests.mss_helpers import lmarena

    rows = [
        eligible_row("d/m", rating=1600, created=1_789_000_000, canonical_slug="d/m-20260910"),
        eligible_row("d/m-1017", rating=1660, created=1_792_000_000, canonical_slug="d/m-20261017"),
    ]
    rows[1]["metadata"]["lmarena"] = lmarena(1660)
    entry = _by_id(_frame(rows))["d/m"]
    assert entry["inputs"]["lmarena_from"] == "d/m-1017"
    assert entry["inputs"]["lmarena"]["categories"]["webdev"]["rating"] == 1660


# ── Review fixes (2026-11-09) ──────────────────────────────────────────────


def test_e4_rejects_image_and_audio_generators_that_also_output_text():
    rows = [
        eligible_row(
            "google/pro-image",
            metadata={
                "architecture_input_modalities": ["text", "image"],
                "architecture_output_modalities": ["image", "text"],
            },
        ),
        eligible_row(
            "openai/audio",
            metadata={"architecture_input_modalities": ["text"], "architecture_output_modalities": ["text", "audio"]},
        ),
        eligible_row(
            "z-ai/vision",
            metadata={"architecture_input_modalities": ["text", "image"], "architecture_output_modalities": ["text"]},
        ),
    ]
    entries = _by_id(_frame(rows))
    assert entries["google/pro-image"]["exclusion"]["code"] == "not_text_chat"
    assert entries["openai/audio"]["exclusion"]["code"] == "not_text_chat"
    assert entries["z-ai/vision"]["eligible"]  # image input is fine, text is the only output


def test_open_weights_come_from_the_ga_version_not_a_merged_preview_or_free_copy():
    from backend.rankings.services.frame import FrameConfig

    rows = [
        # The preview build was open; the GA model (local Go row only, no catalog row) is closed.
        eligible_row("tencent/hy4-preview", open_weights=True),
        row(
            "local-opencode-go/hy4",
            endpoint="opencode-go",
            served="hy4",
            free=True,
            tools=True,
            metadata={"catalog_twin": {"model_id": "tencent/hy4", "provider_record": {"open_weights": False}}},
        ),
        # A stale :free copy says closed; the GA catalog row says open.
        eligible_row("minimax/m-flash:free", open_weights=False, free=True),
        eligible_row("minimax/m-flash", open_weights=True),
    ]
    entries = _by_id(_frame(rows))
    assert entries["tencent/hy4"]["open_weights"] is False
    assert entries["tencent/hy4"]["open_weights_source"].startswith(
        "catalog_twin.provider_record:local-opencode-go/hy4",
    )
    assert entries["minimax/m-flash"]["open_weights"] is True
    assert entries["minimax/m-flash"]["open_weights_source"] == "models.dev:minimax/m-flash"
    # A curated label wins over every member record.
    config = FrameConfig.from_dict({**CONFIG.as_dict(), "open_weights": {"minimax/m-flash": False}})
    entry = _by_id(build_frame(rows, AS_OF, config=config))["minimax/m-flash"]
    assert (entry["open_weights"], entry["open_weights_source"]) == (False, "mss_frame.yaml:open_weights")


def test_curated_exclusion_removes_a_speed_edition():
    from backend.rankings.services.frame import FrameConfig

    config = FrameConfig.from_dict({**CONFIG.as_dict(), "exclude": {"x/pro-ultraspeed": "speed edition of x/pro"}})
    rows = [eligible_row("x/pro"), eligible_row("x/pro-ultraspeed")]
    entries = _by_id(build_frame(rows, AS_OF, config=config))
    assert entries["x/pro"]["eligible"]
    assert entries["x/pro-ultraspeed"]["exclusion"] == {
        "step": "E5",
        "code": "curated_exclude",
        "detail": "speed edition of x/pro",
    }


def test_the_shipped_frame_config_excludes_the_known_speed_editions_and_labels_hy4_closed():
    from backend.rankings.services.frame import load_frame_config

    config = load_frame_config()
    assert {"xiaomi/mimo-v2.6-pro-ultraspeed", "z-ai/glm-5.3-flashx"} <= set(config.exclude)
    assert config.open_weights["tencent/hy4"] is False
    assert config.open_weights["minimax/minimax-m3.1-flash"] is True


def test_e7_successor_without_evidence_does_not_remove_the_line():
    rows = [
        eligible_row("m/large-3", release="2026-03-01"),  # priced and popular
        eligible_row("m/large-4", release="2026-10-01", price=None, share=None, rating=None),  # no evidence yet
        eligible_row("q/q-27b-3.6", release="2026-04-01"),
        eligible_row("q/q-27b-3.8", release="2026-10-01"),  # newer, with evidence: supersedes
    ]
    entries = _by_id(_frame(rows))
    assert entries["m/large-3"]["eligible"]  # kept: its successor cannot be scored
    assert entries["m/large-4"]["exclusion"] is None  # fails E8 later, at scoring time
    assert entries["q/q-27b-3.6"]["exclusion"]["code"] == "superseded"
    assert entries["q/q-27b-3.8"]["eligible"]
