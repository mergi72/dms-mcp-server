from __future__ import annotations

import logging
from pathlib import Path

from dms_mcp_server.config import Settings


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(settings: Settings) -> Path:
    log_dir = Path(settings.debug_path)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    for handler in tuple(root.handlers):
        if getattr(handler, "_vfs_mcp_handler", False):
            root.removeHandler(handler)
            handler.close()
    root.setLevel(logging.DEBUG if settings.debug_enabled else logging.INFO)
    for logger_name, level_name in settings.logger_levels:
        logging.getLogger(logger_name).setLevel(level_name)

    formatter = logging.Formatter(LOG_FORMAT)
    normal = logging.FileHandler(log_dir / "mcp.log", encoding="utf-8")
    normal.setLevel(logging.INFO)
    normal.setFormatter(formatter)
    normal._vfs_mcp_handler = True  # type: ignore[attr-defined]
    root.addHandler(normal)

    if settings.debug_enabled:
        debug = logging.FileHandler(log_dir / "mcp-debug.log", encoding="utf-8")
        debug.setLevel(logging.DEBUG)
        debug.setFormatter(formatter)
        debug._vfs_mcp_handler = True  # type: ignore[attr-defined]
        root.addHandler(debug)
    return log_dir
