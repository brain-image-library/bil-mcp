"""Lesson 3b: what limit and offset do, plus what happens to an argument name the tool does not have.

Run from the repo root:   uv run python learn/03b_paging.py

Uses the SDK client (mcp.client.Client), which does the JSON-RPC from lessons 1-3
for you, the same job a host's MCP client does. It hits the live BIL API.
"""

import asyncio
from mcp.client import Client
import bil_mcp.server as srv

async def main():
    async with Client(srv.mcp) as c:
        for args in [{"limit": 3, "offset": 0}, {"limit": 3, "offset": 3}, {"limit": 3, "bogus_arg": 5}]:
            r = await c.call_tool("search_datasets", {"filters": {"species": "Human", "technique": "MERFISH"}, **args})
            s = r.structured_content
            if r.is_error:
                print(args, "-> ERROR", r.content[0].text[:200]); continue
            print(args, "-> total", s["total"], "| returned", s["returned"], "|", [d["bildid"] for d in s["datasets"]])
asyncio.run(main())
