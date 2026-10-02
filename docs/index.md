# Lottery Prediction System - Documentation Index

This directory contains the refactored, modular documentation for the lottery prediction project.

## Core Documents
- [**Draws Database (token-efficient data access)**](database.md) — **READ FIRST**. `dbo.Draws` in `LuckyDb` is the read cache for all draw data. Use `ml_models/db_query.py` instead of `Read` on `data/*.json`.
- [System Overview](system_overview.md) — Project goals, data format, statistical reality (IID), and key signals.
- [Data Ingestion](data_ingestion.md) — File layout and required workflow when a new draw is reported.
- [V21 Engine](v21_engine.md) — Legacy Signal+Diversity architecture (superseded).
- [V25 & V26 Engines (Legacy)](v26_engine.md) — Continuous Operations Framework (superseded).
- [Historical Discoveries](history.md) — Log of failed approaches and retired systems.
- [Activity Log](activity_log.md) — Active tracker for runs, data additions, and evaluations.
- [Conversation Log](conversation_log.md) — Index of pivotal prompts and AI responses.

### Archived (`docs/old/`)
- `fasttree_pipeline.md` — original ML/LightGBM architecture (deprecated 2026-05-10, audit found broken feature pipeline).
- `summary_2026-05-10.md` — session snapshot prior to ML deprecation.

## Current Production Systems (updated 2026-07-20)

**Current draw: 3255** (2026-07-19, ingested). **Upcoming/predicting for: 3256.**

### System A — Designed 8-Family v2 (coverage guarantees)
**8 static sets** built via SA-refined greedy set-cover. Guarantees:
- 100% events hit at >=8/14
- 99.49% events hit at >=9/14
- 12+ coverage at provable ceiling (41,280/41,280 events)
- Max pairwise overlap = 8 (disjoint 12+ neighborhoods)

OOS [3193..3255] n=63: static sets, zero-variance per-series. Latest (3255): best=10/14, avg=9.71.

```bash
python ml_models/designed_family_predictor.py <sid>   # 8-set prediction
python ml_models/pair_weight_evaluator.py <sid>       # pair regime diagnostic
python ml_models/pair_regime_diagnostic.py <sid>      # compact regime map
```

> **No "primary" single-ticket engine.** B, B2 and C are statistically indistinguishable from each other and from IID. On the last-5 window (as of 3255): B(7)=B2(7) tied, C(5) weakest. Rankings continue to churn draw to draw. Report all three; do not recommend one over the others. Only System A has a provable floor.

### System B — Recurrence-Ranking Predictor (E1-only single-ticket)
**1 ticket** from an 11-stage deterministic pipeline targeting E1. **No ML.**

Replays all draws with recency-weighted (half-life=26) k=1..7 combination rankings,
cross-level influence consolidation, two disjoint best-7 groups, unique validator
(no collision vs DB combos) + no-repeat ledger. All stages are PASS/FAIL gated.

OOS [3193..3255] n=63: avg **8.032** (+0.192 vs IID 7.840), max=11, 2@11+, 0@12+.
Edge decaying continuously (8.326 at n=46 → 8.192 at n=52 → 8.048 at n=62 → 8.032 at n=63). Latest (3255): 7/14.

**Recent-segment degradation confirmed** (2026-07-20): last-20=7.60, last-10=7.10, last-5=7.00, all below IID and worsening the more recent the window. Root cause traced: half-life weighting locked onto number 6 (ran 80.6% hot in OOS first half, lifetime rate 56.49% — at theoretical) and reacts too slowly as the streak reverts (a draw 52 series back still carries 25% weight at hl=26). An adaptive "regime gate" fix (`ml_models/regime_gate_experiment.py`, standalone, not wired into production) was OOS-tested and returned a null result — full-window avg identical to 4dp (8.032 vs 8.032), confirming there is no real regime to detect faster, just noise reverting to noise. See [[recurrence_predictor]] memory for full writeup. **Do not re-attempt adaptive/regime-aware reweighting without new evidence the process is non-IID.**

Do NOT reintroduce EWMA/momentum/hot-hand/bias. Ledger: `ml_models/recurrence_ledger.json`.

```bash
python ml_models/recurrence_predictor.py <sid>              # predict
python ml_models/recurrence_predictor.py --oos [--from 3193] # OOS eval
```

### System B2 — Force-Evaluator / Beam-Search Cascade (E1-only single-ticket)
**1 ticket** from a beam-search cascade over k=1..7 co-occurrence scores. **No ML.**

72-config sweep optimal: `--hl 20 --bw 100 --mode hybrid`. half_life is the dominant
parameter (hl=20 mean=8.092, hl=100 regresses to 7.756). Registry: `ml_models/force_evaluator_registry.json`.

OOS [3193..3255] n=63: avg **8.016** (+0.176 vs IID), max=11, 4@11+, 0@12+.
Same decaying pattern as System B (shared mechanism, see above). Latest (3255): 7/14.

```bash
python ml_models/force_evaluator.py <sid> [--hl 20] [--bw 100] [--mode hybrid]  # predict
python ml_models/force_evaluator.py --oos [--from 3193]                           # OOS eval
python ml_models/sweep_force_evaluator.py                                         # 72-config sweep
```

### System C — BsaDb EWMA Swapper (E1-only)
**1 ticket** from delta-EWMA top-14 stored in BsaDb. **No ML.**

CondScore base retired 2026-05-23 (OOS avg 7.947 vs EWMA 8.211). Current system:
EWMA swapper updates on each new draw; prediction for series N stored as
`bsa.swapper_pred WHERE SeriesId=N−1`.

OOS live since 3228 (n=26 as of 3254): avg **7.385** (−0.455 vs IID), underperforming IID historically.
Currently underperforming IID — EWMA edge has not held up live; tracked for monitoring, not relied on. Latest (3255): 5/14, weakest of the three this cycle.

```bash
python ml_models/bsadb_update.py <draw_id>   # ingest result + store next prediction
# read ticket: bsa.swapper_pred WHERE SeriesId=<draw_id>  (predicts draw_id+1)
```

See `docs/database.md` for full BsaDb schema and ad-hoc query reference.

## Quick Reference

| Task | Command |
|------|---------|
| System status | `python ml_models/pm_agent.py report` |
| Add new draw | `python ml_models/add_series.py <id> <date> "n,n,..." ...` |
| Rebuild LuckyDb | `python ml_models/db_init.py --drop` |
| Update BsaDb | `python ml_models/bsadb_update.py <draw_id>` |
| Score vs v2 family (E1) | `python ml_models/db_query.py score-family <id>` |
| 8-set prediction (Sys A) | `python ml_models/designed_family_predictor.py <sid>` |
| Recurrence ticket (Sys B, no primacy) | `python ml_models/recurrence_predictor.py <sid>` |
| Force-evaluator ticket (Sys B2, no primacy) | `python ml_models/force_evaluator.py <sid> --hl 20 --bw 100 --mode hybrid` |
| BsaDb ticket (Sys C) | query `bsa.swapper_pred WHERE SeriesId=<prev_sid>` |

## Key Signals & Findings

- **No "primary" single-ticket engine** (revised 2026-06-25, reconfirmed 2026-07-20): B (recurrence), B2 (force), C (BsaDb) are statistically indistinguishable from each other and from IID. Both B and B2's edges are decaying continuously, not flat (recurrence: 8.326 at n=46 → 8.192 at n=52 → 8.048 at n=62 → 8.032 at n=63; force: parallel decay to 8.016 at n=63); rankings keep churning draw to draw. All edges fail significance pre-Bonferroni (edge·√n z-proxy down to ≈1.4-1.5σ at n=63, from a peak of ≈3.2σ at n=46). Report all three; recommend none. Recurrence still supersedes the retired signal_predictor.py (delta-EWMA).
- **Recurrence/force decay mechanism traced (2026-07-20)**: both engines are recency-weighted frequency counters (half-life 20-26 draws). Number 6 ran hot at 80.6% in OOS [3193..~3224] then cooled to 65.6% in [~3224..3255] (lifetime rate 56.49%, at theoretical — no structural bias). The engines' edge peaked while the streak was live and has been decaying as it reverts to baseline; half-life weighting is structurally slow to unwind a broken streak (a draw 52 series back still carries 25% weight). An adaptive regime-detection fix (`ml_models/regime_gate_experiment.py`) was built and OOS-tested — **null result**, full-window avg identical to plain engine to 4dp — confirming there was no real regime to detect faster, just noise on noise. **Do not re-attempt adaptive/regime-aware reweighting on frequency counting** without new evidence the process is non-IID. See [[recurrence_predictor]] memory.
- **Do NOT reintroduce EWMA/momentum/hot-hand/bias** into the recurrence pipeline: hot-hand was proven anti-signal OOS (−0.39 hits/draw); bias was proven overfit (cosine −0.28 between halves). Both stages permanently removed.
- **Force-evaluator** (beam-search cascade, hl=20/bw=100/hybrid): OOS avg 8.016 (n=63). Neck-and-neck with recurrence (8.032); gap is noise-level, not significant. Same decay mechanism as recurrence (shared: both are recency-weighted frequency counters).
- **Predictor `sid` convention**: `recurrence_predictor.py`/`force_evaluator.py`/`designed_family_predictor.py` train on `DrawId < sid` — call with the *next unplayed* series id, not the one just ingested (e.g. after ingesting 3255, predict with `3256`).
- **Hot-number (L10/L20) chasing is anti-signal** on recent regime (p=0.022). Use recurrence-ranking, not raw frequency.
- **L30-X*Lk is dead** for single-ticket: OOS gradient was zero. `hybrid_family_predictor.py` still uses **L30 - 1.5*L5** on its own scale for System A S5 injection only.
- **Pair-regime weights** are diagnostic, not predictive (p=0.64 rolling). Useful for identifying structurally misaligned sets.
- **ML deprecated 2026-05-10**: LightGBM stage used saturated `any_mat` features; edge came entirely from downstream rule-based stages.
- **IID confirmed**: 40+ tests, 15,286 events (as of 3255). No deterministic signal. All edges are probabilistic and small.
- **E1 is the only event with intermediate prizes**: 10/11/12/13 hits pay only on E1 (KINO main parimutuel). E2–E7 pay nothing below 14/14. All analysis targets E1 exclusively.
