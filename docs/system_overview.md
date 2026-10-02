# System Overview

**Goal**: Predict lottery draws where each event contains 14 numbers chosen from 1-25. **Primary target is E1 (KINO main draw)** — the only event that pays lower prize tiers (12/13 matches). E2–E7 only pay on a 14/14 exact match.

**Current draw: 3255** (2026-07-19, ingested). **Predicting for: 3256.**

**Four active systems** (as of 2026-07-20, n=63 OOS series 3193–3255):
- **System A** — Designed 8-Family v2: 8 static sets with provable coverage guarantees. Best-of-8 avg vs E1 ≈ 9.71 OOS. Latest (3255): 10/14.
- **System B** — `ml_models/recurrence_predictor.py`: 1 ticket from 11-stage recency-weighted k-combo pipeline. OOS avg **8.032** (+0.192 vs IID) at n=63; edge decaying continuously (8.326 at n=46 → 8.192 at n=52 → 8.048 at n=62 → 8.032 at n=63). **Recent-segment degradation confirmed**: last-20=7.60, last-10=7.10, last-5=7.00 — all below IID (7.84), worsening the more recent the window. Root cause traced (2026-07-20): the half-life=26 weighting locked onto number 6, which ran hot at 80.6% in OOS [3193..~3224] (lifetime rate 56.49%, at theoretical) then cooled to 65.6% in [~3224..3255]; the weighting is too slow to unwind a broken streak (draw 52 series back still carries 25% weight). A standalone "regime gate" variant (`ml_models/regime_gate_experiment.py`) that discounts numbers cooling relative to their own recent-past baseline was OOS-tested and came back a wash (avg 8.032 identical to 4dp, last-5 only 7.0→7.2) — confirms there was no real regime to detect faster, just noise on noise (see [[recurrence_predictor]] memory). Latest (3255): 7/14. Supersedes signal_predictor.py (session 36, 2026-05-30).
- **System B2** — `ml_models/force_evaluator.py`: 1 ticket from beam-search cascade (hl=20, bw=100, hybrid). OOS avg **8.016** (+0.176 vs IID) at n=63; same decaying pattern as System B (shared mechanism — both are recency-weighted frequency counting). Latest (3255): 7/14.
- **Note**: B, B2 and C are statistically indistinguishable from each other and from IID — none is "primary." Rankings churn draw to draw (3253: B=B2 tied; 3255: B=B2(7) tied, C(5) weakest). Report all three with scores; recommend none over the others (revised 2026-06-25).
- **System C** — BsaDb EWMA swapper: 1 ticket from delta-EWMA top-14 stored in BsaDb. Currently underperforming IID historically (live OOS avg 7.385 as of n=26/3228-3254). Latest (3255): 5/14, weakest of the three this cycle.

## Statistical Reality & Constraints
- **Data is IID random**: Confirmed by 40+ rigorous statistical tests. No global structural bias exists.
- **E1 is statistically identical to E2–E7**: chi²=13.78 (df=24, p>>0.05). No per-game number bias.
- **No exploitable single-ticket signal confirmed** (revised 2026-06-25, reconfirmed 2026-07-20): recurrence-ranking (session 36) peaked at OOS avg 8.326 (n=46) but has been decaying continuously since (8.192 at n=52 → 8.048 at n=62 → 8.032 at n=63), fails significance pre-Bonferroni (edge·√n z-proxy fell from ≈3.2σ at n=46 to ≈1.5σ at n=63), and rankings keep churning between the three single-ticket engines draw to draw. **Mechanism traced 2026-07-20**: the decay is a real, understood effect — a genuinely hot number (6: 80.6% in first half of OOS window) reverting to its true 56% base rate while the half-life weighting lags behind. A regime-detection fix was tried and tested null (see System B note above). Treat all single-ticket edges as noise until proven otherwise on more data. Recurrence still supersedes the retired delta-EWMA pipeline.
- **Do NOT reintroduce EWMA/momentum/hot-hand/bias** into recurrence pipeline: hot-hand proven anti-signal (−0.39 hits/draw OOS); bias proven overfit (cosine −0.28 between halves, removed session 34). Both permanently retired. **Do not re-attempt adaptive/regime-aware reweighting on top of frequency counting** (tested 2026-07-20, null result) without new evidence the process is non-IID.
- **L30-1.5*L5 is used only by hybrid_family_predictor.py** for System A S5 injection — not the single-ticket pipeline.
- **Hot-number following is anti-signal**: L10/L20 raw frequency chasing underperforms random (p=0.022). Do not chase counts directly.
- **Pair-regime weights are diagnostic, not predictive**: C(14,2)=91 pares por set, C(25,2)=300 pares posibles. `peso = tasa_L20 / P_IID`. El peso de par no predice la próxima serie (p=0.64 en test rolling), pero sí identifica sets estructuralmente desalineados con el régimen actual. Swap de pares FROZEN mejora OOS avg +0.129 en n=31.
- **Cross-event pairs (E1 vs Ej)**: regime shift visible en L20/L100 pero no predictivo (chi2 p=0.64). Útil solo como diagnóstico.
- **Date dimensions are null**: Day-of-month (min p=0.96), month (p=0.71), year (p=0.72), day-of-week (p=0.23) — all non-significant after Bonferroni correction across 2,421 draws. Date adds qualitative convergence (triangulating which numbers multiple signals agree on) but no standalone predictive edge. DOW-conditioned signal shows promising OOS (8.35 vs 7.65 random on n=17 draws) but p=0.44 vs pure signal — small-sample variance.
- **Random Baseline**: ~6.3% probability of hitting 12/14 on E1 across 8 sets over the full C(25,14) pool.
- **Event Uniformity**: Events are uniformly drawn from all 4.46 million combinations.
- **Filter Removal**: Previous "smart" filters (e.g., `is_legal_v2`) arbitrarily excluded 22.7% of valid events and were removed. The pool is now the full 4.46M combinations.

## Data Format
Data is structured as JSON files containing series. Each series has 7 events (production) or 6-8 events (historical). Each event is a sorted array of 14 numbers (1-25).
```json
{
  "3205": [
    [3, 5, 7, 8, 9, 11, 14, 15, 17, 18, 20, 21, 22, 24],  // Event 1
    // ... up to 7 events
  ]
}
```
