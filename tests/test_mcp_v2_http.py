from __future__ import annotations

import asyncio
import socket
import threading
from time import perf_counter

import pytest
import uvicorn
from mcp import Client

import dms_mcp_server.server as server_module
from dms_mcp_server.config import Settings


EXPECTED_TOOLS = {
    "bridge_health",
    "list_connections",
    "list_items",
    "search_items",
    "search_metadata",
    "open_share_url",
    "get_item_info",
    "read_document",
}


class _ConcurrentBridge:
    search_started = threading.Event()
    release_search = threading.Event()

    def __init__(self, _settings: Settings) -> None:
        pass

    def list_connections(self) -> dict:
        return {"ok": True, "data": {"connections": ["alfresco"]}}

    def search_items(self, path: str, query: str, max_results: int, files_only: bool, search_mode: str = "first_matches") -> dict:
        self.search_started.set()
        self.release_search.wait(timeout=5)
        return {"ok": True, "data": {"path": path, "query": query, "items": []}}


class _FailingBridge(_ConcurrentBridge):
    def search_items(self, path: str, query: str, max_results: int, files_only: bool, search_mode: str = "first_matches") -> dict:
        raise RuntimeError("simulated bridge failure")


class _TimeoutBridge(_ConcurrentBridge):
    def search_items(self, path: str, query: str, max_results: int, files_only: bool, search_mode: str = "first_matches") -> dict:
        raise TimeoutError("simulated bridge timeout")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_streamable_http_contract_and_concurrent_sync_tools(monkeypatch) -> None:
    async def scenario() -> None:
        _ConcurrentBridge.search_started.clear()
        _ConcurrentBridge.release_search.clear()
        monkeypatch.setattr(server_module, "BridgeClient", _ConcurrentBridge)
        settings = Settings(
            bridge_url="http://127.0.0.1:8765",
            timeout_seconds=30,
            max_document_bytes=1_048_576,
            minimum_bridge_version="0.2.0",
        )
        port = _free_port()
        app = server_module.create_server(settings).streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
        )
        runtime = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        runtime_task = asyncio.create_task(runtime.serve())
        while not runtime.started:
            await asyncio.sleep(0.01)

        try:
            url = f"http://127.0.0.1:{port}/mcp"
            async with Client(url) as slow_client, Client(url) as fast_client:
                tools = await fast_client.list_tools()
                assert {tool.name for tool in tools.tools} == EXPECTED_TOOLS

                slow_call = asyncio.create_task(
                    slow_client.call_tool(
                        "search_items",
                        {
                            "path": "alfresco:/",
                            "query": "-TL-",
                            "max_results": 20,
                            "files_only": True,
                        },
                    )
                )
                assert await asyncio.to_thread(_ConcurrentBridge.search_started.wait, 2)

                started = perf_counter()
                quick_result = await fast_client.call_tool("list_connections")
                quick_duration = perf_counter() - started
                _ConcurrentBridge.release_search.set()
                slow_result = await slow_call

                assert quick_result.is_error is False
                assert slow_result.is_error is False
                assert quick_duration < 1.0
        finally:
            _ConcurrentBridge.release_search.set()
            runtime.should_exit = True
            await runtime_task

    asyncio.run(scenario())


@pytest.mark.parametrize("bridge_type", [_FailingBridge, _TimeoutBridge])
def test_stateless_server_recovers_after_bridge_failure(monkeypatch, bridge_type) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(server_module, "BridgeClient", bridge_type)
        settings = Settings(
            bridge_url="http://127.0.0.1:8765",
            timeout_seconds=30,
            max_document_bytes=1_048_576,
            minimum_bridge_version="0.2.0",
        )
        port = _free_port()
        app = server_module.create_server(settings).streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
        )
        runtime = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        runtime_task = asyncio.create_task(runtime.serve())
        while not runtime.started:
            await asyncio.sleep(0.01)

        try:
            url = f"http://127.0.0.1:{port}/mcp"
            async with Client(url) as client:
                failed = await client.call_tool(
                    "search_items",
                    {"path": "alfresco:/", "query": "-TL-"},
                )
                recovered = await client.call_tool("list_connections")

                assert failed.is_error is True
                assert recovered.is_error is False
        finally:
            runtime.should_exit = True
            await runtime_task

    asyncio.run(scenario())


def test_client_disconnect_does_not_block_other_stateless_requests(monkeypatch) -> None:
    async def scenario() -> None:
        _ConcurrentBridge.search_started.clear()
        _ConcurrentBridge.release_search.clear()
        monkeypatch.setattr(server_module, "BridgeClient", _ConcurrentBridge)
        settings = Settings(
            bridge_url="http://127.0.0.1:8765",
            timeout_seconds=30,
            max_document_bytes=1_048_576,
            minimum_bridge_version="0.2.0",
        )
        port = _free_port()
        app = server_module.create_server(settings).streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
        )
        runtime = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        runtime_task = asyncio.create_task(runtime.serve())
        while not runtime.started:
            await asyncio.sleep(0.01)

        async def abandoned_call(url: str) -> None:
            async with Client(url) as client:
                await client.call_tool(
                    "search_items",
                    {"path": "alfresco:/", "query": "-TL-"},
                )

        try:
            url = f"http://127.0.0.1:{port}/mcp"
            abandoned = asyncio.create_task(abandoned_call(url))
            assert await asyncio.to_thread(_ConcurrentBridge.search_started.wait, 2)
            abandoned.cancel()
            try:
                await abandoned
            except asyncio.CancelledError:
                pass

            async with Client(url) as client:
                started = perf_counter()
                recovered = await client.call_tool("list_connections")
                elapsed = perf_counter() - started

            assert recovered.is_error is False
            assert elapsed < 1.0
        finally:
            _ConcurrentBridge.release_search.set()
            runtime.should_exit = True
            await runtime_task

    asyncio.run(scenario())
