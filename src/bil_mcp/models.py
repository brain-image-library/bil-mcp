"""Pydantic models for tool outputs.

Why models instead of raw dicts? With the MCP Python SDK, a tool whose return
type is a Pydantic model automatically publishes an *output schema* and
returns *structured content*. The client (and the model) then know exactly
which fields to expect. It also forces us to decide what is worth showing,
which keeps responses small: a full BIL record is ~9 KB, a summary ~0.5 KB.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .config import Settings
from .fields import values_in_record, BY_KEY


class DatasetSummary(BaseModel):
    bildid: str
    title: str | None = None
    species: list[str] = []
    technique: str | None = None
    generalmodality: str | None = None
    consortium: str | None = None
    project: str | None = None
    contributors: list[str] = Field(default=[], description="Up to 5 contributor names")
    doi: str | None = None
    bildate: str | None = Field(None, description="Ingest date")
    dataset_size: str | None = Field(None, description="Raw 'dataset_size' value from BIL (units not documented)")
    number_of_files: str | None = None
    rights: str | None = None
    viewer_url: str
    download_url: str | None = None


class FilterStat(BaseModel):
    filter: str = Field(description="field=value as sent to the API")
    query_url: str = Field(description="Reproducible API URL for this filter")
    matches: int


class SearchResult(BaseModel):
    total: int = Field(description="Number of datasets matching ALL filters")
    offset: int
    returned: int
    match_mode: str
    verified: bool = Field(description="True if every hit was checked against full metadata (match='exact')")
    filters: list[FilterStat]
    datasets: list[DatasetSummary]
    bildids: list[str] = Field(default=[], description="Matching ids beyond the summarized page (capped)")
    notes: list[str] = []


class FacetBucket(BaseModel):
    value: str
    count: int
    variants: list[str] = Field(default=[], description="Spellings merged into this bucket")


class FacetResult(BaseModel):
    field: str
    datasets_considered: int
    datasets_total: int
    truncated: bool
    missing_value: int = Field(description="Datasets with no value for this field")
    buckets: list[FacetBucket]


class RelatedGroup(BaseModel):
    relationship: str
    xid: str
    bildids: list[str]


class RelatedResult(BaseModel):
    bildid: str
    linkages: list[dict[str, Any]]
    related: list[RelatedGroup]
    notes: list[str] = []


class AccessInfo(BaseModel):
    bildid: str
    title: str | None
    bildirectory: str | None = Field(description="Path on BIL storage; also valid on PSC Bridges-2 at /bil/data/...")
    https_url: str | None
    wget_command: str | None
    globus_note: str
    viewer_url: str
    doi: str | None
    rights: str | None
    dataset_size: str | None
    number_of_files: str | None


class FileEntry(BaseModel):
    name: str
    url: str
    is_dir: bool
    size: str | None = None
    modified: str | None = None


class FileListing(BaseModel):
    bildid: str
    url: str
    entries: list[FileEntry]
    truncated: bool
    notes: list[str] = []


# --------------------------------------------------------------- converters
def https_url_for(bildirectory: str | None, s: Settings) -> str | None:
    """/bil/data/ab/cd/<uuid>/x  ->  https://download.brainimagelibrary.org/ab/cd/<uuid>/x/"""
    if not bildirectory or not bildirectory.startswith("/bil/data/"):
        return None
    rel = bildirectory[len("/bil/data/") :].strip("/")
    return f"{s.download_base}/{rel}/"


def summarize(bildid: str, rec: dict, s: Settings) -> DatasetSummary:
    raw = rec.get("Dataset")
    ds = raw[0] if isinstance(raw, list) and raw else raw if isinstance(raw, dict) else {}
    sub = rec.get("Submission") or {}
    contributors = list(dict.fromkeys(
        c.get("contributorname") for c in rec.get("Contributors") or [] if c.get("contributorname")
    ))
    return DatasetSummary(
        bildid=bildid,
        title=ds.get("title") or None,
        species=sorted(set(values_in_record(rec, BY_KEY["species"]))),
        technique=ds.get("technique") or None,
        generalmodality=ds.get("generalmodality") or None,
        consortium=sub.get("consortium") or None,
        project=sub.get("project") or None,
        contributors=contributors[:5],
        doi=ds.get("doi") or sub.get("doi") or None,
        bildate=sub.get("bildate") or None,
        dataset_size=_none_str(ds.get("dataset_size")),
        number_of_files=_none_str(ds.get("number_of_files")),
        rights=ds.get("rightsidentifier") or ds.get("rights") or None,
        viewer_url=f"{s.viewer_base}?bildid={bildid}",
        download_url=https_url_for(ds.get("bildirectory"), s),
    )


def _none_str(v: Any) -> str | None:
    if v in (None, "", "None"):
        return None
    return str(v)
