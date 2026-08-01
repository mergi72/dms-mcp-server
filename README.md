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
For a connection path, the MCP server reads the connection auth contract from
the bridge, sends its `credential_id` to the broker, and passes the resulting
in-memory auth context to the bridge operation. Secrets are not returned in
tool results or written to MCP configuration.

## Configuration

Runtime data lives in `config/mcp.json`, separately from application code:

```json
{
  "bridge": { "url": "http://127.0.0.1:8765" },
  "broker": { "url": "http://127.0.0.1:8776" },
  "runtime": {
    "timeoutSeconds": 30,
    "maxDocumentBytes": 1048576
  }
}
```

The machine configuration is authoritative. Optional user overrides are loaded
from `%APPDATA%\\DMS MCP\\config\\mcp.local.json` and merged over it. Set
`DMS_MCP_MACHINE_CONFIG_DIR` or `DMS_MCP_USER_CONFIG_DIR` to use other config
directories.

Environment variables remain available as final runtime overrides:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DMS_BRIDGE_URL` | `http://127.0.0.1:8765` | Local bridge URL |
| `DMS_BROKER_URL` | `http://127.0.0.1:8776` | Local broker URL |
| `DMS_MCP_TIMEOUT_SECONDS` | `30` | Upstream HTTP timeout |
| `DMS_MCP_MAX_DOCUMENT_BYTES` | `1048576` | Maximum document returned to MCP |

Credential IDs are owned by bridge connection configuration and are never
selected by the AI or duplicated in MCP configuration.

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
