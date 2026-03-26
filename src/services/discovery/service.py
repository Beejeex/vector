from __future__ import annotations

import logging
from typing import Any

from src.models.desired import DesiredMonitor
from src.services.discovery.base import (
    DiscoveredService,
    DiscoveredWorkload,
    DiscoveryK8sClientProtocol,
    HttpProbeInfo,
    ServicePort,
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


def _find_probe_for_service_port(
    svc: DiscoveredService,
    port: ServicePort,
    workloads: list[DiscoveredWorkload],
) -> HttpProbeInfo | None:
    """Return a probe (liveness preferred, else readiness) from any workload selected by svc
    whose probe port matches the service's targetPort (or service port as fallback).

    This lets service monitors inherit the correct scheme and path from the deployment's
    probe configuration rather than guessing from the port name alone.
    """
    if not svc.selector:
        return None

    # Determine which container port to match against.
    if isinstance(port.target_port, int) and port.target_port > 0:
        target_port_num = port.target_port
    else:
        # Named targetPort or unknown — fall back to service port number.
        target_port_num = port.port

    for workload in workloads:
        # Skip workloads whose pods are not selected by this service.
        if not all(workload.pod_labels.get(k) == v for k, v in svc.selector.items()):
            continue
        for container_probes in workload.probes:
            probe = container_probes.liveness or container_probes.readiness
            if probe is not None and probe.port == target_port_num:
                return probe

    return None


class ServicePortDiscovery:
    """Produces HTTP monitors for Services with well-known HTTP port names."""

    def __init__(self, k8s: DiscoveryK8sClientProtocol) -> None:
        self._k8s = k8s

    def discover(self, namespace: str, group_name: str) -> list[DesiredMonitor]:
        monitors: list[DesiredMonitor] = []
        workloads = self._k8s.list_workloads(namespace)
        for svc in self._k8s.list_services(namespace):
            for port in svc.ports:
                if not _is_http_port(port.name or ""):
                    continue

                hostname = f"{svc.name}.{namespace}.svc.cluster.local"
                detail = port.name or str(port.port)
                key = make_identity_key(_SOURCE, namespace, svc.name, detail)
                display_name = f"{svc.name}-{detail}"

                probe_info = _find_probe_for_service_port(svc, port, workloads)
                if probe_info is not None:
                    # Probe config is authoritative: use its scheme and path.
                    scheme = probe_info.scheme.lower()
                    path = probe_info.path
                    logger.debug(
                        "Using probe scheme/path for service port monitor",
                        extra={"key": key, "scheme": scheme, "path": path},
                    )
                else:
                    # Fall back to port-name heuristics.
                    scheme = "https" if _is_https_port(port.name or "") else "http"
                    path = "/metrics" if _is_metrics_port(port.name or "") else ""

                url = f"{scheme}://{hostname}:{port.port}{path}"

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
