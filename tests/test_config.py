"""Tests for src/config.py — auth mode validation and env var loading."""
from __future__ import annotations

import os
import pytest

from src.config import load_config


def _env(**kwargs: str) -> dict[str, str]:
    """Return a minimal valid env dict merged with caller-supplied overrides."""
    base = {"KUMA_URL": "http://kuma.example.com"}
    base.update(kwargs)
    return base


class TestLoadConfigRequiredFields:
    def test_missing_kuma_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KUMA_URL", raising=False)
        monkeypatch.delenv("KUMA_USERNAME", raising=False)
        monkeypatch.delenv("KUMA_PASSWORD", raising=False)
        monkeypatch.delenv("KUMA_API_TOKEN", raising=False)
        with pytest.raises(EnvironmentError, match="KUMA_URL"):
            load_config()

    def test_empty_kuma_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for key, val in _env(KUMA_URL="").items():
            monkeypatch.setenv(key, val)
        with pytest.raises(EnvironmentError, match="KUMA_URL"):
            load_config()


class TestLoadConfigAuthModes:
    def _set(self, monkeypatch: pytest.MonkeyPatch, **kwargs: str) -> None:
        # Clear all credential vars first
        for key in ("KUMA_USERNAME", "KUMA_PASSWORD", "KUMA_API_TOKEN"):
            monkeypatch.delenv(key, raising=False)
        for key, val in _env(**kwargs).items():
            monkeypatch.setenv(key, val)

    def test_username_and_password_produces_valid_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._set(monkeypatch, KUMA_USERNAME="admin", KUMA_PASSWORD="secret")
        cfg = load_config()
        assert cfg.kuma_username == "admin"
        assert cfg.kuma_password == "secret"
        assert cfg.kuma_api_token is None

    def test_api_token_produces_valid_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._set(monkeypatch, KUMA_API_TOKEN="uk1_abc123")
        cfg = load_config()
        assert cfg.kuma_api_token == "uk1_abc123"
        assert cfg.kuma_username is None
        assert cfg.kuma_password is None

    def test_no_credentials_is_valid_auth_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._set(monkeypatch)  # no credentials at all
        cfg = load_config()
        assert cfg.kuma_username is None
        assert cfg.kuma_password is None
        assert cfg.kuma_api_token is None

    def test_username_without_password_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._set(monkeypatch, KUMA_USERNAME="admin")
        with pytest.raises(EnvironmentError, match="KUMA_USERNAME and KUMA_PASSWORD"):
            load_config()

    def test_password_without_username_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._set(monkeypatch, KUMA_PASSWORD="secret")
        with pytest.raises(EnvironmentError, match="KUMA_USERNAME and KUMA_PASSWORD"):
            load_config()

    def test_api_token_and_username_password_both_set_is_allowed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Both token and username/password may be set; client decides which to use.
        self._set(
            monkeypatch,
            KUMA_USERNAME="admin",
            KUMA_PASSWORD="secret",
            KUMA_API_TOKEN="uk1_abc123",
        )
        cfg = load_config()
        assert cfg.kuma_api_token == "uk1_abc123"
        assert cfg.kuma_username == "admin"
        assert cfg.kuma_password == "secret"


class TestLoadConfigOptionalFields:
    def _set(self, monkeypatch: pytest.MonkeyPatch, **kwargs: str) -> None:
        for key in ("KUMA_USERNAME", "KUMA_PASSWORD", "KUMA_API_TOKEN",
                    "RECONCILE_INTERVAL", "VECTOR_SQLITE_PATH", "LOG_LEVEL"):
            monkeypatch.delenv(key, raising=False)
        for key, val in _env(**kwargs).items():
            monkeypatch.setenv(key, val)

    def test_defaults_when_optional_vars_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._set(monkeypatch)
        cfg = load_config()
        assert cfg.reconcile_interval == 60
        assert cfg.sqlite_path == "/data/vector.db"
        assert cfg.log_level == "INFO"

    def test_custom_reconcile_interval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set(monkeypatch, RECONCILE_INTERVAL="120")
        cfg = load_config()
        assert cfg.reconcile_interval == 120

    def test_custom_sqlite_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set(monkeypatch, VECTOR_SQLITE_PATH="/tmp/test.db")
        cfg = load_config()
        assert cfg.sqlite_path == "/tmp/test.db"

    def test_log_level_uppercased(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set(monkeypatch, LOG_LEVEL="debug")
        cfg = load_config()
        assert cfg.log_level == "DEBUG"

    def test_whitespace_stripped_from_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set(monkeypatch, KUMA_URL="  http://kuma.example.com  ")
        cfg = load_config()
        assert cfg.kuma_url == "http://kuma.example.com"


class TestLoadConfigDiscovery:
    def _set(self, monkeypatch: pytest.MonkeyPatch, **kwargs: str) -> None:
        for key in (
            "KUMA_USERNAME", "KUMA_PASSWORD", "KUMA_API_TOKEN",
            "DISCOVERY_ENABLED", "DISCOVERY_INGRESS", "DISCOVERY_SERVICES",
            "DISCOVERY_PROBES", "DISCOVERY_DATABASES", "DISCOVERY_INGRESS_DEFAULT_SCHEME",
        ):
            monkeypatch.delenv(key, raising=False)
        for key, val in _env(**kwargs).items():
            monkeypatch.setenv(key, val)

    def test_discovery_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set(monkeypatch)
        cfg = load_config()
        assert cfg.discovery_enabled is False

    def test_discovery_enabled_by_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set(monkeypatch, DISCOVERY_ENABLED="true")
        cfg = load_config()
        assert cfg.discovery_enabled is True

    def test_discovery_enabled_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set(monkeypatch, DISCOVERY_ENABLED="TRUE")
        cfg = load_config()
        assert cfg.discovery_enabled is True

    def test_all_sources_enabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set(monkeypatch, DISCOVERY_ENABLED="true")
        cfg = load_config()
        assert cfg.discovery_ingress is True
        assert cfg.discovery_services is True
        assert cfg.discovery_probes is True
        assert cfg.discovery_databases is True

    def test_individual_source_can_be_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set(monkeypatch, DISCOVERY_ENABLED="true", DISCOVERY_INGRESS="false")
        cfg = load_config()
        assert cfg.discovery_ingress is False
        assert cfg.discovery_services is True

    def test_all_sources_can_be_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set(
            monkeypatch,
            DISCOVERY_ENABLED="true",
            DISCOVERY_INGRESS="false",
            DISCOVERY_SERVICES="false",
            DISCOVERY_PROBES="false",
            DISCOVERY_DATABASES="false",
        )
        cfg = load_config()
        assert cfg.discovery_ingress is False
        assert cfg.discovery_services is False
        assert cfg.discovery_probes is False
        assert cfg.discovery_databases is False

    def test_ingress_default_scheme_is_https(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set(monkeypatch)
        cfg = load_config()
        assert cfg.discovery_ingress_default_scheme == "https"

    def test_ingress_default_scheme_can_be_set_to_http(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set(monkeypatch, DISCOVERY_INGRESS_DEFAULT_SCHEME="http")
        cfg = load_config()
        assert cfg.discovery_ingress_default_scheme == "http"
