from __future__ import annotations

import logging
from pathlib import Path

from dms_mcp_server.config import Settings
from dms_mcp_server.logging_config import configure_logging


def test_configure_logging_creates_normal_and_debug_logs(tmp_path: Path) -> None:
    settings = Settings(
        "http://127.0.0.1:8765",
        "http://127.0.0.1:8776",
        30,
        1_048_576,
        "0.2.0",
        debug_enabled=True,
        debug_path=str(tmp_path),
    )

    configure_logging(settings)
    logging.getLogger("mcp.test").info("mcp_test_event status=ok")
    for handler in logging.getLogger().handlers:
        handler.flush()

    assert "mcp_test_event status=ok" in (tmp_path / "mcp.log").read_text(encoding="utf-8")
    assert "mcp_test_event status=ok" in (tmp_path / "mcp-debug.log").read_text(encoding="utf-8")
