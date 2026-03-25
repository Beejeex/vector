from __future__ import annotations

import logging
from typing import Any

from src.models.desired import DesiredMonitor
from src.services.discovery.base import (
    DiscoveryK8sClientProtocol,
    default_payload,
    make_identity_key,
)

logger = logging.getLogger(__name__)

_SOURCE = "service"

# Port names that indicate an HTTPS endpoint (exact match takes priority).
_HTTPS_PORT_NAMES: frozenset[str] = frozenset({"https"})


def _is_http_port(port_name: str) -> bool:
    """Return True if the port name indicates an HTTP/HTTPS endpoint.

    Matches:
    - Exact names: ``http``, ``https``, ``web``, ``health``, ``metrics``
    - Prefix ``http-``: covers ``http-web``, ``http-metrics``, ``http-alt``, etc.
    - Prefix ``https-``: covers ``https-web`` etc.

    Observed real-world examples: ``http-web``, ``http-metrics``, ``reloader-web``
    only the first two should match; generic suffixes like ``reloader-web`` are
    excluded because they don't reliably indicate a plain HTTP service.
    """
    name = port_name.lower()
    _EXACT_HTTP_NAMES: frozenset[str] = frozenset({"http", "https", "web", "health", "metrics"})
    return name in _EXACT_HTTP_NAMES or name.startswith("http-") or name.startswith("https-")


def _is_https_port(port_name: str) -> bool:
    return port_name.lower() in _HTTPS_PORT_NAMES or port_name.lower().startswith("https-")


def _is_metrics_port(port_name: str) -> bool:
    """Return True for ports that serve Prometheus metrics at /metrics rather than / ."""
    name = port_name.lower()
    return name == "metrics" or name.endswith("-metrics")


class ServicePortDiscovery:
    """Produces HTTP monitors for Services with well-known HTTP port names."""

    def __init__(self, k8s: DiscoveryK8sClientProtocol) -> None:
        self._k8s = k8s

    def discover(self, namespace: str, group_name: str) -> list[DesiredMonitor]:
        monitors: list[DesiredMonitor] = []
        for svc in self._k8s.list_services(namespace):
            for port in svc.ports:
                if not _is_http_port(port.name or ""):
                    continue

                scheme = "https" if _is_https_port(port.name or "") else "http"
                hostname = f"{svc.name}.{namespace}.svc.cluster.local"
                path = "/metrics" if _is_metrics_port(port.name or "") else ""
                url = f"{scheme}://{hostname}:{port.port}{path}"
                detail = port.name or str(port.port)
                key = make_identity_key(_SOURCE, namespace, svc.name, detail)
                display_name = f"{svc.name}-{detail}"

                extra_fields: dict[str, Any] = {}
                if scheme == "https":
                    extra_fields["ignoreTls"] = True

                monitors.append(
                    DesiredMonitor(
                        identity_key=key,
                        payload=default_payload(
                            "http",
                            display_name,
                            url=url,
                            description=f"Discovered from Service {namespace}/{svc.name} port {detail}",
                            **extra_fields,
                        ),
                        parent_name=group_name,
                        notification_names=[],
                        user_tags=[],
                    )
                )
                logger.debug(
                    "Discovered service port monitor",
                    extra={"key": key, "url": url},
                )

        return monitors
