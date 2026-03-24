"""Tests for the reconciler — create / update / delete flows using mocks."""
import pytest

from src.models.crd import KumaMonitor, KumaMonitorSpec
from src.models.desired import build_desired, OWNER_TAG_NAME, owner_tag_value
from src.models.kuma import LiveMonitor
from src.services.reconciler import Reconciler


def _km(namespace="default", name="svc", **spec_kwargs) -> KumaMonitor:
    spec = KumaMonitorSpec(name=name, type="http", url="https://example.com", **spec_kwargs)
    return KumaMonitor(namespace=namespace, name=name, spec=spec)


def _live(monitor_id, key, name="svc", mtype="http", extra: dict = None) -> LiveMonitor:
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
        group_km = _km("default", "infra-group", type="group")
        group_km.spec.__dict__["type"] = "group"
        child_km = _km("default", "api", type="http")

        # patch spec types properly
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
