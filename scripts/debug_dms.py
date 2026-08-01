from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_live_smoke import _call_json, _listing_items, _navigation_path


@dataclass
class DebugLimits:
    max_depth: int
    max_directories: int
    max_items: int


def _item_label(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "<unnamed>")
    if item.get("is_folder") is True:
        return f"{name}/"

    details: list[str] = []
    size = item.get("size")
    if isinstance(size, int) and size >= 0:
        details.append(f"{size} B")
    mime_type = item.get("mime_type") or item.get("mimeType")
    if isinstance(mime_type, str) and mime_type:
        details.append(mime_type)
    return f"{name}  [{', '.join(details)}]" if details else name


def _document_bytes(payload: dict[str, Any]) -> bytes:
    if isinstance(payload.get("text"), str):
        return payload["text"].encode("utf-8")
    if isinstance(payload.get("content_base64"), str):
        return base64.b64decode(payload["content_base64"], validate=True)
    raise RuntimeError("read_document returned no text or base64 content.")


def _health_status(payload: dict[str, Any]) -> str:
    return "ERROR" if payload.get("ok") is False else "OK"


async def _print_tree(
    session: ClientSession,
    path: str,
    limits: DebugLimits,
    depth: int = 0,
    counters: dict[str, int] | None = None,
) -> None:
    state = counters if counters is not None else {"directories": 0, "items": 0}
    if state["directories"] >= limits.max_directories:
        print(f"{'    ' * depth}... directory limit reached")
        return

    state["directories"] += 1
    listing = await _call_json(session, "list_items", {"path": path})
    items = sorted(
        _listing_items(listing),
        key=lambda item: (item.get("is_folder") is not True, str(item.get("name") or "").casefold()),
    )
    for item in items:
        if state["items"] >= limits.max_items:
            print(f"{'    ' * depth}... item limit reached")
            return
        state["items"] += 1
        print(f"{'    ' * depth}- {_item_label(item)}")
        name = item.get("name")
        if item.get("is_folder") is True and isinstance(name, str) and name and depth < limits.max_depth:
            child_path = _navigation_path(path, name)
            try:
                await _print_tree(session, child_path, limits, depth + 1, state)
            except RuntimeError as exc:
                print(f"{'    ' * (depth + 1)}! {exc}")


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
            print(f"Bridge: {health.get('version', 'unknown')} ({_health_status(health)})")

            connections = await _call_json(session, "list_connections", {})
            data = connections.get("data")
            available = data.get("connection_names", []) if isinstance(data, dict) else []
            selected = args.connections or [name for name in available if isinstance(name, str)]
            print(f"Connections: {', '.join(selected) if selected else '<none>'}")

            limits = DebugLimits(args.max_depth, args.max_directories, args.max_items)
            for connection in selected:
                print(f"\n{connection}:/")
                await _print_tree(session, f"{connection}:/", limits)

            if args.document:
                info = await _call_json(session, "get_item_info", {"path": args.document})
                document = await _call_json(session, "read_document", {"path": args.document})
                content = _document_bytes(document)
                summary = {
                    "path": args.document,
                    "info_ok": info.get("ok"),
                    "mime_type": document.get("mime_type"),
                    "size": len(content),
                    "sha256": document.get("sha256") or hashlib.sha256(content).hexdigest(),
                }
                print("\nDocument verification:")
                print(json.dumps(summary, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    default_server = Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "dms-mcp-server.exe"
    parser = argparse.ArgumentParser(description="Safely display a bounded DMS tree through MCP.")
    parser.add_argument("connections", nargs="*", help="Connection names; defaults to every available connection.")
    parser.add_argument("--document", help="Optional connection:/path to verify without printing its content.")
    parser.add_argument("--server", default=str(default_server))
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--max-depth", type=int, default=2)
    parser.add_argument("--max-directories", type=int, default=20)
    parser.add_argument("--max-items", type=int, default=200)
    args = parser.parse_args()
    if args.max_depth < 0 or args.max_directories < 1 or args.max_items < 1:
        parser.error("limits must be non-negative depth and positive directory/item counts")
    return args


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
