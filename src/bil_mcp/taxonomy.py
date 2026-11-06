r"""Species vocabulary for the BIL MCP server.

Turns what a user types ("mouse", "macaque", "rhesus", "insects", "monkeys") into BIL species
queries, runs them, and returns a report of exactly what was queried, what was removed and why.

Two data files, both shipped in bil_mcp/data:
  species_map.yaml    CURATED: BIL string -> NCBI taxon ID, quirk notes, aliases, everyday groups
  ncbi_taxonomy.yaml  GENERATED from NCBI by scripts/build_taxonomy.py: names, synonyms, lineage

Rules (decided 2026-11-06):
  * A term resolves to a taxon; every BIL organism under that taxon is included ("macaque" = genus).
  * A dataset is the unit: counts are unique BIL IDs.
  * BIL query values are case-insensitive regular expressions. Each BIL string is sent escaped and
    anchored (^\s*...\s*$), so it matches that exact string only: "mouse" never pulls in "spiny mouse".
  * BIL strings of organisms NOT included that contain the user's term ("spiny mouse" for "mouse")
    are counted and reported under not_included.
  * A term not found in the shipped files is not sent to BIL; the report lists what BIL holds.

No MCP imports: called from search.matching_ids() for the `species` filter.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Awaitable, Callable

import yaml

Query = Callable[[str], Awaitable[set[str]]]   # regex pattern for the species field -> BIL IDs

# Where a term matched, best first. A term matching at a better level ignores the worse ones.
PRECEDENCE = ["curated alias", "BIL species string", "everyday group (curated)",
              "NCBI taxon ID", "NCBI scientific name", "NCBI common name", "NCBI synonym"]


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


@dataclass(frozen=True)
class Target:
    kind: str          # "taxon" or "group"
    ref: int | str     # taxon ID or group name
    matched_by: str    # one of PRECEDENCE


@dataclass
class Resolution:
    term: str
    targets: list[Target]
    organisms: list[int]                      # BIL organisms included (taxon IDs)
    description: str = ""


@dataclass
class Taxonomy:
    taxa: dict[int, dict]                     # every NCBI record in the generated file
    holdings: dict[int, dict]                 # curated: taxon ID -> {bil_strings, notes}
    groups: dict[str, dict]                   # curated everyday groups
    index: dict[str, list[Target]] = field(default_factory=dict)

    # ---------- loading ----------
    @classmethod
    def load(cls, data_dir: str | Path | None = None) -> "Taxonomy":
        if data_dir is None:
            read = lambda name: resources.files("bil_mcp.data").joinpath(name).read_text()
        else:
            read = lambda name: Path(data_dir, name).read_text()
        curated = yaml.safe_load(read("species_map.yaml"))
        taxa = {int(k): v for k, v in yaml.safe_load(read("ncbi_taxonomy.yaml"))["taxa"].items()}
        holdings = {int(k): {"bil_strings": [_norm(s) for s in v["bil_strings"]], "notes": v.get("notes", [])}
                    for k, v in curated["organisms"].items()}
        tax = cls(taxa, holdings, curated.get("everyday_groups", {}))
        tax._build_index(curated.get("aliases", {}))
        return tax

    def _id_of(self, scientific_name: str) -> int:
        for tid, rec in self.taxa.items():
            if rec["scientific_name"] == scientific_name:
                return tid
        raise ValueError(f"{scientific_name!r} is not in ncbi_taxonomy.yaml; rerun scripts/build_taxonomy.py")

    def _build_index(self, aliases: dict[str, str]) -> None:
        """term -> targets, keeping only the best PRECEDENCE level for each term."""
        found: dict[str, dict[str, set[Target]]] = {}
        add = lambda term, t: found.setdefault(_norm(term), {}).setdefault(t.matched_by, set()).add(t)
        for term, sci in aliases.items():
            add(term, Target("taxon", self._id_of(sci), "curated alias"))
        for tid, h in self.holdings.items():
            for s in h["bil_strings"]:
                add(s, Target("taxon", tid, "BIL species string"))
        for name, g in self.groups.items():
            for term in [name, *g.get("terms", [])]:
                add(term, Target("group", name, "everyday group (curated)"))
        for tid, rec in self.taxa.items():
            for form in (f"{tid}", f"txid{tid}", f"ncbi:txid{tid}", f"ncbitaxon:{tid}"):
                add(form, Target("taxon", tid, "NCBI taxon ID"))
            add(rec["scientific_name"], Target("taxon", tid, "NCBI scientific name"))
            for n in rec.get("common_names", []):
                add(n, Target("taxon", tid, "NCBI common name"))
            for n in rec.get("synonyms", []):
                add(n, Target("taxon", tid, "NCBI synonym"))
        for term, levels in found.items():
            best = next(level for level in PRECEDENCE if level in levels)
            self.index[term] = sorted(levels[best], key=lambda t: str(t.ref))

    # ---------- resolving ----------
    def name(self, tid: int) -> str:
        rec = self.taxa[tid]
        common = rec.get("common_names") or []
        return f"{rec['scientific_name']} ({common[0]})" if common else rec["scientific_name"]

    def _members(self, t: Target) -> list[int]:
        if t.kind == "group":
            g = self.groups[t.ref]
            inc = {self._id_of(n) for n in g["include"]}
            exc = {self._id_of(n) for n in g.get("exclude", [])}
        else:
            inc, exc = {t.ref}, set()
        out = []
        for tid in self.holdings:
            line = {*self.taxa[tid]["lineage"], tid}
            if line & inc and not line & exc:
                out.append(tid)
        return out

    def resolve(self, term: str) -> Resolution:
        targets = self.index.get(_norm(term), [])
        organisms = sorted({tid for t in targets for tid in self._members(t)})
        desc = "; ".join(
            f"{self.groups[t.ref]['definition']}" if t.kind == "group"
            else f"{self.taxa[t.ref]['rank']} {self.taxa[t.ref]['scientific_name']}"
            for t in targets)
        return Resolution(term, targets, organisms, desc)

    def available(self) -> list[dict]:
        return [{"organism": self.name(tid), "taxon_id": f"NCBI:txid{tid}", "bil_strings": h["bil_strings"]}
                for tid, h in sorted(self.holdings.items(), key=lambda kv: self.taxa[kv[0]]["scientific_name"])]

    # ---------- querying ----------
    def owner(self, bil_string: str) -> int | None:
        return next((tid for tid, h in self.holdings.items() if bil_string in h["bil_strings"]), None)

    async def search(self, term: str, query: Query) -> dict:
        """Resolve `term`, query BIL, and return {"ids": [...], "report": {...}}."""
        res = self.resolve(term)
        if not res.organisms:
            return {"ids": [], "report": {
                "term": term, "resolved": False,
                "message": (f"{term!r} is not an organism or group BIL holds, so BIL was not queried."
                            if not res.targets else
                            f"{term!r} resolves to {res.description}, but BIL holds no datasets for it, "
                            f"so BIL was not queried."),
                "available": self.available()}}

        wanted = sorted({s for tid in res.organisms for s in self.holdings[tid]["bil_strings"]})
        t = _norm(term)
        # Other organisms whose BIL string contains the user's word: reported, not included.
        neighbours = sorted({s for tid, h in self.holdings.items() if tid not in res.organisms
                             for s in h["bil_strings"] if t in s})
        strings = wanted + neighbours
        hits = dict(zip(strings, await asyncio.gather(*(query(exact_pattern(s)) for s in strings))))

        all_ids = set().union(*(hits[s] for s in wanted))
        organisms = [{"organism": self.name(tid), "taxon_id": f"NCBI:txid{tid}",
                      "datasets": len(set().union(*(hits[s] for s in self.holdings[tid]["bil_strings"]))),
                      "bil_strings": self.holdings[tid]["bil_strings"]} for tid in res.organisms]
        queries = [{"bil_string": s, "sent_to_bil": f"/query/specimen?species={exact_pattern(s)}",
                    "datasets": len(hits[s])} for s in wanted]
        not_included = [{"bil_string": s, "organism": self.name(self.owner(s)), "datasets": len(hits[s]),
                         "why": f"its name contains {term!r}, but it is a different organism"}
                        for s in neighbours if hits[s]]
        notes = [n["note"] for tid in res.organisms for n in self.holdings[tid]["notes"]]
        return {"ids": sorted(all_ids), "report": {
            "term": term, "resolved": True,
            "matched_by": sorted({t.matched_by for t in res.targets}),
            "interpreted_as": res.description,
            "datasets": len(all_ids),
            "by_organism": organisms,
            "queries": queries,
            "not_included": not_included,
            "notes": notes}}


def exact_pattern(s: str) -> str:
    """BIL treats query values as case-insensitive regexes: escape, and anchor to the whole value."""
    return r"^\s*" + re.escape(s) + r"\s*$"


async def bil_species_query(client, value: str) -> set[str]:
    """Default Query using an httpx.AsyncClient. In the server, use client.py instead (cache + retry)."""
    r = await client.get("https://api.brainimagelibrary.org/query/specimen", params={"species": value})
    if r.status_code == 404 and r.headers.get("content-type", "").startswith("application/json"):
        return set()                                   # API fact 4: 404 + JSON = no hits
    r.raise_for_status()
    return set(r.json().get("bildids", []))
