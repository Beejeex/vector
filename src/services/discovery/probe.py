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
) -> str | None:
    """Return the name of the first service whose non-empty selector is a subset of pod_labels."""
    for svc in services:
        if svc.selector and all(pod_labels.get(k) == v for k, v in svc.selector.items()):
            return svc.name
    return None


class ProbeDiscovery:
    """Produces HTTP monitors derived from liveness/readiness probes on Deployments and StatefulSets.

    Resolves the in-cluster hostname by matching the workload's pod template labels against
    service selectors. Falls back to the workload name if no service is found.
    Only numeric ports are supported; named ports are skipped.
    """

    def __init__(self, k8s: DiscoveryK8sClientProtocol) -> None:
        self._k8s = k8s

    def discover(self, namespace: str, group_name: str) -> list[DesiredMonitor]:
        services = self._k8s.list_services(namespace)
        monitors: list[DesiredMonitor] = []
        for workload in self._k8s.list_workloads(namespace):
            svc_name = _find_service_for_workload(workload.pod_labels, services)
            if svc_name:
                hostname = f"{svc_name}.{namespace}.svc.cluster.local"
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
                url = f"{scheme}://{hostname}:{probe.port}{probe.path}"
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
