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

### 1. Apply CRD and RBAC

```bash
kubectl apply -f deploy/crd.yaml
kubectl apply -f deploy/rbac.yaml
```

### 2. Create the credentials secret

```bash
cp deploy/secret-example.yaml deploy/secret.yaml
# Edit deploy/secret.yaml with your Uptime Kuma URL and credentials
kubectl apply -f deploy/secret.yaml
```

> **Security**: Never commit `deploy/secret.yaml` to version control. Use [Sealed Secrets](https://github.com/bitnami-labs/sealed-secrets) or [External Secrets Operator](https://external-secrets.io/) for GitOps-safe secret management.

### 3. Deploy Vector

```bash
# Update the image reference in deploy/deployment.yaml first
kubectl apply -f deploy/deployment.yaml
```

### 4. Apply monitors

```bash
kubectl apply -f docs/example-kumamonitor.yaml
```

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `KUMA_URL` | yes | — | Base URL of your Uptime Kuma instance |
| `KUMA_USERNAME` | yes | — | Uptime Kuma login username |
| `KUMA_PASSWORD` | yes | — | Uptime Kuma login password |
| `RECONCILE_INTERVAL` | no | `60` | Seconds between full reconcile cycles |
| `VECTOR_SQLITE_PATH` | no | `/data/vector.db` | Path to the SQLite state file |
| `LOG_LEVEL` | no | `INFO` | Logging level |

## Monitor types

`http`, `keyword`, `json-query`, `grpc-keyword`, `ping`, `port`, `dns`, `push`, `docker`, `mqtt`, `kafka-producer`, `postgres`, `mysql`, `sqlserver`, `mongodb`, `redis`, `radius`, `real-browser`, `steam`, `gamedig`, `tailscale-ping`, `group`

See [`docs/crd.yaml`](docs/crd.yaml) for the full field reference and [`docs/example-kumamonitor.yaml`](docs/example-kumamonitor.yaml) for working examples of every type.

## Ownership model

Every monitor Vector creates in Uptime Kuma is tagged `managed-by=vector`. During reconciliation Vector compares the set of managed monitors against the desired state from Kubernetes. Monitors that no longer have a matching `KumaMonitor` resource are deleted. Monitors not carrying the tag are never touched.

Monitor identity is the Kubernetes `<namespace>/<name>` pair, making it stable across display-name renames.

## Project layout

```
.github/
  copilot-instructions.md    # Copilot coding instructions
docs/
  crd.yaml                   # CRD with full field documentation
  example-kumamonitor.yaml   # Example KumaMonitor resources (all types)
deploy/
  crd.yaml                   # Deployable CRD
  rbac.yaml                  # ServiceAccount + ClusterRole
  deployment.yaml            # Vector controller Deployment
  secret-example.yaml        # Credentials secret template
src/
  main.py                    # Entrypoint and reconcile loop
  config.py                  # Environment-based configuration
  logging_setup.py           # Structured logging setup
  models/
    crd.py                   # KumaMonitor resource model
    kuma.py                  # Uptime Kuma API models
    desired.py               # Desired monitor state model
  services/
    kubernetes_client.py     # Reads KumaMonitor resources
    kuma_client.py           # Uptime Kuma API wrapper
    reconciler.py            # Reconciliation orchestration
    diff.py                  # Desired vs current diff engine
    ownership.py             # Ownership tag strategy
    store.py                 # SQLite state cache and trace log
  utils/
    hashing.py               # Deterministic identity hashing
    retry.py                 # Conservative retry helpers
tests/
  ...
```

## Building the image

```bash
docker build -t ghcr.io/beejeex/vector:latest .
docker push ghcr.io/beejeex/vector:latest
```

## Development

```bash
# install runtime + dev dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt

# run tests
pytest

# run with coverage
pytest --cov=src --cov-report=term-missing
```

## License

CC BY-NC 4.0 — free for personal and non-commercial use. See [LICENSE](LICENSE).
