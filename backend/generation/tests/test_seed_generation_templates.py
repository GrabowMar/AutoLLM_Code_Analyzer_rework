from io import StringIO

import pytest
from django.core.management import call_command

from backend.generation.models import AppRequirementTemplate
from backend.generation.models import ContentBlock
from backend.generation.models import PromptTemplate
from backend.generation.models import TemplateBundle


@pytest.mark.django_db
def test_seed_generation_templates_creates_sample_content():
    out = StringIO()

    command_result = call_command("seed_generation_templates", stdout=out)

    assert command_result is None
    assert (
        AppRequirementTemplate.objects.filter(
            slug__in=[
                "analytics_campaign_monitor",
                "operations_incident_center",
                "commerce_subscription_billing",
            ],
        ).count()
        == 3
    )
    assert (
        PromptTemplate.objects.filter(
            slug__in=[
                "fastapi-backend-system-v1",
                "fastapi-backend-user-v1",
                "vue-frontend-system-v1",
                "vue-frontend-user-v1",
            ],
        ).count()
        == 4
    )
    assert (
        ContentBlock.objects.filter(
            slug__in=[
                "fastapi-backend-system",
                "fastapi-backend-user",
                "vue-frontend-system",
                "vue-frontend-user",
            ],
            version=1,
        ).count()
        == 4
    )
    fastapi_react_bundle = TemplateBundle.objects.get(slug="system-fastapi-react-standard")
    assert fastapi_react_bundle.scaffolding_slug == "fastapi-react"
    assert (
        TemplateBundle.objects.filter(
            slug__in=["system-fastapi-vue-standard", "system-fastapi-react-standard"],
        ).count()
        == 2
    )
