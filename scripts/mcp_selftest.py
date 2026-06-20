"""MCP 서버 stdio 자체검증 (항목6 서버측, GUI/node 불필요).

우리 MCP 서버(`app/mcp_server.py`)를 **실제 stdio MCP 클라이언트로 띄워** tools/list +
tool 호출을 검증한다. `mcp dev`(Inspector)와 달리 node/PYTHONPATH 문제 없이 어디서나 동작.

실행(프로젝트 루트에서):
    python -m scripts.mcp_selftest
"""
from __future__ import annotations

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"tools/list ({len(names)}):", names)

            res = await session.call_tool("list_available_symbols", {})
            payload = res.structuredContent or {"content": [c.text[:200] for c in res.content]}
            print("call list_available_symbols →", payload)

            ok = len(names) >= 1 and not res.isError
            print("RESULT:", "PASS ✅" if ok else "FAIL ❌")
            return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
