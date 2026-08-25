from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dms_mcp_server.paths import MACHINE_CONFIG_DIR, USER_CONFIG_DIR


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_ROUTABLE_OPERATIONS = {"list_items", "search_items", "get_item_info", "read_document"}


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


def _local_host(value: str, location: str) -> str:
    host = value.strip().lower()
    if host not in _LOCAL_HOSTS:
        raise ValueError(f"{location} must be localhost until MCP client authentication is available.")
    return host


@dataclass(frozen=True, slots=True)
class Settings:
    bridge_url: str
    timeout_seconds: float
    max_document_bytes: int
    minimum_bridge_version: str
    server_host: str = "127.0.0.1"
    server_port: int = 8781
    server_path: str = "/mcp"
    inspector_host: str = "127.0.0.1"
    inspector_port: int = 8780
    debug_enabled: bool = False
    debug_path: str = "%APPDATA%\\DMS MCP\\logs"
    search_mode: str = "recursive_list"
    search_case_sensitive: bool = False
    search_max_depth: int = 64
    search_max_folders: int = 5000
    search_timeout_seconds: float = 120
    search_concurrency: int = 4
    search_max_results: int = 1000
    routing_rules: tuple[tuple[str, str, tuple[str, ...]], ...] = ()

    def __post_init__(self) -> None:
        _local_host(self.server_host, "server.host")

    def route_connection(self, operation: str, requested_connection: str) -> str:
        requested = requested_connection.strip().casefold()
        for source, target, operations in self.routing_rules:
            if source.casefold() == requested and operation in operations:
                return target
        return requested_connection


def _routing_rules(payload: object) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    if payload is None:
        return ()
    if not isinstance(payload, dict):
        raise ValueError("Configuration routing must be a JSON object.")
    connections = payload.get("connections", {})
    if not isinstance(connections, dict):
        raise ValueError("routing.connections must be a JSON object.")
    rules: list[tuple[str, str, tuple[str, ...]]] = []
    for source, raw_rule in connections.items():
        if not isinstance(source, str) or not source.strip() or ":" in source or "/" in source or "\\" in source:
            raise ValueError("routing.connections keys must be connection names.")
        if not isinstance(raw_rule, dict):
            raise ValueError(f"routing.connections.{source} must be a JSON object.")
        target = raw_rule.get("lowLevelConnection")
        if not isinstance(target, str) or not target.strip() or ":" in target or "/" in target or "\\" in target:
            raise ValueError(f"routing.connections.{source}.lowLevelConnection must be a connection name.")
        raw_operations = raw_rule.get("operations", [])
        if not isinstance(raw_operations, list) or not raw_operations:
            raise ValueError(f"routing.connections.{source}.operations must be a non-empty array.")
        operations: list[str] = []
        for operation in raw_operations:
            if not isinstance(operation, str) or operation not in _ROUTABLE_OPERATIONS:
                allowed = ", ".join(sorted(_ROUTABLE_OPERATIONS))
                raise ValueError(f"routing.connections.{source}.operations contains an unsupported operation; allowed: {allowed}.")
            if operation not in operations:
                operations.append(operation)
        rules.append((source.strip(), target.strip(), tuple(operations)))
    return tuple(rules)


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
    server = payload.get("server")
    inspector = payload.get("inspector")
    runtime = payload.get("runtime")
    search = payload.get("search", {})
    routing = payload.get("routing", {})
    debug = payload.get("debug", {})
    if not all(isinstance(section, dict) for section in (bridge, server, inspector, runtime)):
        raise ValueError("Configuration requires bridge, server, inspector and runtime JSON objects.")
    if not isinstance(debug, dict):
        raise ValueError("Configuration debug must be a JSON object.")
    if not isinstance(search, dict):
        raise ValueError("Configuration search must be a JSON object.")

    search_case_sensitive = search.get("caseSensitive", False)
    if not isinstance(search_case_sensitive, bool):
        raise ValueError("search.caseSensitive must be a boolean.")
    search_mode = search.get("mode", "recursive_list")
    if search_mode != "recursive_list":
        raise ValueError("search.mode must be 'recursive_list'.")

    debug_enabled = debug.get("enable", False)
    if not isinstance(debug_enabled, bool):
        raise ValueError("debug.enable must be a boolean.")
    debug_path = debug.get("path", "%APPDATA%\\DMS MCP\\logs")
    if not isinstance(debug_path, str) or not debug_path.strip():
        raise ValueError("debug.path must be a non-empty string.")

    bridge_url = os.getenv("DMS_BRIDGE_URL") or _required_string(bridge, "url", "bridge")
    minimum_bridge_version = os.getenv("DMS_MCP_MIN_BRIDGE_VERSION") or _required_string(
        bridge, "minimumVersion", "bridge"
    )
    timeout = os.getenv("DMS_MCP_TIMEOUT_SECONDS", runtime.get("timeoutSeconds"))
    max_bytes = os.getenv("DMS_MCP_MAX_DOCUMENT_BYTES", runtime.get("maxDocumentBytes"))
    server_path = os.getenv("DMS_MCP_SERVER_PATH") or _required_string(server, "path", "server")
    if not server_path.startswith("/"):
        raise ValueError("server.path must start with '/'.")
    return Settings(
        bridge_url=bridge_url.rstrip("/"),
        inspector_host=os.getenv("DMS_MCP_INSPECTOR_HOST") or _required_string(inspector, "host", "inspector"),
        inspector_port=_positive_int(
            os.getenv("DMS_MCP_INSPECTOR_PORT", inspector.get("port")), "inspector.port"
        ),
        timeout_seconds=_positive_float(timeout, "runtime.timeoutSeconds"),
        max_document_bytes=_positive_int(max_bytes, "runtime.maxDocumentBytes"),
        minimum_bridge_version=minimum_bridge_version,
        server_host=_local_host(
            os.getenv("DMS_MCP_SERVER_HOST") or _required_string(server, "host", "server"),
            "server.host",
        ),
        server_port=_positive_int(os.getenv("DMS_MCP_SERVER_PORT", server.get("port")), "server.port"),
        server_path=server_path,
        debug_enabled=debug_enabled,
        debug_path=os.path.expandvars(debug_path.strip()),
        search_mode=search_mode,
        search_case_sensitive=search_case_sensitive,
        search_max_depth=_positive_int(search.get("maxDepth", 64), "search.maxDepth"),
        search_max_folders=_positive_int(search.get("maxFolders", 5000), "search.maxFolders"),
        search_timeout_seconds=_positive_float(search.get("timeoutSeconds", 120), "search.timeoutSeconds"),
        search_concurrency=_positive_int(search.get("concurrency", 4), "search.concurrency"),
        search_max_results=_positive_int(search.get("maxResults", 1000), "search.maxResults"),
        routing_rules=_routing_rules(routing),
    )
