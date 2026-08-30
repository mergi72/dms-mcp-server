from __future__ import annotations

import json
import hashlib

import httpx
import pytest

from dms_mcp_server.clients import BridgeClient, UpstreamError
from dms_mcp_server.config import Settings
from dms_mcp_server.tracing import correlation_scope


def _settings(*, max_document_bytes: int = 1_048_576, routing: bool = False) -> Settings:
    return Settings(
        bridge_url="http://127.0.0.1:8765",
        timeout_seconds=30,
        max_document_bytes=max_document_bytes,
        minimum_bridge_version="0.2.0",
        routing_rules=(("edocat", "alfresco", ("list_items", "search_items", "read_document")),) if routing else (),
    )


def test_list_items_routes_edocat_low_level_operation_to_alfresco() -> None:
    settings = _settings(routing=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return _health_response()
        if request.method == "GET":
            assert request.url.path == "/bridge/wfx/connections/alfresco"
            return httpx.Response(200, json={"ok": True, "data": {"auth": {"required": False}}})
        assert json.loads(request.content) == {"path": "alfresco:/Shared", "auth": None}
        return httpx.Response(200, json={"ok": True, "data": {"connection": "alfresco", "provider": "alfresco", "items": [
            {"name": "Document.pdf", "path": "/internal/path", "is_folder": False}
        ]}})

    bridge = BridgeClient(settings, httpx.MockTransport(handler))
    result = bridge.list_items("edocat:/Shared")

    assert result["data"]["connection"] == "edocat"
    assert result["data"]["items"][0]["path"] == "edocat:/Shared/Document.pdf"
    assert result["data"]["routing"] == {
        "requested_connection": "edocat",
        "execution_connection": "alfresco",
        "mode": "configured_low_level",
    }


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


def test_search_items_recursively_lists_with_one_connection_lookup_and_auth() -> None:
    settings = _settings()
    requests: list[tuple[str, str, dict[str, object] | None]] = []

    def bridge_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return _health_response()
        if request.method == "GET":
            requests.append((request.method, request.url.path, None))
            return httpx.Response(
                200,
                json={"ok": True, "data": {"mount": "alfresco:/", "auth": {"required": True, "credential_id": "company/dms"}}},
            )
        body = json.loads(request.content)
        requests.append((request.method, request.url.path, body))
        assert request.url.path == "/bridge/wfx/list"
        assert request.headers["X-VFS-Correlation-ID"] == "123e4567-e89b-12d3-a456-426614174099"
        assert body["auth"] == {"mode": "credentials", "credential_id": "company/dms"}
        if body["path"] == "alfresco:/projects":
            return httpx.Response(200, json={"ok": True, "data": {"provider": "alfresco", "items": [
                {"name": "docs", "path": "/Agenda/Stránky/deals/documentLibrary/projects", "is_folder": True},
                {"name": "unrelated.txt", "path": "/projects/unrelated.txt", "is_folder": False},
            ]}})
        assert body["path"] == "alfresco:/projects/docs"
        return httpx.Response(200, json={"ok": True, "data": {"provider": "alfresco", "items": [
            {"id": "1", "name": "22080-5-PS368-D-TZ-01_3.pdf", "path": "/Agenda/Stránky/deals/documentLibrary/projects/docs", "is_folder": False},
        ]}})

    bridge = BridgeClient(settings, httpx.MockTransport(bridge_handler))
    correlation_id = "123e4567-e89b-12d3-a456-426614174099"
    with correlation_scope(correlation_id):
        result = bridge.search_items("alfresco:/projects", " -tz- ", 15)

    assert result["data"]["items"][0]["path"] == "alfresco:/projects/docs/22080-5-PS368-D-TZ-01_3.pdf"
    assert result["data"]["search"]["folders_scanned"] == 2
    assert result["data"]["search"]["complete"] is True
    assert [request[1] for request in requests].count("/bridge/wfx/connections/alfresco") == 1
    assert all(request[1] != "/bridge/wfx/search" for request in requests)


def test_search_items_first_matches_stops_traversal_at_result_limit() -> None:
    settings = _settings()
    listed_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return _health_response()
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"ok": True, "data": {"mount": "alfresco:/", "auth": {"required": False}}},
            )
        path = json.loads(request.content)["path"]
        listed_paths.append(path)
        if path == "alfresco:/root":
            return httpx.Response(200, json={"ok": True, "data": {"items": [
                {"name": f"folder-{index}", "is_folder": True} for index in range(10)
            ]}})
        return httpx.Response(200, json={"ok": True, "data": {"items": [
            {"name": f"result-{path.rsplit('-', 1)[-1]}.pdf", "is_folder": False}
        ]}})

    bridge = BridgeClient(settings, httpx.MockTransport(handler))
    result = bridge.search_items("alfresco:/root", "result", max_results=2)

    assert result["data"]["returned"] == 2
    assert result["data"]["total"] is None
    assert result["data"]["truncated"] is True
    assert result["data"]["search"]["mode"] == "first_matches"
    assert result["data"]["search"]["complete"] is False
    assert result["data"]["search"]["reason"] == "result_limit"
    assert len(listed_paths) < 11


def test_search_items_exhaustive_keeps_exact_total() -> None:
    settings = _settings()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return _health_response()
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"ok": True, "data": {"mount": "alfresco:/", "auth": {"required": False}}},
            )
        path = json.loads(request.content)["path"]
        if path == "alfresco:/root":
            return httpx.Response(200, json={"ok": True, "data": {"items": [
                {"name": f"folder-{index}", "is_folder": True} for index in range(4)
            ]}})
        return httpx.Response(200, json={"ok": True, "data": {"items": [
            {"name": f"result-{path.rsplit('-', 1)[-1]}.pdf", "is_folder": False}
        ]}})

    bridge = BridgeClient(settings, httpx.MockTransport(handler))
    result = bridge.search_items(
        "alfresco:/root", "result", max_results=2, search_mode="exhaustive"
    )

    assert result["data"]["returned"] == 2
    assert result["data"]["total"] == 4
    assert result["data"]["truncated"] is True
    assert result["data"]["search"]["mode"] == "exhaustive"
    assert result["data"]["search"]["complete"] is True
    assert result["data"]["search"]["reason"] is None


def test_search_items_routes_edocat_tree_through_alfresco_and_preserves_public_mount() -> None:
    settings = _settings(routing=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return _health_response()
        if request.method == "GET":
            assert request.url.path == "/bridge/wfx/connections/alfresco"
            return httpx.Response(
                200,
                json={"ok": True, "data": {"mount": "alfresco:/", "auth": {"required": False}}},
            )
        body = json.loads(request.content)
        assert body["path"] == "alfresco:/Projects"
        return httpx.Response(200, json={"ok": True, "data": {"provider": "alfresco", "items": [
            {"name": "Report-TZ-01.pdf", "is_folder": False}
        ]}})

    bridge = BridgeClient(settings, httpx.MockTransport(handler))
    result = bridge.search_items("edocat:/Projects", "-TZ-")

    assert result["data"]["connection"] == "edocat"
    assert result["data"]["provider"] == "alfresco"
    assert result["data"]["items"][0]["path"] == "edocat:/Projects/Report-TZ-01.pdf"
    assert result["data"]["routing"]["execution_connection"] == "alfresco"


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


def test_search_metadata_forwards_field_value_path_and_auth() -> None:
    settings = _settings()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return _health_response()
        if request.method == "GET":
            return httpx.Response(200, json={"ok": True, "data": {"mount": "edocat:/", "auth": {"required": True, "credential_id": "company/dms"}}})
        if request.url.path == "/bridge/wfx/list":
            return httpx.Response(200, json={"ok": True, "data": {"items": []}})
        assert request.url.path == "/bridge/wfx/search-metadata"
        assert json.loads(request.content) == {
            "path": "edocat:/",
            "field": "rf:set.CMtaggable",
            "value": "nod68-dps",
            "max_results": 20,
            "files_only": False,
            "auth": {"mode": "credentials", "credential_id": "company/dms"},
        }
        return httpx.Response(200, json={"ok": True, "data": {"items": []}})

    bridge = BridgeClient(settings, httpx.MockTransport(handler))

    assert bridge.search_metadata("edocat:/", " rf:set.CMtaggable ", " nod68-dps ")["ok"] is True


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
                json={"ok": True, "data": {"provider": "alfresco", "items": [{
                    "id": "1",
                    "name": "22 080 - UNI_Novy odolejovac bl. 68",
                    "path": "/03 zakázky v realizaci/22 080 - UNI_Novy odolejovac bl. 68",
                    "is_folder": False,
                }]}},
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

    result = bridge.search_items("alfresco:/03 zakázky v realizaci", "22 080")

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


@pytest.mark.parametrize("value", [0, 1001, True, 1.5])
def test_search_items_rejects_invalid_limit(value: object) -> None:
    settings = _settings()
    bridge = BridgeClient(settings, httpx.MockTransport(lambda _request: _health_response()))

    with pytest.raises(ValueError, match="max_results"):
        bridge.search_items("alfresco:/", "query", value)  # type: ignore[arg-type]


def test_search_items_rejects_unknown_search_mode() -> None:
    settings = _settings()
    bridge = BridgeClient(settings, httpx.MockTransport(lambda _request: _health_response()))

    with pytest.raises(ValueError, match="search_mode"):
        bridge.search_items("alfresco:/", "query", search_mode="fast")


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


def test_read_document_routes_edocat_content_through_alfresco() -> None:
    settings = _settings(max_document_bytes=100, routing=True)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return _health_response()
        if request.method == "GET":
            assert request.url.path == "/bridge/wfx/connections/alfresco"
            return httpx.Response(200, json={"ok": True, "data": {"auth": {"required": False}}})
        assert json.loads(request.content)["path"] == "alfresco:/Shared/readme.txt"
        return httpx.Response(
            200,
            content=b"routed",
            headers={"X-Bridge-Raw-Content": "1", "Content-Type": "text/plain"},
        )

    bridge = BridgeClient(settings, httpx.MockTransport(handler))
    result = bridge.read_document("edocat:/Shared/readme.txt")

    assert result["path"] == "edocat:/Shared/readme.txt"
    assert result["text"] == "routed"
    assert result["routing"]["execution_connection"] == "alfresco"


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


def test_stat_can_be_enabled_in_low_level_router() -> None:
    settings = Settings(
        bridge_url="http://127.0.0.1:8765",
        timeout_seconds=30,
        max_document_bytes=100,
        minimum_bridge_version="0.2.0",
        routing_rules=(("edocat", "alfresco", ("get_item_info",)),),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return _health_response()
        if request.method == "GET":
            return httpx.Response(200, json={"ok": True, "data": {"auth": {"required": False}}})
        assert json.loads(request.content)["path"] == "alfresco:/Shared/report.pdf"
        return httpx.Response(200, json={"ok": True, "data": {"name": "report.pdf"}})

    bridge = BridgeClient(settings, httpx.MockTransport(handler))
    result = bridge.stat("edocat:/Shared/report.pdf")

    assert result["data"]["path"] == "edocat:/Shared/report.pdf"
    assert result["data"]["routing"]["execution_connection"] == "alfresco"


def test_rejects_unsupported_bridge_version() -> None:
    settings = Settings(
        bridge_url="http://127.0.0.1:8765",
        timeout_seconds=30,
        max_document_bytes=100,
        minimum_bridge_version="2.0.0",
    )
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
