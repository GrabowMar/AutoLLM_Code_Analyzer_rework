"""``dast_seed``: the API description and sign-in plan the dynamic scanners are seeded with."""

from __future__ import annotations

import json

from backend.analysis.services import dast_seed

CONTENT = {
    "title": "Shop",
    "features": ["auth", "admin"],
    "auth_flow": {
        "register": "auth.register",
        "login": "auth.login",
        "logout": "auth.logout",
        "credential": "bearer",
        "token_json_path": "token",
    },
    "endpoints": [
        {
            "id": "auth.register",
            "method": "POST",
            "path": "/api/auth/register",
            "auth": "public",
            "request_schema": {
                "type": "object",
                "required": ["username", "password", "email"],
                "properties": {
                    "username": {"type": "string", "maxLength": 50},
                    "password": {"type": "string"},
                    "email": {"type": "string"},
                },
            },
        },
        {"id": "auth.login", "method": "POST", "path": "/api/auth/login", "auth": "public"},
        {"id": "auth.logout", "method": "POST", "path": "/api/auth/logout", "auth": "user"},
        {
            "id": "items.list",
            "method": "GET",
            "path": "/api/items",
            "auth": "user",
            "query": [
                {"name": "q", "type": "string", "semantics": "search"},
                {"name": "page", "type": "int", "default": 1},
                {"name": "sort", "type": "enum", "enum": ["name", "price"]},
            ],
        },
        {
            "id": "items.create",
            "method": "POST",
            "path": "/api/items",
            "auth": "user",
            "request_schema": {
                "type": "object",
                "required": ["name", "price"],
                "properties": {
                    "name": {"type": "string", "minLength": 6, "maxLength": 8},
                    "price": {"type": "number", "minimum": 0.01},
                    "stock": {"type": ["integer", "null"], "minimum": 5},
                    "due_date": {"type": "string"},
                    "kind": {"type": "string", "enum": ["a", "b"]},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        {"id": "items.update", "method": "PATCH", "path": "/api/items/{id}", "auth": "owner"},
        {"id": "items.notes", "method": "GET", "path": "/api/items/{id}/notes/{note_id}", "auth": "owner"},
        {"id": "other.get", "method": "GET", "path": "/api/other/{id}", "auth": "public"},
        {"id": "chat.ws", "method": "GET", "path": "/api/ws", "auth": "user", "realtime": "websocket"},
        {
            "id": "files.upload",
            "method": "POST",
            "path": "/api/files",
            "auth": "user",
            "content_type": "multipart",
            "request_schema": {"type": "object", "properties": {"title": {"type": "string"}}},
        },
    ],
}


def test_example_values_satisfy_the_schema() -> None:
    body = dast_seed.example_value(CONTENT["endpoints"][4]["request_schema"])
    assert body == {
        "name": "scansc",
        "price": 1,
        "stock": 5,
        "due_date": "2026-01-15",
        "kind": "a",
        "tags": ["scan"],
    }


def test_seed_plan_signs_in_admin_first_then_a_registered_user() -> None:
    plan = dast_seed.seed_plan(CONTENT, admin_password="pw")  # noqa: S106
    assert [a["identity"] for a in plan["accounts"]] == ["admin", "user"]
    assert plan["accounts"][0]["login"] == {"username": "admin", "password": "pw"}
    assert plan["accounts"][0]["register"] is None
    user = plan["accounts"][1]
    assert user["register"]["username"].startswith("dast_")
    assert user["register"]["email"] == user["register"]["username"] + "@example.com"
    assert user["login"]["username"] == user["register"]["username"]
    assert plan["login_path"] == "/api/auth/login"
    assert plan["check_path"] == "/api/items"
    # Only JSON collection creates; never the auth endpoints, never an upload.
    assert [c["path"] for c in plan["creates"]] == ["/api/items"]


def test_seed_plan_without_admin_credentials_or_feature() -> None:
    assert [a["identity"] for a in dast_seed.seed_plan(CONTENT)["accounts"]] == ["user"]
    plain = {**CONTENT, "features": ["auth"]}
    with_password = dast_seed.seed_plan(plain, admin_password="pw")  # noqa: S106
    assert [a["identity"] for a in with_password["accounts"]] == ["user"]
    assert dast_seed.seed_plan({"endpoints": CONTENT["endpoints"][3:4]})["accounts"] == []


def test_seed_plan_follows_the_run_auth_mode_not_the_task_flow() -> None:
    cookie_task = {**CONTENT, "auth_flow": {"login": "auth.login", "credential": "cookie"}}
    plan = dast_seed.seed_plan(cookie_task, auth_mode="contract_bearer")
    assert (plan["credential"], plan["token_json_path"]) == ("bearer", "token")
    assert (plan["register_path"], plan["login_path"]) == ("/api/auth/register", "/api/auth/login")
    assert dast_seed.seed_plan(CONTENT, auth_mode="cookie_session")["csrf"] == "double_submit_cookie"


def test_openapi_document_covers_the_api_with_examples_and_created_ids() -> None:
    doc = dast_seed.openapi_document(
        CONTENT,
        "http://10.0.0.5:8000/",
        {"/api/items": 42},
        skip_paths=("/api/auth/register", "/api/auth/logout"),
    )
    assert doc["openapi"] == "3.0.3"
    assert doc["servers"] == [{"url": "http://10.0.0.5:8000"}]
    # No sign-out, no registration, no websocket.
    assert sorted(doc["paths"]) == [
        "/api/auth/login",
        "/api/files",
        "/api/items",
        "/api/items/{id}",
        "/api/items/{id}/notes/{note_id}",
        "/api/other/{id}",
    ]
    assert set(doc["paths"]["/api/items"]) == {"get", "post"}
    params = {p["name"]: p for p in doc["paths"]["/api/items"]["get"]["parameters"]}
    assert params["page"]["schema"] == {"type": "integer", "example": 1}
    assert params["sort"]["example"] == "name"
    create = doc["paths"]["/api/items"]["post"]["requestBody"]["content"]["application/json"]
    assert create["example"]["name"] == "scansc"
    # OpenAPI 3.0: one type plus nullable, never a type list.
    assert create["schema"]["properties"]["stock"] == {"type": "integer", "nullable": True, "minimum": 5, "example": 5}
    assert "multipart/form-data" in doc["paths"]["/api/files"]["post"]["requestBody"]["content"]
    # The created object's id fills the ids under its collection; elsewhere the fallback is 1.
    assert doc["paths"]["/api/items/{id}"]["patch"]["parameters"][0]["example"] == 42
    assert [p["example"] for p in doc["paths"]["/api/items/{id}/notes/{note_id}"]["get"]["parameters"]] == [42, 42]
    assert doc["paths"]["/api/other/{id}"]["get"]["parameters"][0]["example"] == 1
    json.dumps(doc)


def test_zap_plan_sends_the_credential_and_imports_the_api() -> None:
    plan = json.loads(dast_seed.zap_plan("http://10.0.0.5:8000", [["Authorization", "Bearer t"]]))
    assert [job["type"] for job in plan["jobs"]] == [
        "replacer",
        "openapi",
        "spider",
        "passiveScan-wait",
        "activeScan",
        "report",
    ]
    assert plan["jobs"][0]["rules"][0]["matchString"] == "Authorization"
    assert plan["jobs"][0]["rules"][0]["replacementString"] == "Bearer t"
    assert plan["jobs"][1]["parameters"]["apiFile"] == dast_seed.OPENAPI_FILE
    anonymous = json.loads(dast_seed.zap_plan("http://10.0.0.5:8000", []))
    assert anonymous["jobs"][0]["type"] == "openapi"


def test_seed_script_compiles() -> None:
    compile(dast_seed.SEED_SCRIPT, "seed.py", "exec")
