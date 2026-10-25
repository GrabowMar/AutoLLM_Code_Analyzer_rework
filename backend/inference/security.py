"""Direct endpoints and SSRF (spec §D.8).

A direct endpoint's ``base_url`` is called from the executor container, which
sits on the compose network next to Postgres, Redis and the Docker daemons.
:func:`validate_direct_base_url` therefore only admits:

- ``http``/``https`` URLs without userinfo or fragment, whose host is not a
  compose service name or a metadata host (``INFERENCE_BLOCKED_HOSTS``),
- where **every** resolved address is not loopback, link-local, multicast,
  unspecified or reserved, not inside a network this container is attached
  to (the compose networks), not the container's default gateway (the
  Docker host) and not one of ``INFERENCE_HOST_PUBLIC_IPS``,
- non-global (private, CGNAT, LAN) addresses only when ``(ip, port)``
  matches an ``INFERENCE_DIRECT_ALLOWED_TARGETS`` ``cidr:port`` entry; global
  addresses on any port except :data:`BLOCKED_PORTS`.

``inference.upstream`` re-runs the check on every connection and connects to
the vetted IP (DNS rebinding cannot swap the target in between).
"""

from __future__ import annotations

import ipaddress
import socket
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from django.conf import settings

BLOCKED_PORTS = frozenset({22, 25, 2375, 2376, 5432, 6379})
_PROC_ROUTE = Path("/proc/net/route")

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class UnsafeTargetError(ValueError):
    """The URL may not be used as a direct endpoint."""


@dataclass(frozen=True)
class Target:
    scheme: str
    host: str
    port: int
    base_path: str
    addresses: tuple[str, ...]

    @property
    def host_header(self) -> str:
        default = 443 if self.scheme == "https" else 80
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if self.port == default else f"{host}:{self.port}"


def _parse_allowed_targets(entries) -> list[tuple[IPNetwork, int]]:
    targets: list[tuple[IPNetwork, int]] = []
    for raw in entries or []:
        entry = str(raw).strip()
        if not entry:
            continue
        cidr, _, port = entry.rpartition(":")
        try:
            targets.append((ipaddress.ip_network(cidr, strict=False), int(port)))
        except ValueError:
            continue
    return targets


def _hex_ipv4(value: str) -> ipaddress.IPv4Address:
    return ipaddress.IPv4Address(struct.pack("<I", int(value, 16)))


@lru_cache(maxsize=1)
def _local_routes() -> tuple[tuple[IPNetwork, ...], tuple[IPAddress, ...]]:
    """``(directly attached networks, default gateways)`` of this container, from ``/proc/net/route``."""
    networks: list[IPNetwork] = []
    gateways: list[IPAddress] = []
    try:
        lines = _PROC_ROUTE.read_text().splitlines()[1:]
    except OSError:
        return (), ()
    for line in lines:
        fields = line.split()
        if len(fields) < 8:
            continue
        destination, gateway, mask = _hex_ipv4(fields[1]), _hex_ipv4(fields[2]), _hex_ipv4(fields[7])
        if int(destination) == 0:
            if int(gateway):
                gateways.append(gateway)
            continue
        try:
            networks.append(ipaddress.IPv4Network(f"{destination}/{mask}", strict=False))
        except ValueError:
            continue
    return tuple(networks), tuple(gateways)


def local_networks() -> tuple[IPNetwork, ...]:
    extra = tuple(
        ipaddress.ip_network(c, strict=False) for c in getattr(settings, "INFERENCE_EXTRA_BLOCKED_CIDRS", []) or []
    )
    return _local_routes()[0] + extra


def host_addresses() -> tuple[IPAddress, ...]:
    configured = tuple(ipaddress.ip_address(a) for a in getattr(settings, "INFERENCE_HOST_PUBLIC_IPS", []) or [])
    return _local_routes()[1] + configured


def check_address(ip: IPAddress, port: int) -> None:
    """Raise :class:`UnsafeTargetError` unless ``(ip, port)`` is an acceptable direct target."""
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        msg = f"{ip} is a loopback, link-local, multicast or reserved address"
        raise UnsafeTargetError(msg)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        check_address(ip.ipv4_mapped, port)
        return
    if ip in host_addresses():
        msg = f"{ip} is this host's own address"
        raise UnsafeTargetError(msg)
    if any(ip in net for net in local_networks()):
        msg = f"{ip} is inside the platform's internal networks"
        raise UnsafeTargetError(msg)
    allowed = _parse_allowed_targets(settings.INFERENCE_DIRECT_ALLOWED_TARGETS)
    if not ip.is_global:
        if not any(ip in net and port == allowed_port for net, allowed_port in allowed):
            msg = (
                f"{ip}:{port} is a private address; add a matching INFERENCE_DIRECT_ALLOWED_TARGETS "
                "entry (cidr:port) to allow it"
            )
            raise UnsafeTargetError(msg)
        return
    if port in BLOCKED_PORTS:
        msg = f"port {port} is not allowed for a direct endpoint"
        raise UnsafeTargetError(msg)


def resolve(host: str, port: int) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        msg = f"cannot resolve {host}: {exc}"
        raise UnsafeTargetError(msg) from exc
    seen: list[str] = []
    for info in infos:
        address = str(info[4][0]).split("%", 1)[0]
        if address not in seen:
            seen.append(address)
    if not seen:
        msg = f"{host} has no addresses"
        raise UnsafeTargetError(msg)
    return tuple(seen)


def validate_direct_base_url(url: str) -> Target:
    """Validate a direct endpoint URL and return its vetted :class:`Target` (raises :class:`UnsafeTargetError`)."""
    raw = (url or "").strip()
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        msg = f"invalid URL: {exc}"
        raise UnsafeTargetError(msg) from exc
    if parts.scheme not in ("http", "https"):
        msg = "the URL must start with http:// or https://"
        raise UnsafeTargetError(msg)
    if parts.username or parts.password or "@" in parts.netloc:
        msg = "credentials in the URL are not allowed; use the API key field"
        raise UnsafeTargetError(msg)
    if parts.fragment or parts.query:
        msg = "the URL must not have a query or fragment"
        raise UnsafeTargetError(msg)
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        msg = "the URL has no host"
        raise UnsafeTargetError(msg)
    blocked = {h.lower() for h in settings.INFERENCE_BLOCKED_HOSTS}
    if host in blocked or host == "localhost" or host.endswith(".localhost"):
        msg = f"{host} is an internal host"
        raise UnsafeTargetError(msg)
    port = port or (443 if parts.scheme == "https" else 80)
    addresses = resolve(host, port)
    for address in addresses:
        check_address(ipaddress.ip_address(address), port)
    return Target(
        scheme=parts.scheme,
        host=host,
        port=port,
        base_path=parts.path.rstrip("/"),
        addresses=addresses,
    )
