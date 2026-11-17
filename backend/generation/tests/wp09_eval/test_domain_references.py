"""Independent API controls for conversion and XML parsing behavior."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2] / "data" / "tasks"


def reference(slug: str, monkeypatch: Any, tmp_path: Path) -> ModuleType:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    path = ROOT / slug / "reference" / "flask-react" / "backend" / "app.py"
    spec = importlib.util.spec_from_file_location(f"reference_{slug}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def headers(client: Any, username: str) -> dict[str, str]:
    credentials = {"username": username, "password": "test-control-password-9"}
    assert client.post("/api/auth/register", json=credentials).status_code == 201
    token = client.post("/api/auth/login", json=credentials).json["token"]
    return {"Authorization": f"Bearer {token}"}


def test_base64_reference_obeys_encoding_contract(monkeypatch: Any, tmp_path: Path) -> None:
    client = reference("utility_base64_tool", monkeypatch, tmp_path).app.test_client()
    for raw, encoded in [("", ""), ("f", "Zg=="), ("foo", "Zm9v"), ("zażółć", "emHFvMOzxYLEhw==")]:
        assert client.post("/api/base64/encode", json={"text": raw}).json == {"encoded": encoded}
        assert client.post("/api/base64/decode", json={"encoded": encoded}).json == {"text": raw}
    assert client.post("/api/base64/decode", json={"encoded": "  aG\nk "}).json == {"text": "hi"}
    for encoded in ("!", "//8=", "a"):
        assert client.post("/api/base64/decode", json={"encoded": encoded}).status_code == 400
    assert client.post("/api/base64/encode", json={"text": "a" * 100001}).status_code == 400
    assert client.post("/api/base64/decode", json={"encoded": "A" * 140001}).status_code == 400


def test_base64_reference_history_is_private_and_stores_previews(monkeypatch: Any, tmp_path: Path) -> None:
    client = reference("utility_base64_tool", monkeypatch, tmp_path).app.test_client()
    owner, other = headers(client, "owner"), headers(client, "other")
    response = client.post("/api/conversions", json={"direction": "encode", "input": "x" * 300}, headers=owner)
    assert response.status_code == 201
    item = response.json
    assert len(item["input_preview"]) == len(item["output_preview"]) == 200
    assert len(item["output"]) == 400
    saved = client.get("/api/conversions", headers=owner).json["items"][0]
    assert "output" not in saved
    assert client.get("/api/conversions", headers=other).json["total"] == 0
    assert client.delete(f"/api/conversions/{item['id']}", headers=other).status_code == 404
    assert client.delete(f"/api/conversions/{item['id']}", headers=owner).status_code == 204
    assert client.get("/api/conversions").status_code == 401


def test_xml_reference_reports_syntax_position_and_rejects_external_entity(monkeypatch: Any, tmp_path: Path) -> None:
    client = reference("validation_xml_checker", monkeypatch, tmp_path).app.test_client()
    valid = client.post("/api/validate/xml", json={"xml": "<!DOCTYPE a><a><b/></a>"})
    assert valid.json == {"valid": True, "error": None, "line": None, "column": None, "root_element": "a"}
    invalid = client.post("/api/validate/xml", json={"xml": "<a>\n<b></a>"})
    assert invalid.status_code == 200
    assert invalid.json["valid"] is False
    assert invalid.json["line"] == 2
    assert invalid.json["column"] > 0
    assert invalid.json["root_element"] is None
    external = '<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>'
    assert client.post("/api/validate/xml", json={"xml": external}).json["valid"] is False
    for xml in ("", "x" * 200001, None):
        assert client.post("/api/validate/xml", json={"xml": xml}).status_code == 400


def test_xml_reference_history_filters_actual_results(monkeypatch: Any, tmp_path: Path) -> None:
    client = reference("validation_xml_checker", monkeypatch, tmp_path).app.test_client()
    owner, other = headers(client, "owner"), headers(client, "other")
    for xml in ("<a/>", "<broken>", "<b/>"):
        assert client.post("/api/validations", json={"xml": xml}, headers=owner).status_code == 201
    result = client.get("/api/validations?valid=true&page_size=1", headers=owner).json
    assert result["total"] == 2
    assert len(result["items"]) == 1
    assert result["items"][0]["root_element"] == "b"
    assert client.get("/api/validations?valid=false", headers=owner).json["items"][0]["valid"] is False
    assert client.get("/api/validations", headers=other).json["total"] == 0
    assert client.delete(f"/api/validations/{result['items'][0]['id']}", headers=other).status_code == 404
