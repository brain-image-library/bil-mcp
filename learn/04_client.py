"""Lesson 4: the BIL client (src/bil_mcp/client.py), with no MCP involved.

Run from the repo root:   uv run python learn/04_client.py

Uses the live BIL API. Every HTTP request the client sends is printed by an
httpx "event hook" (a function httpx calls before each request), so you can
see exactly what goes over the network.
"""

import asyncio
import time

import httpx

from bil_mcp.client import BILAPIError, BILClient
from bil_mcp.config import Settings

API = "https://api.brainimagelibrary.org"


async def main() -> None:
    print("========== A. A BIL quirk, seen with plain httpx ==========")
    async with httpx.AsyncClient(base_url=API) as h:
        r = await h.get("/query/specimen", params={"species": "axolotl"})
        print("GET /query/specimen?species=axolotl")
        print("  status code :", r.status_code)
        print("  body        :", r.text)

    c = BILClient()
    sent: list[str] = []

    async def log_request(request: httpx.Request) -> None:
        sent.append(f"{request.method} {request.url.path}?{request.url.query.decode()}")

    c._http.event_hooks["request"].append(log_request)

    print("\n========== B. The client turns that quirk into an empty list ==========")
    print("c.query('specimen', 'species', 'axolotl') ->", await c.query("specimen", "species", "axolotl"))

    print("\n========== C. Cache: the same query twice ==========")
    for attempt in (1, 2):
        sent.clear()
        t = time.perf_counter()
        ids = await c.query("dataset", "technique", "MERFISH")
        print(f"  call {attempt}: {len(ids)} ids in {time.perf_counter() - t:.3f} s, HTTP requests sent: {sent}")

    print("\n========== D. Batching: full records for all 280 MERFISH ids ==========")
    sent.clear()
    t = time.perf_counter()
    recs = await c.retrieve_many(ids)
    print(f"  {len(recs)} records in {time.perf_counter() - t:.2f} s, using {len(sent)} HTTP requests:")
    for s in sent:
        print("   ", s)
    await c.aclose()

    print("\n========== E. Failure: an address that doesn't answer ==========")
    bad = BILClient(Settings(api_base="https://api.brainimagelibrary.invalid", max_retries=2, timeout_s=5))
    t = time.perf_counter()
    try:
        await bad.query("specimen", "species", "Mouse")
    except BILAPIError as e:
        print(f"  BILAPIError after {time.perf_counter() - t:.1f} s: {e}")
    await bad.aclose()


asyncio.run(main())
