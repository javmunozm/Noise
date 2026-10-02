# Historical Discoveries & Retired Systems

Over 34 sessions and 190+ independent mathematical approaches, the system evolved from pure coverage to ML-based single-ticket prediction, then ML was deprecated when audited as a load-bearing accident.

## Current Architecture (session 34, 2026-05-10)

Two parallel systems run for each prediction:
1. **Designed 8-Family v2** — 8-set coverage system with provable 9+/12+ floors
2. **Deterministic signal-blend pipeline** (`ml_models/signal_predictor.py`) — 7-stage single-ticket system. Stage 1 is a fixed linear blend (signal 0.40 + pool 0.55 + L100 0.05) of validated signals; Stages 2-7 are rule-based (bias + signal reweight + refiner + pool + polisher + auditor). Signal formula: `L30 - 1.0*L10`. OOS avg **8.250** in-sample / **~+0.10 walk-forward** on n=32 [3193..3224].

**Critical lesson (session 34)**: ML appeared to provide +0.191 edge but the audit (2026-05-10) found this was structural compensation, not signal:
- 8 of 14 LightGBM features were saturated on `any_mat` (every number appears in some event of every draw -> FreqLast10=10, Recency=1, MeanGap=1.05 for every number). Model output was near-uniform.
- The edge came entirely from rule-based downstream stages (hand-curated bias + overdue refiner) compensating for the broken model.
- Fixing the features made OOS drop to 7.438 — *below random* — confirming ML contributed negative value once features carried real signal.
- Replacing Stage 1 with a deterministic blend of the validated signals lifted OOS to 8.125. A signal-formula revision later the same day (L30-1.5*L5 -> L30-1.0*L10) lifted it again to 8.250. The lesson: when adding ML on top of validated rules, verify the model is actually using informative features before claiming an ML edge.

**Previous lesson (session 33)** — "calibrate around model's 30% failure cases" — was *correct* given a broken model, but the right move was to fix the model, not patch downstream. The 30% failure cases existed because the model was making essentially-random rankings.

**ml_models/ cleanup (session 33)**: 51 dead files removed (all `deep_accuracy_engine_*.py`, all `experiment_*.py`, backtests, legacy tools). 9 core files remain.

## Crucial Lessons Evaluated & Closed
- **Data is IID Random**: Verified via Information Geometry, Copula dependency maps, and 40+ structural/spectral tests. It contains ZERO latent spatial dependencies.
- **Maximum Minimum-Distance Boundary**: Generating grids at `max_overlap <= 8` violently fragments the lattice spacing and physically restricts output generation below 8 tickets. The mathematically optimal ceiling for disjoint 8-ticket pacing is exactly `overlap <= 9`.
- **The "Legal Filter" Error**: Legacy filters (`is_legal_v2`) artificially rejected 22.7% of perfectly normal draws while severely handicapping predictive nets.

## Key Retired Systems (DO NOT RESURRECT)

### V12.3 Hybrid Recency (Meta-Overfitted)
- Scored 7.5% on walk-forward but **0/13 on Out-Of-Sample (OOS)**.
- Re-evaluation found the 13+ catches were "cascade artifacts" derived from NumPy index ordering, not mathematical signal generalization.

### V12.2 Cascade System
- Originally dubbed "Best Production Engine" but retired. Survives currently ONLY as a legacy scoring library reused by other models.

### V22 Pure Boundary Geometry
- Rejected. Positioned combinations on the filtered boundary theoretically optimizing coverage limit, neglecting uniform dispersion. Scored poorly (0@13+).

## Failed Selector Strategies (2026-04-14)
7 alternative selectors backtested on 10 series (3204-3213), all selecting 8 from 600-3000 sets:
- **Quality-first** (Bayesian posterior rank): avg 10.50 — WORSE than coverage
- **Cluster** (1 center + 7 one-edit neighbors): avg 10.30-10.50 — WORST
- **Multi-cluster** (2-4 centers + neighbors): avg 10.30-10.40 — WORST
- **Hybrid** (weighted quality + coverage): avg 10.60 — TIE
- **Larger pool** (1800-3000 sets): avg 10.70 — NO improvement over 600
- **Coverage-first** (current): avg 10.70 — BEST

Root cause: P(12+ from 8x7=56 set-event pairs) = 6.3% on IID, independent of selector.

## Failed Alternative Frameworks
Tested comprehensively and failed to beat the random threshold (6.3%):
- Optimal Transport, Ising Models, Koopman Operator
- Integer Morphological Processing, SVD/Spectral (V14)
- Beam Search (V13), Syndicate 100-sets (V16)
- MaxEnt + Determinantal Point Processes
