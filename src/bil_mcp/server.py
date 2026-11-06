"""The MCP layer: exposes BIL search as tools, resources and prompts.

This file is deliberately thin. Each tool validates input, calls into
``search.py`` / ``client.py`` / ``files.py``, and turns domain errors into
``ToolError`` so the agent gets a readable message instead of a crash.

Run it with ``bil-mcp`` (stdio transport, which is what local MCP clients
like Claude Desktop and Claude Code use).

IMPORTANT for stdio servers: stdout carries the JSON-RPC protocol stream, so
never ``print()`` here; logs go to stderr via ``logging``. (mcp 2.x diverts
stdout to stderr while the server is running, so a stray print() inside a tool
is now harmless, but output written before the server starts still corrupts
the stream, and other SDKs offer no such protection.)
"""

from __future__ import annotations

import argparse
import inspect
import json
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import Field

from . import __version__
from .client import BILAPIError, BILClient
from .config import Settings
from .fields import FIELDS, UnknownFieldError
from .files import list_files
from .models import AccessInfo, FieldCounts, FileListing, RelatedResult, SearchResult, SummaryPage, https_url_for, summarize
from .search import SearchError, count_by_field, dataset_summaries, related_datasets, search
from .audit import LogToolCalls
from .strict import RejectUnknownArguments
from .taxonomy import Taxonomy
from .version_check import update_notice

log = logging.getLogger("bil_mcp")


# ---------------------------------------------------------------- lifespan
@dataclass
class AppState:
    client: BILClient
    taxonomy: Taxonomy
    update_notice: str | None = None   # set once at startup; None if up to date or unknown
    notice_sent: bool = False


def make_client() -> BILClient:
    """Factory for the shared client. Tests replace this to inject a fake API."""
    return BILClient(Settings.from_env())


@asynccontextmanager
async def lifespan(_server: MCPServer) -> AsyncIterator[AppState]:
    """Runs once per server process: open one HTTP client (connection pool +
    cache), load the species taxonomy, check PyPI for a newer release (at most
    ~2 s, silent on any error), and share all of it across every tool call."""
    client = make_client()
    log.info("BIL MCP server starting (api=%s)", client.s.api_base)
    try:
        tax = Taxonomy.load()
        notice = await update_notice(client.http)
        if notice:
            log.warning(notice)
        yield AppState(client=client, taxonomy=tax, update_notice=notice)
    finally:
        await client.aclose()


class AttachUpdateNotice:
    """Middleware: add the update notice (if any) to the first tool result.

    Once per server process, which on stdio (how local users run bil-mcp) is one
    session. The SDK gives middleware no public per-connection handle, so on
    streamable-http only the first session after startup gets it. It is an extra
    text block, so the structured result (and its schema) is unchanged."""

    async def __call__(self, ctx: Any, call_next: Any) -> Any:
        result = await call_next(ctx)
        if ctx.method != "tools/call":
            return result
        state = ctx.lifespan_context
        notice = getattr(state, "update_notice", None)
        if not notice or state.notice_sent:
            return result
        text = f"Notice from the bil-mcp server, please tell the user: {notice}"
        if isinstance(result, CallToolResult):
            result.content.append(TextContent(type="text", text=text))
        elif isinstance(result, dict) and isinstance(result.get("content"), list):
            result["content"].append({"type": "text", "text": text})
        else:
            return result
        state.notice_sent = True
        return result


# Shown in the instructions and tool descriptions, so the agent sees the real limit.
STATS_LIMIT = Settings.from_env().stats_limit

INSTRUCTIONS = f"""\
Tools for the Brain Image Library (BIL), the BRAIN Initiative archive for brain microscopy
and spatial transcriptomics data. Datasets are identified by bildids like 'ace-ban-win'.

How to work well with these tools:
- Start with search_datasets. Filters are AND-ed; a list value is OR-ed within a field.
- search_datasets always returns the total and how many datasets each filter matches on its own.
  Up to {STATS_LIMIT} matches it also reads every matching record and returns
  breakdowns (species, technique, consortium, contributor, specimen sex) and brief rows for the
  20 newest datasets (all of them when there are 20 or fewer). Above the limit it returns counts only.
- Each result has next_steps: offer those choices to the user. When a choice costs waiting time
  (fetch_all), let the user decide, and pass on what the step says about speed and caching.
- Records read by any tool stay cached in this server (1 hour by default), so follow-up searches
  over the same datasets return almost instantly.
- To narrow by a breakdown value, add {{filter_key: value}} to the filters.
- get_dataset_summaries: full summaries for chosen bildids, or the next page of a search.
  get_dataset: the full record of one dataset (large; pass sections). count_by_field: a breakdown
  by any other field.
- If unsure of a field name, read the bil://fields resource or call list_fields.
- species is matched by organism: 'mouse', 'macaque' (the genus), 'rodents', 'monkeys', 'NCBI:txid9606'
  all work. The result's species_report says which BIL species strings were queried and which related
  names were left out (e.g. 'spiny mouse'); tell the user. A term that is not an organism BIL holds
  returns 0 without querying, plus the list of organisms BIL does hold.
- Other filters match as case-insensitive SUBSTRING ('sex=male' also matches 'Female'); use
  match='exact' to check against full metadata. A dataset matches if ANY of its specimens matches;
  specimen_sex on each row shows the mix (e.g. 'male 8 / female 4').
- BICAN datasets carry donor/tissue/section ids in Specimen.linkage; find_related_datasets
  follows them.
- If a search returns 0 hits, tell the user which filter emptied it (see the result's notes) and
  suggest real values from count_by_field; don't silently retry with guessed values.
- Always give users the bildid and viewer link so they can check results themselves.
"""

mcp = MCPServer(
    "bil",
    title="Brain Image Library",
    instructions=INSTRUCTIONS,
    version=__version__,
    lifespan=lifespan,
)

# Unknown argument names become tool errors instead of being silently dropped.
mcp.middleware.append(LogToolCalls())             # outermost: sees every call, even rejected ones
mcp.middleware.append(AttachUpdateNotice())
mcp.middleware.append(RejectUnknownArguments(mcp))

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True, destructive_hint=False)


def bil_tool(name: str | None = None):
    """Register a read-only tool, with its docstring cleaned of indentation.

    The SDK copies a function's raw __doc__ into the tool description, including
    the leading spaces of every docstring line. inspect.cleandoc strips them, so
    the model reads (and the context pays for) only the text.
    """
    def decorator(fn):
        doc = inspect.cleandoc(fn.__doc__ or "").replace("{stats_limit}", str(STATS_LIMIT))
        return mcp.tool(name=name, description=doc, annotations=READ_ONLY)(fn)
    return decorator


def _client(ctx: Context) -> BILClient:
    return ctx.request_context.lifespan_context.client


def _tax(ctx: Context) -> Taxonomy:
    return ctx.request_context.lifespan_context.taxonomy


def _tool_error(e: Exception) -> ToolError:
    if isinstance(e, (SearchError, UnknownFieldError, ValueError)):
        return ToolError(str(e))
    if isinstance(e, BILAPIError):
        return ToolError(f"BIL API problem: {e}")
    return ToolError(f"Unexpected error: {type(e).__name__}: {e}")


# ------------------------------------------------------------------- tools
FilterDict = Annotated[
    dict[str, str | list[str]] | None,
    Field(
        description=(
            "Field filters, AND-ed together. Keys like species, technique, generalmodality, consortium, "
            "contributorname, affiliation, project, linkage.xid (see list_fields). A list value means OR. "
            'Example: {"species": "Mouse", "generalmodality": "spatial transcriptomics"}'
        )
    ),
]


MatchArg = Annotated[
    Literal["contains", "exact"],
    Field(description="'contains' = BIL's substring match; 'exact' = keep a dataset only if a specimen/field "
                      "value equals the filter value (needs the records)"),
]
TextArg = Annotated[str | None, Field(description="Free text searched across all metadata")]
TextModeArg = Annotated[Literal["all", "any"], Field(description="'all' = every word must match")]
FetchAllArg = Annotated[
    bool, Field(description=f"Read all matching records even above the automatic limit ({STATS_LIMIT}). "
                            "Only after the user agreed to wait")]


@bil_tool()
async def search_datasets(
    ctx: Context,
    filters: FilterDict = None,
    text: TextArg = None,
    text_mode: TextModeArg = "all",
    match: MatchArg = "contains",
    fetch_all: FetchAllArg = False,
) -> SearchResult:
    """Search BIL datasets by metadata filters and/or free text.

    Always returns the total and per-filter match counts with reproducible API URLs.
    Up to {stats_limit} matches it also returns breakdowns of all matches (species, technique,
    consortium, contributor, specimen sex) and brief rows for the 20 newest.
    Above that it returns counts only; next_steps says what to ask the user.
    """
    try:
        return await search(_client(ctx), filters, text, text_mode, match, fetch_all, _tax(ctx))
    except Exception as e:
        raise _tool_error(e) from e


@bil_tool()
async def get_dataset(
    ctx: Context,
    bildid: Annotated[str | None, Field(description="BIL dataset id, e.g. 'ace-ban-win'")] = None,
    doi: Annotated[str | None, Field(description="Dataset DOI, e.g. 'https://doi.org/10.35077/rut'")] = None,
    sections: Annotated[
        list[str] | None,
        Field(description="Only return these sections, e.g. ['Specimen','Image']. Omit for all."),
    ] = None,
) -> dict[str, Any]:
    """Full metadata record for one dataset (by bildid or DOI).

    Sections: Submission, Contributors, Funders, Publication, Instrument, Dataset,
    Specimen, Image, Assets. A full record is large; pass `sections` when you only
    need part of it.
    """
    if not bildid and not doi:
        raise ToolError("Give a bildid or a doi.")
    c = _client(ctx)
    try:
        rec = await c.retrieve(bildid.strip()) if bildid else await c.retrieve_by_doi(doi.strip())  # type: ignore[union-attr]
    except Exception as e:
        raise _tool_error(e) from e
    if rec is None:
        raise ToolError(f"No BIL dataset found for {bildid or doi!r}.")
    if sections:
        wanted = {s.lower() for s in sections}
        rec = {k: v for k, v in rec.items() if k.lower() in wanted}
    return rec


@bil_tool()
async def get_dataset_summaries(
    ctx: Context,
    bildids: Annotated[list[str] | None, Field(max_length=200, description="Up to 200 bildids")] = None,
    filters: FilterDict = None,
    text: TextArg = None,
    text_mode: TextModeArg = "all",
    match: MatchArg = "contains",
    fetch_all: FetchAllArg = False,
    limit: Annotated[int, Field(ge=1, le=100, description="Search mode: datasets per page")] = 20,
    offset: Annotated[int, Field(ge=0, description="Search mode: skip this many (newest first)")] = 0,
    detail: Annotated[Literal["full", "brief"], Field(description="'full' summaries or one-line 'brief' rows")] = "full",
) -> SummaryPage:
    """Summaries of chosen datasets, or one page of a search's matches.

    Give bildids (all are returned, in order), OR the same filters/text/match as
    search_datasets to page through its matches newest first with limit/offset.
    """
    if not (bildids or filters or text):
        raise ToolError("Give bildids, or filters/text to page through a search.")
    try:
        return await dataset_summaries(_client(ctx), bildids, filters, text, text_mode, match, fetch_all,
                                       limit, offset, detail, _tax(ctx))
    except Exception as e:
        raise _tool_error(e) from e


# Python name differs from the tool name to avoid clashing with search.count_by_field.
@bil_tool(name="count_by_field")
async def count_by_field_tool(
    ctx: Context,
    field: Annotated[str, Field(description="Field to count, e.g. 'species', 'technique', 'consortium'")],
    filters: FilterDict = None,
    text: Annotated[str | None, Field(description="Optional free text to restrict the set")] = None,
    bildids: Annotated[list[str] | None, Field(description="Count over these ids instead of a search")] = None,
    top: Annotated[int, Field(ge=1, le=200)] = 25,
    max_records: Annotated[
        int | None, Field(ge=1, le=20000, description=f"Cap on records read to count (default {STATS_LIMIT})")] = None,
    case_sensitive: Annotated[bool, Field(description="Keep 'Mouse' and 'mouse' as separate values")] = False,
) -> FieldCounts:
    """Break a set of datasets down by the values of one field.

    Good for overview questions: 'which techniques exist for human data?',
    'who contributed BICAN spatial transcriptomics?'.
    """
    if not (filters or text or bildids):
        raise ToolError("Give filters, text, or bildids to define the set to count.")
    try:
        return await count_by_field(_client(ctx), field, filters, text, bildids, top, max_records, case_sensitive,
                                    _tax(ctx))
    except Exception as e:
        raise _tool_error(e) from e


@bil_tool()
async def find_related_datasets(
    ctx: Context,
    bildid: Annotated[str, Field(description="A BIL dataset id")],
    levels: Annotated[
        list[str] | None,
        Field(description="Linkage levels to follow, e.g. ['donor'] or ['tissue','section']. Omit for all."),
    ] = None,
) -> RelatedResult:
    """Find other BIL datasets from the same donor / tissue / section.

    Follows the cross-reference ids (BICAN NHash such as 'DO-OHTO1697') stored in
    Specimen.linkage. Most non-BICAN datasets have no linkage.
    """
    try:
        return await related_datasets(_client(ctx), bildid.strip(), levels)
    except Exception as e:
        raise _tool_error(e) from e


@bil_tool()
async def get_access_info(
    ctx: Context,
    bildid: Annotated[str, Field(description="A BIL dataset id")],
) -> AccessInfo:
    """How to get a dataset's files: HTTPS URL, wget command, Bridges-2 path, viewer link, license."""
    c = _client(ctx)
    try:
        rec = await c.retrieve(bildid.strip())
    except Exception as e:
        raise _tool_error(e) from e
    if rec is None:
        raise ToolError(f"No BIL dataset with id {bildid!r}.")
    ds = (rec.get("Dataset") or [{}])[0]
    s = summarize(bildid, rec, c.s)
    url = https_url_for(ds.get("bildirectory"), c.s)
    return AccessInfo(
        bildid=bildid,
        title=s.title,
        bildirectory=ds.get("bildirectory") or None,
        https_url=url,
        wget_command=f"wget -r -np -nH --cut-dirs=3 {url}" if url else None,
        globus_note="BIL data are also available through Globus; see https://www.brainimagelibrary.org/download.html",
        viewer_url=s.viewer_url,
        doi=s.doi,
        rights=s.rights,
        dataset_size=s.dataset_size,
        number_of_files=s.number_of_files,
    )


@bil_tool()
async def list_dataset_files(
    ctx: Context,
    bildid: Annotated[str, Field(description="A BIL dataset id")],
    subpath: Annotated[str, Field(description="Sub-directory inside the dataset, e.g. 'raw/'")] = "",
    max_entries: Annotated[int, Field(ge=1, le=5000)] = 500,
) -> FileListing:
    """List files and folders of a dataset on the BIL download server (one level, no download)."""
    try:
        return await list_files(_client(ctx), bildid.strip(), subpath, max_entries)
    except Exception as e:
        raise _tool_error(e) from e


@bil_tool()
async def get_group_doi(
    ctx: Context,
    group_id: Annotated[str, Field(description="Group/collection DOI id, e.g. 'g.8' or '10.35077/g.8'")],
) -> dict[str, Any]:
    """Look up a BIL collection (group) DOI: creators, title, and the datasets it groups."""
    try:
        doc = await _client(ctx).group_doi(group_id)
    except Exception as e:
        raise _tool_error(e) from e
    if doc is None:
        raise ToolError(f"No BIL group DOI {group_id!r}.")
    attrs = ((doc.get("datacite") or {}).get("data") or {}).get("attributes") or {}
    return {
        "doi": attrs.get("doi") or doc.get("dataid"),
        "titles": [t.get("title") for t in attrs.get("titles") or []],
        "creators": [c.get("name") for c in attrs.get("creators") or []],
        "publication_year": attrs.get("publicationYear"),
        "url": attrs.get("url"),
        "related_identifiers": attrs.get("relatedIdentifiers") or [],
        "bil_relations": (doc.get("public") or {}).get("relatedmap") or [],
    }


@bil_tool()
def list_fields() -> list[dict[str, Any]]:
    """All filter keys usable in search_datasets / count_by_field, with descriptions and example values."""
    return _field_catalog()


def _field_catalog() -> list[dict[str, Any]]:
    return [
        {"key": f.key, "section": f.section, "description": f.description, "examples": list(f.examples)}
        for f in FIELDS
    ]


# --------------------------------------------------------------- resources
@mcp.resource("bil://fields", title="BIL searchable fields", mime_type="application/json",
              description="Catalog of searchable BIL fields")
def fields_resource() -> str:
    return json.dumps(_field_catalog(), indent=1)


@mcp.resource("bil://dataset/{bildid}", title="BIL dataset record", mime_type="application/json",
              description="Full metadata of one dataset")
async def dataset_resource(bildid: str, ctx: Context) -> str:
    rec = await _client(ctx).retrieve(bildid)
    if rec is None:
        raise ValueError(f"No BIL dataset with id {bildid!r}")
    return json.dumps(rec, indent=1)


# ----------------------------------------------------------------- prompts
@mcp.prompt(title="Find BIL datasets")
def find_datasets(question: str) -> str:
    """Turn a research question into a BIL search, with a short report."""
    return (
        f"Research question: {question}\n\n"
        "Use the Brain Image Library tools to answer it:\n"
        "1. Pick filters (call list_fields if unsure) and run search_datasets.\n"
        "2. Describe the matches with the result's breakdowns. If records were not fetched (too many matches), "
        "follow next_steps: ask the user whether to narrow the search or fetch everything.\n"
        "3. Report: total hits, a table of the most relevant datasets (bildid, title, species, technique, "
        "viewer_url), and the filter query URLs so the search is reproducible.\n"
        "4. Say clearly what you could NOT determine from metadata."
    )


@mcp.prompt(title="Dataset report")
def dataset_report(bildid: str) -> str:
    """Summarize one dataset for a methods section or data-reuse decision."""
    return (
        f"Write a concise report on BIL dataset {bildid}. Call get_dataset, get_access_info, and "
        "find_related_datasets. Cover: what was imaged (species, sex, age, genotype, region), technique and "
        "instrument, voxel size, size and file count, contributors and funding, license, how to cite (DOI) "
        "and how to access the files. Flag missing or ambiguous metadata."
    )


# ------------------------------------------------------------------ entry
def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="bil-mcp", description="Brain Image Library MCP server")
    parser.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    parser.add_argument("--port", type=int, default=8000, help="Port for streamable-http")
    parser.add_argument("--log-level", default="WARNING")
    parser.add_argument("--version", action="version", version=f"bil-mcp {__version__}")
    args = parser.parse_args(argv)

    # force=True: the SDK installs its own logging handler when MCPServer is created
    # (at import), which would otherwise make basicConfig a silent no-op.
    logging.basicConfig(level=args.log_level.upper(), stream=sys.stderr, force=True,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # httpx/httpcore log every connection step at DEBUG; our own client already logs
    # each BIL request, so keep the libraries quiet.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run("streamable-http", port=args.port)


if __name__ == "__main__":
    main()
