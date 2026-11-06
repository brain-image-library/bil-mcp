"""Field registry: which BIL metadata fields can be queried, and where they live.

The BIL query endpoint has the shape ``/query/<section>?<field>=<value>``.
An agent shouldn't have to know which section owns ``species`` (Specimen) or
``technique`` (Dataset), so filters use a short field name and this module
resolves it to ``(endpoint, field)``.

Every field below was checked against the live API (Oct 2026): it was accepted
as a query variable. Fields the API rejects (``dataset_size``,
``number_of_files``, ``nameidentifierscheme``) are left out on purpose.

The API treats a value as a *case-insensitive regex* searched anywhere in the
field. ``search.py`` escapes values, so they match as substrings:
``sex=male`` ALSO matches ``Female``; ``match="exact"`` corrects for that.
``species`` is the exception: it is resolved through ``taxonomy.py``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    key: str          # name agents use in filters, e.g. "species" or "linkage.xid"
    section: str      # top-level key in a retrieved record, e.g. "Specimen"
    endpoint: str     # path segment after /query/, e.g. "specimen"
    field: str        # query variable name the API expects
    description: str
    examples: tuple[str, ...] = ()


# Short descriptions are what the agent sees in the field catalog, so they are
# written for a model choosing filters, not for a database administrator.
_F = Field
FIELDS: tuple[Field, ...] = (
    # --- Dataset: what the data is ---------------------------------------
    _F("title", "Dataset", "dataset", "title", "Dataset title."),
    _F("abstract", "Dataset", "dataset", "abstract", "Free-text description of the dataset."),
    _F("technique", "Dataset", "dataset", "technique", "Specific experimental technique.",
       ("MERFISH", "fMOST", "STPT", "light sheet", "Patch-seq")),
    _F("generalmodality", "Dataset", "dataset", "generalmodality", "Broad data modality.",
       ("spatial transcriptomics", "connectivity", "population imaging", "cell morphology")),
    _F("methods", "Dataset", "dataset", "methods", "Methods text (often 'modality / technique')."),
    _F("rights", "Dataset", "dataset", "rights", "License text.", ("Creative Commons",)),
    _F("rightsidentifier", "Dataset", "dataset", "rightsidentifier", "SPDX license id.", ("CC-BY-4.0",)),
    _F("dataset_doi", "Dataset", "dataset", "doi", "Dataset DOI (full URL or suffix)."),
    _F("bildirectory", "Dataset", "dataset", "bildirectory", "Path on BIL storage, /bil/data/..."),
    _F("subject", "Dataset", "dataset", "subject", "Subject keywords."),
    _F("technicalinfo", "Dataset", "dataset", "technicalinfo", "Technical notes."),
    _F("other", "Dataset", "dataset", "other", "Miscellaneous notes."),
    # --- Specimen: what was imaged ---------------------------------------
    _F("species", "Specimen", "specimen", "species", "Species common name.",
       ("Mouse", "Human", "Macaque", "Marmoset", "Zebrafish")),
    _F("ncbitaxonomy", "Specimen", "specimen", "ncbitaxonomy", "NCBI taxonomy id.", ("NCBI:txid10090",)),
    _F("sex", "Specimen", "specimen", "sex", "Specimen sex. Use match='exact' (male is a substring of Female).",
       ("Male", "Female", "Unknown")),
    _F("age", "Specimen", "specimen", "age", "Age value (number as text; see ageunit)."),
    _F("ageunit", "Specimen", "specimen", "ageunit", "Unit for age.", ("Days", "Years")),
    _F("genotype", "Specimen", "specimen", "genotype", "Genotype / strain text."),
    _F("organname", "Specimen", "specimen", "organname", "Organ.", ("Brain",)),
    _F("atlas", "Specimen", "specimen", "atlas", "Reference atlas.", ("AllenMouseV2",)),
    _F("locations", "Specimen", "specimen", "locations", "Brain locations/regions sampled."),
    _F("localid", "Specimen", "specimen", "localid", "Submitter's local specimen id."),
    _F("samplelocalid", "Specimen", "specimen", "samplelocalid", "Submitter's local sample id."),
    _F("organlocalid", "Specimen", "specimen", "organlocalid", "Submitter's local organ id."),
    # --- Specimen linkage: BICAN cross-references (NHash ids) -------------
    _F("linkage.xid", "Specimen.linkage", "specimen/linkage", "xid",
       "Cross-reference id, e.g. BICAN donor/tissue/section NHash.", ("DO-FXXJ3038",)),
    _F("linkage.relationship", "Specimen.linkage", "specimen/linkage", "relationship",
       "Level of the cross-reference.", ("donor", "tissue", "section", "slab")),
    _F("linkage.type", "Specimen.linkage", "specimen/linkage", "type", "Linkage source type.", ("cubie_tissue",)),
    _F("linkage.class", "Specimen.linkage", "specimen/linkage", "class", "Linkage class.", ("xref",)),
    # --- Submission: provenance ------------------------------------------
    _F("consortium", "Submission", "submission", "consortium", "Funding consortium.", ("BICAN", "BICCN")),
    _F("project", "Submission", "submission", "project", "Project / grant name.", ("U19 Huang",)),
    _F("submission_uuid", "Submission", "submission", "submission_uuid", "16-hex-digit submission id."),
    _F("submission_doi", "Submission", "submission", "doi", "Submission DOI."),
    _F("bildate", "Submission", "submission", "bildate", "Ingest date YYYY-MM-DD (substring, so '2025' works)."),
    _F("collection", "Submission", "submission", "collection", "Collection number."),
    _F("method", "Submission", "submission", "method", "Ingest method.", ("ingest_1",)),
    _F("metadata_version", "Submission", "submission", "metadata", "Metadata model version.", ("2.0",)),
    _F("sheet", "Submission", "submission", "sheet", "Submission sheet number."),
    # --- Contributors / Funders / Publication ----------------------------
    _F("contributorname", "Contributors", "contributors", "contributorname", "Person or group name.",
       ("Tasic, Bosiljka", "Zhuang")),
    _F("affiliation", "Contributors", "contributors", "affiliation", "Contributor affiliation.",
       ("Allen Institute", "Broad")),
    _F("affiliationidentifier", "Contributors", "contributors", "affiliationidentifier", "ROR id of affiliation."),
    _F("contributortype", "Contributors", "contributors", "contributortype", "Role.",
       ("ProjectLeader", "ResearchGroup", "DataCollector")),
    _F("creator", "Contributors", "contributors", "creator", "Is a creator.", ("Yes", "No")),
    _F("nameidentifier", "Contributors", "contributors", "nameidentifier", "ORCID URL of a person."),
    _F("nametype", "Contributors", "contributors", "nametype", "Personal or Organizational."),
    _F("fundername", "Funders", "funders", "fundername", "Funder.", ("National Institutes of Health",)),
    _F("award_number", "Funders", "funders", "award_number", "Grant number."),
    _F("award_title", "Funders", "funders", "award_title", "Grant title."),
    _F("funding_reference_identifier", "Funders", "funders", "funding_reference_identifier", "Funder ROR id."),
    _F("publication", "Publication", "publication", "relatedidentifier",
       "Related publication identifier (DOI URL, or 'doi' to find any)."),
    # --- Instrument -------------------------------------------------------
    _F("microscopetype", "Instrument", "instrument", "microscopetype", "Microscope type.",
       ("Two Photon", "light sheet", "confocal")),
    _F("microscopemanufacturerandmodel", "Instrument", "instrument", "microscopemanufacturerandmodel",
       "Microscope make/model."),
    _F("objectivename", "Instrument", "instrument", "objectivename", "Objective lens."),
    _F("objectiveimmersion", "Instrument", "instrument", "objectiveimmersion", "Immersion medium."),
    _F("objectivemagnification", "Instrument", "instrument", "objectivemagnification", "e.g. 20X."),
    _F("objectivena", "Instrument", "instrument", "objectivena", "Numerical aperture."),
    _F("detectortype", "Instrument", "instrument", "detectortype", "Detector type.", ("PMT", "sCMOS")),
    _F("detectormodel", "Instrument", "instrument", "detectormodel", "Detector model."),
    _F("illuminationtypes", "Instrument", "instrument", "illuminationtypes", "Illumination types."),
    _F("illuminationwavelength", "Instrument", "instrument", "illuminationwavelength", "Excitation wavelength."),
    _F("detectionwavelength", "Instrument", "instrument", "detectionwavelength", "Detection wavelength."),
    _F("sampletemperature", "Instrument", "instrument", "sampletemperature", "Sample temperature."),
    # --- Image ------------------------------------------------------------
    _F("flurophore", "Image", "image", "flurophore", "Fluorophores/channels (BIL spelling)."),
    _F("representation", "Image", "image", "representation", "What the image represents.", ("neurite",)),
    _F("channels", "Image", "image", "channels", "Number of channels."),
    _F("gbytes", "Image", "image", "gbytes", "Image size in GB (text)."),
    _F("stepsizex", "Image", "image", "stepsizex", "Voxel size x (microns)."),
    _F("stepsizey", "Image", "image", "stepsizey", "Voxel size y (microns)."),
    _F("stepsizez", "Image", "image", "stepsizez", "Voxel size z (microns)."),
    _F("dimensionorder", "Image", "image", "dimensionorder", "Axis order."),
)

BY_KEY: dict[str, Field] = {f.key: f for f in FIELDS}

# Friendly aliases so agents' natural guesses still resolve.
ALIASES: dict[str, str] = {
    "organism": "species",
    "modality": "generalmodality",
    "xid": "linkage.xid",
    "nhash": "linkage.xid",
    "contributor": "contributorname",
    "funder": "fundername",
    "doi": "dataset_doi",
    "license": "rightsidentifier",
    "microscope": "microscopetype",
}


class UnknownFieldError(ValueError):
    pass


def resolve(key: str) -> Field:
    """Turn an agent-facing filter key into a Field, accepting aliases and
    'Section.field' spellings (e.g. 'Specimen.species')."""
    k = key.strip()
    lk = k.lower()
    if lk in BY_KEY:
        return BY_KEY[lk]
    if lk in ALIASES:
        return BY_KEY[ALIASES[lk]]
    if "." in lk:  # "specimen.species" -> "species"
        section, _, name = lk.partition(".")
        for f in FIELDS:
            if f.section.lower() == section and f.field == name:
                return f
    raise UnknownFieldError(
        f"Unknown filter field {key!r}. Read the bil://fields resource (or call list_fields) "
        f"for valid keys. Some valid keys: species, technique, generalmodality, consortium, "
        f"contributorname, affiliation, linkage.xid."
    )


def values_in_record(record: dict, f: Field) -> list[str]:
    """Extract every value of field `f` from one retrieved record.

    Sections are usually lists of dicts (a dataset can have several
    contributors); Submission is a single dict; linkage is nested inside
    Specimen. Always returns a flat list of strings.
    """
    if f.section == "Specimen.linkage":
        out = []
        for spec in _as_list(record.get("Specimen")):
            for link in _as_list(spec.get("linkage")):
                v = link.get(f.field)
                if v not in (None, ""):
                    out.append(str(v))
        return out
    out = []
    for item in _as_list(record.get(f.section)):
        v = item.get(f.field)
        if v not in (None, ""):
            out.append(str(v))
    return out


def _as_list(x) -> list:
    if x is None:
        return []
    if isinstance(x, list):
        return [i for i in x if isinstance(i, dict)]
    if isinstance(x, dict):
        return [x]
    return []
