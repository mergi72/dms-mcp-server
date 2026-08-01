from __future__ import annotations

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
    def get_item_info(path: str) -> dict:
        """Return metadata for one file or folder at connection:/path."""
        return bridge.stat(path)

    @mcp.tool()
    def read_document(path: str) -> dict:
        """Read a size-limited document; text is decoded and binary data is base64 encoded."""
        return bridge.read_document(path)

    return mcp


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
