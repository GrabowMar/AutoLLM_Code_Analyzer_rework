from factory import Faker
from factory import LazyAttribute
from factory.django import DjangoModelFactory

from llm_lab.llm_models.models import LLMModel


class LLMModelFactory(DjangoModelFactory[LLMModel]):
    model_id = Faker("slug")
    canonical_slug = LazyAttribute(
        lambda o: o.model_id.replace("/", "_").replace(":", "_"),
    )
    provider = Faker("company")
    model_name = Faker("catch_phrase")
    description = Faker("paragraph")
    is_free = False
    context_window = 128_000
    max_output_tokens = 4_096
    input_price_per_token = 0.0000025
    output_price_per_token = 0.00001
    supports_function_calling = True
    supports_vision = False
    supports_streaming = True
    supports_json_mode = True
    cost_efficiency = 0.5
    capabilities_json = {}
    metadata = {}

    class Meta:
        model = LLMModel
        django_get_or_create = ["model_id"]
