"""Dependency caches (spec 4.2, 6.5; invariant 13.4 "cache isolation: nothing written in run A is visible
in run B", owned by WP-05).

The caches are read-only by configuration (no run can publish), the sandbox mounts no shared volume, and
every package-manager cache path lives inside the run's own container filesystem.
"""

from __future__ import annotations

import http.server
import io
import json
import re
import tarfile
import threading
import uuid
from pathlib import Path

import pytest
import yaml

from backend.generation.build import depcache
from backend.generation.contracts.harness import RenderedHarness
from backend.generation.contracts.outcome import NOTE_CODES
from backend.generation.testing.fake_sandbox import FakeSandbox

REPO = Path(__file__).resolve().parents[4]
DEPCACHE_DIR = REPO / "compose" / "local" / "depcache"
URLS = {
    "DEPCACHE_NPM_URL": "http://172.30.98.2:4873",
    "DEPCACHE_PYPI_URL": "http://172.30.98.3:3141",
    "DEPCACHE_GOPROXY_URL": "http://172.30.98.4:3000",
    "DEPCACHE_MAVEN_URL": "http://172.30.98.5:8080/maven-central",
}
ALL_UP = dict.fromkeys(depcache.ECOSYSTEMS, True)


@pytest.fixture
def deployed(settings):
    for name, url in URLS.items():
        setattr(settings, name, url)
    settings.DEPCACHE_PROBE_TIMEOUT_S = 0
    return settings


def test_note_code_is_registered():
    assert depcache.NOTE_UNAVAILABLE in NOTE_CODES


def test_config_points_every_ecosystem_at_its_cache(deployed):
    cfg = depcache.sandbox_config(dependency_cache="shared_ro", network_policy="packages", available=ALL_UP)
    assert cfg.env["npm_config_registry"] == "http://172.30.98.2:4873/"
    assert cfg.env["PIP_INDEX_URL"] == "http://172.30.98.3:3141/root/pypi/+simple/"
    assert cfg.env["PIP_TRUSTED_HOST"] == "172.30.98.3"
    assert cfg.env["GOPROXY"] == "http://172.30.98.4:3000"
    assert cfg.env["GOFLAGS"] == "-mod=mod"
    settings_xml = cfg.files[depcache.MAVEN_SETTINGS_PATH].decode()
    assert "<mirrorOf>*</mirrorOf>" in settings_xml
    assert "http://172.30.98.5:8080/maven-central/" in settings_xml
    assert f"<localRepository>{depcache.EPHEMERAL_ROOT}/m2/repository</localRepository>" in settings_xml
    assert cfg.env["MAVEN_ARGS"] == f"--settings {depcache.MAVEN_SETTINGS_PATH}"
    assert cfg.proxy_hosts == ("172.30.98.2:4873", "172.30.98.3:3141", "172.30.98.4:3000", "172.30.98.5:8080")
    assert cfg.notes == ()


def test_no_writable_shared_volume_and_every_cache_path_is_per_run(deployed):
    for policy in ("off", "shared_ro"):
        for network in ("none", "packages"):
            cfg = depcache.sandbox_config(dependency_cache=policy, network_policy=network, available=ALL_UP)
            assert cfg.volumes == ()
            for key, value in depcache.CACHE_ENV.items():
                assert cfg.env[key] == value
                assert value.startswith(depcache.EPHEMERAL_ROOT + "/"), key
                assert value in cfg.ephemeral_dirs
    with pytest.raises(ValueError, match="shared volume"):
        depcache.SandboxDepConfig(env={}, files={}, ephemeral_dirs=(), proxy_hosts=(), notes=(), volumes=("x:/y",))


def test_unavailable_caches_fall_back_with_a_note(deployed):
    cfg = depcache.sandbox_config(
        dependency_cache="shared_ro",
        network_policy="packages",
        available={"npm": True, "pypi": False, "go": False, "maven": False},
    )
    assert cfg.notes == (depcache.NOTE_UNAVAILABLE,)
    assert cfg.unavailable == ("pypi", "go", "maven")
    assert "npm_config_registry" in cfg.env
    assert "PIP_INDEX_URL" not in cfg.env
    assert "GOPROXY" not in cfg.env
    assert "<mirror>" not in cfg.files[depcache.MAVEN_SETTINGS_PATH].decode()


def test_not_deployed_is_unavailable_but_off_or_no_network_is_silent(settings):
    for name in URLS:
        setattr(settings, name, "")
    cfg = depcache.sandbox_config(dependency_cache="shared_ro", network_policy="packages")
    assert cfg.notes == (depcache.NOTE_UNAVAILABLE,)
    assert cfg.unavailable == depcache.ECOSYSTEMS
    for policy, network in (("off", "packages"), ("shared_ro", "none")):
        cfg = depcache.sandbox_config(dependency_cache=policy, network_policy=network)
        assert cfg.notes == ()
        assert cfg.proxy_hosts == ()
        assert "npm_config_registry" not in cfg.env


def test_unknown_policy_values_are_refused():
    with pytest.raises(ValueError, match="dependency_cache"):
        depcache.sandbox_config(dependency_cache="shared_rw", network_policy="packages")
    with pytest.raises(ValueError, match="network_policy"):
        depcache.sandbox_config(dependency_cache="off", network_policy="open")


def test_probe_reports_answering_and_dead_caches(settings):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/-/ping" else 404)
            self.end_headers()

        def log_message(self, *args):
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}/"
        result = depcache.probe_endpoints({"npm": base, "pypi": base, "go": "http://127.0.0.1:1/"}, timeout_s=1)
        assert result == {"npm": True, "pypi": False, "go": False}
        settings.DEPCACHE_NPM_URL = base
        settings.DEPCACHE_PYPI_URL = ""
        settings.DEPCACHE_GOPROXY_URL = ""
        settings.DEPCACHE_MAVEN_URL = ""
        settings.DEPCACHE_PROBE_TIMEOUT_S = 1
        cfg = depcache.sandbox_config(dependency_cache="shared_ro", network_policy="packages")
        assert cfg.env["npm_config_registry"] == base
        assert cfg.unavailable == ("pypi", "go", "maven")
    finally:
        server.shutdown()


_WRITE = r"""
set -e
for d in __DIRS__; do mkdir -p "$LLMLAB_ROOT$d" && echo "run-a" > "$LLMLAB_ROOT$d/leak-__TOKEN__"; done
mkdir -p "$HOME/.npm" "$HOME/.cache/pip" "$HOME/.m2" && echo run-a > "$HOME/.npm/leak-__TOKEN__"
echo written > "$LLMLAB_ROOT/workspace/written.txt"
"""
_READ = r"""
found=""
for d in __DIRS__ "$HOME/.npm" "$HOME/.cache/pip" "$HOME/.m2"; do
  for f in "$LLMLAB_ROOT$d/leak-__TOKEN__" "$d/leak-__TOKEN__"; do [ -e "$f" ] && found="$found $f"; done
done
printf '%s' "$found" > "$LLMLAB_ROOT/workspace/found.txt"
"""


def test_cache_isolation_nothing_written_in_run_a_is_visible_in_run_b(deployed):
    """Invariant 13.4 with the fake sandbox: two runs, each with the config the real sandbox applies."""
    cfg = depcache.sandbox_config(dependency_cache="shared_ro", network_policy="packages", available=ALL_UP)
    token = uuid.uuid4().hex
    dirs = " ".join(sorted(cfg.ephemeral_dirs))
    write = _WRITE.replace("__DIRS__", dirs).replace("__TOKEN__", token)
    read = _READ.replace("__DIRS__", dirs).replace("__TOKEN__", token)
    with FakeSandbox() as sandbox:
        run_a = sandbox.run(RenderedHarness(files=cfg.files, env=cfg.env, command=write))
        assert run_a.exit_info.exit_code == 0, run_a.stderr
        assert run_a.workspace_files()["written.txt"] == b"written\n"
        assert (run_a.root / depcache.EPHEMERAL_ROOT.lstrip("/") / "npm" / f"leak-{token}").is_file()
        run_b = sandbox.run(RenderedHarness(files=cfg.files, env=cfg.env, command=read))
        assert run_b.exit_info.exit_code == 0, run_b.stderr
        assert run_b.workspace_files()["found.txt"] == b""
        assert run_a.root != run_b.root


# ------------------------------------------------------------------------------------------------
# The cache services themselves are read-only by configuration
# ------------------------------------------------------------------------------------------------


def test_verdaccio_refuses_publish_and_registration():
    config = yaml.safe_load((DEPCACHE_DIR / "verdaccio" / "config.yaml").read_text())
    assert config["auth"]["htpasswd"]["max_users"] == -1
    assert config["packages"]
    for pattern, rules in config["packages"].items():
        assert rules["publish"] == "$nobody", pattern
        assert rules["unpublish"] == "$nobody", pattern
        assert rules["access"] == "$all", pattern
    assert config["web"]["enable"] is False


def test_devpi_serves_only_the_read_only_mirror():
    entrypoint = (DEPCACHE_DIR / "devpi" / "entrypoint.sh").read_text()
    assert "--restrict-modify root" in entrypoint
    assert "secrets.token_urlsafe" in entrypoint  # a root password nobody knows
    assert "devpi-server==" in (DEPCACHE_DIR / "devpi" / "Dockerfile").read_text()
    assert "@sha256:" in (DEPCACHE_DIR / "devpi" / "Dockerfile").read_text()


def test_reposilite_has_one_proxied_repository_and_no_tokens():
    shared = json.loads((DEPCACHE_DIR / "reposilite" / "configuration.shared.json").read_text())
    repositories = shared["maven"]["repositories"]
    assert len(repositories) == 1
    assert repositories[0]["redeployment"] is False
    assert repositories[0]["proxied"][0]["reference"] == "https://repo.maven.apache.org/maven2/"
    assert "token" not in json.dumps(shared).lower()


def test_compose_block_mounts_cache_volumes_only_into_the_caches():
    compose = yaml.safe_load((REPO / "docker-compose.local.yml").read_text())
    services = compose["services"]
    caches = {name for name in services if name.startswith("depcache-")}
    assert caches == {"depcache-npm", "depcache-pypi", "depcache-go", "depcache-maven"}
    for name in caches:
        assert services[name]["profiles"] == ["depcache"]
        assert "depcache" in services[name]["networks"]
        image = services[name].get("image", "")
        assert "@sha256:" in image or name == "depcache-pypi", name
    cache_volumes = {v for v in compose["volumes"] if v.startswith("depcache_")}
    assert cache_volumes == {"depcache_npm", "depcache_pypi", "depcache_go", "depcache_maven"}
    for name, service in services.items():
        if name in caches:
            continue
        mounts = " ".join(str(v) for v in service.get("volumes", []))
        assert not re.search(r"\bdepcache_", mounts), name
    assert compose["networks"]["depcache"]["internal"] is True


# ------------------------------------------------------------------------------------------------
# Docker (LLMLAB_DOCKER_TESTS=1): real containers
# ------------------------------------------------------------------------------------------------


@pytest.mark.docker
def test_docker_cache_isolation_between_two_containers(docker_client, deployed):
    """Two sandbox-like containers with the real config: run B never sees what run A wrote to a cache."""
    from backend.generation.build import dockerfiles

    cfg = depcache.sandbox_config(dependency_cache="shared_ro", network_policy="packages", available=ALL_UP)
    image = dockerfiles.base_images()["python"].pinned
    token = uuid.uuid4().hex
    dirs = " ".join(sorted(cfg.ephemeral_dirs))
    write = f'for d in {dirs}; do mkdir -p "$d" && echo a > "$d/leak-{token}"; done; ls {depcache.EPHEMERAL_ROOT}'
    read = f'for d in {dirs}; do [ -e "$d/leak-{token}" ] && echo "LEAK $d"; done; true'
    docker_client.images.pull(image)
    common = {"environment": cfg.env, "network_disabled": True, "remove": True, "cap_drop": ["ALL"]}
    out_a = docker_client.containers.run(image, ["sh", "-c", write], **common).decode()
    assert "npm" in out_a
    out_b = docker_client.containers.run(image, ["sh", "-c", read], **common).decode()
    assert "LEAK" not in out_b


@pytest.mark.docker
def test_docker_verdaccio_refuses_npm_publish(docker_client):
    """The real verdaccio with our config answers 401/403 to a publish (no run can publish to the cache)."""
    from backend.generation.build import dockerfiles

    network = docker_client.networks.create(f"llmlab-wp05-{uuid.uuid4().hex[:8]}", internal=True)
    config = (DEPCACHE_DIR / "verdaccio" / "config.yaml").read_bytes()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo("config.yaml")
        info.size = len(config)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(config))
    registry = None
    try:
        image = "verdaccio/verdaccio:7.0.2@sha256:5623458a61100ff2c89526752e8ea2db58ae2d494fa202c18bf4a508ec46892c"
        docker_client.images.pull(image)
        registry = docker_client.containers.create(
            image,
            network=network.name,
            name=f"depcache-npm-{uuid.uuid4().hex[:8]}",
        )
        registry.put_archive("/verdaccio/conf", buffer.getvalue())
        registry.start()
        node = dockerfiles.base_images()["node"].pinned
        script = (
            "for i in $(seq 1 60); do node -e \"fetch('http://REG:4873/-/ping').then(r=>process.exit(r.ok?0:1),"
            '()=>process.exit(1))" && break; sleep 1; done; '
            'mkdir /tmp/p && cd /tmp/p && echo \'{"name":"llmlab-leak-probe","version":"1.0.0"}\' > package.json && '
            "echo '//REG:4873/:_authToken=anything' > .npmrc && "
            "npm publish --registry http://REG:4873/ 2>&1; echo EXIT=$?"
        ).replace("REG", registry.name)
        out = docker_client.containers.run(node, ["sh", "-c", script], network=network.name, remove=True).decode()
        assert "EXIT=0" not in out, out
        assert re.search(r"E40[13]|401|403|unauthorized|forbidden", out, re.IGNORECASE), out
    finally:
        if registry is not None:
            registry.remove(force=True)
        network.remove()
