import factory
from factory.django import DjangoModelFactory

from llm_lab.runtime.models import ContainerAction
from llm_lab.runtime.models import ContainerInstance
from llm_lab.users.tests.factories import UserFactory


class ContainerInstanceFactory(DjangoModelFactory):
    class Meta:
        model = ContainerInstance

    name = factory.Sequence(lambda n: f"container-{n}")
    image = "llm_lab/test:latest"
    container_id = factory.Sequence(lambda n: f"abc{n:06d}")
    status = ContainerInstance.Status.STOPPED
    backend_port = factory.Sequence(lambda n: 5100 + n)
    frontend_port = factory.Sequence(lambda n: 8100 + n)
    created_by = factory.SubFactory(UserFactory)


class ContainerActionFactory(DjangoModelFactory):
    class Meta:
        model = ContainerAction

    action_id = factory.Sequence(lambda n: f"act_{n:012x}")
    container = factory.SubFactory(ContainerInstanceFactory)
    action_type = ContainerAction.ActionType.START
    status = ContainerAction.Status.PENDING
    triggered_by = factory.SubFactory(UserFactory)
