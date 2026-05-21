import pytest

from llm_lab.llm_models.models import LLMModel
from llm_lab.llm_models.tests.factories import LLMModelFactory


@pytest.mark.django_db
class TestLLMModel:
    def test_str(self):
        model = LLMModelFactory(provider="openai", model_name="gpt-4o")
        assert str(model) == "openai/gpt-4o"

    def test_capabilities_list(self):
        model = LLMModelFactory(
            supports_function_calling=True,
            supports_vision=True,
            supports_streaming=False,
            supports_json_mode=False,
        )
        caps = model.get_capabilities_list()
        assert "Code" in caps
        assert "Function Calling" in caps
        assert "Vision" in caps
        assert "Streaming" not in caps

    def test_cost_efficiency_free(self):
        assert LLMModel.calculate_cost_efficiency(128_000, 0, 0) == 1.0

    def test_cost_efficiency_paid(self):
        score = LLMModel.calculate_cost_efficiency(128_000, 0.0000025, 0.00001)
        assert 0 < score < 1

    def test_cost_efficiency_zero_context(self):
        assert LLMModel.calculate_cost_efficiency(0, 0.001, 0.001) == 0.0
