"""Async HTTP client for the BIL metadata API.

This module knows about BIL and HTTP, and nothing about MCP. Keeping it
separate means it can be reused from a notebook or script, and tested
without starting a server.

API facts this client encodes (checked against the live API, Oct 2026):

* ``GET /query/<endpoint>?<field>=<value>`` -> ``{"success", "bildids": [...]}``.
  Only ONE variable per call. Matching is case-insensitive substring.
* A query with no hits returns HTTP 404 with JSON ``success: "false"`` and
  the message "GET failure, no entry found". That is an empty result, not
  an error. Other logical errors also come back as 404/405 + JSON.
* ``GET /retrieve?bildid=<id>`` (or ``?doi=``) -> ``{"retjson": [record]}``.
* ``POST /retrieve`` with ``{"bildids": [...]}`` -> many records at once,
  plus a ``status`` map of which ids were found. If NONE are found:
  HTTP 405, "POST failure, no entries found".
* ``GET /retrieve/groupdoi?bildid=g.<n>`` -> a group (collection) DOI doc.
* ``success`` is the STRING "true"/"false", not a JSON boolean.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Any, Iterable

import httpx

from .config import Settings

log = logging.getLogger(__name__)

NO_ENTRY = "no entry found"


class BILAPIError(RuntimeError):
    """The BIL API said something went wrong (bad field, server error...)."""


class TTLCache:
    """Small LRU cache whose entries expire after `ttl` seconds.

    BIL metadata changes rarely, so caching for an hour makes repeated agent
    calls (very common: the model refines a search 3-4 times) nearly free.
    """

    def __init__(self, ttl: float, max_entries: int):
        self.ttl = ttl
        self.max_entries = max_entries
        self._d: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        hit = self._d.get(key)
        if hit is None:
            return None
        ts, value = hit
        if time.monotonic() - ts > self.ttl:
            del self._d[key]
            return None
        self._d.move_to_end(key)
        return value

    def set(self, key: str, value: Any) -> None:
        self._d[key] = (time.monotonic(), value)
        self._d.move_to_end(key)
        while len(self._d) > self.max_entries:
            self._d.popitem(last=False)

    def __len__(self) -> int:
        return len(self._d)


def _is_true(v: Any) -> bool:
    return v is True or str(v).lower() == "true"


class BILClient:
    def __init__(self, settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.s = settings or Settings.from_env()
        # `transport` is the test seam: tests pass httpx.MockTransport so no
        # network is touched.
        self._http = httpx.AsyncClient(
            base_url=self.s.api_base,
            timeout=self.s.timeout_s,
            transport=transport,
            headers={"User-Agent": "bil-mcp/0.1 (+https://github.com/fjorka)"},
            follow_redirects=True,
        )
        self._cache = TTLCache(self.s.cache_ttl_s, self.s.cache_max_entries)
        self._sem = asyncio.Semaphore(self.s.max_concurrency)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "BILClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ HTTP
    async def _request(self, method: str, path: str, **kw) -> dict:
        """One HTTP call with bounded concurrency and retry on transient errors."""
        last: Exception | None = None
        for attempt in range(self.s.max_retries + 1):
            try:
                async with self._sem:
                    log.debug("BIL request %s %s", method, path)
                    r = await self._http.request(method, path, **kw)
                if r.status_code >= 500:
                    raise BILAPIError(f"BIL API returned HTTP {r.status_code} for {path}")
                # BIL sends logical failures ("no entry found", "not a valid
                # variable") as HTTP 404/405 WITH a JSON body. So: if the body
                # is JSON, return it and let the caller read "success"/"message".
                # A 404 with an HTML body means the endpoint itself doesn't exist.
                if "json" in r.headers.get("content-type", ""):
                    return r.json()
                if r.status_code == 404:
                    raise BILAPIError(f"BIL API has no endpoint {path} (HTTP 404)")
                raise BILAPIError(f"BIL API returned non-JSON (HTTP {r.status_code}) for {path}: {r.text[:200]!r}")
            except (httpx.TransportError, BILAPIError) as e:
                last = e
                # Only network errors and 5xx are worth retrying.
                retryable = isinstance(e, httpx.TransportError) or "HTTP 5" in str(e)
                if not retryable or attempt == self.s.max_retries:
                    break
                await asyncio.sleep(0.5 * 2**attempt)
        if isinstance(last, httpx.TransportError):
            raise BILAPIError(f"Could not reach the BIL API ({type(last).__name__}: {last})") from last
        raise last  # type: ignore[misc]

    # ----------------------------------------------------------------- query
    async def query(self, endpoint: str, field: str, value: str) -> list[str]:
        """Return the bildids matching one `field=value` on one endpoint."""
        key = f"q|{endpoint}|{field}|{value.lower()}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        data = await self._request("GET", f"/query/{endpoint}", params={field: value})
        if _is_true(data.get("success")):
            ids = list(dict.fromkeys(data.get("bildids") or []))  # de-dupe, keep order
        elif NO_ENTRY in str(data.get("message", "")).lower():
            ids = []
        else:
            raise BILAPIError(f"BIL query {endpoint}?{field}= failed: {data.get('message')}")
        self._cache.set(key, ids)
        return ids

    async def fulltext(self, text: str) -> list[str]:
        """Full-text search. NOTE: multi-word text is OR'ed by the API."""
        return await self.query("fulltext", "text", text)

    # -------------------------------------------------------------- retrieve
    async def retrieve(self, bildid: str) -> dict | None:
        """Full metadata record for one dataset id, or None if not found."""
        recs = await self.retrieve_many([bildid])
        return recs.get(bildid)

    async def retrieve_by_doi(self, doi: str) -> dict | None:
        data = await self._request("GET", "/retrieve", params={"doi": doi})
        if not _is_true(data.get("success")):
            return None
        recs = data.get("retjson") or []
        if recs:
            rec = recs[0]
            bid = _record_bildid(rec)
            if bid:
                self._cache.set(f"r|{bid}", rec)
            return rec
        return None

    async def retrieve_many(self, bildids: Iterable[str]) -> dict[str, dict]:
        """Records for many ids, keyed by id. Missing ids are simply absent.

        Uses cached records first, then POSTs the rest in batches (default
        200 ids/batch, ~0.5 s each against the live API).
        """
        ids = list(dict.fromkeys(bildids))
        out: dict[str, dict] = {}
        missing: list[str] = []
        for i in ids:
            hit = self._cache.get(f"r|{i}")
            if hit is not None:
                out[i] = hit
            else:
                missing.append(i)

        bs = self.s.retrieve_batch_size
        batches = [missing[i : i + bs] for i in range(0, len(missing), bs)]

        async def one_batch(batch: list[str]) -> None:
            data = await self._request("POST", "/retrieve", json={"bildids": batch})
            if not _is_true(data.get("success")):
                if NO_ENTRY in str(data.get("message", "")).lower() or "no entries" in str(data.get("message", "")).lower():
                    return
                raise BILAPIError(f"BIL retrieve failed: {data.get('message')}")
            for rec in data.get("retjson") or []:
                bid = _record_bildid(rec)
                if bid:
                    self._cache.set(f"r|{bid}", rec)
                    out[bid] = rec

        await asyncio.gather(*(one_batch(b) for b in batches))
        # Return in the order requested.
        return {i: out[i] for i in ids if i in out}

    async def group_doi(self, group_id: str) -> dict | None:
        gid = group_id.strip()
        for prefix in ("https://doi.org/", "https://doi.brainimagelibrary.org/doi/", "10.35077/"):
            if gid.startswith(prefix):
                gid = gid[len(prefix) :]
        gid = gid.removeprefix("10.35077/")
        data = await self._request("GET", "/retrieve/groupdoi", params={"bildid": gid})
        if not _is_true(data.get("success")):
            return None
        return data.get("doc")

    # ----------------------------------------------------------------- files
    async def get_text(self, url: str) -> tuple[int, str]:
        """GET an absolute URL (used for the download server's directory pages).

        Uses the shorter download timeout: a directory page is small, so if no
        answer comes within ~15 s the server is down, not slow.
        """
        async with self._sem:
            r = await self._http.get(url, timeout=self.s.download_timeout_s)
        return r.status_code, r.text


def _record_bildid(rec: dict) -> str | None:
    for a in rec.get("Assets") or []:
        if isinstance(a, dict) and a.get("bildid"):
            return a["bildid"]
    return None
