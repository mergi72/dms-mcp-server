from __future__ import annotations

import json
import hashlib

import httpx
import pytest

from dms_mcp_server.clients import BridgeClient, UpstreamError
from dms_mcp_server.config import Settings


def _settings(*, max_document_bytes: int = 1_048_576) -> Settings:
    return Settings(
        "http://127.0.0.1:8765",
        30,
        max_document_bytes,
        "0.2.0",
    )


def _health_response() -> httpx.Response:
    return httpx.Response(200, json={"status": "ok", "service": "dms-provider-bridge", "version": "1.0.1"})


def test_list_items_passes_only_credential_reference_to_bridge() -> None:
    settings = _settings()

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
            "auth": {"mode": "credentials", "credential_id": "company/dms"},
        }
        return httpx.Response(200, json={"ok": True, "data": {"items": []}})

    bridge = BridgeClient(settings, httpx.MockTransport(bridge_handler))

    assert bridge.list_items("alfresco:/Shared")["ok"] is True


def test_root_listing_sends_no_auth_reference() -> None:
    settings = _settings()

    def bridge_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return _health_response()
        assert json.loads(request.content) == {"path": "/", "auth": None}
        return httpx.Response(200, json={"ok": True, "data": {"items": []}})

    bridge = BridgeClient(settings, httpx.MockTransport(bridge_handler))

    assert bridge.list_items("/")["ok"] is True


def test_search_items_forwards_general_contract_and_auth() -> None:
    settings = _settings()
    def bridge_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return _health_response()
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"ok": True, "data": {"mount": "alfresco:/", "auth": {"required": True, "credential_id": "company/dms"}}},
            )
        assert request.url.path == "/bridge/wfx/search"
        assert json.loads(request.content) == {
            "path": "alfresco:/projects",
            "query": "steam DN50",
            "max_results": 15,
            "files_only": True,
            "auth": {"mode": "credentials", "credential_id": "company/dms"},
        }
        return httpx.Response(
            200,
            json={"ok": True, "data": {"total": 1, "items": [{"id": "1", "path": "/projects/steam.docx"}]}},
        )

    bridge = BridgeClient(settings, httpx.MockTransport(bridge_handler))

    result = bridge.search_items("alfresco:/projects", " steam DN50 ", 15)
    assert result["ok"] is True
    assert result["data"]["items"][0]["path"] == "alfresco:/projects/steam.docx"


def test_search_items_forwards_folder_inclusion() -> None:
    settings = _settings()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return _health_response()
        if request.method == "GET":
            return httpx.Response(200, json={"ok": True, "data": {"mount": "alfresco:/", "auth": {"required": False}}})
        if request.url.path == "/bridge/wfx/list":
            return httpx.Response(200, json={"ok": True, "data": {"items": []}})
        assert json.loads(request.content)["files_only"] is False
        return httpx.Response(200, json={"ok": True, "data": {"total": 0, "returned": 0, "items": []}})

    bridge = BridgeClient(settings, httpx.MockTransport(handler))

    assert bridge.search_items("alfresco:/", "steam", files_only=False)["ok"] is True


def test_search_items_returns_connection_path_below_document_library() -> None:
    settings = _settings()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return _health_response()
        if request.method == "GET":
            return httpx.Response(200, json={"ok": True, "data": {"mount": "alfresco:/", "auth": {"required": False}}})
        if request.url.path == "/bridge/wfx/list":
            return httpx.Response(
                200,
                json={"ok": True, "data": {"items": [{"name": "03 zakázky v realizaci"}]}},
            )
        return httpx.Response(
            200,
            json={
                "ok": True,
                "data": {
                    "items": [
                        {
                            "id": "1",
                            "path": "/Agenda společnosti/Stránky/deals/documentLibrary/03 zakázky v realizaci/22 080 - UNI_Novy odolejovac bl. 68",
                        }
                    ]
                },
            },
        )

    bridge = BridgeClient(settings, httpx.MockTransport(handler))

    result = bridge.search_items("alfresco:/", "22080")

    assert result["data"]["items"][0]["path"] == (
        "alfresco:/03 zakázky v realizaci/22 080 - UNI_Novy odolejovac bl. 68"
    )


def test_public_search_path_uses_connection_contract_without_provider_markers() -> None:
    assert BridgeClient._public_search_path(
        "firma-dms:/",
        "/",
        "/private/upstream/root/Projects/Contract.docx",
        {"Projects"},
    ) == "firma-dms:/Projects/Contract.docx"


def test_public_search_path_refuses_unmatched_internal_path() -> None:
    assert BridgeClient._public_search_path(
        "alfresco:/",
        "/",
        "/private/upstream/root/unknown.docx",
        {"Projects"},
    ) is None


@pytest.mark.parametrize("value", [0, 101, True, 1.5])
def test_search_items_rejects_invalid_limit(value: object) -> None:
    settings = _settings()
    bridge = BridgeClient(settings, httpx.MockTransport(lambda _request: _health_response()))

    with pytest.raises(ValueError, match="max_results"):
        bridge.search_items("alfresco:/", "query", value)  # type: ignore[arg-type]


def test_open_share_url_resolves_stats_and_lists_folder() -> None:
    settings = _settings()
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/health":
            return _health_response()
        if request.url.path == "/bridge/wfx/resolve-share-url":
            assert json.loads(request.content) == {
                "share_url": "https://dms.example/share/page/#/Shared/Documents",
                "connection": "alfresco",
            }
            return httpx.Response(
                200,
                json={"ok": True, "data": {"connection": "alfresco", "path": "alfresco:/Shared/Documents"}},
            )
        if request.url.path == "/bridge/wfx/connections":
            return httpx.Response(
                200,
                json={"ok": True, "data": {"connections": [{"name": "alfresco", "driver": "alfresco", "registered": True}]}},
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"ok": True, "data": {"driver": "alfresco", "auth": {"required": False}}},
            )
        body = json.loads(request.content)
        assert body == {"path": "alfresco:/Shared/Documents", "auth": None}
        if request.url.path == "/bridge/wfx/stat":
            return httpx.Response(200, json={"ok": True, "data": {"name": "Documents", "is_folder": True}})
        if request.url.path == "/bridge/wfx/list":
            return httpx.Response(200, json={"ok": True, "data": {"total": 1, "items": [{"name": "report.pdf"}]}})
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    bridge = BridgeClient(settings, httpx.MockTransport(handler))

    result = bridge.open_share_url(" https://dms.example/share/page/#/Shared/Documents ")

    assert result["data"]["resolved"]["path"] == "alfresco:/Shared/Documents"
    assert result["data"]["item"]["is_folder"] is True
    assert result["data"]["listing"]["items"][0]["name"] == "report.pdf"
    assert ("POST", "/bridge/wfx/list") in requests


def test_open_share_url_auto_selects_edocat_for_dir_link() -> None:
    settings = _settings()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return _health_response()
        if request.url.path == "/bridge/wfx/connections":
            return httpx.Response(
                200,
                json={"ok": True, "data": {"connections": [{"name": "edocat", "driver": "edocat", "registered": True}]}},
            )
        if request.url.path == "/bridge/wfx/resolve-share-url":
            assert json.loads(request.content)["connection"] == "edocat"
            return httpx.Response(200, json={"ok": True, "data": {"path": "edocat:/Shared"}})
        if request.url.path == "/bridge/wfx/connections/edocat":
            return httpx.Response(200, json={"ok": True, "data": {"auth": {"required": False}}})
        if request.url.path == "/bridge/wfx/stat":
            return httpx.Response(200, json={"ok": True, "data": {"name": "Shared", "is_folder": False}})
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    bridge = BridgeClient(settings, httpx.MockTransport(handler))

    result = bridge.open_share_url("https://edocat.example/share/page/browse/DIR-250566")

    assert result["data"]["requested_connection"] == "edocat"


def test_open_share_url_uses_edocat_connection_directly() -> None:
    settings = _settings()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return _health_response()
        if request.url.path == "/bridge/wfx/resolve-share-url":
            assert json.loads(request.content)["connection"] == "edocat"
            return httpx.Response(
                200,
                json={"ok": True, "data": {"connection": "edocat", "path": "edocat:/Shared/report.pdf"}},
            )
        if request.url.path == "/bridge/wfx/connections/edocat":
            return httpx.Response(200, json={"ok": True, "data": {"driver": "edocat", "auth": {"required": False}}})
        if request.url.path == "/bridge/wfx/stat":
            return httpx.Response(200, json={"ok": True, "data": {"name": "report.pdf", "is_folder": False}})
        raise AssertionError(f"Unexpected request: {request.method} {request.url.path}")

    bridge = BridgeClient(settings, httpx.MockTransport(handler))

    result = bridge.open_share_url("https://edocat.example/share/page/browse/DIR-1", "edocat")

    assert result["data"]["requested_connection"] == "edocat"
    assert result["data"]["resolved"]["path"] == "edocat:/Shared/report.pdf"
    assert result["data"]["listing"] is None


@pytest.mark.parametrize("share_url", ["", "not-a-url", "file:///tmp/item"])
def test_open_share_url_rejects_invalid_url(share_url: str) -> None:
    settings = _settings()
    bridge = BridgeClient(settings, httpx.MockTransport(lambda _request: _health_response()))

    with pytest.raises(ValueError, match="share_url"):
        bridge.open_share_url(share_url)


def test_read_text_document() -> None:
    settings = _settings(max_document_bytes=100)

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

    bridge = BridgeClient(settings, httpx.MockTransport(bridge_handler))

    result = bridge.read_document("alfresco:/readme.txt")

    assert result["text"] == "Ahoj DMS"
    assert result["mime_type"] == "text/plain"
    assert result["sha256"] == hashlib.sha256("Ahoj DMS".encode()).hexdigest()


def test_read_text_document_hashes_original_bytes() -> None:
    settings = _settings(max_document_bytes=100)
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

    bridge = BridgeClient(settings, httpx.MockTransport(bridge_handler))
    result = bridge.read_document("alfresco:/legacy.txt")

    assert "�" in result["text"]
    assert result["sha256"] == hashlib.sha256(original).hexdigest()


def test_read_document_enforces_limit() -> None:
    settings = _settings(max_document_bytes=3)

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
        httpx.MockTransport(bridge_handler),
    )

    with pytest.raises(UpstreamError, match="exceeds"):
        bridge.read_document("alfresco:/large.bin")


def test_stat_uses_connection_auth() -> None:
    settings = _settings()

    def bridge_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return _health_response()
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"ok": True, "data": {"auth": {"required": True, "credential_id": "company/dms"}}},
            )
        assert request.url.path == "/bridge/wfx/stat"
        assert json.loads(request.content)["auth"] == {
            "mode": "credentials",
            "credential_id": "company/dms",
        }
        return httpx.Response(200, json={"ok": True, "data": {"name": "readme.txt", "is_folder": False}})

    bridge = BridgeClient(settings, httpx.MockTransport(bridge_handler))

    assert bridge.stat("alfresco:/readme.txt")["data"]["name"] == "readme.txt"


def test_rejects_unsupported_bridge_version() -> None:
    settings = Settings("http://127.0.0.1:8765", 30, 100, "2.0.0")
    bridge = BridgeClient(settings, httpx.MockTransport(lambda _request: _health_response()))

    with pytest.raises(UpstreamError, match="minimum required version"):
        bridge.list_connections()


def test_bridge_rejects_non_object_json() -> None:
    settings = _settings()
    bridge = BridgeClient(
        settings,
        httpx.MockTransport(lambda _request: httpx.Response(200, json=["unexpected"])),
    )

    with pytest.raises(UpstreamError, match="non-object JSON"):
        bridge.health()


def test_read_document_rejects_invalid_content_length() -> None:
    settings = _settings(max_document_bytes=100)

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

    bridge = BridgeClient(settings, httpx.MockTransport(bridge_handler))

    with pytest.raises(UpstreamError, match="invalid Content-Length"):
        bridge.read_document("alfresco:/readme.txt")
