"""
Recurrence-Ranking Predictor -- 14 of 25 (E1 single ticket).

Multi-stage frequency-analysis pipeline over historical E1 draws:
  Stage 0  -- Ingestion & integrity
  Stage 1  -- Weighted singles ranking
  ...
  Stage 7  -- Weighted 7-combinations ranking
  Stage 8  -- Cross-level consolidation
  Stage 9  -- Two complementary groups of 7
  Stage 10 -- Final 14 + no-repeat enforcement

All frequency counts are recency-weighted (half-life decay).
  HALF_LIFE = 26 draws (default)
  weight(t) = 0.5 ^ ((N - t) / HALF_LIFE)  (t=1 oldest, t=N newest)

Usage:
  python ml_models/recurrence_predictor.py <series_id>
  python ml_models/recurrence_predictor.py --oos [--from 3193]
"""
from __future__ import annotations

import json
import sys
import time
from itertools import combinations
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pyodbc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml_models"))

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DESKTOP-QR14EDK\\SQLEXPRESS01;"
    "Database=LuckyDb;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

POOL = list(range(1, 26))
DRAW_SIZE = 14
MAX_K = 7
DEFAULT_HALF_LIFE = 26

LEDGER_PATH  = ROOT / "ml_models" / "recurrence_ledger.json"
OOS_CACHE    = ROOT / "ml_models" / "oos_cache.json"


# -------------------------------------------------------------
# Ledger (no-repeat enforcement)
# -------------------------------------------------------------

def _load_ledger() -> list[frozenset]:
    if LEDGER_PATH.exists():
        raw = json.loads(LEDGER_PATH.read_text())
        return [frozenset(entry) for entry in raw]
    return []


def _save_ledger(ledger: list[frozenset]) -> None:
    LEDGER_PATH.write_text(
        json.dumps([sorted(s) for s in ledger], indent=2)
    )


# -------------------------------------------------------------
# DB fetch -- E1 draws only + unique validator
# -------------------------------------------------------------

def _fetch_db_combos(up_to_sid: int | None = None) -> set[frozenset]:
    """Return all 14-number drawn sets from dbo.Draws (all events) with DrawId < up_to_sid.

    Used as the unique validator: any generated ticket matching one of these
    was already drawn in the DB and must not be emitted again.
    Collision probability is ~1/1832 per ticket; the guard is free and correct.
    """
    conn = pyodbc.connect(CONN_STR, readonly=True)
    cur = conn.cursor()
    if up_to_sid is not None:
        cur.execute(
            "SELECT N01,N02,N03,N04,N05,N06,N07,N08,N09,N10,N11,N12,N13,N14 "
            "FROM dbo.Draws WHERE DrawId < ?",
            up_to_sid,
        )
    else:
        cur.execute(
            "SELECT N01,N02,N03,N04,N05,N06,N07,N08,N09,N10,N11,N12,N13,N14 "
            "FROM dbo.Draws"
        )
    rows = cur.fetchall()
    conn.close()
    return {frozenset(int(x) for x in r) for r in rows}


def _fetch_db_combos_by_sid() -> list[tuple[int, frozenset]]:
    """Return (DrawId, combo) for every event row in dbo.Draws, ordered by DrawId.

    Single full-table fetch; callers building an OOS loop should use this once
    and grow a running set incrementally instead of re-querying per iteration.
    """
    conn = pyodbc.connect(CONN_STR, readonly=True)
    cur = conn.cursor()
    cur.execute(
        "SELECT DrawId, N01,N02,N03,N04,N05,N06,N07,N08,N09,N10,N11,N12,N13,N14 "
        "FROM dbo.Draws ORDER BY DrawId ASC"
    )
    rows = cur.fetchall()
    conn.close()
    return [(int(r[0]), frozenset(int(x) for x in r[1:15])) for r in rows]


def _fetch_e1(up_to_sid: int | None = None) -> tuple[list[int], list[list[int]]]:
    """Return (sid_order, draws) for E1, oldest first.

    If up_to_sid is given, only rows with DrawId < up_to_sid are included
    (strict: we predict *for* up_to_sid so it must not be in training).
    """
    conn = pyodbc.connect(CONN_STR, readonly=True)
    cur = conn.cursor()
    cur.execute(
        "SELECT DrawId, N01,N02,N03,N04,N05,N06,N07,N08,N09,N10,N11,N12,N13,N14 "
        "FROM dbo.Draws WHERE EventIndex=1 ORDER BY DrawId ASC"
    )
    rows = cur.fetchall()
    conn.close()

    sid_order, draws = [], []
    for r in rows:
        sid = int(r[0])
        if up_to_sid is not None and sid >= up_to_sid:
            continue
        nums = [int(x) for x in r[1:15]]
        sid_order.append(sid)
        draws.append(nums)
    return sid_order, draws


# -------------------------------------------------------------
# Weights
# -------------------------------------------------------------

def _compute_weights(N: int, half_life: float) -> list[float]:
    """weight(t) = 0.5^((N-t)/half_life), t=1..N (oldest=1, newest=N)."""
    return [0.5 ** ((N - t) / half_life) for t in range(1, N + 1)]


# -------------------------------------------------------------
# Stage data structures
# -------------------------------------------------------------

class StageResult(NamedTuple):
    stage: int
    name: str
    status: str          # "PASS" | "FAIL"
    key_metrics: dict
    validation_results: list[str]
    ready_for_next: bool


def _checkpoint(result: StageResult) -> None:
    print(f"\n{'-'*64}")
    print(f"Stage {result.stage}/10 -- {result.name} -- STATUS: {result.status}")
    for msg in result.validation_results:
        print(f"  {msg}")
    print(f"  ready_for_next: {result.ready_for_next}")
    print(f"{'-'*64}\n")
    if not result.ready_for_next:
        print(f"HALT: stage {result.stage} FAILED. Fix above before continuing.")
        sys.exit(1)


# -------------------------------------------------------------
# Stage 0 -- Ingestion & integrity
# -------------------------------------------------------------

def stage0(draws: list[list[int]], weights: list[float]) -> StageResult:
    checks = []
    ok = True

    # Each draw exactly 14 distinct numbers in 1..25
    bad = []
    for i, d in enumerate(draws):
        if len(d) != DRAW_SIZE or len(set(d)) != DRAW_SIZE:
            bad.append(i)
        if any(n < 1 or n > 25 for n in d):
            bad.append(i)
    if bad:
        checks.append(f"FAIL: {len(bad)} draws have wrong size or out-of-range numbers: {bad[:5]}")
        ok = False
    else:
        checks.append(f"PASS: all {len(draws)} draws have exactly 14 distinct numbers in 1..25")

    # Weights length matches
    if len(weights) != len(draws):
        checks.append(f"FAIL: weights length {len(weights)} != draws length {len(draws)}")
        ok = False
    else:
        checks.append(f"PASS: weights length matches ({len(weights)})")
        checks.append(f"  weight range: [{min(weights):.6f}, {max(weights):.6f}]")

    checks.append(f"  total draws: {len(draws)}")

    status = "PASS" if ok else "FAIL"
    return StageResult(0, "Ingestion & integrity", status,
                       {"n_draws": len(draws)}, checks, ok)


# -------------------------------------------------------------
# Stages 1..7 -- Weighted recurrence ranking by combo size k
# -------------------------------------------------------------

def stage_k(
    k: int,
    draws: list[list[int]],
    weights: list[float],
    top_n: int = 25,
    verbose: bool = True,
) -> tuple[StageResult, list[tuple[tuple[int, ...], float, int]]]:
    """Return (StageResult, ranking) where ranking is list of (combo, wcount, last_idx)."""
    name = f"Weighted k={k} recurrence ranking"

    # Build weighted counts and last-appearance index
    wcount: dict[tuple[int, ...], float] = {}
    last_idx: dict[tuple[int, ...], int] = {}

    N = len(draws)
    total_expected = sum(weights[i] * _comb(DRAW_SIZE, k) for i in range(N))

    for i, (draw, w) in enumerate(zip(draws, weights)):
        for combo in combinations(sorted(draw), k):
            wcount[combo] = wcount.get(combo, 0.0) + w
            last_idx[combo] = i

    # Sort: descending wcount, ties by most-recent last_idx
    ranking = sorted(wcount.items(), key=lambda x: (-x[1], -last_idx[x[0]]))
    ranking_full = [(c, w, last_idx[c]) for c, w in ranking]

    # Validation checks
    checks = []
    ok = True

    total_actual = sum(w for _, w, _ in ranking_full)
    if abs(total_actual - total_expected) > 0.01:
        checks.append(f"FAIL: total wcount={total_actual:.4f} != expected {total_expected:.4f}")
        ok = False
    else:
        checks.append(f"PASS: total weighted occurrences {total_actual:.4f} matches expected {total_expected:.4f}")

    # No duplicates
    if len(ranking_full) != len(wcount):
        checks.append("FAIL: duplicate combos in ranking")
        ok = False
    else:
        checks.append(f"PASS: no duplicate combos ({len(ranking_full)} unique)")

    # Numbers in range, combo length correct
    bad = [(c, w) for c, w, _ in ranking_full if len(c) != k or any(n < 1 or n > 25 for n in c)]
    if bad:
        checks.append(f"FAIL: {len(bad)} combos with wrong length or out-of-range: {bad[:3]}")
        ok = False
    else:
        checks.append(f"PASS: all combos length={k}, all numbers in 1..25")

    # Monotonically non-increasing
    prev_w = float("inf")
    mono_ok = True
    for c, w, _ in ranking_full:
        if w > prev_w + 1e-9:
            mono_ok = False
            break
        prev_w = w
    if not mono_ok:
        checks.append("FAIL: ranking not monotonically non-increasing")
        ok = False
    else:
        checks.append("PASS: ranking is monotonically non-increasing")

    if verbose:
        top = ranking_full[:top_n]
        checks.append(f"\n  TOP {min(top_n, len(top))} (of {len(ranking_full)}) k={k} combos:")
        for rank, (c, w, li) in enumerate(top, 1):
            checks.append(f"    {rank:3d}. {list(c)}  wcount={w:.4f}  last_draw_idx={li}")

    status = "PASS" if ok else "FAIL"
    return (
        StageResult(k, name, status,
                    {"k": k, "unique_combos": len(ranking_full),
                     "total_wcount": round(total_actual, 4)},
                    checks, ok),
        ranking_full,
    )


def _comb(n: int, k: int) -> int:
    from math import comb as math_comb
    return math_comb(n, k)


# -------------------------------------------------------------
# Stage 8 -- Cross-level consolidation
# -------------------------------------------------------------

def stage8(
    rankings: dict[int, list[tuple[tuple[int, ...], float, int]]],
) -> tuple[StageResult, dict[int, float]]:
    """Build per-number influence scores from RANKING_1..7."""
    name = "Cross-level consolidation"

    # Score = for each k, rank of number's best k-combo (lower rank = better)
    # Influence score: for each number, sum over k of (1 / best_rank_at_k).
    # This rewards numbers that appear in top combos at every level.
    influence: dict[int, float] = {n: 0.0 for n in POOL}

    # For k=1 use direct rank; for k>1 use best rank among combos containing n
    for k in range(1, MAX_K + 1):
        ranking = rankings[k]
        # Map each combo -> rank (1-indexed)
        combo_rank = {c: r + 1 for r, (c, _, _) in enumerate(ranking)}
        best_rank_for_num: dict[int, int] = {}
        for combo, _, _ in ranking:
            rank = combo_rank[combo]
            for n in combo:
                if n not in best_rank_for_num or rank < best_rank_for_num[n]:
                    best_rank_for_num[n] = rank
        for n in POOL:
            r = best_rank_for_num.get(n)
            if r is not None:
                influence[n] += 1.0 / r

    checks = []
    ok = True

    # Consistency check: top singles should overlap with members of top k-combos
    top14_singles = sorted(POOL, key=lambda n: -(rankings[1][POOL.index(n)][1]
                                                  if POOL.index(n) < len(rankings[1]) else 0))
    # Simpler: rank singles by their direct wcount
    single_wcount = {c[0]: w for c, w, _ in rankings[1]}
    top14_by_single = sorted(POOL, key=lambda n: -single_wcount.get(n, 0))[:14]
    top14_by_influence = sorted(POOL, key=lambda n: -influence[n])[:14]
    overlap = len(set(top14_by_single) & set(top14_by_influence))

    checks.append(f"Top-14 by singles wcount:     {sorted(top14_by_single)}")
    checks.append(f"Top-14 by influence score:    {sorted(top14_by_influence)}")
    checks.append(f"Overlap between the two:      {overlap}/14")
    if overlap < 7:
        checks.append(f"WARNING: low overlap ({overlap}/14) -- contradiction between levels; review data")
    else:
        checks.append(f"PASS: reasonable cross-level consistency ({overlap}/14 overlap)")

    # Show influence table (top 25)
    sorted_influence = sorted(POOL, key=lambda n: -influence[n])
    checks.append(f"\n  Per-number influence scores (all 25 numbers):")
    checks.append(f"  {'N':>3}  {'Influence':>10}  {'SingleWcount':>12}")
    for n in sorted_influence:
        checks.append(f"  {n:>3}  {influence[n]:>10.4f}  {single_wcount.get(n, 0):>12.4f}")

    status = "PASS" if ok else "FAIL"
    return StageResult(8, name, status, {"top14_influence": sorted_influence[:14]},
                       checks, ok), influence


# -------------------------------------------------------------
# Stage 9 -- Two complementary groups of 7
# -------------------------------------------------------------

def stage9(
    rankings7: list[tuple[tuple[int, ...], float, int]],
    draws: list[list[int]],
    weights: list[float],
    influence: dict[int, float],
) -> tuple[StageResult, list[int], list[int]]:
    name = "Two complementary groups of 7"

    # G1: highest-ranked 7-combo from RANKING_7
    g1_combo, g1_w, _ = rankings7[0]
    G1 = list(g1_combo)
    complement = [n for n in POOL if n not in set(G1)]  # 18 numbers

    # G2: highest-ranked 7-combo from complement only (re-rank)
    _, ranking7_complement = stage_k(7, draws, weights, top_n=5, verbose=False)
    # Filter to combos entirely within complement
    g2_combo, g2_w = None, -1.0
    for combo, w, _ in ranking7_complement:
        if all(n in complement for n in combo):
            g2_combo, g2_w = combo, w
            break

    checks = []
    ok = True

    if g2_combo is None:
        # Fallback: build G2 from top-7 by influence among complement
        G2 = sorted(complement, key=lambda n: -influence[n])[:7]
        checks.append("NOTE: no disjoint 7-combo found in RANKING_7; G2 built from top-7 influence among complement")
        g2_w = sum(influence[n] for n in G2)
    else:
        G2 = list(g2_combo)

    # Validation gate 9
    if len(G1) != 7:
        checks.append(f"FAIL: |G1|={len(G1)} != 7"); ok = False
    else:
        checks.append(f"PASS: |G1|=7  G1={sorted(G1)}  wcount={g1_w:.4f}")

    if len(G2) != 7:
        checks.append(f"FAIL: |G2|={len(G2)} != 7"); ok = False
    else:
        checks.append(f"PASS: |G2|=7  G2={sorted(G2)}  score={g2_w:.4f}")

    intersection = set(G1) & set(G2)
    if intersection:
        checks.append(f"FAIL: G1 X G2 = {sorted(intersection)} (must be empty)"); ok = False
    else:
        checks.append(f"PASS: G1 X G2 = empty")

    union = set(G1) | set(G2)
    if len(union) != 14:
        checks.append(f"FAIL: |G1 U G2|={len(union)} != 14"); ok = False
    else:
        checks.append(f"PASS: |G1 U G2|=14")

    status = "PASS" if ok else "FAIL"
    return StageResult(9, name, status, {"G1": sorted(G1), "G2": sorted(G2)},
                       checks, ok), G1, G2


# -------------------------------------------------------------
# Stage 10 -- Final 14 + no-repeat enforcement
# -------------------------------------------------------------

def stage10(
    G1: list[int],
    G2: list[int],
    influence: dict[int, float],
    ledger: list[frozenset],
    rankings7: list[tuple[tuple[int, ...], float, int]],
    draws: list[list[int]],
    weights: list[float],
    db_combos: set[frozenset] | None = None,
) -> tuple[StageResult, list[int]]:
    name = "Final 14 + no-repeat enforcement"

    final14 = sorted(set(G1) | set(G2))
    checks = []
    ok = True
    blocked = set(ledger) | (db_combos or set())

    attempts = 0
    while frozenset(final14) in blocked and attempts < 25:
        attempts += 1
        source = "ledger" if frozenset(final14) in set(ledger) else "DB"
        lowest = min(final14, key=lambda n: influence[n])
        candidates = sorted(
            [n for n in POOL if n not in set(final14)],
            key=lambda n: -influence[n],
        )
        if not candidates:
            break
        final14 = sorted((set(final14) - {lowest}) | {candidates[0]})
        checks.append(f"  Unique-validator swap ({source}): dropped {lowest} "
                      f"(influence {influence[lowest]:.4f}), "
                      f"added {candidates[0]} (influence {influence[candidates[0]]:.4f})")

    if len(final14) != 14:
        checks.append(f"FAIL: final set has {len(final14)} numbers"); ok = False
    else:
        checks.append(f"PASS: exactly 14 distinct numbers")

    if any(n < 1 or n > 25 for n in final14):
        checks.append("FAIL: out-of-range number in final set"); ok = False
    else:
        checks.append("PASS: all numbers in 1..25")

    if frozenset(final14) in set(ledger):
        checks.append("FAIL: set still collides with ledger after swap attempts"); ok = False
    else:
        checks.append(f"PASS: not in no-repeat ledger (ledger size={len(ledger)})")

    if db_combos is not None:
        if frozenset(final14) in db_combos:
            checks.append("FAIL: set still matches a DB-drawn combo after swap attempts"); ok = False
        else:
            checks.append(f"PASS: unique validator -- not present in DB "
                          f"({len(db_combos)} historical combos checked)")

    if ok:
        ledger.append(frozenset(final14))
        _save_ledger(ledger)
        checks.append("  -> appended to NO_REPEAT_LEDGER")

    status = "PASS" if ok else "FAIL"
    return StageResult(10, name, status, {"final14": final14}, checks, ok), final14


# -------------------------------------------------------------
# Full pipeline
# -------------------------------------------------------------

def run_pipeline(
    sid: int,
    half_life: float = DEFAULT_HALF_LIFE,
    verbose: bool = True,
    update_ledger: bool = True,
) -> list[int]:
    """Run the full 11-stage pipeline and return the FINAL_14 ticket."""
    t_start = time.time()
    audit_log: list[StageResult] = []

    # -- Load data
    sid_order, draws = _fetch_e1(up_to_sid=sid)
    N = len(draws)
    if N == 0:
        print("ERROR: no E1 draws loaded"); sys.exit(1)
    weights = _compute_weights(N, half_life)

    if verbose:
        print(f"\nPredicting for series {sid} | training draws: {N} | half_life: {half_life}")
        print(f"Weight range: [{min(weights):.6f}, {max(weights):.6f}]\n")

    # -- Stage 0
    r0 = stage0(draws, weights)
    audit_log.append(r0)
    if verbose:
        _checkpoint(r0)

    # -- Stages 1..7
    rankings: dict[int, list] = {}
    for k in range(1, MAX_K + 1):
        top_n = 25 if k <= 3 else (15 if k <= 5 else 10)
        rk, ranking_k = stage_k(k, draws, weights, top_n=top_n, verbose=verbose)
        audit_log.append(rk)
        rankings[k] = ranking_k
        if verbose:
            _checkpoint(rk)

    # -- Stage 8
    r8, influence = stage8(rankings)
    audit_log.append(r8)
    if verbose:
        _checkpoint(r8)

    # -- Stage 9
    r9, G1, G2 = stage9(rankings[7], draws, weights, influence)
    audit_log.append(r9)
    if verbose:
        _checkpoint(r9)

    # -- Stage 10
    ledger = _load_ledger() if update_ledger else []
    db_combos = _fetch_db_combos(up_to_sid=sid)
    r10, final14 = stage10(G1, G2, influence, ledger, rankings[7], draws, weights, db_combos)
    audit_log.append(r10)
    if verbose:
        _checkpoint(r10)

    # -- Summary
    if verbose:
        elapsed = time.time() - t_start
        print(f"\n{'='*64}")
        print(f"VALIDATION SUMMARY  (all stages must be PASS)")
        print(f"{'='*64}")
        print(f"  {'Stage':<6}  {'Name':<42}  {'Status'}")
        print(f"  {'-'*60}")
        for r in audit_log:
            print(f"  {r.stage:<6}  {r.name:<42}  {r.status}")

        print(f"\nGROUP_1 (top 7-combo):          {sorted(G1)}")
        print(f"GROUP_2 (best disjoint 7-combo): {sorted(G2)}")
        print(f"\n{'='*64}")
        print(f"FINAL TICKET -- series {sid}  (heuristic estimate, not a guarantee):")
        print(f"  {' '.join(f'{n:02d}' for n in final14)}")
        print(f"{'='*64}")
        print(f"  elapsed: {elapsed:.1f}s")

    return final14


# -------------------------------------------------------------
# OOS evaluation (mirrors signal_predictor --oos interface)
# -------------------------------------------------------------

def oos_eval(oos_lo: int = 3193, half_life: float = DEFAULT_HALF_LIFE) -> list[int]:
    # Fetch all sid_order to find OOS targets
    conn = pyodbc.connect(CONN_STR, readonly=True)
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT DrawId FROM dbo.Draws WHERE EventIndex=1 ORDER BY DrawId ASC"
    )
    all_sids = [int(r[0]) for r in cur.fetchall()]
    conn.close()

    targets = [s for s in all_sids if s >= oos_lo]
    print(f"OOS targets: {len(targets)} series ({targets[0]}..{targets[-1]})")
    print()

    # Pre-load all E1 data once
    sid_order_full, draws_full = _fetch_e1()
    sid_to_idx = {s: i for i, s in enumerate(sid_order_full)}

    # Pre-load all (DrawId, combo) rows once; grow the validator set incrementally
    # instead of re-querying dbo.Draws on every OOS iteration (was O(n^2) DB work).
    all_combos_by_sid = _fetch_db_combos_by_sid()
    combo_ptr = 0
    db_combos: set[frozenset] = set()

    scores = []
    hits12 = 0; hits11 = 0

    for k, sid in enumerate(targets):
        # Training data: all draws strictly before sid
        idx = sid_to_idx[sid]
        draws_train = draws_full[:idx]
        sids_train = sid_order_full[:idx]
        N = len(draws_train)
        if N == 0:
            continue
        weights = _compute_weights(N, half_life)

        # Run pipeline silently
        r0 = stage0(draws_train, weights)
        if not r0.ready_for_next:
            print(f"  [{k+1}] sid={sid}  SKIPPED (Stage 0 FAIL)")
            continue

        rankings = {}
        for kk in range(1, MAX_K + 1):
            _, ranking_kk = stage_k(kk, draws_train, weights, verbose=False)
            rankings[kk] = ranking_kk

        _, influence = stage8(rankings)
        _, G1, G2 = stage9(rankings[7], draws_train, weights, influence)
        while combo_ptr < len(all_combos_by_sid) and all_combos_by_sid[combo_ptr][0] < sid:
            db_combos.add(all_combos_by_sid[combo_ptr][1])
            combo_ptr += 1
        _, final14 = stage10(G1, G2, influence, [], rankings[7], draws_train, weights, db_combos)

        # Score vs actual E1
        actual = set(draws_full[idx])
        h = len(set(final14) & actual)
        scores.append(h)
        if h >= 12: hits12 += 1
        if h >= 11: hits11 += 1
        print(f"  [{k+1:3d}/{len(targets)}] sid={sid}  hits={h}/14  ticket={final14}", flush=True)

    if not scores:
        print("No results.")
        return []

    n = len(scores)
    avg = sum(scores) / n
    print()
    print(f"{'='*60}")
    print(f"OOS E1 results (recurrence)  n={n}  ({targets[0]}..{targets[-1]})")
    print(f"  avg   = {avg:.3f}  (IID baseline 7.840)")
    print(f"  max   = {max(scores)}")
    print(f"  11+   = {hits11}/{n}  ({100*hits11/n:.1f}%)")
    print(f"  12+   = {hits12}/{n}  ({100*hits12/n:.1f}%)")
    print(f"  edge  = {avg - 7.840:+.3f} vs random")
    print(f"{'='*60}")

    cache = json.loads(OOS_CACHE.read_text()) if OOS_CACHE.exists() else {}
    cache["recurrence"] = {
        "oos_lo": oos_lo, "n": n, "avg": round(avg, 3),
        "max": int(max(scores)), "hits11": hits11, "hits12": hits12,
        "last_sid": int(targets[-1]), "last_hits": int(scores[-1]),
    }
    OOS_CACHE.write_text(json.dumps(cache, indent=2))

    return scores


# -------------------------------------------------------------
# CLI
# -------------------------------------------------------------

def main():
    args = sys.argv[1:]

    if "--oos" in args:
        lo = int(args[args.index("--from") + 1]) if "--from" in args else 3193
        hl = float(args[args.index("--hl") + 1]) if "--hl" in args else DEFAULT_HALF_LIFE
        oos_eval(lo, hl)
        return

    if not args or args[0].startswith("--"):
        print("Usage: python ml_models/recurrence_predictor.py <series_id> [--hl <half_life>]")
        print("       python ml_models/recurrence_predictor.py --oos [--from 3193] [--hl 26]")
        sys.exit(1)

    sid = int(next(a for a in args if not a.startswith("--")))
    hl = float(args[args.index("--hl") + 1]) if "--hl" in args else DEFAULT_HALF_LIFE

    print("Loading history...", flush=True)
    run_pipeline(sid, half_life=hl)


if __name__ == "__main__":
    main()
