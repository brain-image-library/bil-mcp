"""The real transport: launch the server as a subprocess and talk over stdin/stdout.

This is exactly what Claude Desktop / Claude Code do. Listing tools needs no
network, so this runs offline.
"""

import sys

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


async def test_stdio_handshake_and_list_tools():
    params = StdioServerParameters(command=sys.executable, args=["-m", "bil_mcp"])
    async with Client(params) as c:
        tools = {t.name for t in (await c.list_tools()).tools}
        assert "search_datasets" in tools
        assert (await c.read_resource("bil://fields")).contents[0].text.startswith("[")
