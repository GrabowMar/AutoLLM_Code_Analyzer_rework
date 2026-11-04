"""Read-only dependency caches for agent sandboxes (spec sections 4.2 "Dependency caches", 6.5, D12).

The compose ``depcache`` block runs four pull-through caches (``compose/local/depcache/``): verdaccio for
npm (``publish``/``unpublish``: ``$nobody``), devpi-server with only the read-only ``root/pypi`` mirror,
athens for Go modules, and reposilite as a Maven Central mirror without write tokens. They never decide a
version: locks do (6.10), so the caches are class O and outside every identity key.

``sandbox_config`` is what the sandbox (WP-08, ``agentic/sandbox.py``) applies to an agent container:

* registry settings (env vars, a Maven ``settings.xml``) pointing at the caches that are reachable;
* every package-manager cache directory moved under ``EPHEMERAL_ROOT`` inside the container's own writable
  layer, so a file a run writes there dies with its container. ``volumes`` is always empty: **no writable
  shared volume is mounted into any agent container**; caches are reached over the network only;
* ``proxy_hosts``: the cache ``host:port`` pairs the gateway's egress proxy must forward (WP-06);
* the note ``depcache_unavailable`` when the policy asks for caches (``caches.dependency_cache=shared_ro``,
  ``network_policy=packages``) and some are not deployed or not answering: those ecosystems then use the
  public registries through the gateway's CONNECT allowlist.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from typing import Final
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

__all__ = [
    "CACHE_ENV",
    "ECOSYSTEMS",
    "EPHEMERAL_ROOT",
    "MAVEN_SETTINGS_PATH",
    "NOTE_UNAVAILABLE",
    "SandboxDepConfig",
    "endpoints",
    "maven_settings_xml",
    "probe_endpoints",
    "sandbox_config",
]

logger = logging.getLogger(__name__)

ECOSYSTEMS: Final = ("npm", "pypi", "go", "maven")
NOTE_UNAVAILABLE: Final = "depcache_unavailable"
# Inside the agent container's own filesystem (never a mount): removed with the container after collection.
EPHEMERAL_ROOT: Final = "/tmp/llmlab-deps"  # noqa: S108 - a path inside the throwaway container, not on the host
MAVEN_SETTINGS_PATH: Final = "/etc/llmlab/maven-settings.xml"
# Every cache location a package manager in the sandbox images writes to, redirected under EPHEMERAL_ROOT.
CACHE_ENV: Final = {
    "npm_config_cache": f"{EPHEMERAL_ROOT}/npm",
    "YARN_CACHE_FOLDER": f"{EPHEMERAL_ROOT}/yarn",
    "PNPM_HOME": f"{EPHEMERAL_ROOT}/pnpm",
    "PIP_CACHE_DIR": f"{EPHEMERAL_ROOT}/pip",
    "UV_CACHE_DIR": f"{EPHEMERAL_ROOT}/uv",
    "POETRY_CACHE_DIR": f"{EPHEMERAL_ROOT}/poetry",
    "GOPATH": f"{EPHEMERAL_ROOT}/go",
    "GOMODCACHE": f"{EPHEMERAL_ROOT}/go/pkg/mod",
    "GOCACHE": f"{EPHEMERAL_ROOT}/go/build",
    "MAVEN_LOCAL_REPO": f"{EPHEMERAL_ROOT}/m2/repository",
}
_SETTINGS: Final = {
    "npm": "DEPCACHE_NPM_URL",
    "pypi": "DEPCACHE_PYPI_URL",
    "go": "DEPCACHE_GOPROXY_URL",
    "maven": "DEPCACHE_MAVEN_URL",
}
# Health probe path per cache (relative to its URL).
_HEALTH: Final = {"npm": "-/ping", "pypi": "+api", "go": "healthz", "maven": ""}
_HTTP_OK_MAX: Final = 399


def endpoints() -> dict[str, str]:
    """``{ecosystem: base URL}`` of the deployed caches (settings read at call time; empty ones left out)."""
    from django.conf import settings

    out: dict[str, str] = {}
    for eco, name in _SETTINGS.items():
        url = str(getattr(settings, name, "") or "").strip()
        if url:
            out[eco] = url.rstrip("/") + "/"
    return out


def probe_endpoints(urls: Mapping[str, str], *, timeout_s: float = 2.0) -> dict[str, bool]:
    """``{ecosystem: answering}`` with one GET of each cache's health path."""
    out: dict[str, bool] = {}
    for eco, url in urls.items():
        target = url + _HEALTH.get(eco, "")
        if urlsplit(target).scheme not in {"http", "https"}:
            out[eco] = False
            continue
        try:
            with urllib.request.urlopen(target, timeout=timeout_s) as response:  # noqa: S310 - scheme checked
                out[eco] = response.status <= _HTTP_OK_MAX
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.info("depcache %s unavailable at %s: %s", eco, target, exc)
            out[eco] = False
    return out


def maven_settings_xml(mirror_url: str | None) -> bytes:
    """A Maven ``settings.xml``: the per-run local repository and, when given, the read-only mirror."""
    mirror = ""
    if mirror_url:
        mirror = (
            "  <mirrors>\n"
            "    <mirror>\n"
            "      <id>llmlab-depcache</id>\n"
            "      <mirrorOf>*</mirrorOf>\n"
            f"      <url>{escape(mirror_url)}</url>\n"
            "    </mirror>\n"
            "  </mirrors>\n"
        )
    text = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0">\n'
        f"  <localRepository>{CACHE_ENV['MAVEN_LOCAL_REPO']}</localRepository>\n"
        "  <interactiveMode>false</interactiveMode>\n"
        f"{mirror}"
        "</settings>\n"
    )
    return text.encode("utf-8")


@dataclass(frozen=True)
class SandboxDepConfig:
    """Dependency settings for one agent container (see the module docstring)."""

    env: dict[str, str]
    files: dict[str, bytes]  # absolute in-container paths -> bytes
    ephemeral_dirs: tuple[str, ...]  # created inside the container; WP-08 must never mount anything over them
    proxy_hosts: tuple[str, ...]  # host:port the gateway egress must forward
    notes: tuple[str, ...]
    unavailable: tuple[str, ...] = ()  # ecosystems that fell back to the public registries
    volumes: tuple[str, ...] = field(default=())  # always empty (spec 4.2): no writable shared volume

    def __post_init__(self) -> None:
        if self.volumes:
            msg = "agent sandboxes never mount a shared volume for dependency caches"
            raise ValueError(msg)


def _host_port(url: str) -> str:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return f"{parts.hostname}:{port}"


def _registry_env(eco: str, url: str) -> dict[str, str]:
    if eco == "npm":
        return {"npm_config_registry": url, "YARN_REGISTRY": url}
    if eco == "pypi":
        index = f"{url}root/pypi/+simple/"
        env = {"PIP_INDEX_URL": index, "UV_DEFAULT_INDEX": index, "UV_INDEX_URL": index}
        if urlsplit(url).scheme == "http":
            env["PIP_TRUSTED_HOST"] = str(urlsplit(url).hostname)
            env["UV_INSECURE_HOST"] = str(urlsplit(url).hostname)
        return env
    if eco == "go":
        return {"GOPROXY": url.rstrip("/"), "GOFLAGS": "-mod=mod"}
    return {}


def sandbox_config(
    *,
    dependency_cache: str,
    network_policy: str,
    available: Mapping[str, bool] | None = None,
) -> SandboxDepConfig:
    """The dependency settings of one agent container.

    ``dependency_cache`` is the policy's ``caches.dependency_cache`` (``off`` | ``shared_ro``) and
    ``network_policy`` its ``network_policy`` (``none`` | ``packages``). ``available`` overrides the health
    probe (tests, or a probe the caller already ran); by default each configured cache is probed with
    ``settings.DEPCACHE_PROBE_TIMEOUT_S`` (0 = trust the configuration).
    """
    from django.conf import settings

    if dependency_cache not in {"off", "shared_ro"}:
        msg = f"unknown caches.dependency_cache {dependency_cache!r}"
        raise ValueError(msg)
    if network_policy not in {"none", "packages"}:
        msg = f"unknown network_policy {network_policy!r}"
        raise ValueError(msg)
    env = dict(CACHE_ENV)
    env |= {"npm_config_update_notifier": "false", "npm_config_fund": "false", "PIP_NO_INPUT": "1"}
    wanted = dependency_cache == "shared_ro" and network_policy == "packages"
    urls = endpoints() if wanted else {}
    if wanted and available is None:
        timeout = float(getattr(settings, "DEPCACHE_PROBE_TIMEOUT_S", 2.0) or 0)
        available = probe_endpoints(urls, timeout_s=timeout) if timeout > 0 else dict.fromkeys(urls, True)
    available = available or {}
    reachable = {eco: url for eco, url in urls.items() if available.get(eco)}
    for eco, url in sorted(reachable.items()):
        env |= _registry_env(eco, url)
    maven_url = reachable.get("maven")
    files = {MAVEN_SETTINGS_PATH: maven_settings_xml(maven_url)}
    env["MAVEN_ARGS"] = f"--settings {MAVEN_SETTINGS_PATH}"
    unavailable = tuple(eco for eco in ECOSYSTEMS if wanted and eco not in reachable)
    notes = (NOTE_UNAVAILABLE,) if unavailable else ()
    dirs = tuple(sorted(set(CACHE_ENV.values())))
    return SandboxDepConfig(
        env=env,
        files=files,
        ephemeral_dirs=dirs,
        proxy_hosts=tuple(sorted({_host_port(url) for url in reachable.values()})),
        notes=notes,
        unavailable=unavailable,
    )
