from __future__ import annotations

import asyncio

from dms_mcp_server.config import Settings
from dms_mcp_server.server import _request_correlation_id, create_server


class _RequestContext:
    def __init__(self, headers: dict[str, str]) -> None:
        self.request = type("Request", (), {"headers": headers})()


class _Context:
    def __init__(self, headers: dict[str, str]) -> None:
        self.request_context = _RequestContext(headers)


def test_request_correlation_prefers_vfs_header() -> None:
    context = _Context(
        {
            "X-VFS-Correlation-ID": "123e4567-e89b-12d3-a456-426614174000",
            "Mcp-Session-Id": "720745f0d9294cbe8fe53933672e90c1",
        }
    )
    assert _request_correlation_id(context) == "123e4567-e89b-12d3-a456-426614174000"


def test_request_correlation_falls_back_to_mcp_session() -> None:
    context = _Context({"Mcp-Session-Id": "720745f0d9294cbe8fe53933672e90c1"})
    assert _request_correlation_id(context) == "720745f0d9294cbe8fe53933672e90c1"


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
        "search_metadata",
        "open_share_url",
        "get_item_info",
        "read_document",
    }
    for tool in tools:
        wire_tool = tool.model_dump(by_alias=True)
        properties = wire_tool["inputSchema"].get("properties", {})
        assert "credential_id" not in properties
        annotations = wire_tool["annotations"]
        assert annotations["readOnlyHint"] is True
        assert annotations["destructiveHint"] is False
        assert annotations["openWorldHint"] is False


def test_tool_input_contract_stays_compatible_with_demi() -> None:
    settings = Settings(
        bridge_url="http://127.0.0.1:8765",
        timeout_seconds=30,
        max_document_bytes=1_048_576,
        minimum_bridge_version="0.2.0",
    )
    tools = {tool.name: tool for tool in asyncio.run(create_server(settings).list_tools())}

    expected = {
        "bridge_health": ({}, set()),
        "list_connections": ({}, set()),
        "list_items": ({"path": "/"}, set()),
        "search_items": ({"path": None, "query": None, "max_results": 20, "files_only": True, "search_mode": "first_matches"}, {"path", "query"}),
        "search_metadata": ({"path": None, "field": None, "value": None, "max_results": 20, "files_only": False}, {"path", "field", "value"}),
        "open_share_url": ({"share_url": None, "connection": "auto"}, {"share_url"}),
        "get_item_info": ({"path": None}, {"path"}),
        "read_document": ({"path": None}, {"path"}),
    }
    for name, (parameters, required) in expected.items():
        schema = tools[name].model_dump(by_alias=True)["inputSchema"]
        properties = schema.get("properties", {})
        assert set(properties) == set(parameters)
        assert set(schema.get("required", [])) == required
        for parameter, default in parameters.items():
            if default is None:
                assert "default" not in properties[parameter]
            else:
                assert properties[parameter]["default"] == default


def test_tool_descriptions_define_deterministic_tag_search_orchestration() -> None:
    settings = Settings(
        bridge_url="http://127.0.0.1:8765",
        timeout_seconds=30,
        max_document_bytes=1_048_576,
        minimum_bridge_version="0.2.0",
    )
    tools = {tool.name: tool for tool in asyncio.run(create_server(settings).list_tools())}

    metadata_description = tools["search_metadata"].description or ""
    search_description = tools["search_items"].description or ""
    assert "path='alfresco:/', field='TAG'" in metadata_description
    assert "do not probe eDoCat" in metadata_description
    assert "reuse its public path verbatim" in metadata_description
    assert "Do not repeat an identical successful call" in search_description
    assert "first_matches with reason=result_limit is a successful final result" in search_description
    assert "exhaustive with complete=true and warnings=[] is final" in search_description


def test_server_instructions_define_final_search_results() -> None:
    settings = Settings(
        bridge_url="http://127.0.0.1:8765",
        timeout_seconds=30,
        max_document_bytes=1_048_576,
        minimum_bridge_version="0.2.0",
    )
    instructions = create_server(settings).instructions or ""

    assert "For first_matches, reason=result_limit with warnings=[] is a successful final interactive result" in instructions
    assert "must not be retried" in instructions
    assert "For exhaustive, complete=true with warnings=[] is final" in instructions


def test_server_registers_health_route() -> None:
    settings = Settings(
        bridge_url="http://127.0.0.1:8765",
        timeout_seconds=30,
        max_document_bytes=1_048_576,
        minimum_bridge_version="0.2.0",
    )
    routes = create_server(settings)._custom_starlette_routes
    assert any(route.path == "/health" and "GET" in route.methods for route in routes)
