from __future__ import annotations

import pytest
from ninja.testing import TestClient

from config.api import api
from llm_lab.analysis.tests.factories import AnalysisResultFactory
from llm_lab.analysis.tests.factories import AnalysisTaskFactory
from llm_lab.analysis.tests.factories import FindingFactory


@pytest.fixture
def analysis_task(user):
    return AnalysisTaskFactory(created_by=user)


@pytest.fixture
def analysis_result(analysis_task):
    return AnalysisResultFactory(task=analysis_task)


@pytest.fixture
def finding(analysis_result):
    return FindingFactory(result=analysis_result)


@pytest.fixture
def api_client(user):
    client = TestClient(api)
    client.user = user
    return client
