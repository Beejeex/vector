from __future__ import annotations

import logging

from src.models.desired import DesiredMonitor
from src.services.discovery.base import (
    DiscoveryK8sClientProtocol,
    default_payload,
    make_identity_key,
)

logger = logging.getLogger(__name__)

_SOURCE = "database"

# Well-known database ports → (monitor display label, uptime kuma monitor type for TCP check)
_DB_PORTS: dict[int, str] = {
    5432: "postgres",
    6379: "redis",
    3306: "mysql",
    27017: "mongodb",
    1433: "sqlserver",
}


class DatabasePortDiscovery:
    """Produces TCP port monitors for Services exposing well-known database ports."""

    def __init__(self, k8s: DiscoveryK8sClientProtocol) -> None:
        self._k8s = k8s

    def discover(self, namespace: str, group_name: str) -> list[DesiredMonitor]:
        monitors: list[DesiredMonitor] = []
        for svc in self._k8s.list_services(namespace):
            for port in svc.ports:
                db_label = _DB_PORTS.get(port.port)
                if db_label is None:
                    continue

                hostname = f"{svc.name}.{namespace}.svc.cluster.local"
                key = make_identity_key(_SOURCE, namespace, svc.name, str(port.port))
                display_name = f"{svc.name} ({db_label})"

                monitors.append(
                    DesiredMonitor(
                        identity_key=key,
                        payload=default_payload(
                            "port",
                            display_name,
                            hostname=hostname,
                            port=port.port,
                            description=f"Discovered {db_label} port on {namespace}/{svc.name}",
                        ),
                        parent_name=group_name,
                        notification_names=[],
                        user_tags=[],
                    )
                )
                logger.debug(
                    "Discovered database port monitor",
                    extra={"key": key, "hostname": hostname, "port": port.port, "db": db_label},
                )

        return monitors
