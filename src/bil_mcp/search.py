"""Search logic: compose single-field BIL queries into multi-filter searches.

The BIL API accepts ONE field per query. This module turns
``{"species": "Human", "technique": "MERFISH"}`` into two API calls run
concurrently and intersects the returned id sets. It also:

* supports OR within one field: ``{"species": ["Human", "Macaque"]}``
* makes multi-word full text behave as AND (the API ORs words)
* optionally verifies hits against full metadata (``match="exact"``),
  because API matching is substring-based (``sex=male`` matches "Female")

This is the seam where a future intent-level IR (intermediate
representation) translator plugs in: it would produce the same
``filters``/``text`` inputs that ``search()`` already accepts.
"""

from __future__ import annotations

import asyncio
import difflib
from collections import Counter
from typing import Literal, Union
from urllib.parse import urlencode

from .client import BILClient
from .config import Settings
from .fields import BY_KEY, Field, resolve, values_in_record
from .models import (
    DatasetSummary,
    FacetBucket,
    FacetResult,
    FilterStat,
    RelatedGroup,
    RelatedResult,
    SearchResult,
    summarize,
)

FilterValue = Union[str, list[str]]
MatchMode = Literal["contains", "exact"]

MAX_LIMIT = 100
MAX_IDS_RETURNED = 500
DEFAULT_VERIFY_LIMIT = 3000


class SearchError(ValueError):
    pass


def _query_url(s: Settings, endpoint: str, field: str, value: str) -> str:
    return f"{s.api_base}/query/{endpoint}?{urlencode({field: value})}"


async def _ids_for_filter(client: BILClient, f: Field, values: list[str]) -> tuple[set[str], list[FilterStat]]:
    """Union of ids over the values of ONE field (OR semantics)."""
    results = await asyncio.gather(*(client.query(f.endpoint, f.field, v) for v in values))
    stats = [
        FilterStat(filter=f"{f.key}={v}", query_url=_query_url(client.s, f.endpoint, f.field, v), matches=len(ids))
        for v, ids in zip(values, results)
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


async def matching_ids(
    client: BILClient,
    filters: dict[str, FilterValue] | None = None,
    text: str | None = None,
    text_mode: Literal["all", "any"] = "all",
) -> tuple[list[str], list[FilterStat], list[tuple[Field, list[str]]]]:
    """Ids matching every filter (and the text), sorted for stable paging."""
    norm = _normalize_filters(filters)
    if not norm and not (text and text.strip()):
        raise SearchError("Give at least one filter or some text.")

    tasks = [_ids_for_filter(client, f, vals) for f, vals in norm]
    text_terms: list[str] = []
    if text and text.strip():
        text_terms = text.split() if text_mode == "all" else [text.strip()]
        tasks += [_ids_for_filter(client, _FULLTEXT, [t]) for t in text_terms]

    results = await asyncio.gather(*tasks)
    stats = [st for _, sts in results for st in sts]
    sets = [ids for ids, _ in results]
    hits = set.intersection(*sets) if sets else set()
    return sorted(hits), stats, norm


# A pseudo-field so full text goes through the same code path.
_FULLTEXT = Field("text", "*", "fulltext", "text", "Full-text search")


def _record_matches_exact(rec: dict, norm: list[tuple[Field, list[str]]]) -> bool:
    for f, vals in norm:
        have = {v.strip().lower() for v in values_in_record(rec, f)}
        if not have.intersection(v.lower() for v in vals):
            return False
    return True


async def search(
    client: BILClient,
    filters: dict[str, FilterValue] | None = None,
    text: str | None = None,
    text_mode: Literal["all", "any"] = "all",
    match: MatchMode = "contains",
    limit: int = 10,
    offset: int = 0,
    verify_limit: int = DEFAULT_VERIFY_LIMIT,
) -> SearchResult:
    limit = max(0, min(limit, MAX_LIMIT))
    offset = max(0, offset)
    ids, stats, norm = await matching_ids(client, filters, text, text_mode)
    notes: list[str] = []
    verified = False

    if match == "exact" and norm:
        if len(ids) > verify_limit:
            notes.append(
                f"{len(ids)} candidates is more than verify_limit={verify_limit}; returned substring "
                f"matches unverified. Add filters to narrow, then retry with match='exact'."
            )
        else:
            recs = await client.retrieve_many(ids)
            ids = [i for i in ids if i in recs and _record_matches_exact(recs[i], norm)]
            verified = True

    page = ids[offset : offset + limit]
    recs = await client.retrieve_many(page)
    summaries: list[DatasetSummary] = [summarize(i, recs[i], client.s) for i in page if i in recs]
    if len(summaries) < len(page):
        notes.append(f"{len(page) - len(summaries)} ids matched but could not be retrieved.")
    if match == "contains" and any(f.key in ("sex", "species") for f, _ in norm):
        notes.append("Substring matching is on: e.g. sex=Male also matches Female. Use match='exact' to be strict.")
    if text and text_mode == "all" and len(text.split()) > 1:
        notes.append("Multi-word text was treated as AND (one fulltext query per word).")

    notes += _zero_hit_hints(stats)

    rest = ids[offset + limit : offset + limit + MAX_IDS_RETURNED]
    return SearchResult(
        total=len(ids),
        offset=offset,
        returned=len(summaries),
        match_mode=match,
        verified=verified,
        filters=stats,
        datasets=summaries,
        bildids=rest,
        notes=notes,
    )


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
        hints.append(msg + " Use facet_counts on a broader search to see real values.")
    return hints


async def facet_counts(
    client: BILClient,
    field: str,
    filters: dict[str, FilterValue] | None = None,
    text: str | None = None,
    bildids: list[str] | None = None,
    top: int = 25,
    max_records: int = 3000,
    case_sensitive: bool = False,
) -> FacetResult:
    """Count values of `field` over a set of datasets (given or searched).

    BIL values are not normalized ("Mouse" and "mouse" both occur), so by
    default values are grouped case-insensitively and shown with their most
    common spelling.
    """
    f = resolve(field)
    if bildids:
        ids = list(dict.fromkeys(bildids))
    else:
        ids, _, _ = await matching_ids(client, filters, text)
    considered = ids[:max_records]
    recs = await client.retrieve_many(considered)
    counter: Counter[str] = Counter()
    spellings: dict[str, Counter[str]] = {}
    missing = 0
    for i in considered:
        rec = recs.get(i)
        raw = [v.strip() for v in values_in_record(rec, f)] if rec else []
        keys = {v if case_sensitive else v.casefold(): v for v in raw if v}
        if not keys:
            missing += 1
        for k, v in keys.items():
            counter[k] += 1
            spellings.setdefault(k, Counter())[v] += 1
    return FacetResult(
        field=f.key,
        datasets_considered=len(considered),
        datasets_total=len(ids),
        truncated=len(ids) > len(considered),
        missing_value=missing,
        buckets=[
            FacetBucket(value=spellings[k].most_common(1)[0][0], count=c,
                        variants=sorted(spellings[k]) if len(spellings[k]) > 1 else [])
            for k, c in counter.most_common(max(1, top))
        ],
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
    results = await asyncio.gather(*(client.query(xf.endpoint, xf.field, l["xid"]) for l in chosen))
    groups = []
    for link, ids in zip(chosen, results):
        # xid matching is substring-based too, but NHash ids are fixed-format
        # (e.g. DO-OHTO1697), so accidental partial matches are unlikely.
        others = sorted(i for i in ids if i != bildid)
        groups.append(
            RelatedGroup(relationship=str(link.get("relationship", "")), xid=link["xid"], bildids=others[:max_per_group])
        )
        if len(others) > max_per_group:
            notes.append(f"{link['xid']}: {len(others)} related, showing {max_per_group}.")
    return RelatedResult(bildid=bildid, linkages=links, related=groups, notes=notes)
