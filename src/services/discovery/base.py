from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from src.models.desired import DesiredMonitor

# Annotation key used on Namespace to opt into discovery.
DISCOVER_ANNOTATION = "vector.beejeex.github.io/discover"
# Annotation key used on Namespace to override the Uptime Kuma group name.
GROUP_ANNOTATION = "vector.beejeex.github.io/group"

DISCOVERY_KEY_PREFIX = "discovered"


def make_identity_key(source: str, namespace: str, resource: str, detail: str) -> str:
    """Build a stable identity key for a discovered monitor."""
    return f"{DISCOVERY_KEY_PREFIX}:{source}:{namespace}/{resource}/{detail}"


def make_group_key(namespace: str) -> str:
    """Build the identity key for a namespace's auto-created group monitor."""
    return f"{DISCOVERY_KEY_PREFIX}:group:{namespace}"


def default_payload(monitor_type: str, name: str, **kwargs: Any) -> dict[str, Any]:
    """Return a base payload dict with sensible defaults for all owned fields."""
    payload: dict[str, Any] = {
        "type": monitor_type,
        "name": name,
        "interval": 60,
        "timeout": 30,
        "retryInterval": 60,
        "resendInterval": 0,
        "maxretries": 1,
        "upsideDown": False,
        "expiryNotification": False,
        "ignoreTls": False,
        "maxredirects": 10,
        "method": "GET",
        "invertKeyword": False,
        "packetSize": 56,
        "dns_resolve_type": "A",
        "kafkaProducerSsl": False,
        "kafkaProducerAllowAutoTopicCreation": False,
        "grpcEnableTls": False,
    }
    payload.update(kwargs)
    return payload


# ---------------------------------------------------------------------------
# Simple data models returned by DiscoveryK8sClientProtocol.
# These decouple discovery sources from the kubernetes library types.
# ---------------------------------------------------------------------------


@dataclass
class DiscoveredNamespace:
    name: str
    group_name: str  # annotation value or defaults to namespace name


@dataclass
class IngressRule:
    host: str
    tls: bool  # True when the host is covered by an Ingress TLS entry


@dataclass
class DiscoveredIngress:
    name: str
    namespace: str
    rules: list[IngressRule] = field(default_factory=list)


@dataclass
class ServicePort:
    name: str  # may be empty string
    port: int
    protocol: str  # "TCP", "UDP", etc.


@dataclass
class DiscoveredService:
    name: str
    namespace: str
    cluster_ip: str
    ports: list[ServicePort] = field(default_factory=list)


@dataclass
class HttpProbeInfo:
    path: str
    port: int
    scheme: str  # "HTTP" or "HTTPS"


@dataclass
class ContainerProbes:
    container_name: str
    liveness: Optional[HttpProbeInfo]
    readiness: Optional[HttpProbeInfo]


@dataclass
class DiscoveredWorkload:
    """Represents a Deployment or StatefulSet."""
    name: str
    namespace: str
    probes: list[ContainerProbes] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class DiscoveryK8sClientProtocol(Protocol):
    def list_opted_in_namespaces(self) -> list[DiscoveredNamespace]: ...
    def list_ingresses(self, namespace: str) -> list[DiscoveredIngress]: ...
    def list_services(self, namespace: str) -> list[DiscoveredService]: ...
    def list_workloads(self, namespace: str) -> list[DiscoveredWorkload]: ...


class DiscoverySourceProtocol(Protocol):
    def discover(self, namespace: str, group_name: str) -> list[DesiredMonitor]: ...
