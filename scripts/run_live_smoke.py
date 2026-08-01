from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
from collections import deque
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _result_text(result: Any) -> str:
    if not result.content:
        return ""
    block = result.content[0]
    return block.text if getattr(block, "type", None) == "text" else ""


async def _call_json(session: ClientSession, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await session.call_tool(tool, arguments)
    text = _result_text(result)
    if result.isError:
        raise RuntimeError(text or f"MCP tool {tool} failed.")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise RuntimeError(f"MCP tool {tool} returned non-object JSON.")
    return payload


def _listing_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data")
    items = data.get("items") if isinstance(data, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _navigation_path(parent: str, item_name: str) -> str:
    return f"{parent.rstrip('/')}/{item_name.lstrip('/')}"


async def _find_readable_file(
    session: ClientSession,
    root: str,
    max_depth: int,
    max_directories: int,
    max_bytes: int,
) -> str:
    queue: deque[tuple[str, int]] = deque([(root, 0)])
    visited = 0
    while queue and visited < max_directories:
        path, depth = queue.popleft()
        visited += 1
        try:
            listing = await _call_json(session, "list_items", {"path": path})
        except RuntimeError:
            if depth == 0:
                raise
            continue
        for item in _listing_items(listing):
            item_name = item.get("name")
            if not isinstance(item_name, str) or not item_name:
                continue
            item_path = _navigation_path(path, item_name)
            if item.get("is_folder") is True:
                if depth < max_depth:
                    queue.append((item_path, depth + 1))
                continue
            size = item.get("size")
            if size is None or (isinstance(size, int) and 0 <= size <= max_bytes):
                return item_path
    raise RuntimeError(f"No readable file found below {root} within smoke-test limits.")


def _content_digest(payload: dict[str, Any]) -> str:
    digest = payload.get("sha256")
    if isinstance(digest, str) and digest:
        return digest
    if isinstance(payload.get("text"), str):
        content = payload["text"].encode("utf-8")
    elif isinstance(payload.get("content_base64"), str):
        content = base64.b64decode(payload["content_base64"], validate=True)
    else:
        raise RuntimeError("read_document returned no text or base64 content.")
    return hashlib.sha256(content).hexdigest()


async def run(args: argparse.Namespace) -> None:
    executable = Path(args.server).resolve()
    params = StdioServerParameters(
        command=str(executable),
        args=[],
        cwd=str(executable.parent.parent.parent),
        env={**os.environ, "DMS_MCP_TIMEOUT_SECONDS": str(args.timeout)},
    )
    async with stdio_client(params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            health = await _call_json(session, "bridge_health", {})
            print(json.dumps({"bridge": health}, ensure_ascii=False))
            connections = await _call_json(session, "list_connections", {})
            data = connections.get("data")
            print(json.dumps({"connections": data.get("connection_names", []) if isinstance(data, dict) else []}))

            for connection in args.connections:
                root = f"{connection}:/"
                file_path = await _find_readable_file(
                    session,
                    root,
                    args.max_depth,
                    args.max_directories,
                    args.max_document_bytes,
                )
                info = await _call_json(session, "get_item_info", {"path": file_path})
                document = await _call_json(session, "read_document", {"path": file_path})
                print(
                    json.dumps(
                        {
                            "connection": connection,
                            "path": file_path,
                            "info_ok": info.get("ok"),
                            "mime_type": document.get("mime_type"),
                            "size": document.get("size"),
                            "sha256": _content_digest(document),
                        },
                        ensure_ascii=False,
                    )
                )


def parse_args() -> argparse.Namespace:
    default_server = Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "dms-mcp-server.exe"
    parser = argparse.ArgumentParser(description="Run a read-only MCP-to-DMS live smoke test.")
    parser.add_argument("connections", nargs="*", default=["alfresco", "edocat"])
    parser.add_argument("--server", default=str(default_server))
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--max-depth", type=int, default=4)
    parser.add_argument("--max-directories", type=int, default=30)
    parser.add_argument("--max-document-bytes", type=int, default=1_048_576)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
