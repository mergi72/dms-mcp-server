from __future__ import annotations

import argparse
import logging
from time import perf_counter
from typing import Any, Callable

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from dms_mcp_server import __version__
from dms_mcp_server.clients import BridgeClient
from dms_mcp_server.config import Settings, load_settings
from dms_mcp_server.logging_config import configure_logging
from dms_mcp_server.tracing import CORRELATION_HEADER, correlation_scope


LOGGER = logging.getLogger("mcp")
SERVICE_NAME = "vfs-mcp-server"
MCP_SESSION_HEADER = "Mcp-Session-Id"
READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    openWorldHint=False,
)


def _request_correlation_id(context: Context) -> str | None:
    request = context.request_context.request
    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    return headers.get(CORRELATION_HEADER) or headers.get(MCP_SESSION_HEADER)


def _run_tool(name: str, operation: Callable[[], dict], correlation_id: str | None = None, **fields: Any) -> dict:
    started = perf_counter()
    detail = " ".join(f"{key}={value!r}" for key, value in fields.items())
    with correlation_scope(correlation_id) as active_id:
        LOGGER.debug("mcp_tool_start correlation_id=%s tool=%s%s", active_id, name, f" {detail}" if detail else "")
        try:
            result = operation()
        except Exception as exc:
            LOGGER.exception(
                "mcp_tool_failed correlation_id=%s tool=%s error_type=%s duration_ms=%d%s",
                active_id, name, type(exc).__name__, round((perf_counter() - started) * 1000), f" {detail}" if detail else "",
            )
            raise
        LOGGER.info(
            "mcp_tool_done correlation_id=%s tool=%s duration_ms=%d%s",
            active_id, name, round((perf_counter() - started) * 1000), f" {detail}" if detail else "",
        )
        return result


def create_server(settings: Settings | None = None) -> MCPServer:
    active_settings = settings or load_settings()
    bridge = BridgeClient(active_settings)
    mcp = MCPServer(
        "DMS",
        version=__version__,
        instructions=(
            "Read-only access to DMS connections through DMS Provider Bridge. "
            "Paths use the connection:/path format. "
            "For a known DMS tag, call search_metadata exactly once with path='alfresco:/', field='TAG', and the tag as value. "
            "If that call returns one folder, reuse its returned public path verbatim. "
            "Do not repeat an identical successful search_items call in one turn. A result with complete=true and warnings=[] "
            "is final even when truncated=true solely because max_results limited the returned items."
        ),
    )

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        LOGGER.info("health_check status=ok service=%s version=%s", SERVICE_NAME, __version__)
        return JSONResponse({"ok": True, "service": SERVICE_NAME, "version": __version__})

    @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
    def bridge_health(ctx: Context) -> dict:
        """Check whether the local DMS Provider Bridge is available."""
        return _run_tool("bridge_health", bridge.health, _request_correlation_id(ctx))

    @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
    def list_connections(ctx: Context) -> dict:
        """List DMS connections available through the bridge."""
        return _run_tool("list_connections", bridge.list_connections, _request_correlation_id(ctx))

    @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
    def list_items(ctx: Context, path: str = "/") -> dict:
        """List files and folders at a connection:/path location."""
        return _run_tool("list_items", lambda: bridge.list_items(path), _request_correlation_id(ctx), path=path)

    @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
    def search_items(
        path: str,
        query: str,
        ctx: Context,
        max_results: int = 20,
        files_only: bool = True,
        search_mode: str = "first_matches",
    ) -> dict:
        """Search names recursively below connection:/path. Use first_matches for interactive AI requests and exhaustive only when an exact total and global ordering are required. Returned paths are exact and must be reused verbatim; never shorten or rewrite them. By default return unique files only. Do not repeat an identical successful call in one turn: first_matches with reason=result_limit is a successful final result, while exhaustive with complete=true and warnings=[] is final."""
        return _run_tool(
            "search_items",
            lambda: bridge.search_items(path, query, max_results, files_only, search_mode),
            _request_correlation_id(ctx),
            path=path,
            max_results=max_results,
            files_only=files_only,
            search_mode=search_mode,
        )

    @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
    def search_metadata(path: str, field: str, value: str, ctx: Context, max_results: int = 20, files_only: bool = False) -> dict:
        """Search exact metadata values below connection:/path. For a known DMS tag use path='alfresco:/', field='TAG', and the tag as value; do not probe eDoCat or alternate field names. If exactly one folder is returned, reuse its public path verbatim in the next operation."""
        return _run_tool(
            "search_metadata",
            lambda: bridge.search_metadata(path, field, value, max_results, files_only),
            _request_correlation_id(ctx),
            path=path,
            field=field,
            max_results=max_results,
            files_only=files_only,
        )

    @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
    def open_share_url(share_url: str, ctx: Context, connection: str = "auto") -> dict:
        """Open an Alfresco or eDoCat DMS share URL read-only. Resolve its exact path, return item metadata, and list contents when it targets a folder."""
        return _run_tool("open_share_url", lambda: bridge.open_share_url(share_url, connection), _request_correlation_id(ctx), connection=connection)

    @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
    def get_item_info(path: str, ctx: Context) -> dict:
        """Return metadata for one file or folder at connection:/path."""
        return _run_tool("get_item_info", lambda: bridge.stat(path), _request_correlation_id(ctx), path=path)

    @mcp.tool(annotations=READ_ONLY_ANNOTATIONS)
    def read_document(path: str, ctx: Context) -> dict:
        """Read a size-limited document; text is decoded and binary data is base64 encoded."""
        result = _run_tool("read_document", lambda: bridge.read_document(path), _request_correlation_id(ctx), path=path)
        LOGGER.debug(
            "mcp_document_read path=%r size=%r mime_type=%r",
            path,
            result.get("size"),
            result.get("mime_type"),
        )
        return result

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only DMS MCP server.")
    parser.add_argument("--stdio", action="store_true", help="Use stdio for legacy diagnostic scripts.")
    args = parser.parse_args()
    settings = load_settings()
    log_dir = configure_logging(settings)
    transport = "stdio" if args.stdio else "streamable-http"
    LOGGER.info(
        "mcp_start service=%s version=%s transport=%s host=%s port=%d path=%s log_dir=%s",
        SERVICE_NAME,
        __version__,
        transport,
        settings.server_host,
        settings.server_port,
        settings.server_path,
        log_dir,
    )
    server = create_server(settings)
    if args.stdio:
        server.run(transport="stdio")
    else:
        server.run(
            transport="streamable-http",
            host=settings.server_host,
            port=settings.server_port,
            streamable_http_path=settings.server_path,
            json_response=True,
            stateless_http=settings.server_stateless_http,
        )


if __name__ == "__main__":
    main()
