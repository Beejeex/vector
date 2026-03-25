# Vector

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)

**V**alidation & **E**ndpoint **C**ontrol for **T**racking & **O**bservability **R**esources

Vector is a Kubernetes-native controller that reads `KumaMonitor` custom resources and reconciles them into [Uptime Kuma](https://uptime-kuma.pet/). It is designed for GitOps workflows — Flux owns the desired state and Vector applies it.

## How it works

1. Flux applies the CRD and `KumaMonitor` resources from Git
2. Vector reads `KumaMonitor` resources from Kubernetes (read-only)
3. Vector reads current monitors from Uptime Kuma
4. Vector diffs desired vs current state
5. Vector creates, updates, or deletes owned monitors in Uptime Kuma
6. All reconciliation actions are recorded in a local SQLite database

Vector only touches monitors tagged `managed-by=vector`. Manually created monitors are never modified or deleted.

## Quick start

### 1. Apply the CRD and RBAC

The CRD registers the `KumaMonitor` resource kind. The RBAC manifest creates the `vector` namespace, `ServiceAccount`, `ClusterRole`, and `ClusterRoleBinding`. Reference: [`docs/rbac.yaml`](docs/rbac.yaml).

### 2. Create the credentials secret

Copy [`docs/secret.yaml`](docs/secret.yaml) to `deploy/secret.yaml`, fill in your Uptime Kuma URL and credentials, then apply it. The deployment reads `KUMA_URL`, `KUMA_USERNAME`, and `KUMA_PASSWORD` from this secret — the pod will not start without it.

> **Security**: Never commit `deploy/secret.yaml` to version control. Use [Sealed Secrets](https://github.com/bitnami-labs/sealed-secrets) or [External Secrets Operator](https://external-secrets.io/) for GitOps-safe secret management.

### 3. Deploy Vector

Update the `image` field in [`docs/deployment.yaml`](docs/deployment.yaml) to point to your registry, copy it to `deploy/deployment.yaml`, then apply.

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

\* Credentials are optional when Uptime Kuma has authentication disabled. When auth is enabled, provide either `KUMA_API_TOKEN` **or** both `KUMA_USERNAME` and `KUMA_PASSWORD`. API keys are not available when auth is disabled.

## Monitor types

`http`, `keyword`, `json-query`, `grpc-keyword`, `ping`, `port`, `dns`, `push`, `docker`, `mqtt`, `kafka-producer`, `postgres`, `mysql`, `sqlserver`, `mongodb`, `redis`, `radius`, `real-browser`, `steam`, `gamedig`, `tailscale-ping`, `group`

See [`docs/crd.yaml`](docs/crd.yaml) for the full field reference and [`docs/example-kumamonitor.yaml`](docs/example-kumamonitor.yaml) for working examples of every type.

## Ownership model

Every monitor Vector creates in Uptime Kuma is tagged `managed-by=vector`. During reconciliation Vector compares the set of managed monitors against the desired state from Kubernetes. Monitors that no longer have a matching `KumaMonitor` resource are deleted. Monitors not carrying the tag are never touched.

Monitor identity is the Kubernetes `<namespace>/<name>` pair, making it stable across display-name renames.

## License

CC BY-NC 4.0 — free for personal and non-commercial use. See [LICENSE](LICENSE).
