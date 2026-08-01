from __future__ import annotations

import json

import httpx
import pytest

from dms_mcp_server.clients import BridgeClient, BrokerClient, UpstreamError
from dms_mcp_server.config import Settings


def test_list_items_resolves_credentials_through_broker() -> None:
    settings = Settings("http://127.0.0.1:8765", "http://127.0.0.1:8776", 30, 1_048_576)

    def broker_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/credentials/resolve"
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
        if request.method == "GET":
            assert request.url.path == "/bridge/wfx/connections/alfresco"
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "data": {
                        "name": "alfresco",
                        "auth": {"required": True, "credential_id": "company/dms"},
                    },
                },
            )
        body = json.loads(request.content)
        assert body == {
            "path": "alfresco:/Shared",
            "auth": {"mode": "credentials", "username": "alice", "password": "secret"},
        }
        return httpx.Response(200, json={"ok": True, "data": {"items": []}})

    broker = BrokerClient(settings, httpx.MockTransport(broker_handler))
    bridge = BridgeClient(settings, broker, httpx.MockTransport(bridge_handler))

    assert bridge.list_items("alfresco:/Shared")["ok"] is True


def test_root_listing_does_not_call_broker() -> None:
    settings = Settings("http://127.0.0.1:8765", "http://127.0.0.1:8776", 30, 1_048_576)

    def broker_handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Broker must not be called for the bridge root.")

    def bridge_handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"path": "/", "auth": None}
        return httpx.Response(200, json={"ok": True, "data": {"items": []}})

    broker = BrokerClient(settings, httpx.MockTransport(broker_handler))
    bridge = BridgeClient(settings, broker, httpx.MockTransport(bridge_handler))

    assert bridge.list_items("/")["ok"] is True


def test_read_text_document() -> None:
    settings = Settings("http://127.0.0.1:8765", "http://127.0.0.1:8776", 30, 100)
    broker = BrokerClient(
        settings,
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"ok": True, "auth": {"mode": "credentials", "username": "alice", "password": "secret"}},
            )
        ),
    )

    def bridge_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"ok": True, "data": {"auth": {"required": True, "credentialId": "company/dms"}}},
            )
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
    settings = Settings("http://127.0.0.1:8765", "http://127.0.0.1:8776", 30, 3)
    broker = BrokerClient(
        settings,
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"ok": True, "auth": {"mode": "credentials", "username": "alice", "password": "secret"}},
            )
        ),
    )

    def bridge_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"ok": True, "data": {"auth": {"required": True, "target": "company/dms"}}},
            )
        return httpx.Response(
            200,
            content=b"1234",
            headers={"X-Bridge-Raw-Content": "1"},
        )

    bridge = BridgeClient(
        settings,
        broker,
        httpx.MockTransport(bridge_handler),
    )

    with pytest.raises(UpstreamError, match="exceeds"):
        bridge.read_document("alfresco:/large.bin")
