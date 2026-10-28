"""Live look at what search_datasets returns in each size band, and what it tells the agent.

Run:  uv run python scripts/explore_decisions.py
"""
import asyncio
import json
import time

from bil_mcp.client import BILClient
from bil_mcp.search import search

CASES = [
    ("40 matches", {"species": "Human", "technique": "MERFISH"}, {}),
    ("medium (20 newest)", {"generalmodality": "spatial transcriptomics", "sex": "Male"}, {"match": "exact"}),
    ("large (> 2000)", {"sex": "Male"}, {}),
    ("large, fetch_all", {"sex": "Male"}, {"fetch_all": True}),
    ("narrowed after fetch_all", {"sex": "Male", "technique": "MERFISH"}, {}),
]


async def main():
    async with BILClient() as c:
        for label, filters, kw in CASES:
            t = time.perf_counter()
            r = await search(c, filters, **kw)
            dt = time.perf_counter() - t
            size = len(r.model_dump_json())
            print(f"\n=== {label}: {filters} {kw}")
            print(f"total={r.total} records_fetched={r.records_fetched} rows={len(r.datasets)} "
                  f"time={dt:.2f}s response={size:,} chars")
            if r.specimen_sex_mix:
                print("specimen_sex_mix:", r.specimen_sex_mix)
            for b in r.breakdowns[:2]:
                print(f"breakdown {b.filter_key}: " + ", ".join(f"{v.value} {v.count}" for v in b.top))
            for step in r.next_steps:
                print("next_step:", step)


asyncio.run(main())
