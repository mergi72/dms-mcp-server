from __future__ import annotations

import os
from dataclasses import dataclass


def _positive_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number.") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero.")
    return value


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero.")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    bridge_url: str = "http://127.0.0.1:8765"
    broker_url: str = "http://127.0.0.1:8776"
    timeout_seconds: float = 30.0
    max_document_bytes: int = 1_048_576
    default_credential_id: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        return cls(
            bridge_url=os.getenv("DMS_BRIDGE_URL", defaults.bridge_url).rstrip("/"),
            broker_url=os.getenv("DMS_BROKER_URL", defaults.broker_url).rstrip("/"),
            timeout_seconds=_positive_float("DMS_MCP_TIMEOUT_SECONDS", defaults.timeout_seconds),
            max_document_bytes=_positive_int("DMS_MCP_MAX_DOCUMENT_BYTES", defaults.max_document_bytes),
            default_credential_id=os.getenv("DMS_MCP_CREDENTIAL_ID") or None,
        )
