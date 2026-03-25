from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from src.models.crd import KumaMonitor

# Tag name used in Uptime Kuma to mark ownership and encode the identity key.
# Tag value format: "vector:<namespace>/<name>"
OWNER_TAG_NAME = "managed-by"
OWNER_TAG_VALUE_PREFIX = "vector:"
OWNER_TAG_COLOR = "#7b61ff"


def owner_tag_value(identity_key: str) -> str:
    return f"{OWNER_TAG_VALUE_PREFIX}{identity_key}"


def parse_identity_key(tag_value: str) -> Optional[str]:
    """Extract the identity key from a tag value, or None if it's not ours."""
    if tag_value.startswith(OWNER_TAG_VALUE_PREFIX):
        return tag_value[len(OWNER_TAG_VALUE_PREFIX):]
    return None


@dataclass
class DesiredMonitor:
    """Normalized desired state for one Uptime Kuma monitor."""

    identity_key: str              # "<namespace>/<name>" — used for ownership matching
    payload: dict[str, Any]        # Kuma API fields, ready to send (minus parent/tag resolution)
    parent_name: Optional[str]     # group monitor name to resolve to ID at reconcile time
    notification_names: list[str]  # notification channel names to resolve to IDs
    user_tags: list[str]           # extra tag names from the spec


def build_desired(monitor: KumaMonitor) -> DesiredMonitor:
    """Convert a KumaMonitor CRD resource into a DesiredMonitor payload."""
    spec = monitor.spec

    payload: dict[str, Any] = {
        "type": spec.type,
        "name": spec.name,
        "interval": spec.interval,
        "timeout": spec.timeout,
        "retryInterval": spec.retry_interval,
        "resendInterval": spec.resend_interval,
        "maxretries": spec.retries,
        "maintenance": spec.maintenance,
        "upsideDown": spec.upside_down,
        "expiryNotification": spec.expiry_notification,
        "ignoreTls": spec.ignore_tls,
        "cacheBust": spec.cache_bust,
        "maxredirects": spec.max_redirects,
        "method": spec.method,
        "invertKeyword": spec.invert_keyword,
        "packetSize": spec.packet_size,
        "dns_resolve_type": spec.dns_resolve_type,
        "kafkaProducerSsl": spec.kafka_producer_ssl,
        "kafkaProducerAllowAutoTopicCreation": spec.kafka_producer_allow_auto_topic_creation,
        "grpcEnableTls": spec.grpc_enable_tls,
    }

    _set_if(payload, "url", spec.url)
    _set_if(payload, "description", spec.description)
    _set_if(payload, "ipFamily", spec.ip_family)
    _set_if(payload, "authMethod", spec.auth_method)
    _set_if(payload, "basic_auth_user", spec.basic_auth_user)
    _set_if(payload, "basic_auth_pass", spec.basic_auth_pass)
    _set_if(payload, "authDomain", spec.auth_domain)
    _set_if(payload, "authWorkstation", spec.auth_workstation)
    _set_if(payload, "oauth_client_id", spec.oauth_client_id)
    _set_if(payload, "oauth_client_secret", spec.oauth_client_secret)
    _set_if(payload, "oauth_token_url", spec.oauth_token_url)
    _set_if(payload, "oauth_scopes", spec.oauth_scopes)
    _set_if(payload, "oauth_audience", spec.oauth_audience)
    _set_if(payload, "oauth_auth_method", spec.oauth_auth_method)
    _set_if(payload, "tlsCert", spec.tls_cert)
    _set_if(payload, "tlsKey", spec.tls_key)
    _set_if(payload, "tlsCa", spec.tls_ca)
    _set_if(payload, "keyword", spec.keyword)
    _set_if(payload, "json_path", spec.json_path)
    _set_if(payload, "jsonPathOperator", spec.json_path_operator)
    _set_if(payload, "expected_value", spec.expected_value)
    _set_if(payload, "hostname", spec.hostname)
    _set_if(payload, "port", spec.port)
    _set_if(payload, "dns_resolve_server", spec.dns_resolve_server)
    _set_if(payload, "docker_container", spec.docker_container)
    _set_if(payload, "docker_host", spec.docker_host)
    _set_if(payload, "mqtt_topic", spec.mqtt_topic)
    _set_if(payload, "mqtt_username", spec.mqtt_username)
    _set_if(payload, "mqtt_password", spec.mqtt_password)
    _set_if(payload, "mqtt_success_message", spec.mqtt_success_message)
    _set_if(payload, "database_connection_string", spec.database_connection_string)
    _set_if(payload, "database_query", spec.database_query)
    _set_if(payload, "kafka_producer_brokers", spec.kafka_producer_brokers)
    _set_if(payload, "kafka_producer_topic", spec.kafka_producer_topic)
    _set_if(payload, "kafka_producer_message", spec.kafka_producer_message)
    _set_if(payload, "grpc_url", spec.grpc_url)
    _set_if(payload, "grpc_protobuf", spec.grpc_protobuf)
    _set_if(payload, "grpc_body", spec.grpc_body)
    _set_if(payload, "grpc_metadata", spec.grpc_metadata)
    _set_if(payload, "grpc_method", spec.grpc_method)
    _set_if(payload, "grpc_service_name", spec.grpc_service_name)
    _set_if(payload, "radiusUsername", spec.radius_username)
    _set_if(payload, "radiusPassword", spec.radius_password)
    _set_if(payload, "radiusCalledStationId", spec.radius_called_station_id)
    _set_if(payload, "radiusCallingStationId", spec.radius_calling_station_id)
    _set_if(payload, "radiusSecret", spec.radius_secret)

    if spec.accepted_statuscodes:
        payload["accepted_statuscodes"] = spec.accepted_statuscodes

    if spec.headers:
        # Kuma stores headers as a JSON string
        payload["headers"] = json.dumps(spec.headers)

    if spec.body is not None:
        payload["body"] = spec.body

    if spec.http_body_encoding:
        payload["httpBodyEncoding"] = spec.http_body_encoding

    return DesiredMonitor(
        identity_key=monitor.identity_key,
        payload=payload,
        parent_name=spec.resolved_parent_name,
        notification_names=spec.notification_names or [],
        user_tags=spec.tags or [],
    )


def _set_if(d: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        d[key] = value
