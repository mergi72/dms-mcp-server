# dms-mcp-server

[![CI](https://github.com/mergi72/dms-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/mergi72/dms-mcp-server/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-%3E%3D3.11-blue)](https://www.python.org/)
[![MCP SDK](https://img.shields.io/badge/MCP%20SDK-v2-5c4ee5)](https://github.com/modelcontextprotocol/python-sdk)
[![Release](https://img.shields.io/github/v/release/mergi72/dms-mcp-server?label=Release&color=blueviolet)](https://github.com/mergi72/dms-mcp-server/releases/latest)

Read-only Model Context Protocol server for DMS repositories exposed by
`dms-provider-bridge`. The MCP server never receives DMS usernames, passwords
or tokens. It forwards only the connection-owned `credential_id` reference and
leaves credential resolution to the bridge.

The server uses the official Python MCP SDK v2 with Streamable HTTP. The
server supports stateless HTTP so a long-running DMS operation does not block
independent MCP clients. The public read-only tool contract remains unchanged.

## MVP tools

- `bridge_health`
- `list_connections`
- `list_items`
- `search_items`
- `search_metadata`
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
MCP client --HTTP--> dms-mcp-server --HTTP--> dms-provider-bridge --> DMS
                                                       |
                                                       +--> credential resolution
```
For a connection path, the MCP server reads the connection auth contract from
the bridge and passes only its `credential_id` reference back to the bridge
operation. The bridge owns resolution and use of DMS secrets. Secrets never
enter the MCP process, tool results or MCP configuration.

## Configuration

Runtime data lives in `config/mcp.json`, separately from application code:

```json
{
  "bridge": {
    "url": "http://127.0.0.1:8765",
    "minimumVersion": "0.2.0"
  },
  "server": {
    "host": "127.0.0.1",
    "port": 8781,
    "path": "/mcp",
    "statelessHttp": true
  },
  "runtime": {
    "timeoutSeconds": 30,
    "maxDocumentBytes": 1048576
  },
  "debug": {
    "enable": true,
    "path": "%APPDATA%\\DMS MCP\\logs",
    "loggerLevels": {
      "httpx": "WARNING",
      "httpcore": "WARNING"
    }
  }
}
```

`server.statelessHttp` is enabled by default for the SDK v2 runtime. Logger
levels are data-driven; the defaults keep low-level HTTP transport chatter out
of Laděnka while preserving MCP tool and operation events.

The service always writes UTF-8 operational events to `mcp.log`. When
`debug.enable` is true, detailed events are also written to `mcp-debug.log` in
the configured directory. Tool logs never contain credentials or downloaded
document content.

The machine configuration provides the required base document. Optional user
overrides are loaded from `%APPDATA%\\DMS MCP\\config\\mcp.local.json` and
merged over that base. Set
`DMS_MCP_MACHINE_CONFIG_DIR` or `DMS_MCP_USER_CONFIG_DIR` to use other config
directories.

The distributed application keeps `config/mcp.json` beside the application,
not inside the Python package. A packaged executable therefore has this
layout:

```text
dms-mcp-server/
|-- dms-mcp-server.exe
`-- config/
    `-- mcp.json
```

User overrides in `%APPDATA%\\DMS MCP\\config\\mcp.local.json` are merged on
top of that default. Environment variables have the highest priority. A wheel
is only an installer input: a bare `pip install` is not a complete application
deployment unless the installer also places `config/mcp.json` beside the
application or sets `DMS_MCP_MACHINE_CONFIG_DIR`.

Environment variables remain available as final runtime overrides:

| Variable | Default | Purpose |
| --- | --- | --- |
| `DMS_BRIDGE_URL` | `http://127.0.0.1:8765` | Local bridge URL |
| `DMS_MCP_SERVER_HOST` | `127.0.0.1` | Local MCP bind address (`127.0.0.1`, `localhost` or `::1`) |
| `DMS_MCP_SERVER_PORT` | `8781` | MCP service port |
| `DMS_MCP_SERVER_PATH` | `/mcp` | Streamable HTTP endpoint path |
| `DMS_MCP_TIMEOUT_SECONDS` | `30` | Upstream HTTP timeout |
| `DMS_MCP_MAX_DOCUMENT_BYTES` | `1048576` | Maximum document returned to MCP |
| `DMS_MCP_MIN_BRIDGE_VERSION` | `0.2.0` | Minimum compatible bridge version |

Credential IDs are owned by bridge connection configuration and are never
selected by the AI or duplicated in MCP configuration.

The Bridge HTTP client ignores system proxy environment variables. This keeps
local Bridge traffic on its configured direct connection instead of routing it
through `HTTP_PROXY` or `ALL_PROXY`.

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

Run the MCP HTTP service:

```powershell
.\.venv\Scripts\dms-mcp-server.exe
```

The default endpoint is `http://127.0.0.1:8781/mcp`. Diagnostic scripts invoke
the same executable with the explicit `--stdio` compatibility switch.

Run the read-only live smoke test while the bridge and its configured credential
resolution service are running:

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

Example HTTP MCP client configuration:

```json
{
  "mcpServers": {
    "dms": { "url": "http://127.0.0.1:8781/mcp" }
  }
}
```
