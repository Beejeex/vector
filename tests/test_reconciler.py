"""Tests for the reconciler — create / update / delete flows using mocks."""
import pytest

from src.models.crd import KumaMonitor, KumaMonitorSpec
from src.models.desired import build_desired, OWNER_TAG_NAME, owner_tag_value
from src.models.kuma import LiveMonitor
from src.services.reconciler import Reconciler


def _km(namespace="default", name="svc", **spec_kwargs) -> KumaMonitor:
    spec = KumaMonitorSpec(name=name, type="http", url="https://example.com", **spec_kwargs)
    return KumaMonitor(namespace=namespace, name=name, spec=spec)


def _live(monitor_id, key, name: str = None, mtype="http", extra: dict = None) -> LiveMonitor:
    if name is None:
        name = key.split("/")[-1] if "/" in key else key
    base = {
        "id": monitor_id,
        "name": name,
        "type": mtype,
        "tags": [{"name": OWNER_TAG_NAME, "value": f"vector:{key}"}],
        "url": "https://example.com",
        "interval": 60,
        "timeout": 30,
        "maxretries": 1,
        "active": True,
        "maintenance": False,
        "upsideDown": False,
        "expiryNotification": False,
        "ignoreTls": False,
        "cacheBust": False,
        "maxredirects": 10,
        "method": "GET",
        "invertKeyword": False,
        "packetSize": 56,
        "dns_resolve_type": "A",
        "kafkaProducerSsl": False,
        "kafkaProducerAllowAutoTopicCreation": False,
        "grpcEnableTls": False,
        "retryInterval": 60,
        "resendInterval": 0,
    }
    if extra:
        base.update(extra)
    return LiveMonitor(base)


class _MockK8s:
    def __init__(self, monitors):
        self._monitors = monitors

    def list_monitors(self):
        return self._monitors


class _MockKuma:
    def __init__(self, live_monitors=None):
        self.live = live_monitors or []
        self.created = []
        self.updated = []
        self.deleted = []
        self.tags_added = []
        self.next_id = 100

    def list_monitors(self):
        return self.live

    def create_monitor(self, payload):
        monitor_id = self.next_id
        self.next_id += 1
        self.created.append(payload)
        return monitor_id

    def update_monitor(self, monitor_id, payload):
        self.updated.append((monitor_id, payload))

    def delete_monitor(self, monitor_id):
        self.deleted.append(monitor_id)

    def ensure_tag(self, name, color):
        return 1

    def add_monitor_tag(self, tag_id, monitor_id, value):
        self.tags_added.append((tag_id, monitor_id, value))

    def delete_monitor_tag(self, tag_id, monitor_id, value):
        pass

    def get_notifications(self):
        return []


class _MockStore:
    def __init__(self):
        self.states = {}
        self.traces = []

    def upsert_state(self, key, monitor_id, spec_hash):
        self.states[key] = (monitor_id, spec_hash)

    def delete_state(self, key):
        self.states.pop(key, None)

    def get_state(self, key):
        return self.states.get(key)

    def record_trace(self, namespace, name, action, outcome, monitor_id=None, detail=None):
        self.traces.append((namespace, name, action, outcome, monitor_id, detail))


class TestReconcilerCreate:
    def test_creates_monitor_and_attaches_tag(self):
        k8s = _MockK8s([_km("default", "api")])
        kuma = _MockKuma(live_monitors=[])
        store = _MockStore()

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        assert len(kuma.created) == 1
        assert kuma.created[0]["name"] == "api"
        assert len(kuma.tags_added) == 1
        tag_value = kuma.tags_added[0][2]
        assert tag_value == "vector:default/api"

    def test_state_recorded_after_create(self):
        k8s = _MockK8s([_km("default", "api")])
        kuma = _MockKuma(live_monitors=[])
        store = _MockStore()

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        assert "default/api" in store.states

    def test_trace_recorded_on_create_success(self):
        k8s = _MockK8s([_km("default", "api")])
        kuma = _MockKuma(live_monitors=[])
        store = _MockStore()

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        actions = [t[2] for t in store.traces]
        outcomes = [t[3] for t in store.traces]
        assert "create" in actions
        assert "success" in outcomes

    def test_group_created_before_child(self):
        group_spec = KumaMonitorSpec(name="infra-group", type="group")
        child_spec = KumaMonitorSpec(name="api", type="http", url="https://example.com", group="infra-group")
        group_monitor = KumaMonitor(namespace="default", name="infra-group", spec=group_spec)
        child_monitor = KumaMonitor(namespace="default", name="api", spec=child_spec)

        k8s = _MockK8s([child_monitor, group_monitor])  # child listed first intentionally
        kuma = _MockKuma(live_monitors=[])
        store = _MockStore()

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        # Group should be the first created payload (type=group sorted first)
        assert kuma.created[0]["type"] == "group"


class TestReconcilerUpdate:
    def test_updates_changed_monitor(self):
        k8s = _MockK8s([_km("default", "api", interval=120)])
        kuma = _MockKuma(live_monitors=[_live(1, "default/api")])
        store = _MockStore()

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        assert len(kuma.updated) == 1
        assert kuma.updated[0][0] == 1  # monitor ID
        assert kuma.updated[0][1]["interval"] == 120

    def test_no_update_when_unchanged(self):
        k8s = _MockK8s([_km("default", "api")])
        kuma = _MockKuma(live_monitors=[_live(1, "default/api")])
        store = _MockStore()

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        assert len(kuma.updated) == 0

    def test_trace_recorded_on_update(self):
        k8s = _MockK8s([_km("default", "api", timeout=60)])
        kuma = _MockKuma(live_monitors=[_live(1, "default/api", extra={"timeout": 10})])
        store = _MockStore()

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        actions = [t[2] for t in store.traces]
        assert "update" in actions


class TestReconcilerDelete:
    def test_deletes_orphaned_managed_monitor(self):
        k8s = _MockK8s([])
        kuma = _MockKuma(live_monitors=[_live(5, "default/old-svc")])
        store = _MockStore()

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        assert 5 in kuma.deleted

    def test_does_not_delete_unmanaged_monitor(self):
        unmanaged = LiveMonitor({"id": 99, "name": "manual", "type": "http", "tags": []})
        k8s = _MockK8s([])
        kuma = _MockKuma(live_monitors=[unmanaged])
        store = _MockStore()

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        assert len(kuma.deleted) == 0

    def test_state_cleaned_up_after_delete(self):
        k8s = _MockK8s([])
        kuma = _MockKuma(live_monitors=[_live(5, "default/svc")])
        store = _MockStore()
        store.upsert_state("default/svc", 5, "oldhash")

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        assert store.get_state("default/svc") is None

    def test_trace_recorded_on_delete(self):
        k8s = _MockK8s([])
        kuma = _MockKuma(live_monitors=[_live(5, "default/svc")])
        store = _MockStore()

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        actions = [t[2] for t in store.traces]
        assert "delete" in actions


class TestReconcilerErrorIsolation:
    def test_error_in_one_create_does_not_stop_others(self):
        """If creating one monitor fails, the rest should still be attempted."""

        class FlakyKuma(_MockKuma):
            def __init__(self):
                super().__init__([])
                self._calls = 0

            def create_monitor(self, payload):
                self._calls += 1
                if self._calls == 1:
                    raise RuntimeError("Kuma API unreachable")
                return super().create_monitor(payload)

        k8s = _MockK8s([_km("default", "svc1"), _km("default", "svc2")])
        kuma = FlakyKuma()
        store = _MockStore()

        # Should not raise; second monitor should still be created
        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        assert len(kuma.created) == 1  # only the second succeeded

        outcomes = [t[3] for t in store.traces]
        assert "error" in outcomes
        assert "success" in outcomes


class TestReconcilerInvalidSpec:
    def test_invalid_spec_is_skipped_gracefully(self):
        """A monitor that raises in build_desired should be skipped; others still processed."""

        class BrokenMonitor:
            namespace = "default"
            name = "broken"
            # no spec attribute — will cause AttributeError in build_desired

        valid_km = _km("default", "valid")
        k8s = _MockK8s([BrokenMonitor(), valid_km])
        kuma = _MockKuma([])
        store = _MockStore()

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        assert len(kuma.created) == 1
        assert kuma.created[0]["name"] == "valid"


class TestReconcilerCacheHit:
    def test_cache_hit_skips_kuma_update_call(self):
        """When stored hash matches desired hash, kuma.update_monitor must not be called."""
        from src.services.diff import payload_hash
        from src.models.desired import build_desired

        km = _km("default", "api", interval=60)
        desired = build_desired(km)
        h = payload_hash(desired.payload)

        # Live state differs from desired (would normally trigger update).
        k8s = _MockK8s([km])
        kuma = _MockKuma(live_monitors=[_live(1, "default/api", extra={"interval": 999})])
        store = _MockStore()
        # Seed the cache with the desired hash — we already applied this spec.
        store.upsert_state("default/api", 1, h)

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        assert len(kuma.updated) == 0

    def test_cache_miss_triggers_update(self):
        """When stored hash differs from desired hash, update must proceed."""
        k8s = _MockK8s([_km("default", "api", interval=120)])
        kuma = _MockKuma(live_monitors=[_live(1, "default/api")])
        store = _MockStore()
        store.upsert_state("default/api", 1, "stale_hash")

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        assert len(kuma.updated) == 1

    def test_no_cache_entry_triggers_update(self):
        """When there is no cache entry at all, update must still run."""
        k8s = _MockK8s([_km("default", "api", interval=120)])
        kuma = _MockKuma(live_monitors=[_live(1, "default/api")])
        store = _MockStore()

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        assert len(kuma.updated) == 1


class TestReconcilerNotifications:
    def test_notification_name_resolved_to_id(self):
        """Notification names in spec should be resolved to IDs in the created payload."""

        class KumaWithNotifications(_MockKuma):
            def get_notifications(self):
                return [{"id": 7, "name": "slack"}]

        spec = KumaMonitorSpec(
            name="api", type="http", url="https://example.com", notification_names=["slack"]
        )
        km = KumaMonitor(namespace="default", name="api", spec=spec)
        k8s = _MockK8s([km])
        kuma = KumaWithNotifications([])
        store = _MockStore()

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        payload = kuma.created[0]
        assert "notificationIDList" in payload
        assert payload["notificationIDList"] == {"7": True}

    def test_unknown_notification_name_omitted_from_payload(self):
        """An unresolvable notification name should not crash; payload has no notificationIDList."""
        spec = KumaMonitorSpec(
            name="api", type="http", url="https://example.com", notification_names=["nonexistent"]
        )
        km = KumaMonitor(namespace="default", name="api", spec=spec)
        k8s = _MockK8s([km])
        kuma = _MockKuma([])
        store = _MockStore()

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        assert len(kuma.created) == 1
        assert "notificationIDList" not in kuma.created[0]


class TestReconcilerParentResolution:
    def test_parent_id_injected_into_child_payload(self):
        """Child monitor payload must contain parent=<group monitor ID>."""
        group_spec = KumaMonitorSpec(name="infra-group", type="group")
        child_spec = KumaMonitorSpec(
            name="api", type="http", url="https://example.com", group="infra-group"
        )
        group_km = KumaMonitor(namespace="default", name="infra-group", spec=group_spec)
        child_km = KumaMonitor(namespace="default", name="api", spec=child_spec)

        k8s = _MockK8s([child_km, group_km])  # child listed first — ordering must still work
        kuma = _MockKuma([])
        store = _MockStore()

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        # Group is created first (sorted), child second.
        assert kuma.created[0]["type"] == "group"
        child_payload = kuma.created[1]
        assert "parent" in child_payload
        assert child_payload["parent"] == 100  # first assigned ID from _MockKuma

    def test_missing_parent_does_not_block_child_create(self):
        """If the parent group is not found, child is still created without parent field."""
        child_spec = KumaMonitorSpec(
            name="api", type="http", url="https://example.com", group="nonexistent-group"
        )
        child_km = KumaMonitor(namespace="default", name="api", spec=child_spec)

        k8s = _MockK8s([child_km])
        kuma = _MockKuma([])
        store = _MockStore()

        Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()

        assert len(kuma.created) == 1
        assert "parent" not in kuma.created[0]


# ---------------------------------------------------------------------------
# Discovery integration in reconciler
# ---------------------------------------------------------------------------


class _MockDiscovery:
    def __init__(self, monitors: list) -> None:
        self._monitors = monitors

    def run(self) -> list:
        return self._monitors


class TestReconcilerDiscovery:
    def test_discovery_monitors_are_created(self):
        """Monitors from discovery feed are created in Uptime Kuma."""
        from src.models.desired import DesiredMonitor
        from src.services.diff import payload_hash

        discovered = DesiredMonitor(
            identity_key="discovered:ingress:default/my-ingress/app.example.com",
            payload={
                "type": "http",
                "name": "app.example.com",
                "url": "http://app.example.com",
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
            },
            parent_name=None,
            notification_names=[],
            user_tags=[],
        )

        k8s = _MockK8s([])
        kuma = _MockKuma([])
        store = _MockStore()
        discovery = _MockDiscovery([discovered])

        Reconciler(k8s=k8s, kuma=kuma, store=store, discovery=discovery).run_once()

        assert len(kuma.created) == 1

    def test_no_discovery_runner_works_normally(self):
        """Reconciler with discovery=None behaves exactly as before."""
        k8s = _MockK8s([_km("default", "svc")])
        kuma = _MockKuma([])
        store = _MockStore()

        Reconciler(k8s=k8s, kuma=kuma, store=store, discovery=None).run_once()

        assert len(kuma.created) == 1

    def test_discovery_runner_exception_does_not_stop_crd_reconciliation(self):
        """If discovery.run() raises, CRD-defined monitors are still reconciled."""

        class _BrokenDiscovery:
            def run(self):
                raise RuntimeError("discovery exploded")

        k8s = _MockK8s([_km("default", "svc")])
        kuma = _MockKuma([])
        store = _MockStore()

        Reconciler(k8s=k8s, kuma=kuma, store=store, discovery=_BrokenDiscovery()).run_once()

        assert len(kuma.created) == 1


# ---------------------------------------------------------------------------
# _split_key with discovered identity keys
# ---------------------------------------------------------------------------


class TestSplitKey:
    def test_crd_key(self):
        from src.services.reconciler import _split_key

        assert _split_key("production/my-monitor") == ("production", "my-monitor")

    def test_discovered_ingress_key(self):
        from src.services.reconciler import _split_key

        ns, name = _split_key("discovered:ingress:production/my-ingress/app.example.com")
        assert ns == "production"
        assert name == "ingress:my-ingress/app.example.com"

    def test_discovered_group_key(self):
        from src.services.reconciler import _split_key

        ns, name = _split_key("discovered:group:production")
        assert ns == "production"
        assert name == "group"

    def test_none_key(self):
        from src.services.reconciler import _split_key

        assert _split_key(None) == ("", "")

    def test_key_without_slash(self):
        from src.services.reconciler import _split_key

        assert _split_key("nonamespace") == ("", "nonamespace")
