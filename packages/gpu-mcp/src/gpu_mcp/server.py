"""MCP stdio server for gpu-watch.

JSON-RPC 2.0, protocol revision ``2024-11-05``. Same wire shape as the
demo-mcp-server in Olympus, so Olympus's StdioTransport handshakes
without changes.

One JSON object per line on stdin/stdout. ``notifications/initialized``
is acknowledged silently per spec.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from .tools import dispatch_tool, tools_descriptor


PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "gpu-mcp", "version": "0.1.0"}

logger = logging.getLogger(__name__)


def _ok(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _err(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def dispatch(request: dict) -> dict | None:
    """Return a response envelope, or None for notifications."""
    method = request.get("method")
    msg_id = request.get("id")
    params = request.get("params") or {}

    if msg_id is None:                           # notification — no response
        return None

    if method == "initialize":
        return _ok(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })

    if method == "tools/list":
        return _ok(msg_id, {"tools": tools_descriptor()})

    if method == "tools/call":
        return _ok(msg_id, dispatch_tool(
            params.get("name") or "",
            params.get("arguments") or {},
        ))

    return _err(msg_id, -32601, f"method not found: {method}")


def serve(stdin=sys.stdin, stdout=sys.stdout) -> int:
    try:
        stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": f"parse error: {exc}"},
            }) + "\n")
            stdout.flush()
            continue

        try:
            response = dispatch(request)
        except Exception as exc:                                # noqa: BLE001
            logger.exception("dispatch failed")
            response = _err(request.get("id"), -32603, f"internal error: {exc}")

        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="gpu-mcp")
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args()
    logging.basicConfig(
        level=args.log_level.upper(),
        stream=sys.stderr,                  # never write logs to stdout
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return serve()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
