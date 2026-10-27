"""Re-record the real BIL records the offline tests run against.

    uv run python scripts/record_fixtures.py

The offline tests use these REAL records (not invented ones) behind a fake
API that reproduces BIL's query semantics. Re-run this if BIL's metadata
model changes, then re-run the tests.
"""

import json
from pathlib import Path

import httpx

API = "https://api.brainimagelibrary.org"
IDS = [
    "ace-ban-win",  # mouse MERFISH (BICCN, Zhuang), large
    "ace-can-ill",  # human MERFISH (BICCN, Kriegstein)
    "ace-boo-sag",  # mouse MERFISH hypothalamus, male
    "ace-lot-web",  # BICAN, with donor/tissue/section linkage
    "ace-lot-wet",  # BICAN, same donor as ace-lot-web
    "rut",          # older record with a DOI (Allen, STPT)
]
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    r = httpx.post(f"{API}/retrieve", json={"bildids": IDS}, timeout=60).json()
    recs = {}
    for rec in r["retjson"]:
        recs[rec["Assets"][0]["bildid"]] = rec
    missing = set(IDS) - set(recs)
    assert not missing, f"not found on BIL: {missing}"
    (OUT / "records.json").write_text(json.dumps(recs, indent=1, sort_keys=True))
    g = httpx.get(f"{API}/retrieve/groupdoi", params={"bildid": "g.8"}, timeout=60).json()
    (OUT / "groupdoi_g.8.json").write_text(json.dumps(g, indent=1, sort_keys=True))
    print(f"wrote {len(recs)} records + 1 group DOI to {OUT}")


if __name__ == "__main__":
    main()
