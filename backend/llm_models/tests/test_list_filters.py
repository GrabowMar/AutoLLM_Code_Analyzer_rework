"""List filters of ``GET /api/models/`` built on catalog metadata (capabilities, modality, weights, ...)."""

import time

import pytest

from backend.llm_models.tests.factories import LLMModelFactory

DAY = 86_400


@pytest.fixture
def api_client(client, user):
    client.force_login(user)
    return client


def _model(slug: str, **kw):
    kw.setdefault("metadata", {})
    return LLMModelFactory(model_id=f"p/{slug}", canonical_slug=slug, model_name=kw.pop("name", slug), **kw)


def _slugs(api_client, query: str) -> set[str]:
    resp = api_client.get(f"/api/models/?per_page=200&{query}")
    assert resp.status_code == 200, resp.content
    return {item["canonical_slug"] for item in resp.json()["items"]}


@pytest.mark.django_db
class TestCapabilityFilter:
    def test_comma_list_requires_every_capability(self, api_client):
        _model("both", supports_vision=True, supports_function_calling=True)
        _model("vision_only", supports_vision=True, supports_function_calling=False)
        assert _slugs(api_client, "capability=vision,function_calling") == {"both"}

    def test_catalog_parameters_seed_logprobs_structured(self, api_client):
        _model("seeded", metadata={"openrouter_supported_parameters": ["seed", "logprobs"]})
        _model("plain", metadata={"openrouter_supported_parameters": ["temperature"]})
        assert _slugs(api_client, "capability=seed") == {"seeded"}
        assert _slugs(api_client, "capability=seed,logprobs") == {"seeded"}
        assert _slugs(api_client, "capability=structured_outputs") == set()

    def test_reasoning_matches_the_table_rule(self, api_client):
        _model("or_reasoning", metadata={"openrouter_supported_parameters": ["include_reasoning"]})
        _model("ollama_thinking", metadata={"ollama": {"capabilities": ["completion", "thinking"]}})
        _model("no_reasoning", metadata={"openrouter_supported_parameters": ["tools"]})
        assert _slugs(api_client, "capability=reasoning") == {"or_reasoning", "ollama_thinking"}

    def test_unknown_capability_is_ignored(self, api_client):
        _model("a")
        assert _slugs(api_client, "capability=telepathy") == {"a"}


@pytest.mark.django_db
class TestMetadataFilters:
    def test_input_modality_all_must_hold(self, api_client):
        _model("img_audio", metadata={"architecture_input_modalities": ["text", "image", "audio"]})
        _model("img", metadata={"architecture_input_modalities": ["text", "image"]})
        _model("text", metadata={"architecture_input_modalities": ["text"]})
        assert _slugs(api_client, "modality=image") == {"img_audio", "img"}
        assert _slugs(api_client, "modality=image,audio") == {"img_audio"}

    def test_weights_open_closed_and_unknown(self, api_client):
        _model("open", metadata={"models_dev": {"open_weights": True}})
        _model("closed", metadata={"models_dev": {"open_weights": False}})
        _model("unknown")
        assert _slugs(api_client, "weights=open") == {"open"}
        assert _slugs(api_client, "weights=closed") == {"closed"}

    def test_released_windows(self, api_client):
        now = int(time.time())
        _model("fresh", metadata={"openrouter_created": now - 5 * DAY})
        _model("quarter", metadata={"openrouter_created": now - 60 * DAY})
        _model("old", metadata={"openrouter_created": now - 400 * DAY})
        _model("undated")
        assert _slugs(api_client, "released=30d") == {"fresh"}
        assert _slugs(api_client, "released=90d") == {"fresh", "quarter"}
        assert _slugs(api_client, "released=older") == {"old"}

    def test_tokenizer_family(self, api_client):
        _model("qwen", metadata={"architecture_tokenizer": "Qwen3"})
        _model("gpt", metadata={"architecture_tokenizer": "GPT"})
        assert _slugs(api_client, "tokenizer=Qwen3") == {"qwen"}

    def test_max_output_range_skips_unreported(self, api_client):
        _model("small", max_output_tokens=4_096)
        _model("big", max_output_tokens=200_000)
        _model("unreported", max_output_tokens=0)
        assert _slugs(api_client, "max_output_range=small") == {"small"}
        assert _slugs(api_client, "max_output_range=xlarge") == {"big"}

    def test_coverage_requires_each_source(self, api_client):
        _model("both", metadata={"epoch": {"model": "x"}, "lmarena": {"names": ["x"]}})
        _model("epoch", metadata={"epoch": {"model": "y"}})
        _model("none")
        assert _slugs(api_client, "coverage=epoch") == {"both", "epoch"}
        assert _slugs(api_client, "coverage=epoch,lmarena") == {"both"}

    def test_hide_variants_drops_batch_and_routers(self, api_client):
        _model("base", name="Claude Sonnet 5")
        _model("batch", name="Claude Sonnet 5 (batch)")
        _model("router", name="Auto Router", metadata={"architecture_tokenizer": "Router"})
        _model("tokenized", name="Qwen 3", metadata={"architecture_tokenizer": "Qwen3"})
        assert _slugs(api_client, "hide_variants=true") == {"base", "tokenized"}
        assert _slugs(api_client, "") == {"base", "batch", "router", "tokenized"}


@pytest.mark.django_db
class TestFacets:
    def test_providers_and_tokenizers_with_counts(self, api_client):
        _model("a1", provider="alpha", metadata={"architecture_tokenizer": "GPT"})
        _model("a2", provider="alpha", metadata={"architecture_tokenizer": "GPT"})
        _model("b1", provider="beta", metadata={"architecture_tokenizer": "Router"})
        data = api_client.get("/api/models/facets/").json()
        assert {"value": "alpha", "count": 2} in data["providers"]
        assert {"value": "beta", "count": 1} in data["providers"]
        # Routers are pseudo-models, not a tokenizer family.
        assert data["tokenizers"] == [{"value": "GPT", "count": 2}]


@pytest.mark.django_db
class TestListColumnFacts:
    def test_facts_from_metadata(self, api_client):
        _model(
            "rich",
            metadata={
                "openrouter_created": 1_767_225_600,  # 2026-01-01
                "models_dev": {"open_weights": True},
                "architecture_tokenizer": "Qwen3",
                "architecture_input_modalities": ["text", "image"],
                "huggingface": {"parameters": 8_000_000_000},
                "epoch": {"parameters": 7_000_000_000},
                "lmarena": {"names": ["x"]},
            },
        )
        item = api_client.get("/api/models/?search=rich").json()["items"][0]
        assert item["released_at"] == "2026-01-01"
        assert item["open_weights"] is True
        assert item["tokenizer"] == "Qwen3"
        assert item["input_modalities"] == ["text", "image"]
        # Exact Hugging Face count wins over Epoch's reported figure.
        assert (item["parameters"], item["parameters_source"]) == (8_000_000_000, "huggingface")
        assert item["research_data"] == ["lmarena", "epoch", "huggingface"]

    def test_empty_metadata_gives_empty_facts(self, api_client):
        _model("bare")
        item = api_client.get("/api/models/?search=bare").json()["items"][0]
        assert item["released_at"] is None
        assert item["open_weights"] is None
        assert item["parameters"] is None
        assert item["research_data"] == []

    def test_released_sort_puts_undated_last(self, api_client):
        _model("old", metadata={"openrouter_created": 1_600_000_000})
        _model("new", metadata={"openrouter_created": 1_760_000_000})
        _model("undated")
        for direction in ("asc", "desc"):
            items = api_client.get(f"/api/models/?sort_by=released&sort_dir={direction}").json()["items"]
            assert items[-1]["canonical_slug"] == "undated"
