import factory
from factory.django import DjangoModelFactory

from llm_lab.generation.models import AppRequirementTemplate
from llm_lab.generation.models import GenerationJob
from llm_lab.generation.models import PromptTemplate
from llm_lab.generation.models import ScaffoldingTemplate
from llm_lab.users.tests.factories import UserFactory


class ScaffoldingTemplateFactory(DjangoModelFactory):
    class Meta:
        model = ScaffoldingTemplate

    name = factory.Sequence(lambda n: f"Scaffolding {n}")
    slug = factory.Sequence(lambda n: f"scaffolding-{n}")
    description = factory.Faker("sentence")
    tech_stack = {"frontend": "React 18", "backend": "Flask 3.x"}
    is_default = True


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


class GenerationJobFactory(DjangoModelFactory):
    class Meta:
        model = GenerationJob

    mode = "custom"
    created_by = factory.SubFactory(UserFactory)
    custom_system_prompt = "You are a senior developer."
    custom_user_prompt = "Build a todo app."
