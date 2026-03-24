from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS monitor_state (
    key         TEXT PRIMARY KEY,
    monitor_id  INTEGER NOT NULL,
    spec_hash   TEXT NOT NULL,
    synced_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reconcile_trace (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    namespace   TEXT NOT NULL,
    name        TEXT NOT NULL,
    monitor_id  INTEGER,
    action      TEXT NOT NULL,
    outcome     TEXT NOT NULL,
    detail      TEXT
);
"""


class StoreProtocol(Protocol):
    def upsert_state(self, key: str, monitor_id: int, spec_hash: str) -> None: ...
    def delete_state(self, key: str) -> None: ...
    def get_state(self, key: str) -> Optional[tuple[int, str]]: ...
    def record_trace(
        self,
        namespace: str,
        name: str,
        action: str,
        outcome: str,
        monitor_id: Optional[int] = None,
        detail: Optional[str] = None,
    ) -> None: ...


class SQLiteStore:
    """
    SQLite-backed state cache and reconciliation trace log.

    Two tables:
    - monitor_state  — last-known reconciled state per identity key
    - reconcile_trace — append-only audit log of every action taken

    The controller must recover fully if this file is deleted.
    All DB errors are logged as warnings and do not interrupt reconciliation.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_schema(self) -> None:
        try:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
            logger.debug("SQLite schema ready", extra={"path": self._path})
        except sqlite3.Error as exc:
            logger.error(
                "Failed to initialize SQLite schema — store will be unavailable",
                extra={"path": self._path, "error": str(exc)},
            )

    def upsert_state(self, key: str, monitor_id: int, spec_hash: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO monitor_state (key, monitor_id, spec_hash, synced_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        monitor_id = excluded.monitor_id,
                        spec_hash  = excluded.spec_hash,
                        synced_at  = excluded.synced_at
                    """,
                    (key, monitor_id, spec_hash, _now()),
                )
        except sqlite3.Error as exc:
            logger.warning("Failed to upsert monitor state", extra={"key": key, "error": str(exc)})

    def delete_state(self, key: str) -> None:
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM monitor_state WHERE key = ?", (key,))
        except sqlite3.Error as exc:
            logger.warning("Failed to delete monitor state", extra={"key": key, "error": str(exc)})

    def get_state(self, key: str) -> Optional[tuple[int, str]]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT monitor_id, spec_hash FROM monitor_state WHERE key = ?", (key,)
                ).fetchone()
                if row:
                    return row["monitor_id"], row["spec_hash"]
        except sqlite3.Error as exc:
            logger.warning("Failed to get monitor state", extra={"key": key, "error": str(exc)})
        return None

    def record_trace(
        self,
        namespace: str,
        name: str,
        action: str,
        outcome: str,
        monitor_id: Optional[int] = None,
        detail: Optional[str] = None,
    ) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO reconcile_trace
                        (timestamp, namespace, name, monitor_id, action, outcome, detail)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (_now(), namespace, name, monitor_id, action, outcome, detail),
                )
        except sqlite3.Error as exc:
            logger.warning(
                "Failed to write reconcile trace",
                extra={"namespace": namespace, "monitor_name": name, "error": str(exc)},
            )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
