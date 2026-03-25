from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from src.models.desired import DesiredMonitor
from src.models.kuma import LiveMonitor
from src.services.ownership import filter_managed, get_identity_key

logger = logging.getLogger(__name__)

# Kuma API field names that Vector actively manages.
# Only these are compared when deciding whether an update is needed.
# Fields not in this set (e.g. internal Kuma state, heartbeat data) are ignored.
# These must match the exact names returned by Uptime Kuma in live monitor data.
_OWNED_FIELDS: frozenset[str] = frozenset({
    "type", "name", "url", "interval", "timeout", "retryInterval",
    "resendInterval", "maxretries", "upsideDown",
    "expiryNotification", "ignoreTls", "maxredirects", "method",
    "invertKeyword", "packetSize", "dns_resolve_type", "kafkaProducerSsl",
    "kafkaProducerAllowAutoTopicCreation", "grpcEnableTls",
    "authMethod", "basic_auth_user", "authDomain", "authWorkstation",
    "oauth_client_id", "oauth_token_url", "oauth_scopes",
    "oauth_auth_method", "tlsCert", "tlsKey", "tlsCa",
    "keyword", "jsonPath", "expectedValue",
    "hostname", "port", "dns_resolve_server", "docker_container", "docker_host",
    "mqttTopic", "mqttUsername", "mqttPassword", "mqttSuccessMessage",
    "databaseConnectionString", "databaseQuery",
    "kafkaProducerBrokers", "kafkaProducerTopic", "kafkaProducerMessage",
    "grpcUrl", "grpcProtobuf", "grpcBody", "grpcMetadata",
    "grpcMethod", "grpcServiceName",
    "radiusUsername", "radiusPassword", "radiusSecret",
    "radiusCalledStationId", "radiusCallingStationId",
    "accepted_statuscodes", "headers", "body", "httpBodyEncoding", "description",
    "notificationIDList",
})


@dataclass
class DiffResult:
    to_create: list[DesiredMonitor] = field(default_factory=list)
    to_update: list[tuple[DesiredMonitor, int]] = field(default_factory=list)
    to_delete: list[int] = field(default_factory=list)
    skipped_unmanaged: int = 0


def payload_hash(payload: dict[str, Any]) -> str:
    """Stable SHA-256 of a payload dict for quick drift detection."""
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _normalize(val: Any) -> Any:
    """Normalize a value for stable comparison."""
    if isinstance(val, list):
        return sorted(str(x) for x in val)
    if isinstance(val, str):
        return val.strip()
    return val


def compute_diff(
    desired_monitors: list[DesiredMonitor],
    live_monitors: list[LiveMonitor],
) -> DiffResult:
    result = DiffResult()

    managed = filter_managed(live_monitors)
    result.skipped_unmanaged = len(live_monitors) - len(managed)

    # Build lookup maps
    live_by_key: dict[str, LiveMonitor] = {}
    for m in managed:
        key = get_identity_key(m)
        if key:
            live_by_key[key] = m

    desired_by_key: dict[str, DesiredMonitor] = {d.identity_key: d for d in desired_monitors}

    # Desired exists but no live match → CREATE
    for key, desired in desired_by_key.items():
        if key not in live_by_key:
            result.to_create.append(desired)
            logger.debug("diff → create", extra={"key": key})

    # Both exist → compare and UPDATE if changed
    for key, desired in desired_by_key.items():
        if key in live_by_key:
            live = live_by_key[key]
            if _needs_update(desired, live):
                result.to_update.append((desired, live.id))
                logger.debug("diff → update", extra={"key": key, "monitor_id": live.id})

    # Live managed but no desired match → DELETE
    for key, live in live_by_key.items():
        if key not in desired_by_key:
            result.to_delete.append(live.id)
            logger.debug("diff → delete", extra={"key": key, "monitor_id": live.id})

    return result


def _needs_update(desired: DesiredMonitor, live: LiveMonitor) -> bool:
    """Return True if any owned field in desired differs from the live monitor."""
    raw = live.raw
    for key, desired_val in desired.payload.items():
        if key not in _OWNED_FIELDS:
            continue
        live_val = raw.get(key)
        if _normalize(desired_val) != _normalize(live_val):
            return True
    return False
