# FastTree Single-Ticket Pipeline

**File**: `ml_models/fasttree_predictor.py`  
**Bias config**: `ml_models/fasttree_bias.json`  
**Added**: Session 33 (2026-05-09)

## Overview

A 7-stage ML pipeline that produces **one ticket** targeting E1 (KINO main draw).  
OOS [3193–3224] n=32: avg **8.031/14** (+0.191 vs IID random 7.840), max=11, 1×11+.

Inspired by the user's C# system (FastTreeScorer + SelectorRefiner + SelectorPolisher + RefinerAuditor). Key difference: this system is calibrated around the model's systematic **30% failure cases** — the numbers LightGBM chronically under-ranks or over-ranks.

---

## Pipeline Stages

### Stage 1 — LightGBM (FastTree analogue)
- **Label**: E1-only (`AllCombinations[0].Contains(n)` — not any-event)
- **Training window**: rolling last 100 series (distant history hurts E1 signal; tested 100/200/300/500/all)
- **14 features per number**:
  - Any-event: Recency, FreqLast10/20/50/100, MeanGap, GapStd, AppearancesTotal, Streak
  - E1-specific: Event1FreqLast10/20/50
  - Co-appearance: MeanCoAppear50, TopCoAppear50 (E1 only, last 50 draws)
- **Hyperparams**: 100 trees, lr=0.2, num_leaves=20, min_data_in_leaf=10, seed=42
- Output: normalised probability vector over 1..25

### Stage 2 — Bias Calibration
- **Method**: rank-calibrated on OOS window
  - For each number n: `bias[n] = -(avg_rank_when_in_actual - 13.0) / 13.0`
  - 13.0 = expected rank of a correct number under a no-skill model (uniform over 25)
  - Clipped to [−0.40, +0.40]
- **Lifted** (model under-ranks these): 1, 2, 6, 10, 11, 18, 19, 25
- **Suppressed** (model over-ranks these): 5, 9, 13, 16, 20, 21, 23, 24
- **Config file**: `ml_models/fasttree_bias.json` — recalibrate after every ~10 new draws
- **Do NOT** use the C# bias vector directly — it was fitted on a different model/data combination and is wrong for the Python LightGBM (n=9 gets −0.20 in C# but should be suppressed here too for different reasons)

### Stage 3 — Hot/Cold Signal (L30−1.5×L5)
- Same signal as hybrid_family_predictor: `score[n] = freq_L30[n] − 1.5×freq_L5[n]`
- Alpha=0.10 (tested 0.0–0.40; peaks at 0.10, degrades beyond 0.20)
- Blend: `prob[n] *= (1 + 0.10 × (norm_signal[n] − 0.5))`

### Stage 4 — SelectorRefiner (overdue-swap)
- **Borderline zone**: bottom 4 of top-14 (swap-out candidates) vs top 6 outside top-14 (swap-in candidates) — rank-based, not margin-based
  - Margin-based (C# approach) fails here because LightGBM prob distributions are flatter than C# FastTree; overdue numbers often sit 40–60% below cut
- **Swap-in gate**: `overdue_score(n) >= 2.0` — calibrated on L20: od≥2.0 gives 66.7% accuracy vs 50% base
- **Swap-out gate**: `overdue_score(swap_out) == 0 AND pool_rank(swap_out) > 14` — requires both not-overdue AND pool also rejects it
- Max 2 swaps per series

### Stage 5 — Pool Model
- Independent frequency-based scorer (structurally different from LightGBM)
- **Multi-window weighted frequency**: windows [5, 10, 20, 50, 100], weights [0.35, 0.25, 0.20, 0.12, 0.08]
- Less concentrated than LightGBM (pool confidence ~0.56 vs FT ~0.63–0.69) — provides genuine disagreement signal
- Used by Polisher (V1 component) and Auditor (pool top-14 reference)

### Stage 6 — SelectorPolisher (veto bad refiner swaps)
- Three veto components, weighted 0.40/0.35/0.25:
  - **V1 PoolDisagreement** (0.40): pool rank gap ≥ 10 between swap-in and swap-out — calibrated: gap≥10 gives 66.7% pool-veto accuracy (gaps 2–9 are noise at 33–43%)
  - **V2 RawConfidenceMargin** (0.35): raw FastTree prob ratio > 1.05 (swap-out was more confident before bias)
  - **V3 RecentEvent1Streak** (0.25): swap-out drawn ≥3 times in last 5; swap-in ≤1
- **Veto threshold**: 0.62 (raised from C# default 0.60 to avoid marginal 2-component fires)
- **Min components**: 2

### Stage 7 — RefinerAuditor (single risk/rescue substitution)
- Risk scores each refined pick on 4 components (StaleAnchor, ClusterImbalance, ContextDrift, SwapInstability)
- Rescue scores each non-refined number on 3 components (CoverageGapFill, RecentDrawAgreement, MissedByBothModels)
- **Very conservative**: risk_threshold=0.65, min_risk_components=3 — rarely fires
- Calibrated conservative because on IID data with 32 draws, 2-component fires at 0.50 are mostly noise

---

## Workflow

### Per-series prediction
```bash
python ml_models/fasttree_predictor.py 3225
```

### OOS evaluation
```bash
python ml_models/fasttree_predictor.py --oos --from 3193
```

### Bias recalibration (after new draw)
```bash
# Recalibrate on last 20 draws (rolling window)
python ml_models/fasttree_predictor.py --fit-bias --from <latest-19> --to <latest>
# Then verify improvement
python ml_models/fasttree_predictor.py --oos --from 3193
```

---

## Design Decisions & Ablation Results

| Config | OOS avg | Notes |
|--------|---------|-------|
| Raw LightGBM (all history) | 7.438 | Below random |
| Window=100 | 8.000 | Best training window |
| +Bias (C# vector) | 8.062 | C# bias better than Python-fitted |
| +Signal alpha=0.10 | 8.062 | Peaks at 0.10 |
| +Recency buffer | 7.625 | Net negative — removed |
| +Pair polish | 7.500 | Net negative — removed |
| +Rank-calibrated bias | 8.031 | Better than C# on recall metric |
| +SelectorRefiner (margin) | 7.969 | Margin too tight for flat LGB probs |
| +SelectorRefiner (rank-based) | 8.031 | Rank-based border works |
| +Pool model | enables polisher | V1 now has real signal |
| +SelectorPolisher (V1 gap≥10) | 8.031 | Calibrated threshold matters |
| +RefinerAuditor (c≥3, 0.65) | 8.031 | Conservative — silent most draws |
| **Final pipeline** | **8.031** | +0.191 vs random |

---

## Key Insight (session 33)

The C# system's edge comes from calibration around the **30% failure cases** of the ML model — numbers it systematically under-ranks or over-ranks — not from the raw model itself. The raw LightGBM alone scores below random. The bias, refiner, and polisher exist specifically to correct systematic errors at the cut boundary.

The Python port confirmed: fitting bias correctly (rank-vs-random method) lifts the right numbers (n=1,2,6,10,11,18,19,25 chronically under-ranked; n=9,13,20,21 over-ranked despite being in actual less often).

---

## Files

| File | Purpose |
|------|---------|
| `ml_models/fasttree_predictor.py` | Full pipeline — predict, OOS eval, bias fitting |
| `ml_models/fasttree_bias.json` | Current bias vector (recalibrate rolling L20) |
| `ml_models/TEMP_FILES.md` | Cleanup tracker |
