"""Search logic: compose single-field BIL queries into multi-filter searches.

The BIL API accepts ONE field per query. This module turns
``{"species": "Human", "technique": "MERFISH"}`` into two API calls run
concurrently and intersects the returned id sets. It also:

* supports OR within one field: ``{"species": ["Human", "Macaque"]}``
* makes multi-word full text behave as AND (the API ORs words)
* checks ``sex`` against the full records, because BIL matching is
  substring-based (``sex=male`` would also match "Female"); every other
  filter matches as a substring, which is what people want there
* routes the ``species`` filter through ``taxonomy.Taxonomy``, which turns a
  term ("mouse", "macaque", "insects") into exact queries for every BIL
  species string under that taxon, and reports what it did

BIL ``/query`` values are case-insensitive regular expressions. Every other
filter value is sent through ``re.escape``, so it still matches as a plain
substring and characters like ``( + [ .`` no longer break the query.

Getting ids is cheap (one GET per filter value); full records cost POSTs
(~0.3 s per 200). So up to ``Settings.stats_limit`` matches, ``search()``
fetches every record and returns breakdowns plus a few brief rows: the
response stays small however many records were read. Above the limit it
returns counts only, and the agent asks the user whether to narrow the
search or fetch everything (``fetch_all=True``).

Division of work: the agent turns the user's question into filters
("mouse spatial transcriptomics from BICAN" -> species, generalmodality,
consortium); this server turns filter values into correct BIL queries
("mouse" -> the three BIL strings for Mus musculus).
"""

from __future__ import annotations

import asyncio
import difflib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from typing import Literal, Union
from urllib.parse import urlencode

from .client import BILClient
from .config import Settings
from .fields import BY_KEY, Field, resolve, values_in_record
from .models import (
    Breakdown,
    SpeciesCount,
    DatasetSummary,
    ValueCount,
    FieldCounts,
    FilterStat,
    RelatedGroup,
    RelatedResult,
    SearchResult,
    SummaryPage,
    bildate,
    brief,
    sex_mix,
    summarize,
)
from .taxonomy import Taxonomy

FilterValue = Union[str, list[str]]
# Filters the server checks for an exact value against the full records (case and surrounding spaces
# ignored), because substring matching is always wrong for them: "male" is inside "Female".
# Every other filter matches as a substring ("BICAN" finds "Brain Initiative Cell Atlas Network (BICAN)").
EXACT_FIELDS = ("sex",)

ROWS_SHOWN = 20        # brief rows in a search result: all matches if total <= 20, else the 20 newest
BREAKDOWN_TOP = 5      # values listed per breakdown field
BREAKDOWN_FIELDS = ("species", "technique", "consortium", "contributorname", "sex")
SEX_MIX_ORDER = ("male only", "female only", "mixed", "unknown or not given")


class SearchError(ValueError):
    pass


@cache
def default_taxonomy() -> Taxonomy:
    """The shipped taxonomy, loaded once. The server passes its own (loaded in the lifespan)."""
    return Taxonomy.load()


def _query_url(s: Settings, endpoint: str, field: str, value: str) -> str:
    return f"{s.api_base}/query/{endpoint}?{urlencode({field: value})}"


async def _ids_for_filter(client: BILClient, f: Field, values: list[str],
                          escape: bool = True) -> tuple[set[str], list[FilterStat]]:
    """Union of ids over the values of ONE field (OR semantics).

    BIL treats the value as a regex, so it is escaped: it then matches as a
    literal substring. (Full text is sent as is.)"""
    sent = [re.escape(v) if escape else v for v in values]
    results = await asyncio.gather(*(client.query(f.endpoint, f.field, v) for v in sent))
    stats = [
        FilterStat(filter=f"{f.key}={v}", query_url=_query_url(client.s, f.endpoint, f.field, q), matches=len(ids))
        for v, q, ids in zip(values, sent, results)
    ]
    union: set[str] = set()
    for ids in results:
        union.update(ids)
    return union, stats


def _normalize_filters(filters: dict[str, FilterValue] | None) -> list[tuple[Field, list[str]]]:
    out = []
    for key, val in (filters or {}).items():
        f = resolve(key)
        vals = [val] if isinstance(val, str) else list(val)
        vals = [v.strip() for v in vals if v and v.strip()]
        if not vals:
            raise SearchError(f"Filter {key!r} has an empty value.")
        out.append((f, vals))
    return out


async def _ids_for_species(client: BILClient, tax: Taxonomy, terms: list[str]) -> tuple[set[str], list[FilterStat], list[dict]]:
    """Union of ids over species terms, each resolved through the taxonomy.

    Returns the ids, one FilterStat per term, and the taxonomy report per term."""
    f = BY_KEY["species"]

    async def query(pattern: str) -> set[str]:
        return set(await client.query(f.endpoint, f.field, pattern))  # client cache + retry

    outs = await asyncio.gather(*(tax.search(t, query) for t in terms))
    stats, reports, union = [], [], set()
    for term, out in zip(terms, outs):
        rep = out["report"]
        qs = rep.get("queries", [])
        if not rep["resolved"]:
            url = "not sent: the term is not an organism BIL holds (see species_report)"
        elif len(qs) == 1:
            url = f"{client.s.api_base}{qs[0]['sent_to_bil']}"
        else:
            url = f"{len(qs)} queries, one per BIL species string (see species_report)"
        stats.append(FilterStat(filter=f"species={term}", query_url=url, matches=len(out["ids"])))
        reports.append(rep)
        union.update(out["ids"])
    return union, stats, reports


@dataclass
class Matches:
    ids: list[str]                             # sorted for stable paging
    stats: list[FilterStat]
    norm: list[tuple[Field, list[str]]]
    species_report: list[dict] = field(default_factory=list)   # one per species term


async def matching_ids(
    client: BILClient,
    filters: dict[str, FilterValue] | None = None,
    text: str | None = None,
    text_mode: Literal["all", "any"] = "all",
    tax: Taxonomy | None = None,
) -> Matches:
    """Ids matching every filter (and the text).

    If no species term resolves to an organism BIL holds, nothing is sent to
    BIL: the result is empty, with the taxonomy report saying why."""
    norm = _normalize_filters(filters)
    if not norm and not (text and text.strip()):
        raise SearchError("Give at least one filter or some text.")
    tax = tax or default_taxonomy()

    species_groups = [vals for f, vals in norm if f.key == "species"]
    if any(not any(tax.resolve(t).organisms for t in vals) for vals in species_groups):
        # A species filter that matches no organism BIL holds empties the search: send nothing.
        outs = [await _ids_for_species(client, tax, vals) for vals in species_groups]  # these send no queries
        return Matches([], [st for o in outs for st in o[1]], norm, [r for o in outs for r in o[2]])

    tasks = [_ids_for_species(client, tax, vals) if f.key == "species" else _ids_for_filter(client, f, vals)
             for f, vals in norm]
    if text and text.strip():
        text_terms = text.split() if text_mode == "all" else [text.strip()]
        tasks += [_ids_for_filter(client, _FULLTEXT, [t], escape=False) for t in text_terms]

    results = await asyncio.gather(*tasks)
    stats = [st for r in results for st in r[1]]
    reports = [rep for r in results if len(r) == 3 for rep in r[2]]
    sets = [r[0] for r in results]
    hits = set.intersection(*sets) if sets else set()
    return Matches(sorted(hits), stats, norm, reports)


# A pseudo-field so full text goes through the same code path.
_FULLTEXT = Field("text", "*", "fulltext", "text", "Full-text search")


def _exact_filters(norm: list[tuple[Field, list[str]]]) -> list[tuple[Field, list[str]]]:
    return [(f, vals) for f, vals in norm if f.key in EXACT_FIELDS]


def _record_matches_exact(rec: dict, exact: list[tuple[Field, list[str]]]) -> bool:
    """True if, for every EXACT_FIELDS filter, a specimen/field value equals one of the filter values."""
    for f, vals in exact:
        have = {v.strip().lower() for v in values_in_record(rec, f)}
        if not have.intersection(v.lower() for v in vals):
            return False
    return True


def estimate_fetch_seconds(n: int, s: Settings) -> float:
    """Rough time to fetch n records: POSTs of `retrieve_batch_size` ids, at most
    `max_concurrency` at once. A round of POSTs took 0.3-1 s on the live API
    (Oct 2026), slower for big records; 11,569 records took 14 s. Use 1 s/round
    so the user is never promised less than it takes."""
    batches = math.ceil(n / max(1, s.retrieve_batch_size))
    rounds = math.ceil(batches / max(1, s.max_concurrency))
    return float(max(rounds, 1))


@dataclass
class ResolvedSet:
    ids: list[str]                    # matching ids, sorted by bildid
    stats: list[FilterStat]
    norm: list[tuple[Field, list[str]]]
    records: dict[str, dict] | None   # every matching record, or None if not fetched
    verified: bool                    # EXACT_FIELDS filters (sex) were checked against the records
    notes: list[str]
    species_report: list[dict] = field(default_factory=list)


async def resolve_set(
    client: BILClient,
    filters: dict[str, FilterValue] | None = None,
    text: str | None = None,
    text_mode: Literal["all", "any"] = "all",
    fetch_all: bool = False,
    tax: Taxonomy | None = None,
) -> ResolvedSet:
    """Ids for a search, plus every record when there are at most `stats_limit`
    matches (or `fetch_all`). Records land in the client cache, so the next
    search over the same datasets needs no POSTs."""
    m = await matching_ids(client, filters, text, text_mode, tax)
    ids, stats, norm, sp = m.ids, m.stats, m.norm, m.species_report
    notes: list[str] = [r["message"] for r in sp if not r["resolved"]]
    exact = _exact_filters(norm)
    if len(ids) > client.s.stats_limit and not fetch_all:
        if exact:
            notes.append(_unchecked_note(exact))
        return ResolvedSet(ids, stats, norm, None, False, notes, sp)
    recs = await client.retrieve_many(ids)
    if len(recs) < len(ids):
        notes.append(f"{len(ids) - len(recs)} ids matched but could not be retrieved; they are left out.")
        ids = [i for i in ids if i in recs]
    if exact:
        before = len(ids)
        ids = [i for i in ids if _record_matches_exact(recs[i], exact)]
        if before and not ids:
            notes.append(_emptied_note(exact, before))
    return ResolvedSet(ids, stats, norm, recs, bool(exact), notes, sp)


def _filter_text(exact: list[tuple[Field, list[str]]]) -> str:
    return " and ".join(f"{f.key}={'/'.join(vals)}" for f, vals in exact)


def _unchecked_note(exact: list[tuple[Field, list[str]]]) -> str:
    return (f"{_filter_text(exact)} is not checked yet: it is matched exactly against the records, which were not "
            f"read. Until then the total also counts substring matches (sex=Male also counts Female specimens).")


def _emptied_note(exact: list[tuple[Field, list[str]]], before: int) -> str:
    return (f"{before} datasets match all filters as substrings, but none has a specimen with exactly "
            f"{_filter_text(exact)}. That is what emptied the search.")


def _newest_first(ids: list[str], recs: dict[str, dict]) -> list[str]:
    # ids are already sorted by bildid, and sort() is stable, so ties stay in id order.
    return sorted(ids, key=lambda i: bildate(recs[i]), reverse=True)


def _breakdown(recs: dict[str, dict], ids: list[str], f: Field, tax: Taxonomy,
               top: int = BREAKDOWN_TOP) -> Breakdown:
    t = _tally(recs, ids, f, tax=tax)
    return Breakdown(filter_key=f.key, distinct_values=len(t.counter), missing_value=t.missing,
                     top=_buckets(t, top, tax))


async def search(
    client: BILClient,
    filters: dict[str, FilterValue] | None = None,
    text: str | None = None,
    text_mode: Literal["all", "any"] = "all",
    fetch_all: bool = False,
    tax: Taxonomy | None = None,
) -> SearchResult:
    rs = await resolve_set(client, filters, text, text_mode, fetch_all, tax)
    s = client.s
    total = len(rs.ids)
    notes = list(rs.notes)
    if any(r["resolved"] for r in rs.species_report):
        notes.append("species was matched by organism, not by substring: species_report lists each BIL species "
                     "string queried, related names left out (e.g. 'spiny mouse' for 'mouse'), and data notes. "
                     "Pass these on to the user.")
    if text and text_mode == "all" and len(text.split()) > 1:
        notes.append("Multi-word text was treated as AND (one fulltext query per word).")
    if text and text_mode == "any" and len(text.split()) > 1:
        notes.append("Multi-word text was sent as ONE fulltext query ('+' in the URL is an encoded space); "
                     "BIL matches datasets containing ANY of the words.")
    notes += _zero_hit_hints(rs.stats)
    ttl_min = round(s.cache_ttl_s / 60)
    base = dict(total=total, verified=rs.verified, filters=rs.stats,
                species_report=rs.species_report, viewer_url_template=f"{s.viewer_base}?bildid={{bildid}}")

    if rs.records is None:
        est = estimate_fetch_seconds(total, s)
        return SearchResult(
            **base, records_fetched=False, notes=notes,
            next_steps=[
                f"{total} matches is more than the automatic limit of {s.stats_limit}, so no records were "
                "fetched: there are no breakdowns or example datasets yet. Ask the user which they prefer:",
                "(a) Narrow the search with more filters. The filter stats show how many datasets each filter "
                "matches on its own.",
                f"(b) Get the breakdowns anyway: call search_datasets again with the same arguments plus "
                f"fetch_all=true. It fetches all {total} records, roughly {est:.0f} s. Tell the user that the records "
                f"then stay cached in this server for {ttl_min} minutes, so narrowing the search afterwards is "
                "fast: each added filter needs only a quick id lookup, and no records are downloaded again.",
            ] + ([
                "The sex filter is matched exactly only once the records are read, so this total still includes "
                "datasets that match only as a substring (sex=Male also counts Female). Tell the user the number "
                "is an upper bound; (b) gives the exact count."
            ] if _exact_filters(rs.norm) else []),
        )

    recs = rs.records
    if not total:
        return SearchResult(**base, records_fetched=True, notes=notes, next_steps=[
            "Tell the user which filter emptied the search (see notes) and suggest real values "
            "(count_by_field on a broader search). Don't silently retry with guessed values."])

    mix = Counter(sex_mix(recs[i]) for i in rs.ids)
    breakdowns = [_breakdown(recs, rs.ids, BY_KEY[k], tax or default_taxonomy()) for k in BREAKDOWN_FIELDS]
    rows = [brief(i, recs[i]) for i in _newest_first(rs.ids, recs)[:ROWS_SHOWN]]

    next_steps = []
    if total > ROWS_SHOWN:
        next_steps.append(
            f"datasets lists the {ROWS_SHOWN} newest of {total}. Offer the user: narrow the search (the "
            "breakdowns show how the matches split), or list more with get_dataset_summaries using the same "
            f"filters/text plus offset={ROWS_SHOWN} and detail='brief'.")
    example = next((b for b in breakdowns if b.filter_key == "contributorname" and b.top), None)
    example_txt = (f", e.g. {json.dumps({example.filter_key: example.top[0].value})}" if example else "")
    next_steps.append(
        f"To look inside one group of a breakdown, add {{filter_key: value}} to the filters{example_txt}. "
        f"The records are cached for {ttl_min} minutes, so this is fast.")
    next_steps.append("For full summaries of specific datasets, call get_dataset_summaries with their bildids.")
    return SearchResult(
        **base, records_fetched=True, breakdowns=breakdowns,
        specimen_sex_mix={k: mix[k] for k in SEX_MIX_ORDER if mix[k]},
        datasets=rows, notes=notes, next_steps=next_steps,
    )


async def dataset_summaries(
    client: BILClient,
    bildids: list[str] | None = None,
    filters: dict[str, FilterValue] | None = None,
    text: str | None = None,
    text_mode: Literal["all", "any"] = "all",
    fetch_all: bool = False,
    limit: int = ROWS_SHOWN,
    offset: int = 0,
    detail: Literal["full", "brief"] = "full",
    tax: Taxonomy | None = None,
) -> SummaryPage:
    """Summaries for given bildids (all of them, in the order given), or one page
    of a search's matches, newest first."""
    make = (lambda i, r: summarize(i, r, client.s)) if detail == "full" else brief
    if bildids:
        if filters or text:
            raise SearchError("Give bildids OR a search (filters/text), not both.")
        ids = list(dict.fromkeys(i.strip() for i in bildids if i.strip()))
        recs = await client.retrieve_many(ids)
        found = [make(i, recs[i]) for i in ids if i in recs]
        return SummaryPage(total=len(ids), offset=0, returned=len(found), order="as given", datasets=found,
                           not_found=[i for i in ids if i not in recs])
    rs = await resolve_set(client, filters, text, text_mode, fetch_all, tax)
    if rs.records is None:
        raise SearchError(
            f"{len(rs.ids)} matches is more than the automatic limit of {client.s.stats_limit}. Narrow the "
            f"search, or pass fetch_all=true (roughly {estimate_fetch_seconds(len(rs.ids), client.s):.0f} s).")
    limit = max(0, min(limit, 100))
    offset = max(0, offset)
    page = _newest_first(rs.ids, rs.records)[offset : offset + limit]
    return SummaryPage(total=len(rs.ids), offset=offset, returned=len(page), order="newest first",
                       datasets=[make(i, rs.records[i]) for i in page], notes=rs.notes)


def _zero_hit_hints(stats: list[FilterStat]) -> list[str]:
    """Say WHICH filter emptied the search, and suggest close known values.

    An empty result is a valid answer, so it is not an error. But "0 hits" alone
    doesn't tell the model whether BIL lacks the data or the value was mistyped
    ('humanh'). Naming the filter and suggesting close values helps it retry.
    """
    hints = []
    for st in stats:
        if st.matches:
            continue
        key, _, value = st.filter.partition("=")
        f = BY_KEY.get(key)
        msg = f"Filter {st.filter!r} matched 0 datasets on its own."
        if f and f.examples:
            by_lower = {e.lower(): e for e in f.examples}
            close = difflib.get_close_matches(value.lower(), list(by_lower), n=3, cutoff=0.6)
            if close:
                msg += f" Did you mean: {', '.join(by_lower[c] for c in close)}?"
            else:
                msg += f" Known values include: {', '.join(f.examples)}."
        hints.append(msg + " Use count_by_field on a broader search to see real values.")
    return hints


@dataclass
class _Tally:
    counter: Counter[str]               # group key -> datasets
    raw: dict[str, Counter[str]]        # group key -> exact stored value -> datasets
    spelling: dict[str, Counter[str]]   # group key -> spelling (case ignored) -> datasets
    missing: int


def _tally(recs: dict[str, dict], ids: list[str], f: Field, case_sensitive: bool = False,
           tax: Taxonomy | None = None) -> _Tally:
    """Datasets per value of `f`, each dataset counted once per group.

    Values are grouped case-insensitively by default ("Mouse", "mouse"). For
    `species`, values are grouped by organism through species_map.yaml
    ("macaca mulatta", "rhesus macaque", "macaque" -> Macaca mulatta); a species
    value not in the map keeps its own group. case_sensitive=True turns both off."""
    by_organism = f.key == "species" and tax is not None and not case_sensitive

    def group(v: str) -> str:
        if by_organism:
            tid = tax.owner(" ".join(v.lower().split()))
            if tid is not None:
                return f"taxon:{tid}"
        return v if case_sensitive else v.casefold()

    t = _Tally(Counter(), {}, {}, 0)
    for i in ids:
        rec = recs.get(i)
        values = [v.strip() for v in values_in_record(rec, f)] if rec else []
        groups: dict[str, set[str]] = {}
        for v in values:
            if v:
                groups.setdefault(group(v), set()).add(v)
        if not groups:
            t.missing += 1
        for k, vs in groups.items():
            t.counter[k] += 1
            for v in vs:
                t.raw.setdefault(k, Counter())[v] += 1
            for sp in {v if case_sensitive else v.casefold() for v in vs}:
                t.spelling.setdefault(k, Counter())[sp] += 1
    return t


def _buckets(t: _Tally, top: int, tax: Taxonomy | None = None) -> list[ValueCount]:
    out = []
    for k, c in t.counter.most_common(max(1, top)):
        raw = t.raw[k]
        if k.startswith("taxon:"):
            tid = int(k.removeprefix("taxon:"))
            rec = tax.taxa[tid]
            # Display each spelling the way it is most often stored.
            shown = {sp: max((v for v in raw if v.casefold() == sp), key=lambda v: raw[v])
                     for sp in t.spelling[k]}
            out.append(SpeciesCount(
                value=rec["scientific_name"], count=c, taxon_id=f"NCBI:txid{tid}",
                common_name=(rec.get("common_names") or [None])[0],
                spellings={shown[sp]: n for sp, n in t.spelling[k].most_common()},
                variants=sorted(raw) if len(raw) > 1 else []))
        else:
            out.append(ValueCount(value=raw.most_common(1)[0][0], count=c,
                                  variants=sorted(raw) if len(raw) > 1 else []))
    return out


async def count_by_field(
    client: BILClient,
    field: str,
    filters: dict[str, FilterValue] | None = None,
    text: str | None = None,
    bildids: list[str] | None = None,
    top: int = 25,
    max_records: int | None = None,
    case_sensitive: bool = False,
    tax: Taxonomy | None = None,
) -> FieldCounts:
    """Count values of `field` over a set of datasets (given or searched).

    BIL values are not normalized ("Mouse" and "mouse" both occur), so by
    default values are grouped case-insensitively and shown with their most
    common spelling. At most `max_records` records are read (default:
    ``Settings.stats_limit``). EXACT_FIELDS filters (sex) are checked against
    the records read, as in search().
    """
    f = resolve(field)
    notes: list[str] = []
    exact: list[tuple[Field, list[str]]] = []
    if bildids:
        ids = list(dict.fromkeys(bildids))
    else:
        m = await matching_ids(client, filters, text, tax=tax)
        ids, exact = m.ids, _exact_filters(m.norm)
        notes += [r["message"] for r in m.species_report if not r["resolved"]]
    read = ids[: max_records or client.s.stats_limit]
    truncated = len(ids) > len(read)
    recs = await client.retrieve_many(read)
    considered = [i for i in read if i in recs]
    if exact:
        before = len(considered)
        considered = [i for i in considered if _record_matches_exact(recs[i], exact)]
        if before and not considered:
            notes.append(_emptied_note(exact, before))
        if truncated:
            notes.append(f"Only {len(read)} of {len(ids)} records were read. {_filter_text(exact)} was checked on "
                         f"those; datasets_total ({len(ids)}) is not checked and also counts substring matches.")
    t = _tally(recs, considered, f, case_sensitive, tax or default_taxonomy())
    return FieldCounts(
        field=f.key,
        datasets_considered=len(considered),
        datasets_total=len(ids) if truncated else len(considered),
        truncated=truncated,
        missing_value=t.missing,
        buckets=_buckets(t, top, tax or default_taxonomy()),
        notes=notes,
    )


async def related_datasets(
    client: BILClient,
    bildid: str,
    levels: list[str] | None = None,
    max_per_group: int = 200,
) -> RelatedResult:
    """Other BIL datasets that share a specimen cross-reference (e.g. BICAN
    donor / tissue / section NHash ids in Specimen.linkage)."""
    rec = await client.retrieve(bildid)
    if rec is None:
        raise SearchError(f"No BIL dataset with id {bildid!r}.")
    links = []
    for spec in rec.get("Specimen") or []:
        for link in spec.get("linkage") or []:
            if isinstance(link, dict) and link.get("xid"):
                links.append(link)
    notes = []
    if not links:
        notes.append("This dataset has no Specimen.linkage cross-references (common outside BICAN).")
    wanted = {l.lower() for l in levels} if levels else None
    chosen = [l for l in links if wanted is None or str(l.get("relationship", "")).lower() in wanted]
    xf = BY_KEY["linkage.xid"]
    results = await asyncio.gather(*(client.query(xf.endpoint, xf.field, re.escape(l["xid"])) for l in chosen))
    groups = []
    for link, ids in zip(chosen, results):
        # xid matching is substring-based too (the escaped value is a literal regex), but NHash ids are fixed-format
        # (e.g. DO-OHTO1697), so accidental partial matches are unlikely.
        others = sorted(i for i in ids if i != bildid)
        groups.append(
            RelatedGroup(relationship=str(link.get("relationship", "")), xid=link["xid"], bildids=others[:max_per_group])
        )
        if len(others) > max_per_group:
            notes.append(f"{link['xid']}: {len(others)} related, showing {max_per_group}.")
    return RelatedResult(bildid=bildid, linkages=links, related=groups, notes=notes)
