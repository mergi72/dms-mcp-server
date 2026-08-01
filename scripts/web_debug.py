from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_live_smoke import _call_json


ALLOWED_TOOLS = {"bridge_health", "list_connections", "list_items", "get_item_info", "read_document"}


HTML = r"""<!doctype html>
<html lang="cs">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>DMS MCP Inspector</title>
  <style>
    :root { color-scheme: dark; font-family: ui-monospace, Consolas, monospace; }
    body { margin: 0; background: #10151c; color: #d9e2ef; }
    header { padding: 18px 24px; border-bottom: 1px solid #293544; background: #151c25; }
    h1 { margin: 0 0 5px; font-size: 20px; } .hint { color: #8fa3b8; font-size: 12px; }
    main { padding: 20px 24px; }
    .controls { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
    input { flex: 1; min-width: 320px; background: #0b1016; color: #eef5ff; border: 1px solid #34465a; padding: 10px; }
    button { background: #1769aa; color: white; border: 0; padding: 10px 14px; cursor: pointer; }
    button:hover { background: #2183d3; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    section { min-width: 0; } h2 { font-size: 13px; color: #8fa3b8; }
    pre { min-height: 420px; max-height: 70vh; overflow: auto; margin: 0; padding: 14px; background: #090d12; border: 1px solid #293544; white-space: pre-wrap; }
    #status { margin: 10px 0; color: #6fdc8c; } .error { color: #ff7b72 !important; }
    @media (max-width: 850px) { .grid { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
<header><h1>DMS MCP Inspector</h1><div class="hint">Browser → MCP stdio → Provider Bridge → DMS; read-only</div></header>
<main>
  <div class="controls">
    <input id="path" value="alfresco:/" aria-label="DMS path">
    <button onclick="callTool('bridge_health', {})">Health</button>
    <button onclick="callTool('list_connections', {})">Connections</button>
    <button onclick="pathTool('list_items')">List</button>
    <button onclick="pathTool('get_item_info')">Info</button>
    <button onclick="pathTool('read_document')">Verify document</button>
  </div>
  <div id="status">Připraveno</div>
  <div class="grid">
    <section><h2>MCP REQUEST</h2><pre id="request">{}</pre></section>
    <section><h2>MCP RESPONSE</h2><pre id="response">{}</pre></section>
  </div>
</main>
<script>
const requestView = document.getElementById('request');
const responseView = document.getElementById('response');
const statusView = document.getElementById('status');
const pretty = value => JSON.stringify(value, null, 2);
function pathTool(tool) { callTool(tool, {path: document.getElementById('path').value}); }
async function callTool(tool, arguments_) {
  const request = {tool, arguments: arguments_};
  requestView.textContent = pretty(request); responseView.textContent = 'Čekám…';
  statusView.textContent = 'Dotaz běží'; statusView.className = '';
  try {
    const response = await fetch('/api/call', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(request)});
    const payload = await response.json();
    responseView.textContent = pretty(payload);
    statusView.textContent = response.ok ? 'Hotovo' : 'Chyba';
    statusView.className = response.ok ? '' : 'error';
  } catch (error) {
    responseView.textContent = pretty({error: String(error)}); statusView.textContent = 'Chyba spojení'; statusView.className = 'error';
  }
}
</script>
</body></html>"""


def _safe_result(tool: str, payload: dict[str, Any]) -> dict[str, Any]:
    if tool != "read_document":
        return payload
    if isinstance(payload.get("text"), str):
        content = payload["text"].encode("utf-8")
    elif isinstance(payload.get("content_base64"), str):
        content = base64.b64decode(payload["content_base64"], validate=True)
    else:
        return payload
    return {
        "path": payload.get("path"),
        "mime_type": payload.get("mime_type"),
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "content_omitted": True,
    }


def _validate_request(payload: Any) -> tuple[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("Request must be a JSON object.")
    tool = payload.get("tool")
    arguments = payload.get("arguments", {})
    if tool not in ALLOWED_TOOLS:
        raise ValueError("Unknown or non-read-only MCP tool.")
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be a JSON object.")
    if tool in {"list_items", "get_item_info", "read_document"}:
        path = arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("This tool requires a non-empty path.")
        arguments = {"path": path.strip()}
    else:
        arguments = {}
    return tool, arguments


async def _invoke(server: Path, timeout: float, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    params = StdioServerParameters(
        command=str(server.resolve()),
        args=[],
        cwd=str(PROJECT_ROOT),
        env={**os.environ, "DMS_MCP_TIMEOUT_SECONDS": str(timeout)},
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await _call_json(session, tool, arguments)


def create_handler(server: Path, timeout: float) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/":
                self.send_error(404)
                return
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/call":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 16_384:
                    raise ValueError("Invalid request size.")
                tool, arguments = _validate_request(json.loads(self.rfile.read(length)))
                result = asyncio.run(_invoke(server, timeout, tool, arguments))
                self._json(200, {"tool": tool, "arguments": arguments, "result": _safe_result(tool, result)})
            except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
                self._json(400, {"error": str(exc)})
            except Exception as exc:
                self._json(502, {"error": f"MCP request failed: {exc}"})

        def log_message(self, format: str, *args: Any) -> None:
            print(f"HTTP {self.address_string()} - {format % args}")

    return Handler


def parse_args() -> argparse.Namespace:
    default_server = PROJECT_ROOT / ".venv" / "Scripts" / "dms-mcp-server.exe"
    parser = argparse.ArgumentParser(description="Open a local read-only web inspector for DMS MCP calls.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8780)
    parser.add_argument("--server", default=str(default_server))
    parser.add_argument("--timeout", type=float, default=30)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Debug inspector may bind only to localhost.")
    server = ThreadingHTTPServer((args.host, args.port), create_handler(Path(args.server), args.timeout))
    print(f"DMS MCP Inspector: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
