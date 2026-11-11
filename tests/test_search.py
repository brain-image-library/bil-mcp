"""Search composition: intersections, OR lists, exact sex matching, value counts, linkage."""

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


async def test_sex_is_always_exact(client, fake):
    # 'Male' is a substring of 'Female': BIL returns rut (Female) too; the server drops it.
    r = await search(client, {"sex": "Male"})
    assert any("sex=Male" in c for c in fake.calls)
    assert "rut" not in ids(r) and r.verified
    assert r.total == 3  # ace-boo-sag, ace-lot-web, ace-lot-wet


async def test_sex_exact_does_not_tighten_other_filters(client):
    # The bug this design fixes: an exact check on consortium turned "BICAN" into 0 datasets.
    r = await search(client, {"sex": "Male", "consortium": "BICAN"})
    assert r.total > 0 and all(d.specimen_sex.startswith("male") for d in r.datasets)


async def test_note_says_when_the_sex_check_emptied_the_search(client):
    r = await search(client, {"sex": "ale"})  # substring of Male and Female, equal to neither
    assert r.total == 0
    assert any("none has a specimen with exactly sex=ale" in n for n in r.notes)


async def test_count_by_field_checks_sex_too(client):
    f = await count_by_field(client, "species", {"sex": "Male"})
    assert f.datasets_total == f.datasets_considered == 3


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
        r = await search(c, {"sex": "Male"})
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
        assert "upper bound" not in steps  # no sex filter here
        sex = await search(c, {"sex": "Male"})
        assert not sex.records_fetched and not sex.verified
        assert any("upper bound" in n for n in sex.next_steps)
        assert any("sex=Male is not checked yet" in n for n in sex.notes)
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
    mouse = next(b for b in f.buckets if getattr(b, "taxon_id", None) == "NCBI:txid10090")
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
    r = await search(client, {"technique": "MERFISHH"})
    assert r.total == 0
    assert any("'technique=MERFISHH' matched 0" in n for n in r.notes)


# ---------- species through the taxonomy ----------
async def test_species_goes_through_taxonomy(client, fake):
    r = await search(client, {"species": "Mouse"})
    assert r.total == 5  # mouse + Mouse, every fixture record but the human one
    rep, = r.species_report
    assert rep["resolved"] and rep["datasets"] == 5
    sent = [c for c in fake.calls if "species=" in c]
    # One anchored (^...$) query per BIL mouse string, plus one for 'spiny mouse' to report it as not included.
    assert len(sent) == 4 and all("%5E" in c for c in sent)
    assert [n["bil_string"] for n in rep["not_included"]] == []  # no spiny mouse in the fixtures
    assert any("matched by organism" in n for n in r.notes)


async def test_species_or_list_unions_terms(client):
    r = await search(client, {"species": ["human", "mouse"]})
    assert r.total == 6 and [rep["term"] for rep in r.species_report] == ["human", "mouse"]


async def test_unknown_species_returns_nothing_and_queries_nothing(client, fake):
    r = await search(client, {"species": "axolotl", "technique": "MERFISH"})
    assert r.total == 0 and fake.calls == []
    rep, = r.species_report
    assert rep["resolved"] is False and rep["available"]
    assert any("BIL was not queried" in n for n in r.notes)


async def test_species_scientific_name_finds_common_name_records(client):
    # The fixture's human record says "human"; "Homo sapiens" must still find it.
    r = await search(client, {"species": "Homo sapiens"})
    assert r.total == 1 and r.datasets[0].bildid == "ace-can-ill"


async def test_count_by_field_uses_taxonomy(client):
    f = await count_by_field(client, "technique", {"species": "rodents"})
    assert f.datasets_total == 5


# ---------- regex escaping ----------
async def test_filter_values_are_regex_escaped(client, fake):
    r = await search(client, {"technique": "MERFISH (x"})  # unescaped, '(' is an invalid regex: HTTP 500
    assert r.total == 0
    assert any("technique=MERFISH%5C+%5C%28x" in c for c in fake.calls)
    assert r.filters[0].filter == "technique=MERFISH (x"  # the label keeps what the user typed


async def test_escaping_keeps_substring_behaviour(client):
    assert (await search(client, {"technique": "merf"})).total == 5


# ---------- species grouped by organism in breakdowns and count_by_field ----------
def _client_with_species(spellings: dict[str, list[str]]) -> BILClient:
    """Fixture records with their specimen species replaced (bildid -> species of each specimen)."""
    recs = copy.deepcopy(RECORDS)
    for bid, species in spellings.items():
        template = recs[bid]["Specimen"][0]
        recs[bid]["Specimen"] = [{**template, "species": s} for s in species]
    return BILClient(Settings(max_retries=0), transport=httpx.MockTransport(FakeBIL(recs)))


MACAQUE_SPELLINGS = {
    "ace-ban-win": ["Macaca mulatta"], "ace-boo-sag": ["Macaca mulatta"], "ace-lot-web": ["rhesus macaque"],
    "ace-lot-wet": ["Rhesus Macaque"], "rut": ["macaque"], "ace-can-ill": ["pig-tailed macaque"],
}


async def test_species_breakdown_groups_by_organism_with_spellings():
    c = _client_with_species(MACAQUE_SPELLINGS)
    try:
        r = await search(c, {"species": "macaque"})
    finally:
        await c.aclose()
    sp = next(b for b in r.breakdowns if b.filter_key == "species")
    assert sp.distinct_values == 2  # two organisms, five stored spellings
    mulatta, nemestrina = sp.top
    assert (mulatta.value, mulatta.taxon_id, mulatta.count) == ("Macaca mulatta", "NCBI:txid9544", 5)
    assert mulatta.spellings == {"Macaca mulatta": 2, "rhesus macaque": 2, "macaque": 1}  # case merged
    assert (nemestrina.value, nemestrina.count, nemestrina.spellings) == (
        "Macaca nemestrina", 1, {"pig-tailed macaque": 1})


async def test_count_by_field_groups_species_and_counts_each_dataset_once():
    c = _client_with_species({"rut": ["macaca mulatta", "rhesus macaque"]})  # one dataset, two spellings
    try:
        f = await count_by_field(c, "species", bildids=["rut"])
    finally:
        await c.aclose()
    b, = f.buckets
    assert b.count == 1 and b.spellings == {"macaca mulatta": 1, "rhesus macaque": 1}


async def test_unmapped_species_value_keeps_its_own_bucket():
    c = _client_with_species({"rut": ["axolotl"]})
    try:
        f = await count_by_field(c, "species", bildids=["rut"])
    finally:
        await c.aclose()
    assert [(b.value, type(b).__name__) for b in f.buckets] == [("axolotl", "ValueCount")]  # no organism fields


async def test_case_sensitive_keeps_raw_species_values(client):
    f = await count_by_field(client, "species", {"technique": "MERFISH"}, case_sensitive=True)
    assert all(type(b).__name__ == "ValueCount" for b in f.buckets)


def test_every_organism_name_in_a_breakdown_works_as_a_filter():
    """Breakdown rows say 'add {filter_key: value}'; for species the value must resolve back to that organism."""
    from bil_mcp.search import default_taxonomy
    tax = default_taxonomy()
    for tid in tax.holdings:
        assert tax.resolve(tax.taxa[tid]["scientific_name"]).organisms == [tid]
