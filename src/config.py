from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Config:
    kuma_url: str
    kuma_username: str
    kuma_password: str
    reconcile_interval: int
    sqlite_path: str
    log_level: str


def load_config() -> Config:
    kuma_url = os.environ.get("KUMA_URL", "").strip()
    kuma_username = os.environ.get("KUMA_USERNAME", "").strip()
    kuma_password = os.environ.get("KUMA_PASSWORD", "").strip()

    missing = [
        k
        for k, v in {
            "KUMA_URL": kuma_url,
            "KUMA_USERNAME": kuma_username,
            "KUMA_PASSWORD": kuma_password,
        }.items()
        if not v
    ]
    if missing:
        raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")

    return Config(
        kuma_url=kuma_url,
        kuma_username=kuma_username,
        kuma_password=kuma_password,
        reconcile_interval=int(os.environ.get("RECONCILE_INTERVAL", "60")),
        sqlite_path=os.environ.get("VECTOR_SQLITE_PATH", "/data/vector.db"),
        log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    )
