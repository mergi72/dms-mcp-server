from __future__ import annotations

import json
from pathlib import Path

import pytest

from dms_mcp_server.config import load_settings
from dms_mcp_server.paths import PROJECT_CONFIG_DIR, USER_CONFIG_DIR, _distributed_config_dir


def _write_config(directory: Path, name: str, payload: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(payload), encoding="utf-8")


def _base_config() -> dict:
    return {
        "bridge": {"url": "http://127.0.0.1:8765", "minimumVersion": "0.2.0"},
        "broker": {"url": "http://127.0.0.1:8776"},
        "server": {"host": "127.0.0.1", "port": 8781, "path": "/mcp"},
        "inspector": {"host": "127.0.0.1", "port": 8780},
        "runtime": {"timeoutSeconds": 30, "maxDocumentBytes": 1_048_576},
        "debug": {"enable": True, "path": "%APPDATA%\\DMS MCP\\logs"},
    }


def test_load_settings_reads_machine_json(tmp_path: Path) -> None:
    machine_dir = tmp_path / "machine"
    _write_config(machine_dir, "mcp.json", _base_config())

    settings = load_settings(machine_dir, tmp_path / "missing-user")

    assert settings.bridge_url == "http://127.0.0.1:8765"
    assert settings.broker_url == "http://127.0.0.1:8776"
    assert settings.server_host == "127.0.0.1"
    assert settings.server_port == 8781
    assert settings.server_path == "/mcp"
    assert settings.inspector_host == "127.0.0.1"
    assert settings.inspector_port == 8780
    assert settings.timeout_seconds == 30
    assert settings.max_document_bytes == 1_048_576
    assert settings.minimum_bridge_version == "0.2.0"
    assert settings.debug_enabled is True
    assert settings.debug_path.endswith("DMS MCP\\logs")


def test_debug_config_requires_boolean_enable(tmp_path: Path) -> None:
    machine_dir = tmp_path / "machine"
    config = _base_config()
    config["debug"]["enable"] = "true"
    _write_config(machine_dir, "mcp.json", config)

    with pytest.raises(ValueError, match="debug.enable"):
        load_settings(machine_dir, tmp_path / "missing-user")


def test_user_local_json_overrides_machine_config(tmp_path: Path) -> None:
    machine_dir = tmp_path / "machine"
    user_dir = tmp_path / "user"
    _write_config(machine_dir, "mcp.json", _base_config())
    _write_config(
        user_dir,
        "mcp.local.json",
        {"bridge": {"url": "http://127.0.0.1:9000"}, "runtime": {"timeoutSeconds": 12}},
    )

    settings = load_settings(machine_dir, user_dir)

    assert settings.bridge_url == "http://127.0.0.1:9000"
    assert settings.broker_url == "http://127.0.0.1:8776"
    assert settings.timeout_seconds == 12


def test_environment_overrides_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    machine_dir = tmp_path / "machine"
    _write_config(machine_dir, "mcp.json", _base_config())
    monkeypatch.setenv("DMS_BRIDGE_URL", "http://127.0.0.1:9100/")
    monkeypatch.setenv("DMS_MCP_MAX_DOCUMENT_BYTES", "2048")

    settings = load_settings(machine_dir, tmp_path / "missing-user")

    assert settings.bridge_url == "http://127.0.0.1:9100"
    assert settings.max_document_bytes == 2048


def test_missing_machine_config_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="mcp.json"):
        load_settings(tmp_path / "missing", tmp_path / "user")


def test_source_tree_uses_project_default_config() -> None:
    assert PROJECT_CONFIG_DIR.name == "config"
    assert (PROJECT_CONFIG_DIR / "mcp.json").is_file()
    assert _distributed_config_dir() == PROJECT_CONFIG_DIR


def test_default_user_config_is_below_appdata(monkeypatch: pytest.MonkeyPatch) -> None:
    appdata = Path.home() / "AppData" / "Roaming"
    monkeypatch.setenv("APPDATA", str(appdata))

    from dms_mcp_server.paths import _user_config_dir

    assert _user_config_dir() == appdata / "DMS MCP" / "config"
    if USER_CONFIG_DIR is not None:
        assert USER_CONFIG_DIR.name == "config"
