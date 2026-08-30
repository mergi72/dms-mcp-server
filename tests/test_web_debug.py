from __future__ import annotations

import base64
import hashlib

import pytest

from scripts.web_debug import HTML, _safe_result, _validate_browser_headers, _validate_request


def test_validate_request_allows_read_only_path_tool() -> None:
    assert _validate_request({"tool": "list_items", "arguments": {"path": " alfresco:/ "}}) == (
        "list_items",
        {"path": "alfresco:/"},
    )


def test_validate_request_allows_bounded_search() -> None:
    assert _validate_request(
        {"tool": "search_items", "arguments": {"path": " edocat:/ ", "query": " steam ", "max_results": 12}}
    ) == (
        "search_items",
        {"path": "edocat:/", "query": "steam", "max_results": 12, "files_only": True},
    )


def test_validate_request_allows_folders_in_search() -> None:
    assert _validate_request(
        {"tool": "search_items", "arguments": {"path": "edocat:/", "query": "steam", "files_only": False}}
    )[1]["files_only"] is False


def test_validate_request_rejects_non_boolean_files_only() -> None:
    with pytest.raises(ValueError, match="files_only"):
        _validate_request(
            {"tool": "search_items", "arguments": {"path": "edocat:/", "query": "steam", "files_only": "yes"}}
        )


@pytest.mark.parametrize("max_results", [0, 101, True, "20"])
def test_validate_request_rejects_invalid_search_limit(max_results: object) -> None:
    with pytest.raises(ValueError, match="max_results"):
        _validate_request(
            {"tool": "search_items", "arguments": {"path": "edocat:/", "query": "steam", "max_results": max_results}}
        )


def test_validate_request_rejects_unknown_tool() -> None:
    with pytest.raises(ValueError, match="non-read-only"):
        _validate_request({"tool": "delete_item", "arguments": {"path": "alfresco:/x"}})


def test_safe_result_omits_document_content() -> None:
    content = b"secret document bytes"
    result = _safe_result(
        "read_document",
        {"path": "alfresco:/x.pdf", "mime_type": "application/pdf", "content_base64": base64.b64encode(content).decode()},
    )
    assert result == {
        "path": "alfresco:/x.pdf",
        "mime_type": "application/pdf",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_omitted": True,
    }


def test_safe_result_prefers_original_byte_digest() -> None:
    result = _safe_result(
        "read_document",
        {"path": "alfresco:/x.txt", "mime_type": "text/plain", "text": "�", "sha256": "original-digest"},
    )
    assert result["sha256"] == "original-digest"


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "127.0.0.1:8780", "Content-Type": "text/plain"},
        {"Host": "attacker.example", "Content-Type": "application/json"},
        {
            "Host": "127.0.0.1:8780",
            "Content-Type": "application/json",
            "Origin": "https://attacker.example",
        },
    ],
)
def test_browser_headers_reject_cross_site_requests(headers: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        _validate_browser_headers(headers, 8780)


def test_browser_headers_allow_local_json_request() -> None:
    _validate_browser_headers(
        {
            "Host": "localhost:8780",
            "Content-Type": "application/json; charset=utf-8",
            "Origin": "http://localhost:8780",
        },
        8780,
    )


def test_html_offers_response_and_ui_views() -> None:
    assert "MCP Response" in HTML
    assert "UI View" in HTML
    assert "renderUI(tool, payload, request)" in HTML


def test_html_offers_parent_folder_navigation() -> None:
    assert "function parentPath(path)" in HTML
    assert "itemButton('..', parent, true)" in HTML


def test_html_offers_native_search() -> None:
    assert 'onclick="searchTool()"' in HTML
    assert "callTool('search_items'" in HTML


def test_search_ui_uses_public_item_path_verbatim() -> None:
    assert "itemButton(item.name || '<unnamed>', item.path" in HTML
    assert "request.arguments.path.split(':/')[0]" not in HTML
