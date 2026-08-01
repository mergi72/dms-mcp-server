from __future__ import annotations

import json
import hashlib

import httpx
import pytest

from dms_mcp_server.clients import BridgeClient, BrokerClient, UpstreamError
from dms_mcp_server.config import Settings


def _settings(*, max_document_bytes: int = 1_048_576) -> Settings:
    return Settings(
        "http://127.0.0.1:8765",
        "http://127.0.0.1:8776",
        30,
        max_document_bytes,
        "0.2.0",
    )


def _health_response() -> httpx.Response:
    return httpx.Response(200, json={"status": "ok", "service": "dms-provider-bridge", "version": "1.0.1"})


def test_list_items_resolves_credentials_through_broker() -> None:
    settings = _settings()

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
        if request.url.path == "/health":
            return _health_response()
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
    settings = _settings()

    def broker_handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Broker must not be called for the bridge root.")

    def bridge_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return _health_response()
        assert json.loads(request.content) == {"path": "/", "auth": None}
        return httpx.Response(200, json={"ok": True, "data": {"items": []}})

    broker = BrokerClient(settings, httpx.MockTransport(broker_handler))
    bridge = BridgeClient(settings, broker, httpx.MockTransport(bridge_handler))

    assert bridge.list_items("/")["ok"] is True


def test_read_text_document() -> None:
    settings = _settings(max_document_bytes=100)
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
        if request.url.path == "/health":
            return _health_response()
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
    assert result["sha256"] == hashlib.sha256("Ahoj DMS".encode()).hexdigest()


def test_read_text_document_hashes_original_bytes() -> None:
    settings = _settings(max_document_bytes=100)
    broker = BrokerClient(
        settings,
        httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"ok": True, "auth": {"mode": "credentials"}})
        ),
    )
    original = b"\x80non-utf8"

    def bridge_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return _health_response()
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"ok": True, "data": {"auth": {"required": True, "credential_id": "company/dms"}}},
            )
        return httpx.Response(
            200,
            content=original,
            headers={"X-Bridge-Raw-Content": "1", "Content-Type": "text/plain"},
        )

    bridge = BridgeClient(settings, broker, httpx.MockTransport(bridge_handler))
    result = bridge.read_document("alfresco:/legacy.txt")

    assert "�" in result["text"]
    assert result["sha256"] == hashlib.sha256(original).hexdigest()


def test_read_document_enforces_limit() -> None:
    settings = _settings(max_document_bytes=3)
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
        if request.url.path == "/health":
            return _health_response()
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


def test_stat_uses_connection_auth() -> None:
    settings = _settings()
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
        if request.url.path == "/health":
            return _health_response()
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"ok": True, "data": {"auth": {"required": True, "credential_id": "company/dms"}}},
            )
        assert request.url.path == "/bridge/wfx/stat"
        assert json.loads(request.content)["auth"]["username"] == "alice"
        return httpx.Response(200, json={"ok": True, "data": {"name": "readme.txt", "is_folder": False}})

    bridge = BridgeClient(settings, broker, httpx.MockTransport(bridge_handler))

    assert bridge.stat("alfresco:/readme.txt")["data"]["name"] == "readme.txt"


def test_rejects_unsupported_bridge_version() -> None:
    settings = Settings("http://127.0.0.1:8765", "http://127.0.0.1:8776", 30, 100, "2.0.0")
    broker = BrokerClient(settings, httpx.MockTransport(lambda _request: httpx.Response(500)))
    bridge = BridgeClient(settings, broker, httpx.MockTransport(lambda _request: _health_response()))

    with pytest.raises(UpstreamError, match="minimum required version"):
        bridge.list_connections()


def test_broker_rejects_non_object_json() -> None:
    settings = _settings()
    broker = BrokerClient(
        settings,
        httpx.MockTransport(lambda _request: httpx.Response(200, json=["unexpected"])),
    )

    with pytest.raises(UpstreamError, match="non-object JSON"):
        broker.resolve("company/dms")


def test_bridge_rejects_non_object_json() -> None:
    settings = _settings()
    broker = BrokerClient(settings, httpx.MockTransport(lambda _request: httpx.Response(500)))
    bridge = BridgeClient(
        settings,
        broker,
        httpx.MockTransport(lambda _request: httpx.Response(200, json=["unexpected"])),
    )

    with pytest.raises(UpstreamError, match="non-object JSON"):
        bridge.health()


def test_read_document_rejects_invalid_content_length() -> None:
    settings = _settings(max_document_bytes=100)
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
        if request.url.path == "/health":
            return _health_response()
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"ok": True, "data": {"auth": {"required": True, "credential_id": "company/dms"}}},
            )
        return httpx.Response(
            200,
            content=b"abc",
            headers={"X-Bridge-Raw-Content": "1", "Content-Length": "invalid"},
        )

    bridge = BridgeClient(settings, broker, httpx.MockTransport(bridge_handler))

    with pytest.raises(UpstreamError, match="invalid Content-Length"):
        bridge.read_document("alfresco:/readme.txt")
