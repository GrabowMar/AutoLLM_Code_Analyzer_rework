import factory
from factory.django import DjangoModelFactory

from backend.generation.models import AppRequirementTemplate
from backend.generation.models import ContentBlock
from backend.generation.models import Experiment
from backend.generation.models import ExperimentCondition
from backend.generation.models import GenerationJob
from backend.generation.models import PromptTemplate
from backend.generation.models import TemplateBundle
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory


class AppRequirementTemplateFactory(DjangoModelFactory):
    class Meta:
        model = AppRequirementTemplate

    name = factory.Sequence(lambda n: f"App Template {n}")
    slug = factory.Sequence(lambda n: f"app-template-{n}")
    category = "Productivity"
    description = factory.Faker("sentence")
    backend_requirements = ["Requirement 1", "Requirement 2"]
    frontend_requirements = ["UI Requirement 1"]


class PromptTemplateFactory(DjangoModelFactory):
    class Meta:
        model = PromptTemplate

    name = factory.Sequence(lambda n: f"Prompt {n}")
    slug = factory.Sequence(lambda n: f"prompt-{n}")
    stage = "backend"
    role = "system"
    content = "You are a helpful assistant."
    is_default = True


class ContentBlockFactory(DjangoModelFactory):
    class Meta:
        model = ContentBlock

    block_type = ContentBlock.BlockType.PROMPT_TONE
    slug = factory.Sequence(lambda n: f"block-{n}")
    version = 1
    name = factory.Sequence(lambda n: f"Block {n}")
    content = "Sample block content"
    is_system = True


class TemplateBundleFactory(DjangoModelFactory):
    class Meta:
        model = TemplateBundle

    name = factory.Sequence(lambda n: f"Bundle {n}")
    slug = factory.Sequence(lambda n: f"bundle-{n}")
    scaffolding_slug = "flask-react"
    block_refs = []
    is_system = True


class GenerationJobFactory(DjangoModelFactory):
    class Meta:
        model = GenerationJob

    mode = "custom"
    created_by = factory.SubFactory(UserFactory)
    custom_system_prompt = "You are a senior developer."
    custom_user_prompt = "Build a todo app."


class ExperimentFactory(DjangoModelFactory):
    class Meta:
        model = Experiment

    name = factory.Sequence(lambda n: f"Experiment {n}")
    slug = factory.Sequence(lambda n: f"experiment-{n}")
    created_by = factory.SubFactory(UserFactory)
    repeats = 2


class ExperimentConditionFactory(DjangoModelFactory):
    class Meta:
        model = ExperimentCondition

    experiment = factory.SubFactory(ExperimentFactory)
    template_bundle = factory.SubFactory(TemplateBundleFactory)
    model = factory.SubFactory(LLMModelFactory)
