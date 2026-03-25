from __future__ import annotations

import logging
from typing import Optional

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from src.services.discovery.base import (
    DISCOVER_ANNOTATION,
    GROUP_ANNOTATION,
    ContainerProbes,
    DiscoveredIngress,
    DiscoveredNamespace,
    DiscoveredService,
    DiscoveredWorkload,
    HttpProbeInfo,
    IngressRule,
    ServicePort,
)

logger = logging.getLogger(__name__)


class DiscoveryK8sClient:
    """Read-only Kubernetes client for discovery sources.

    Translates raw kubernetes library objects into the simple data models
    defined in base.py so that discovery sources remain decoupled from
    the kubernetes library.
    """

    def __init__(self) -> None:
        try:
            config.load_incluster_config()
            logger.debug("Discovery K8s client: loaded in-cluster config")
        except config.ConfigException:
            config.load_kube_config()
            logger.debug("Discovery K8s client: loaded local kubeconfig")
        self._core = client.CoreV1Api()
        self._networking = client.NetworkingV1Api()
        self._apps = client.AppsV1Api()

    # ------------------------------------------------------------------
    # Namespace discovery
    # ------------------------------------------------------------------

    def list_opted_in_namespaces(self) -> list[DiscoveredNamespace]:
        try:
            result = self._core.list_namespace()
        except ApiException as exc:
            logger.error(
                "Failed to list namespaces for discovery",
                extra={"status": exc.status, "reason": exc.reason},
            )
            return []

        namespaces: list[DiscoveredNamespace] = []
        for ns in result.items:
            annotations = ns.metadata.annotations or {}
            if annotations.get(DISCOVER_ANNOTATION, "").lower() != "true":
                continue
            name = ns.metadata.name
            group_name = annotations.get(GROUP_ANNOTATION, name) or name
            namespaces.append(DiscoveredNamespace(name=name, group_name=group_name))

        return namespaces

    # ------------------------------------------------------------------
    # Ingress discovery
    # ------------------------------------------------------------------

    def list_ingresses(self, namespace: str) -> list[DiscoveredIngress]:
        try:
            result = self._networking.list_namespaced_ingress(namespace)
        except ApiException as exc:
            logger.warning(
                "Failed to list ingresses",
                extra={"namespace": namespace, "status": exc.status, "reason": exc.reason},
            )
            return []

        ingresses: list[DiscoveredIngress] = []
        for ing in result.items:
            name = ing.metadata.name
            spec = ing.spec or client.V1IngressSpec()

            # Build TLS host set
            tls_hosts: set[str] = set()
            for tls in (spec.tls or []):
                for host in (tls.hosts or []):
                    tls_hosts.add(host)

            rules: list[IngressRule] = []
            for rule in (spec.rules or []):
                host = rule.host
                if not host:
                    continue
                rules.append(IngressRule(host=host, tls=host in tls_hosts))

            if rules:
                ingresses.append(DiscoveredIngress(name=name, namespace=namespace, rules=rules))

        return ingresses

    # ------------------------------------------------------------------
    # Service discovery
    # ------------------------------------------------------------------

    def list_services(self, namespace: str) -> list[DiscoveredService]:
        try:
            result = self._core.list_namespaced_service(namespace)
        except ApiException as exc:
            logger.warning(
                "Failed to list services",
                extra={"namespace": namespace, "status": exc.status, "reason": exc.reason},
            )
            return []

        services: list[DiscoveredService] = []
        for svc in result.items:
            name = svc.metadata.name
            spec = svc.spec or client.V1ServiceSpec()
            cluster_ip = spec.cluster_ip or ""

            # Skip headless services (no ClusterIP)
            if cluster_ip in ("None", ""):
                continue

            ports: list[ServicePort] = []
            for p in (spec.ports or []):
                port_name = p.name or ""
                port_number = p.port
                protocol = p.protocol or "TCP"
                # target_port may be int or str (named port)
                raw_target = getattr(p, "target_port", None)
                if isinstance(raw_target, int):
                    target_port: int | str = raw_target
                elif isinstance(raw_target, str) and raw_target:
                    target_port = raw_target
                else:
                    target_port = 0
                if port_number:
                    ports.append(ServicePort(name=port_name, port=port_number, protocol=protocol, target_port=target_port))

            selector: dict[str, str] = dict(spec.selector or {})

            if ports:
                services.append(
                    DiscoveredService(
                        name=name,
                        namespace=namespace,
                        cluster_ip=cluster_ip,
                        ports=ports,
                        selector=selector,
                    )
                )

        return services

    # ------------------------------------------------------------------
    # Workload (Deployment + StatefulSet) discovery
    # ------------------------------------------------------------------

    def list_workloads(self, namespace: str) -> list[DiscoveredWorkload]:
        workloads: list[DiscoveredWorkload] = []
        workloads.extend(self._list_deployments(namespace))
        workloads.extend(self._list_statefulsets(namespace))
        return workloads

    def _list_deployments(self, namespace: str) -> list[DiscoveredWorkload]:
        try:
            result = self._apps.list_namespaced_deployment(namespace)
        except ApiException as exc:
            logger.warning(
                "Failed to list deployments",
                extra={"namespace": namespace, "status": exc.status, "reason": exc.reason},
            )
            return []
        return [self._workload_from_template(d.metadata.name, namespace, d.spec) for d in result.items]

    def _list_statefulsets(self, namespace: str) -> list[DiscoveredWorkload]:
        try:
            result = self._apps.list_namespaced_stateful_set(namespace)
        except ApiException as exc:
            logger.warning(
                "Failed to list statefulsets",
                extra={"namespace": namespace, "status": exc.status, "reason": exc.reason},
            )
            return []
        return [self._workload_from_template(s.metadata.name, namespace, s.spec) for s in result.items]

    def _workload_from_template(
        self, name: str, namespace: str, spec: Optional[object]
    ) -> DiscoveredWorkload:
        probes: list[ContainerProbes] = []
        if spec is None:
            return DiscoveredWorkload(name=name, namespace=namespace, probes=probes)

        template = getattr(spec, "template", None)
        pod_spec = getattr(template, "spec", None) if template else None
        containers = getattr(pod_spec, "containers", None) or []

        # Extract pod template labels so probe discovery can match services by selector.
        pod_template_metadata = getattr(template, "metadata", None) if template else None
        pod_labels: dict[str, str] = dict(getattr(pod_template_metadata, "labels", None) or {})

        for container in containers:
            liveness = _extract_http_probe(getattr(container, "liveness_probe", None))
            readiness = _extract_http_probe(getattr(container, "readiness_probe", None))
            if liveness or readiness:
                probes.append(
                    ContainerProbes(
                        container_name=container.name,
                        liveness=liveness,
                        readiness=readiness,
                    )
                )

        return DiscoveredWorkload(name=name, namespace=namespace, probes=probes, pod_labels=pod_labels)


def _extract_http_probe(probe: Optional[object]) -> Optional[HttpProbeInfo]:
    if probe is None:
        return None
    http_get = getattr(probe, "http_get", None)
    if http_get is None:
        return None
    path = getattr(http_get, "path", "/") or "/"
    port = getattr(http_get, "port", None)
    scheme = (getattr(http_get, "scheme", None) or "HTTP").upper()
    if port is None:
        return None
    # port can be int or str (named port) — we only handle numeric ports
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        return None
    return HttpProbeInfo(path=path, port=port_int, scheme=scheme)
