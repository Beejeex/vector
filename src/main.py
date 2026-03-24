from __future__ import annotations

import logging
import sys
import time

from src.config import load_config
from src.logging_setup import setup_logging
from src.services.kubernetes_client import KubernetesClient
from src.services.kuma_client import UptimeKumaClient
from src.services.reconciler import Reconciler
from src.services.store import SQLiteStore

logger = logging.getLogger(__name__)


def main() -> None:
    try:
        cfg = load_config()
    except EnvironmentError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)

    setup_logging(cfg.log_level)
    logger.info(
        "Vector starting",
        extra={"kuma_url": cfg.kuma_url, "reconcile_interval": cfg.reconcile_interval},
    )

    k8s = KubernetesClient()
    store = SQLiteStore(cfg.sqlite_path)

    while True:
        kuma = UptimeKumaClient(cfg.kuma_url, cfg.kuma_username, cfg.kuma_password)
        try:
            kuma.connect()
            Reconciler(k8s=k8s, kuma=kuma, store=store).run_once()
        except Exception as exc:
            logger.error("Reconciliation cycle failed", extra={"error": str(exc)})
        finally:
            kuma.disconnect()

        logger.debug("Sleeping", extra={"seconds": cfg.reconcile_interval})
        time.sleep(cfg.reconcile_interval)


if __name__ == "__main__":
    main()
