"""Tests for UptimeKumaClient — particularly the conditions injection fix."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

from src.services.kuma_client import UptimeKumaClient


def _make_client() -> UptimeKumaClient:
    return UptimeKumaClient(
        url="http://kuma.example.com",
        username=None,
        password=None,
    )


def _mock_api(build_result: dict[str, Any] | None = None, call_result: dict[str, Any] | None = None) -> MagicMock:
    """Return a mock UptimeKumaApi instance."""
    api = MagicMock()
    api._build_monitor_data.return_value = build_result or {
        "type": "http",
        "name": "test",
        "interval": 60,
    }
    api._call.return_value = call_result or {"monitorID": 42, "msg": "successAdded"}
    return api


class TestCreateMonitorConditionsInjection:
    def test_conditions_injected_when_absent(self):
        """_build_monitor_data returns no conditions field → must be injected as '[]'."""
        client = _make_client()
        api = _mock_api(build_result={"type": "http", "name": "test"})
        client._api = api

        client.create_monitor({"type": "http", "name": "test"})

        sent_data = api._call.call_args[0][1]
        assert "conditions" in sent_data
        assert sent_data["conditions"] == "[]"

    def test_conditions_not_overridden_when_present(self):
        """If _build_monitor_data already returns conditions, it must not be overwritten."""
        client = _make_client()
        existing = '[{"id":"abc"}]'
        api = _mock_api(build_result={"type": "http", "name": "test", "conditions": existing})
        client._api = api

        client.create_monitor({"type": "http", "name": "test"})

        sent_data = api._call.call_args[0][1]
        assert sent_data["conditions"] == existing

    def test_conditions_not_overridden_when_empty_string_truthy_check(self):
        """An empty string '' for conditions is falsy — inject '[]' in that case too."""
        client = _make_client()
        api = _mock_api(build_result={"type": "http", "name": "test", "conditions": ""})
        client._api = api

        client.create_monitor({"type": "http", "name": "test"})

        sent_data = api._call.call_args[0][1]
        assert sent_data["conditions"] == "[]"

    def test_calls_add_event_not_add_monitor(self):
        """Must use _call('add', ...) directly, not the high-level add_monitor()."""
        client = _make_client()
        api = _mock_api()
        client._api = api

        client.create_monitor({"type": "http", "name": "test"})

        api._call.assert_called_once()
        assert api._call.call_args[0][0] == "add"
        api.add_monitor.assert_not_called()

    def test_returns_monitor_id(self):
        """create_monitor must return the integer monitorID from the API response."""
        client = _make_client()
        api = _mock_api(call_result={"monitorID": 99, "msg": "successAdded"})
        client._api = api

        result = client.create_monitor({"type": "http", "name": "test"})

        assert result == 99

    def test_payload_kwargs_passed_to_build_monitor_data(self):
        """All keys from payload must be forwarded to _build_monitor_data as kwargs."""
        client = _make_client()
        api = _mock_api()
        client._api = api

        payload = {"type": "http", "name": "my-svc", "url": "https://example.com", "interval": 120}
        client.create_monitor(payload)

        api._build_monitor_data.assert_called_once_with(**payload)


class TestCreateMonitorNotConnected:
    def test_raises_when_not_connected(self):
        """Calling create_monitor before connect() must raise RuntimeError."""
        client = _make_client()
        with pytest.raises(RuntimeError, match="not connected"):
            client.create_monitor({"type": "http", "name": "test"})


class TestConnect:
    def test_connect_auth_disabled(self):
        """With no credentials, connect() must not call login or login_by_token."""
        client = _make_client()
        with patch("src.services.kuma_client.UptimeKumaApi") as MockApi:
            mock_instance = MagicMock()
            MockApi.return_value = mock_instance
            client.connect()
            mock_instance.login.assert_not_called()
            mock_instance.login_by_token.assert_not_called()

    def test_connect_username_password(self):
        """With username+password, connect() must call login()."""
        client = UptimeKumaClient(
            url="http://kuma.example.com",
            username="admin",
            password="secret",
        )
        with patch("src.services.kuma_client.UptimeKumaApi") as MockApi:
            mock_instance = MagicMock()
            MockApi.return_value = mock_instance
            client.connect()
            mock_instance.login.assert_called_once_with("admin", "secret")
            mock_instance.login_by_token.assert_not_called()

    def test_connect_api_token(self):
        """With an API token, connect() must call login_by_token()."""
        client = UptimeKumaClient(
            url="http://kuma.example.com",
            username=None,
            password=None,
            api_token="mytoken",
        )
        with patch("src.services.kuma_client.UptimeKumaApi") as MockApi:
            mock_instance = MagicMock()
            MockApi.return_value = mock_instance
            client.connect()
            mock_instance.login_by_token.assert_called_once_with("mytoken")
            mock_instance.login.assert_not_called()


class TestDisconnect:
    def test_disconnect_clears_api(self):
        """After disconnect(), the internal _api must be None."""
        client = _make_client()
        client._api = MagicMock()
        client.disconnect()
        assert client._api is None

    def test_disconnect_when_not_connected_is_silent(self):
        """Calling disconnect() without a prior connect() must not raise."""
        client = _make_client()
        client.disconnect()  # should not raise


class TestListMonitors:
    def test_returns_live_monitors_from_dict_response(self):
        """get_monitors() returns a dict keyed by ID — must be converted to list."""
        client = _make_client()
        api = MagicMock()
        api.get_monitors.return_value = {
            1: {"id": 1, "name": "svc-a", "type": "http", "tags": []},
            2: {"id": 2, "name": "svc-b", "type": "ping", "tags": []},
        }
        client._api = api

        monitors = client.list_monitors()

        assert len(monitors) == 2
        ids = {m.id for m in monitors}
        assert ids == {1, 2}

    def test_returns_live_monitors_from_list_response(self):
        """If get_monitors() returns a list, must still work correctly."""
        client = _make_client()
        api = MagicMock()
        api.get_monitors.return_value = [
            {"id": 3, "name": "svc-c", "type": "http", "tags": []},
        ]
        client._api = api

        monitors = client.list_monitors()

        assert len(monitors) == 1
        assert monitors[0].id == 3


class TestEnsureTag:
    def test_returns_existing_tag_id(self):
        """If tag already exists in Kuma, returns its ID without creating a new one."""
        client = _make_client()
        api = MagicMock()
        api.get_tags.return_value = [{"id": 5, "name": "managed-by", "color": "#fff"}]
        client._api = api

        tag_id = client.ensure_tag("managed-by")

        assert tag_id == 5
        api.add_tag.assert_not_called()

    def test_creates_tag_when_missing(self):
        """If tag does not exist, creates it and returns the new ID."""
        client = _make_client()
        api = MagicMock()
        api.get_tags.return_value = []
        api.add_tag.return_value = {"id": 7}
        client._api = api

        tag_id = client.ensure_tag("managed-by")

        assert tag_id == 7
        api.add_tag.assert_called_once()

    def test_cache_prevents_repeated_api_calls(self):
        """Second call for same tag name must use cache — no API call."""
        client = _make_client()
        api = MagicMock()
        api.get_tags.return_value = [{"id": 5, "name": "managed-by", "color": "#fff"}]
        client._api = api

        client.ensure_tag("managed-by")
        client.ensure_tag("managed-by")

        api.get_tags.assert_called_once()
