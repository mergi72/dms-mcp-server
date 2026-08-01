from __future__ import annotations

import json

import httpx
import pytest

from dms_mcp_server.clients import BridgeClient, BrokerClient, UpstreamError
from dms_mcp_server.config import Settings


def test_list_items_resolves_credentials_through_broker() -> None:
    settings = Settings()

    def broker_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/resolve"
        body = json.loads(request.content)
        assert body["auth"]["target"] == "company/dms"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "auth": {"mode": "credentials", "username": "alice", "password": "secret"},
            },
        )

    def bridge_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body == {
            "path": "alfresco:/Shared",
            "auth": {"mode": "credentials", "username": "alice", "password": "secret"},
        }
        return httpx.Response(200, json={"ok": True, "data": {"items": []}})

    broker = BrokerClient(settings, httpx.MockTransport(broker_handler))
    bridge = BridgeClient(settings, broker, httpx.MockTransport(bridge_handler))

    assert bridge.list_items("alfresco:/Shared", "company/dms")["ok"] is True


def test_read_text_document() -> None:
    settings = Settings(max_document_bytes=100)
    broker = BrokerClient(settings, httpx.MockTransport(lambda _request: httpx.Response(500)))

    def bridge_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content="Ahoj DMS".encode(),
            headers={"X-Bridge-Raw-Content": "1", "Content-Type": "text/plain; charset=utf-8"},
        )

    bridge = BridgeClient(settings, broker, httpx.MockTransport(bridge_handler))

    result = bridge.read_document("alfresco:/readme.txt")

    assert result["text"] == "Ahoj DMS"
    assert result["mime_type"] == "text/plain"


def test_read_document_enforces_limit() -> None:
    settings = Settings(max_document_bytes=3)
    broker = BrokerClient(settings, httpx.MockTransport(lambda _request: httpx.Response(500)))
    bridge = BridgeClient(
        settings,
        broker,
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                content=b"1234",
                headers={"X-Bridge-Raw-Content": "1"},
            )
        ),
    )

    with pytest.raises(UpstreamError, match="exceeds"):
        bridge.read_document("alfresco:/large.bin")

