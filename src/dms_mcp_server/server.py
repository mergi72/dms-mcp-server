from __future__ import annotations

import argparse
import logging
from time import perf_counter
from typing import Any, Callable

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from dms_mcp_server import __version__
from dms_mcp_server.clients import BridgeClient, BrokerClient
from dms_mcp_server.config import Settings, load_settings
from dms_mcp_server.logging_config import configure_logging


LOGGER = logging.getLogger("mcp")


def _run_tool(name: str, operation: Callable[[], dict], **fields: Any) -> dict:
    started = perf_counter()
    detail = " ".join(f"{key}={value!r}" for key, value in fields.items())
    LOGGER.debug("mcp_tool_start tool=%s%s", name, f" {detail}" if detail else "")
    try:
        result = operation()
    except Exception as exc:
        LOGGER.exception(
            "mcp_tool_failed tool=%s error_type=%s duration_ms=%d%s",
            name,
            type(exc).__name__,
            round((perf_counter() - started) * 1000),
            f" {detail}" if detail else "",
        )
        raise
    LOGGER.info(
        "mcp_tool_done tool=%s duration_ms=%d%s",
        name,
        round((perf_counter() - started) * 1000),
        f" {detail}" if detail else "",
    )
    return result


def create_server(settings: Settings | None = None) -> FastMCP:
    active_settings = settings or load_settings()
    broker = BrokerClient(active_settings)
    bridge = BridgeClient(active_settings, broker)
    mcp = FastMCP(
        "DMS",
        instructions=(
            "Read-only access to DMS connections through DMS Provider Bridge. "
            "Paths use the connection:/path format."
        ),
        host=active_settings.server_host,
        port=active_settings.server_port,
        streamable_http_path=active_settings.server_path,
        json_response=True,
    )

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        LOGGER.info("health_check status=ok service=vfs-mcp-server version=%s", __version__)
        return JSONResponse({"ok": True, "service": "vfs-mcp-server", "version": __version__})

    @mcp.tool()
    def bridge_health() -> dict:
        """Check whether the local DMS Provider Bridge is available."""
        return _run_tool("bridge_health", bridge.health)

    @mcp.tool()
    def list_connections() -> dict:
        """List DMS connections available through the bridge."""
        return _run_tool("list_connections", bridge.list_connections)

    @mcp.tool()
    def list_items(path: str = "/") -> dict:
        """List files and folders at a connection:/path location."""
        return _run_tool("list_items", lambda: bridge.list_items(path), path=path)

    @mcp.tool()
    def search_items(path: str, query: str, max_results: int = 20, files_only: bool = True) -> dict:
        """Search natively below connection:/path. Returned paths are exact and must be reused verbatim; never shorten or rewrite them. By default return unique files only."""
        return _run_tool(
            "search_items",
            lambda: bridge.search_items(path, query, max_results, files_only),
            path=path,
            max_results=max_results,
            files_only=files_only,
        )

    @mcp.tool()
    def open_share_url(share_url: str, connection: str = "auto") -> dict:
        """Open an Alfresco or eDoCat DMS share URL read-only. Resolve its exact path, return item metadata, and list contents when it targets a folder."""
        return _run_tool("open_share_url", lambda: bridge.open_share_url(share_url, connection), connection=connection)

    @mcp.tool()
    def get_item_info(path: str) -> dict:
        """Return metadata for one file or folder at connection:/path."""
        return _run_tool("get_item_info", lambda: bridge.stat(path), path=path)

    @mcp.tool()
    def read_document(path: str) -> dict:
        """Read a size-limited document; text is decoded and binary data is base64 encoded."""
        result = _run_tool("read_document", lambda: bridge.read_document(path), path=path)
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
        "mcp_start service=vfs-mcp-server version=%s transport=%s host=%s port=%d path=%s log_dir=%s",
        __version__,
        transport,
        settings.server_host,
        settings.server_port,
        settings.server_path,
        log_dir,
    )
    create_server(settings).run(transport=transport)


if __name__ == "__main__":
    main()
