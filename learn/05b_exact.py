"""Lesson 5b: what match="exact" actually checks, on real BIL records.

Run from the repo root:   uv run python learn/05b_exact.py

For sex=Male + spatial transcriptomics, the API's substring match returns 533
candidates. For each candidate, "exact" fetches its full metadata record, reads
the one structured field the filter names (Specimen -> sex), and keeps the
dataset only if that value equals "Male", ignoring upper/lower case. It does
not search free text.
"""

import asyncio
from collections import Counter

from bil_mcp.client import BILClient
from bil_mcp.fields import BY_KEY, values_in_record
from bil_mcp.search import matching_ids


async def main() -> None:
    c = BILClient()
    ids, _, _ = await matching_ids(c, {"sex": "Male", "generalmodality": "spatial transcriptomics"})
    recs = await c.retrieve_many(ids)
    sex = BY_KEY["sex"]

    kept, dropped = [], []
    for i in ids:
        values = values_in_record(recs[i], sex)          # e.g. ['Female']
        (kept if any(v.strip().lower() == "male" for v in values) else dropped).append((i, values))

    print(f"candidates from BIL (substring 'male'): {len(ids)}")
    print(f"kept by exact check                   : {len(kept)}")
    print(f"dropped                               : {len(dropped)}")
    print("\nSpecimen.sex values among the DROPPED datasets:",
          Counter(v for _, vals in dropped for v in vals).most_common())
    print("\nTwo real examples:")
    for i, vals in (kept[0], dropped[0]):
        title = recs[i]["Dataset"][0]["title"]
        print(f"  {i}: Specimen.sex = {vals}  ->  {'KEPT' if (i, vals) in kept else 'DROPPED'}   ({title[:60]})")
    await c.aclose()


asyncio.run(main())
