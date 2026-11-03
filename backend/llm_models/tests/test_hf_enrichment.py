"""Hugging Face enrichment: token resolution, the paced client, parsers, params, twins, sampling usage."""

from __future__ import annotations

from http import HTTPStatus
from io import StringIO
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from requests.structures import CaseInsensitiveDict

from backend.credentials.models import Provider
from backend.credentials.models import UserApiCredential
from backend.generation.tests.factories import GenerationRunFactory
from backend.generation.tests.factories import RunManifestFactory
from backend.generation.tests.factories import run_manifest_content
from backend.inference.models import LLMEndpoint
from backend.llm_models.models import LLMModel
from backend.llm_models.services import hf_metadata
from backend.llm_models.services.enrichment import enrich_model
from backend.llm_models.services.enrichment import fetch_hf_info
from backend.llm_models.services.enrichment import link_local_models
from backend.llm_models.services.hf import STATUS_GATED
from backend.llm_models.services.hf import STATUS_NOT_FOUND
from backend.llm_models.services.hf import STATUS_OK
from backend.llm_models.services.hf import STATUS_RATE_LIMITED
from backend.llm_models.services.hf import HfClient
from backend.llm_models.services.hf import hf_token_for
from backend.llm_models.services.hf import is_hf_url
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

REQUESTS_GET = "backend.llm_models.services.hf.requests.get"
FAKE_TOKEN = "hf_test_not_a_real_token"  # noqa: S105 — test fixture, never a real secret


def _run_with_sampling(user, model, sampling):
    """A run of ``model`` whose frozen manifest records ``sampling`` (policy, forced/stripped params)."""
    run = GenerationRunFactory(created_by=user, model=model)
    manifest = run_manifest_content(run)
    # Keep the run's own pinned sampling ref (run/manifest agreement); a bare ``name@N`` is not a pinned ref.
    ref = (manifest.get("sampling") or {}).get("ref") or "t07@1#0123456789ab"
    manifest["sampling"] = {"ref": ref, "seed_forwarded": True, **sampling}
    RunManifestFactory(run=run, manifest=manifest)
    return run


def _resp(status=200, payload=None, headers=None):
    resp = Mock()
    resp.status_code = status
    resp.headers = CaseInsensitiveDict(headers or {})
    if isinstance(payload, Exception):
        resp.json.side_effect = payload
    else:
        resp.json.return_value = payload
    return resp


def _quiet_client(token=None, **kwargs) -> HfClient:
    return HfClient(token, sleep=lambda _s: None, **kwargs)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


# ── token resolution ────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestTokenResolution:
    def test_user_credential_wins(self, user):
        cred = UserApiCredential(user=user, provider=Provider.HUGGINGFACE)
        cred.set_secret(FAKE_TOKEN)
        cred.save()
        with override_settings(HUGGINGFACE_API_TOKEN="settings-token"):  # noqa: S106
            assert hf_token_for(user) == FAKE_TOKEN

    def test_settings_fallback_then_anonymous(self, user):
        with override_settings(HUGGINGFACE_API_TOKEN="settings-token"):  # noqa: S106
            assert hf_token_for(user) == "settings-token"
            assert hf_token_for(None) == "settings-token"
        with override_settings(HUGGINGFACE_API_TOKEN=""):
            assert hf_token_for(user) is None

    def test_openrouter_credential_is_not_used(self, user):
        cred = UserApiCredential(user=user, provider=Provider.OPENROUTER)
        cred.set_secret("sk-or-other")
        cred.save()
        with override_settings(HUGGINGFACE_API_TOKEN=""):
            assert hf_token_for(user) is None


# ── client ──────────────────────────────────────────────────────────────────


class TestHfClient:
    def test_is_hf_url(self):
        assert is_hf_url("https://huggingface.co/api/models/a/b")
        assert is_hf_url("https://cdn.huggingface.co/x")
        assert not is_hf_url("http://huggingface.co/api")
        assert not is_hf_url("https://huggingface.co.evil.test/api")
        assert not is_hf_url("https://cas-bridge.xethub.hf.co/x")

    @patch(REQUESTS_GET)
    def test_token_only_sent_to_huggingface_hosts(self, mock_get):
        mock_get.side_effect = [
            _resp(307, headers={"Location": "https://cas-bridge.xethub.hf.co/blob?sig=1"}),
            _resp(200, {"ok": True}),
        ]
        client = _quiet_client(FAKE_TOKEN)

        result = client.get_json("https://huggingface.co/a/b/resolve/main/config.json")

        assert result.status == STATUS_OK
        first, second = mock_get.call_args_list
        assert first.kwargs["headers"]["Authorization"] == f"Bearer {FAKE_TOKEN}"
        assert first.kwargs["allow_redirects"] is False
        assert "Authorization" not in second.kwargs["headers"]
        assert second.args[0] == "https://cas-bridge.xethub.hf.co/blob?sig=1"
        assert FAKE_TOKEN not in repr(client)

    @patch(REQUESTS_GET)
    def test_relative_redirect_keeps_token_on_hub(self, mock_get):
        mock_get.side_effect = [
            _resp(307, headers={"Location": "/api/resolve-cache/models/a/b/sha/config.json"}),
            _resp(200, {"model_type": "x"}, headers={"X-Repo-Commit": "abc"}),
        ]
        result = _quiet_client(FAKE_TOKEN).get_json("https://huggingface.co/a/b/resolve/main/config.json")
        assert result.ok
        assert (
            mock_get.call_args_list[1].args[0] == "https://huggingface.co/api/resolve-cache/models/a/b/sha/config.json"
        )
        assert mock_get.call_args_list[1].kwargs["headers"]["Authorization"].startswith("Bearer ")

    @patch(REQUESTS_GET)
    def test_anonymous_client_sends_no_authorization(self, mock_get):
        mock_get.return_value = _resp(200, {})
        _quiet_client(None).get_json("https://huggingface.co/api/models/a/b")
        assert "Authorization" not in mock_get.call_args.kwargs["headers"]

    @patch(REQUESTS_GET)
    def test_429_honours_retry_after_then_succeeds(self, mock_get):
        mock_get.side_effect = [_resp(429, headers={"Retry-After": "7"}), _resp(200, {"id": "a/b"})]
        sleeps: list[float] = []
        client = HfClient(None, sleep=sleeps.append, min_interval=0)

        result = client.get_json("https://huggingface.co/api/models/a/b")

        assert result.ok
        assert 7.0 in sleeps
        assert client.rate_limited == 1

    @patch(REQUESTS_GET)
    def test_429_beyond_max_wait_is_reported_not_slept(self, mock_get):
        mock_get.return_value = _resp(429, headers={"Retry-After": "600"})
        sleeps: list[float] = []
        client = HfClient(None, sleep=sleeps.append, min_interval=0, max_retry_wait=10)

        result = client.get_json("https://huggingface.co/api/models/a/b")

        assert result.status == STATUS_RATE_LIMITED
        assert result.retry_after == 600.0
        assert 600.0 not in sleeps

    @patch(REQUESTS_GET)
    def test_401_403_are_gated_and_repo_not_found_is_not_found(self, mock_get):
        client = _quiet_client(FAKE_TOKEN)
        mock_get.return_value = _resp(403, headers={"X-Error-Code": "GatedRepo"})
        assert client.get_json("https://huggingface.co/x/y/resolve/main/config.json").status == STATUS_GATED
        mock_get.return_value = _resp(401)
        assert client.get_json("https://huggingface.co/x/y/resolve/main/config.json").status == STATUS_GATED
        mock_get.return_value = _resp(401, headers={"X-Error-Code": "RepoNotFound"})
        assert client.get_json("https://huggingface.co/api/models/x/y").status == STATUS_NOT_FOUND
        mock_get.return_value = _resp(404)
        assert client.get_json("https://huggingface.co/x/y/resolve/main/g.json").status == STATUS_NOT_FOUND

    @patch(REQUESTS_GET)
    def test_paces_requests(self, mock_get):
        mock_get.return_value = _resp(200, {})
        now = [100.0]
        sleeps: list[float] = []

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        client = HfClient(FAKE_TOKEN, sleep=sleep, clock=lambda: now[0])
        client.get_json("https://huggingface.co/api/models/a/b")
        client.get_json("https://huggingface.co/api/models/a/c")

        assert sleeps == [pytest.approx(0.35)]

    @patch(REQUESTS_GET)
    def test_low_rate_limit_window_backs_off(self, mock_get):
        mock_get.return_value = _resp(200, {}, headers={"RateLimit": '"api";r=1;t=20'})
        now = [0.0]
        sleeps: list[float] = []

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        client = HfClient(FAKE_TOKEN, sleep=sleep, clock=lambda: now[0])
        client.get_json("https://huggingface.co/api/models/a/b")
        client.get_json("https://huggingface.co/api/models/a/c")

        assert client.last_rate_limit == {"remaining": 1, "reset_seconds": 20}
        assert sleeps
        assert sleeps[-1] == pytest.approx(21.0)


# ── parsers ─────────────────────────────────────────────────────────────────

DEEPSEEK_LIKE_CONFIG = {
    "architectures": ["DeepseekV41ForCausalLM"],
    "model_type": "deepseek_v41",
    "dtype": "bfloat16",
    "quantization_config": {"quant_method": "fp8", "scale_fmt": "ue8m0", "weight_block_size": [32, 32]},
    "text_config": {
        "model_type": "deepseek_v41_text",
        "vocab_size": 129280,
        "hidden_size": 5120,
        "moe_intermediate_size": 2304,
        "num_hidden_layers": 40,
        "num_attention_heads": 64,
        "num_key_value_heads": 1,
        "head_dim": 512,
        "max_position_embeddings": 1048576,
        "rope_theta": 10000,
        "rope_scaling": {"rope_type": "yarn", "factor": 16, "original_max_position_embeddings": 65536},
        "n_routed_experts": 384,
        "n_shared_experts": 1,
        "num_experts_per_tok": 6,
        "num_nextn_predict_layers": 3,
        "engram_layer_ids": [1, 14],
    },
}

QWEN_MOE_CONFIG = {
    "architectures": ["Qwen3MoeForCausalLM"],
    "model_type": "qwen3_moe",
    "decoder_sparse_step": 1,
    "hidden_size": 2048,
    "moe_intermediate_size": 768,
    "num_attention_heads": 32,
    "num_key_value_heads": 4,
    "num_experts": 128,
    "num_experts_per_tok": 8,
    "num_hidden_layers": 48,
    "mlp_only_layers": [],
    "max_position_embeddings": 262144,
    "torch_dtype": "bfloat16",
    "rope_scaling": None,
    "rope_theta": 10000000,
}

GEMMA_LIKE_CONFIG = {
    "architectures": ["Gemma4ForConditionalGeneration"],
    "model_type": "gemma4",
    "tie_word_embeddings": True,
    "text_config": {
        "model_type": "gemma4_text",
        "hidden_size": 5376,
        "intermediate_size": 21504,
        "num_hidden_layers": 4,
        "num_attention_heads": 32,
        "num_key_value_heads": 16,
        "num_experts": None,
        "top_k_experts": None,
        "enable_moe_block": False,
        "layer_types": ["sliding_attention", "sliding_attention", "sliding_attention", "full_attention"],
        "sliding_window": 1024,
        "rope_parameters": {
            "full_attention": {"rope_theta": 1000000.0, "rope_type": "proportional"},
            "sliding_attention": {"rope_theta": 10000.0, "rope_type": "default"},
        },
    },
}


class TestArchitecture:
    def test_nested_text_config_moe_and_rope_scaling(self):
        arch = hf_metadata.parse_architecture(DEEPSEEK_LIKE_CONFIG)
        assert arch["multimodal"] is True
        assert arch["model_type"] == "deepseek_v41"
        assert arch["text_model_type"] == "deepseek_v41_text"
        assert arch["num_hidden_layers"] == 40
        assert arch["is_moe"] is True
        assert arch["moe"]["num_experts"] == 384
        assert arch["moe"]["experts_per_token"] == 6
        assert arch["moe"]["num_shared_experts"] == 1
        assert arch["rope"] == {
            "theta": 10000,
            "scaling_type": "yarn",
            "factor": 16,
            "original_max_position_embeddings": 65536,
        }
        assert arch["native_context"] == 65536
        assert arch["attention"]["variant"] == "MQA"
        assert arch["torch_dtype"] == "bfloat16"
        assert arch["quantization_config"]["quant_method"] == "fp8"
        assert arch["quantization_config"]["fmt"] == "ue8m0"
        assert arch["uncovered_features"] == ["engram"]

    def test_dense_multimodal_with_per_layer_rope(self):
        arch = hf_metadata.parse_architecture(GEMMA_LIKE_CONFIG)
        assert arch["is_moe"] is False
        assert arch["has_expert_keys"] is False
        assert arch["attention"]["variant"] == "GQA"
        assert arch["attention"]["layer_types"] == {"sliding_attention": 3, "full_attention": 1}
        assert arch["rope"]["scaling_type"] == "proportional"
        assert arch["tie_word_embeddings"] is True

    def test_mla_variant(self):
        arch = hf_metadata.parse_architecture({"model_type": "glm", "num_hidden_layers": 2, "kv_lora_rank": 512})
        assert arch["attention"]["variant"] == "MLA"
        assert arch["attention"]["kv_lora_rank"] == 512

    def test_not_a_config(self):
        assert hf_metadata.parse_architecture(None) is None
        assert hf_metadata.parse_architecture({"error": "nope"}) is None


class TestParams:
    def test_name_pattern_with_config_estimate_cross_check(self):
        hf = {
            "id": "Qwen/Qwen3-Coder-30B-A3B-Instruct",
            "parameters": 30_532_122_624,
            "parameters_by_dtype": {"BF16": 30_532_122_624},
            "architecture": hf_metadata.parse_architecture(QWEN_MOE_CONFIG),
        }
        params = hf_metadata.compute_params(hf)
        assert params["active_source"] == "name_pattern"
        assert params["active"] == 3_000_000_000
        assert params["reported_total"] == 30_000_000_000
        # 30.53e9 - 48 x 120 x 3 x 2048 x 768 = 3.353e9 (official: 3.3B)
        assert params["config_estimate"] == 3_353_032_704

    def test_config_estimate_flagged_when_no_name_pattern(self):
        hf = {
            "id": "acme/Coder-MoE",
            "parameters": 30_532_122_624,
            "parameters_by_dtype": {"BF16": 30_532_122_624},
            "architecture": hf_metadata.parse_architecture(QWEN_MOE_CONFIG),
        }
        params = hf_metadata.compute_params(hf)
        assert params["active_source"] == "config_estimate"
        assert params["active"] == params["config_estimate"]
        assert "Estimate" in params["active_note"]

    def test_curated_model_card_entry(self):
        hf = {
            "id": "deepseek-ai/DeepSeek-V4.1-Flash",
            "parameters": 763_205_315_794,
            "parameters_by_dtype": {"I8": 557_171_343_360, "F8_E4M3": 204_015_223_296},
            "architecture": hf_metadata.parse_architecture(DEEPSEEK_LIKE_CONFIG),
        }
        params = hf_metadata.compute_params(hf)
        assert params["active_source"] == "model_card_manual"
        assert params["active"] == 16_000_000_000
        assert params["reported_total"] == 552_000_000_000
        assert params["config_estimate"] is None  # Engram + packed int8: not covered
        assert params["checkpoint_total"] == 763_205_315_794

    def test_uncovered_architecture_stays_null(self):
        hf = {
            "id": "acme/Engram-Model",
            "parameters": 700_000_000_000,
            "parameters_by_dtype": {"BF16": 700_000_000_000},
            "architecture": hf_metadata.parse_architecture(DEEPSEEK_LIKE_CONFIG),
        }
        params = hf_metadata.compute_params(hf)
        assert params["active"] is None
        assert params["active_source"] is None
        assert "engram" in params["active_note"]

    def test_dense_model_uses_checkpoint(self):
        hf = {
            "id": "google/gemma-like",
            "parameters": 31_273_088_876,
            "parameters_by_dtype": {"BF16": 31_273_088_876},
            "architecture": hf_metadata.parse_architecture(GEMMA_LIKE_CONFIG),
        }
        params = hf_metadata.compute_params(hf)
        assert params["active_source"] == "dense"
        assert params["active"] == 31_273_088_876

    def test_gated_config_note_and_closed_weights_mapping(self):
        hf = {"id": "meta-llama/Llama-3.3-70B-Instruct", "parameters": 70_553_706_496, "architecture": None}
        hf["fetch_status"] = {"config": "gated_inaccessible"}
        params = hf_metadata.compute_params(hf, open_weights=False)
        assert params["active"] is None
        assert "gated" in params["active_note"]
        assert "closed-weights" in params["mapping_note"]

    def test_name_pattern_variants(self):
        assert hf_metadata.params_from_name("qwen/qwen3-next-80b-a3b-instruct")["active"] == 3_000_000_000
        total = hf_metadata.params_from_name("Qwen3-235B-A22B")
        assert (total["reported_total"], total["active"]) == (235_000_000_000, 22_000_000_000)
        assert hf_metadata.params_from_name("llama-3.3-70b-instruct") is None
        assert hf_metadata.params_from_name("model-a3b2") is None


class TestFileParsers:
    def test_tree_counts_root_safetensors_and_oids(self):
        tree = [
            {"type": "file", "path": "config.json", "size": 900, "oid": "c1"},
            {"type": "file", "path": "model-00001-of-00002.safetensors", "size": 4_000},
            {"type": "file", "path": "model-00002-of-00002.safetensors", "size": 1_000},
            {"type": "file", "path": "original/consolidated.00.pth", "size": 9_000},
            {"type": "directory", "path": "original"},
        ]
        files = hf_metadata.parse_tree(tree)
        assert files["weight_format"] == "safetensors"
        assert files["weights_bytes"] == 5_000
        assert files["shard_count"] == 2
        assert files["other_weight_formats"] == ["pytorch_pth"]
        assert files["file_oids"] == {"config.json": "c1"}
        assert files["present"] == ["config.json"]

    def test_gguf_alternatives_are_not_summed(self):
        tree = [
            {"type": "file", "path": "m-Q4_K_M.gguf", "size": 10},
            {"type": "file", "path": "m-Q8_0.gguf", "size": 20},
        ]
        files = hf_metadata.parse_tree(tree)
        assert files["weight_format"] == "gguf"
        assert files["weights_bytes"] is None

    def test_generation_config_keeps_sampling_only(self):
        gen = hf_metadata.parse_generation_config(
            {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "eos_token_id": [1], "_from_model_config": True},
            commit="abc",
        )
        assert gen == {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "from_model_config": True, "commit": "abc"}

    def test_tokenizer_fingerprints_template_and_falls_back_to_hub_config(self):
        tok = hf_metadata.parse_tokenizer(
            {
                "tokenizer_class": "Qwen2Tokenizer",
                "model_max_length": 10**30,
                "chat_template": "{{ x }}",
                "eos_token": "<e>",
            },
        )
        assert tok["has_chat_template"] is True
        assert tok["chat_template_source"] == "tokenizer_config.json"
        assert len(tok["chat_template_sha256"]) == 64
        assert tok["model_max_length"] is None
        fallback = hf_metadata.parse_tokenizer(
            None,
            {"tokenizer_config": {"eos_token": {"content": "<eot>"}}, "chat_template_jinja": "{{ y }}"},
        )
        assert fallback["source"] == "hub_config"
        assert fallback["eos_token"] == "<eot>"  # noqa: S105
        assert fallback["chat_template_source"] == "chat_template.jinja"

    def test_eval_results_from_both_formats_are_self_reported(self):
        info = {
            "evalResults": [
                {
                    "verified": False,
                    "data": {
                        "dataset": {"id": "org/bench", "task_id": "pass@1"},
                        "value": 74.2,
                        "source": {"name": "card", "url": "https://x.test"},
                        "notes": "official mini-swe-agent harness",
                    },
                },
                {"data": {"dataset": {"id": "org/no-value"}}},
            ],
            "cardData": {
                "model-index": [
                    {
                        "results": [
                            {
                                "task": {"type": "text-generation"},
                                "dataset": {"name": "HumanEval"},
                                "metrics": [{"type": "pass@1", "value": 0.8}],
                            },
                        ],
                    },
                ],
            },
        }
        results = hf_metadata.hf_eval_results(info)
        assert len(results) == 2
        assert {r["source"] for r in results} == {"model card (self-reported)"}
        assert results[0]["value"] == 74.2
        assert results[0]["notes"] == "official mini-swe-agent harness"
        assert results[1]["dataset"] == "HumanEval"

    def test_record_extras(self):
        extras = hf_metadata.hf_record_extras(
            {
                "sha": "deadbeef",
                "usedStorage": 123,
                "trendingScore": 81,
                "childrenModelCount": {"adapter": 1, "merge": 0, "quantized": 145, "finetune": 25},
                "baseModels": {"relation": "finetune", "models": [{"id": "g/base"}]},
                "inferenceProviderMapping": {
                    "novita": {"status": "live", "providerId": "x", "task": "conversational"},
                },
                "config": {"quantization_config": {"quant_method": "fp8"}},
                "spaces": [f"s{i}" for i in range(100)],
            },
        )
        assert extras["children"]["quantized"] == 145
        assert extras["base_model_relation"] == "finetune"
        assert extras["base_models_expanded"] == ["g/base"]
        assert extras["inference_providers"] == [
            {"provider": "novita", "status": "live", "task": "conversational", "provider_model_id": "x"},
        ]
        assert extras["native_quant_method"] == "fp8"
        assert extras["spaces_count_capped"] is True


# ── fetch_hf_info end to end (URL-routed fake Hub) ──────────────────────────

API_RECORD = {
    "id": "acme/Coder-30B-A3B",
    "author": "acme",
    "sha": "cafebabe",
    "downloads": 10,
    "downloadsAllTime": 99,
    "likes": 2,
    "tags": ["license:apache-2.0", "arxiv:2505.09388"],
    "cardData": {},
    "config": {"model_type": "qwen3_moe", "architectures": ["Qwen3MoeForCausalLM"]},
    "safetensors": {"total": 30_532_122_624, "parameters": {"BF16": 30_532_122_624}},
    "gated": False,
    "spaces": [],
}


def _fake_hub(routes: dict[str, Mock]):
    def handler(url, **kwargs):
        for fragment, response in routes.items():
            if fragment in url:
                return response
        return _resp(404)

    return handler


@pytest.mark.django_db
class TestFetchHfInfo:
    @patch(REQUESTS_GET)
    def test_full_record(self, mock_get):
        mock_get.side_effect = _fake_hub(
            {
                "/api/models/acme/Coder-30B-A3B/tree/cafebabe": _resp(
                    200,
                    [
                        {"type": "file", "path": "config.json", "size": 1, "oid": "o1"},
                        {"type": "file", "path": "generation_config.json", "size": 1, "oid": "o2"},
                        {"type": "file", "path": "model.safetensors", "size": 61_000},
                    ],
                ),
                "/resolve/cafebabe/config.json": _resp(200, QWEN_MOE_CONFIG),
                "/resolve/cafebabe/generation_config.json": _resp(
                    200,
                    {"temperature": 0.7},
                    {"X-Repo-Commit": "cafebabe"},
                ),
                "/api/papers/2505.09388": _resp(200, {"title": "Qwen3 Technical Report", "upvotes": 3}),
                "/api/models/acme/Coder-30B-A3B": _resp(200, API_RECORD),
            },
        )
        record = fetch_hf_info("acme/Coder-30B-A3B", client=_quiet_client(FAKE_TOKEN))

        assert record["downloads_all_time"] == 99
        assert record["sha"] == "cafebabe"
        assert record["authenticated"] is True
        assert record["architecture"]["moe"]["num_experts"] == 128
        assert record["generation_config"]["temperature"] == 0.7
        assert record["generation_config"]["commit"] == "cafebabe"
        assert record["files"]["weights_bytes"] == 61_000
        assert record["papers"][0]["title"] == "Qwen3 Technical Report"
        # tokenizer_config.json is absent from the tree: not requested, recorded as not found.
        assert record["fetch_status"]["tokenizer_config"] == STATUS_NOT_FOUND
        assert not any("tokenizer_config.json" in c.args[0] for c in mock_get.call_args_list)
        assert record["gated_inaccessible"] is False
        expand = mock_get.call_args_list[0].kwargs["params"]
        assert ("expand[]", "downloadsAllTime") in expand

    @patch(REQUESTS_GET)
    def test_gated_files_recorded_and_transient_errors_keep_previous(self, mock_get):
        mock_get.side_effect = _fake_hub(
            {
                "/tree/": _resp(500),
                "/resolve/cafebabe/config.json": _resp(403, headers={"X-Error-Code": "GatedRepo"}),
                "/resolve/cafebabe/generation_config.json": _resp(403),
                "/resolve/cafebabe/tokenizer_config.json": _resp(403),
                "/api/papers/": _resp(200, {"title": "P"}),
                "/api/models/acme/Coder-30B-A3B": _resp(200, API_RECORD),
            },
        )
        previous = {"files": {"weights_bytes": 1}, "architecture": {"stale": True}}
        record = fetch_hf_info("acme/Coder-30B-A3B", client=_quiet_client(), previous=previous)

        assert record["fetch_status"]["config"] == STATUS_GATED
        assert record["gated_inaccessible"] is True
        assert record["architecture"] is None  # gated is the current truth, not a transient failure
        assert record["files"] == {"weights_bytes": 1}  # tree 500 → previous kept

    @patch(REQUESTS_GET)
    def test_unreadable_model_returns_none(self, mock_get):
        mock_get.return_value = _resp(401, headers={"X-Error-Code": "RepoNotFound"})
        assert fetch_hf_info("acme/missing", client=_quiet_client()) is None
        assert fetch_hf_info("not-a-repo-id", client=_quiet_client()) is None

    @patch(REQUESTS_GET)
    def test_truncated_tree_reports_no_byte_total_and_skips_nothing(self, mock_get):
        page = [{"type": "file", "path": "model-00001-of-09999.safetensors", "size": 5}]
        next_link = {
            "Link": '<https://huggingface.co/api/models/acme/Coder-30B-A3B/tree/cafebabe?cursor=x>; rel="next"',
        }
        mock_get.side_effect = _fake_hub(
            {
                "/tree/": _resp(200, page, next_link),
                "/resolve/cafebabe/config.json": _resp(200, QWEN_MOE_CONFIG),
                "/api/models/acme/Coder-30B-A3B": _resp(200, {**API_RECORD, "tags": []}),
            },
        )
        record = fetch_hf_info("acme/Coder-30B-A3B", client=_quiet_client())

        assert record["files"]["complete"] is False
        assert record["files"]["weights_bytes"] is None
        # config.json was not in the partial listing but is still fetched.
        assert record["architecture"]["model_type"] == "qwen3_moe"
        tree_calls = [c for c in mock_get.call_args_list if "/tree/" in c.args[0]]
        assert len(tree_calls) == 3  # page cap


# ── enrich_model: params and local twins ────────────────────────────────────

MODELS_DEV = {
    "openrouter": {
        "api": "https://openrouter.ai/api/v1",
        "models": {
            "deepseek/deepseek-v4-pro": {
                "id": "deepseek/deepseek-v4-pro",
                "canonical_model_id": "deepseek/deepseek-v4-pro",
            },
        },
    },
    "opencode-go": {
        "api": "https://opencode.ai/inference/go/openai/v1",
        "name": "OpenCode Go",
        "models": {
            "deepseek-v4-pro": {
                "id": "deepseek-v4-pro",
                "knowledge": "2025-05",
                "open_weights": True,
                "canonical_model_id": "deepseek/deepseek-v4-pro",
            },
        },
    },
}


def _local_model(owner, served: str, slug: str = "opencode-go") -> LLMModel:
    endpoint = LLMEndpoint.objects.filter(slug=slug).first() or LLMEndpoint.objects.create(
        slug=slug,
        name="OpenCode Go",
        kind="openai_compatible",
        connection="direct",
        api_flavor="openai",
        base_url="https://opencode.ai/zen/go",
        owner=owner,
    )
    return LLMModel.objects.create(
        model_id=f"local-{slug}/{served}",
        canonical_slug=f"local-{slug}_{served}",
        provider=slug,
        model_name=served,
        endpoint=endpoint,
        served_model_name=served,
        metadata={"ollama": {}},
    )


@pytest.mark.django_db
class TestEnrichModel:
    @patch("backend.llm_models.services.enrichment.requests.get")
    def test_params_written_from_stored_record_without_fetching(self, mock_get):
        mock_get.side_effect = AssertionError("no network")
        hf = {
            "id": "acme/Coder-30B-A3B",
            "parameters": 30_532_122_624,
            "architecture": hf_metadata.parse_architecture(QWEN_MOE_CONFIG),
        }
        model = LLMModelFactory(model_id="acme/coder-30b-a3b", metadata={"huggingface": hf})

        written = enrich_model(model, catalog={}, include_hf=False)

        assert written["params"] is True
        model.refresh_from_db()
        assert model.metadata["params"]["active_source"] == "name_pattern"
        # A second pass with nothing new does not rewrite the row.
        assert enrich_model(model, catalog={}, include_hf=False)["params"] is False

    @patch("backend.llm_models.services.enrichment.requests.get")
    def test_local_model_linked_via_models_dev_provider(self, mock_get, user):
        mock_get.return_value = _resp(200, MODELS_DEV)
        twin = LLMModelFactory(
            model_id="deepseek/deepseek-v4-pro",
            metadata={"huggingface": {"id": "deepseek-ai/DeepSeek-V4-Pro"}},
        )
        local = _local_model(user, "deepseek-v4-pro")

        written = enrich_model(local)

        assert written["catalog_twin"] is True
        local.refresh_from_db()
        link = local.metadata["catalog_twin"]
        assert link["model_id"] == twin.model_id
        assert link["source"] == "models.dev:opencode-go"
        assert link["provider_record"]["knowledge"] == "2025-05"
        assert "assumed same weights" in link["note"].lower() or "Assumed same weights" in link["note"]
        assert local.metadata["ollama"] == {}  # inventory metadata kept
        assert "huggingface" not in local.metadata  # nothing copied: the page reads the twin

    @patch("backend.llm_models.services.enrichment.requests.get")
    def test_served_name_fallback_and_manual_link_kept(self, mock_get, user):
        mock_get.return_value = _resp(200, {"openrouter": {"models": {}}})
        LLMModelFactory(model_id="z-ai/glm-9")
        LLMModelFactory(model_id="z-ai/glm-9:free")
        local = _local_model(user, "glm-9")
        manual = _local_model(user, "deepseek-v4-pro")
        manual.metadata = {"catalog_twin": {"model_id": "x/y", "source": "manual"}}
        manual.save()

        linked = link_local_models()

        assert linked == 1
        local.refresh_from_db()
        manual.refresh_from_db()
        assert local.metadata["catalog_twin"]["source"] == "served_name"
        assert local.metadata["catalog_twin"]["model_id"] == "z-ai/glm-9"
        assert manual.metadata["catalog_twin"] == {"model_id": "x/y", "source": "manual"}


# ── API: twin payload and sampling usage ────────────────────────────────────


@pytest.mark.django_db
class TestModelApi:
    def test_detail_of_local_model_carries_twin_metadata(self, client, user):
        client.force_login(user)
        LLMModelFactory(
            model_id="deepseek/deepseek-v4-pro",
            canonical_slug="deepseek_deepseek-v4-pro",
            metadata={"huggingface": {"id": "deepseek-ai/DeepSeek-V4-Pro"}, "params": {"active": 49}, "other": 1},
            capabilities_json={"hugging_face_id": "deepseek-ai/DeepSeek-V4-Pro", "pricing": {}},
        )
        local = _local_model(user, "deepseek-v4-pro")
        local.metadata = {"catalog_twin": {"model_id": "deepseek/deepseek-v4-pro", "source": "models.dev:opencode-go"}}
        local.save()

        data = client.get(f"/api/models/detail/{local.canonical_slug}/").json()

        twin = data["twin"]
        assert twin["model"]["canonical_slug"] == "deepseek_deepseek-v4-pro"
        assert set(twin["metadata"]) == {"huggingface", "params"}
        assert twin["capabilities"] == {"hugging_face_id": "deepseek-ai/DeepSeek-V4-Pro"}
        assert data["id"] == local.pk
        catalog = client.get("/api/models/detail/deepseek_deepseek-v4-pro/").json()
        assert catalog["twin"] is None

    def test_sampling_usage_reports_what_runs_sent(self, client, user):
        client.force_login(user)
        model = LLMModelFactory(model_id="acme/coder-1", canonical_slug="acme_coder-1")
        _run_with_sampling(
            user,
            model,
            {"policy": "provider_default", "forced_params": {"seed": 1}, "stripped_params": ["temperature"]},
        )
        _run_with_sampling(user, model, {"policy": "explicit", "forced_params": {"temperature": 0.3, "seed": 2}})
        _run_with_sampling(user, model, {"policy": "explicit", "forced_params": {"temperature": 0.3, "top_p": 0.9}})
        # A run without a manifest recorded nothing.
        GenerationRunFactory(created_by=user, model=model)
        _run_with_sampling(UserFactory(), model, {"policy": "explicit", "forced_params": {"temperature": 1.5}})

        resp = client.get("/api/models/detail/acme_coder-1/sampling/")

        assert resp.status_code == HTTPStatus.OK
        data = resp.json()
        assert data["n_jobs"] == 4
        assert data["by_mode"] == {"app_agentic": 4}
        assert data["policies"] == {"provider_default": 1, "explicit": 2, "not_recorded": 1}
        temperature = next(p for p in data["params"] if p["key"] == "temperature")
        assert temperature["values"] == [{"value": 0.3, "n": 2}]
        # Known not sent only under provider_default; the manifest-less run is unknown.
        assert (temperature["n_not_sent"], temperature["n_unknown"]) == (1, 1)
        top_p = next(p for p in data["params"] if p["key"] == "top_p")
        assert top_p["n_sent"] == 1
        # Explicit runs that forced no top_p: their harness may have sent one.
        assert (top_p["n_not_sent"], top_p["n_unknown"]) == (1, 2)
        assert data["harness_sampling_stripped"] == [{"key": "temperature", "jobs": 1, "calls": None}]

    def test_sampling_usage_requires_auth(self, client):
        LLMModelFactory(model_id="acme/coder-1", canonical_slug="acme_coder-1")
        assert client.get("/api/models/detail/acme_coder-1/sampling/").status_code == HTTPStatus.UNAUTHORIZED


# ── command and task ────────────────────────────────────────────────────────


@pytest.mark.django_db
class TestCommandAndTask:
    @patch("backend.llm_models.services.enrichment.requests.get")
    def test_dry_run_makes_no_request(self, mock_get, user):
        mock_get.side_effect = AssertionError("dry run must not fetch")
        LLMModelFactory(model_id="acme/coder-1", capabilities_json={"hugging_face_id": "acme/Coder-1"})
        out = StringIO()

        call_command("enrich_models", "--dry-run", "--model", "acme/coder-1", "--hf-user", str(user.pk), stdout=out)

        text = out.getvalue()
        assert "Hugging Face fetches: 1" in text
        assert "acme/Coder-1" in text
        assert "Dry run" in text

    def test_task_uses_the_given_users_token(self, user):
        from backend.llm_models.tasks import enrich_catalog_task

        cred = UserApiCredential(user=user, provider=Provider.HUGGINGFACE)
        cred.set_secret(FAKE_TOKEN)
        cred.save()
        seen = {}

        def fake_enrich_catalog(*, include_hf, hf_client):
            seen["authenticated"] = hf_client.authenticated
            return {}

        with (
            override_settings(HUGGINGFACE_API_TOKEN=""),
            patch("backend.llm_models.services.enrichment.enrich_catalog", fake_enrich_catalog),
        ):
            enrich_catalog_task(hf_user_id=user.pk)
            assert seen["authenticated"] is True
            enrich_catalog_task()
            assert seen["authenticated"] is False


# ── review regressions ──────────────────────────────────────────────────────


class TestReviewRegressionsParsers:
    def test_eval_results_cap_is_reported_not_silent(self):
        rows = [{"data": {"dataset": {"id": f"bench-{i}"}, "value": i}} for i in range(75)]
        extras = hf_metadata.hf_record_extras({"evalResults": rows})
        assert len(extras["eval_results"]) == hf_metadata.MAX_EVAL_RESULTS
        assert extras["eval_results_total"] == 75

    def test_mistral_consolidated_copy_is_not_double_counted(self):
        # Mistral-Nemo-Instruct-2407: the native consolidated file duplicates the HF shards.
        tree = [
            {"type": "file", "path": "consolidated.safetensors", "size": 24_495_604_224},
            *(
                {"type": "file", "path": f"model-0000{i}-of-00005.safetensors", "size": 4_899_120_844}
                for i in range(1, 6)
            ),
        ]
        files = hf_metadata.parse_tree(tree)
        assert files["weights_bytes"] == 5 * 4_899_120_844
        assert files["weight_files"] == 5
        assert files["shard_count"] == 5

    def test_sharded_consolidated_and_alternative_series_dropped_extra_component_kept(self):
        tree = [
            {"type": "file", "path": "consolidated-00001-of-00002.safetensors", "size": 70},
            {"type": "file", "path": "consolidated-00002-of-00002.safetensors", "size": 30},
            {"type": "file", "path": "model-00001-of-00002.safetensors", "size": 60},
            {"type": "file", "path": "model-00002-of-00002.safetensors", "size": 40},
            {"type": "file", "path": "modelv2-00001-of-00002.safetensors", "size": 50},
            {"type": "file", "path": "modelv2-00002-of-00002.safetensors", "size": 50},
            {"type": "file", "path": "mtp.safetensors", "size": 5},  # Inkling: a real extra component
        ]
        files = hf_metadata.parse_tree(tree)
        assert files["weights_bytes"] == 105
        assert files["weight_files"] == 3

    def test_unparsed_config_is_not_called_dense(self):
        # moondream3-preview's config.json (live 2026-11-02): no layer / width / expert fields at all
        # (the 9B MoE layout lives in its remote code). It must not be reported as 9.27B "dense" active.
        config = {
            "architectures": ["HfMoondream"],
            "auto_map": {"AutoConfig": "hf_moondream.HfConfig"},
            "config": {"skills": ["query", "caption"]},
            "model_type": "moondream3",
            "torch_dtype": "bfloat16",
        }
        hf = {
            "id": "moondream/moondream3-preview",
            "parameters": 9_268_626_928,
            "parameters_by_dtype": {"BF16": 9_268_626_928},
            "architecture": hf_metadata.parse_architecture(config),
        }
        params = hf_metadata.compute_params(hf)
        assert params["active"] is None
        assert params["active_source"] is None
        assert "not recognised" in params["active_note"]

    def test_nested_expert_keys_block_the_dense_claim(self):
        arch = hf_metadata.parse_architecture(
            {
                "model_type": "custom",
                "num_hidden_layers": 24,
                "hidden_size": 2048,
                "text": {"moe": {"num_experts": 64, "experts_per_token": 8}},
            },
        )
        assert arch["has_expert_keys"] is True
        params = hf_metadata.compute_params({"id": "acme/x", "parameters": 9_000_000_000, "architecture": arch})
        assert params["active"] is None
        assert "expert keys" in params["active_note"]

    def test_dense_multimodal_note_says_the_encoder_is_counted(self):
        flat_vl = {
            "architectures": ["Qwen2_5_VLForConditionalGeneration"],
            "model_type": "qwen2_5_vl",
            "num_hidden_layers": 28,
            "hidden_size": 3584,
            "vision_config": {"depth": 32},
        }
        assert hf_metadata.parse_architecture(flat_vl)["multimodal"] is True
        for config in (flat_vl, GEMMA_LIKE_CONFIG):
            hf = {"id": "acme/vl", "parameters": 8_292_166_656, "architecture": hf_metadata.parse_architecture(config)}
            params = hf_metadata.compute_params(hf)
            assert params["active_source"] == "dense"
            assert "encoder" in params["active_note"]
        text_only = {"model_type": "qwen2", "num_hidden_layers": 28, "hidden_size": 3584}
        hf = {"id": "acme/t", "parameters": 7_615_616_512, "architecture": hf_metadata.parse_architecture(text_only)}
        assert "encoder" not in hf_metadata.compute_params(hf)["active_note"]


@pytest.mark.django_db
class TestReviewRegressionsEnrichment:
    def test_keys_written_by_a_concurrent_writer_survive_the_hub_fetch(self):
        model = LLMModelFactory(
            model_id="acme/coder-30b-a3b",
            capabilities_json={"hugging_face_id": "acme/Coder-30B-A3B"},
            metadata={"models_dev": {"id": "old"}},
        )
        record = {"id": "acme/Coder-30B-A3B", "parameters": 30_532_122_624, "schema": 2}

        def slow_fetch(hf_id, **kwargs):
            # While this process waits on the Hub, an external-covariates import writes the row.
            LLMModel.objects.filter(pk=model.pk).update(metadata={"models_dev": {"id": "old"}, "epoch": {"x": 1}})
            return dict(record)

        with patch("backend.llm_models.services.enrichment.fetch_hf_info", side_effect=slow_fetch):
            written = enrich_model(model, catalog={}, force_hf=True, hf_client=_quiet_client())

        assert written["huggingface"] is True
        model.refresh_from_db()
        assert model.metadata["epoch"] == {"x": 1}  # not overwritten with the stale copy
        assert model.metadata["huggingface"]["id"] == "acme/Coder-30B-A3B"
        assert model.metadata["params"]["active_source"] == "name_pattern"

    @patch("backend.llm_models.services.enrichment.requests.get")
    def test_batch_run_fetches_a_shared_repo_once(self, mock_get):
        from backend.llm_models.services.enrichment import enrich_catalog

        mock_get.return_value = _resp(200, {"openrouter": {"models": {}}})
        caps = {"hugging_face_id": "acme/Coder-30B-A3B"}
        paid = LLMModelFactory(model_id="acme/coder-30b-a3b", capabilities_json=caps)
        free = LLMModelFactory(model_id="acme/coder-30b-a3b:free", capabilities_json=caps)
        record = {"id": "acme/Coder-30B-A3B", "parameters": 30_532_122_624, "schema": 2}

        with patch("backend.llm_models.services.enrichment.fetch_hf_info", return_value=record) as fetch:
            result = enrich_catalog(force_hf=True, hf_client=_quiet_client(), link_local=False)

        assert fetch.call_count == 1
        assert result["huggingface_fetched"] == 2
        for row in (paid, free):
            row.refresh_from_db()
            assert row.metadata["huggingface"]["id"] == "acme/Coder-30B-A3B"

    @patch("backend.llm_models.services.enrichment.requests.get")
    def test_served_name_twin_rejected_when_sizes_disagree(self, mock_get, user):
        mock_get.return_value = _resp(200, {"openrouter": {"models": {}}})
        LLMModelFactory(model_id="deepseek/deepseek-r1", metadata={"huggingface": {"parameters": 684_531_386_000}})
        distill = _local_model(user, "deepseek-r1", slug="my-ollama")
        distill.parameter_size = "8.2B"  # Ollama's deepseek-r1 is the 8B distill, not the 671B model
        distill.save()

        assert link_local_models() == 0
        distill.refresh_from_db()
        assert "catalog_twin" not in distill.metadata

        distill.parameter_size = "671B"
        distill.save()
        assert link_local_models() == 1

    def test_sampling_usage_explicit_runs_are_unknown_not_unsent(self, client, user):
        client.force_login(user)
        model = LLMModelFactory(model_id="acme/coder-2", canonical_slug="acme_coder-2")
        _run_with_sampling(user, model, {"policy": "explicit", "forced_params": {"top_p": 0.9}})
        data = client.get("/api/models/detail/acme_coder-2/sampling/").json()
        params = {p["key"]: p for p in data["params"]}
        assert (params["top_p"]["n_sent"], params["top_p"]["n_not_sent"], params["top_p"]["n_unknown"]) == (1, 0, 0)
        assert (params["temperature"]["n_not_sent"], params["temperature"]["n_unknown"]) == (0, 1)
