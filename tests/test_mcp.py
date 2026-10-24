import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


class MCPTests(unittest.TestCase):
    def test_real_stdio_discovery_and_readonly_call(self):
        async def check(folder):
            params = StdioServerParameters(command=sys.executable, args=[str(ROOT / 'mcp_server.py')],
                env={**os.environ, 'GROKBOT_BRIDGE_DATA': folder})
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    self.assertEqual({t.name for t in tools.tools}, {
                        'generate_video', 'get_video_status', 'wait_video', 'download_video', 'list_video_jobs'})
                    result = await client.call_tool('list_video_jobs', {'limit': 3})
                    self.assertFalse(result.isError)
                    self.assertEqual(result.content, [])
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(check(folder))


if __name__ == '__main__':
    unittest.main()
