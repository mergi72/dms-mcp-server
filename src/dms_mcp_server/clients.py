from __future__ import annotations

import base64
import hashlib
from typing import Any
from urllib.parse import quote

import httpx

from dms_mcp_server.config import Settings
from dms_mcp_server.compatibility import require_supported_bridge_version


class UpstreamError(RuntimeError):
    """A local bridge or broker request failed."""


class BrokerClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(
            base_url=settings.broker_url,
            timeout=settings.timeout_seconds,
            transport=transport,
            trust_env=False,
        )

    def resolve(self, credential_id: str) -> dict[str, Any]:
        try:
            response = self._client.post(
                "/credentials/resolve",
                json={"auth": {"mode": "windows", "target": credential_id, "required": True}},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise UpstreamError(f"Credential Broker request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise UpstreamError("Credential Broker returned a non-object JSON response.")
        if payload.get("ok") is not True or not isinstance(payload.get("auth"), dict):
            raise UpstreamError(str(payload.get("message") or "Credential Broker did not resolve credentials."))

        auth = dict(payload["auth"])
        # Bridge accepts credentials carrying either username/password or token.
        auth["mode"] = "credentials"
        return auth


class BridgeClient:
    def __init__(
        self,
        settings: Settings,
        broker: BrokerClient,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._broker = broker
        self._client = httpx.Client(
            base_url=settings.bridge_url,
            timeout=settings.timeout_seconds,
            transport=transport,
            trust_env=False,
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
                return self._broker.resolve(credential_id.strip())
        if auth.get("required") is False or str(auth.get("mode") or "").lower() == "none":
            return None
        raise UpstreamError(f"Connection {connection_name!r} requires auth but has no credential_id.")

    def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, **kwargs)
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
        payload = self._json(
            "POST",
            "/bridge/wfx/search",
            json={
                "path": path,
                "query": normalized_query,
                "max_results": max_results,
                "files_only": files_only,
                "auth": self._auth_for_path(path),
            },
        )
        connection_name = self._connection_name(path)
        data = payload.get("data")
        items = data.get("items") if isinstance(data, dict) else None
        if connection_name and isinstance(items, list):
            for item in items:
                item_path = item.get("path") if isinstance(item, dict) else None
                if isinstance(item_path, str) and item_path.startswith("/"):
                    item["path"] = f"{connection_name}:{item_path}"
        return payload

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
