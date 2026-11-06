"""Offline tests for bil_mcp.taxonomy: the shipped data files and the resolve/search logic.

The fake BIL below behaves like the real one: query values are case-insensitive regexes searched
anywhere in the species string.
"""
import asyncio
import re

import pytest

from bil_mcp.taxonomy import Taxonomy, exact_pattern

FAKE_BIL = {  # BIL ID -> specimen species string, as BIL stores it
    "m1": "mouse", "m2": "Mouse", "m3": "mus musculus", "m4": "mus musculus (house mouse)",
    "s1": "spiny mouse",
    "h1": "human", "h2": "homo sapiens",
    "r1": "macaca mulatta", "r2": "rhesus macaque", "r3": "macaque", "r4": "m. mulatta (rhesus macaque)",
    "n1": "pig-tailed macaque", "f1": "crab-eating macaque",
}


@pytest.fixture(scope="module")
def tax():
    return Taxonomy.load()


def fake_query(calls):
    async def query(pattern):
        calls.append(pattern)
        return {i for i, sp in FAKE_BIL.items() if re.search(pattern, sp, re.IGNORECASE)}
    return query


def search(tax, term):
    calls = []
    out = asyncio.run(tax.search(term, fake_query(calls)))
    return out, calls


# ---------- the data files ----------
def test_every_curated_organism_has_ncbi_lineage(tax):
    for tid in tax.holdings:
        assert tax.taxa[tid].get("lineage"), f"rerun scripts/build_taxonomy.py: no lineage for {tid}"


def test_no_bil_string_belongs_to_two_organisms(tax):
    seen = {}
    for tid, h in tax.holdings.items():
        for s in h["bil_strings"]:
            assert s not in seen, f"{s!r} is listed under {seen[s]} and {tid}"
            seen[s] = tid


def test_everyday_groups_name_real_ncbi_taxa(tax):
    for g in tax.groups.values():
        for name in [*g["include"], *g.get("exclude", [])]:
            tax._id_of(name)      # raises if the generated file lacks it


# ---------- resolving ----------
@pytest.mark.parametrize("term, expected", [
    ("mouse", {10090}),
    ("Mouse ", {10090}),
    ("rhesus", {9544}),
    ("macaque", {9544, 9545, 9541}),                 # genus, by decision
    ("monkeys", {9483, 9521, 9544, 9545, 9541}),     # no humans
    ("invertebrates", {7227, 2015173, 327109, 1774208}),
    ("fish", {7955}),
    ("insects", {7227, 2015173}),
    ("insect", {7227, 2015173}),
    ("rodents", {10090, 10116, 60744}),
    ("NCBI:txid9606", {9606}),
    ("NCBITaxon:9443", {9606, 9483, 9521, 9544, 9545, 9541}),   # Primates, OBO-style ID
    ("axolotl", set()),
])
def test_resolve(tax, term, expected):
    assert set(tax.resolve(term).organisms) == expected


# ---------- searching ----------
def test_mouse_is_exact_and_reports_spiny_mouse(tax):
    out, _ = search(tax, "mouse")
    assert out["ids"] == ["m1", "m2", "m3", "m4"]
    assert [n["bil_string"] for n in out["report"]["not_included"]] == ["spiny mouse"]


def test_human_reports_both_strings(tax):
    out, _ = search(tax, "human")
    assert out["report"]["datasets"] == 2
    assert [q["bil_string"] for q in out["report"]["queries"]] == ["homo sapiens", "human"]


def test_rhesus_includes_bare_macaque_with_note(tax):
    out, _ = search(tax, "rhesus")
    assert out["ids"] == ["r1", "r2", "r3", "r4"]
    assert any("labelled only" in n for n in out["report"]["notes"])


def test_macaque_genus_breaks_down_by_species(tax):
    out, _ = search(tax, "macaque")
    by = {o["taxon_id"]: o["datasets"] for o in out["report"]["by_organism"]}
    assert by == {"NCBI:txid9541": 1, "NCBI:txid9544": 4, "NCBI:txid9545": 1}


def test_unknown_term_does_not_query_bil(tax):
    out, calls = search(tax, "axolotl")
    assert calls == [] and out["ids"] == []
    assert out["report"]["resolved"] is False and len(out["report"]["available"]) == len(tax.holdings)


def test_exact_pattern_escapes_regex_characters():
    p = exact_pattern("m. mulatta (rhesus macaque)")
    assert re.search(p, "M. Mulatta (rhesus macaque) ", re.IGNORECASE)
    assert not re.search(p, "mx mulatta (rhesus macaque)", re.IGNORECASE)
