# dms-mcp-server

[![CI](https://github.com/mergi72/dms-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/mergi72/dms-mcp-server/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-%3E%3D3.11-blue)](https://www.python.org/)
[![Release](https://img.shields.io/github/v/release/mergi72/dms-mcp-server?label=Release&color=blueviolet)](https://github.com/mergi72/dms-mcp-server/releases/latest)

Read-only Model Context Protocol server for DMS repositories exposed by
`dms-provider-bridge`. Credential resolution is delegated to the local
`credential-broker`; the MCP server never reads Windows Credential Manager
directly.

## MVP tools

- `bridge_health`
- `list_connections`
- `list_items`
- `search_items`
- `open_share_url`
- `get_item_info`
- `read_document`

All DMS paths use `connection:/path`. The server intentionally has no upload,
move, copy, mkdir or delete tools.

`open_share_url` accepts an Alfresco-compatible shared URL, resolves its exact
`connection:/path`, returns item metadata and lists the contents when the URL
targets a folder. eDoCat `DIR-...` links are resolved through their same-host
redirect to the underlying Alfresco document-library path.

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
  "bridge": {
    "url": "http://127.0.0.1:8765",
    "minimumVersion": "0.2.0"
  },
  "broker": { "url": "http://127.0.0.1:8776" },
  "runtime": {
    "timeoutSeconds": 30,
    "maxDocumentBytes": 1048576
  }
}
```

The machine configuration provides the required base document. Optional user
overrides are loaded from `%APPDATA%\\DMS MCP\\config\\mcp.local.json` and
merged over that base. Set
`DMS_MCP_MACHINE_CONFIG_DIR` or `DMS_MCP_USER_CONFIG_DIR` to use other config
directories.

Environment variables remain available as final runtime overrides:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DMS_BRIDGE_URL` | `http://127.0.0.1:8765` | Local bridge URL |
| `DMS_BROKER_URL` | `http://127.0.0.1:8776` | Local broker URL |
| `DMS_MCP_TIMEOUT_SECONDS` | `30` | Upstream HTTP timeout |
| `DMS_MCP_MAX_DOCUMENT_BYTES` | `1048576` | Maximum document returned to MCP |
| `DMS_MCP_MIN_BRIDGE_VERSION` | `0.2.0` | Minimum compatible bridge version |

Credential IDs are owned by bridge connection configuration and are never
selected by the AI or duplicated in MCP configuration.

Bridge and broker HTTP clients ignore system proxy environment variables. This
keeps local credential resolution and bridge traffic on their configured direct
connections instead of routing them through `HTTP_PROXY` or `ALL_PROXY`.

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

Run the stdio server:

```powershell
.\.venv\Scripts\dms-mcp-server.exe
```

Run the read-only live smoke test while bridge and broker are running:

```powershell
.\.venv\Scripts\python.exe scripts\run_live_smoke.py alfresco edocat
```

The smoke test exercises the original browsing and document tools. It prints document metadata,
size and SHA-256 only; document content is never printed.

Display a bounded tree of DMS folders and files through the MCP server:

```powershell
.\.venv\Scripts\python.exe scripts\debug_dms.py alfresco edocat --max-depth 2
```

Optionally verify one document without printing its content:

```powershell
.\.venv\Scripts\python.exe scripts\debug_dms.py alfresco `
  --document "alfresco:/Shared/report.docx"
```

The debug command prints names and available item metadata. Document
verification prints only MIME type, byte size and SHA-256. Traversal is bounded
by `--max-depth`, `--max-directories` and `--max-items`.

Run the local web inspector to see every MCP request and response side by side:

```powershell
.\.venv\Scripts\python.exe scripts\web_debug.py
```

Then open the address configured by `inspector.host` and `inspector.port` in
`config/mcp.json` (default `http://127.0.0.1:8780`). Command-line `--host` and
`--port` values may temporarily override the JSON configuration. The inspector
exposes the original six browsing and document tools and binds only to localhost;
`open_share_url` remains available to MCP clients without adding another
diagnostic UI control. Its Search
control calls native provider search below the selected DMS path. For
`read_document`, it displays
MIME type, byte size and SHA-256 of the original bytes while omitting document
content. Switch between the raw `MCP Response` and a clickable `UI View` for
connections, folders, files and metadata; `..` navigates to the parent folder.
Its API accepts only local
Host/Origin values and JSON requests.

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
