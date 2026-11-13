"""Parent-authored tests (slot workspaces drop them unless include_tests)."""

from backend.app import create_app


def test_health():
    client = create_app().test_client()
    assert client.get("/api/health").get_json() == {"status": "ok"}
