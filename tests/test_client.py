"""BILClient: HTTP behaviour, error mapping, caching. No MCP involved."""

import httpx
import pytest

from bil_mcp.client import BILAPIError, BILClient, TTLCache
from bil_mcp.config import Settings


async def test_query_returns_ids(client):
    ids = await client.query("specimen", "species", "human")
    assert ids == ["ace-can-ill"]


async def test_no_entry_found_is_empty_not_error(client):
    assert await client.query("specimen", "species", "axolotl") == []


async def test_invalid_variable_raises(client):
    with pytest.raises(BILAPIError, match="Not a valid variable"):
        await client.query("dataset", "bogus", "x")


async def test_unknown_endpoint_raises(client):
    with pytest.raises(BILAPIError, match="404"):
        await client.query("nosuch", "x", "1")


async def test_query_is_cached(client, fake):
    await client.query("dataset", "technique", "MERFISH")
    await client.query("dataset", "technique", "merfish")  # same query, different case
    assert len([c for c in fake.calls if "/query/dataset" in c]) == 1


async def test_retrieve_many_uses_cache_and_skips_missing(client, fake):
    recs = await client.retrieve_many(["rut", "nope-x", "ace-ban-win"])
    assert list(recs) == ["rut", "ace-ban-win"]  # order kept, missing dropped
    await client.retrieve_many(["rut", "ace-ban-win"])
    assert len([c for c in fake.calls if c.startswith("POST")]) == 1


async def test_retrieve_all_missing_returns_empty(client):
    assert await client.retrieve_many(["nope-x"]) == {}
    assert await client.retrieve("nope-x") is None


async def test_retrieve_by_doi(client):
    rec = await client.retrieve_by_doi("https://doi.org/10.35077/rut")
    assert rec["Assets"][0]["bildid"] == "rut"


async def test_group_doi_accepts_prefixes(client):
    for gid in ("g.8", "10.35077/g.8", "https://doi.org/10.35077/g.8"):
        doc = await client.group_doi(gid)
        assert doc["dataid"] == "10.35077/g.8"


async def test_retries_then_reports_unreachable():
    calls = 0

    def boom(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("down")

    c = BILClient(Settings(max_retries=2), transport=httpx.MockTransport(boom))
    with pytest.raises(BILAPIError, match="Could not reach"):
        await c.query("specimen", "species", "Mouse")
    assert calls == 3
    await c.aclose()


def test_ttl_cache_expires_and_evicts(monkeypatch):
    t = [0.0]
    monkeypatch.setattr("bil_mcp.client.time.monotonic", lambda: t[0])
    c = TTLCache(ttl=10, max_entries=2)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)  # evicts "a" (least recently used)
    assert c.get("a") is None and c.get("b") == 2
    t[0] = 11
    assert c.get("b") is None  # expired
