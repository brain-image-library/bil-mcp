"""Pydantic models for tool outputs.

Why models instead of raw dicts? With the MCP Python SDK, a tool whose return
type is a Pydantic model automatically publishes an *output schema* and
returns *structured content*. The client (and the model) then know exactly
which fields to expect. It also forces us to decide what is worth showing,
which keeps responses small: a full BIL record is ~9 KB, a summary ~0.5 KB.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Union

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
    specimen_sex: str | None = Field(None, description="Specimens per sex, e.g. 'male 8 / female 4'")
    viewer_url: str
    download_url: str | None = None


class BriefRow(BaseModel):
    """One line per dataset: enough to recognise it and decide whether to look closer."""
    bildid: str
    title: str | None = None
    species: list[str] = []
    technique: str | None = None
    bildate: str | None = Field(None, description="Ingest date")
    specimen_sex: str | None = Field(None, description="Specimens per sex, e.g. 'male 8 / female 4'")


class FilterStat(BaseModel):
    filter: str = Field(description="field=value as sent to the API")
    query_url: str = Field(description="Reproducible API URL for this filter")
    matches: int


class ValueCount(BaseModel):
    value: str
    count: int
    variants: list[str] = Field(default=[], description="Spellings merged into this bucket")


class Breakdown(BaseModel):
    filter_key: str = Field(description="Field counted. Also the filter key: add {filter_key: value} to the "
                                        "filters to narrow the search to one value")
    distinct_values: int
    missing_value: int = Field(description="Datasets with no value for this field")
    top: list[ValueCount] = Field(description="Most common values, with the number of datasets for each")


class SearchResult(BaseModel):
    total: int = Field(description="Number of datasets matching ALL filters")
    match_mode: str
    verified: bool = Field(description="True if every hit was checked against full metadata (match='exact')")
    records_fetched: bool = Field(description="False when total is above the automatic limit: then there are "
                                              "no breakdowns or rows; see next_steps")
    filters: list[FilterStat] = Field(description="Per filter: how many datasets it matches on its own")
    breakdowns: list[Breakdown] = []
    specimen_sex_mix: dict[str, int] = Field(
        default={}, description="Datasets by specimen sex: male only / female only / mixed / unknown or not given")
    datasets: list[BriefRow] = Field(default=[], description="Newest first. All matches when total <= 20, "
                                                             "otherwise the 20 newest")
    viewer_url_template: str = Field(description="Replace {bildid} to get a dataset's web viewer link")
    notes: list[str] = []
    next_steps: list[str] = Field(default=[], description="What to offer the user next")


class SummaryPage(BaseModel):
    total: int = Field(description="Datasets in the set (ids given, or search matches)")
    offset: int
    returned: int
    order: str = Field(description="'newest first' for a search, 'as given' for bildids")
    datasets: list[Union[DatasetSummary, BriefRow]]
    not_found: list[str] = []
    notes: list[str] = []


class FieldCounts(BaseModel):
    field: str
    datasets_considered: int
    datasets_total: int
    truncated: bool
    missing_value: int = Field(description="Datasets with no value for this field")
    buckets: list[ValueCount]


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


def _dataset_section(rec: dict) -> dict:
    raw = rec.get("Dataset")
    return raw[0] if isinstance(raw, list) and raw else raw if isinstance(raw, dict) else {}


def bildate(rec: dict) -> str:
    return (rec.get("Submission") or {}).get("bildate") or ""


def sex_counts(rec: dict) -> Counter[str]:
    """Specimens per sex value, lower-cased: Counter({'male': 8, 'female': 4})."""
    return Counter(v.strip().lower() for v in values_in_record(rec, BY_KEY["sex"]) if v.strip())


def sex_label(rec: dict) -> str | None:
    c = sex_counts(rec)
    return " / ".join(f"{k} {n}" for k, n in c.most_common()) or None


def sex_mix(rec: dict) -> str:
    """Classify a dataset by its specimens' sexes. Values other than male/female
    (e.g. 'Unknown') are ignored unless they are all there is."""
    known = set(sex_counts(rec)) & {"male", "female"}
    if known == {"male", "female"}:
        return "mixed"
    if known == {"male"}:
        return "male only"
    if known == {"female"}:
        return "female only"
    return "unknown or not given"


def brief(bildid: str, rec: dict) -> BriefRow:
    ds = _dataset_section(rec)
    return BriefRow(
        bildid=bildid,
        title=ds.get("title") or None,
        species=sorted(set(values_in_record(rec, BY_KEY["species"]))),
        technique=ds.get("technique") or None,
        bildate=bildate(rec) or None,
        specimen_sex=sex_label(rec),
    )


def summarize(bildid: str, rec: dict, s: Settings) -> DatasetSummary:
    ds = _dataset_section(rec)
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
        specimen_sex=sex_label(rec),
        viewer_url=f"{s.viewer_base}?bildid={bildid}",
        download_url=https_url_for(ds.get("bildirectory"), s),
    )


def _none_str(v: Any) -> str | None:
    if v in (None, "", "None"):
        return None
    return str(v)
