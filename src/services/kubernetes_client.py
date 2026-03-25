from __future__ import annotations

import logging
from typing import Protocol

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from pydantic import ValidationError

from src.models.crd import KumaMonitor, KumaMonitorSpec

logger = logging.getLogger(__name__)

CRD_GROUP = "vector.beejeex.github.io"
CRD_VERSION = "v1alpha1"
CRD_PLURAL = "kumamonitors"

# Verbs the controller requires on the KumaMonitor CRD.
_REQUIRED_VERBS = ("get", "list", "watch")


class KubernetesClientProtocol(Protocol):
    def list_monitors(self) -> list[KumaMonitor]: ...


class KubernetesClient:
    """Reads KumaMonitor CRDs from Kubernetes using get/list/watch permissions only."""

    def __init__(self) -> None:
        try:
            config.load_incluster_config()
            logger.debug("Loaded in-cluster Kubernetes config")
        except config.ConfigException:
            config.load_kube_config()
            logger.debug("Loaded local kubeconfig (development)")
        self._api = client.CustomObjectsApi()
        self._authz_api = client.AuthorizationV1Api()

    def check_permissions(self) -> bool:
        """
        Use SelfSubjectAccessReview to verify the controller has the required
        verbs on the KumaMonitor CRD. Logs a warning for each missing permission.
        Returns True if all required permissions are present, False otherwise.
        """
        all_ok = True
        for verb in _REQUIRED_VERBS:
            review = client.V1SelfSubjectAccessReview(
                spec=client.V1SelfSubjectAccessReviewSpec(
                    resource_attributes=client.V1ResourceAttributes(
                        group=CRD_GROUP,
                        resource=CRD_PLURAL,
                        verb=verb,
                    )
                )
            )
            try:
                result = self._authz_api.create_self_subject_access_review(review)
                allowed = result.status.allowed if result.status else False
            except ApiException as exc:
                logger.warning(
                    "Could not check permission — SelfSubjectAccessReview failed",
                    extra={"verb": verb, "status": exc.status, "reason": exc.reason},
                )
                allowed = False

            if allowed:
                logger.debug("Permission check passed", extra={"verb": verb, "resource": CRD_PLURAL})
            else:
                logger.warning(
                    "Permission check FAILED — controller may not work correctly",
                    extra={"verb": verb, "resource": CRD_PLURAL, "group": CRD_GROUP},
                )
                all_ok = False

        return all_ok

    def list_monitors(self) -> list[KumaMonitor]:
        monitors: list[KumaMonitor] = []
        try:
            result = self._api.list_cluster_custom_object(
                group=CRD_GROUP,
                version=CRD_VERSION,
                plural=CRD_PLURAL,
            )
        except ApiException as exc:
            logger.error(
                "Failed to list KumaMonitors from Kubernetes",
                extra={"status": exc.status, "reason": exc.reason},
            )
            return []

        for item in result.get("items", []):
            metadata = item.get("metadata", {})
            namespace = metadata.get("namespace", "default")
            name = metadata.get("name", "")
            spec_data = item.get("spec", {})
            try:
                spec = KumaMonitorSpec.model_validate(spec_data)
                monitors.append(KumaMonitor(namespace=namespace, name=name, spec=spec))
            except ValidationError as exc:
                logger.warning(
                    "Skipping KumaMonitor with invalid spec",
                    extra={"namespace": namespace, "name": name, "error": str(exc)},
                )

        logger.info("Listed KumaMonitors", extra={"count": len(monitors)})
        return monitors
