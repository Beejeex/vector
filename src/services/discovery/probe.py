from __future__ import annotations

import logging

from src.models.desired import DesiredMonitor
from src.services.discovery.base import (
    DiscoveredService,
    DiscoveryK8sClientProtocol,
    default_payload,
    make_identity_key,
)

logger = logging.getLogger(__name__)

_SOURCE = "probe"


def _find_service_for_workload(
    pod_labels: dict[str, str],
    services: list[DiscoveredService],
) -> DiscoveredService | None:
    """Return the first service whose non-empty selector is a subset of pod_labels."""
    for svc in services:
        if svc.selector and all(pod_labels.get(k) == v for k, v in svc.selector.items()):
            return svc
    return None


def _resolve_service_port(svc: DiscoveredService, container_port: int) -> int:
    """Return the service port number that routes to the given container port.

    Checks each ServicePort's target_port against container_port.
    Falls back to using container_port directly if no match is found (e.g. host networking).
    """
    for sp in svc.ports:
        # target_port may be an int (numeric) or str (named port — ignored here).
        if isinstance(sp.target_port, int) and sp.target_port == container_port:
            return sp.port
    # No matching targetPort found — fall back to the container port so the URL is still useful.
    logger.debug(
        "No service port maps to container port, using container port directly",
        extra={"service": svc.name, "container_port": container_port},
    )
    return container_port


class ProbeDiscovery:
    """Produces HTTP monitors derived from liveness/readiness probes on Deployments and StatefulSets.

    Resolves the in-cluster hostname by matching the workload's pod template labels against
    service selectors. Uses the service's port (not the container port) so Uptime Kuma connects
    through the service, not directly to the pod IP.
    Falls back to the workload name if no service is found.
    Only numeric ports are supported; named ports are skipped.
    """

    def __init__(self, k8s: DiscoveryK8sClientProtocol) -> None:
        self._k8s = k8s

    def discover(self, namespace: str, group_name: str) -> list[DesiredMonitor]:
        services = self._k8s.list_services(namespace)
        monitors: list[DesiredMonitor] = []
        for workload in self._k8s.list_workloads(namespace):
            svc = _find_service_for_workload(workload.pod_labels, services)
            if svc:
                hostname = f"{svc.name}.{namespace}.svc.cluster.local"
            else:
                hostname = f"{workload.name}.{namespace}.svc.cluster.local"
                if workload.probes:
                    logger.warning(
                        "No matching service for workload probes, using workload name as hostname",
                        extra={"namespace": namespace, "workload": workload.name},
                    )
            for container_probes in workload.probes:
                # Prefer liveness over readiness when both are present on the same container.
                probe = container_probes.liveness or container_probes.readiness
                probe_type = "liveness" if container_probes.liveness else "readiness"
                if probe is None:
                    continue

                scheme = probe.scheme.lower()
                # Use the service port if a service was matched — the service port routes
                # through kube-proxy and is reachable from Uptime Kuma; the container port
                # is only directly accessible on the pod IP.
                if svc is not None:
                    port = _resolve_service_port(svc, probe.port)
                else:
                    port = probe.port
                url = f"{scheme}://{hostname}:{port}{probe.path}"
                detail = f"{container_probes.container_name}-{probe_type}"
                key = make_identity_key(_SOURCE, namespace, workload.name, detail)
                display_name = f"{workload.name}-{container_probes.container_name}"

                monitors.append(
                    DesiredMonitor(
                        identity_key=key,
                        payload=default_payload(
                            "http",
                            display_name,
                            url=url,
                            description=(
                                f"Discovered from {probe_type} probe on "
                                f"{namespace}/{workload.name}/{container_probes.container_name}"
                            ),
                        ),
                        parent_name=group_name,
                        notification_names=[],
                        user_tags=[],
                    )
                )
                logger.debug(
                    "Discovered probe monitor",
                    extra={"key": key, "url": url},
                )

        return monitors
