from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
PROJECT_ROOT = SRC_DIR.parent


def _machine_config_dir() -> Path:
    explicit = os.getenv("DMS_MCP_MACHINE_CONFIG_DIR")
    if explicit:
        return Path(explicit)
    return PROJECT_ROOT / "config"


def _user_config_dir() -> Path | None:
    explicit = os.getenv("DMS_MCP_USER_CONFIG_DIR")
    if explicit:
        return Path(explicit)
    app_data = os.getenv("APPDATA")
    if app_data:
        return Path(app_data) / "DMS MCP" / "config"
    return None


MACHINE_CONFIG_DIR = _machine_config_dir()
USER_CONFIG_DIR = _user_config_dir()

