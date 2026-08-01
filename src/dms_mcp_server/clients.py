from __future__ import annotations

import base64
from typing import Any

import httpx

from dms_mcp_server.config import Settings


class UpstreamError(RuntimeError):
    """A local bridge or broker request failed."""


class BrokerClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(
            base_url=settings.broker_url,
            timeout=settings.timeout_seconds,
            transport=transport,
        )

    def resolve(self, credential_id: str) -> dict[str, Any]:
        try:
            response = self._client.post(
                "/auth/resolve",
                json={"auth": {"mode": "windows", "target": credential_id, "required": True}},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise UpstreamError(f"Credential Broker request failed: {exc}") from exc
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
        )

    def _auth(self, credential_id: str | None) -> dict[str, Any] | None:
        resolved_id = credential_id or self._settings.default_credential_id
        return self._broker.resolve(resolved_id) if resolved_id else None

    def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, **kwargs)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise UpstreamError(f"DMS Provider Bridge request failed: {exc}") from exc
        if payload.get("ok") is False:
            raise UpstreamError(str(payload.get("message") or "DMS Provider Bridge operation failed."))
        return payload

    def health(self) -> dict[str, Any]:
        return self._json("GET", "/health")

    def list_connections(self) -> dict[str, Any]:
        return self._json("GET", "/bridge/wfx/connections")

    def list_items(self, path: str, credential_id: str | None = None) -> dict[str, Any]:
        return self._json(
            "POST",
            "/bridge/wfx/list",
            json={"path": path, "auth": self._auth(credential_id)},
        )

    def stat(self, path: str, credential_id: str | None = None) -> dict[str, Any]:
        return self._json(
            "POST",
            "/bridge/wfx/stat",
            json={"path": path, "auth": self._auth(credential_id)},
        )

    def read_document(self, path: str, credential_id: str | None = None) -> dict[str, Any]:
        request = self._client.build_request(
            "POST",
            "/bridge/wfx/download-raw",
            json={"path": path, "auth": self._auth(credential_id)},
        )
        response: httpx.Response | None = None
        try:
            response = self._client.send(request, stream=True)
            response.raise_for_status()
            if response.headers.get("X-Bridge-Raw-Content") != "1":
                payload = response.json()
                raise UpstreamError(str(payload.get("message") or "Document download failed."))
            declared_size = response.headers.get("Content-Length")
            if declared_size and int(declared_size) > self._settings.max_document_bytes:
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

        result: dict[str, Any] = {"path": path, "mime_type": media_type, "size": len(content)}
        if media_type.startswith("text/") or media_type in {"application/json", "application/xml"}:
            result["text"] = content.decode("utf-8", errors="replace")
        else:
            result["content_base64"] = base64.b64encode(content).decode("ascii")
            result["encoding"] = "base64"
        return result
