"""Offline test harness: a fake BIL API serving REAL recorded records.

`FakeBIL` is an httpx MockTransport. It answers /query and /retrieve the way
the live API does (one variable per query, case-insensitive substring match,
"no entry found" as HTTP 404 + JSON success="false", string booleans), computed over
tests/fixtures/records.json. The live-API tests in test_live.py check that
those assumptions still hold.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from bil_mcp.client import BILClient
from bil_mcp.config import Settings
from bil_mcp.fields import FIELDS, values_in_record

FIX = Path(__file__).parent / "fixtures"
RECORDS: dict[str, dict] = json.loads((FIX / "records.json").read_text())
GROUP_G8: dict = json.loads((FIX / "groupdoi_g.8.json").read_text())


def _ok(endpoint: str, **kw) -> httpx.Response:
    return httpx.Response(200, json={"success": "true", "endpoint": endpoint, "message": "GET success", **kw})


def _fail(endpoint: str, message: str, status: int = 404) -> httpx.Response:
    # Real BIL: logical failures are HTTP 404 (405 for an all-miss POST) + JSON.
    return httpx.Response(status, json={"success": "false", "endpoint": endpoint, "message": message})


class FakeBIL:
    def __init__(self, records: dict[str, dict] = RECORDS):
        self.records = records
        self.calls: list[str] = []  # "GET /query/specimen?species=Human" etc.

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        self.calls.append(f"{request.method} {path}?{request.url.query.decode()}")

        if path.startswith("/query/"):
            endpoint = path[len("/query/") :]
            if request.method != "GET":
                return _fail(endpoint, "POST method not allowed")
            if len(params) != 1:
                return _fail(endpoint, "Multiple Variables, endpoint only accepts one variable")
            (var, value), = params.items()
            if endpoint == "fulltext":
                words = value.lower().split()  # BIL ORs words
                ids = [i for i, r in self.records.items() if any(w in json.dumps(r).lower() for w in words)]
            else:
                fields = [f for f in FIELDS if f.endpoint == endpoint and f.field == var]
                if not any(f.endpoint == endpoint for f in FIELDS):
                    return httpx.Response(404, text="<h1>Not Found</h1>")
                if not fields:
                    return _fail(endpoint, "Not a valid variable for the endpoint")
                ids = [
                    i for i, r in self.records.items()
                    if any(value.lower() in v.lower() for v in values_in_record(r, fields[0]))
                ]
            if not ids:
                return _fail(endpoint, "GET failure, no entry found")
            return _ok(endpoint, bildids=ids, **{var: value})

        if path == "/retrieve" and request.method == "GET":
            if "bildid" in params:
                rec = self.records.get(params["bildid"])
            else:
                doi = params.get("doi", "")
                rec = next((r for r in self.records.values() if r["Dataset"][0].get("doi") == doi and doi), None)
            if rec is None:
                return _fail("Retrieve", "GET failure, no entry found")
            return _ok("Retrieve", retjson=[rec])

        if path == "/retrieve" and request.method == "POST":
            ids = json.loads(request.content)["bildids"]
            status = {i: i in self.records for i in ids}
            found = [self.records[i] for i in ids if i in self.records]
            if not found:
                return _fail("Retrieve", "POST failure, no entries found", status=405)
            msg = "POST success" if all(status.values()) else "POST partially successful, some entries retreived"
            return httpx.Response(200, json={"success": "true", "endpoint": "Retrieve", "message": msg,
                                             "status": status, "retjson": found})

        if path == "/retrieve/groupdoi":
            if params.get("bildid") == "g.8":
                return httpx.Response(200, json=GROUP_G8)
            return _fail("GroupDoi", "No group DOI found")

        return httpx.Response(404, text="<h1>Not Found</h1>")


@pytest.fixture
def fake() -> FakeBIL:
    return FakeBIL()


@pytest.fixture
async def client(fake: FakeBIL):
    c = BILClient(Settings(max_retries=0), transport=httpx.MockTransport(fake))
    yield c
    await c.aclose()
