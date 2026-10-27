# Learning path: bil-mcp from zero

Each step has a runnable script in this folder that works on the real server. Run them from the repo root:
`uv run python learn/<script>.py`. Don't move on until the check question feels easy.

| # | Step | You'll be able to explain | Script |
|---|------|--------------------------|--------|
| 1 | The wire | What an MCP host, client and server are; JSON-RPC over stdio; the initialize handshake | `01_raw_handshake.py` |
| 2 | Three primitives | Tools vs resources vs prompts, and who controls each (model / app / user) | `02_primitives.py` |
| 3 | Function → tool | How type hints, `Annotated`/`Field` and Pydantic returns become input/output schemas; argument validation | `03_schemas.py` |
| 4 | The BIL client | Why `client.py` knows nothing about MCP; BIL API quirks; retries, cache, batching | `04_client.py` |
| 5 | Search logic | Intersection of single-field queries, OR lists, exact verification, facets, linkage | `05_search.py` |
| 6 | Server plumbing | Lifespan and shared state, `ToolError` vs crashes, logging to stderr, annotations | `06_errors_lifespan.py` |
| 7 | Testing | A fake API built from real records, the in-process client, the stdio test, live contract tests (the 404 story) | `07_tests.py` |
| 8 | Hosts, transports, safety | Claude Desktop/Code config, Inspector, stdio vs streamable HTTP, read-only design, prompt injection | (config + Inspector) |
| 9 | Capstone | Defend decisions D1–D9 in `docs/ARCHITECTURE.md`; add a new tool yourself | your code |
