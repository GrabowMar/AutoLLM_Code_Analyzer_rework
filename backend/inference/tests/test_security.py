"""Direct-endpoint SSRF rules (spec §D.8) and upstream call hygiene."""

from __future__ import annotations

import http.server
import json
import ssl
import threading
from unittest import mock

import pytest

from backend.inference import security
from backend.inference import upstream
from backend.inference.security import UnsafeTargetError
from backend.inference.security import validate_direct_base_url


@pytest.fixture(autouse=True)
def _no_local_routes(monkeypatch, settings):
    monkeypatch.setattr(security, "_local_routes", lambda: ((), ()))
    settings.INFERENCE_DIRECT_ALLOWED_TARGETS = ["100.64.0.0/10:11434"]
    settings.INFERENCE_HOST_PUBLIC_IPS = []


def _resolving(*addresses):
    return mock.patch.object(security, "resolve", return_value=tuple(addresses))


@pytest.mark.parametrize(
    "url",
    [
        "ftp://100.64.1.2:11434",
        "http://user:pw@100.64.1.2:11434",
        "http://100.64.1.2:11434/#frag",
        "http://100.64.1.2:11434/?a=1",
        "http://postgres:5432",
        "http://redis",
        "http://localhost:11434",
        "http://metadata.google.internal",
    ],
)
def test_malformed_or_internal_hosts(url):
    with _resolving("100.64.1.2"), pytest.raises(UnsafeTargetError):
        validate_direct_base_url(url)


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "169.254.169.254", "224.0.0.1", "0.0.0.0", "::1", "fe80::1", "::ffff:127.0.0.1"],  # noqa: S104
)
def test_special_addresses(address):
    with _resolving(address), pytest.raises(UnsafeTargetError):
        validate_direct_base_url("http://gpu.example.org:11434")


def test_private_targets_need_the_allowlist():
    with _resolving("100.64.1.2"):
        target = validate_direct_base_url("http://gpu.tail.ts.net:11434/")
    assert (target.host, target.port, target.addresses) == ("gpu.tail.ts.net", 11434, ("100.64.1.2",))
    with _resolving("100.64.1.2"), pytest.raises(UnsafeTargetError):
        validate_direct_base_url("http://gpu.tail.ts.net:8080")
    with _resolving("192.168.1.10"), pytest.raises(UnsafeTargetError):
        validate_direct_base_url("http://box.lan:11434")


def test_every_resolved_address_must_pass():
    with _resolving("100.64.1.2", "10.0.0.5"), pytest.raises(UnsafeTargetError):
        validate_direct_base_url("http://gpu.tail.ts.net:11434")


def test_public_addresses_and_blocked_ports():
    with _resolving("8.8.8.8"):
        assert validate_direct_base_url("https://api.example.com/v1").base_path == "/v1"
    for port in (22, 5432, 6379, 2376):
        with _resolving("8.8.8.8"), pytest.raises(UnsafeTargetError):
            validate_direct_base_url(f"http://api.example.com:{port}")


def test_compose_networks_and_host_are_rejected(monkeypatch, settings):
    import ipaddress

    monkeypatch.setattr(
        security,
        "_local_routes",
        lambda: ((ipaddress.ip_network("172.18.0.0/16"),), (ipaddress.ip_address("172.18.0.1"),)),
    )
    settings.INFERENCE_DIRECT_ALLOWED_TARGETS = ["172.16.0.0/12:11434"]
    with _resolving("172.18.0.7"), pytest.raises(UnsafeTargetError):
        validate_direct_base_url("http://ollama:11434")
    settings.INFERENCE_HOST_PUBLIC_IPS = ["8.8.4.4"]
    with _resolving("8.8.4.4"), pytest.raises(UnsafeTargetError):
        validate_direct_base_url("http://myhost.example.org")


def test_proc_route_parsing(tmp_path, monkeypatch):
    monkeypatch.undo()  # the autouse fixture stubs _local_routes
    route = tmp_path / "route"
    route.write_text(
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        "eth0\t000012AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0\n",
    )
    monkeypatch.setattr(security, "_PROC_ROUTE", route)
    networks, gateways = security._local_routes.__wrapped__()
    assert [str(n) for n in networks] == ["172.18.0.0/16"]
    assert [str(g) for g in gateways] == ["172.18.0.1"]


class _Redirecting(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_GET(self):
        if self.path == "/api/tags":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = json.dumps({"host": self.headers.get("Host")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def local_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Redirecting)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def _vetted(port, scheme="http", host="gpu.example.org"):
    return security.Target(scheme=scheme, host=host, port=port, base_path="", addresses=("127.0.0.1",))


def test_upstream_connects_to_vetted_ip_and_never_follows_redirects(local_server):
    with mock.patch.object(upstream, "validate_direct_base_url", return_value=_vetted(local_server)) as vet:
        resp = upstream.call("http://gpu.example.org", "GET", "/api/version", timeout=5)
        assert resp.payload == {"host": f"gpu.example.org:{local_server}"}
        with pytest.raises(upstream.UpstreamError) as exc:
            upstream.call("http://gpu.example.org", "GET", "/api/tags", timeout=5)
    assert exc.value.kind == "http"
    assert "redirect" in str(exc.value)
    assert vet.call_count == 2  # re-validated (re-resolved) on every connection


def test_upstream_path_allowlist():
    for method, path, body in (
        ("POST", "/api/pull", {}),
        ("DELETE", "/api/delete", {}),
        ("POST", "/api/generate", {"prompt": "hi"}),
    ):
        with pytest.raises(upstream.UpstreamError) as exc:
            upstream.call("http://x", method, path, body, timeout=1)
        assert exc.value.kind == "forbidden_path"


def test_tls_checks_the_hostname(local_server):
    wrapped = {}

    class FakeContext:
        def wrap_socket(self, sock, server_hostname=None):
            wrapped["server_hostname"] = server_hostname
            msg = "bad cert"
            raise ssl.SSLCertVerificationError(msg)

    with (
        mock.patch.object(upstream, "validate_direct_base_url", return_value=_vetted(local_server, "https")),
        mock.patch.object(upstream.ssl, "create_default_context", return_value=FakeContext()),
        pytest.raises(upstream.UpstreamError) as exc,
    ):
        upstream.call("https://gpu.example.org", "GET", "/api/version", timeout=5)
    assert wrapped["server_hostname"] == "gpu.example.org"
    assert exc.value.kind == "connect"


def test_relay_script_allowlist_matches_server():
    import importlib.util
    from pathlib import Path

    from django.conf import settings

    spec = importlib.util.spec_from_file_location(
        "llmlab_relay",
        Path(settings.BASE_DIR) / "relay" / "llmlab_relay.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.ALLOWED_PATHS == upstream.ALLOWED_PATHS
    assert not module.path_allowed("POST", "/api/generate", {"prompt": "x"})
    assert module.path_allowed("POST", "/api/generate", {"model": "m", "keep_alive": 0})
