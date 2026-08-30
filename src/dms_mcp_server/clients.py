from __future__ import annotations

import base64
import hashlib
import logging
from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from time import perf_counter
from typing import Any
from urllib.parse import quote

import httpx

from dms_mcp_server.config import Settings
from dms_mcp_server.compatibility import require_supported_bridge_version
from dms_mcp_server.tracing import current_correlation_headers


LOGGER = logging.getLogger("mcp")


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

    def _route_path(self, operation: str, path: str) -> tuple[str, str, str, bool]:
        requested_connection = self._connection_name(path)
        if requested_connection is None:
            return path, "", "", False
        execution_connection = self._settings.route_connection(operation, requested_connection)
        routed = execution_connection.casefold() != requested_connection.casefold()
        if not routed:
            return path, requested_connection, execution_connection, False
        _mount, separator, remainder = path.partition(":/")
        if not separator:
            raise ValueError("path must use the connection:/path format.")
        return f"{execution_connection}:/{remainder}", requested_connection, execution_connection, True

    @staticmethod
    def _routing_data(requested_connection: str, execution_connection: str, routed: bool) -> dict[str, Any]:
        return {
            "requested_connection": requested_connection,
            "execution_connection": execution_connection,
            "mode": "configured_low_level" if routed else "requested_connection",
        }

    @staticmethod
    def _present_routed_listing(
        payload: dict[str, Any],
        requested_path: str,
        requested_connection: str,
        execution_connection: str,
    ) -> dict[str, Any]:
        data = payload.get("data")
        if not isinstance(data, dict):
            return payload
        items = data.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                if isinstance(name, str) and name.strip():
                    item["path"] = f"{requested_path.rstrip('/')}/{name.strip()}"
        data["connection"] = requested_connection
        data["path"] = requested_path.partition(":")[2] or "/"
        data["routing"] = BridgeClient._routing_data(requested_connection, execution_connection, True)
        metadata = payload.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["connection"] = requested_connection
            metadata["execution_connection"] = execution_connection
            metadata["routing"] = "configured_low_level"
        return payload

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

    def _rewrite_search_item_paths(
        self,
        requested_path: str,
        payload: dict[str, Any],
        auth: dict[str, Any] | None,
    ) -> dict[str, Any]:
        connection_name = self._connection_name(requested_path)
        data = payload.get("data")
        items = data.get("items") if isinstance(data, dict) else None
        if not connection_name or not isinstance(items, list):
            return payload
        detail_data = self.connection_detail(connection_name).get("data")
        mount = detail_data.get("mount") if isinstance(detail_data, dict) else None
        if not isinstance(mount, str) or not mount.endswith(":/"):
            raise UpstreamError(f"Connection {connection_name!r} has no valid mount in bridge.")
        search_root = data.get("path") if isinstance(data.get("path"), str) else requested_path.partition(":")[2]
        search_root = search_root or "/"
        root_names: set[str] = set()
        if search_root == "/":
            root_payload = self._json("POST", "/bridge/wfx/list", json={"path": mount, "auth": auth})
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
        execution_path, requested_connection, execution_connection, routed = self._route_path("list_items", path)
        payload = self._json(
            "POST",
            "/bridge/wfx/list",
            json={"path": execution_path, "auth": self._auth_for_path(execution_path)},
        )
        if routed:
            return self._present_routed_listing(
                payload, path, requested_connection, execution_connection,
            )
        return payload

    @staticmethod
    def _join_public_path(mount: str, parent_path: str, item: dict[str, Any]) -> str | None:
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        # The stable list contract identifies the current folder by the request
        # path and each child by name. Providers differ in whether item.path is
        # the parent, the child, or an internal repository path, so it is not
        # authoritative for recursive VFS traversal.
        return f"{parent_path.rstrip('/')}/{name.strip()}"

    def _list_items_with_auth(
        self,
        path: str,
        auth: dict[str, Any] | None,
        correlation_headers: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        return self._json(
            "POST",
            "/bridge/wfx/list",
            headers=correlation_headers,
            timeout=timeout_seconds,
            json={"path": path, "auth": auth},
        )

    def search_items(
        self,
        path: str,
        query: str,
        max_results: int = 20,
        files_only: bool = True,
        search_mode: str = "first_matches",
    ) -> dict[str, Any]:
        self._ensure_compatible()
        if (
            not isinstance(max_results, int)
            or isinstance(max_results, bool)
            or not 1 <= max_results <= self._settings.search_max_results
        ):
            raise ValueError(
                f"max_results must be an integer between 1 and {self._settings.search_max_results}."
            )
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty.")
        if not isinstance(files_only, bool):
            raise ValueError("files_only must be a boolean.")
        if search_mode not in {"first_matches", "exhaustive"}:
            raise ValueError("search_mode must be 'first_matches' or 'exhaustive'.")
        execution_path, requested_connection, execution_connection, routed = self._route_path("search_items", path)
        connection_name = self._connection_name(execution_path)
        if connection_name is None:
            raise ValueError("path must use the connection:/path format.")
        detail = self.connection_detail(connection_name)
        detail_data = detail.get("data")
        mount = detail_data.get("mount") if isinstance(detail_data, dict) else None
        if not isinstance(mount, str) or not mount.endswith(":/"):
            raise UpstreamError(f"Connection {connection_name!r} has no valid mount in bridge.")
        auth_contract = detail_data.get("auth") if isinstance(detail_data, dict) else None
        if not isinstance(auth_contract, dict):
            raise UpstreamError(f"Connection {connection_name!r} has no auth contract in bridge.")
        auth: dict[str, Any] | None = None
        for key in ("credential_id", "credentialId", "target", "targetBase", "target_base"):
            credential_id = auth_contract.get(key)
            if isinstance(credential_id, str) and credential_id.strip():
                auth = {"mode": "credentials", "credential_id": credential_id.strip()}
                break
        if auth is None and not (
            auth_contract.get("required") is False
            or str(auth_contract.get("mode") or "").lower() == "none"
        ):
            raise UpstreamError(f"Connection {connection_name!r} requires auth but has no credential_id.")

        needle = normalized_query if self._settings.search_case_sensitive else normalized_query.casefold()
        start_path = execution_path if execution_path.endswith(":/") else execution_path.rstrip("/")
        pending: deque[tuple[str, int]] = deque([(start_path or mount, 0)])
        visited: set[str] = set()
        matches: list[dict[str, Any]] = []
        matched_paths: set[str] = set()
        warnings: list[str] = []
        started = perf_counter()
        deadline = started + self._settings.search_timeout_seconds
        provider: str | None = None
        complete = True
        correlation_headers = current_correlation_headers()

        executor = ThreadPoolExecutor(max_workers=self._settings.search_concurrency)
        futures: dict[Future[dict[str, Any]], tuple[str, int]] = {}

        def submit_available() -> None:
            nonlocal complete
            while pending and len(futures) < self._settings.search_concurrency:
                folder_path, depth = pending.popleft()
                key = folder_path.casefold()
                if key in visited:
                    continue
                if len(visited) >= self._settings.search_max_folders:
                    if "Maximum folder count reached." not in warnings:
                        warnings.append("Maximum folder count reached.")
                    complete = False
                    pending.clear()
                    return
                remaining = deadline - perf_counter()
                if remaining <= 0:
                    return
                visited.add(key)
                future = executor.submit(
                    self._list_items_with_auth,
                    folder_path,
                    auth,
                    correlation_headers,
                    min(self._settings.timeout_seconds, remaining),
                )
                futures[future] = (folder_path, depth)

        try:
            submit_available()
            while futures:
                remaining = deadline - perf_counter()
                if remaining <= 0:
                    warnings.append("Search timeout reached.")
                    complete = False
                    break
                done, _pending_futures = wait(
                    tuple(futures), timeout=remaining, return_when=FIRST_COMPLETED
                )
                if not done:
                    warnings.append("Search timeout reached.")
                    complete = False
                    break
                for future in done:
                    folder_path, depth = futures.pop(future)
                    try:
                        payload = future.result()
                    except Exception as exc:
                        warnings.append(f"Could not list {folder_path}: {type(exc).__name__}")
                        complete = False
                        continue
                    data = payload.get("data")
                    if not isinstance(data, dict):
                        warnings.append(f"Invalid list response for {folder_path}.")
                        complete = False
                        continue
                    if provider is None and isinstance(data.get("provider"), str):
                        provider = data["provider"]
                    items = data.get("items")
                    if not isinstance(items, list):
                        warnings.append(f"Invalid item list for {folder_path}.")
                        complete = False
                        continue
                    for raw_item in items:
                        if not isinstance(raw_item, dict):
                            continue
                        public_path = self._join_public_path(mount, folder_path, raw_item)
                        if public_path is None:
                            continue
                        item = dict(raw_item)
                        item["path"] = public_path
                        is_folder = item.get("is_folder") is True
                        name = item.get("name")
                        comparable = name if isinstance(name, str) else ""
                        if not self._settings.search_case_sensitive:
                            comparable = comparable.casefold()
                        if needle in comparable and (not files_only or not is_folder):
                            match_key = public_path.casefold()
                            if match_key not in matched_paths:
                                matched_paths.add(match_key)
                                matches.append(item)
                        if is_folder and depth < self._settings.search_max_depth:
                            pending.append((public_path, depth + 1))
                        elif is_folder and depth >= self._settings.search_max_depth:
                            complete = False
                            if "Maximum search depth reached." not in warnings:
                                warnings.append("Maximum search depth reached.")
                if search_mode == "first_matches" and len(matches) >= max_results:
                    complete = False
                    break
                submit_available()
            if not futures and pending:
                complete = False
                if perf_counter() >= deadline:
                    if "Search timeout reached." not in warnings:
                        warnings.append("Search timeout reached.")
                elif "Maximum folder count reached." not in warnings:
                    warnings.append("Maximum folder count reached.")
        finally:
            for future in futures:
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)

        unique: dict[str, dict[str, Any]] = {}
        for item in matches:
            unique.setdefault(str(item["path"]).casefold(), item)
        ordered = sorted(unique.values(), key=lambda item: str(item["path"]).casefold())
        returned = ordered[:max_results]
        result_limit_reached = search_mode == "first_matches" and not complete and len(ordered) >= max_results
        if routed:
            execution_prefix = f"{execution_connection}:/"
            requested_prefix = f"{requested_connection}:/"
            for item in returned:
                item_path = item.get("path")
                if isinstance(item_path, str) and item_path.casefold().startswith(execution_prefix.casefold()):
                    item["path"] = requested_prefix + item_path[len(execution_prefix):]
        duration_ms = round((perf_counter() - started) * 1000)
        LOGGER.info(
            "mcp_recursive_search path=%r query=%r search_mode=%s folders_scanned=%d total=%r returned=%d complete=%s duration_ms=%d",
            path, normalized_query, search_mode, len(visited), None if result_limit_reached else len(ordered), len(returned), complete, duration_ms,
        )
        return {
            "ok": True,
            "data": {
                "connection": requested_connection or connection_name,
                "path": path.partition(":")[2] or "/",
                "query": normalized_query,
                "total": None if result_limit_reached else len(ordered),
                "returned": len(returned),
                "items": returned,
                "truncated": len(ordered) > len(returned) or not complete,
                "provider": provider,
                "search": {
                    "mode": search_mode,
                    "folders_scanned": len(visited),
                    "duration_ms": duration_ms,
                    "complete": complete,
                    "reason": "result_limit" if result_limit_reached else None,
                    "warnings": warnings,
                },
                "routing": self._routing_data(
                    requested_connection or connection_name,
                    execution_connection or connection_name,
                    routed,
                ),
            },
            "metadata": {
                "connection": requested_connection or connection_name,
                "execution_connection": execution_connection or connection_name,
                "provider": provider,
                "routing": "configured_low_level" if routed else "requested_connection",
            },
        }

    def search_metadata(self, path: str, field: str, value: str, max_results: int = 20, files_only: bool = False) -> dict[str, Any]:
        self._ensure_compatible()
        if not isinstance(max_results, int) or isinstance(max_results, bool) or not 1 <= max_results <= 100:
            raise ValueError("max_results must be an integer between 1 and 100.")
        normalized_field = field.strip()
        normalized_value = value.strip()
        if not normalized_field:
            raise ValueError("field must not be empty.")
        if not normalized_value:
            raise ValueError("value must not be empty.")
        if not isinstance(files_only, bool):
            raise ValueError("files_only must be a boolean.")
        auth = self._auth_for_path(path)
        payload = self._json(
            "POST",
            "/bridge/wfx/search-metadata",
            json={
                "path": path,
                "field": normalized_field,
                "value": normalized_value,
                "max_results": max_results,
                "files_only": files_only,
                "auth": auth,
            },
        )
        return self._rewrite_search_item_paths(path, payload, auth)

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
        execution_path, requested_connection, execution_connection, routed = self._route_path("get_item_info", path)
        payload = self._json(
            "POST",
            "/bridge/wfx/stat",
            json={"path": execution_path, "auth": self._auth_for_path(execution_path)},
        )
        if routed:
            data = payload.get("data")
            if isinstance(data, dict):
                data["connection"] = requested_connection
                data["path"] = path
                data["routing"] = self._routing_data(requested_connection, execution_connection, True)
            metadata = payload.setdefault("metadata", {})
            if isinstance(metadata, dict):
                metadata["connection"] = requested_connection
                metadata["execution_connection"] = execution_connection
                metadata["routing"] = "configured_low_level"
        return payload

    def read_document(self, path: str) -> dict[str, Any]:
        self._ensure_compatible()
        execution_path, requested_connection, execution_connection, routed = self._route_path("read_document", path)
        request = self._client.build_request(
            "POST",
            "/bridge/wfx/download-raw",
            json={"path": execution_path, "auth": self._auth_for_path(execution_path)},
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
            "routing": self._routing_data(
                requested_connection,
                execution_connection,
                routed,
            ) if requested_connection else None,
        }
        if media_type.startswith("text/") or media_type in {"application/json", "application/xml"}:
            result["text"] = content.decode("utf-8", errors="replace")
        else:
            result["content_base64"] = base64.b64encode(content).decode("ascii")
            result["encoding"] = "base64"
        return result
