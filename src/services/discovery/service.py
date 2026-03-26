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


def _scheme_for_unnamed_port(port_number: int) -> str | None:
    """Return http/https for well-known port numbers when the port has no name.

    Many services (e.g. Emby, Jellyfin) expose port 80 or 443 without naming it.
    This lets service discovery still produce a monitor for these ports rather than
    silently skipping them.

    Returns None if the port number is not well-known.
    """
    _WELL_KNOWN: dict[int, str] = {
        80: "http",
        443: "https",
        8080: "http",
        8443: "https",
    }
    return _WELL_KNOWN.get(port_number)


def _find_probe_for_service_port(
    svc: DiscoveredService,
    port: ServicePort,
    workloads: list[DiscoveredWorkload],
) -> HttpProbeInfo | None:
    """Return a probe (liveness preferred, else readiness) from any workload selected by svc
    whose probe port matches the service's targetPort (or service port as fallback).

    When a probe is found the service port is considered *owned* by ProbeDiscovery.
    ServicePortDiscovery will skip this port to avoid creating a duplicate monitor.
    """
    if not svc.selector:
        return None

    # Determine which container port number to match against the probe port.
    # target_port may be an int (numeric) or str (named — resolve via workload container ports).
    if isinstance(port.target_port, int) and port.target_port > 0:
        target_port_num: int | None = port.target_port
    elif isinstance(port.target_port, str) and port.target_port:
        # Named targetPort — will be resolved per-workload below.
        target_port_num = None
    else:
        # Unknown — fall back to service port number.
        target_port_num = port.port

    for workload in workloads:
        # Skip workloads whose pods are not selected by this service.
        if not all(workload.pod_labels.get(k) == v for k, v in svc.selector.items()):
            continue

        # Resolve a named targetPort against this workload's container ports.
        if target_port_num is None and isinstance(port.target_port, str):
            resolved = workload.named_container_ports.get(port.target_port)
            effective_target = resolved if resolved is not None else port.port
        else:
            effective_target = target_port_num if target_port_num is not None else port.port

        for container_probes in workload.probes:
            probe = container_probes.liveness or container_probes.readiness
            if probe is not None and probe.port == effective_target:
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
                port_name = port.name or ""

                # Determine the scheme: named port takes priority, then well-known number.
                if _is_http_port(port_name):
                    scheme_from_name: str | None = "https" if _is_https_port(port_name) else "http"
                else:
                    scheme_from_name = None

                unnamed_scheme = _scheme_for_unnamed_port(port.port) if not port_name else None

                scheme = scheme_from_name or unnamed_scheme
                if scheme is None:
                    continue  # not an HTTP/HTTPS port we recognise

                hostname = f"{svc.name}.{namespace}.svc.cluster.local"
                detail = port_name or str(port.port)
                key = make_identity_key(_SOURCE, namespace, svc.name, detail)
                display_name = f"{svc.name}-{detail}"

                probe_info = _find_probe_for_service_port(svc, port, workloads)
                if probe_info is not None:
                    # This port is already covered by ProbeDiscovery (which derives
                    # its URL from the workload's liveness/readiness probe directly).
                    # Skip it here to avoid a duplicate monitor.
                    logger.debug(
                        "Skipping service port — covered by probe discovery",
                        extra={"key": key, "target_port": port.target_port},
                    )
                    continue

                path = "/metrics" if _is_metrics_port(port_name) else ""
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
