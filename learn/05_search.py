"""Lesson 5: search logic (src/bil_mcp/search.py) on the live BIL API, no MCP involved.

Run from the repo root:   uv run python learn/05_search.py

Each section calls one function from search.py and prints what it returned,
plus how many HTTP requests it cost (counted with an httpx event hook, as in
lesson 4). The cache is shared across sections, so repeated sub-queries are free.
"""

import asyncio

import httpx

from bil_mcp.client import BILClient
from bil_mcp.search import facet_counts, related_datasets, search

sent: list[str] = []


async def count(request: httpx.Request) -> None:
    sent.append(f"{request.method} {request.url.path}")


def show_filters(r) -> None:
    for f in r.filters:
        print(f"    {f.filter:45} {f.matches:6} matches")


async def main() -> None:
    c = BILClient()
    c._http.event_hooks["request"].append(count)

    print("========== A. AND across fields = intersection of id sets ==========")
    sent.clear()
    r = await search(c, {"species": "Human", "technique": "MERFISH"}, limit=0)
    show_filters(r)
    print(f"  total (in both sets): {r.total}   HTTP requests: {sent}")
    human = set(await c.query("specimen", "species", "Human"))       # cached now: no request
    merfish = set(await c.query("dataset", "technique", "MERFISH"))
    print(f"  same thing by hand, len(human & merfish) = {len(human & merfish)}")

    print("\n========== B. A list value = OR within one field (union) ==========")
    r = await search(c, {"species": ["Human", "Macaque"], "technique": "MERFISH"}, limit=0)
    show_filters(r)
    print(f"  total: {r.total}")

    print("\n========== C. Multi-word text: 'all' (AND) vs 'any' (BIL's own OR) ==========")
    for mode in ("all", "any"):
        r = await search(c, text="motor cortex", text_mode=mode, limit=0)
        print(f"  text_mode={mode!r:6} total={r.total:6}   queries: {[f.filter for f in r.filters]}")

    print("\n========== D. 'contains' (BIL substring) vs 'exact' (checked against full records) ==========")
    for match in ("contains", "exact"):
        sent.clear()
        r = await search(c, {"sex": "Male", "generalmodality": "spatial transcriptomics"}, match=match, limit=0)
        print(f"  match={match!r:10} total={r.total:4}  verified={r.verified}  HTTP requests: {len(sent)}")

    print("\n========== E. Facets: what's inside a result set ==========")
    sent.clear()
    f = await facet_counts(c, "species", {"generalmodality": "spatial transcriptomics"}, top=6)
    print(f"  {f.datasets_considered} datasets counted, HTTP requests: {len(sent)}")
    for b in f.buckets:
        print(f"    {b.value:20} {b.count:4}   merged spellings: {b.variants}")

    print("\n========== F. Linkage: other datasets from the same BICAN donor ==========")
    rel = await related_datasets(c, "ace-lot-web", ["donor"])
    for link in rel.linkages:
        print(f"    linkage on ace-lot-web: {link['relationship']:8} {link['xid']}")
    for g in rel.related:
        print(f"  {g.relationship} {g.xid}: {len(g.bildids)} other datasets, first 3: {g.bildids[:3]}")

    await c.aclose()


asyncio.run(main())
