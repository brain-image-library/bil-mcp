# bil-mcp: Brain Image Library MCP server

An [MCP](https://modelcontextprotocol.io) (Model Context Protocol) server that lets your AI agent
(Claude Desktop, Claude Code, Cursor, or any other MCP client) search and inspect datasets in the
[Brain Image Library](https://www.brainimagelibrary.org) (BIL). It runs **locally** on your machine,
needs **no API key**, and is **read-only**.

> "Find human MERFISH datasets in BIL, break them down by project, and tell me how to download the
> largest one."

The server turns requests like that into calls against the public
[BIL metadata API](https://www.brainimagelibrary.org/metadataapi.html), and covers its limits:

| BIL API on its own | With bil-mcp |
|---|---|
| One field per query | Multi-filter AND, OR lists, AND-ed full-text words |
| Returns only ids | Compact summaries, paging, facet counts |
| Substring match (`sex=male` also matches `Female`) | `match="exact"` checks each hit against its full record |
| Values not normalized (`Mouse` / `mouse`) | Facets merge case variants |
| BICAN linkage is buried in records | `find_related_datasets` follows donor / tissue / section ids |

## Quick start

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.10+ for you if needed).

```bash
git clone <this repo> bil_mcp && cd bil_mcp
uv sync                 # create .venv and install dependencies
uv run pytest           # offline tests (no network needed)
uv run bil-mcp --help
```

You don't run `bil-mcp` by hand in normal use. Your MCP client starts it as a subprocess and talks
to it over stdin/stdout.

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) and add:

```json
{
  "mcpServers": {
    "bil": {
      "command": "/usr/local/bin/uv",
      "args": ["--directory", "/ABSOLUTE/PATH/TO/bil_mcp", "run", "bil-mcp"]
    }
  }
}
```

Use the output of `which uv` for `command`, because Desktop apps don't inherit your shell's `PATH`.
Restart Claude Desktop. "bil" should then appear under the connectors/tools menu.
A copy is in [`examples/claude_desktop_config.json`](examples/claude_desktop_config.json).

### Claude Code

```bash
claude mcp add bil -- uv --directory /ABSOLUTE/PATH/TO/bil_mcp run bil-mcp
```

Or copy [`examples/claude_code.mcp.json`](examples/claude_code.mcp.json) to `.mcp.json` at the root of a repository (project scope), so everyone working there gets it.

### Any other client / debugging with MCP Inspector

```bash
npx @modelcontextprotocol/inspector uv --directory . run bil-mcp
```

This opens a web UI where you can list and call every tool by hand. It's the best way to see the
raw protocol messages.

### HTTP mode (optional)

`uv run bil-mcp --transport streamable-http --port 8000` serves MCP at `http://localhost:8000/mcp`.
It has no authentication, so keep it on localhost.

## What the agent gets

**Tools** (all read-only):

| Tool | Purpose |
|---|---|
| `search_datasets` | Filters + free text → total count, summaries, remaining ids, reproducible query URLs |
| `get_dataset` | Full record by bildid or DOI, optionally only some sections |
| `get_dataset_summaries` | Summaries for up to 200 ids |
| `facet_counts` | Value counts of one field over a search or id list ("which techniques?") |
| `find_related_datasets` | Other datasets from the same BICAN donor / tissue / section |
| `get_access_info` | HTTPS URL, `wget` command, Bridges-2 path, viewer link, license |
| `list_dataset_files` | One directory level from the download server (no downloading) |
| `get_group_doi` | Collection DOI (`g.N`) metadata |
| `list_fields` | All filter keys with descriptions and verified example values |

**Resources**: `bil://fields` (field catalog) and `bil://dataset/{bildid}` (full record as JSON).

**Prompts**: `find_datasets(question)` and `dataset_report(bildid)`, reusable instructions the user
can pick from the client's prompt menu.

### Filters

```json
{"filters": {"species": "Mouse", "generalmodality": "spatial transcriptomics", "consortium": "BICAN"},
 "text": "hypothalamus", "match": "exact", "limit": 20}
```

* Keys are short names (`species`, `technique`, `contributorname`, `linkage.xid`, ...) or aliases
  (`organism`, `modality`, `nhash`). See `list_fields`.
* A list value is OR: `{"species": ["Human", "Macaque"]}`.
* Multi-word `text` is AND by default (`text_mode="any"` for BIL's native OR).

## Configuration

All optional, set through the `env` block of your client config:

| Variable | Default | |
|---|---|---|
| `BIL_API_BASE` | `https://api.brainimagelibrary.org` | |
| `BIL_DOWNLOAD_BASE` | `https://download.brainimagelibrary.org` | |
| `BIL_TIMEOUT` | `60` | seconds per HTTP call to the metadata API |
| `BIL_DOWNLOAD_TIMEOUT` | `15` | seconds per directory page on the download server (fail fast when it's down) |
| `BIL_CACHE_TTL` | `3600` | seconds; metadata changes rarely |
| `BIL_RETRIEVE_BATCH` | `200` | ids per POST /retrieve |
| `BIL_MAX_CONCURRENCY` | `4` | parallel HTTP calls (be polite to BIL) |

## Development

```bash
uv run pytest            # 33 offline tests: unit, MCP-protocol, stdio subprocess
uv run pytest -m live    # contract tests against the real BIL API
uv run python scripts/record_fixtures.py   # refresh the real records used offline
```

* Offline tests run against **real BIL records** (`tests/fixtures/records.json`) served by a fake
  API that copies BIL's actual behaviour (substring matching, 404+JSON for "no entry found", ...).
* Live tests check that BIL still behaves that way. If one fails, the API changed.
* `test_download_server_listing` could not be run while building (the download server was not
  reachable from the build sandbox). Run `uv run pytest -m live` on your machine to check it.

Architecture and design decisions: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Troubleshooting

* **Server doesn't show up in Claude Desktop**: use an absolute path to `uv`, then check
  `~/Library/Logs/Claude/mcp-server-bil.log`.
* **"Could not reach the BIL API"**: check your network or VPN; try `curl https://api.brainimagelibrary.org/`.
* **stdout is the protocol channel.** Output written to stdout before the server starts (e.g. a
  `print()` at import time) corrupts it. mcp 2.x diverts stdout to stderr while serving, so a
  stray `print()` inside a tool is harmless here, but log to stderr anyway (`--log-level DEBUG`
  shows every request to BIL).
