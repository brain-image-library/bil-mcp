# Roadmap

Where bil-mcp is going, roughly in order. Small behaviour decisions and things to verify live in
[OPEN_ITEMS.md](OPEN_ITEMS.md); design decisions already made are in [ARCHITECTURE.md](ARCHITECTURE.md).
Background on what other archives ship: the DANDI MCP landscape review (Oct 2026), in the BIL MCP project docs.

## Now: v1 metadata server (local, stdio)
- [x] Search, dataset details, field values / `count_by_field`, file listing, access info, related datasets
- [x] Strict arguments, structured outputs, readable errors, audit log, live contract tests
- [ ] Verify `list_dataset_files` against the real download server (see OPEN_ITEMS)
- [x] Settle paging and `match="exact"` behaviour: breakdowns + 20 newest rows, `fetch_all` above 2000, sex mix reported (ARCHITECTURE D11)

## Next: distribution
- [ ] Move the repo to a BIL/PSC GitHub org (not a personal account)
- [ ] Publish to PyPI so local users run `uvx bil-mcp` with no install step
      (ephys-mcp is a working example of this pattern: `claude mcp add ephys -- uvx ephys-mcp`)
- [ ] Release from GitHub Actions with PyPI Trusted Publishing, so releases carry provenance attestations
- [ ] Claude Desktop extension (`.mcpb`: `manifest.json` + `mcpb pack`) for one-click install;
      Docker MCP Toolkit as the alternative for users who run many servers across clients
- [ ] Hosted Streamable HTTP endpoint at PSC, same codebase, so users only add a URL.
      Lesson from DANDI: the community servers depend on personal Vercel/Railway hosts, which is a sustainability risk
- [ ] List in the official MCP registry

## Next: evaluation
- [ ] Fixed question set of 20–30 real BIL data-finding tasks with known answers
- [ ] Compare an agent with and without the server: accuracy, time, tool calls, cost per task
      (the DANDI Dandiset Explorer paper reported per-task cost and reviewer ratings)
- [ ] Try Harbor (harbor-framework) as the eval harness

## Next: skills shipped alongside the server
- [ ] BIL skills that teach agents common workflows (find data → check access → cite)
- [ ] MCP prompts for the same workflows (ephys-mcp ships an `analyze_session` prompt)

## Later: new tools
- [ ] **File header inspection.** Read OME-Zarr / TIFF / IMS headers remotely and return shape,
      voxel size, channels, pyramid levels, plus a Python snippet to load the data. No downloads (D5).
      This is BIL's analog of DANDI/Neurosift `nwb_file_info`, the most useful tool in that ecosystem.
- [ ] **Docs search.** Search the BIL user guide and submission docs, or expose them as MCP resources,
      so agents write correct access code (Neurosift's analog: `pynwb_docs_semantic_search`)
- [ ] **Citation and licence on every dataset response**, plus link conventions in tool descriptions
      so agents cite clickable BIL portal URLs
- [ ] **Intent-level / natural-language search.** Implement `ir_to_filters()` at the IR seam in `search.search()`; needs a species synonym map

## Later: derived index
The most useful DANDI tools (semantic search, search by data type, SQL) all run on a precomputed index, not the live API.
- [ ] Local index behind `retrieve_many` (SQLite); nothing above `client.py` changes
- [ ] Semantic search over dataset descriptions (embeddings)
- [ ] Modality/format index built from file headers (dims, voxel size, channels) to search by image properties

## Later: cross-archive
- [ ] NeMO client and a tool that joins on `linkage.xid` (NHash), for BICAN linkage
