# V26 & V25 Geometric Coverage Framework

*Status: CURRENT PRODUCTION (updated 2026-04-14)*

## Architecture

### A. Short-Window Bayesian Sentinel (HDM)
Tracks number frequencies over the **last 15 series only** (not all history). Uses 95% HDI.
- **State A (no drift)**: Pure geometric generation — uniform random scoring, overlap ≤ 9.
- **State B (drift detected)**: Bayesian-weighted scoring, V26 relaxes overlap (9 → 10 or 11).

Note: V25 generates ~103 sets (constrained by overlap ≤ 9 ceiling), V26 generates 300.

### B. Coverage Selector v2 (replaces Centroid & Smart Observer)
`ensemble_centroid_tool.py` — multi-seed pool generation + coverage-first selection.

**v2 Changes (2026-04-14):**
- Multi-seed generation: 3 seeds x 2 engines = ~1200 unique sets (was 600)
- Pool analysis report: retrospective ceiling and top candidates vs last series
- Deduplication across seeds

**Selection algorithm:** Greedy coverage-first, then diversity (min pairwise overlap).

**Why coverage-first is optimal:** 7 alternative strategies were backtested on 10 series (3204-3213):
| Strategy | avg | 12+ |
|----------|-----|-----|
| Coverage (current) | 10.70 | 0 |
| Quality-first (Bayesian rank) | 10.50 | 0 |
| Cluster (center + 1-edit neighbors) | 10.30-10.50 | 0 |
| Hybrid (quality + coverage weighted) | 10.60 | 0 |
| Larger pool (1800/3000 sets) | 10.70 | 0 |

**Mathematical constraint:** P(12+ from 8 sets x 7 events) = 6.3% per series on IID data, independent of which 8 sets are chosen. No selector strategy can change this — only the number of sets can.

**Pool ceiling:** Always 12/14 (600 sets), rises to 13/14 in 50% of series with 3000 sets. The gap between ceiling (12-13) and selection (10-11) is structural: 8 picks from 1200 = 0.7% sampling rate.

### Removed Components
- **Smart Observer** (`smart_observer_tool.py`): Deleted. Backtested at random baseline (avg 9.60 vs random 9.35). Cannot pick winning grid in advance on IID data.
- **Centroid averager**: Replaced. Averaging 600 grids destroys variance, converges to baseline ~7.84.

### Corrections Applied (2026-04-12, Claude Code)
| Issue | Gemini's Version | Corrected |
|-------|-----------------|-----------|
| Sentinel window | All 1100+ series (never fired) | Last 15 series (fires correctly) |
| HDI threshold | 99.9% (impossible to breach) | 95% (responsive to short window) |
| Smart Observer signals | Fixed-decay frequency (stale) | Removed entirely — doesn't beat random |
| Selection method | Centroid (averages out variance) | Coverage selector v2 (multi-seed, coverage-first) |
