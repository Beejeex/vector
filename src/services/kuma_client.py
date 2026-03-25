from __future__ import annotations

import logging
from typing import Any, Optional, Protocol

from uptime_kuma_api import UptimeKumaApi

from src.models.kuma import LiveMonitor

logger = logging.getLogger(__name__)


class KumaClientProtocol(Protocol):
    def list_monitors(self) -> list[LiveMonitor]: ...
    def create_monitor(self, payload: dict[str, Any]) -> int: ...
    def update_monitor(self, monitor_id: int, payload: dict[str, Any]) -> None: ...
    def delete_monitor(self, monitor_id: int) -> None: ...
    def ensure_tag(self, name: str, color: str) -> int: ...
    def add_monitor_tag(self, tag_id: int, monitor_id: int, value: str) -> None: ...
    def delete_monitor_tag(self, tag_id: int, monitor_id: int, value: str) -> None: ...
    def get_notifications(self) -> list[dict[str, Any]]: ...


class UptimeKumaClient:
    """
    Wraps uptime-kuma-api for one reconcile cycle.
    Call connect() before use, disconnect() when done.
    """

    def __init__(self, url: str, username: Optional[str], password: Optional[str], api_token: Optional[str] = None) -> None:
        self._url = url
        self._username = username
        self._password = password
        self._api_token = api_token
        self._api: Optional[UptimeKumaApi] = None
        self._tag_cache: dict[str, int] = {}  # tag name → id

    def connect(self) -> None:
        self._api = UptimeKumaApi(self._url)
        if self._api_token:
            self._api.login_by_token(self._api_token)
            logger.debug("Connected to Uptime Kuma via API token", extra={"url": self._url})
        elif self._username and self._password:
            self._api.login(self._username, self._password)
            logger.debug("Connected to Uptime Kuma via username/password", extra={"url": self._url})
        else:
            # Auth disabled — Uptime Kuma emits auto_login; no explicit login call needed.
            logger.debug("Connected to Uptime Kuma (auth disabled)", extra={"url": self._url})

    def disconnect(self) -> None:
        if self._api:
            try:
                self._api.disconnect()
            except Exception:
                pass
            self._api = None
            self._tag_cache.clear()
            logger.debug("Disconnected from Uptime Kuma")

    @property
    def _client(self) -> UptimeKumaApi:
        if self._api is None:
            raise RuntimeError("KumaClient is not connected — call connect() first")
        return self._api

    def list_monitors(self) -> list[LiveMonitor]:
        raw = self._client.get_monitors()
        # uptime-kuma-api may return a list or a dict keyed by monitor ID
        items: list[dict[str, Any]] = list(raw.values()) if isinstance(raw, dict) else raw
        return [LiveMonitor(m) for m in items]

    def create_monitor(self, payload: dict[str, Any]) -> int:
        # Build the data dict using the library helper, then inject `conditions` if absent.
        # Uptime Kuma >= 1.23 requires conditions as a NOT NULL column; the library (1.2.1)
        # does not populate it, causing an SQLITE_CONSTRAINT failure on add_monitor.
        data = self._client._build_monitor_data(**payload)
        if not data.get("conditions"):
            data["conditions"] = "[]"
        result = self._client._call("add", data)
        monitor_id: int = result["monitorID"]
        logger.info(
            "Monitor created in Uptime Kuma",
            extra={"monitor_id": monitor_id, "name": payload.get("name")},
        )
        return monitor_id

    def update_monitor(self, monitor_id: int, payload: dict[str, Any]) -> None:
        self._client.edit_monitor(monitor_id, **payload)
        logger.info(
            "Monitor updated in Uptime Kuma",
            extra={"monitor_id": monitor_id, "name": payload.get("name")},
        )

    def delete_monitor(self, monitor_id: int) -> None:
        self._client.delete_monitor(monitor_id)
        logger.info("Monitor deleted from Uptime Kuma", extra={"monitor_id": monitor_id})

    def ensure_tag(self, name: str, color: str = "#7b61ff") -> int:
        """Return the tag ID for the given name, creating it in Kuma if it doesn't exist."""
        if name in self._tag_cache:
            return self._tag_cache[name]

        for t in self._client.get_tags():
            if t.get("name") == name:
                self._tag_cache[name] = t["id"]
                return t["id"]

        result = self._client.add_tag(name=name, color=color)
        tag_id: int = result["id"]
        self._tag_cache[name] = tag_id
        logger.info("Tag created in Uptime Kuma", extra={"tag_name": name, "tag_id": tag_id})
        return tag_id

    def add_monitor_tag(self, tag_id: int, monitor_id: int, value: str) -> None:
        self._client.add_monitor_tag(tag_id=tag_id, monitor_id=monitor_id, value=value)

    def delete_monitor_tag(self, tag_id: int, monitor_id: int, value: str) -> None:
        self._client.delete_monitor_tag(tag_id=tag_id, monitor_id=monitor_id, value=value)

    def get_notifications(self) -> list[dict[str, Any]]:
        return self._client.get_notifications()
