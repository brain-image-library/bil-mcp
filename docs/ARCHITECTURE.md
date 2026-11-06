# Architecture and design decisions

## The picture

```
 ┌──────────────── your machine ────────────────┐
 │                                              │
 │  MCP host (Claude Desktop / Claude Code)     │
 │    └─ MCP client ──stdin/stdout (JSON-RPC)──┐│
 │                                             ││
 │  bil-mcp process  ◄─────────────────────────┘│
 │   server.py   MCP layer: tools/resources/prompts
 │   strict.py   middleware: reject unknown tool arguments
 │   search.py   multi-filter logic, value counts, linkage
 │   files.py    download-server directory pages
 │   client.py   HTTP + retry + cache (no MCP)  │
 │   fields.py   filter key → (endpoint, field) │
 │   models.py   Pydantic output shapes         │
 └───────────────────────┬──────────────────────┘
                         │ HTTPS
          api.brainimagelibrary.org  (metadata)
          download.brainimagelibrary.org (files)
```

Imports only point downward: `server → search/files → client/fields/models → config`.
Nothing below `server.py` imports `mcp`, so the BIL logic can be reused from a notebook or script
and tested without a server.

## Decisions

| # | Decision | Why | Alternative not taken |
|---|---|---|---|
| D1 | Official `mcp` Python SDK (v2, `MCPServer`) | Tracks the spec, few dependencies, matches the official docs; lets you drop down to the low-level `Server` | Standalone `fastmcp` 2.x: more features, more abstraction |
| D2 | stdio transport by default | Local, per-user, no auth or ports; it's what desktop clients launch | Streamable HTTP is available behind a flag for shared or remote use |
| D3 | Live API + in-memory TTL cache | Always fresh, no index to rebuild; BIL is fast (~0.2 s/query, ~0.5 s per 200-record POST) | A local SQLite index: instant value counts, but goes stale and adds code |
| D4 | Field-level filters; IR seam in `search.search()` | Maps 1:1 to the verified API, so nothing is invented | Intent-level IR now: would mean guessing at a schema still being designed |
| D5 | Links + one-level file listing, no downloads | BIL datasets are often terabytes; an agent shouldn't start those transfers | Download tool with size caps |
| D6 | Pydantic return types | SDK publishes an output schema and returns structured content; forces compact responses | Raw dicts (still used where the shape is BIL's own, e.g. `get_dataset`) |
| D7 | Errors become `ToolError` | The model gets a readable message (`is_error: true`) it can recover from, and the session stays up | Letting exceptions escape |
| D8 | Lifespan-managed shared client | One connection pool and one cache per process, closed cleanly on shutdown | A client per call: no caching, slower |
| D10 | Unknown tool arguments are rejected (middleware in `strict.py`); schemas carry `additionalProperties: false` | The SDK's default silently drops misnamed arguments (`max_results` instead of `limit`), so the model gets default behaviour and no warning. mcp 2.3 has no setting for this, so it goes through the public `MCPServer.middleware` hook | Accept the SDK default |
| D9 | Ids sorted by bildid, rows ordered newest first | BIL returns ids in no stable order; sorting makes results reproducible. Ordering by date needs the records, which D11 now fetches anyway | Paging over bare ids |
| D11 | Search reads every record up to `stats_limit` (2000, so the automatic wait stays under 5 s) and returns breakdowns + 20 brief rows, never all records | GETs (ids) are cheap; POSTs cost 0.3–1 s per round of 4×200 records. Fetching is fast, but sending records to the model is not: 1000 summaries ≈ 575k chars. So the server reads everything and returns a small result. Above the limit it returns counts and `next_steps`, and the agent asks the user (narrow, or `fetch_all`). Records stay in the cache, so narrowing afterwards needs no POSTs | `limit`/`offset` pages plus up to 500 bare ids (the old design: ~14k chars at `limit=10`, mostly ids) |

## BIL API facts the code depends on

All verified live in Oct 2026 (facts 2 and 9 in Nov 2026), and re-checked by `tests/test_live.py` and
`tests/test_taxonomy_live.py`:

1. `/query/<endpoint>?<field>=<value>` takes **one** variable. Two are rejected.
2. Query values are **case-insensitive regular expressions**, searched anywhere in the field: a plain
   word acts as a substring (`sex=Male` ⊇ `sex=Female`), `^mouse$` is exact, and an invalid pattern
   (e.g. `(`) returns **HTTP 500**. So every filter value is sent through `re.escape`, and the
   `species` filter sends one anchored pattern per BIL species string (`taxonomy.exact_pattern`).
3. Multi-word `fulltext` is **OR**.
4. "No hits" is **HTTP 404 + JSON** `{"success":"false","message":"GET failure, no entry found"}`.
   A 404 with an HTML body means the endpoint doesn't exist.
5. `POST /retrieve {"bildids":[...]}` returns found records plus a `status` map. If none are found
   it returns **HTTP 405** "POST failure, no entries found". An empty list gives HTTP 500.
6. `success` is the string `"true"`/`"false"`.
7. `Dataset.bildirectory` `/bil/data/ab/cd/<uuid>/x` → `https://download.brainimagelibrary.org/ab/cd/<uuid>/x/`.
8. Field values are not normalized (`Mouse`, `mouse`, `common marmoset`).
9. `GET /stats?type=all` (undocumented) returns archive-wide counts, including a `species` list of
   `{name, ...}` entries. The counts are **specimen entries, not datasets**: a dataset with several
   specimens counts several times. Used only by the live test that checks `species_map.yaml` covers
   every species string BIL holds; dataset counts always come from `/query` ids.

Fact 4 is a lesson in why contract tests exist: the first version of the client treated every 404
as "endpoint missing", and the fake API in the offline tests had the same wrong assumption, so
every offline test passed. The live test caught it.

## Request flow: `search_datasets({"species":"Human","technique":"MERFISH"})`

1. Client sends `tools/call`. The SDK validates arguments against the JSON Schema it generated
   from the function signature.
2. `search.matching_ids` resolves each key through `fields.resolve`. `species` goes through
   `taxonomy.Taxonomy.search`: "Human" resolves to *Homo sapiens*, so it sends one anchored query per
   BIL string for that organism (`^\s*human\s*$`, `^\s*homo\ sapiens\s*$`) and keeps a
   `species_report`. Other values are escaped (`technique=MERFISH`). All queries run **concurrently**.
   A species term that resolves to no organism BIL holds sends nothing and returns 0 with the report.
3. It intersects the two id sets (1273 ∩ 280 → 40) and sorts them.
4. `search.resolve_set`: 40 ≤ `stats_limit`, so it retrieves all 40 records (`POST /retrieve`, one
   batch; cached). Above the limit it stops here and returns counts + `next_steps`.
5. (If `match="exact"`) it keeps only records whose field equals the value (any specimen).
6. It builds breakdowns of all matches and brief rows for the 20 newest.
7. It returns a `SearchResult`. The SDK serializes it as `structuredContent`, plus a text copy for
   older clients.

## Where to extend

* **IR / natural-language search**: write `ir_to_filters(ir) -> (filters, text)` and add a tool
  that calls `search()`.
* **Cross-archive (NeMO)**: a sibling `nemo_client.py`, plus a tool that joins on `linkage.xid`
  (NHash).
* **Local index**: implement `retrieve_many` from SQLite behind the same interface. Nothing above
  `client.py` would change.
