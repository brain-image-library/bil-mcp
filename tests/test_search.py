"""Search composition: intersections, OR lists, exact matching, value counts, linkage."""

import pytest

from bil_mcp.fields import UnknownFieldError, resolve
from bil_mcp.search import SearchError, count_by_field, related_datasets, search


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
    loose = await search(client, {"sex": "Male"}, limit=0)
    strict = await search(client, {"sex": "Male"}, match="exact", limit=0)
    assert "rut" in loose.bildids
    assert "rut" not in strict.bildids and strict.verified
    assert strict.total == 3  # ace-boo-sag, ace-lot-web, ace-lot-wet
    assert any("substring" in n.lower() for n in loose.notes)


async def test_text_all_vs_any(client):
    all_ = await search(client, text="hypothalamus enhancer", text_mode="all")
    any_ = await search(client, text="hypothalamus enhancer", text_mode="any")
    assert all_.total == 0
    assert set(d.bildid for d in any_.datasets) == {"ace-boo-sag", "rut"}


async def test_paging(client):
    p1 = await search(client, {"technique": "MERFISH"}, limit=2)
    p2 = await search(client, {"technique": "MERFISH"}, limit=2, offset=2)
    ids1 = [d.bildid for d in p1.datasets]
    ids2 = [d.bildid for d in p2.datasets]
    assert len(ids1) == 2 and not set(ids1) & set(ids2)
    assert ids1 == sorted(ids1)


async def test_needs_some_criterion(client):
    with pytest.raises(SearchError):
        await search(client, {})


async def test_summary_fields(client):
    r = await search(client, {"dataset_doi": "10.35077/rut"})
    s = r.datasets[0]
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
