"""Tests for the discovery sources and runner."""
from __future__ import annotations

import pytest

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
                liveness=HttpProbeInfo(path="/healthz", port=8080, scheme="HTTP"),
                readiness=None,
            )],
            pod_labels={"app": "tracearr", "version": "1.0"},
        )
        svc = DiscoveredService(
            name="svc-tracearr",
            namespace="tracearr",
            cluster_ip="10.43.151.231",
            ports=[ServicePort(name="http", port=80, protocol="TCP")],
            selector={"app": "tracearr"},
        )
        k8s = _MockDiscoveryK8s(
            workloads={"tracearr": [workload]},
            services={"tracearr": [svc]},
        )
        monitors = ProbeDiscovery(k8s).discover("tracearr", "G")
        assert len(monitors) == 1
        assert "svc-tracearr.tracearr.svc.cluster.local" in monitors[0].payload["url"]

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
        k8s = _MockDiscoveryK8s(
            namespaces=[DiscoveredNamespace(name="ns1", group_name="NS One")]
        )
        runner = DiscoveryRunner(k8s, [])
        result = runner.run()
        assert len(result) == 1
        group = result[0]
        assert group.payload["type"] == "group"
        assert group.payload["name"] == "NS One"
        assert group.identity_key == make_group_key("ns1")
        assert group.parent_name is None

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
        k8s = _MockDiscoveryK8s(
            namespaces=[
                DiscoveredNamespace(name="ns1", group_name="NS1"),
                DiscoveredNamespace(name="ns2", group_name="NS2"),
            ]
        )
        result = DiscoveryRunner(k8s, []).run()
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
