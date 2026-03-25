# Vector

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)

**V**alidation & **E**ndpoint **C**ontrol for **T**racking & **O**bservability **R**esources

Vector is a Kubernetes-native controller that reads `KumaMonitor` custom resources and reconciles them into [Uptime Kuma](https://uptime-kuma.pet/). It is designed for GitOps workflows — Flux owns the desired state and Vector applies it.

## How it works

1. Flux applies the CRD and `KumaMonitor` resources from Git
2. Vector reads `KumaMonitor` resources from Kubernetes (read-only)
3. Vector optionally discovers additional monitors from Kubernetes resources (Ingress, Services, probes, database ports) in opted-in namespaces
4. Vector reads current monitors from Uptime Kuma
5. Vector diffs desired vs current state (CRD-defined + discovery-derived combined)
6. Vector creates, updates, or deletes owned monitors in Uptime Kuma
7. All reconciliation actions are recorded in a local SQLite database

Vector only touches monitors tagged `managed-by=vector`. Manually created monitors are never modified or deleted.

## Quick start

### 1. Apply the CRD and RBAC

The CRD registers the `KumaMonitor` resource kind. The RBAC manifest creates the `vector` namespace, `ServiceAccount`, `ClusterRole`, and `ClusterRoleBinding`. Reference: [`deploy/rbac.yaml`](deploy/rbac.yaml).

### 2. Create the credentials secret

Copy [`docs/secret.yaml`](docs/secret.yaml) to `deploy/secret.yaml`, fill in your Uptime Kuma URL and credentials, then apply it. The deployment reads `KUMA_URL`, `KUMA_USERNAME`, and `KUMA_PASSWORD` from this secret — the pod will not start without it.

> **Security**: Never commit `deploy/secret.yaml` to version control. Use [Sealed Secrets](https://github.com/bitnami-labs/sealed-secrets) or [External Secrets Operator](https://external-secrets.io/) for GitOps-safe secret management.

### 3. Deploy Vector

Update the `image` field in [`deploy/deployment.yaml`](deploy/deployment.yaml) to point to your registry, then apply.

### 4. Apply monitors

[`docs/example-kumamonitor.yaml`](docs/example-kumamonitor.yaml) contains working examples for every supported monitor type.

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `KUMA_URL` | yes | — | Base URL of your Uptime Kuma instance |
| `KUMA_USERNAME` | no* | — | Uptime Kuma login username |
| `KUMA_PASSWORD` | no* | — | Uptime Kuma login password |
| `KUMA_API_TOKEN` | no* | — | Uptime Kuma API key (alternative to username/password) |
| `RECONCILE_INTERVAL` | no | `60` | Seconds between full reconcile cycles |
| `VECTOR_SQLITE_PATH` | no | `/data/vector.db` | Path to the SQLite state file |
| `LOG_LEVEL` | no | `INFO` | Logging level |
| `DISCOVERY_ENABLED` | no | `false` | Master switch for auto-discovery |
| `DISCOVERY_INGRESS` | no | `true` | Discover monitors from Ingress rules (requires `DISCOVERY_ENABLED=true`) |
| `DISCOVERY_SERVICES` | no | `true` | Discover monitors from Service port heuristics (requires `DISCOVERY_ENABLED=true`) |
| `DISCOVERY_PROBES` | no | `true` | Discover monitors from liveness/readiness HTTP probes (requires `DISCOVERY_ENABLED=true`) |
| `DISCOVERY_DATABASES` | no | `true` | Discover monitors from well-known database ports (requires `DISCOVERY_ENABLED=true`) |
| `DISCOVERY_INGRESS_DEFAULT_SCHEME` | no | `https` | Default URL scheme for Ingress-discovered monitors when no `tls:` entry is present in the spec. Set to `http` if your ingress controller does not serve HTTPS externally. |

\* Credentials are optional when Uptime Kuma has authentication disabled. When auth is enabled, provide either `KUMA_API_TOKEN` **or** both `KUMA_USERNAME` and `KUMA_PASSWORD`. API keys are not available when auth is disabled.

## Monitor types

`http`, `keyword`, `json-query`, `grpc-keyword`, `ping`, `port`, `dns`, `push`, `docker`, `mqtt`, `kafka-producer`, `postgres`, `mysql`, `sqlserver`, `mongodb`, `redis`, `radius`, `real-browser`, `steam`, `gamedig`, `tailscale-ping`, `group`

See [`deploy/crd.yaml`](deploy/crd.yaml) for the full field reference and [`docs/example-kumamonitor.yaml`](docs/example-kumamonitor.yaml) for working examples of every type.

## Ownership model

Every monitor Vector creates in Uptime Kuma is tagged `managed-by=vector`. During reconciliation Vector compares the set of managed monitors against the desired state from Kubernetes. Monitors that no longer have a matching `KumaMonitor` resource are deleted. Monitors not carrying the tag are never touched.

Monitor identity is the Kubernetes `<namespace>/<name>` pair, making it stable across display-name renames.

Discovery-derived monitors use the same `managed-by=vector` ownership tag and a stable identity key in the form `discovered:<source>:<namespace>/<resource>/<detail>` (for example `discovered:ingress:production/my-app/app.example.com`). This makes them distinguishable from CRD-defined monitors and keeps their identity stable as long as the underlying Kubernetes resource name does not change.

## Discovery

When `DISCOVERY_ENABLED=true`, Vector scans opted-in namespaces for Kubernetes resources and automatically creates monitors in Uptime Kuma without requiring explicit `KumaMonitor` CRs.

### Opting in a namespace

Add the annotation to the namespace you want Vector to scan:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: production
  annotations:
    vector.beejeex.github.io/discover: "true"
```

Namespaces without this annotation are ignored by discovery. Flux can manage the annotation, keeping the opt-in fully GitOps-native.

### Grouping

Discovered monitors are placed in an Uptime Kuma group named after the namespace by default. Override the group name with an annotation on the namespace:

```yaml
annotations:
  vector.beejeex.github.io/discover: "true"
  vector.beejeex.github.io/group: "My Production Apps"
```

### Discovery sources

| Source | Kubernetes resource | Monitor type produced |
|---|---|---|
| **Ingress** | `networking.k8s.io/v1 Ingress` | `http` — one monitor per host |
| **Service ports** | `v1 Service` | `http` or `port` — port names matching `http`, `https`, `web`, `health`, `metrics`, or any name prefixed with `http-` / `https-` (e.g. `http-web`, `http-metrics`) |
| **Probes** | `apps/v1 Deployment` / `StatefulSet` | `http` — derived from liveness or readiness HTTP probes |
| **Database ports** | `v1 Service` | `port` (TCP) — well-known ports: 5432 (Postgres), 6379 (Redis), 3306 (MySQL), 27017 (MongoDB), 1433 (SQL Server) |

Each source can be individually disabled via its env var while keeping discovery globally on.

## License

CC BY-NC 4.0 — free for personal and non-commercial use. See [LICENSE](LICENSE).
