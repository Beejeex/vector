from __future__ import annotations

import logging
import sys
import time
from typing import Optional

from src.config import load_config
from src.logging_setup import setup_logging
from src.services.discovery.runner import DiscoveryRunner, DiscoveryRunnerProtocol
from src.services.kubernetes_client import KubernetesClient
from src.services.kuma_client import UptimeKumaClient
from src.services.reconciler import Reconciler
from src.services.store import SQLiteStore

logger = logging.getLogger(__name__)


def _auth_mode(cfg) -> str:  # type: ignore[no-untyped-def]
    if cfg.kuma_api_token:
        return "api-token"
    if cfg.kuma_username:
        return "username-password"
    return "auth-disabled"


def _build_discovery_runner(cfg) -> Optional[DiscoveryRunnerProtocol]:  # type: ignore[no-untyped-def]
    if not cfg.discovery_enabled:
        logger.info("Discovery disabled")
        return None

    from src.services.discovery.database import DatabasePortDiscovery
    from src.services.discovery.ingress import IngressDiscovery
    from src.services.discovery.k8s import DiscoveryK8sClient
    from src.services.discovery.probe import ProbeDiscovery
    from src.services.discovery.service import ServicePortDiscovery

    dk8s = DiscoveryK8sClient()
    sources = []
    if cfg.discovery_ingress:
        sources.append(IngressDiscovery(dk8s, default_scheme=cfg.discovery_ingress_default_scheme))
    if cfg.discovery_services:
        sources.append(ServicePortDiscovery(dk8s))
    if cfg.discovery_probes:
        sources.append(ProbeDiscovery(dk8s))
    if cfg.discovery_databases:
        sources.append(DatabasePortDiscovery(dk8s))

    logger.info(
        "Discovery enabled",
        extra={
            "ingress": cfg.discovery_ingress,
            "services": cfg.discovery_services,
            "probes": cfg.discovery_probes,
            "databases": cfg.discovery_databases,
            "active_sources": len(sources),
        },
    )
    return DiscoveryRunner(dk8s, sources)


def main() -> None:
    try:
        cfg = load_config()
    except EnvironmentError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)

    setup_logging(cfg.log_level)
    logger.info(
        "Vector starting",
        extra={
            "kuma_url": cfg.kuma_url,
            "auth_mode": _auth_mode(cfg),
            "reconcile_interval": cfg.reconcile_interval,
            "sqlite_path": cfg.sqlite_path,
            "log_level": cfg.log_level,
            "discovery_enabled": cfg.discovery_enabled,
        },
    )

    k8s = KubernetesClient()

    logger.info("Checking Kubernetes RBAC permissions")
    if not k8s.check_permissions():
        logger.error(
            "One or more required Kubernetes permissions are missing. "
            "Apply deploy/rbac.yaml and ensure the ServiceAccount is correct. "
            "Vector will continue but reconciliation may fail."
        )
    else:
        logger.info("Kubernetes RBAC permissions OK")

    store = SQLiteStore(cfg.sqlite_path)
    discovery = _build_discovery_runner(cfg)

    while True:
        kuma = UptimeKumaClient(cfg.kuma_url, cfg.kuma_username, cfg.kuma_password, cfg.kuma_api_token)
        try:
            kuma.connect()
            Reconciler(k8s=k8s, kuma=kuma, store=store, discovery=discovery).run_once()
        except Exception as exc:
            logger.error(
                "Reconciliation cycle failed",
                extra={"error": str(exc)},
                exc_info=True,
            )
        finally:
            kuma.disconnect()

        # Touch the liveness file so Kubernetes knows the loop is still running.
        try:
            open("/tmp/healthy", "w").close()
        except OSError:
            pass

        logger.debug("Sleeping", extra={"seconds": cfg.reconcile_interval})
        time.sleep(cfg.reconcile_interval)


if __name__ == "__main__":
    main()
