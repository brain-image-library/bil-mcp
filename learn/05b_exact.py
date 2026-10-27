"""Lesson 5b: what match="exact" actually checks.

Run from the repo root:   uv run python learn/05b_exact.py

'exact' does NOT search descriptions for words. Each BIL record is structured
JSON. The check reads one named field (here Specimen[].sex) from the record and
compares the WHOLE value to the filter value, ignoring upper/lower case.
A dataset can hold several specimens, so a record can have several sex values.
"""

import asyncio
from collections import Counter

from bil_mcp.client import BILClient
from bil_mcp.fields import BY_KEY, values_in_record
from bil_mcp.search import _record_matches_exact, _normalize_filters, matching_ids


async def main() -> None:
    c = BILClient()
    filters = {"sex": "Male", "generalmodality": "spatial transcriptomics"}
    ids, _, _ = await matching_ids(c, filters)          # the 533 'contains' candidates
    recs = await c.retrieve_many(ids)
    norm = _normalize_filters(filters)
    sex = BY_KEY["sex"]

    print("What the record looks like (ace-boo-sag, Specimen section, first specimen):")
    spec = recs["ace-boo-sag"]["Specimen"][0]
    print("  ", {k: spec[k] for k in ("localid", "species", "sex", "age", "ageunit")})

    print("\nThree real candidates, the sex values in their records, and the exact check:")
    examples = {}
    for i in ids:
        vals = set(values_in_record(recs[i], sex))
        kind = "only Female" if vals == {"Female"} else "only Male" if vals == {"Male"} else "mixed"
        examples.setdefault(kind, i)
    for kind, i in examples.items():
        vals = values_in_record(recs[i], sex)
        print(f"  {i:12} {kind:12} sex values={sorted(set(vals))} x{len(vals)}  "
              f"kept by exact: {_record_matches_exact(recs[i], norm)}")

    groups = Counter()
    for i in ids:
        vals = set(values_in_record(recs[i], sex))
        groups["only Female" if vals == {"Female"} else "only Male" if vals == {"Male"} else "mixed"] += 1
    print(f"\nAll {len(ids)} 'contains' candidates: {dict(groups)}")
    print("exact keeps 'only Male' + 'mixed' =", groups["only Male"] + groups["mixed"])
    await c.aclose()


asyncio.run(main())
