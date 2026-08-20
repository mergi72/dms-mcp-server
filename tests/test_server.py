from __future__ import annotations

import asyncio

from dms_mcp_server.config import Settings
from dms_mcp_server.server import create_server


def test_server_registers_only_expected_read_only_tools() -> None:
    settings = Settings(
        bridge_url="http://127.0.0.1:8765",
        timeout_seconds=30,
        max_document_bytes=1_048_576,
        minimum_bridge_version="0.2.0",
    )
    tools = asyncio.run(create_server(settings).list_tools())

    assert {tool.name for tool in tools} == {
        "bridge_health",
        "list_connections",
        "list_items",
        "search_items",
        "open_share_url",
        "get_item_info",
        "read_document",
    }
    for tool in tools:
        properties = tool.inputSchema.get("properties", {})
        assert "credential_id" not in properties


def test_server_registers_health_route() -> None:
    settings = Settings(
        bridge_url="http://127.0.0.1:8765",
        timeout_seconds=30,
        max_document_bytes=1_048_576,
        minimum_bridge_version="0.2.0",
    )
    routes = create_server(settings)._custom_starlette_routes
    assert any(route.path == "/health" and "GET" in route.methods for route in routes)
