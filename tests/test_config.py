from __future__ import annotations

import pytest

from dms_mcp_server.config import Settings


def test_settings_use_local_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DMS_BRIDGE_URL",
        "DMS_BROKER_URL",
        "DMS_MCP_TIMEOUT_SECONDS",
        "DMS_MCP_MAX_DOCUMENT_BYTES",
        "DMS_MCP_CREDENTIAL_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.bridge_url == "http://127.0.0.1:8765"
    assert settings.broker_url == "http://127.0.0.1:8776"


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DMS_BRIDGE_URL", "http://127.0.0.1:9000/")
    monkeypatch.setenv("DMS_BROKER_URL", "http://127.0.0.1:9001/")
    monkeypatch.setenv("DMS_MCP_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("DMS_MCP_MAX_DOCUMENT_BYTES", "2048")
    monkeypatch.setenv("DMS_MCP_CREDENTIAL_ID", "company/dms")

    settings = Settings.from_env()

    assert settings.bridge_url == "http://127.0.0.1:9000"
    assert settings.broker_url == "http://127.0.0.1:9001"
    assert settings.timeout_seconds == 12.5
    assert settings.max_document_bytes == 2048
    assert settings.default_credential_id == "company/dms"
