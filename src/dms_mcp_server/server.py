from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP

from dms_mcp_server.clients import BridgeClient, BrokerClient
from dms_mcp_server.config import Settings, load_settings


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

    @mcp.tool()
    def bridge_health() -> dict:
        """Check whether the local DMS Provider Bridge is available."""
        return bridge.health()

    @mcp.tool()
    def list_connections() -> dict:
        """List DMS connections available through the bridge."""
        return bridge.list_connections()

    @mcp.tool()
    def list_items(path: str = "/") -> dict:
        """List files and folders at a connection:/path location."""
        return bridge.list_items(path)

    @mcp.tool()
    def search_items(path: str, query: str, max_results: int = 20, files_only: bool = True) -> dict:
        """Search natively below connection:/path. Returned paths are exact and must be reused verbatim; never shorten or rewrite them. By default return unique files only."""
        return bridge.search_items(path, query, max_results, files_only)

    @mcp.tool()
    def open_share_url(share_url: str, connection: str = "auto") -> dict:
        """Open an Alfresco or eDoCat DMS share URL read-only. Resolve its exact path, return item metadata, and list contents when it targets a folder."""
        return bridge.open_share_url(share_url, connection)

    @mcp.tool()
    def get_item_info(path: str) -> dict:
        """Return metadata for one file or folder at connection:/path."""
        return bridge.stat(path)

    @mcp.tool()
    def read_document(path: str) -> dict:
        """Read a size-limited document; text is decoded and binary data is base64 encoded."""
        return bridge.read_document(path)

    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only DMS MCP server.")
    parser.add_argument("--stdio", action="store_true", help="Use stdio for legacy diagnostic scripts.")
    args = parser.parse_args()
    create_server().run(transport="stdio" if args.stdio else "streamable-http")


if __name__ == "__main__":
    main()
