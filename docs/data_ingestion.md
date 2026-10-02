# Data Ingestion & File Layout

Authoritative guide for where draw data lives, how the files relate, and the
required workflow when a new Kino draw is reported.

## Files

| File | Role | Format | Writer |
|------|------|--------|--------|
| `data/full_series_data.json` | Legacy curated file. Read by ~20 scripts (pm_agent, backtests, experiments, production_predictor). Do not delete. | `{draw_id: [events]}` | `ml_models/add_series.py` |
| `data/historical_series_data.json` | Earlier-era archive, draws 2104..2979 (8 events/draw). Read-only on disk; kept for provenance. | `{draw_id: [events]}` | Never (historical snapshot) |
| `data/full_api_data.json` | Raw scrape from `https://kinohistorico.cl/kino-api/draws/{N}`, draws 799..3215. Read-only on disk; kept for provenance. | `{draw_id: {date, events}}` | `audits/scrape_kino_api.py` (one-off) |
| `data/all_draws.json` | **Canonical unified file** (~939 KB). Union of all three sources, deduped per-draw, with dates + source tags. 2,417 draws, 15,006 events. | `{draws: {draw_id: {date, events, sources}}}` | `ml_models/add_series.py` or `audits/merge_all_draws.py` (rebuild) |
| `data/filter_pool.json` | Flat set of all historical events for novelty checking. Derived from `all_draws.json`. 14,971 unique events. | `{events: [...]}` | `ml_models/add_series.py` or `audits/build_filter_pool.py` (rebuild) |

## Workflow: new draw reported

When the user announces a new Kino draw:

1. **If the date is not given, ask for it (`YYYY-MM-DD`)**. The date is stored
   only in `all_draws.json` and cannot be recovered from event numbers alone.
2. Run `add_series.py` with the draw id, date, and each event (14
   comma-separated ints per event):

   ```bash
   python ml_models/add_series.py <draw_id> <YYYY-MM-DD> \
       "n1,n2,...,n14" "n1,n2,...,n14" ... "n1,n2,...,n14"
   ```

   Or invoke with no events for interactive mode (paste one event per line).

3. The script updates three files atomically:
   - `full_series_data.json` — appends `{draw_id: [events]}`.
   - `all_draws.json` — adds/merges `{draw_id: {date, events, sources:['curated']}}`.
   - `filter_pool.json` — fully regenerated from `all_draws.json`.

4. Rebuild LuckyDb cache:
   ```bash
   python ml_models/db_init.py --drop
   ```

5. Update BsaDb (conditional-score swapper pipeline):
   ```bash
   python ml_models/bsadb_update.py <draw_id>
   ```

Validation: each event must be exactly 14 distinct ints in `[1, 25]`. The draw
id must not already exist in `full_series_data.json` (pass `--force` to
overwrite an existing draw).

## Rebuilding from scratch

If `all_draws.json` or `filter_pool.json` becomes corrupted or you want to
fully re-derive them from the three source files:

```bash
python audits/merge_all_draws.py    # rebuilds data/all_draws.json
python audits/build_filter_pool.py  # rebuilds data/filter_pool.json from all_draws.json
```

Both scripts are idempotent and safe to re-run.

## Consumer guidance

- **Novelty checks**: prefer `data/filter_pool.json` (used by
  `ml_models/designed_family_predictor.py`; falls back to
  `full_series_data.json` if the pool is missing).
- **Backtests and analyses**: prefer `data/all_draws.json` for new code —
  has dates and per-draw source attribution. Legacy scripts keep reading the
  file they were written against; migrate opportunistically.
- **Never write `full_series_data.json`, `all_draws.json`, or
  `filter_pool.json` by hand**. Go through `add_series.py` so all three stay
  in sync.

## Known data-integrity notes

- Modern-era overlap draws (2980..3215) have 7 events in
  `full_series_data.json` vs 6 in `full_api_data.json`. The 7th event in the
  curated file is intentional extra-filter-pool data (see session-25.6 entry
  in `activity_log.md`).
- Draws 3083, 3097, 3101 have a single-number discrepancy between the curated
  file and the API (13/14 overlap, likely one transcription typo). Not
  auto-reconciled. API is likely authoritative but has not been propagated to
  the curated file.
