"""Tests for CRD spec → DesiredMonitor conversion."""
from src.models.crd import KumaMonitor, KumaMonitorSpec
from src.models.desired import build_desired, OWNER_TAG_VALUE_PREFIX


def _make_monitor(namespace="default", name="test", **spec_kwargs) -> KumaMonitor:
    spec = KumaMonitorSpec(name="Test Monitor", type="http", **spec_kwargs)
    return KumaMonitor(namespace=namespace, name=name, spec=spec)


class TestIdentityKey:
    def test_basic(self):
        m = _make_monitor(namespace="monitoring", name="api")
        assert m.identity_key == "monitoring/api"

    def test_default_namespace(self):
        m = _make_monitor(namespace="default", name="web")
        assert m.identity_key == "default/web"


class TestBuildDesired:
    def test_core_fields_present(self):
        m = _make_monitor(url="https://example.com")
        d = build_desired(m)
        assert d.payload["type"] == "http"
        assert d.payload["name"] == "Test Monitor"
        assert d.payload["url"] == "https://example.com"
        assert d.identity_key == "default/test"

    def test_defaults_are_set(self):
        m = _make_monitor()
        d = build_desired(m)
        assert d.payload["interval"] == 60
        assert d.payload["timeout"] == 30
        assert d.payload["maxretries"] == 1
        assert "active" not in d.payload  # active state not sent to add_monitor
        assert d.payload["ignoreTls"] is False

    def test_enabled_maps_to_active(self):
        # enabled=False has no effect on the payload — the uptime-kuma-api library
        # does not accept 'active' in add_monitor; pause/resume are separate calls.
        m = _make_monitor(enabled=False)
        d = build_desired(m)
        assert "active" not in d.payload

    def test_retries_maps_to_maxretries(self):
        m = _make_monitor(retries=5)
        d = build_desired(m)
        assert d.payload["maxretries"] == 5

    def test_optional_fields_excluded_when_none(self):
        m = _make_monitor()
        d = build_desired(m)
        assert "hostname" not in d.payload
        assert "keyword" not in d.payload
        assert "authMethod" not in d.payload

    def test_optional_fields_included_when_set(self):
        m = _make_monitor(keyword="OK", hostname="192.168.1.1", port=8080)
        d = build_desired(m)
        assert d.payload["keyword"] == "OK"
        assert d.payload["hostname"] == "192.168.1.1"
        assert d.payload["port"] == 8080

    def test_headers_serialized_as_json(self):
        import json
        m = _make_monitor(headers={"Authorization": "Bearer token"})
        d = build_desired(m)
        assert d.payload["headers"] == json.dumps({"Authorization": "Bearer token"})

    def test_accepted_statuscodes_included(self):
        m = _make_monitor(accepted_statuscodes=["200-299", "404"])
        d = build_desired(m)
        assert d.payload["accepted_statuscodes"] == ["200-299", "404"]

    def test_parent_name_resolved_from_group(self):
        m = _make_monitor(group="my-group")
        d = build_desired(m)
        assert d.parent_name == "my-group"

    def test_parent_name_resolved_from_parent_name(self):
        m = _make_monitor(parent_name="fallback-group")
        d = build_desired(m)
        assert d.parent_name == "fallback-group"

    def test_group_takes_precedence_over_parent_name(self):
        m = _make_monitor(group="primary", parent_name="secondary")
        d = build_desired(m)
        assert d.parent_name == "primary"

    def test_notification_names_forwarded(self):
        m = _make_monitor(notification_names=["slack", "email"])
        d = build_desired(m)
        assert d.notification_names == ["slack", "email"]

    def test_user_tags_forwarded(self):
        m = _make_monitor(tags=["prod", "critical"])
        d = build_desired(m)
        assert d.user_tags == ["prod", "critical"]

    def test_empty_notification_names_default(self):
        m = _make_monitor()
        d = build_desired(m)
        assert d.notification_names == []
        assert d.user_tags == []
