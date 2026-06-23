from __future__ import annotations

import pytest
from django.test import Client

from llm_lab.analysis.tests.factories import AnalysisRunFactory
from llm_lab.analysis.tests.factories import AnalyzerToolFactory
from llm_lab.analysis.tests.factories import FindingFactory
from llm_lab.analysis.tests.factories import ToolResultFactory


@pytest.fixture
def tool(db):
    return AnalyzerToolFactory()


@pytest.fixture
def analysis_run(db, user):
    return AnalysisRunFactory(created_by=user)


@pytest.fixture
def tool_result(analysis_run):
    return ToolResultFactory(run=analysis_run)


@pytest.fixture
def finding(tool_result):
    return FindingFactory(result=tool_result)


@pytest.fixture
def api_client(db, user):
    """Session-authenticated Django test client."""
    client = Client()
    client.force_login(user)
    return client
