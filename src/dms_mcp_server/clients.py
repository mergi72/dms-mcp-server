from __future__ import annotations

import base64
import hashlib
from typing import Any
from urllib.parse import quote

import httpx

from dms_mcp_server.config import Settings
from dms_mcp_server.compatibility import require_supported_bridge_version
from dms_mcp_server.tracing import current_correlation_headers


class UpstreamError(RuntimeError):
    """The local Provider Bridge request failed."""


class BridgeClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._client = httpx.Client(
            base_url=settings.bridge_url,
            timeout=settings.timeout_seconds,
            transport=transport,
            trust_env=False,
            headers={"X-VFS-Component": "mcp"},
        )
        self._compatibility_checked = False

    def _ensure_compatible(self) -> dict[str, Any]:
        if self._compatibility_checked:
            return {}
        health = self._json("GET", "/health")
        version = health.get("version")
        if not isinstance(version, str) or not version.strip():
            raise UpstreamError("Bridge health response has no version.")
        try:
            require_supported_bridge_version(version, self._settings.minimum_bridge_version)
        except RuntimeError as exc:
            raise UpstreamError(str(exc)) from exc
        self._compatibility_checked = True
        return health

    @staticmethod
    def _connection_name(path: str) -> str | None:
        if ":/" not in path:
            return None
        name, _separator, _remainder = path.partition(":/")
        normalized = name.strip()
        return normalized or None

    @staticmethod
    def _public_search_path(
        mount: str,
        search_root: str,
        item_path: str,
        root_names: set[str],
    ) -> str | None:
        segments = [segment for segment in item_path.replace("\\", "/").split("/") if segment]
        root_segments = [segment for segment in search_root.replace("\\", "/").split("/") if segment]
        start: int | None = None
        if root_segments:
            folded = [segment.casefold() for segment in segments]
            anchor = [segment.casefold() for segment in root_segments]
            for index in range(len(segments) - len(anchor) + 1):
                if folded[index : index + len(anchor)] == anchor:
                    start = index
                    break
        else:
            names = {name.casefold() for name in root_names}
            start = next((index for index, segment in enumerate(segments) if segment.casefold() in names), None)
        if start is None:
            return None
        relative = "/" + "/".join(segments[start:])
        return f"{mount.rstrip('/')}{relative}"

    def connection_detail(self, connection_name: str) -> dict[str, Any]:
        encoded_name = quote(connection_name.strip(), safe="")
        return self._json("GET", f"/bridge/wfx/connections/{encoded_name}")

    def _auth_for_path(self, path: str) -> dict[str, Any] | None:
        connection_name = self._connection_name(path)
        if connection_name is None:
            return None

        detail = self.connection_detail(connection_name)
        data = detail.get("data")
        auth = data.get("auth") if isinstance(data, dict) else None
        if not isinstance(auth, dict):
            raise UpstreamError(f"Connection {connection_name!r} has no auth contract in bridge.")

        for key in ("credential_id", "credentialId", "target", "targetBase", "target_base"):
            credential_id = auth.get(key)
            if isinstance(credential_id, str) and credential_id.strip():
                return {"mode": "credentials", "credential_id": credential_id.strip()}
        if auth.get("required") is False or str(auth.get("mode") or "").lower() == "none":
            return None
        raise UpstreamError(f"Connection {connection_name!r} requires auth but has no credential_id.")

    def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        headers.update(current_correlation_headers())
        try:
            response = self._client.request(method, path, headers=headers, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise UpstreamError(f"DMS Provider Bridge request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise UpstreamError("DMS Provider Bridge returned a non-object JSON response.")
        if payload.get("ok") is False:
            raise UpstreamError(str(payload.get("message") or "DMS Provider Bridge operation failed."))
        return payload

    def health(self) -> dict[str, Any]:
        health = self._json("GET", "/health")
        version = health.get("version")
        if not isinstance(version, str) or not version.strip():
            raise UpstreamError("Bridge health response has no version.")
        try:
            require_supported_bridge_version(version, self._settings.minimum_bridge_version)
        except RuntimeError as exc:
            raise UpstreamError(str(exc)) from exc
        self._compatibility_checked = True
        return health

    def list_connections(self) -> dict[str, Any]:
        self._ensure_compatible()
        return self._json("GET", "/bridge/wfx/connections")

    def list_items(self, path: str) -> dict[str, Any]:
        self._ensure_compatible()
        return self._json(
            "POST",
            "/bridge/wfx/list",
            json={"path": path, "auth": self._auth_for_path(path)},
        )

    def search_items(self, path: str, query: str, max_results: int = 20, files_only: bool = True) -> dict[str, Any]:
        self._ensure_compatible()
        if not isinstance(max_results, int) or isinstance(max_results, bool) or not 1 <= max_results <= 100:
            raise ValueError("max_results must be an integer between 1 and 100.")
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty.")
        if not isinstance(files_only, bool):
            raise ValueError("files_only must be a boolean.")
        auth = self._auth_for_path(path)
        payload = self._json(
            "POST",
            "/bridge/wfx/search",
            json={
                "path": path,
                "query": normalized_query,
                "max_results": max_results,
                "files_only": files_only,
                "auth": auth,
            },
        )
        connection_name = self._connection_name(path)
        data = payload.get("data")
        items = data.get("items") if isinstance(data, dict) else None
        if connection_name and isinstance(items, list):
            detail_data = self.connection_detail(connection_name).get("data")
            mount = detail_data.get("mount") if isinstance(detail_data, dict) else None
            if not isinstance(mount, str) or not mount.endswith(":/"):
                raise UpstreamError(f"Connection {connection_name!r} has no valid mount in bridge.")
            search_root = data.get("path") if isinstance(data.get("path"), str) else path.partition(":")[2]
            search_root = search_root or "/"
            root_names: set[str] = set()
            if search_root == "/":
                root_payload = self._json(
                    "POST",
                    "/bridge/wfx/list",
                    json={"path": mount, "auth": auth},
                )
                root_data = root_payload.get("data")
                root_items = root_data.get("items") if isinstance(root_data, dict) else None
                if isinstance(root_items, list):
                    root_names = {
                        str(item["name"])
                        for item in root_items
                        if isinstance(item, dict) and isinstance(item.get("name"), str)
                    }
            for item in items:
                item_path = item.get("path") if isinstance(item, dict) else None
                if isinstance(item_path, str) and item_path.startswith("/"):
                    public_path = self._public_search_path(mount, search_root, item_path, root_names)
                    if public_path is None:
                        item.pop("path", None)
                        item["path_unresolved"] = True
                    else:
                        item["path"] = public_path
        return payload

    def open_share_url(self, share_url: str, connection: str = "auto") -> dict[str, Any]:
        self._ensure_compatible()
        normalized_url = share_url.strip()
        if not normalized_url or len(normalized_url) > 4096:
            raise ValueError("share_url must be a non-empty URL up to 4096 characters.")
        try:
            parsed_url = httpx.URL(normalized_url)
        except Exception as exc:
            raise ValueError("share_url must be a valid HTTP or HTTPS URL.") from exc
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.host:
            raise ValueError("share_url must be a valid HTTP or HTTPS URL.")

        normalized_connection = connection.strip().rstrip(":/")
        if normalized_connection.casefold() == "auto":
            driver = "edocat" if parsed_url.path.startswith("/share/page/browse/DIR-") else "alfresco"
            registry_payload = self.list_connections().get("data")
            registered = registry_payload.get("connections") if isinstance(registry_payload, dict) else None
            normalized_connection = next(
                (
                    str(item["name"])
                    for item in registered
                    if isinstance(item, dict)
                    and str(item.get("driver") or "").casefold() == driver
                    and item.get("registered") is True
                    and isinstance(item.get("name"), str)
                ),
                "",
            ) if isinstance(registered, list) else ""
            if not normalized_connection:
                raise UpstreamError(f"No registered {driver} connection is available for this Share URL.")
        if not normalized_connection or any(character in normalized_connection for character in "\\/"):
            raise ValueError("connection must be a connection name, not a path.")

        resolved = self._json(
            "POST",
            "/bridge/wfx/resolve-share-url",
            json={"share_url": normalized_url, "connection": normalized_connection},
        )
        resolved_data = resolved.get("data")
        resolved_path = resolved_data.get("path") if isinstance(resolved_data, dict) else None
        if not isinstance(resolved_path, str) or not resolved_path.strip():
            raise UpstreamError("DMS Provider Bridge did not return a resolved share URL path.")

        item_response = self.stat(resolved_path)
        item = item_response.get("data")
        listing = self.list_items(resolved_path).get("data") if isinstance(item, dict) and item.get("is_folder") is True else None
        return {
            "ok": True,
            "data": {
                "resolved": resolved_data,
                "requested_connection": normalized_connection,
                "item": item,
                "listing": listing,
            },
        }

    def stat(self, path: str) -> dict[str, Any]:
        self._ensure_compatible()
        return self._json(
            "POST",
            "/bridge/wfx/stat",
            json={"path": path, "auth": self._auth_for_path(path)},
        )

    def read_document(self, path: str) -> dict[str, Any]:
        self._ensure_compatible()
        request = self._client.build_request(
            "POST",
            "/bridge/wfx/download-raw",
            json={"path": path, "auth": self._auth_for_path(path)},
            headers=current_correlation_headers(),
        )
        response: httpx.Response | None = None
        try:
            response = self._client.send(request, stream=True)
            response.raise_for_status()
            if response.headers.get("X-Bridge-Raw-Content") != "1":
                payload = response.json()
                if not isinstance(payload, dict):
                    raise UpstreamError("Bridge download error response is not a JSON object.")
                raise UpstreamError(str(payload.get("message") or "Document download failed."))
            declared_size = response.headers.get("Content-Length")
            if declared_size:
                try:
                    parsed_size = int(declared_size)
                except ValueError as exc:
                    raise UpstreamError("Bridge returned an invalid Content-Length header.") from exc
                if parsed_size < 0:
                    raise UpstreamError("Bridge returned a negative Content-Length header.")
                if parsed_size > self._settings.max_document_bytes:
                    raise UpstreamError(
                        f"Document exceeds the {self._settings.max_document_bytes} byte MCP read limit."
                    )
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > self._settings.max_document_bytes:
                    raise UpstreamError(
                        f"Document exceeds the {self._settings.max_document_bytes} byte MCP read limit."
                    )
                chunks.append(chunk)
            content = b"".join(chunks)
            media_type = response.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0]
        except httpx.HTTPError as exc:
            raise UpstreamError(f"DMS Provider Bridge request failed: {exc}") from exc
        finally:
            if response is not None:
                response.close()

        result: dict[str, Any] = {
            "path": path,
            "mime_type": media_type,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        if media_type.startswith("text/") or media_type in {"application/json", "application/xml"}:
            result["text"] = content.decode("utf-8", errors="replace")
        else:
            result["content_base64"] = base64.b64encode(content).decode("ascii")
            result["encoding"] = "base64"
        return result
