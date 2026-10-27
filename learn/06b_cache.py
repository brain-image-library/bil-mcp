"""Lesson 6b: what the cache is, and what an HTTP client keeps open.

Run from the repo root:   uv run python learn/06b_cache.py

Uses BILClient from src/bil_mcp/client.py against the live BIL API and looks
inside its cache (client._cache._d, an OrderedDict in this process's memory).
"""

import asyncio
import json
import time

import httpx

from bil_mcp.client import BILClient
from bil_mcp.config import Settings
from bil_mcp.search import search

API = "https://api.brainimagelibrary.org"


def describe(value) -> str:
    if isinstance(value, list):
        return f"list of {len(value)} ids, e.g. {value[:2]}"
    return f"dict (full record), sections {list(value)[:4]}..., {len(json.dumps(value))} chars as JSON"


async def main() -> None:
    print("========== A. What is in the cache after one search ==========")
    c = BILClient()
    await search(c, {"species": "Human", "technique": "MERFISH"}, limit=3)
    for key, (stored_at, value) in c._cache._d.items():
        print(f"  {key:32} -> {describe(value)}")
    print(f"  entries: {len(c._cache)} (limit {c._cache.max_entries}), each expires {c._cache.ttl:.0f} s after it was stored")

    print("\n========== B. How big can it get? ==========")
    ids = await c.query("dataset", "generalmodality", "spatial transcriptomics")
    recs = await c.retrieve_many(ids)
    sizes = [len(json.dumps(r)) for r in recs.values()]
    print(f"  {len(recs)} records cached, average {sum(sizes) / len(sizes) / 1000:.1f} KB as JSON, "
          f"largest {max(sizes) / 1000:.0f} KB, total {sum(sizes) / 1e6:.1f} MB")
    await c.aclose()

    print("\n========== C. Expiry: a cache with a 2-second TTL ==========")
    sent = []
    c = BILClient(Settings(cache_ttl_s=2))
    c._http.event_hooks["request"].append(lambda req: _append(sent, req))
    for label, wait in (("first call", 0), ("again at once", 0), ("again after 2.5 s", 2.5)):
        await asyncio.sleep(wait)
        before = len(sent)
        await c.query("dataset", "technique", "MERFISH")
        print(f"  {label:18} HTTP requests: {len(sent) - before}")
    await c.aclose()

    print("\n========== D. Size limit: a cache that holds only 2 entries ==========")
    c = BILClient(Settings(cache_max_entries=2))
    for tech in ("MERFISH", "fMOST", "STPT"):
        await c.query("dataset", "technique", tech)
        print(f"  after querying {tech:8} cache keys: {list(c._cache._d)}")
    await c.aclose()

    print("\n========== E. HTTP client: reusing one open connection ==========")
    async with httpx.AsyncClient(base_url=API) as h:            # one client, kept for all 5 requests
        times = []
        for _ in range(5):
            t = time.perf_counter()
            await h.get("/query/dataset", params={"technique": "MERFISH"})
            times.append(time.perf_counter() - t)
        print("  one client, 5 requests (s):        ", [round(x, 3) for x in times])
    times = []
    for _ in range(5):                                          # a brand-new client for every request
        async with httpx.AsyncClient(base_url=API) as h:
            t = time.perf_counter()
            await h.get("/query/dataset", params={"technique": "MERFISH"})
            times.append(time.perf_counter() - t)
    print("  new client for every request (s):  ", [round(x, 3) for x in times])


async def _append(sent, req):
    sent.append(req.url.path)


asyncio.run(main())
