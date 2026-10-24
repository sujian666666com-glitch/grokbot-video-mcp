"""Invoke the real stdio MCP protocol when a running chat has not refreshed tools yet."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(tool, arguments):
    root = Path(__file__).resolve().parent
    params = StdioServerParameters(command=sys.executable, args=[str(root / 'mcp_server.py')])
    async with stdio_client(params) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            if tool == 'list-tools':
                response = await session.list_tools()
            else:
                response = await session.call_tool(tool, arguments)
            print(response.model_dump_json(indent=2))
            if getattr(response, 'isError', False):
                return 1
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('tool')
    parser.add_argument('--arguments', default='{}')
    options = parser.parse_args()
    sys.exit(asyncio.run(run(options.tool, json.loads(options.arguments))))
