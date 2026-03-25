from __future__ import annotations

import logging
from typing import Protocol

from src.models.desired import DesiredMonitor
from src.services.discovery.base import (
    DiscoveryK8sClientProtocol,
    DiscoverySourceProtocol,
    ValidatorProtocol,
    default_payload,
    make_group_key,
)

logger = logging.getLogger(__name__)


class DiscoveryRunnerProtocol(Protocol):
    def run(self) -> list[DesiredMonitor]: ...


class DiscoveryRunner:
    """Orchestrates all discovery sources across all opted-in namespaces.

    For each opted-in namespace the runner:
    1. Creates one group DesiredMonitor (so discovered monitors can be grouped).
    2. Runs every enabled source and collects child monitors.
    3. Filters child monitors through the validator before including them.

    The returned list feeds directly into the reconciler's desired monitor list.
    """

    def __init__(
        self,
        k8s: DiscoveryK8sClientProtocol,
        sources: list[DiscoverySourceProtocol],
        validator: ValidatorProtocol | None = None,
    ) -> None:
        self._k8s = k8s
        self._sources = sources
        self._validator = validator

    def run(self) -> list[DesiredMonitor]:
        result: list[DesiredMonitor] = []

        namespaces = self._k8s.list_opted_in_namespaces()
        logger.info("Discovery: opted-in namespaces", extra={"count": len(namespaces)})

        for ns_info in namespaces:
            # Always create a group monitor for the namespace so children can reference it.
            group_monitor = _make_group_monitor(ns_info.name, ns_info.group_name)
            result.append(group_monitor)

            child_count = 0
            skipped_count = 0
            for source in self._sources:
                try:
                    discovered = source.discover(ns_info.name, ns_info.group_name)
                except Exception as exc:
                    logger.warning(
                        "Discovery source failed",
                        extra={
                            "namespace": ns_info.name,
                            "source": type(source).__name__,
                            "error": str(exc),
                        },
                    )
                    continue

                for monitor in discovered:
                    if self._validator is not None and not self._validator.is_reachable(monitor):
                        skipped_count += 1
                        continue
                    result.append(monitor)
                    child_count += 1

            logger.info(
                "Discovery complete for namespace",
                extra={
                    "namespace": ns_info.name,
                    "discovered": child_count,
                    "skipped_unreachable": skipped_count,
                },
            )

        return result


def _make_group_monitor(namespace: str, group_name: str) -> DesiredMonitor:
    key = make_group_key(namespace)
    return DesiredMonitor(
        identity_key=key,
        payload=default_payload(
            "group",
            group_name,
            description=f"Auto-discovered monitors for namespace {namespace}",
        ),
        parent_name=None,
        notification_names=[],
        user_tags=[],
    )
