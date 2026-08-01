# dms-mcp-server

Read-only Model Context Protocol server for DMS repositories exposed by
`dms-provider-bridge`. Credential resolution is delegated to the local
`credential-broker`; the MCP server never reads Windows Credential Manager
directly.

## MVP tools

- `bridge_health`
- `list_connections`
- `list_items`
- `get_item_info`
- `read_document`

All DMS paths use `connection:/path`. The server intentionally has no upload,
move, copy, mkdir or delete tools.

## Architecture

```text
MCP client --stdio--> dms-mcp-server --HTTP--> dms-provider-bridge --> DMS
                            |
                            +----------HTTP--> credential-broker
```

When a tool receives a `credential_id`, the MCP server resolves it through the
broker and passes the resulting in-memory auth context to the bridge. Secrets
are not returned in tool results or written to configuration.

## Configuration

Environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DMS_BRIDGE_URL` | `http://127.0.0.1:8765` | Local bridge URL |
| `DMS_BROKER_URL` | `http://127.0.0.1:8776` | Local broker URL |
| `DMS_MCP_TIMEOUT_SECONDS` | `30` | Upstream HTTP timeout |
| `DMS_MCP_MAX_DOCUMENT_BYTES` | `1048576` | Maximum document returned to MCP |
| `DMS_MCP_CREDENTIAL_ID` | unset | Optional default credential target |

Tool-level `credential_id` overrides the configured default. If neither is
provided, the bridge receives no explicit auth and may use connection defaults.

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install -e ".[dev]"
.\.venv\Scripts\pytest.exe
```

Run the stdio server:

```powershell
.\.venv\Scripts\dms-mcp-server.exe
```

Example MCP client configuration:

```json
{
  "mcpServers": {
    "dms": {
      "command": "C:\\Users\\YOUR_USER\\python_projects\\dms-mcp-server\\.venv\\Scripts\\dms-mcp-server.exe",
      "env": {
        "DMS_BRIDGE_URL": "http://127.0.0.1:8765",
        "DMS_BROKER_URL": "http://127.0.0.1:8776"
      }
    }
  }
}
```

