"""Tests for the discovery sources and runner."""
from __future__ import annotations

import pytest
from unittest.mock import patch

from src.models.desired import DesiredMonitor
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
    make_group_key,
    make_identity_key,
)
from src.services.discovery.database import DatabasePortDiscovery
from src.services.discovery.ingress import IngressDiscovery
from src.services.discovery.probe import ProbeDiscovery
from src.services.discovery.runner import DiscoveryRunner, _make_group_monitor
from src.services.discovery.service import ServicePortDiscovery
from src.services.discovery.validator import EndpointValidator, NullValidator, _endpoint_for_monitor


# ---------------------------------------------------------------------------
# Mock K8s client
# ---------------------------------------------------------------------------


class _MockDiscoveryK8s:
    """Minimal mock for DiscoveryK8sClientProtocol."""

    def __init__(
        self,
        namespaces: list[DiscoveredNamespace] | None = None,
        ingresses: dict[str, list[DiscoveredIngress]] | None = None,
        services: dict[str, list[DiscoveredService]] | None = None,
        workloads: dict[str, list[DiscoveredWorkload]] | None = None,
    ) -> None:
        self._namespaces = namespaces or []
        self._ingresses = ingresses or {}
        self._services = services or {}
        self._workloads = workloads or {}

    def list_opted_in_namespaces(self) -> list[DiscoveredNamespace]:
        return self._namespaces

    def list_ingresses(self, namespace: str) -> list[DiscoveredIngress]:
        return self._ingresses.get(namespace, [])

    def list_services(self, namespace: str) -> list[DiscoveredService]:
        return self._services.get(namespace, [])

    def list_workloads(self, namespace: str) -> list[DiscoveredWorkload]:
        return self._workloads.get(namespace, [])


# ---------------------------------------------------------------------------
# Identity key helpers
# ---------------------------------------------------------------------------


class TestIdentityKeys:
    def test_make_identity_key(self) -> None:
        key = make_identity_key("ingress", "production", "my-ingress", "app.example.com")
        assert key == "discovered:ingress:production/my-ingress/app.example.com"

    def test_make_group_key(self) -> None:
        key = make_group_key("production")
        assert key == "discovered:group:production"


# ---------------------------------------------------------------------------
# IngressDiscovery
# ---------------------------------------------------------------------------


class TestIngressDiscovery:
    def _k8s(self, namespace: str, ingresses: list[DiscoveredIngress]) -> _MockDiscoveryK8s:
        return _MockDiscoveryK8s(ingresses={namespace: ingresses})

    def test_default_scheme_is_https_when_no_tls(self) -> None:
        """Real-world: BunkerWeb/Traefik manage TLS outside the spec, no tls: entry."""
        ing = DiscoveredIngress(
            name="authentik-ingress",
            namespace="authentik",
            rules=[IngressRule(host="idp.hidden-hive.net", tls=False)],
        )
        monitors = IngressDiscovery(self._k8s("authentik", [ing])).discover("authentik", "Authentik")
        assert monitors[0].payload["url"] == "https://idp.hidden-hive.net"

    def test_explicit_tls_entry_overrides_default_scheme(self) -> None:
        ing = DiscoveredIngress(
            name="secure-ingress",
            namespace="prod",
            rules=[IngressRule(host="secure.example.com", tls=True)],
        )
        monitors = IngressDiscovery(self._k8s("prod", [ing])).discover("prod", "Production")
        assert monitors[0].payload["url"] == "https://secure.example.com"

    def test_http_monitor_for_non_tls_host(self) -> None:
        """When default_scheme=http, non-TLS host gets http:// URL."""
        ing = DiscoveredIngress(
            name="my-ingress",
            namespace="default",
            rules=[IngressRule(host="app.example.com", tls=False)],
        )
        monitors = IngressDiscovery(
            self._k8s("default", [ing]), default_scheme="http"
        ).discover("default", "Default")
        assert len(monitors) == 1
        m = monitors[0]
        assert m.payload["url"] == "http://app.example.com"
        assert m.payload["type"] == "http"
        assert m.parent_name == "Default"
        assert "discovered:ingress:default/my-ingress/app.example.com" == m.identity_key

    def test_https_monitor_for_tls_host_with_http_default(self) -> None:
        """Explicit TLS entry always wins, even when default_scheme=http."""
        ing = DiscoveredIngress(
            name="secure-ingress",
            namespace="prod",
            rules=[IngressRule(host="secure.example.com", tls=True)],
        )
        monitors = IngressDiscovery(
            self._k8s("prod", [ing]), default_scheme="http"
        ).discover("prod", "Production")
        assert monitors[0].payload["url"] == "https://secure.example.com"

    def test_multiple_rules_produce_multiple_monitors(self) -> None:
        ing = DiscoveredIngress(
            name="multi",
            namespace="default",
            rules=[
                IngressRule(host="a.example.com", tls=False),
                IngressRule(host="b.example.com", tls=True),
            ],
        )
        monitors = IngressDiscovery(self._k8s("default", [ing])).discover("default", "G")
        assert len(monitors) == 2
        urls = {m.payload["url"] for m in monitors}
        # Both use https: b because of explicit TLS, a because default is https
        assert "https://a.example.com" in urls
        assert "https://b.example.com" in urls

    def test_empty_ingress_list_returns_empty(self) -> None:
        monitors = IngressDiscovery(self._k8s("default", [])).discover("default", "G")
        assert monitors == []

    def test_distinct_identity_keys(self) -> None:
        ing = DiscoveredIngress(
            name="ing",
            namespace="ns",
            rules=[
                IngressRule(host="a.example.com", tls=False),
                IngressRule(host="b.example.com", tls=False),
            ],
        )
        monitors = IngressDiscovery(self._k8s("ns", [ing])).discover("ns", "G")
        keys = [m.identity_key for m in monitors]
        assert len(keys) == len(set(keys)), "Identity keys must be unique"

    def test_ingress_path_appended_to_url(self) -> None:
        """When an ingress only serves at /hiveui (e.g. BunkerWeb UI), monitor URL includes the path."""
        ing = DiscoveredIngress(
            name="bunkerweb-ingress",
            namespace="bunkerweb",
            rules=[IngressRule(host="bkw.hidden-hive.net", tls=False, path="/hiveui")],
        )
        monitors = IngressDiscovery(self._k8s("bunkerweb", [ing])).discover("bunkerweb", "G")
        assert monitors[0].payload["url"] == "https://bkw.hidden-hive.net/hiveui"

    def test_ingress_root_path_omitted(self) -> None:
        """Ingress with root path / → URL ends at hostname with no path suffix."""
        ing = DiscoveredIngress(
            name="my-ingress",
            namespace="default",
            rules=[IngressRule(host="app.example.com", tls=False, path="")],
        )
        monitors = IngressDiscovery(self._k8s("default", [ing])).discover("default", "G")
        assert monitors[0].payload["url"] == "https://app.example.com"


# ---------------------------------------------------------------------------
# ServicePortDiscovery
# ---------------------------------------------------------------------------


class TestServicePortDiscovery:
    def _k8s(self, namespace: str, services: list[DiscoveredService]) -> _MockDiscoveryK8s:
        return _MockDiscoveryK8s(services={namespace: services})

    def test_http_port_name_produces_monitor(self) -> None:
        svc = DiscoveredService(
            name="my-app",
            namespace="default",
            cluster_ip="10.0.0.1",
            ports=[ServicePort(name="http", port=8080, protocol="TCP")],
        )
        monitors = ServicePortDiscovery(self._k8s("default", [svc])).discover("default", "G")
        assert len(monitors) == 1
        m = monitors[0]
        assert m.payload["url"] == "http://my-app.default.svc.cluster.local:8080"
        assert m.payload["type"] == "http"

    def test_https_port_name_uses_https_scheme(self) -> None:
        svc = DiscoveredService(
            name="my-app",
            namespace="default",
            cluster_ip="10.0.0.1",
            ports=[ServicePort(name="https", port=8443, protocol="TCP")],
        )
        monitors = ServicePortDiscovery(self._k8s("default", [svc])).discover("default", "G")
        assert monitors[0].payload["url"] == "https://my-app.default.svc.cluster.local:8443"

    def test_https_port_sets_ignore_tls(self) -> None:
        """Internal cluster HTTPS services commonly use self-signed certs."""
        svc = DiscoveredService(
            name="authentik-server",
            namespace="authentik",
            cluster_ip="10.43.0.1",
            ports=[ServicePort(name="https", port=443, protocol="TCP")],
        )
        monitors = ServicePortDiscovery(self._k8s("authentik", [svc])).discover("authentik", "G")
        assert monitors[0].payload.get("ignoreTls") is True

    def test_http_port_does_not_set_ignore_tls(self) -> None:
        svc = DiscoveredService(
            name="my-app",
            namespace="default",
            cluster_ip="10.0.0.1",
            ports=[ServicePort(name="http", port=8080, protocol="TCP")],
        )
        monitors = ServicePortDiscovery(self._k8s("default", [svc])).discover("default", "G")
        assert not monitors[0].payload.get("ignoreTls")

    def test_http_prefix_port_name_matches(self) -> None:
        """Real-world: Prometheus uses http-web, http-metrics port names."""
        svc = DiscoveredService(
            name="prometheus-kube-prometheus-prometheus",
            namespace="prometheus",
            cluster_ip="10.43.51.56",
            ports=[
                ServicePort(name="http-web", port=9090, protocol="TCP"),
                ServicePort(name="reloader-web", port=8080, protocol="TCP"),  # should NOT match
            ],
        )
        monitors = ServicePortDiscovery(self._k8s("prometheus", [svc])).discover("prometheus", "G")
        assert len(monitors) == 1
        assert ":9090" in monitors[0].payload["url"]

    def test_http_metrics_port_name_matches(self) -> None:
        """Real-world: node-exporter uses http-metrics port name."""
        svc = DiscoveredService(
            name="prometheus-prometheus-node-exporter",
            namespace="prometheus",
            cluster_ip="10.43.128.84",
            ports=[ServicePort(name="http-metrics", port=9100, protocol="TCP")],
        )
        monitors = ServicePortDiscovery(self._k8s("prometheus", [svc])).discover("prometheus", "G")
        assert len(monitors) == 1
        assert ":9100" in monitors[0].payload["url"]

    def test_metrics_port_appends_metrics_path(self) -> None:
        """Ports named 'metrics' or 'http-metrics' serve at /metrics, not /."""
        svc = DiscoveredService(
            name="authentik-server-metrics",
            namespace="authentik",
            cluster_ip="10.43.0.2",
            ports=[ServicePort(name="metrics", port=9300, protocol="TCP")],
        )
        monitors = ServicePortDiscovery(self._k8s("authentik", [svc])).discover("authentik", "G")
        assert monitors[0].payload["url"].endswith("/metrics")

    def test_http_metrics_port_appends_metrics_path(self) -> None:
        """http-metrics port name (Prometheus convention) also gets /metrics path."""
        svc = DiscoveredService(
            name="node-exporter",
            namespace="prometheus",
            cluster_ip="10.43.0.3",
            ports=[ServicePort(name="http-metrics", port=9100, protocol="TCP")],
        )
        monitors = ServicePortDiscovery(self._k8s("prometheus", [svc])).discover("prometheus", "G")
        assert monitors[0].payload["url"].endswith("/metrics")

    def test_non_metrics_port_has_no_path(self) -> None:
        svc = DiscoveredService(
            name="my-app",
            namespace="default",
            cluster_ip="10.0.0.1",
            ports=[ServicePort(name="http", port=8080, protocol="TCP")],
        )
        monitors = ServicePortDiscovery(self._k8s("default", [svc])).discover("default", "G")
        url = monitors[0].payload["url"]
        assert not url.endswith("/metrics")
        # URL should end with port, no path
        assert url == "http://my-app.default.svc.cluster.local:8080"

    def test_unknown_port_name_is_skipped(self) -> None:
        svc = DiscoveredService(
            name="my-app",
            namespace="default",
            cluster_ip="10.0.0.1",
            ports=[ServicePort(name="grpc", port=9000, protocol="TCP")],
        )
        monitors = ServicePortDiscovery(self._k8s("default", [svc])).discover("default", "G")
        assert monitors == []

    def test_reloader_web_is_skipped(self) -> None:
        """'reloader-web' should not match — it's not an http-prefixed name."""
        svc = DiscoveredService(
            name="operator",
            namespace="prometheus",
            cluster_ip="10.43.18.245",
            ports=[ServicePort(name="reloader-web", port=8080, protocol="TCP")],
        )
        monitors = ServicePortDiscovery(self._k8s("prometheus", [svc])).discover("prometheus", "G")
        assert monitors == []

    def test_multiple_matching_ports_on_one_service(self) -> None:
        svc = DiscoveredService(
            name="multi",
            namespace="default",
            cluster_ip="10.0.0.2",
            ports=[
                ServicePort(name="http", port=80, protocol="TCP"),
                ServicePort(name="metrics", port=9090, protocol="TCP"),
            ],
        )
        monitors = ServicePortDiscovery(self._k8s("default", [svc])).discover("default", "G")
        assert len(monitors) == 2

    def test_parent_name_set_correctly(self) -> None:
        svc = DiscoveredService(
            name="svc",
            namespace="ns",
            cluster_ip="10.0.0.3",
            ports=[ServicePort(name="web", port=80, protocol="TCP")],
        )
        monitors = ServicePortDiscovery(self._k8s("ns", [svc])).discover("ns", "My Group")
        assert monitors[0].parent_name == "My Group"

    def test_probe_port_match_skips_service_monitor(self) -> None:
        """When a workload probe targets the service's port, ServicePortDiscovery skips
        that port — ProbeDiscovery owns it, so no duplicate monitor is created.

        Real-world case: inscripta-backend has both a probe monitor (from ProbeDiscovery)
        and would previously emit a service monitor for the same 'http' port.
        """
        svc = DiscoveredService(
            name="inscripta-backend",
            namespace="inscripta",
            cluster_ip="10.43.0.10",
            ports=[ServicePort(name="http", port=8080, protocol="TCP", target_port=8080)],
            selector={"app": "inscripta-backend"},
        )
        workload = DiscoveredWorkload(
            name="inscripta-backend",
            namespace="inscripta",
            pod_labels={"app": "inscripta-backend"},
            probes=[
                ContainerProbes(
                    container_name="backend",
                    liveness=HttpProbeInfo(path="/healthz", port=8080, scheme="HTTP"),
                    readiness=None,
                )
            ],
        )
        k8s = _MockDiscoveryK8s(services={"inscripta": [svc]}, workloads={"inscripta": [workload]})
        monitors = ServicePortDiscovery(k8s).discover("inscripta", "G")
        # Service port is covered by the probe — no duplicate service monitor.
        assert monitors == []

    def test_probe_scheme_overrides_port_name_heuristic(self) -> None:
        """Kept for documentation: if a probe IS found, the port is skipped entirely
        (previously we used the probe's scheme — now we skip to avoid duplicates).
        ProbeDiscovery will create the authoritative monitor using the probe data.
        """
        svc = DiscoveredService(
            name="prometheus-operated",
            namespace="monitoring",
            cluster_ip="10.43.0.10",
            ports=[ServicePort(name="http-web", port=9090, protocol="TCP", target_port=9090)],
            selector={"app": "prometheus"},
        )
        workload = DiscoveredWorkload(
            name="prometheus",
            namespace="monitoring",
            pod_labels={"app": "prometheus"},
            probes=[
                ContainerProbes(
                    container_name="prometheus",
                    liveness=HttpProbeInfo(path="/-/healthy", port=9090, scheme="HTTPS"),
                    readiness=None,
                )
            ],
        )
        k8s = _MockDiscoveryK8s(services={"monitoring": [svc]}, workloads={"monitoring": [workload]})
        monitors = ServicePortDiscovery(k8s).discover("monitoring", "G")
        # Port is owned by ProbeDiscovery — no service monitor emitted.
        assert monitors == []

    def test_probe_path_overrides_port_name_heuristic(self) -> None:
        """Port with matching probe is skipped regardless of probe path."""
        svc = DiscoveredService(
            name="prometheus-operated",
            namespace="monitoring",
            cluster_ip="10.43.0.10",
            ports=[ServicePort(name="http-web", port=9090, protocol="TCP", target_port=9090)],
            selector={"app": "prometheus"},
        )
        workload = DiscoveredWorkload(
            name="prometheus",
            namespace="monitoring",
            pod_labels={"app": "prometheus"},
            probes=[
                ContainerProbes(
                    container_name="prometheus",
                    liveness=HttpProbeInfo(path="/-/healthy", port=9090, scheme="HTTP"),
                    readiness=None,
                )
            ],
        )
        k8s = _MockDiscoveryK8s(services={"monitoring": [svc]}, workloads={"monitoring": [workload]})
        monitors = ServicePortDiscovery(k8s).discover("monitoring", "G")
        assert monitors == []

    def test_probe_not_matched_falls_back_to_port_name(self) -> None:
        """When no workload probe targets the port, port-name heuristic still applies."""
        svc = DiscoveredService(
            name="my-app",
            namespace="default",
            cluster_ip="10.0.0.1",
            ports=[ServicePort(name="https", port=8443, protocol="TCP", target_port=8443)],
            selector={"app": "my-app"},
        )
        # Workload probes on a different port — should not affect this service port.
        workload = DiscoveredWorkload(
            name="my-app",
            namespace="default",
            pod_labels={"app": "my-app"},
            probes=[
                ContainerProbes(
                    container_name="app",
                    liveness=HttpProbeInfo(path="/health", port=9999, scheme="HTTP"),
                    readiness=None,
                )
            ],
        )
        k8s = _MockDiscoveryK8s(services={"default": [svc]}, workloads={"default": [workload]})
        monitors = ServicePortDiscovery(k8s).discover("default", "G")
        assert len(monitors) == 1
        # Falls back to https heuristic from port name.
        assert monitors[0].payload["url"].startswith("https://")

    def test_probe_with_no_selector_falls_back_to_port_name(self) -> None:
        """Services without selectors (e.g. ExternalName) skip the probe lookup."""
        svc = DiscoveredService(
            name="external-svc",
            namespace="default",
            cluster_ip="10.0.0.2",
            ports=[ServicePort(name="http", port=80, protocol="TCP", target_port=80)],
            selector={},  # no selector
        )
        workload = DiscoveredWorkload(
            name="app",
            namespace="default",
            pod_labels={"app": "app"},
            probes=[
                ContainerProbes(
                    container_name="app",
                    liveness=HttpProbeInfo(path="/healthz", port=80, scheme="HTTPS"),
                    readiness=None,
                )
            ],
        )
        k8s = _MockDiscoveryK8s(services={"default": [svc]}, workloads={"default": [workload]})
        monitors = ServicePortDiscovery(k8s).discover("default", "G")
        # Falls back to http (from port name "http"), ignores workload probe.
        assert monitors[0].payload["url"].startswith("http://")
        assert not monitors[0].payload.get("ignoreTls")

    def test_unnamed_port_80_treated_as_http(self) -> None:
        """Real-world: Emby/Jellyfin expose port 80 with no name.

        Without this heuristic, service discovery produces no monitor and
        the namespace group is suppressed (all children filtered).
        """
        svc = DiscoveredService(
            name="svc-emby",
            namespace="emby",
            cluster_ip="10.43.85.179",
            ports=[ServicePort(name="", port=80, protocol="TCP", target_port=8096)],
        )
        monitors = ServicePortDiscovery(self._k8s("emby", [svc])).discover("emby", "G")
        assert len(monitors) == 1
        assert monitors[0].payload["url"] == "http://svc-emby.emby.svc.cluster.local:80"

    def test_unnamed_port_443_treated_as_https_with_ignore_tls(self) -> None:
        svc = DiscoveredService(
            name="my-svc",
            namespace="ns",
            cluster_ip="10.0.0.1",
            ports=[ServicePort(name="", port=443, protocol="TCP", target_port=8443)],
        )
        monitors = ServicePortDiscovery(self._k8s("ns", [svc])).discover("ns", "G")
        assert len(monitors) == 1
        assert monitors[0].payload["url"] == "https://my-svc.ns.svc.cluster.local:443"
        assert monitors[0].payload.get("ignoreTls") is True

    def test_unnamed_non_well_known_port_skipped(self) -> None:
        """Ports without a name that aren't 80/443/8080/8443 are still skipped."""
        svc = DiscoveredService(
            name="my-svc",
            namespace="ns",
            cluster_ip="10.0.0.1",
            ports=[ServicePort(name="", port=9999, protocol="TCP")],
        )
        monitors = ServicePortDiscovery(self._k8s("ns", [svc])).discover("ns", "G")
        assert monitors == []


# ---------------------------------------------------------------------------
# ProbeDiscovery
# ---------------------------------------------------------------------------


class TestProbeDiscovery:
    def _k8s(self, namespace: str, workloads: list[DiscoveredWorkload]) -> _MockDiscoveryK8s:
        return _MockDiscoveryK8s(workloads={namespace: workloads})

    def test_liveness_probe_produces_monitor(self) -> None:
        workload = DiscoveredWorkload(
            name="my-app",
            namespace="default",
            probes=[
                ContainerProbes(
                    container_name="app",
                    liveness=HttpProbeInfo(path="/healthz", port=8080, scheme="HTTP"),
                    readiness=None,
                )
            ],
        )
        monitors = ProbeDiscovery(self._k8s("default", [workload])).discover("default", "G")
        assert len(monitors) == 1
        assert monitors[0].payload["url"] == "http://my-app.default.svc.cluster.local:8080/healthz"

    def test_readiness_probe_used_when_no_liveness(self) -> None:
        workload = DiscoveredWorkload(
            name="svc",
            namespace="ns",
            probes=[
                ContainerProbes(
                    container_name="c",
                    liveness=None,
                    readiness=HttpProbeInfo(path="/ready", port=9090, scheme="HTTP"),
                )
            ],
        )
        monitors = ProbeDiscovery(self._k8s("ns", [workload])).discover("ns", "G")
        assert "/ready" in monitors[0].payload["url"]
        assert ":9090" in monitors[0].payload["url"]

    def test_liveness_preferred_over_readiness(self) -> None:
        workload = DiscoveredWorkload(
            name="svc",
            namespace="ns",
            probes=[
                ContainerProbes(
                    container_name="c",
                    liveness=HttpProbeInfo(path="/live", port=8080, scheme="HTTP"),
                    readiness=HttpProbeInfo(path="/ready", port=8081, scheme="HTTP"),
                )
            ],
        )
        monitors = ProbeDiscovery(self._k8s("ns", [workload])).discover("ns", "G")
        assert "/live" in monitors[0].payload["url"]
        assert ":8080" in monitors[0].payload["url"]

    def test_https_scheme(self) -> None:
        workload = DiscoveredWorkload(
            name="svc",
            namespace="ns",
            probes=[
                ContainerProbes(
                    container_name="c",
                    liveness=HttpProbeInfo(path="/healthz", port=8443, scheme="HTTPS"),
                    readiness=None,
                )
            ],
        )
        monitors = ProbeDiscovery(self._k8s("ns", [workload])).discover("ns", "G")
        assert monitors[0].payload["url"].startswith("https://")

    def test_no_probes_returns_empty(self) -> None:
        workload = DiscoveredWorkload(name="svc", namespace="ns", probes=[])
        monitors = ProbeDiscovery(self._k8s("ns", [workload])).discover("ns", "G")
        assert monitors == []

    def test_uses_service_name_when_selector_matches(self) -> None:
        """Real-world: deployment 'tracearr' has service 'svc-tracearr' with matching selector."""
        workload = DiscoveredWorkload(
            name="tracearr",
            namespace="tracearr",
            probes=[ContainerProbes(
                container_name="app",
                liveness=HttpProbeInfo(path="/", port=3000, scheme="HTTP"),
                readiness=None,
            )],
            pod_labels={"app": "tracearr"},
        )
        svc = DiscoveredService(
            name="svc-tracearr",
            namespace="tracearr",
            cluster_ip="10.43.151.231",
            ports=[ServicePort(name="http", port=80, protocol="TCP", target_port=3000)],
            selector={"app": "tracearr"},
        )
        k8s = _MockDiscoveryK8s(
            workloads={"tracearr": [workload]},
            services={"tracearr": [svc]},
        )
        monitors = ProbeDiscovery(k8s).discover("tracearr", "G")
        assert len(monitors) == 1
        assert "svc-tracearr.tracearr.svc.cluster.local" in monitors[0].payload["url"]

    def test_probe_uses_service_port_not_container_port(self) -> None:
        """Real-world: headlamp probe port 4466 → service port 80.
        Uptime Kuma must connect to the service port, not the container port."""
        workload = DiscoveredWorkload(
            name="headlamp",
            namespace="headlamp-system",
            probes=[ContainerProbes(
                container_name="headlamp",
                liveness=HttpProbeInfo(path="/", port=4466, scheme="HTTP"),
                readiness=None,
            )],
            pod_labels={"app.kubernetes.io/name": "headlamp"},
        )
        svc = DiscoveredService(
            name="svc-headlamp",
            namespace="headlamp-system",
            cluster_ip="10.43.8.199",
            ports=[ServicePort(name="http", port=80, protocol="TCP", target_port=4466)],
            selector={"app.kubernetes.io/name": "headlamp"},
        )
        k8s = _MockDiscoveryK8s(
            workloads={"headlamp-system": [workload]},
            services={"headlamp-system": [svc]},
        )
        monitors = ProbeDiscovery(k8s).discover("headlamp-system", "G")
        assert len(monitors) == 1
        url = monitors[0].payload["url"]
        assert "svc-headlamp.headlamp-system.svc.cluster.local" in url
        # Must use service port 80, not container port 4466
        assert ":80/" in url
        assert "4466" not in url

    def test_falls_back_to_workload_name_when_no_service_matches(self) -> None:
        """When no service selector matches, the workload name is used as hostname."""
        workload = DiscoveredWorkload(
            name="my-app",
            namespace="default",
            probes=[ContainerProbes(
                container_name="c",
                liveness=HttpProbeInfo(path="/health", port=8080, scheme="HTTP"),
                readiness=None,
            )],
            pod_labels={"app": "my-app"},
        )
        unrelated_svc = DiscoveredService(
            name="other-svc",
            namespace="default",
            cluster_ip="10.0.0.1",
            ports=[ServicePort(name="http", port=80, protocol="TCP")],
            selector={"app": "other-app"},  # does not match
        )
        k8s = _MockDiscoveryK8s(
            workloads={"default": [workload]},
            services={"default": [unrelated_svc]},
        )
        monitors = ProbeDiscovery(k8s).discover("default", "G")
        assert len(monitors) == 1
        assert "my-app.default.svc.cluster.local" in monitors[0].payload["url"]


# ---------------------------------------------------------------------------
# Named probe port resolution (_extract_http_probe)
# ---------------------------------------------------------------------------


from src.services.discovery.k8s import _extract_http_probe  # noqa: E402


class _FakeHttpGet:
    def __init__(self, path: str, port: object, scheme: str = "HTTP") -> None:
        self.path = path
        self.port = port
        self.scheme = scheme


class _FakeProbe:
    def __init__(self, http_get: _FakeHttpGet) -> None:
        self.http_get = http_get


class TestExtractHttpProbe:
    def test_numeric_port_resolved(self) -> None:
        probe = _FakeProbe(_FakeHttpGet("/-/healthy", 9090))
        result = _extract_http_probe(probe)
        assert result is not None
        assert result.port == 9090
        assert result.path == "/-/healthy"

    def test_named_port_resolved_via_named_ports(self) -> None:
        """Real-world: Prometheus StatefulSet uses port name 'http-web'."""
        probe = _FakeProbe(_FakeHttpGet("/-/healthy", "http-web", "HTTP"))
        result = _extract_http_probe(probe, named_ports={"http-web": 9090})
        assert result is not None
        assert result.port == 9090
        assert result.path == "/-/healthy"
        assert result.scheme == "HTTP"

    def test_unknown_named_port_returns_none(self) -> None:
        probe = _FakeProbe(_FakeHttpGet("/health", "unknown-port"))
        result = _extract_http_probe(probe, named_ports={"other-port": 8080})
        assert result is None

    def test_named_port_no_named_ports_dict_returns_none(self) -> None:
        probe = _FakeProbe(_FakeHttpGet("/health", "http-web"))
        result = _extract_http_probe(probe)
        assert result is None

    def test_none_probe_returns_none(self) -> None:
        assert _extract_http_probe(None) is None


# ---------------------------------------------------------------------------
# Named targetPort resolution — service skip + probe port
# ---------------------------------------------------------------------------


class TestNamedTargetPortResolution:
    """Real-world regression: prometheus-kube-prometheus-operator.

    Service: name=https, port=443, targetPort="https" (named string).
    Container: name="https", containerPort=10250.
    Probe: httpGet path=/healthz, port="https" (named) → resolves to 10250.

    Expected:
    - ServicePortDiscovery skips port 443 (probe owns it).
    - ProbeDiscovery produces a monitor at :443/healthz (service port, not container port).
    """

    def _make_resources(self) -> tuple[DiscoveredService, DiscoveredWorkload]:
        svc = DiscoveredService(
            name="prometheus-kube-prometheus-operator",
            namespace="prometheus",
            cluster_ip="10.43.18.245",
            ports=[ServicePort(name="https", port=443, protocol="TCP", target_port="https")],
            selector={"app": "kube-prometheus-stack-operator", "release": "prometheus"},
        )
        workload = DiscoveredWorkload(
            name="prometheus-kube-prometheus-operator",
            namespace="prometheus",
            pod_labels={"app": "kube-prometheus-stack-operator", "release": "prometheus"},
            probes=[
                ContainerProbes(
                    container_name="kube-prometheus-stack",
                    liveness=HttpProbeInfo(path="/healthz", port=10250, scheme="HTTPS"),
                    readiness=None,
                )
            ],
            named_container_ports={"https": 10250},
        )
        return svc, workload

    def test_service_skips_port_covered_by_probe(self) -> None:
        svc, workload = self._make_resources()
        k8s = _MockDiscoveryK8s(
            services={"prometheus": [svc]},
            workloads={"prometheus": [workload]},
        )
        monitors = ServicePortDiscovery(k8s).discover("prometheus", "G")
        assert monitors == [], "Service monitor should be skipped — probe owns this port"

    def test_probe_uses_service_port_not_container_port(self) -> None:
        svc, workload = self._make_resources()
        k8s = _MockDiscoveryK8s(
            services={"prometheus": [svc]},
            workloads={"prometheus": [workload]},
        )
        monitors = ProbeDiscovery(k8s).discover("prometheus", "G")
        assert len(monitors) == 1
        url = monitors[0].payload["url"]
        assert ":443" in url, f"Expected service port 443, got: {url}"
        assert "/healthz" in url
        assert url.startswith("https://")


# ---------------------------------------------------------------------------
# DatabasePortDiscovery
# ---------------------------------------------------------------------------


class TestDatabasePortDiscovery:
    def _k8s(self, namespace: str, services: list[DiscoveredService]) -> _MockDiscoveryK8s:
        return _MockDiscoveryK8s(services={namespace: services})

    @pytest.mark.parametrize(
        "port,expected_label",
        [
            (5432, "postgres"),
            (6379, "redis"),
            (3306, "mysql"),
            (27017, "mongodb"),
            (1433, "sqlserver"),
        ],
    )
    def test_known_db_port_produces_tcp_monitor(self, port: int, expected_label: str) -> None:
        svc = DiscoveredService(
            name="db",
            namespace="default",
            cluster_ip="10.0.0.5",
            ports=[ServicePort(name="", port=port, protocol="TCP")],
        )
        monitors = DatabasePortDiscovery(self._k8s("default", [svc])).discover("default", "G")
        assert len(monitors) == 1
        m = monitors[0]
        assert m.payload["type"] == "port"
        assert m.payload["port"] == port
        assert expected_label in m.payload["name"]
        assert m.payload["hostname"] == "db.default.svc.cluster.local"

    def test_unknown_port_is_skipped(self) -> None:
        svc = DiscoveredService(
            name="something",
            namespace="default",
            cluster_ip="10.0.0.6",
            ports=[ServicePort(name="", port=8080, protocol="TCP")],
        )
        monitors = DatabasePortDiscovery(self._k8s("default", [svc])).discover("default", "G")
        assert monitors == []

    def test_identity_key_format(self) -> None:
        svc = DiscoveredService(
            name="pg",
            namespace="prod",
            cluster_ip="10.0.0.7",
            ports=[ServicePort(name="", port=5432, protocol="TCP")],
        )
        monitors = DatabasePortDiscovery(self._k8s("prod", [svc])).discover("prod", "G")
        assert monitors[0].identity_key == "discovered:database:prod/pg/5432"


# ---------------------------------------------------------------------------
# DiscoveryRunner
# ---------------------------------------------------------------------------


class TestDiscoveryRunner:
    def test_no_opted_in_namespaces_returns_empty(self) -> None:
        k8s = _MockDiscoveryK8s(namespaces=[])
        runner = DiscoveryRunner(k8s, [])
        assert runner.run() == []

    def test_group_monitor_created_per_namespace(self) -> None:
        """A group is only emitted when at least one child monitor exists.
        An empty namespace (no sources) produces no monitors — this avoids
        the 'Group empty' warning in Uptime Kuma.
        """
        k8s = _MockDiscoveryK8s(
            namespaces=[DiscoveredNamespace(name="ns1", group_name="NS One")],
            ingresses={
                "ns1": [
                    DiscoveredIngress(
                        name="ing",
                        namespace="ns1",
                        rules=[IngressRule(host="app.example.com", tls=False)],
                    )
                ]
            },
        )
        runner = DiscoveryRunner(k8s, [IngressDiscovery(k8s)])
        result = runner.run()
        # 1 group + 1 ingress child
        assert len(result) == 2
        group = next(m for m in result if m.payload["type"] == "group")
        assert group.payload["name"] == "NS One"
        assert group.identity_key == make_group_key("ns1")
        assert group.parent_name is None

    def test_empty_namespace_produces_no_monitors(self) -> None:
        """Namespaces with no sources or no matching resources emit no monitors at all."""
        k8s = _MockDiscoveryK8s(
            namespaces=[DiscoveredNamespace(name="empty", group_name="Empty")]
        )
        result = DiscoveryRunner(k8s, []).run()
        assert result == []

    def test_sources_are_called_per_namespace(self) -> None:
        k8s = _MockDiscoveryK8s(
            namespaces=[DiscoveredNamespace(name="default", group_name="Default")],
            ingresses={
                "default": [
                    DiscoveredIngress(
                        name="ing",
                        namespace="default",
                        rules=[IngressRule(host="app.example.com", tls=False)],
                    )
                ]
            },
        )
        sources = [IngressDiscovery(k8s)]
        result = DiscoveryRunner(k8s, sources).run()
        # 1 group + 1 ingress monitor
        assert len(result) == 2
        types = {m.payload["type"] for m in result}
        assert "group" in types
        assert "http" in types

    def test_multiple_namespaces_each_get_group(self) -> None:
        """Each namespace that has at least one child monitor gets its own group."""
        k8s = _MockDiscoveryK8s(
            namespaces=[
                DiscoveredNamespace(name="ns1", group_name="NS1"),
                DiscoveredNamespace(name="ns2", group_name="NS2"),
            ],
            ingresses={
                "ns1": [
                    DiscoveredIngress(
                        name="ing1",
                        namespace="ns1",
                        rules=[IngressRule(host="a.example.com", tls=False)],
                    )
                ],
                "ns2": [
                    DiscoveredIngress(
                        name="ing2",
                        namespace="ns2",
                        rules=[IngressRule(host="b.example.com", tls=False)],
                    )
                ],
            },
        )
        result = DiscoveryRunner(k8s, [IngressDiscovery(k8s)]).run()
        group_keys = {m.identity_key for m in result if m.payload["type"] == "group"}
        assert make_group_key("ns1") in group_keys
        assert make_group_key("ns2") in group_keys

    def test_source_exception_does_not_stop_other_sources(self) -> None:
        class _FailingSource:
            def discover(self, namespace: str, group_name: str) -> list[DesiredMonitor]:
                raise RuntimeError("source exploded")

        class _GoodSource:
            def discover(self, namespace: str, group_name: str) -> list[DesiredMonitor]:
                return [
                    DesiredMonitor(
                        identity_key=f"discovered:good:{namespace}/svc/detail",
                        payload={"type": "http", "name": "good"},
                        parent_name=group_name,
                        notification_names=[],
                        user_tags=[],
                    )
                ]

        k8s = _MockDiscoveryK8s(
            namespaces=[DiscoveredNamespace(name="ns", group_name="NS")]
        )
        result = DiscoveryRunner(k8s, [_FailingSource(), _GoodSource()]).run()
        # group + good source monitor; failing source should not kill the run
        assert any(m.identity_key.startswith("discovered:good:") for m in result)

    def test_discovered_child_monitors_reference_group_name(self) -> None:
        k8s = _MockDiscoveryK8s(
            namespaces=[DiscoveredNamespace(name="prod", group_name="Production")],
            ingresses={
                "prod": [
                    DiscoveredIngress(
                        name="ing",
                        namespace="prod",
                        rules=[IngressRule(host="x.example.com", tls=False)],
                    )
                ]
            },
        )
        result = DiscoveryRunner(k8s, [IngressDiscovery(k8s)]).run()
        child = next(m for m in result if m.payload["type"] == "http")
        assert child.parent_name == "Production"


# ---------------------------------------------------------------------------
# Group monitor helper
# ---------------------------------------------------------------------------


class TestMakeGroupMonitor:
    def test_group_monitor_fields(self) -> None:
        m = _make_group_monitor("production", "My Production")
        assert m.payload["type"] == "group"
        assert m.payload["name"] == "My Production"
        assert m.identity_key == make_group_key("production")
        assert m.parent_name is None
        assert m.notification_names == []
        assert m.user_tags == []


# ---------------------------------------------------------------------------
# EndpointValidator
# ---------------------------------------------------------------------------

def _http_monitor(url: str) -> DesiredMonitor:
    return DesiredMonitor(
        identity_key="discovered:ingress:ns/ing/host",
        payload={"type": "http", "name": "test", "url": url},
        parent_name="G",
        notification_names=[],
        user_tags=[],
    )


def _port_monitor(hostname: str, port: int) -> DesiredMonitor:
    return DesiredMonitor(
        identity_key="discovered:database:ns/svc/5432",
        payload={"type": "port", "name": "test", "hostname": hostname, "port": port},
        parent_name="G",
        notification_names=[],
        user_tags=[],
    )


def _group_monitor() -> DesiredMonitor:
    return DesiredMonitor(
        identity_key="discovered:group:ns",
        payload={"type": "group", "name": "NS"},
        parent_name=None,
        notification_names=[],
        user_tags=[],
    )


class TestEndpointForMonitor:
    def test_http_url_with_explicit_port(self) -> None:
        m = _http_monitor("http://svc.ns.svc.cluster.local:8080/health")
        assert _endpoint_for_monitor(m) == ("svc.ns.svc.cluster.local", 8080)

    def test_http_url_default_port_80(self) -> None:
        m = _http_monitor("http://app.example.com")
        assert _endpoint_for_monitor(m) == ("app.example.com", 80)

    def test_https_url_default_port_443(self) -> None:
        m = _http_monitor("https://app.example.com")
        assert _endpoint_for_monitor(m) == ("app.example.com", 443)

    def test_port_monitor_returns_hostname_and_port(self) -> None:
        m = _port_monitor("pg.ns.svc.cluster.local", 5432)
        assert _endpoint_for_monitor(m) == ("pg.ns.svc.cluster.local", 5432)

    def test_group_monitor_returns_none(self) -> None:
        assert _endpoint_for_monitor(_group_monitor()) is None

    def test_http_monitor_with_no_url_returns_none(self) -> None:
        m = DesiredMonitor(
            identity_key="k",
            payload={"type": "http", "name": "t", "url": ""},
            parent_name=None,
            notification_names=[],
            user_tags=[],
        )
        assert _endpoint_for_monitor(m) is None


class TestEndpointValidator:
    def test_reachable_endpoint_accepted(self) -> None:
        v = EndpointValidator(timeout_sec=1.0)
        with patch("src.services.discovery.validator._tcp_connect", return_value=True):
            assert v.is_reachable(_http_monitor("http://svc.ns.svc.cluster.local:8080")) is True

    def test_unreachable_endpoint_rejected(self) -> None:
        v = EndpointValidator(timeout_sec=1.0)
        with patch("src.services.discovery.validator._tcp_connect", return_value=False):
            assert v.is_reachable(_http_monitor("http://dead.ns.svc.cluster.local:8080")) is False

    def test_group_monitor_always_accepted(self) -> None:
        v = EndpointValidator(timeout_sec=1.0)
        # No TCP call should be made for group monitors.
        with patch("src.services.discovery.validator._tcp_connect", side_effect=AssertionError("should not call")):
            assert v.is_reachable(_group_monitor()) is True

    def test_port_monitor_validated_by_tcp(self) -> None:
        v = EndpointValidator(timeout_sec=1.0)
        with patch("src.services.discovery.validator._tcp_connect", return_value=True) as mock_tcp:
            assert v.is_reachable(_port_monitor("pg.ns.svc.cluster.local", 5432)) is True
            mock_tcp.assert_called_once_with("pg.ns.svc.cluster.local", 5432, 1.0)


class TestNullValidator:
    def test_always_returns_true(self) -> None:
        v = NullValidator()
        assert v.is_reachable(_http_monitor("http://anything:9999")) is True
        assert v.is_reachable(_group_monitor()) is True


class TestDiscoveryRunnerWithValidator:
    def _make_monitor(self, key: str, url: str, group: str) -> DesiredMonitor:
        return DesiredMonitor(
            identity_key=key,
            payload={"type": "http", "name": key, "url": url},
            parent_name=group,
            notification_names=[],
            user_tags=[],
        )

    def test_unreachable_monitors_filtered_out(self) -> None:
        class _Source:
            def discover(self, namespace: str, group_name: str) -> list[DesiredMonitor]:
                return [
                    DesiredMonitor(
                        identity_key=f"discovered:ingress:{namespace}/ing/good.example.com",
                        payload={"type": "http", "name": "good", "url": "http://good.example.com"},
                        parent_name=group_name, notification_names=[], user_tags=[],
                    ),
                    DesiredMonitor(
                        identity_key=f"discovered:ingress:{namespace}/ing/dead.example.com",
                        payload={"type": "http", "name": "dead", "url": "http://dead.example.com"},
                        parent_name=group_name, notification_names=[], user_tags=[],
                    ),
                ]

        class _Validator:
            def is_reachable(self, monitor: DesiredMonitor) -> bool:
                return "good" in monitor.payload.get("url", "")

        k8s = _MockDiscoveryK8s(namespaces=[DiscoveredNamespace(name="ns", group_name="NS")])
        result = DiscoveryRunner(k8s, [_Source()], validator=_Validator()).run()
        names = {m.payload["name"] for m in result}
        assert "good" in names
        assert "dead" not in names
        # group monitor always passes through
        assert any(m.payload["type"] == "group" for m in result)

    def test_null_validator_passes_all_through(self) -> None:
        class _Source:
            def discover(self, namespace: str, group_name: str) -> list[DesiredMonitor]:
                return [
                    DesiredMonitor(
                        identity_key=f"discovered:ingress:{namespace}/ing/x",
                        payload={"type": "http", "name": "x", "url": "http://x.example.com"},
                        parent_name=group_name, notification_names=[], user_tags=[],
                    )
                ]

        k8s = _MockDiscoveryK8s(namespaces=[DiscoveredNamespace(name="ns", group_name="NS")])
        result = DiscoveryRunner(k8s, [_Source()], validator=NullValidator()).run()
        assert any(m.payload["name"] == "x" for m in result)

    def test_no_validator_passes_all_through(self) -> None:
        """When validator=None (default), nothing is filtered."""
        class _Source:
            def discover(self, namespace: str, group_name: str) -> list[DesiredMonitor]:
                return [
                    DesiredMonitor(
                        identity_key=f"discovered:ingress:{namespace}/ing/x",
                        payload={"type": "http", "name": "x", "url": "http://x.example.com"},
                        parent_name=group_name, notification_names=[], user_tags=[],
                    )
                ]

        k8s = _MockDiscoveryK8s(namespaces=[DiscoveredNamespace(name="ns", group_name="NS")])
        result = DiscoveryRunner(k8s, [_Source()]).run()
        assert any(m.payload["name"] == "x" for m in result)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    """Tests for URL-based duplicate monitor removal in DiscoveryRunner."""

    def _source(self, monitors: list[DesiredMonitor]):  # type: ignore[return]
        class _S:
            def discover(self, namespace: str, group_name: str) -> list[DesiredMonitor]:
                return monitors
        return _S()

    def test_duplicate_url_second_dropped(self) -> None:
        url = "http://app.example.com"
        ns = _MockDiscoveryK8s(namespaces=[DiscoveredNamespace(name="ns", group_name="G")])
        m1 = DesiredMonitor(
            identity_key="discovered:probe:ns/app/live",
            payload={"type": "http", "name": "probe", "url": url},
            parent_name="G", notification_names=[], user_tags=[],
        )
        m2 = DesiredMonitor(
            identity_key="discovered:service:ns/app/http",
            payload={"type": "http", "name": "service", "url": url},
            parent_name="G", notification_names=[], user_tags=[],
        )
        result = DiscoveryRunner(ns, [self._source([m1, m2])]).run()
        http_monitors = [m for m in result if m.payload.get("url") == url]
        assert len(http_monitors) == 1
        assert http_monitors[0].identity_key == m1.identity_key  # first wins

    def test_different_urls_both_kept(self) -> None:
        ns = _MockDiscoveryK8s(namespaces=[DiscoveredNamespace(name="ns", group_name="G")])
        m1 = DesiredMonitor(
            identity_key="discovered:probe:ns/a/live",
            payload={"type": "http", "name": "a", "url": "http://a.example.com"},
            parent_name="G", notification_names=[], user_tags=[],
        )
        m2 = DesiredMonitor(
            identity_key="discovered:probe:ns/b/live",
            payload={"type": "http", "name": "b", "url": "http://b.example.com"},
            parent_name="G", notification_names=[], user_tags=[],
        )
        result = DiscoveryRunner(ns, [self._source([m1, m2])]).run()
        urls = {m.payload.get("url") for m in result if m.payload.get("url")}
        assert "http://a.example.com" in urls
        assert "http://b.example.com" in urls

    def test_group_monitor_not_deduped(self) -> None:
        """Group monitors have no URL and must never be removed by the dedup pass."""
        ns = _MockDiscoveryK8s(namespaces=[
            DiscoveredNamespace(name="ns1", group_name="G1"),
            DiscoveredNamespace(name="ns2", group_name="G2"),
        ])
        ingresses = {
            "ns1": [DiscoveredIngress("i1", "ns1", [IngressRule("a.example.com", False)])],
            "ns2": [DiscoveredIngress("i2", "ns2", [IngressRule("b.example.com", False)])],
        }
        k8s = _MockDiscoveryK8s(
            namespaces=[
                DiscoveredNamespace(name="ns1", group_name="G1"),
                DiscoveredNamespace(name="ns2", group_name="G2"),
            ],
            ingresses=ingresses,
        )
        result = DiscoveryRunner(k8s, [IngressDiscovery(k8s)]).run()
        groups = [m for m in result if m.payload["type"] == "group"]
        assert len(groups) == 2
