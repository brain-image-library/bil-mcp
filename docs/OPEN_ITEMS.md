# Open items and deferred decisions

Things noticed while building and teaching, deliberately left for later.

## To verify
- **`list_dataset_files` against the real download server.** The directory-page parser
  (`files.py`) has only been tested on a synthetic page. The download server was down during
  BIL's data-center move (Oct 2026; status: https://www.brainimagelibrary.org/datacentermove.html).
  When it's back: `uv run pytest -m live -k download`. If it reports "No entries parsed", save
  the real page and adapt `parse_index`.

## Behaviour to decide
- ~~**Paging.**~~ Decided Oct 2026: search returns breakdowns + 20 newest rows (ARCHITECTURE D11).
- ~~**`match="exact"` on multi-specimen datasets.**~~ Decided Oct 2026: keep ANY-specimen matching,
  report `specimen_sex` per row and `specimen_sex_mix` per search.
- **Species synonyms in breakdowns.** `fetch_all` on sex=Male shows `human 821` and
  `Homo sapiens 558` as separate values; see "Species synonyms" below.
- **Cache size limit** counts entries (50,000), not bytes. Normal use is a few MB; caching all
  ~17k records would be ~150 MB. Consider a byte-based limit.
- **Species synonyms.** Value counts merge case variants only ("Mouse"/"mouse"), not
  "marmoset"/"common marmoset". The future IR layer likely needs a synonym map; also worth
  reporting to BIL curation.
- **Concurrency cap (4) vs BIL capacity.** Ask BIL operators for worker count / rate limits.

## Distribution
Moved to [ROADMAP.md](ROADMAP.md).
