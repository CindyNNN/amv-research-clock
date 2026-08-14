#!/usr/bin/env python
"""Minimal stdio MCP server for Compass 0AMV local cache.

Register in Cursor MCP settings, for example:

{
  "mcpServers": {
    "compass-0amv": {
      "command": "python",
      "args": ["C:/Users/Cindy/Desktop/Finance/AI金融/scripts/compass_0amv_mcp.py"]
    }
  }
}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ai_invest_advisor.compass_bridge import (  # noqa: E402
    CompassBridgeError,
    cache_status,
    fetch_0amv,
)


SERVER_NAME = "compass-0amv"


def _ok(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _err(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_list():
    return {
        "tools": [
            {
                "name": "compass_0amv_status",
                "description": "检查指南针本地 0AMV(活跃市值) 缓存是否存在及修改时间",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "compass_root": {
                            "type": "string",
                            "description": "指南针安装目录，默认 C:\\\\Softwares\\\\compass",
                        }
                    },
                },
            },
            {
                "name": "compass_0amv_kline",
                "description": "读取指南针本地缓存中的活跃市值(0AMV) K线（day 或 min15）",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "freq": {
                            "type": "string",
                            "enum": ["day", "min15"],
                            "default": "day",
                        },
                        "start": {"type": "string", "description": "YYYY-MM-DD"},
                        "end": {"type": "string", "description": "YYYY-MM-DD"},
                        "tail": {
                            "type": "integer",
                            "default": 30,
                            "description": "只返回最近 N 根，0 表示全部",
                        },
                        "compass_root": {"type": "string"},
                    },
                },
            },
        ]
    }


def _call_tool(name: str, arguments: dict):
    root = arguments.get("compass_root") or r"C:\Softwares\compass"
    if name == "compass_0amv_status":
        return cache_status(root)
    if name == "compass_0amv_kline":
        freq = arguments.get("freq") or "day"
        frame = fetch_0amv(
            freq,
            compass_root=root,
            start=arguments.get("start"),
            end=arguments.get("end"),
        )
        tail = int(arguments.get("tail") or 30)
        if tail > 0:
            frame = frame.tail(tail)
        records = json.loads(frame.to_json(orient="records", date_format="iso"))
        return {
            "symbol": "0AMV",
            "name": "活跃市值",
            "freq": freq,
            "rows": len(records),
            "source": "compass_local_cache",
            "disclaimer": "研究辅助，非投资建议；数据以指南针本地缓存为准，需客户端同步后才最新。",
            "data": records,
        }
    raise CompassBridgeError(f"未知工具: {name}")


def handle(message: dict):
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        return _ok(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": "0.1.0"},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _ok(request_id, _tool_list())
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            result = _call_tool(name, arguments)
            return _ok(
                request_id,
                {
                    "content": [
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False)}
                    ]
                },
            )
        except Exception as exc:  # noqa: BLE001
            return _ok(
                request_id,
                {
                    "content": [{"type": "text", "text": f"错误: {exc}"}],
                    "isError": True,
                },
            )
    if request_id is not None:
        return _err(request_id, -32601, f"Method not found: {method}")
    return None


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
