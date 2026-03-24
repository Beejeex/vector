from __future__ import annotations

import logging
from typing import Any, Optional

from src.models.desired import (
    DesiredMonitor,
    build_desired,
    OWNER_TAG_NAME,
    OWNER_TAG_COLOR,
    owner_tag_value,
)
from src.models.kuma import LiveMonitor
from src.services.diff import compute_diff, payload_hash
from src.services.kubernetes_client import KubernetesClientProtocol
from src.services.kuma_client import KumaClientProtocol
from src.services.ownership import find_parent_id, get_identity_key
from src.services.store import StoreProtocol

logger = logging.getLogger(__name__)


class Reconciler:
    def __init__(
        self,
        k8s: KubernetesClientProtocol,
        kuma: KumaClientProtocol,
        store: StoreProtocol,
    ) -> None:
        self._k8s = k8s
        self._kuma = kuma
        self._store = store

    def run_once(self) -> None:
        logger.info("Reconciliation cycle started")

        # 1. Read desired state from Kubernetes
        k8s_monitors = self._k8s.list_monitors()
        desired_monitors: list[DesiredMonitor] = []
        for km in k8s_monitors:
            try:
                desired_monitors.append(build_desired(km))
            except Exception as exc:
                logger.warning(
                    "Skipping monitor — failed to build desired state",
                    extra={"namespace": km.namespace, "name": km.name, "error": str(exc)},
                )
        logger.info("Desired state ready", extra={"count": len(desired_monitors)})

        # 2. Read current live state from Uptime Kuma
        live_monitors = self._kuma.list_monitors()
        logger.info("Live monitors loaded", extra={"count": len(live_monitors)})

        # 3. Compute diff
        diff = compute_diff(desired_monitors, live_monitors)
        logger.info(
            "Diff computed",
            extra={
                "to_create": len(diff.to_create),
                "to_update": len(diff.to_update),
                "to_delete": len(diff.to_delete),
                "skipped_unmanaged": diff.skipped_unmanaged,
            },
        )

        # Ensure ownership tag exists in Kuma (created once, cached for the cycle)
        owner_tag_id = self._kuma.ensure_tag(OWNER_TAG_NAME, OWNER_TAG_COLOR)

        # Resolve notification name → ID map once per cycle
        notification_map = self._build_notification_map()

        # 4a. Creates — process group monitors first so children can reference them
        ordered_creates = sorted(
            diff.to_create, key=lambda d: 0 if d.payload.get("type") == "group" else 1
        )
        created_monitors: list[LiveMonitor] = []
        for desired in ordered_creates:
            new_monitor = self._create(desired, live_monitors + created_monitors, owner_tag_id, notification_map)
            if new_monitor:
                created_monitors.append(new_monitor)

        # 4b. Updates
        for desired, monitor_id in diff.to_update:
            self._update(desired, monitor_id, live_monitors, notification_map)

        # 4c. Deletes
        for monitor_id in diff.to_delete:
            self._delete(monitor_id, live_monitors)

        logger.info(
            "Reconciliation cycle complete",
            extra={
                "created": len(diff.to_create),
                "updated": len(diff.to_update),
                "deleted": len(diff.to_delete),
            },
        )

    # -------------------------------------------------------------------------
    # Internal helpers

    def _build_notification_map(self) -> dict[str, int]:
        try:
            notifications = self._kuma.get_notifications()
            return {n.get("name", ""): n["id"] for n in notifications if "id" in n}
        except Exception as exc:
            logger.warning("Failed to fetch notification list", extra={"error": str(exc)})
            return {}

    def _resolve_payload(
        self,
        desired: DesiredMonitor,
        live_monitors: list[LiveMonitor],
        notification_map: dict[str, int],
    ) -> dict[str, Any]:
        payload = dict(desired.payload)

        # Resolve parent group name → Kuma monitor ID
        if desired.parent_name:
            parent_id = find_parent_id(desired.parent_name, live_monitors)
            if parent_id is not None:
                payload["parent"] = parent_id
            else:
                logger.warning(
                    "Parent group not found — monitor will be created without parent",
                    extra={"parent_name": desired.parent_name, "key": desired.identity_key},
                )

        # Resolve notification names → IDs
        if desired.notification_names:
            notif_ids: dict[str, bool] = {}
            for n_name in desired.notification_names:
                n_id = notification_map.get(n_name)
                if n_id is not None:
                    notif_ids[str(n_id)] = True
                else:
                    logger.warning(
                        "Notification channel not found in Uptime Kuma",
                        extra={"notification_name": n_name, "key": desired.identity_key},
                    )
            if notif_ids:
                payload["notification_id_list"] = notif_ids

        return payload

    def _create(
        self,
        desired: DesiredMonitor,
        live_monitors: list[LiveMonitor],
        owner_tag_id: int,
        notification_map: dict[str, int],
    ) -> Optional[LiveMonitor]:
        namespace, name = _split_key(desired.identity_key)
        try:
            payload = self._resolve_payload(desired, live_monitors, notification_map)
            monitor_id = self._kuma.create_monitor(payload)

            # Attach ownership + identity tag
            self._kuma.add_monitor_tag(
                tag_id=owner_tag_id,
                monitor_id=monitor_id,
                value=owner_tag_value(desired.identity_key),
            )

            self._store.upsert_state(desired.identity_key, monitor_id, payload_hash(desired.payload))
            self._store.record_trace(namespace, name, "create", "success", monitor_id)
            logger.info(
                "Monitor created",
                extra={"namespace": namespace, "name": name, "monitor_id": monitor_id},
            )
            # Return a minimal LiveMonitor so subsequent creates can resolve it as a parent
            return LiveMonitor({
                "id": monitor_id,
                "name": desired.payload.get("name", name),
                "type": desired.payload.get("type", ""),
                "tags": [{
                    "name": OWNER_TAG_NAME,
                    "value": owner_tag_value(desired.identity_key),
                }],
            })
        except Exception as exc:
            self._store.record_trace(namespace, name, "create", "error", detail=str(exc))
            logger.error(
                "Failed to create monitor",
                extra={"namespace": namespace, "name": name, "error": str(exc)},
            )
            return None

    def _update(
        self,
        desired: DesiredMonitor,
        monitor_id: int,
        live_monitors: list[LiveMonitor],
        notification_map: dict[str, int],
    ) -> None:
        namespace, name = _split_key(desired.identity_key)
        try:
            payload = self._resolve_payload(desired, live_monitors, notification_map)
            self._kuma.update_monitor(monitor_id, payload)
            self._store.upsert_state(desired.identity_key, monitor_id, payload_hash(desired.payload))
            self._store.record_trace(namespace, name, "update", "success", monitor_id)
            logger.info(
                "Monitor updated",
                extra={"namespace": namespace, "name": name, "monitor_id": monitor_id},
            )
        except Exception as exc:
            self._store.record_trace(namespace, name, "update", "error", monitor_id, str(exc))
            logger.error(
                "Failed to update monitor",
                extra={"namespace": namespace, "name": name, "monitor_id": monitor_id, "error": str(exc)},
            )

    def _delete(self, monitor_id: int, live_monitors: list[LiveMonitor]) -> None:
        key = next(
            (get_identity_key(m) for m in live_monitors if m.id == monitor_id), None
        )
        namespace, name = _split_key(key) if key else ("", str(monitor_id))
        try:
            self._kuma.delete_monitor(monitor_id)
            if key:
                self._store.delete_state(key)
            self._store.record_trace(namespace, name, "delete", "success", monitor_id)
            logger.info(
                "Monitor deleted",
                extra={"namespace": namespace, "name": name, "monitor_id": monitor_id},
            )
        except Exception as exc:
            self._store.record_trace(namespace, name, "delete", "error", monitor_id, str(exc))
            logger.error(
                "Failed to delete monitor",
                extra={"namespace": namespace, "name": name, "monitor_id": monitor_id, "error": str(exc)},
            )


def _split_key(key: Optional[str]) -> tuple[str, str]:
    if not key or "/" not in key:
        return ("", key or "")
    ns, _, name = key.partition("/")
    return ns, name
