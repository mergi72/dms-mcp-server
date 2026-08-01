from __future__ import annotations

import pytest

from dms_mcp_server.compatibility import require_supported_bridge_version


@pytest.mark.parametrize("current", ["0.2.0", "0.2.0-beta", "1.0.1"])
def test_supported_bridge_versions(current: str) -> None:
    require_supported_bridge_version(current, "0.2.0")


def test_unsupported_bridge_version() -> None:
    with pytest.raises(RuntimeError, match="minimum required version"):
        require_supported_bridge_version("0.1.9", "0.2.0")


def test_invalid_bridge_version() -> None:
    with pytest.raises(RuntimeError, match="compatibility check failed"):
        require_supported_bridge_version("development", "0.2.0")
