"""Exposes Jarvis's PC actions (tools.py) to Claude Code as an MCP server over stdio.

Claude Code starts this itself (see brain.ClaudeCodeBrain); it isn't meant to be run by hand.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.windows import enable_dpi_awareness  # noqa: E402

enable_dpi_awareness()

import mcp.types as types  # noqa: E402
from mcp.server.lowlevel import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402

from jarvis import tools  # noqa: E402


async def list_tools(_ctx, _params) -> types.ListToolsResult:
    return types.ListToolsResult(tools=[
        types.Tool(name=t["name"], description=t["description"], input_schema=t["input_schema"])
        for t in tools.TOOLS
    ])


async def call_tool(_ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
    try:
        output = await asyncio.to_thread(tools.run, params.name, params.arguments or {})
        is_error = False
    except Exception as e:  # report failures to Claude so it can recover
        output, is_error = f"Error: {e}", True
    return types.CallToolResult(content=[types.TextContent(type="text", text=output)], is_error=is_error)


server = Server("jarvis", on_list_tools=list_tools, on_call_tool=call_tool)


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
