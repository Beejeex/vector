from __future__ import annotations

from typing import Optional

from src.models.kuma import LiveMonitor
from src.models.desired import OWNER_TAG_NAME, OWNER_TAG_VALUE_PREFIX, parse_identity_key


def is_managed(monitor: LiveMonitor) -> bool:
    """Return True if this monitor is owned by Vector."""
    val = monitor.tag_value(OWNER_TAG_NAME)
    return val is not None and val.startswith(OWNER_TAG_VALUE_PREFIX)


def get_identity_key(monitor: LiveMonitor) -> Optional[str]:
    """Extract the '<namespace>/<name>' identity key from the ownership tag."""
    val = monitor.tag_value(OWNER_TAG_NAME)
    if val is None:
        return None
    return parse_identity_key(val)


def filter_managed(monitors: list[LiveMonitor]) -> list[LiveMonitor]:
    """Return only monitors that are owned by Vector."""
    return [m for m in monitors if is_managed(m)]


def find_parent_id(parent_name: str, monitors: list[LiveMonitor]) -> Optional[int]:
    """Resolve a parent group name to its Uptime Kuma monitor ID."""
    for m in monitors:
        if m.type == "group" and m.name == parent_name:
            return m.id
    return None
