"""Shop lifecycle: update detection, update / bulk endpoints, uninstall cleanup."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from backend.analysis.models import InstalledTool
from backend.analysis.services import tool_installer
from backend.analysis.tests.factories import AnalyzerToolFactory
from backend.analysis.tests.factories import AnalyzerWorkspaceFactory
from backend.analysis.tests.factories import InstalledToolFactory
from backend.runtime.models import ContainerInstance

pytestmark = pytest.mark.django_db

VIEWS = "backend.analysis.api.views.installed"


@pytest.fixture
def dispatched(monkeypatch):
    calls = []
    monkeypatch.setattr(f"{VIEWS}.dispatch_in_thread", lambda *a, **k: calls.append(a))
    return calls


@pytest.fixture
def ws(user):
    return AnalyzerWorkspaceFactory(user=user)


def test_update_available_tracks_catalog_version(ws):
    tool = AnalyzerToolFactory(slug="shop-ruff", version="0.6")
    it = InstalledToolFactory(workspace=ws, tool=tool, catalog_version="0.6")
    assert not it.update_available
    tool.version = "0.7"
    tool.save()
    it.refresh_from_db()
    assert it.update_available


def test_catalog_and_installed_expose_update_flag(api_client, ws):
    tool = AnalyzerToolFactory(slug="shop-ruff", version="0.7")
    InstalledToolFactory(workspace=ws, tool=tool, catalog_version="0.6")
    catalog = {t["slug"]: t for t in api_client.get("/api/analyzers/tools/").json()}
    assert catalog["shop-ruff"]["update_available"] is True
    [row] = api_client.get("/api/analyzers/workspace/tools/").json()
    assert row["update_available"] is True
    assert row["latest_version"] == "0.7"


def test_update_tool_marks_updating(api_client, ws, dispatched):
    tool = AnalyzerToolFactory(slug="shop-ruff", version="0.7")
    InstalledToolFactory(workspace=ws, tool=tool, catalog_version="0.6", config={"x": 1})
    resp = api_client.post("/api/analyzers/workspace/tools/shop-ruff/update/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "updating"
    assert resp.json()["config"] == {"x": 1}
    assert dispatched[0][3] is True  # update=True


def test_update_tool_rejects_in_flight_install(api_client, ws, dispatched):
    tool = AnalyzerToolFactory(slug="shop-ruff")
    InstalledToolFactory(workspace=ws, tool=tool, status=InstalledTool.Status.INSTALLING)
    assert api_client.post("/api/analyzers/workspace/tools/shop-ruff/update/").status_code == 409
    assert not dispatched


def test_stale_install_can_be_retried(api_client, ws, dispatched):
    tool = AnalyzerToolFactory(slug="shop-ruff", install_timeout=60)
    it = InstalledToolFactory(workspace=ws, tool=tool, status=InstalledTool.Status.INSTALLING)
    InstalledTool.objects.filter(pk=it.pk).update(updated_at=timezone.now() - timedelta(hours=1))
    resp = api_client.post(
        "/api/analyzers/workspace/tools/",
        data={"tool_slug": "shop-ruff"},
        content_type="application/json",
    )
    assert resp.json()["status"] == "installing"
    assert len(dispatched) == 1


def test_update_all_only_outdated_by_default(api_client, ws, dispatched):
    old = AnalyzerToolFactory(slug="old", version="2")
    cur = AnalyzerToolFactory(slug="cur", version="1")
    InstalledToolFactory(workspace=ws, tool=old, catalog_version="1")
    InstalledToolFactory(workspace=ws, tool=cur, catalog_version="1")
    resp = api_client.post("/api/analyzers/workspace/tools/update-all/", data={}, content_type="application/json")
    assert [r["tool_slug"] for r in resp.json()] == ["old"]

    resp = api_client.post(
        "/api/analyzers/workspace/tools/update-all/",
        data={"only_outdated": False},
        content_type="application/json",
    )
    # "old" is now busy updating, so only "cur" is newly queued.
    assert [r["tool_slug"] for r in resp.json()] == ["cur"]
    assert len(dispatched) == 2  # one background thread per request


def test_bulk_install_queues_on_one_thread(api_client, ws, dispatched):
    AnalyzerToolFactory(slug="a")
    AnalyzerToolFactory(slug="b")
    resp = api_client.post(
        "/api/analyzers/workspace/tools/bulk/",
        data={"tool_slugs": ["a", "b", "missing"]},
        content_type="application/json",
    )
    assert sorted(r["tool_slug"] for r in resp.json()) == ["a", "b"]
    assert len(dispatched) == 1
    _target, _ws_id, tool_ids, update = dispatched[0]
    assert len(tool_ids) == 2
    assert update is False


def test_failed_update_keeps_working_version(ws, monkeypatch):
    tool = AnalyzerToolFactory(slug="shop-ruff", version="0.7")
    InstalledToolFactory(workspace=ws, tool=tool, catalog_version="0.6", installed_version="ruff 0.6.9")
    monkeypatch.setattr(tool_installer.workspace_service, "require_ready_container", lambda w: "c")

    def fake_exec(_name, cmd, timeout_s):
        if cmd == tool.install_cmd:
            return {"exit_code": 1, "output": "network down"}
        return {"exit_code": 0, "output": "ruff 0.6.9"}

    monkeypatch.setattr(tool_installer.docker_manager, "exec_in", fake_exec)
    it = tool_installer.install(ws, tool, update=True)
    assert it.status == InstalledTool.Status.INSTALLED
    assert it.installed_version == "ruff 0.6.9"
    assert it.update_available
    assert "network down" in it.install_log


def test_uninstall_runs_uninstall_cmd_when_running(ws, monkeypatch):
    tool = AnalyzerToolFactory(slug="shop-ruff", uninstall_cmd="pip uninstall -y ruff")
    InstalledToolFactory(workspace=ws, tool=tool)
    ran = []
    monkeypatch.setattr(tool_installer.workspace_service, "is_running", lambda w: True)
    ws.container = ContainerInstance.objects.create(name="c", image="img")
    monkeypatch.setattr(
        tool_installer.docker_manager,
        "exec_in",
        lambda name, cmd, timeout_s: ran.append(cmd) or {"exit_code": 0, "output": ""},
    )
    assert tool_installer.uninstall(ws, tool) == "Package removed."
    assert ran == ["pip uninstall -y ruff"]
    assert not InstalledTool.objects.filter(workspace=ws, tool=tool).exists()


def test_detected_version_skips_a_banner_line(monkeypatch):
    tool = AnalyzerToolFactory.build(slug="shop-shellcheck", verify_cmd="shellcheck --version", version="0.10")
    banner = "ShellCheck - shell script analysis tool\nversion: 0.10.0\nlicense: GNU GPL, version 3\n"
    monkeypatch.setattr(tool_installer.docker_manager, "exec_in", lambda *a, **k: {"exit_code": 0, "output": banner})
    assert tool_installer._detect_version("c", tool) == "version: 0.10.0"
    plain = {"exit_code": 0, "output": "ruff 0.6.9"}
    monkeypatch.setattr(tool_installer.docker_manager, "exec_in", lambda *a, **k: plain)
    assert tool_installer._detect_version("c", tool) == "ruff 0.6.9"


def test_tool_without_install_records_the_shipped_build_when_the_workspace_runs(ws, monkeypatch):
    # npm audit ships with the image's Node.js: nothing to install, but the build doing the audit is recorded.
    tool = AnalyzerToolFactory(slug="shop-npm-audit", install_cmd="", verify_cmd="npm --version", version="10")
    ws.container = ContainerInstance.objects.create(name="c", image="img")
    ws.save()
    monkeypatch.setattr(tool_installer.workspace_service, "is_running", lambda w: True)
    npm = {"exit_code": 0, "output": "10.9.2\n"}
    monkeypatch.setattr(tool_installer.docker_manager, "exec_in", lambda *a, **k: npm)
    it = tool_installer.install(ws, tool)
    assert (it.status, it.installed_version, it.catalog_version) == (InstalledTool.Status.INSTALLED, "10.9.2", "10")
    assert not it.update_available

    # A stopped workspace is never provisioned for this: the catalog pin stands in.
    monkeypatch.setattr(tool_installer.workspace_service, "is_running", lambda w: False)
    monkeypatch.setattr(tool_installer.docker_manager, "exec_in", lambda *a, **k: pytest.fail("must not exec"))
    assert tool_installer.install(ws, tool, update=True).installed_version == "10"


def test_redetect_versions_rerecords_existing_installs_from_verify(ws, monkeypatch):
    # Installs recorded before banner lines were skipped keep the banner as their version.
    shellcheck = AnalyzerToolFactory(slug="shop-redetect-sc", verify_cmd="shellcheck --version")
    perflint = AnalyzerToolFactory(slug="shop-redetect-pf", verify_cmd="perflint --version")
    broken = AnalyzerToolFactory(slug="shop-redetect-broken", verify_cmd="broken --version")
    review = AnalyzerToolFactory(slug="shop-redetect-ai", kind="ai", verify_cmd="")
    InstalledToolFactory(workspace=ws, tool=shellcheck, installed_version="ShellCheck - shell script analysis tool")
    InstalledToolFactory(workspace=ws, tool=perflint, installed_version="perflint 0.8.1")
    InstalledToolFactory(workspace=ws, tool=broken, installed_version="1.0")
    InstalledToolFactory(workspace=ws, tool=review, installed_version="")
    banner = "ShellCheck - shell script analysis tool\nversion: 0.10.0\n"
    outputs = {
        "shellcheck --version": {"exit_code": 0, "output": banner},
        "perflint --version": {"exit_code": 0, "output": "perflint 0.8.1\n"},
        "broken --version": {"exit_code": 127, "output": "sh: broken: not found"},
    }
    ran = []
    monkeypatch.setattr(tool_installer.workspace_service, "is_running", lambda w: True)
    monkeypatch.setattr(
        tool_installer.docker_manager,
        "exec_in",
        lambda name, cmd, **k: ran.append((name, cmd)) or outputs[cmd],
    )
    ws.container = ContainerInstance.objects.create(name="ws-c", image="img")
    ws.save()

    changed = tool_installer.redetect_versions(ws)
    assert changed == {"shop-redetect-sc": ("ShellCheck - shell script analysis tool", "version: 0.10.0")}
    assert {cmd for _, cmd in ran} == set(outputs)  # the AI tool has no container build
    versions = dict(InstalledTool.objects.filter(workspace=ws).values_list("tool__slug", "installed_version"))
    assert versions["shop-redetect-sc"] == "version: 0.10.0"
    assert versions["shop-redetect-broken"] == "1.0"  # a failed verify keeps what was recorded

    # Never provisions a stopped workspace.
    monkeypatch.setattr(tool_installer.workspace_service, "is_running", lambda w: False)
    with pytest.raises(RuntimeError, match="not running"):
        tool_installer.redetect_versions(ws)
