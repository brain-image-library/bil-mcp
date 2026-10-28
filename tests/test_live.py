"""Contract tests against the REAL BIL API. Skipped by default.

    uv run pytest -m live

These check the API behaviours the server relies on. If one fails, BIL
changed, and the matching assumption in client.py / search.py needs updating.
"""

import pytest

from bil_mcp.client import BILAPIError, BILClient
from bil_mcp.files import list_files
from bil_mcp.search import search

pytestmark = pytest.mark.live


@pytest.fixture
async def live():
    c = BILClient()
    yield c
    await c.aclose()


async def test_query_is_case_insensitive(live):
    a = await live.query("specimen", "species", "mouse")
    b = await live.query("specimen", "species", "MOUSE")
    assert len(a) == len(b) > 1000


async def test_query_is_substring(live):
    male = set(await live.query("specimen", "sex", "Male"))
    female = set(await live.query("specimen", "sex", "Female"))
    assert female <= male  # "male" is inside "Female"


async def test_no_hits_is_404_json_and_maps_to_empty(live):
    r = await live._http.get("/query/specimen", params={"species": "axolotl-zzz"})
    assert r.status_code == 404 and r.json()["message"] == "GET failure, no entry found"
    assert await live.query("specimen", "species", "axolotl-zzz") == []


async def test_post_all_missing(live):
    r = await live._http.post("/retrieve", json={"bildids": ["nope-x"]})
    assert r.status_code == 405 and "no entries found" in r.json()["message"]
    assert await live.retrieve_many(["nope-x"]) == {}


async def test_one_variable_only(live):
    data = await live._request("GET", "/query/specimen", params={"species": "Human", "sex": "Female"})
    assert data["success"] == "false" and "one variable" in data["message"]


async def test_invalid_variable(live):
    with pytest.raises(BILAPIError):
        await live.query("dataset", "bogus", "x")


async def test_retrieve_post_partial(live):
    recs = await live.retrieve_many(["ace-ban-win", "nope-x-y"])
    assert list(recs) == ["ace-ban-win"]


async def test_end_to_end_search(live):
    r = await search(live, {"species": "Human", "technique": "MERFISH"})
    assert r.total > 0 and r.records_fetched and r.datasets[0].species and r.breakdowns


async def test_large_search_is_counts_only(live):
    r = await search(live, {"sex": "Male"})
    assert r.total > live.s.stats_limit and not r.records_fetched and r.next_steps


async def test_download_server_listing(live):
    """Verifies the directory-page parser on the real download server."""
    listing = await list_files(live, "ace-ban-win")
    assert listing.entries, listing.notes
