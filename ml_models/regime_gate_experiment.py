"""
Experimental variant of recurrence_predictor.py: adds per-number regime-change
gating on top of the existing half-life weighting.

MOTIVATION (session 2026-07-20): number 6 ran hot at 80.6% in OOS [3193..~3224]
then cooled to 65.6% in [~3224..3255] (lifetime rate 56.49%, theoretical 56.0%).
The half-life=26 weighting still gives a draw 52 series back 25% weight, so it
kept over-betting on 6/8/10/16 for weeks after their streaks broke -- the engine
changes far slower than the underlying (IID) process actually does.

GATE: for each number n, compare its hit rate in the last SHORT draws vs the
hit rate in the SHORT..LONG window before that. If short/long ratio < 1 (the
number has cooled relative to its own recent-past baseline), multiply n's
contribution to every combo wcount by that ratio (floored at MIN_GATE) before
Stage 1-7 aggregation runs. A number that just went cold gets discounted on
the very next draw, not 20+ draws later.

This does NOT touch recurrence_predictor.py or production. Standalone OOS
harness only -- must beat the existing fixed-half-life engine out-of-sample
before any production change is considered (Bonferroni-aware, per CLAUDE.md).

Usage:
  python ml_models/regime_gate_experiment.py --oos [--from 3193] [--short 10] [--long 30] [--floor 0.5]
"""
from __future__ import annotations

import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml_models"))

import pyodbc
from recurrence_predictor import (
    CONN_STR, POOL, DRAW_SIZE, MAX_K, DEFAULT_HALF_LIFE,
    _fetch_e1, _fetch_db_combos, _compute_weights, _comb,
    stage0, stage8, stage9, stage10,
)

IID = 7.840


def _regime_gate(draws: list[list[int]], short: int, long: int, floor: float) -> dict[int, float]:
    """Per-number gate in (floor, 1.0]: short-window hit rate / prior-window hit rate.

    short window  = last `short` draws.
    prior window  = the `long` draws immediately before the short window.
    gate=1.0 when insufficient history (no penalty), or when the number is
    stable/heating (ratio >= 1, clipped to 1.0 -- we only discount cooling,
    never boost heating; boosting would reintroduce hot-hand chasing).
    """
    n = len(draws)
    gate = {num: 1.0 for num in POOL}
    if n < short + long:
        return gate

    short_draws = draws[n - short:]
    prior_draws = draws[n - short - long: n - short]

    for num in POOL:
        short_rate = sum(1 for d in short_draws if num in d) / short
        prior_rate = sum(1 for d in prior_draws if num in d) / long
        if prior_rate <= 0:
            continue
        ratio = short_rate / prior_rate
        if ratio < 1.0:
            gate[num] = max(floor, ratio)
        # ratio >= 1.0 (heating or stable): leave gate at 1.0, do not boost.
    return gate


def _weighted_gated_stage_k(
    k: int,
    draws: list[list[int]],
    weights: list[float],
    gate: dict[int, float],
) -> list[tuple[tuple[int, ...], float, int]]:
    """Same as recurrence_predictor.stage_k's ranking build, but each combo's
    weighted count is additionally scaled by the product of its numbers' gates
    (geometric-mean normalized so k=1..7 stay on comparable scales)."""
    wcount: dict[tuple[int, ...], float] = {}
    last_idx: dict[tuple[int, ...], int] = {}

    for i, (draw, w) in enumerate(zip(draws, weights)):
        for combo in combinations(sorted(draw), k):
            g = 1.0
            for num in combo:
                g *= gate.get(num, 1.0)
            g = g ** (1.0 / k)  # geometric mean so higher k isn't over-penalized
            wcount[combo] = wcount.get(combo, 0.0) + w * g
            last_idx[combo] = i

    ranking = sorted(wcount.items(), key=lambda x: (-x[1], -last_idx[x[0]]))
    return [(c, w, last_idx[c]) for c, w in ranking]


def run_gated_pipeline(
    sid: int,
    draws_train: list[list[int]],
    half_life: float,
    short: int,
    long: int,
    floor: float,
    db_combos: set,
) -> list[int]:
    N = len(draws_train)
    weights = _compute_weights(N, half_life)

    r0 = stage0(draws_train, weights)
    if not r0.ready_for_next:
        raise RuntimeError(f"Stage 0 FAIL for sid={sid}")

    gate = _regime_gate(draws_train, short=short, long=long, floor=floor)

    rankings = {}
    for kk in range(1, MAX_K + 1):
        rankings[kk] = _weighted_gated_stage_k(kk, draws_train, weights, gate)

    _, influence = stage8(rankings)
    _, G1, G2 = stage9(rankings[7], draws_train, weights, influence)
    _, final14 = stage10(G1, G2, influence, [], rankings[7], draws_train, weights, db_combos)
    return final14


def oos_eval(oos_lo: int, half_life: float, short: int, long: int, floor: float) -> None:
    conn = pyodbc.connect(CONN_STR, readonly=True)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT DrawId FROM dbo.Draws WHERE EventIndex=1 ORDER BY DrawId ASC")
    all_sids = [int(r[0]) for r in cur.fetchall()]
    conn.close()

    targets = [s for s in all_sids if s >= oos_lo]
    print(f"OOS targets: {len(targets)} series ({targets[0]}..{targets[-1]})  "
          f"gate: short={short} long={long} floor={floor}")
    print()

    sid_order_full, draws_full = _fetch_e1()
    sid_to_idx = {s: i for i, s in enumerate(sid_order_full)}

    scores = []
    hits12 = hits11 = 0

    for k, sid in enumerate(targets):
        idx = sid_to_idx[sid]
        draws_train = draws_full[:idx]
        if not draws_train:
            continue
        db_combos = _fetch_db_combos(up_to_sid=sid)
        final14 = run_gated_pipeline(sid, draws_train, half_life, short, long, floor, db_combos)

        actual = set(draws_full[idx])
        h = len(set(final14) & actual)
        scores.append(h)
        if h >= 12: hits12 += 1
        if h >= 11: hits11 += 1
        print(f"  [{k+1:3d}/{len(targets)}] sid={sid}  hits={h}/14  ticket={final14}", flush=True)

    if not scores:
        print("No results.")
        return

    n = len(scores)
    avg = sum(scores) / n
    last10 = sum(scores[-10:]) / min(10, n)
    last5 = sum(scores[-5:]) / min(5, n)
    print()
    print(f"{'='*60}")
    print(f"OOS E1 results (regime-gated recurrence)  n={n}  ({targets[0]}..{targets[-1]})")
    print(f"  avg    = {avg:.3f}  (IID baseline {IID})")
    print(f"  max    = {max(scores)}")
    print(f"  11+    = {hits11}/{n}  ({100*hits11/n:.1f}%)")
    print(f"  12+    = {hits12}/{n}  ({100*hits12/n:.1f}%)")
    print(f"  edge   = {avg - IID:+.3f} vs random")
    print(f"  last10 = {last10:.3f}")
    print(f"  last5  = {last5:.3f}")
    print(f"{'='*60}")


def main():
    args = sys.argv[1:]
    if "--oos" not in args:
        print("Usage: python ml_models/regime_gate_experiment.py --oos [--from 3193] "
              "[--hl 26] [--short 10] [--long 30] [--floor 0.5]")
        sys.exit(1)

    lo = int(args[args.index("--from") + 1]) if "--from" in args else 3193
    hl = float(args[args.index("--hl") + 1]) if "--hl" in args else DEFAULT_HALF_LIFE
    short = int(args[args.index("--short") + 1]) if "--short" in args else 10
    long_ = int(args[args.index("--long") + 1]) if "--long" in args else 30
    floor = float(args[args.index("--floor") + 1]) if "--floor" in args else 0.5

    oos_eval(lo, hl, short, long_, floor)


if __name__ == "__main__":
    main()
