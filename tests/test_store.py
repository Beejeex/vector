"""Tests for the SQLite store."""
import os
import tempfile

import pytest

from src.services.store import SQLiteStore


@pytest.fixture
def store(tmp_path):
    return SQLiteStore(str(tmp_path / "test.db"))


class TestUpsertAndGetState:
    def test_upsert_then_get(self, store):
        store.upsert_state("default/svc", monitor_id=42, spec_hash="abc123")
        result = store.get_state("default/svc")
        assert result == (42, "abc123")

    def test_upsert_overwrites(self, store):
        store.upsert_state("default/svc", 1, "hash1")
        store.upsert_state("default/svc", 2, "hash2")
        result = store.get_state("default/svc")
        assert result == (2, "hash2")

    def test_get_missing_returns_none(self, store):
        assert store.get_state("default/nonexistent") is None

    def test_multiple_keys(self, store):
        store.upsert_state("ns/a", 1, "h1")
        store.upsert_state("ns/b", 2, "h2")
        assert store.get_state("ns/a") == (1, "h1")
        assert store.get_state("ns/b") == (2, "h2")


class TestDeleteState:
    def test_delete_removes_entry(self, store):
        store.upsert_state("default/svc", 1, "hash")
        store.delete_state("default/svc")
        assert store.get_state("default/svc") is None

    def test_delete_nonexistent_is_silent(self, store):
        # Should not raise
        store.delete_state("default/nonexistent")


class TestRecordTrace:
    def test_trace_written_create(self, store):
        store.record_trace("default", "svc", "create", "success", monitor_id=5)
        # No exception = success; verify by checking file exists
        import sqlite3
        with sqlite3.connect(store._path) as conn:
            rows = conn.execute("SELECT * FROM reconcile_trace").fetchall()
        assert len(rows) == 1
        assert rows[0][5] == "create"   # action
        assert rows[0][6] == "success"  # outcome

    def test_trace_written_error(self, store):
        store.record_trace("monitoring", "api", "update", "error", detail="timeout")
        import sqlite3
        with sqlite3.connect(store._path) as conn:
            rows = conn.execute("SELECT * FROM reconcile_trace").fetchall()
        assert rows[0][7] == "timeout"  # detail

    def test_multiple_traces_appended(self, store):
        store.record_trace("ns", "a", "create", "success", 1)
        store.record_trace("ns", "a", "update", "success", 1)
        store.record_trace("ns", "a", "delete", "success", 1)
        import sqlite3
        with sqlite3.connect(store._path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM reconcile_trace").fetchone()[0]
        assert count == 3


class TestRecovery:
    def test_recreates_schema_if_file_deleted(self, tmp_path):
        path = str(tmp_path / "vector.db")
        store = SQLiteStore(path)
        store.upsert_state("ns/a", 1, "h1")

        # Delete the file
        os.remove(path)

        # A new store object should reinitialize cleanly
        store2 = SQLiteStore(path)
        assert store2.get_state("ns/a") is None  # data gone, but no crash

    def test_operations_survive_schema_reinit(self, tmp_path):
        path = str(tmp_path / "vector.db")
        os.remove(path) if os.path.exists(path) else None
        store = SQLiteStore(path)
        store.upsert_state("ns/b", 7, "fresh")
        assert store.get_state("ns/b") == (7, "fresh")
