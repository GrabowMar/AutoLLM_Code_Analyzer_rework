"""Historical model predictors must not silently change with the live catalog."""

from types import SimpleNamespace
from unittest.mock import patch

from backend.reports.services.study_report import _model_parameters


def _model():
    return SimpleNamespace(
        model_id="vendor/a",
        capabilities_json={"context_length": 200000},
        context_window=100000,
        num_ctx_override=None,
        max_output_tokens=8192,
        input_price_per_token=0.000002,
        output_price_per_token=0.000004,
    )


def _manifest(context=10000, price=0):
    return {
        "model": {"endpoint": {"num_ctx": context}, "pricing": {"input_per_mtok": price, "output_per_mtok": price}},
        "resources": {
            "effective": {
                "content": {
                    "context": {"max_context_tokens": 8000},
                    "call": {"max_output_tokens": 2048},
                },
            },
        },
    }


def test_recorded_endpoint_and_zero_prices_override_catalog():
    run = SimpleNamespace(id=1, model=_model())
    with patch("backend.generation.models.RunManifest.objects.filter") as query:
        query.return_value.values_list.return_value = [(1, _manifest())]
        row = _model_parameters([run])["vendor/a"]
    assert row["context_window_effective"] == 10000
    assert row["context_budget_tokens"] == 8000
    assert row["call_output_cap_tokens"] == 2048
    assert row["input_price_per_mtok"] == 0
    assert row["context_window_nominal"] == 200000
    assert row["sources"]["context_window_nominal"] == ["current_catalog"]
    assert row["sources"]["input_price_per_mtok"] == ["run_manifest"]


def test_varying_or_incomplete_endpoint_predictors_are_not_pooled():
    model = _model()
    runs = [SimpleNamespace(id=i, model=model) for i in (1, 2)]
    with patch("backend.generation.models.RunManifest.objects.filter") as query:
        query.return_value.values_list.return_value = [(1, _manifest(10000)), (2, _manifest(20000))]
        row = _model_parameters(runs)["vendor/a"]
        assert row["context_window_effective"] is None
        assert row["observed_values"]["context_window_effective"] == [10000, 20000]
        assert "context_window_effective" in row["varying_predictors"]
        query.return_value.values_list.return_value = [(1, _manifest(10000)), (2, _manifest(None))]
        assert _model_parameters(runs)["vendor/a"]["context_window_effective"] is None


def test_legacy_catalog_fallback_is_explicit():
    with patch("backend.generation.models.RunManifest.objects.filter") as query:
        query.return_value.values_list.return_value = []
        row = _model_parameters([SimpleNamespace(id=1, model=_model())])["vendor/a"]
    assert row["context_window_effective"] == 100000
    assert row["sources"]["context_window_effective"] == ["current_catalog"]
    assert row["context_budget_tokens"] is None
