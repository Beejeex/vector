from __future__ import annotations

from typing import Any, Optional


class LiveMonitor:
    """A monitor as returned by the Uptime Kuma API."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.id: int = data["id"]
        self.name: str = data.get("name", "")
        self.type: str = data.get("type", "")
        self.tags: list[dict[str, Any]] = data.get("tags", [])
        self._raw: dict[str, Any] = data

    @property
    def raw(self) -> dict[str, Any]:
        return self._raw

    def tag_value(self, tag_name: str) -> Optional[str]:
        """Return the value of a named tag on this monitor, or None if absent."""
        for t in self.tags:
            if t.get("name") == tag_name:
                return t.get("value", "")
        return None
