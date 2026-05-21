"""Factory-boy factories for automation models."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from llm_lab.automation.models import Batch
from llm_lab.automation.models import BatchItem
from llm_lab.automation.models import Pipeline
from llm_lab.automation.models import PipelineRun
from llm_lab.automation.models import PipelineStep
from llm_lab.automation.models import PipelineStepRun
from llm_lab.automation.models import Schedule
from llm_lab.users.tests.factories import UserFactory


class PipelineFactory(DjangoModelFactory):
    class Meta:
        model = Pipeline

    owner = factory.SubFactory(UserFactory)
    name = factory.Sequence(lambda n: f"Pipeline {n}")
    description = factory.Faker("sentence")
    version = 1
    status = Pipeline.Status.DRAFT
    config = factory.LazyAttribute(
        lambda _: {
            "steps": [
                {
                    "id": "step-1",
                    "name": "Generate",
                    "kind": "generate",
                    "config": {"model_id": "gpt-4", "template_slug": "todo-app"},
                    "depends_on": [],
                },
            ],
        },
    )
    tags = ["test"]


class PipelineStepFactory(DjangoModelFactory):
    class Meta:
        model = PipelineStep

    pipeline = factory.SubFactory(PipelineFactory)
    order = factory.Sequence(lambda n: n)
    name = factory.Sequence(lambda n: f"Step {n}")
    kind = PipelineStep.Kind.GENERATE
    config = {"model_id": "gpt-4", "template_slug": "todo-app"}
    depends_on = []


class PipelineRunFactory(DjangoModelFactory):
    class Meta:
        model = PipelineRun

    pipeline = factory.SubFactory(PipelineFactory)
    triggered_by = factory.SubFactory(UserFactory)
    status = "pending"
    params = {}


class PipelineStepRunFactory(DjangoModelFactory):
    class Meta:
        model = PipelineStepRun

    run = factory.SubFactory(PipelineRunFactory)
    step = factory.SubFactory(PipelineStepFactory)
    status = "pending"
    output = {}
    error = ""
    attempt = 1
    retries_remaining = 0


class BatchFactory(DjangoModelFactory):
    class Meta:
        model = Batch

    owner = factory.SubFactory(UserFactory)
    name = factory.Sequence(lambda n: f"Batch {n}")
    description = factory.Faker("sentence")
    config = {"matrix": {"models": ["gpt-4"], "templates": ["todo-app"]}}
    status = Batch.Status.PENDING


class BatchItemFactory(DjangoModelFactory):
    class Meta:
        model = BatchItem

    batch = factory.SubFactory(BatchFactory)
    pipeline_run = None
    status = BatchItem.Status.PENDING
    params = {"model_id": "gpt-4", "template_slug": "todo-app"}


class ScheduleFactory(DjangoModelFactory):
    class Meta:
        model = Schedule

    pipeline = factory.SubFactory(PipelineFactory)
    owner = factory.SubFactory(UserFactory)
    cron_expression = "0 * * * *"
    enabled = True
