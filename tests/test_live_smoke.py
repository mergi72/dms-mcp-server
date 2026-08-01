from __future__ import annotations

from scripts.run_live_smoke import _navigation_path


def test_navigation_path_joins_connection_root() -> None:
    assert _navigation_path("alfresco:/", "Shared") == "alfresco:/Shared"


def test_navigation_path_joins_nested_folder() -> None:
    assert _navigation_path("alfresco:/Shared", "Folder") == "alfresco:/Shared/Folder"


def test_navigation_path_normalizes_leading_separator() -> None:
    assert _navigation_path("alfresco:/Shared", "/File.txt") == "alfresco:/Shared/File.txt"
