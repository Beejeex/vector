from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class KumaMonitorSpec(BaseModel):
    model_config = {"extra": "ignore"}

    # --- Core ---
    name: str
    type: str
    url: Optional[str] = None
    description: Optional[str] = None

    # --- Timing ---
    interval: int = 60
    timeout: int = 30
    retry_interval: int = 60
    resend_interval: int = 0
    retries: int = 1

    # --- Behaviour ---
    enabled: bool = True
    maintenance: bool = False
    upside_down: bool = False
    expiry_notification: bool = False

    # --- HTTP ---
    method: str = "GET"
    headers: Optional[dict[str, str]] = None
    body: Optional[str] = None
    http_body_encoding: Optional[str] = None
    max_redirects: int = 10
    accepted_statuscodes: Optional[list[str]] = None
    ignore_tls: bool = False
    cache_bust: bool = False
    ip_family: Optional[str] = None

    # --- Authentication ---
    auth_method: Optional[str] = None
    basic_auth_user: Optional[str] = None
    basic_auth_pass: Optional[str] = None
    auth_domain: Optional[str] = None
    auth_workstation: Optional[str] = None
    oauth_client_id: Optional[str] = None
    oauth_client_secret: Optional[str] = None
    oauth_token_url: Optional[str] = None
    oauth_scopes: Optional[str] = None
    oauth_audience: Optional[str] = None
    oauth_auth_method: Optional[str] = None
    tls_cert: Optional[str] = None
    tls_key: Optional[str] = None
    tls_ca: Optional[str] = None

    # --- Keyword / JSON-query ---
    keyword: Optional[str] = None
    invert_keyword: bool = False
    json_path: Optional[str] = None
    json_path_operator: Optional[str] = None
    expected_value: Optional[str] = None

    # --- Hostname / Port ---
    hostname: Optional[str] = None
    port: Optional[int] = None
    packet_size: int = 56

    # --- DNS ---
    dns_resolve_type: str = "A"
    dns_resolve_server: Optional[str] = None

    # --- Docker ---
    docker_container: Optional[str] = None
    docker_host: Optional[str] = None

    # --- MQTT ---
    mqtt_topic: Optional[str] = None
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None
    mqtt_success_message: Optional[str] = None

    # --- Database ---
    database_connection_string: Optional[str] = None
    database_query: Optional[str] = None

    # --- Kafka Producer ---
    kafka_producer_brokers: Optional[list[str]] = None
    kafka_producer_topic: Optional[str] = None
    kafka_producer_message: Optional[str] = None
    kafka_producer_ssl: bool = False
    kafka_producer_allow_auto_topic_creation: bool = False

    # --- gRPC ---
    grpc_url: Optional[str] = None
    grpc_protobuf: Optional[str] = None
    grpc_body: Optional[str] = None
    grpc_metadata: Optional[str] = None
    grpc_method: Optional[str] = None
    grpc_service_name: Optional[str] = None
    grpc_enable_tls: bool = False

    # --- RADIUS ---
    radius_username: Optional[str] = None
    radius_password: Optional[str] = None
    radius_called_station_id: Optional[str] = None
    radius_calling_station_id: Optional[str] = None
    radius_secret: Optional[str] = None

    # --- Grouping ---
    group: Optional[str] = None
    parent_name: Optional[str] = None

    # --- Tags & Notifications ---
    tags: Optional[list[str]] = None
    notification_names: Optional[list[str]] = None

    @property
    def resolved_parent_name(self) -> Optional[str]:
        """Prefers `group` over `parent_name` (they are aliases)."""
        return self.group or self.parent_name


class KumaMonitor(BaseModel):
    """A KumaMonitor custom resource as read from Kubernetes."""

    namespace: str
    name: str
    spec: KumaMonitorSpec

    @property
    def identity_key(self) -> str:
        return f"{self.namespace}/{self.name}"
