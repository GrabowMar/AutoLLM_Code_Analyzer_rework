"""Tests for the APIError -> HTTP response mapping registered on the API."""

from __future__ import annotations

import json

from django.test import RequestFactory

from backend.common.exceptions import NotFoundError
from backend.common.exceptions import OperationFailed
from backend.common.exceptions import ValidationFailed
from config.api import handle_api_error


def _response_for(exc) -> tuple[int, dict]:
    request = RequestFactory().get("/api/anything/")
    response = handle_api_error(request, exc)
    return response.status_code, json.loads(response.content)


def test_validation_failed_maps_to_400():
    status, body = _response_for(ValidationFailed("bad input"))
    assert status == 400
    assert body == {"detail": "bad input"}


def test_not_found_maps_to_404():
    status, body = _response_for(NotFoundError("missing"))
    assert status == 404
    assert body == {"detail": "missing"}


def test_operation_failed_maps_to_500():
    status, body = _response_for(OperationFailed("boom"))
    assert status == 500
    assert body == {"detail": "boom"}


def test_explicit_status_code_override():
    status, body = _response_for(ValidationFailed("conflict", status_code=409))
    assert status == 409
    assert body == {"detail": "conflict"}
