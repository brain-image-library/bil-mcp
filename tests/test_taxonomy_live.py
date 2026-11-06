"""Live checks that the shipped species map still describes BIL. Run nightly: uv run pytest -m live"""
import asyncio

import httpx
import pytest

from bil_mcp.taxonomy import Taxonomy, bil_species_query, exact_pattern

API = "https://api.brainimagelibrary.org"
pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def tax():
    return Taxonomy.load()


def test_every_bil_species_string_is_in_the_map(tax):
    """Fails when a new submission brings a species string we have not mapped yet."""
    stats = httpx.get(f"{API}/stats", params={"type": "all"}, timeout=60).json()
    live = {" ".join(s["name"].lower().split()) for s in stats["species"]}
    mapped = {s for h in tax.holdings.values() for s in h["bil_strings"]}
    assert live - mapped == set(), f"add to species_map.yaml, then rerun scripts/build_taxonomy.py: {sorted(live - mapped)}"


def test_every_mapped_string_still_has_datasets(tax):
    """Fails when a mapped string disappears from BIL (renamed, curated, or a typo in the map)."""
    async def run():
        async with httpx.AsyncClient(timeout=60) as c:
            strings = [s for h in tax.holdings.values() for s in h["bil_strings"]]
            counts = await asyncio.gather(*(bil_species_query(c, exact_pattern(s)) for s in strings))
            return {s: len(ids) for s, ids in zip(strings, counts)}
    empty = [s for s, n in asyncio.run(run()).items() if n == 0]
    assert empty == [], f"no datasets for {empty}"


def test_query_values_are_case_insensitive_regexes():
    """API fact: /query values are regexes. exact_pattern() depends on it."""
    n = lambda v: len(httpx.get(f"{API}/query/specimen", params={"species": v}, timeout=60).json()["bildids"])
    assert n("^mouse$") < n("mouse")          # anchoring excludes "spiny mouse"
    assert n("^MOUSE$") == n("^mouse$")       # case-insensitive
    assert httpx.get(f"{API}/query/specimen", params={"species": "("}, timeout=60).status_code == 500
