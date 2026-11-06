"""Try species terms against the live BIL API and print the report the agent would get.

    uv run python scripts/try_species.py mouse macaque "monkeys"
    uv run python scripts/try_species.py --full mouse      # whole report as JSON
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bil_mcp.taxonomy import Taxonomy, bil_species_query  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "src" / "bil_mcp" / "data"


async def main(terms: list[str], full: bool) -> None:
    tax = Taxonomy.load(DATA)
    async with httpx.AsyncClient(timeout=60) as client:
        for term in terms:
            rep = (await tax.search(term, lambda s: bil_species_query(client, s)))["report"]
            if full:
                print(json.dumps(rep, indent=2))
                continue
            if not rep["resolved"]:
                print(f"{term!r}: {rep['message']} ({len(rep['available'])} organisms available)")
                continue
            print(f"{term!r} -> {rep['interpreted_as']} [{', '.join(rep['matched_by'])}]: {rep['datasets']} datasets")
            for o in rep["by_organism"]:
                print(f"    {o['datasets']:>6}  {o['organism']}")
            for q in rep["queries"]:
                print(f"    queried '{q['bil_string']}': {q['datasets']} datasets")
            for n in rep["not_included"]:
                print(f"    not included: '{n['bil_string']}' = {n['organism']}, {n['datasets']} datasets")
            for n in rep["notes"]:
                print(f"    note: {n}")


if __name__ == "__main__":
    args = sys.argv[1:]
    asyncio.run(main([a for a in args if a != "--full"], "--full" in args))
