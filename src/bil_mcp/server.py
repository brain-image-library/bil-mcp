"""The MCP layer: exposes BIL search as tools, resources and prompts.

This file is deliberately thin. Each tool validates input, calls into
``search.py`` / ``client.py`` / ``files.py``, and turns domain errors into
``ToolError`` so the agent gets a readable message instead of a crash.

Run it with ``bil-mcp`` (stdio transport, which is what local MCP clients
like Claude Desktop and Claude Code use).

IMPORTANT for stdio servers: stdout carries the JSON-RPC protocol stream.
Never ``print()`` here; logs go to stderr via ``logging``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .client import BILAPIError, BILClient
from .config import Settings
from .fields import FIELDS, UnknownFieldError
from .files import list_files
from .models import AccessInfo, FacetResult, FileListing, RelatedResult, SearchResult, https_url_for, summarize
from .search import SearchError, facet_counts, related_datasets, search
from .strict import RejectUnknownArguments

log = logging.getLogger("bil_mcp")


# ---------------------------------------------------------------- lifespan
@dataclass
class AppState:
    client: BILClient


def make_client() -> BILClient:
    """Factory for the shared client. Tests replace this to inject a fake API."""
    return BILClient(Settings.from_env())


@asynccontextmanager
async def lifespan(_server: MCPServer) -> AsyncIterator[AppState]:
    """Runs once per server process: open one HTTP client (connection pool +
    cache) and share it across every tool call, then close it on shutdown."""
    client = make_client()
    log.info("BIL MCP server starting (api=%s)", client.s.api_base)
    try:
        yield AppState(client=client)
    finally:
        await client.aclose()


INSTRUCTIONS = """\
Tools for the Brain Image Library (BIL), the BRAIN Initiative archive for brain microscopy
and spatial transcriptomics data. Datasets are identified by bildids like 'ace-ban-win'.

How to work well with these tools:
- Start with search_datasets. Filters are AND-ed; a list value is OR-ed within a field.
- If unsure of a field name, read the bil://fields resource or call list_fields.
- API matching is case-insensitive SUBSTRING ('sex=male' also matches 'Female'); use
  match='exact' to verify against full metadata.
- To summarize a large result ("which species / techniques?"), use facet_counts rather than
  paging through search results.
- get_dataset returns full metadata; prefer summaries unless details are needed.
- BICAN datasets carry donor/tissue/section ids in Specimen.linkage; find_related_datasets
  follows them.
- Always give users the bildid and viewer_url so they can check results themselves.
"""

mcp = MCPServer(
    "bil",
    title="Brain Image Library",
    instructions=INSTRUCTIONS,
    version=__version__,
    lifespan=lifespan,
)

# Unknown argument names become tool errors instead of being silently dropped.
mcp.middleware.append(RejectUnknownArguments(mcp))

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=True, idempotent_hint=True, destructive_hint=False)


def _client(ctx: Context) -> BILClient:
    return ctx.request_context.lifespan_context.client


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


@mcp.tool(annotations=READ_ONLY)
async def search_datasets(
    ctx: Context,
    filters: FilterDict = None,
    text: Annotated[str | None, Field(description="Free text searched across all metadata")] = None,
    text_mode: Annotated[Literal["all", "any"], Field(description="'all' = every word must match")] = "all",
    match: Annotated[
        Literal["contains", "exact"],
        Field(description="'contains' = BIL's substring match (fast); 'exact' = verify each hit's field equals the value"),
    ] = "contains",
    limit: Annotated[int, Field(ge=0, le=100, description="Summaries to return")] = 10,
    offset: Annotated[int, Field(ge=0, description="Skip this many hits (paging)")] = 0,
) -> SearchResult:
    """Search BIL datasets by metadata filters and/or free text.

    Returns the total hit count, short summaries for one page of hits, the remaining
    matching bildids, and a reproducible API URL per filter.
    """
    try:
        return await search(_client(ctx), filters, text, text_mode, match, limit, offset)
    except Exception as e:
        raise _tool_error(e) from e


@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=READ_ONLY)
async def get_dataset_summaries(
    ctx: Context,
    bildids: Annotated[list[str], Field(min_length=1, max_length=200, description="Up to 200 bildids")],
) -> dict[str, Any]:
    """Short summaries for a list of bildids (e.g. ids returned by search_datasets)."""
    c = _client(ctx)
    try:
        recs = await c.retrieve_many(bildids)
    except Exception as e:
        raise _tool_error(e) from e
    found = [summarize(i, recs[i], c.s).model_dump() for i in bildids if i in recs]
    missing = [i for i in bildids if i not in recs]
    return {"datasets": found, "not_found": missing}


# Python name differs from the tool name to avoid clashing with search.facet_counts.
@mcp.tool(name="facet_counts", annotations=READ_ONLY)
async def facet_counts_tool(
    ctx: Context,
    field: Annotated[str, Field(description="Field to count, e.g. 'species', 'technique', 'consortium'")],
    filters: FilterDict = None,
    text: Annotated[str | None, Field(description="Optional free text to restrict the set")] = None,
    bildids: Annotated[list[str] | None, Field(description="Count over these ids instead of a search")] = None,
    top: Annotated[int, Field(ge=1, le=200)] = 25,
    max_records: Annotated[int, Field(ge=1, le=20000, description="Cap on records fetched to count")] = 3000,
    case_sensitive: Annotated[bool, Field(description="Keep 'Mouse' and 'mouse' as separate values")] = False,
) -> FacetResult:
    """Break a set of datasets down by the values of one field.

    Good for overview questions: 'which techniques exist for human data?',
    'who contributed BICAN spatial transcriptomics?'.
    """
    if not (filters or text or bildids):
        raise ToolError("Give filters, text, or bildids to define the set to count.")
    try:
        return await facet_counts(_client(ctx), field, filters, text, bildids, top, max_records, case_sensitive)
    except Exception as e:
        raise _tool_error(e) from e


@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=READ_ONLY)
def list_fields() -> list[dict[str, Any]]:
    """All filter keys usable in search_datasets / facet_counts, with descriptions and example values."""
    return _field_catalog()


def _field_catalog() -> list[dict[str, Any]]:
    return [
        {"key": f.key, "section": f.section, "description": f.description, "examples": list(f.examples)}
        for f in FIELDS
    ]


# --------------------------------------------------------------- resources
@mcp.resource("bil://fields", mime_type="application/json", description="Catalog of searchable BIL fields")
def fields_resource() -> str:
    return json.dumps(_field_catalog(), indent=1)


@mcp.resource("bil://dataset/{bildid}", mime_type="application/json", description="Full metadata of one dataset")
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
        "2. If there are many hits, use facet_counts on species, technique and consortium to describe them.\n"
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

    logging.basicConfig(level=args.log_level.upper(), stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per HTTP call is noise
    if args.transport == "stdio":
        mcp.run()
    else:
        mcp.run("streamable-http", port=args.port)


if __name__ == "__main__":
    main()
