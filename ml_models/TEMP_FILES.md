# Experimental / Temporary Files

Files created during active development that require cleanup or promotion decision after validation.

## Active (kept until further OOS data confirms)

| File | Purpose | Decision after |
|------|---------|----------------|
| `signal_predictor.py` | Single-stage deterministic single-ticket pipeline. Signal = delta-EWMA(0.30, 0.08). OOS avg 8.216 (n=37). Bias stage removed 2026-05-21. | Permanent production — no further changes needed unless OOS degrades below 8.0. |
| `bias_audit.py` | 5-part CV audit tool for bias stage. Proved bias hurts (−0.24/draw CV). Keep as diagnostic. | Permanent diagnostic tool. |

## History

- 2026-05-21: Bias stage removed from `signal_predictor.py`. 5-part CV audit showed cosine −0.28 between half-bias vectors, sign agreement 8/25, CV delta −0.24/draw. `signal_bias.json` is now unused (kept for reference). Do NOT refit or reintroduce bias.
- 2026-05-16: Signal formula revised from L30-1.0*L10 → delta-EWMA(0.30, 0.08). L30-1.0*L10 had zero OOS gradient (top-7 rank hit rate = random). Walk-forward: 8.412 vs 7.647.
- 2026-05-11: Pipeline reduced from 7 stages to 2 (signal + bias only). Refiner/Polisher/Auditor all fired 0/33 or hurt.
- 2026-05-10: LightGBM deprecated. Replaced with deterministic blend. OOS 8.031 → 8.125 → 8.250 same day. Renamed `fasttree_predictor.py` → `signal_predictor.py`.
