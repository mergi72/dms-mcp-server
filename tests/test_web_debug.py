from __future__ import annotations

import base64
import hashlib

import pytest

from scripts.web_debug import _safe_result, _validate_request


def test_validate_request_allows_read_only_path_tool() -> None:
    assert _validate_request({"tool": "list_items", "arguments": {"path": " alfresco:/ "}}) == (
        "list_items",
        {"path": "alfresco:/"},
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
