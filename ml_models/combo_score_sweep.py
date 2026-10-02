"""
Hyperparameter sweep for the delta-EWMA signal pipeline.

Sweeps:
  alpha_fast    in [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]
  alpha_slow    in [0.04, 0.06, 0.08, 0.10, 0.12, 0.15]
  warmup        in [100, 150, 200]
  signal_weight in [0.7, 0.8, 0.9, 1.0]   (blend signal with uniform 1/25)

Only combos with alpha_fast > alpha_slow are evaluated.

Walk-forward OOS on series 3193..3229. No bias is applied — signal only.
For each series, EWMA state is rebuilt from the warmup-window of E1 history
strictly BEFORE that series (no look-ahead).

Score per series = |top14_predicted intersect actual_E1|.

Output:
  - Top 20 combos ranked by avg hits desc, then 11+ count desc.
  - [CURRENT] tag on (0.30, 0.08, 200, 1.0).
  - Counts of configs that beat/tie/lose vs current.
"""
from __future__ import annotations

import sys
import time
from itertools import product
from pathlib import Path

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

NUMBERS = list(range(1, 26))

OOS_LO = 3193
OOS_HI = 3229

ALPHA_FAST    = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]
ALPHA_SLOW    = [0.04, 0.06, 0.08, 0.10, 0.12, 0.15]
WARMUPS       = [100, 150, 200]
SIGNAL_WEIGHT = [0.7, 0.8, 0.9, 1.0]

CURRENT = (0.30, 0.08, 200, 1.0)


# ────────────────────────────────────────────────────────────────────────────
# Data loading (matches signal_predictor._fetch_all exactly)
# ────────────────────────────────────────────────────────────────────────────

def _fetch_all() -> tuple[list[int], np.ndarray]:
    conn = pyodbc.connect(CONN_STR, readonly=True)
    cur  = conn.cursor()
    cur.execute(
        "SELECT DrawId, EventIndex, "
        "N01,N02,N03,N04,N05,N06,N07,N08,N09,N10,N11,N12,N13,N14 "
        "FROM dbo.Draws ORDER BY DrawId ASC, EventIndex ASC"
    )
    rows = cur.fetchall()
    conn.close()

    by_sid: dict[int, list] = {}
    for r in rows:
        sid = int(r[0]); ei = int(r[1]); nums = [int(x) for x in r[2:16]]
        by_sid.setdefault(sid, []).append((ei, nums))

    sid_order = sorted(by_sid)
    S = len(sid_order)
    e1_mat = np.zeros((S, 26), dtype=bool)
    for i, sid in enumerate(sid_order):
        events = sorted(by_sid[sid], key=lambda x: x[0])
        for j, (ei, nums) in enumerate(events):
            if j == 0:
                for n in nums:
                    e1_mat[i, n] = True
    return sid_order, e1_mat


# ────────────────────────────────────────────────────────────────────────────
# EWMA + scoring (replicates signal_predictor exactly with extra knobs)
# ────────────────────────────────────────────────────────────────────────────

def _build_ewma_state(
    e1_mat: np.ndarray, up_to_idx: int,
    warmup: int, alpha_fast: float, alpha_slow: float,
) -> tuple[np.ndarray, np.ndarray]:
    ewma_f = np.zeros(26, dtype=np.float64)
    ewma_s = np.zeros(26, dtype=np.float64)
    lo = max(0, up_to_idx - warmup)
    for i in range(lo, up_to_idx):
        row = e1_mat[i, 1:26].astype(np.float64)
        ewma_f[1:26] = alpha_fast * row + (1.0 - alpha_fast) * ewma_f[1:26]
        ewma_s[1:26] = alpha_slow * row + (1.0 - alpha_slow) * ewma_s[1:26]
    return ewma_f, ewma_s


def _norm01(v: np.ndarray) -> np.ndarray:
    out = np.zeros(26, dtype=np.float64)
    s = v[1:26]
    rng = s.max() - s.min()
    out[1:26] = (s - s.min()) / rng if rng > 0 else np.full(25, 0.5)
    return out


def _raw_probs(ewma_f: np.ndarray, ewma_s: np.ndarray) -> np.ndarray:
    delta = ewma_f - ewma_s
    normed = _norm01(delta)
    probs = np.zeros(26, dtype=np.float64)
    probs[1:26] = np.maximum(normed[1:26], 1e-9)
    probs[1:26] /= probs[1:26].sum()
    return probs


def _blend_with_uniform(probs: np.ndarray, w: float) -> np.ndarray:
    """Blend signal probs with uniform (1/25). w=1.0 -> pure signal, w=0.0 -> pure uniform."""
    if w >= 1.0:
        return probs
    out = np.zeros(26, dtype=np.float64)
    out[1:26] = w * probs[1:26] + (1.0 - w) * (1.0 / 25.0)
    out[1:26] /= out[1:26].sum()
    return out


def _ticket_from_probs(probs: np.ndarray) -> list[int]:
    return sorted(NUMBERS, key=lambda n: -probs[n])[:14]


# ────────────────────────────────────────────────────────────────────────────
# Sweep
# ────────────────────────────────────────────────────────────────────────────

def evaluate_combo(
    sid_order: list[int], e1_mat: np.ndarray, oos_indices: list[int],
    alpha_fast: float, alpha_slow: float, warmup: int, signal_weight: float,
) -> dict:
    scores = []
    for idx in oos_indices:
        ewma_f, ewma_s = _build_ewma_state(
            e1_mat, idx, warmup=warmup,
            alpha_fast=alpha_fast, alpha_slow=alpha_slow,
        )
        probs = _raw_probs(ewma_f, ewma_s)
        probs = _blend_with_uniform(probs, signal_weight)
        ticket = _ticket_from_probs(probs)
        actual = set(n for n in NUMBERS if e1_mat[idx, n])
        scores.append(len(set(ticket) & actual))
    arr = np.array(scores, dtype=np.int32)
    return {
        "avg":    float(arr.mean()),
        "max":    int(arr.max()),
        "n":      int(len(arr)),
        "h10":    int((arr >= 10).sum()),
        "h11":    int((arr >= 11).sum()),
        "h12":    int((arr >= 12).sum()),
    }


def main():
    t0 = time.time()
    print("Loading history from LuckyDb...", flush=True)
    sid_order, e1_mat = _fetch_all()
    sid_to_idx = {s: i for i, s in enumerate(sid_order)}

    oos_sids = [s for s in sid_order if OOS_LO <= s <= OOS_HI]
    oos_indices = [sid_to_idx[s] for s in oos_sids]
    print(f"OOS window: {len(oos_sids)} series ({oos_sids[0]}..{oos_sids[-1]})")
    print(f"Loaded {len(sid_order)} total series; e1_mat shape={e1_mat.shape}")
    print()

    combos = []
    for af, as_, wu, sw in product(ALPHA_FAST, ALPHA_SLOW, WARMUPS, SIGNAL_WEIGHT):
        if af > as_:
            combos.append((af, as_, wu, sw))
    print(f"Total combos to evaluate: {len(combos)} "
          f"(of {len(ALPHA_FAST)*len(ALPHA_SLOW)*len(WARMUPS)*len(SIGNAL_WEIGHT)} cartesian)")
    print()

    results = []
    for k, (af, as_, wu, sw) in enumerate(combos):
        r = evaluate_combo(sid_order, e1_mat, oos_indices, af, as_, wu, sw)
        r["alpha_fast"]    = af
        r["alpha_slow"]    = as_
        r["warmup"]        = wu
        r["signal_weight"] = sw
        results.append(r)
        if (k + 1) % 25 == 0 or k + 1 == len(combos):
            print(f"  evaluated {k+1}/{len(combos)}  "
                  f"(elapsed {time.time()-t0:.1f}s)", flush=True)

    # Rank: avg desc, then 11+ desc, then 12+ desc, then max desc
    results.sort(key=lambda r: (-r["avg"], -r["h11"], -r["h12"], -r["max"]))

    # Find current
    cur_res = next(
        r for r in results
        if (r["alpha_fast"], r["alpha_slow"], r["warmup"], r["signal_weight"]) == CURRENT
    )
    cur_avg = cur_res["avg"]

    # Tally beat/tie/lose
    beat = sum(1 for r in results if r["avg"] > cur_avg + 1e-9)
    tie  = sum(1 for r in results if abs(r["avg"] - cur_avg) <= 1e-9)
    lose = sum(1 for r in results if r["avg"] < cur_avg - 1e-9)

    # ─── Print ranked table ────────────────────────────────────────────────
    print()
    print("=" * 92)
    print(f"FULL RANKING — {len(results)} combos, OOS window {OOS_LO}..{OOS_HI} "
          f"(n={cur_res['n']}), no bias")
    print("=" * 92)
    header = f"{'rk':>3}  {'a_fast':>6} {'a_slow':>6} {'wu':>4} {'w':>4}  " \
             f"{'avg':>6}  {'max':>3}  {'10+':>4} {'11+':>4} {'12+':>4}   tag"
    print(header)
    print("-" * len(header))
    for rank, r in enumerate(results, 1):
        is_cur = (r["alpha_fast"], r["alpha_slow"], r["warmup"], r["signal_weight"]) == CURRENT
        tag = "[CURRENT]" if is_cur else ""
        if rank <= 20 or is_cur:
            print(f"{rank:>3}  {r['alpha_fast']:>6.2f} {r['alpha_slow']:>6.2f} "
                  f"{r['warmup']:>4d} {r['signal_weight']:>4.2f}  "
                  f"{r['avg']:>6.3f}  {r['max']:>3d}  "
                  f"{r['h10']:>4d} {r['h11']:>4d} {r['h12']:>4d}   {tag}")

    print()
    print("=" * 92)
    print(f"CURRENT config (0.30, 0.08, 200, 1.0): avg={cur_avg:.3f}  "
          f"max={cur_res['max']}  10+={cur_res['h10']}  11+={cur_res['h11']}  "
          f"12+={cur_res['h12']}")
    print(f"IID baseline: 7.840")
    print()
    print(f"Configs that BEAT current avg:  {beat:>3d}")
    print(f"Configs that TIE  current avg:  {tie:>3d}")
    print(f"Configs that LOSE current avg:  {lose:>3d}")
    print(f"Total evaluated:                {len(results):>3d}")
    print()

    top = results[0]
    delta = top["avg"] - cur_avg
    materially = delta > 0.15
    print(f"BEST combo: a_fast={top['alpha_fast']}  a_slow={top['alpha_slow']}  "
          f"warmup={top['warmup']}  signal_weight={top['signal_weight']}")
    print(f"            avg={top['avg']:.3f}  (delta vs current = {delta:+.3f})")
    print(f"VERDICT: {'MATERIAL IMPROVEMENT (>0.15)' if materially else 'NO MATERIAL IMPROVEMENT (<=0.15)'} "
          f"over current.")
    print("=" * 92)
    print(f"Total runtime: {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
