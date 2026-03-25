from __future__ import annotations

import logging
import socket
from urllib.parse import urlparse

from src.models.desired import DesiredMonitor

logger = logging.getLogger(__name__)

# Default TCP ports when none is explicit in the URL.
_SCHEME_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}


def _tcp_connect(host: str, port: int, timeout: float) -> bool:
    """Return True if a TCP connection to host:port succeeds within timeout seconds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _endpoint_for_monitor(monitor: DesiredMonitor) -> tuple[str, int] | None:
    """Extract (host, port) to validate from a DesiredMonitor payload.

    Returns None for monitors that have no meaningful TCP endpoint to check
    (e.g. group monitors).
    """
    payload = monitor.payload
    monitor_type = payload.get("type", "")

    if monitor_type == "group":
        return None

    if monitor_type == "port":
        hostname = payload.get("hostname", "")
        port = payload.get("port")
        if hostname and port:
            return str(hostname), int(port)
        return None

    # http, keyword, json-query, and all HTTP-based types.
    url = payload.get("url", "")
    if not url:
        return None
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not host:
        return None
    port = parsed.port or _SCHEME_DEFAULT_PORTS.get(parsed.scheme, 80)
    return host, port


class EndpointValidator:
    """Validates discovered monitors by attempting a TCP connection to the endpoint.

    This catches:
    - DNS resolution failures (ENOTFOUND)
    - Network unreachable errors (EHOSTUNREACH)
    - Refused connections (port not open)

    It does NOT filter based on HTTP response codes or TLS errors — those are
    handled by Uptime Kuma itself once the monitor is created.
    """

    def __init__(self, timeout_sec: float = 3.0) -> None:
        self._timeout = timeout_sec

    def is_reachable(self, monitor: DesiredMonitor) -> bool:
        endpoint = _endpoint_for_monitor(monitor)
        if endpoint is None:
            # Non-TCP monitors (groups etc.) are always accepted.
            return True
        host, port = endpoint
        reachable = _tcp_connect(host, port, self._timeout)
        if not reachable:
            logger.warning(
                "Discovered endpoint is not reachable, skipping monitor",
                extra={
                    "monitor_name": monitor.payload.get("name"),
                    "host": host,
                    "port": port,
                    "key": monitor.identity_key,
                },
            )
        return reachable


class NullValidator:
    """No-op validator used when DISCOVERY_VALIDATE is false (default)."""

    def is_reachable(self, monitor: DesiredMonitor) -> bool:
        return True
