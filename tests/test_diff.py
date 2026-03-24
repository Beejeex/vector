"""Tests for the diff engine."""
from src.models.crd import KumaMonitor, KumaMonitorSpec
from src.models.desired import build_desired, OWNER_TAG_NAME
from src.models.kuma import LiveMonitor
from src.services.diff import compute_diff, payload_hash


def _desired(namespace="default", name="svc", **spec_kwargs):
    spec = KumaMonitorSpec(name=name, type="http", url="https://example.com", **spec_kwargs)
    km = KumaMonitor(namespace=namespace, name=name, spec=spec)
    return build_desired(km)


def _live(monitor_id, key, name: str = None, mtype="http", extra: dict = None):
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


class TestComputeDiff:
    def test_create_when_no_live_match(self):
        desired = [_desired("default", "new-svc")]
        result = compute_diff(desired, [])
        assert len(result.to_create) == 1
        assert len(result.to_update) == 0
        assert len(result.to_delete) == 0

    def test_no_action_when_state_matches(self):
        desired = [_desired("default", "svc")]
        live = [_live(1, "default/svc")]
        result = compute_diff(desired, live)
        assert len(result.to_create) == 0
        assert len(result.to_update) == 0
        assert len(result.to_delete) == 0

    def test_update_when_field_differs(self):
        desired = [_desired("default", "svc", interval=120)]
        live = [_live(1, "default/svc")]  # live has interval=60
        result = compute_diff(desired, live)
        assert len(result.to_update) == 1
        assert result.to_update[0][1] == 1  # kuma monitor ID

    def test_delete_when_desired_removed(self):
        live = [_live(1, "default/old-svc")]
        result = compute_diff([], live)
        assert len(result.to_delete) == 1
        assert result.to_delete[0] == 1

    def test_unmanaged_monitor_never_deleted(self):
        unmanaged = LiveMonitor({"id": 99, "name": "manual", "type": "http", "tags": []})
        result = compute_diff([], [unmanaged])
        assert len(result.to_delete) == 0
        assert result.skipped_unmanaged == 1

    def test_unmanaged_count_correct(self):
        managed = _live(1, "default/svc")
        unmanaged1 = LiveMonitor({"id": 2, "name": "m1", "type": "http", "tags": []})
        unmanaged2 = LiveMonitor({"id": 3, "name": "m2", "type": "http", "tags": []})
        result = compute_diff([_desired("default", "svc")], [managed, unmanaged1, unmanaged2])
        assert result.skipped_unmanaged == 2

    def test_create_update_delete_in_one_cycle(self):
        desired = [
            _desired("default", "new-svc"),           # → create
            _desired("default", "existing", interval=120),  # → update (interval changed)
        ]
        live = [
            _live(1, "default/existing"),             # → update
            _live(2, "default/old-svc"),              # → delete
        ]
        result = compute_diff(desired, live)
        assert len(result.to_create) == 1
        assert len(result.to_update) == 1
        assert len(result.to_delete) == 1

    def test_multiple_managed_monitors_same_namespace(self):
        desired = [_desired("monitoring", f"svc{i}") for i in range(3)]
        live = [_live(i, f"monitoring/svc{i}") for i in range(3)]
        result = compute_diff(desired, live)
        assert len(result.to_create) == 0
        assert len(result.to_update) == 0
        assert len(result.to_delete) == 0


class TestPayloadHash:
    def test_same_payload_same_hash(self):
        p = {"name": "test", "type": "http", "interval": 60}
        assert payload_hash(p) == payload_hash(p)

    def test_different_payload_different_hash(self):
        p1 = {"name": "test", "interval": 60}
        p2 = {"name": "test", "interval": 120}
        assert payload_hash(p1) != payload_hash(p2)

    def test_key_order_irrelevant(self):
        p1 = {"a": 1, "b": 2}
        p2 = {"b": 2, "a": 1}
        assert payload_hash(p1) == payload_hash(p2)
