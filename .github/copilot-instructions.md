# Copilot Instructions — Vector

## Project overview
**Vector** — Validation & Endpoint Control for Tracking & Observability Resources

Vector is a Kubernetes-native controller that reads CRD-defined desired state and reconciles it into Uptime Kuma through the Uptime Kuma API.

This is a GitOps-first design:
- Flux owns the CRD and custom resources
- Kubernetes is the source of desired state
- the controller is read-only inside the cluster
- the controller writes only to Uptime Kuma
- no Kubernetes status writes in v1
- no finalizers in v1

## Primary goal
Create a small, reliable, idempotent controller that:
- watches KumaMonitor custom resources
- builds desired monitor definitions
- compares desired state with current state in Uptime Kuma
- creates, updates, and deletes monitors in Uptime Kuma
- only manages monitors that belong to this controller

## Runtime environment
All code runs inside containers. Never suggest or generate commands intended to be run directly on the host OS (e.g. `pip install`, `pytest`, `python main.py`). All execution — including tests — must happen inside a container or as part of a container build.

## Before push / before update
Before suggesting or applying any push or update, always validate:
- all tests pass inside the container (`docker build` followed by the test stage, or `docker compose run tests`)
- no regressions are introduced to existing tests
- linting passes (ruff or equivalent, inside the container)
- type checking passes (pyright/mypy, inside the container)
- no secrets, credentials, or sensitive values are present in any staged file
- YAML manifests in `deploy/` and `docs/` are valid and well-formed

Do not push or advise pushing if any of the above checks fail.

## Non-goals for v1
Do not add these unless explicitly requested:
- no web UI
- no separate dashboard
- no status subresource updates
- no finalizers
- no admission webhooks
- no leader-election complexity unless needed
- no operator framework unless it clearly improves maintainability
- no broad cluster write permissions

## Architecture
Source of truth:
- Git repository
- applied by Flux to Kubernetes

Flow:
1. Flux applies CRD and KumaMonitor resources
2. Vector reads KumaMonitor resources from Kubernetes
3. Vector reads current monitors from Uptime Kuma
4. Vector computes diff
5. Vector applies create/update/delete actions in Uptime Kuma
6. Vector persists reconciliation decisions and endpoint state to SQLite

## Security model
Kubernetes permissions must be minimal:
- get
- list
- watch

The controller must not require write access to Kubernetes for v1.

The controller does require write access to Uptime Kuma because reconciliation changes are applied there.

Treat Uptime Kuma credentials as secrets and never hardcode them.

## CRD direction
The CRD definition lives at [`docs/crd.yaml`](../docs/crd.yaml).
Example KumaMonitor resources covering all common use cases are in [`docs/example-kumamonitor.yaml`](../docs/example-kumamonitor.yaml).

- apiVersion: monitoring.monitoring.example.com/v1alpha1
- kind: KumaMonitor

Supported monitor types: `http`, `keyword`, `json-query`, `grpc-keyword`, `ping`, `port` (TCP), `dns`, `push`, `docker`, `mqtt`, `kafka-producer`, `postgres`, `mysql`, `sqlserver`, `mongodb`, `redis`, `radius`, `real-browser`, `steam`, `gamedig`, `tailscale-ping`, `group`.

Core spec fields (full schema in `docs/crd.yaml`):

| Field | Required | Type | Description |
|---|---|---|---|
| `name` | yes | string | Display name in Uptime Kuma |
| `type` | yes | string | Monitor type (see list above) |
| `url` | no* | string | Target URL or address (*required for http/keyword/ping/port/dns/mqtt) |
| `description` | no | string | Optional description |
| `interval` | no | integer | Check interval in seconds (default: 60) |
| `timeout` | no | integer | Timeout in seconds (default: 30) |
| `retry_interval` | no | integer | Retry interval in seconds (default: 60) |
| `resend_interval` | no | integer | Resend notification after N failures (default: 0 = off) |
| `retries` | no | integer | Retries before marking down (default: 1) |
| `enabled` | no | boolean | Active state (default: true) |
| `maintenance` | no | boolean | Maintenance mode (default: false) |
| `upside_down` | no | boolean | Flip UP/DOWN logic (default: false) |
| `expiry_notification` | no | boolean | Notify on TLS cert expiry (default: false) |
| `method` | no | string | HTTP method: GET POST PUT PATCH DELETE HEAD (default: GET) |
| `headers` | no | map | Additional HTTP request headers |
| `body` | no | string | HTTP request body for POST/PUT/PATCH |
| `http_body_encoding` | no | string | Body encoding: json, form, xml |
| `max_redirects` | no | integer | Max HTTP redirects (default: 10) |
| `accepted_statuscodes` | no | list | Accepted status codes, e.g. `["200-299"]` |
| `ignore_tls` | no | boolean | Ignore TLS errors (default: false) |
| `cache_bust` | no | boolean | Append cache-buster to URL (default: false) |
| `ip_family` | no | string | ipv4, ipv6, or auto |
| `auth_method` | no | string | Auth type: basic, ntlm, mtls, oauth2-cc |
| `basic_auth_user` | no | string | Username for basic/NTLM auth |
| `basic_auth_pass` | no | string | Password for basic/NTLM auth |
| `auth_domain` | no | string | Windows domain for NTLM |
| `auth_workstation` | no | string | Workstation for NTLM |
| `oauth_client_id` | no | string | OAuth2 client ID |
| `oauth_client_secret` | no | string | OAuth2 client secret |
| `oauth_token_url` | no | string | OAuth2 token endpoint |
| `oauth_scopes` | no | string | OAuth2 scopes |
| `oauth_audience` | no | string | OAuth2 audience |
| `oauth_auth_method` | no | string | client_secret_basic or client_secret_post |
| `tls_cert` | no | string | PEM client cert for mTLS |
| `tls_key` | no | string | PEM client key for mTLS |
| `tls_ca` | no | string | PEM CA cert to trust |
| `keyword` | no | string | Keyword to match in response (keyword/grpc-keyword) |
| `invert_keyword` | no | boolean | UP when keyword is absent (default: false) |
| `json_path` | no | string | JSONPath expression (json-query) |
| `json_path_operator` | no | string | contains, not_contains, equals, not_equals, less_than, greater_than |
| `expected_value` | no | string | Expected value for json_path comparison |
| `hostname` | no | string | Hostname/IP for ping/port/dns/radius |
| `port` | no | integer | Port for TCP/DNS/RADIUS monitors |
| `packet_size` | no | integer | ICMP packet size in bytes (default: 56) |
| `dns_resolve_type` | no | string | DNS record type: A AAAA CAA CNAME MX NS PTR SOA SRV TXT (default: A) |
| `dns_resolve_server` | no | string | DNS resolver address (default: 1.1.1.1) |
| `docker_container` | no | string | Container name/ID for docker monitor |
| `docker_host` | no | string | Docker Host name configured in Uptime Kuma |
| `mqtt_topic` | no | string | MQTT topic to subscribe to |
| `mqtt_username` | no | string | MQTT username |
| `mqtt_password` | no | string | MQTT password |
| `mqtt_success_message` | no | string | Expected MQTT payload |
| `database_connection_string` | no | string | DB connection string (postgres/mysql/sqlserver/mongodb/redis) |
| `database_query` | no | string | SQL query to execute |
| `kafka_producer_brokers` | no | list | Kafka broker addresses |
| `kafka_producer_topic` | no | string | Kafka topic |
| `kafka_producer_message` | no | string | Kafka message payload |
| `kafka_producer_ssl` | no | boolean | Kafka SSL (default: false) |
| `kafka_producer_allow_auto_topic_creation` | no | boolean | Auto-create topic (default: false) |
| `grpc_url` | no | string | gRPC server URL |
| `grpc_service_name` | no | string | gRPC service name |
| `grpc_method` | no | string | gRPC method name |
| `grpc_body` | no | string | gRPC request body JSON |
| `grpc_metadata` | no | string | gRPC metadata headers JSON |
| `grpc_protobuf` | no | string | Protobuf definition |
| `grpc_enable_tls` | no | boolean | gRPC TLS (default: false) |
| `radius_username` | no | string | RADIUS username |
| `radius_password` | no | string | RADIUS password |
| `radius_secret` | no | string | RADIUS shared secret |
| `radius_called_station_id` | no | string | RADIUS Called-Station-Id |
| `radius_calling_station_id` | no | string | RADIUS Calling-Station-Id |
| `group` | no | string | Parent group monitor name in Uptime Kuma |
| `parent_name` | no | string | Alias for `group` |
| `tags` | no | list | Tags to attach in Uptime Kuma |
| `notification_names` | no | list | Notification channel names to attach |

Keep the spec extensible and avoid locking the design too early.

## Ownership model
The controller must only modify monitors that it manages.

Always implement a clear ownership marker in Uptime Kuma-managed resources.
Use a deterministic tag, name prefix, description marker, or another reliable ownership mechanism.
Do not modify unrelated monitors created manually by users.

Preferred approach:
- add a dedicated management tag such as `managed-by=vector`
- also keep a deterministic external key derived from namespace/name

## Deterministic identity
Every KumaMonitor must map deterministically to one Uptime Kuma monitor.

Preferred identity:
- external key = `<namespace>/<name>`

Do not rely only on display name for matching.
Display names can change.
Use a stable ownership marker or metadata strategy for correlation.

## Reconciliation rules
The controller must be idempotent.

Rules:
- create monitor if desired resource exists and no managed Kuma monitor matches it
- update monitor if desired state differs from current managed Kuma monitor
- delete managed Kuma monitor if it no longer exists in Kubernetes desired state
- never delete monitors that are not explicitly owned by this controller

A full reconciliation loop is preferred in v1.
Do not depend on local cache or persistent state for correctness.

## Deletion behavior
Because v1 does not use finalizers:
- if a KumaMonitor disappears from Kubernetes, the controller detects that during reconciliation
- the matching managed monitor in Uptime Kuma is then deleted

This is acceptable.
Do not try to simulate finalizers in Kubernetes.

## State management and SQLite
Vector uses SQLite as a required operational layer for two purposes:
- **Endpoint state cache** — stores the last-known reconciled state of each managed monitor, used to detect drift without a full re-diff on every cycle
- **Reconciliation trace log** — records every create, update, and delete action with timestamp, namespace, name, monitor ID, and outcome for operational visibility and audit

SQLite is a supporting layer, not the source of truth.
Rules:
- Kubernetes + Uptime Kuma remain the authoritative sources of truth
- the controller must recover fully if the SQLite file is deleted — it will re-populate on the next reconcile cycle
- use `VECTOR_SQLITE_PATH` to configure the file location (default: `/data/vector.db`)
- always wrap SQLite access in `store.py` — do not scatter DB calls across the codebase
- on startup, run migrations to ensure the schema is up to date

## Coding style
This project follows the **SOLID** design principles:

- **S — Single Responsibility**: every class and module has one clearly defined reason to change. The Kubernetes reader, Kuma client, diff engine, reconciler, and SQLite store are each a separate responsibility in a separate file.
- **O — Open/Closed**: extend behaviour by adding new types or implementations, not by modifying existing logic. Monitor type mappings and ownership strategies should be extensible without editing the reconciler core.
- **L — Liskov Substitution**: concrete implementations (e.g. the real Kuma client and a test double) must be substitutable without changing the calling code.
- **I — Interface Segregation**: keep interfaces narrow. The reconciler depends only on what it needs (a reader, a writer, a store) — not on the full API surface of any dependency.
- **D — Dependency Inversion**: high-level modules (reconciler) depend on abstractions, not on concrete implementations. Inject clients and stores via constructor arguments or function parameters so they can be replaced in tests.

General expectations:
- favor simplicity over abstraction — SOLID guides design, but do not introduce complexity that is not yet needed
- keep functions small and obvious
- prioritize readability and testability
- use explicit names
- avoid magic behavior
- avoid hidden side effects
- fail loudly and clearly
- prefer standard library where reasonable

For Python:
- target a modern Python version (3.11+)
- use type hints throughout
- use `Protocol` or abstract base classes to define interfaces when multiple implementations are expected (e.g. KumaClientProtocol, StoreProtocol)
- use dataclasses or pydantic models where useful, but do not overengineer
- keep API models separate from Kubernetes models
- use structured logging
- handle retries explicitly and conservatively

## Logging
Logs must be human-readable and operationally useful.

Every reconcile cycle should clearly log:
- start/end of reconciliation
- number of resources found in Kubernetes
- number of monitors found in Uptime Kuma
- creates
- updates
- deletes
- skipped unmanaged monitors
- errors with context

Use structured fields where practical:
- namespace
- name
- monitor_id
- action
- reason

Never log secrets.

## Error handling
Handle failures predictably:
- invalid resource spec should not crash the controller
- one failed resource must not stop reconciliation of others
- Uptime Kuma API errors should be retried carefully when safe
- authentication failures should be surfaced clearly
- network failures should be logged with enough context to debug

Prefer partial success over full failure.

## Testing expectations
Add tests early.

Minimum tests:
- spec to desired monitor conversion
- ownership matching logic
- diff logic
- create/update/delete decision logic
- unmanaged monitor protection
- deletion of no-longer-desired managed monitors

Where possible:
- keep reconciliation logic pure and unit-testable
- isolate Uptime Kuma API access behind an interface
- mock API interactions cleanly

For SQLite:
- test that the controller recovers correctly if the SQLite file is missing or corrupt
- test cache population and cache-hit paths separately
- test that trace records are written for create, update, and delete actions

## File structure guidance
Prefer a layout similar to:

- src/
  - main.py
  - config.py
  - logging_setup.py
  - models/
    - crd.py
    - kuma.py
    - desired.py
  - services/
    - kubernetes_client.py
    - kuma_client.py
    - reconciler.py
    - diff.py
    - ownership.py
    - store.py          ← SQLite state cache and trace log
  - utils/
    - hashing.py
    - retry.py
- tests/
- deploy/
  - crd.yaml
  - rbac.yaml
  - deployment.yaml
  - secret-example.yaml

Keep the separation between:
- reading Kubernetes
- representing desired state
- talking to Uptime Kuma
- diffing
- reconciliation orchestration
- SQLite state cache and trace log (store.py)

## Kubernetes assumptions
Assume:
- deployment runs in-cluster
- RBAC is minimal and read-only for the CRD resources
- configuration is supplied through environment variables and Kubernetes Secrets
- one namespace or many namespaces may be supported later, but do not hardcode one too early

## Uptime Kuma assumptions
Assume the API may be imperfect or evolve.
Wrap API interactions behind a dedicated client layer.
Do not spread direct API calls across the codebase.

Normalize monitor payloads before diffing so comparison is stable.

## Diffing strategy
Before comparing desired and current monitor definitions:
- normalize values
- remove non-semantic fields
- sort unordered collections when needed
- compare only fields owned by this controller

Do not trigger updates because of irrelevant API-returned noise.

## Configuration reference
All configuration through environment variables:

| Variable | Required | Description |
|---|---|---|
| `KUMA_URL` | yes | Base URL for the Uptime Kuma instance |
| `KUMA_USERNAME` | yes | Uptime Kuma login username |
| `KUMA_PASSWORD` | yes | Uptime Kuma login password (use a Secret) |
| `RECONCILE_INTERVAL` | no | Seconds between full reconcile cycles (default: 60) |
| `VECTOR_SQLITE_PATH` | no | Path to SQLite file (default: /data/vector.db) |
| `LOG_LEVEL` | no | Logging level (default: INFO) |

Never hardcode credentials. Never log credential values.

## Prompting behavior for Copilot
When generating code for this project:
- always produce complete files unless asked otherwise
- do not leave TODO placeholders for core logic
- prefer working implementations over stubs
- include imports
- include type hints
- include error handling
- include concise comments only where they add real value
- do not add unnecessary frameworks
- do not add Kubernetes write logic in v1
- do not add status/finalizer logic in v1
- always implement store.py — SQLite is required, not optional

## First implementation target
Copilot should help implement in this order:

1. internal models for KumaMonitor and desired Uptime Kuma monitor
2. Kubernetes reader for KumaMonitor resources
3. Uptime Kuma API client wrapper
4. ownership marker strategy
5. diff engine
6. reconciler
7. SQLite store (store.py) — state cache and trace log
8. command-line entrypoint
9. deployment manifests
10. unit tests

## Definition of done for v1
v1 is done when:
- Flux can apply CRD and KumaMonitor resources
- Vector can read those resources with get/list/watch permissions only
- Vector can create, update, and delete owned monitors in Uptime Kuma
- Vector does not touch unmanaged monitors
- reconciliation is idempotent
- SQLite store records endpoint state and reconciliation traces
- the controller recovers correctly if the SQLite file is deleted and starts fresh
