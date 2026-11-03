"""Variability endpoints: routing, ownership and query validation (the computation is mocked).

The payloads themselves are :mod:`backend.statistics.services.variability`'s and are tested there (WP-02b).
"""

from __future__ import annotations

import sys
import types
import uuid

import pytest

from backend.generation.tests.factories import GenerationRunFactory
from backend.generation.tests.factories import StudyFactory
from backend.llm_models.tests.factories import LLMModelFactory
from backend.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

API = "/api/generation"


@pytest.fixture
def user():
    return UserFactory()


@pytest.fixture
def api(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def svc(monkeypatch):
    """A stand-in for the statistics service: records the calls and answers with canned payloads.

    It replaces the module itself, so these tests pin the endpoint contract (names, arguments) without
    depending on the statistics stack.
    """
    import backend.statistics.services as services_pkg

    fake = types.ModuleType("backend.statistics.services.variability")
    calls: list[tuple[str, tuple, dict]] = []

    def install(name, result):
        def stub(*args, **kwargs):
            calls.append((name, args, kwargs))
            if isinstance(result, Exception):
                raise result
            return result

        setattr(fake, name, stub)

    install("study_variability", {"cells": [], "notes": ["canned"]})
    install("study_variability_csv", "condition,task\n")
    install("run_replicates", {"run_id": "x", "members": []})
    install("model_variability", {"groups": []})
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    monkeypatch.setattr(services_pkg, "variability", fake, raising=False)
    return types.SimpleNamespace(calls=calls, install=install)


class TestStudyVariability:
    def test_owner_gets_the_service_payload_with_the_query_options(self, api, user, svc):
        study = StudyFactory(created_by=user)
        resp = api.get(f"{API}/studies/{study.pk}/variability/?fingerprint=all&sensitivity=exclude_truncated")
        assert resp.status_code == 200
        assert resp.json() == {"cells": [], "notes": ["canned"]}
        name, args, kwargs = svc.calls[0]
        assert name == "study_variability"
        assert args[0] == study
        assert kwargs == {"fingerprint": "all", "sensitivity": "exclude_truncated"}

    def test_defaults_to_the_majority_fingerprint(self, api, user, svc):
        study = StudyFactory(created_by=user)
        api.get(f"{API}/studies/{study.pk}/variability/")
        assert svc.calls[0][2] == {"fingerprint": "majority", "sensitivity": None}

    def test_csv(self, api, user, svc):
        study = StudyFactory(created_by=user)
        resp = api.get(f"{API}/studies/{study.pk}/variability/?format=csv")
        assert resp.status_code == 200
        assert resp["Content-Type"].startswith("text/csv")
        assert f"{study.slug}-variability.csv" in resp["Content-Disposition"]
        assert resp.content.decode() == "condition,task\n"

    @pytest.mark.parametrize(
        ("query", "code"),
        [("format=xml", "invalid_format"), ("fingerprint=some", "invalid_fingerprint")],
    )
    def test_bad_query_is_400(self, api, user, svc, query, code):
        study = StudyFactory(created_by=user)
        resp = api.get(f"{API}/studies/{study.pk}/variability/?{query}")
        assert resp.status_code == 400
        assert resp.json()["code"] == code
        assert svc.calls == []

    def test_service_value_error_is_400(self, api, user, svc):
        svc.install("study_variability", ValueError("unknown sensitivity"))
        study = StudyFactory(created_by=user)
        resp = api.get(f"{API}/studies/{study.pk}/variability/?sensitivity=nope")
        assert resp.status_code == 400
        assert resp.json() == {"detail": "unknown sensitivity", "code": "invalid_variability_query"}

    def test_access_control(self, api, user, svc):
        assert api.get(f"{API}/studies/{StudyFactory().pk}/variability/").status_code == 404
        assert api.get(f"{API}/studies/not-a-uuid/variability/").status_code == 404
        assert svc.calls == []

    def test_anonymous_is_rejected(self, client, user, svc):
        study = StudyFactory(created_by=user)
        assert client.get(f"{API}/studies/{study.pk}/variability/").status_code == 401


class TestReplicates:
    def test_owner_gets_the_group(self, api, user, svc):
        run = GenerationRunFactory(created_by=user)
        resp = api.get(f"{API}/runs/{run.pk}/replicates/")
        assert resp.status_code == 200
        name, args, _kwargs = svc.calls[0]
        assert (name, args[0], args[1]) == ("run_replicates", run, user)

    def test_access_control(self, api, user, svc):
        assert api.get(f"{API}/runs/{GenerationRunFactory().pk}/replicates/").status_code == 404
        assert api.get(f"{API}/runs/{uuid.uuid4()}/replicates/").status_code == 404
        assert api.get(f"{API}/runs/not-a-uuid/replicates/").status_code == 404
        assert svc.calls == []


class TestModelVariability:
    def test_visible_model(self, api, user, svc):
        model = LLMModelFactory()
        resp = api.get(f"{API}/models/{model.pk}/variability/")
        assert resp.status_code == 200
        assert resp.json() == {"groups": []}
        name, args, _kwargs = svc.calls[0]
        assert (name, args[0], args[1]) == ("model_variability", model, user)

    def test_unknown_model_is_404(self, api, user, svc):
        assert api.get(f"{API}/models/999999/variability/").status_code == 404
