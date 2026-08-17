from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dms_mcp_server.paths import MACHINE_CONFIG_DIR, USER_CONFIG_DIR


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Configuration root must be a JSON object: {path}")
    return payload


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _required_string(section: dict[str, Any], key: str, location: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing non-empty configuration value: {location}.{key}")
    return value.strip()


def _positive_float(value: object, location: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{location} must be a number.") from exc
    if parsed <= 0:
        raise ValueError(f"{location} must be greater than zero.")
    return parsed


def _positive_int(value: object, location: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{location} must be an integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{location} must be an integer.") from exc
    if parsed <= 0:
        raise ValueError(f"{location} must be greater than zero.")
    return parsed


@dataclass(frozen=True, slots=True)
class Settings:
    bridge_url: str
    broker_url: str
    timeout_seconds: float
    max_document_bytes: int
    minimum_bridge_version: str
    server_host: str = "127.0.0.1"
    server_port: int = 8781
    server_path: str = "/mcp"
    inspector_host: str = "127.0.0.1"
    inspector_port: int = 8780


def load_settings(
    machine_dir: Path | None = None,
    user_dir: Path | None = None,
) -> Settings:
    active_machine_dir = machine_dir or Path(os.getenv("DMS_MCP_MACHINE_CONFIG_DIR", str(MACHINE_CONFIG_DIR)))
    if user_dir is None:
        user_dir_raw = os.getenv("DMS_MCP_USER_CONFIG_DIR")
        active_user_dir = Path(user_dir_raw) if user_dir_raw else USER_CONFIG_DIR
    else:
        active_user_dir = user_dir

    machine_path = active_machine_dir / "mcp.json"
    payload = _read_json(machine_path)
    if payload is None:
        raise FileNotFoundError(f"MCP configuration not found: {machine_path}")

    if active_user_dir is not None:
        local_payload = _read_json(active_user_dir / "mcp.local.json")
        if local_payload is not None:
            payload = _merge_dicts(payload, local_payload)

    bridge = payload.get("bridge")
    broker = payload.get("broker")
    server = payload.get("server")
    inspector = payload.get("inspector")
    runtime = payload.get("runtime")
    if not all(isinstance(section, dict) for section in (bridge, broker, server, inspector, runtime)):
        raise ValueError("Configuration requires bridge, broker, server, inspector and runtime JSON objects.")

    bridge_url = os.getenv("DMS_BRIDGE_URL") or _required_string(bridge, "url", "bridge")
    minimum_bridge_version = os.getenv("DMS_MCP_MIN_BRIDGE_VERSION") or _required_string(
        bridge, "minimumVersion", "bridge"
    )
    broker_url = os.getenv("DMS_BROKER_URL") or _required_string(broker, "url", "broker")
    timeout = os.getenv("DMS_MCP_TIMEOUT_SECONDS", runtime.get("timeoutSeconds"))
    max_bytes = os.getenv("DMS_MCP_MAX_DOCUMENT_BYTES", runtime.get("maxDocumentBytes"))
    server_path = os.getenv("DMS_MCP_SERVER_PATH") or _required_string(server, "path", "server")
    if not server_path.startswith("/"):
        raise ValueError("server.path must start with '/'.")
    return Settings(
        bridge_url=bridge_url.rstrip("/"),
        broker_url=broker_url.rstrip("/"),
        inspector_host=os.getenv("DMS_MCP_INSPECTOR_HOST") or _required_string(inspector, "host", "inspector"),
        inspector_port=_positive_int(
            os.getenv("DMS_MCP_INSPECTOR_PORT", inspector.get("port")), "inspector.port"
        ),
        timeout_seconds=_positive_float(timeout, "runtime.timeoutSeconds"),
        max_document_bytes=_positive_int(max_bytes, "runtime.maxDocumentBytes"),
        minimum_bridge_version=minimum_bridge_version,
        server_host=os.getenv("DMS_MCP_SERVER_HOST") or _required_string(server, "host", "server"),
        server_port=_positive_int(os.getenv("DMS_MCP_SERVER_PORT", server.get("port")), "server.port"),
        server_path=server_path,
    )
