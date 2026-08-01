from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.debug_dms import _document_bytes, _health_status, _item_label


def test_item_label_formats_folder() -> None:
    assert _item_label({"name": "Shared", "is_folder": True}) == "Shared/"


def test_item_label_formats_file_metadata() -> None:
    item = {"name": "report.pdf", "size": 42, "mime_type": "application/pdf"}
    assert _item_label(item) == "report.pdf  [42 B, application/pdf]"


def test_document_bytes_decodes_text() -> None:
    assert _document_bytes({"text": "Příliš žluťoučký"}) == "Příliš žluťoučký".encode()


def test_document_bytes_decodes_base64() -> None:
    assert _document_bytes({"content_base64": base64.b64encode(b"binary").decode()}) == b"binary"


def test_document_bytes_rejects_missing_content() -> None:
    with pytest.raises(RuntimeError, match="no text or base64"):
        _document_bytes({})


def test_health_is_ok_unless_bridge_explicitly_reports_failure() -> None:
    assert _health_status({"version": "1.0.1"}) == "OK"
    assert _health_status({"ok": True}) == "OK"
    assert _health_status({"ok": False}) == "ERROR"


def test_debug_script_can_run_directly() -> None:
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(project_root / "scripts" / "debug_dms.py"), "--help"],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Safely display a bounded DMS tree" in result.stdout
