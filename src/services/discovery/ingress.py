from __future__ import annotations

import logging

from src.models.desired import DesiredMonitor
from src.services.discovery.base import (
    DiscoveryK8sClientProtocol,
    default_payload,
    make_identity_key,
)

logger = logging.getLogger(__name__)

_SOURCE = "ingress"


class IngressDiscovery:
    """Produces one HTTP monitor per Ingress host in opted-in namespaces.

    ``default_scheme`` controls what scheme is used when the Ingress spec has no
    ``tls:`` entry.  Defaults to ``https`` because most modern ingress controllers
    (BunkerWeb, Traefik, nginx + cert-manager) manage TLS outside the Ingress spec
    and serve HTTPS externally regardless of the presence of a ``tls:`` block.
    Set ``DISCOVERY_INGRESS_DEFAULT_SCHEME=http`` to override.
    """

    def __init__(
        self,
        k8s: DiscoveryK8sClientProtocol,
        default_scheme: str = "https",
    ) -> None:
        self._k8s = k8s
        self._default_scheme = default_scheme.lower()

    def discover(self, namespace: str, group_name: str) -> list[DesiredMonitor]:
        monitors: list[DesiredMonitor] = []
        for ingress in self._k8s.list_ingresses(namespace):
            for rule in ingress.rules:
                # tls=True means the host is explicitly listed in spec.tls[].hosts.
                # tls=False falls back to the configured default scheme.
                scheme = "https" if rule.tls else self._default_scheme
                url = f"{scheme}://{rule.host}"
                key = make_identity_key(_SOURCE, namespace, ingress.name, rule.host)
                display_name = rule.host

                monitors.append(
                    DesiredMonitor(
                        identity_key=key,
                        payload=default_payload(
                            "http",
                            display_name,
                            url=url,
                            description=f"Discovered from Ingress {namespace}/{ingress.name}",
                        ),
                        parent_name=group_name,
                        notification_names=[],
                        user_tags=[],
                    )
                )
                logger.debug(
                    "Discovered ingress monitor",
                    extra={"key": key, "url": url},
                )

        return monitors
