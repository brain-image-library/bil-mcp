"""Lesson 8b: what text_mode="any" and "all" actually send to BIL.

Run from the repo root:   uv run python learn/08b_text_modes.py

Prints, for each mode, the exact query URLs the server sends (from the
result's `filters`) and the hit counts. Live BIL API.
"""

import asyncio

from bil_mcp.client import BILClient
from bil_mcp.search import search


async def main() -> None:
    c = BILClient()
    for text in ("axolotl Ambystoma", "axolotl zebrafish"):
        for mode in ("any", "all"):
            r = await search(c, text=text, text_mode=mode, limit=0)
            print(f"text={text!r:22} text_mode={mode!r:6} total={r.total}")
            for f in r.filters:
                print(f"    {f.query_url}   -> {f.matches} matches")
    await c.aclose()


asyncio.run(main())
