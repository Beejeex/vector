from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Optional


@dataclass(frozen=True)
class Config:
    kuma_url: str
    kuma_username: Optional[str]
    kuma_password: Optional[str]
    kuma_api_token: Optional[str]
    reconcile_interval: int
    sqlite_path: str
    log_level: str


def load_config() -> Config:
    kuma_url = os.environ.get("KUMA_URL", "").strip()
    if not kuma_url:
        raise EnvironmentError("Missing required environment variable: KUMA_URL")

    kuma_username = os.environ.get("KUMA_USERNAME", "").strip() or None
    kuma_password = os.environ.get("KUMA_PASSWORD", "").strip() or None
    kuma_api_token = os.environ.get("KUMA_API_TOKEN", "").strip() or None

    # Partial credentials (one of username/password set but not both) is a mistake.
    if bool(kuma_username) != bool(kuma_password):
        raise EnvironmentError(
            "KUMA_USERNAME and KUMA_PASSWORD must both be set, or both omitted. "
            "To use an API key set KUMA_API_TOKEN instead. "
            "To connect to an instance with auth disabled, omit all credential variables."
        )

    return Config(
        kuma_url=kuma_url,
        kuma_username=kuma_username,
        kuma_password=kuma_password,
        kuma_api_token=kuma_api_token,
        reconcile_interval=int(os.environ.get("RECONCILE_INTERVAL", "60")),
        sqlite_path=os.environ.get("VECTOR_SQLITE_PATH", "/data/vector.db"),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )
