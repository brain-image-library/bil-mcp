# Open items and deferred decisions

Things noticed while building and teaching, deliberately left for later.

## To verify
- **`list_dataset_files` against the real download server.** The directory-page parser
  (`files.py`) has only been tested on a synthetic page. The download server was down during
  BIL's data-center move (Oct 2026; status: https://www.brainimagelibrary.org/datacentermove.html).
  When it's back: `uv run pytest -m live -k download`. If it reports "No entries parsed", save
  the real page and adapt `parse_index`.

## Behaviour to decide
- **Paging.** Keep `limit`/`offset` paging, return all ids with N summaries, or steer large
  sets to `facet_counts`?
- **`match="exact"` on multi-specimen datasets.** Currently a dataset matches if ANY specimen
  matches (32 of 287 male spatial-transcriptomics datasets are mixed-sex). Alternatives:
  ALL specimens must match, or report the mix in each summary.
- **Cache size limit** counts entries (50,000), not bytes. Normal use is a few MB; caching all
  ~17k records would be ~150 MB. Consider a byte-based limit.
- **Species synonyms.** Facets merge case variants only ("Mouse"/"mouse"), not
  "marmoset"/"common marmoset". The future IR layer likely needs a synonym map; also worth
  reporting to BIL curation.
- **Concurrency cap (4) vs BIL capacity.** Ask BIL operators for worker count / rate limits.
