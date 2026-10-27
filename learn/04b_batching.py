"""Lesson 4b: what batching means, measured on the live BIL API.

Run from the repo root:   uv run python learn/04b_batching.py

A search query returns only ids. To show a title or species we need each
dataset's full record, from /retrieve. There are two ways to ask for 50 records:
  1. one at a time: 50 separate GET requests, each carrying one id
  2. batched:       1 POST request whose body carries all 50 ids
Plain httpx here (no bil_mcp code), so you see the raw requests.
"""

import json
import time

import httpx

API = "https://api.brainimagelibrary.org"

with httpx.Client(base_url=API, timeout=60) as h:
    ids = h.get("/query/dataset", params={"technique": "MERFISH"}).json()["bildids"][:50]
    print("50 ids from a MERFISH query, first 5:", ids[:5])

    print("\n========== 1. One at a time: GET /retrieve?bildid=<one id> ==========")
    t = time.perf_counter()
    one_by_one = [h.get("/retrieve", params={"bildid": i}).json()["retjson"][0] for i in ids]
    print(f"  example request : GET {API}/retrieve?bildid={ids[0]}")
    print(f"  {len(one_by_one)} records, {len(ids)} HTTP requests, {time.perf_counter() - t:.2f} s")

    print("\n========== 2. Batched: POST /retrieve with all ids in the body ==========")
    body = {"bildids": ids}
    print("  request body (first 120 chars):", json.dumps(body)[:120] + "...")
    t = time.perf_counter()
    reply = h.post("/retrieve", json=body).json()
    print(f"  {len(reply['retjson'])} records, 1 HTTP request, {time.perf_counter() - t:.2f} s")
    print("  reply also says which ids were found, e.g.:", dict(list(reply["status"].items())[:3]))

    got = [r["Assets"][0]["bildid"] for r in reply["retjson"]]
    print("\nSame set of records both ways:", set(got) == set(ids))
    print("Batch reply in the order we asked:", got == ids, "| first 5 returned:", got[:5])
    # BIL returns the batch in its own order, so client.py matches records to ids by
    # each record's Assets.bildid instead of trusting the position in the list.
