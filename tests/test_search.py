"""Search composition: intersections, OR lists, exact matching, value counts, linkage."""

import copy

import httpx
import pytest

from bil_mcp.client import BILClient
from bil_mcp.config import Settings
from bil_mcp.fields import UnknownFieldError, resolve
from bil_mcp.search import SearchError, count_by_field, dataset_summaries, related_datasets, search

from conftest import RECORDS, FakeBIL


def ids(result):
    return [d.bildid for d in result.datasets]


def test_resolve_aliases_and_section_syntax():
    assert resolve("species").endpoint == "specimen"
    assert resolve("organism").key == "species"
    assert resolve("Specimen.species").key == "species"
    assert resolve("nhash").endpoint == "specimen/linkage"
    with pytest.raises(UnknownFieldError):
        resolve("spieces")


async def test_filters_are_anded(client):
    r = await search(client, {"technique": "MERFISH", "species": "Human"})
    assert r.total == 1 and r.datasets[0].bildid == "ace-can-ill"
    assert [f.matches for f in r.filters] == [6 - 1, 1]  # rut is not MERFISH


async def test_list_value_is_ored(client):
    r = await search(client, {"species": ["Human", "nothing-here"]})
    assert r.total == 1


async def test_substring_trap_and_exact_fix(client):
    # 'Male' is a substring of 'Female' -> rut (Female) sneaks in with contains.
    loose = await search(client, {"sex": "Male"})
    strict = await search(client, {"sex": "Male"}, match="exact")
    assert "rut" in ids(loose)
    assert "rut" not in ids(strict) and strict.verified
    assert strict.total == 3  # ace-boo-sag, ace-lot-web, ace-lot-wet
    assert any("substring" in n.lower() for n in loose.notes)


async def test_text_all_vs_any(client):
    all_ = await search(client, text="hypothalamus enhancer", text_mode="all")
    any_ = await search(client, text="hypothalamus enhancer", text_mode="any")
    assert all_.total == 0
    assert set(d.bildid for d in any_.datasets) == {"ace-boo-sag", "rut"}


async def test_rows_are_newest_first(client):
    r = await search(client, {"technique": "MERFISH"})
    dates = [d.bildate for d in r.datasets]
    assert r.total == 5 and len(r.datasets) == 5  # <= 20: every match is listed
    assert dates == sorted(dates, reverse=True)
    assert ids(r)[0] == "ace-ban-win"  # 2025-11-05


async def test_summary_pages_of_a_search(client):
    p1 = await dataset_summaries(client, filters={"technique": "MERFISH"}, limit=2)
    p2 = await dataset_summaries(client, filters={"technique": "MERFISH"}, limit=2, offset=2)
    assert p1.total == 5 and p1.order == "newest first"
    assert len(p1.datasets) == 2 and not set(ids(p1)) & set(ids(p2))
    assert ids(p1) + ids(p2) == ids(await search(client, {"technique": "MERFISH"}))[:4]


async def test_summaries_by_bildids_keep_order_and_report_missing(client):
    page = await dataset_summaries(client, ["rut", "nope-x", "ace-ban-win"], detail="brief")
    assert ids(page) == ["rut", "ace-ban-win"] and page.not_found == ["nope-x"]
    assert not hasattr(page.datasets[0], "viewer_url")  # brief rows


async def test_breakdowns_and_drill_down_hint(client):
    r = await search(client, {"technique": "MERFISH"})
    by = {b.filter_key: b for b in r.breakdowns}
    assert list(by) == ["species", "technique", "consortium", "contributorname", "sex"]
    assert by["species"].top[0].count == 4  # Mouse + mouse merged
    assert any("add {filter_key: value}" in n and '"contributorname"' in n for n in r.next_steps)
    # A breakdown value works as a filter.
    top_contrib = by["contributorname"].top[0]
    narrowed = await search(client, {"technique": "MERFISH", "contributorname": top_contrib.value})
    assert narrowed.total == top_contrib.count


async def test_sex_mix_per_dataset_and_in_rows():
    recs = copy.deepcopy(RECORDS)
    spec = recs["ace-lot-web"]["Specimen"]
    spec.append({**spec[0], "sex": "Female"})  # make one dataset mixed-sex
    c = BILClient(Settings(max_retries=0), transport=httpx.MockTransport(FakeBIL(recs)))
    try:
        r = await search(c, {"sex": "Male"}, match="exact")
    finally:
        await c.aclose()
    assert r.specimen_sex_mix == {"male only": 2, "mixed": 1}  # ANY specimen matches: mixed stays in
    row = next(d for d in r.datasets if d.bildid == "ace-lot-web")
    assert row.specimen_sex == "male 1 / female 1"


async def test_above_limit_returns_counts_and_asks(fake):
    c = BILClient(Settings(max_retries=0, stats_limit=3), transport=httpx.MockTransport(fake))
    try:
        r = await search(c, {"technique": "MERFISH"})
        assert r.total == 5 and not r.records_fetched
        assert r.breakdowns == [] and r.datasets == []
        assert not any(call.startswith("POST") for call in fake.calls)  # no records read
        steps = " ".join(r.next_steps)
        assert "fetch_all=true" in steps and "cached" in steps and "narrowing" in steps
        assert "match='exact'" not in steps  # no substring-prone filter here
        sex = await search(c, {"sex": "Male"}, match="exact")
        assert not sex.records_fetched and not sex.verified
        assert any("match='exact'" in n and "not make this set smaller" in n for n in sex.next_steps)
        with pytest.raises(SearchError, match="fetch_all"):
            await dataset_summaries(c, filters={"technique": "MERFISH"})

        full = await search(c, {"technique": "MERFISH"}, fetch_all=True)
        assert full.records_fetched and full.breakdowns
        posts = sum(call.startswith("POST") for call in fake.calls)
        # Narrowing afterwards reuses the cached records: no new POSTs.
        narrowed = await search(c, {"technique": "MERFISH", "species": "Human"})
        assert narrowed.total == 1 and narrowed.records_fetched
        assert sum(call.startswith("POST") for call in fake.calls) == posts
    finally:
        await c.aclose()


async def test_needs_some_criterion(client):
    with pytest.raises(SearchError):
        await search(client, {})


async def test_summary_fields(client):
    r = await dataset_summaries(client, filters={"dataset_doi": "10.35077/rut"})
    s = r.datasets[0]
    assert s.specimen_sex == "female 1"
    assert s.doi == "https://doi.org/10.35077/rut"
    assert s.download_url.startswith("https://download.brainimagelibrary.org/96/ba/96ba8210cceceeb7/")
    assert s.viewer_url.endswith("bildid=rut")


async def test_value_counts_merge_case_variants(client):
    f = await count_by_field(client, "species", {"technique": "MERFISH"})
    mouse = next(b for b in f.buckets if b.value.lower() == "mouse")
    assert mouse.count == 4 and set(mouse.variants) == {"Mouse", "mouse"}
    f2 = await count_by_field(client, "species", {"technique": "MERFISH"}, case_sensitive=True)
    assert {b.value for b in f2.buckets} >= {"Mouse", "mouse"}


async def test_related_by_donor(client):
    r = await related_datasets(client, "ace-lot-web", ["donor"])
    assert r.related[0].xid == "DO-OHTO1697"
    assert r.related[0].bildids == ["ace-lot-wet"]


async def test_related_without_linkage(client):
    r = await related_datasets(client, "rut")
    assert r.related == [] and r.notes


async def test_zero_hit_filter_gets_a_hint(client):
    r = await search(client, {"species": "humanh", "technique": "MERFISH"})
    assert r.total == 0
    assert any("'species=humanh' matched 0" in n and "Did you mean: Human" in n for n in r.notes)
