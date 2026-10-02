# Draws Database — Token-Efficient Data Access

> **Read this first.** To conserve context tokens, always query `dbo.Draws`
> via `ml_models/db_query.py` **instead of using `Read` on `data/*.json`**.
> The JSON files are source-of-truth for writes; the DB is the read cache.

## Databases

### LuckyDb — draw history cache

- Server: `DESKTOP-QR14EDK\SQLEXPRESS01`
- Database: `LuckyDb`
- Auth: Integrated / Trusted
- Table: `dbo.Draws`

## Schema

```
dbo.Draws
  DrawId     INT         -- series id (e.g. 3217)
  EventIndex TINYINT     -- 1..N within a draw (canonicalized lex order)
  N01..N14   TINYINT     -- 14 sorted numbers in [1,25]
  DrawDate   DATE NULL
  Sources    NVARCHAR(50) NULL  -- "curated", "api", "archive" or CSV of them
  PK         (DrawId, EventIndex)
```

Current state (`python ml_models/db_query.py stats`):
- 15,244 events across 2,451 draws (IDs 799..3249, latest 3249 dated 2026-07-05)

**Important:** `EventIndex` is the post-canonical lex order of events within a
draw, **not** the original draw-time position. Scoring set-vs-event is
order-independent, so this is harmless for prediction/novelty; only matters if
you want original draw-order semantics (don't rely on it).

## Canonical read commands

Always prefer these over opening JSON files directly.

| Command | Output | When to use |
|---|---|---|
| `python ml_models/db_query.py stats` | one line: events/draws/id range/date range | session start, sanity check |
| `python ml_models/db_query.py latest N` | last N draws (id, date, #events) | "what's the latest?" |
| `python ml_models/db_query.py get <id>` | one draw: date + 7 events | inspect a specific series |
| `python ml_models/db_query.py range <lo> <hi>` | count of events in range (`-v` to list) | windowed analysis |
| `python ml_models/db_query.py pool --count` | `total_events=X unique=Y` | pool size check |
| `python ml_models/db_query.py pool --all` | one 14-set per line (14,985 lines today) | bulk pool export |
| `python ml_models/db_query.py novelty "n1,...,n14"` | `NOVEL` / `COLLISION draw=X event=EY` | novelty check for a candidate |
| `python ml_models/db_query.py score <id> "n1,...,n14"` | per-event match count for one set | single-ticket score |
| `python ml_models/db_query.py score-family <id>` | v2 8-family best/per-set/per-event/avg | production scoring |
| `python ml_models/db_query.py has-draw <id>` | `X: N events` or `X: missing` | existence check |
| `python ml_models/db_query.py count-by-year` | per-year draw + event counts | historical density |

## Token budget comparison (typical)

| Operation | JSON via `Read` | DB via helper |
|---|---|---|
| "What's the latest series?" | ~300k tokens (full file) | ~30 tokens |
| "Get events for draw 3217" | ~300k tokens | ~100 tokens |
| "Score v2 vs 3217" | ~300k + script tokens | ~50 tokens |
| "Novelty of a candidate" | ~300k + filtering | ~10 tokens |
| "Pool size" | ~300k tokens | ~20 tokens |

## When write happens: keeping DB in sync

The JSON files remain the source-of-truth for writes. After any data change
(e.g., `python ml_models/add_series.py ...`), refresh the DB:

```
python ml_models/db_init.py --drop
```

This drops and rebuilds `dbo.Draws` from `data/all_draws.json` in one pass
(~1 second). There is intentionally no incremental-insert path — a full
rebuild is cheap and eliminates drift risk.

## Adding a new query to the helper

Edit `ml_models/db_query.py`:
1. Add a `cmd_<name>(args, cur)` function.
2. Register a subparser block in `main()`.
3. Add the dispatch entry in the `fn` dict.
4. Document it in the table above.

Keep outputs **terse, one-line-per-record when possible** — this file exists
to protect the context window.

## Raw SQL access

For ad-hoc exploration use `sqlcmd` (pre-installed):

```
sqlcmd -S "DESKTOP-QR14EDK\SQLEXPRESS01" -d LuckyDb -E -Q "SELECT TOP 5 DrawId, DrawDate FROM dbo.Draws ORDER BY DrawId DESC"
```

For recurring ad-hoc patterns, promote them into `db_query.py` rather than
running `sqlcmd` repeatedly — the helper is where token savings compound.

---

### BsaDb — conditional-score swapper pipeline (E1-only)

- Server: `DESKTOP-QR14EDK\SQLEXPRESS01`
- Database: `BsaDb`
- Auth: Trusted / `TrustServerCertificate=yes`
- Schema: `bsa`

**Connection string:**
```
DRIVER={ODBC Driver 18 for SQL Server};Server=DESKTOP-QR14EDK\SQLEXPRESS01;Database=BsaDb;Trusted_Connection=yes;TrustServerCertificate=yes;
```

**Update after each new draw:**
```bash
python ml_models/bsadb_update.py <draw_id>
```

**Get prediction for next series** (reads swapper output for last known draw):
```python
# E1 ticket for series N+1 is stored under SeriesId=N
SELECT Number, Source FROM bsa.swapper_pred WHERE SeriesId=<prev_draw_id> ORDER BY Number
```

**Key tables (schema `bsa`):**

| Table | Rows/draw | Description |
|---|---|---|
| `actual_e1` | 14 | E1 numbers for each draw |
| `draws_long` | ~90 | All events long-format (SeriesId, Number, IsEvent1, ElementsRowId) |
| `features` | 25 | Per-number frequency/recency features (Recency, FreqLast10/20/50/100, MeanGap, GapStd, AppearancesTotal, Streak, E1Freq*) |
| `coappear` | 25 | Mean and top co-appearance count over last 50 E1 draws |
| `cond_scores` | 25 | CondScore = avg overlap with previous draw when this number appeared (L50 E1 window); ranked |
| `predictions` / `ranked` | 25 | Same ranking as cond_scores, Score=NULL (mirrors original schema) |
| `cond_scores_r` | ~11 | Swap-candidate subset: bottom-7 predicted + top-4 non-predicted |
| `nonpred_r` | 11 | Non-predicted numbers ranked by CondScore (swap insert candidates) |
| `patch_drops_r` | 14 | Predicted numbers ranked weakest-first (swap drop candidates) |
| `swap_pairs_r..r6` | 0–N | Swap decisions per round: InsertNumber, DropNumber, scores |
| `swapper_pred` | 14 | Final 14-number ticket with `kept`/`inserted` labels |
| `swapper_hits` | 1 | Hit count of swapper_pred[sid-1] vs E1[sid] — backfilled on next update |
| `hits` | 1 | Hit count of base top-14 prediction vs E1 |
| `cfg` | 1 | LatestSeries, WindowStart, WindowEnd (all current as of 3249) |

**Pipeline summary:**
1. Score all 25 numbers by CondScore (avg overlap when they appeared, last 50 E1 draws)
2. Base prediction = top-14 by CondScore
3. Up to 6 swap rounds: if any non-predicted number scores higher than the weakest predicted number, swap them
4. Final ticket = result after all rounds; stored in `swapper_pred`

**OOS tracking:** started from draw 3228. `bsa.hits` tracks base prediction, `bsa.swapper_hits` tracks post-swap ticket.

**Ad-hoc query:**
```
sqlcmd -S "DESKTOP-QR14EDK\SQLEXPRESS01" -d BsaDb -E -Q "SELECT TOP 10 SeriesId, SwapHits FROM bsa.swapper_hits ORDER BY SeriesId DESC"
```
