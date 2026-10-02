"""
E1 single-ticket predictor — FROZEN base-rate favourites.

Session 35 (2026-05-29): delta-EWMA was proven NOT significant on E1.
  - OOS 3193–3232 avg = 8.125 vs IID 7.840; Monte-Carlo (200k) p = 0.086;
    paired vs last-200 favourites p = 0.126 — none survive Bonferroni.
  - A single FROZEN favourites ticket scores 8.025 (held-out, trained ≤3192),
    i.e. the entire dynamic EWMA machinery adds +0.10 (noise) over taping one
    ticket to the wall.
  - CEILING: a mathematically perfect static E1 model caps at 7.947 hits/draw
    (Σ of the 14 largest E1 marginal probs). Extractable static edge = +0.107.
    Single-ticket E1 is already AT its ceiling; nothing left to extract.
  - Recent E1 has NO momentum (consecutive-draw overlap last-200 = 7.815,
    p = 0.77), which is why EWMA/recency methods regress to 7.84 on incoming
    draws (the last-5 dip to 7.2 was regression to the true mean, not decay).

The production signal is FROZEN FAVOURITES (HOT_WEIGHT=0.0, session 36):
  score(n) = long_freq(n)
where long_freq = all-time E1 marginal frequency up to the target draw.
Top-14 by long_freq, with a no-repeat novelty guard (the ticket is never an exact
past E1/any-event combination — a free, correct filter; collisions ~1/1832).

HOT_WEIGHT was 1.0 in session 35 but OOS proof (n=41, 3193-3233) showed it costs
-0.39 hits/draw vs frozen (7.634 vs 8.024), modifying the ticket every single draw.
The p=0.040 was a false positive on the large backtest window.

IMPORTANT — what this CANNOT do: P(>=12 hits) is hypergeometric and IDENTICAL for
every 14-ticket (closed-form theorem, ticket-invariant). The favourites/hot edge
lives only in the 8-9 hit region (minor prizes). It does not raise odds of the
12/13/14 payouts. Do NOT reintroduce EWMA/momentum/bias — measured null on E1.

History retained for the record: L30-1.0*L10 (s32) → delta-EWMA (s34, bias
removed s21) → frozen favourites → favourites+hot (s35). See memory
e1_structure_hunt_session35.

Usage:
  python ml_models/signal_predictor.py <series_id>
  python ml_models/signal_predictor.py <series_id> --dist
  python ml_models/signal_predictor.py --oos [--from 3193]
"""
from __future__ import annotations

import sys
import json
import time
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
WARMUP  = 100  # min idx before any prediction is meaningful

BIAS_PATH = ROOT / "ml_models" / "signal_bias.json"

# delta-EWMA hyperparameters — validated 2026-05-16 on OOS n=35.
# Sweep winner: walk-forward test (3211-3227) avg=8.412 vs 7.647 for L30-1.0*L10.
# Pure delta (w=1.0) outperforms any blend with pool/long components.
_EWMA_FAST = 0.30
_EWMA_SLOW = 0.08
# Warm-up window: number of draws before OOS start used to initialise EWMA state.
_EWMA_WARMUP = 200


# ────────────────────────────────────────────────────────────────────────────
# DB
# ────────────────────────────────────────────────────────────────────────────

def _fetch_all() -> tuple[list[int], np.ndarray, np.ndarray]:
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
    e1_mat  = np.zeros((S, 26), dtype=bool)
    any_mat = np.zeros((S, 26), dtype=bool)

    for i, sid in enumerate(sid_order):
        events = sorted(by_sid[sid], key=lambda x: x[0])
        for j, (ei, nums) in enumerate(events):
            for n in nums:
                any_mat[i, n] = True
            if j == 0:
                for n in nums:
                    e1_mat[i, n] = True

    return sid_order, e1_mat, any_mat


# ────────────────────────────────────────────────────────────────────────────
# Stage 1 — delta-EWMA signal
# ────────────────────────────────────────────────────────────────────────────

def _build_ewma_state(
    e1_mat: np.ndarray,
    up_to_idx: int,
    warmup: int = _EWMA_WARMUP,
    alpha_fast: float = _EWMA_FAST,
    alpha_slow: float = _EWMA_SLOW,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute EWMA_fast and EWMA_slow by replaying draws [up_to_idx-warmup, up_to_idx).

    Returns (ewma_fast, ewma_slow) length-26 vectors (index 0 unused).
    The state reflects everything seen BEFORE up_to_idx — safe to use as
    a prediction signal for the draw at up_to_idx.
    """
    ewma_f = np.zeros(26, dtype=np.float64)
    ewma_s = np.zeros(26, dtype=np.float64)
    lo = max(0, up_to_idx - warmup)
    for i in range(lo, up_to_idx):
        row = e1_mat[i, 1:26].astype(np.float64)
        ewma_f[1:26] = alpha_fast * row + (1.0 - alpha_fast) * ewma_f[1:26]
        ewma_s[1:26] = alpha_slow * row + (1.0 - alpha_slow) * ewma_s[1:26]
    return ewma_f, ewma_s


def _delta_scores(ewma_f: np.ndarray, ewma_s: np.ndarray) -> np.ndarray:
    """EWMA_fast - EWMA_slow: positive = accelerating, negative = decelerating."""
    return ewma_f - ewma_s


def _norm01(v: np.ndarray) -> np.ndarray:
    """Min-max normalize entries 1..25 to [0,1]; index 0 stays 0."""
    out = np.zeros(26, dtype=np.float64)
    s = v[1:26]; rng = s.max() - s.min()
    out[1:26] = (s - s.min()) / rng if rng > 0 else np.full(25, 0.5)
    return out


def _raw_probs(ewma_f: np.ndarray, ewma_s: np.ndarray) -> np.ndarray:
    """Probability vector from normalised delta-EWMA scores. (Retained for --dist
    diagnostics only; the production signal is frozen favourites, see _freq_scores.)"""
    delta = _delta_scores(ewma_f, ewma_s)
    normed = _norm01(delta)
    probs = np.zeros(26, dtype=np.float64)
    probs[1:26] = np.maximum(normed[1:26], 1e-9)
    probs[1:26] /= probs[1:26].sum()
    return probs


def _freq_probs(e1_mat: np.ndarray, up_to_idx: int) -> np.ndarray:
    """Frozen-favourites signal: all-time E1 marginal frequency over draws
    strictly before up_to_idx, normalised to a probability vector. The top-14
    of this are the only statistically defensible static E1 edge (ceiling 7.947).
    """
    counts = e1_mat[:up_to_idx, 1:26].astype(np.float64).sum(axis=0)
    probs = np.zeros(26, dtype=np.float64)
    total = counts.sum()
    if total <= 0:
        probs[1:26] = 1.0 / 25.0
    else:
        probs[1:26] = np.maximum(counts, 1e-9) / counts.sum()
    return probs


# Hot-hand blend weight. Set to 0.0 (pure frozen favourites) after OOS proof:
# HOT_WEIGHT=1.0 cost -0.39 hits/draw across 41 OOS series (3193-3233),
# modifying the ticket every single draw and consistently underperforming frozen.
# The p=0.040 from session 35 was a false positive on the large backtest window.
HOT_WEIGHT = 0.0
HOT_WINDOW = 5


def _blend_scores(e1_mat: np.ndarray, up_to_idx: int,
                  hot_weight: float = HOT_WEIGHT,
                  hot_window: int = HOT_WINDOW) -> np.ndarray:
    """Production signal: long-run E1 frequency + hot-hand tilt.

    score(n) = long_freq(n) + hot_weight * last{hot_window}_freq(n)
    Returned as a length-26 score vector (index 0 unused, not normalised —
    only the ranking matters for ticket selection).
    """
    if up_to_idx <= 0:
        return np.zeros(26, dtype=np.float64)
    long_freq = e1_mat[:up_to_idx, 1:26].astype(np.float64).mean(axis=0)
    lo = max(0, up_to_idx - hot_window)
    hot_freq = e1_mat[lo:up_to_idx, 1:26].astype(np.float64).mean(axis=0)
    scores = np.zeros(26, dtype=np.float64)
    scores[1:26] = long_freq + hot_weight * hot_freq
    return scores


def _past_e1_combos(e1_mat: np.ndarray, up_to_idx: int) -> set[frozenset]:
    """All exact 14-number E1 sets drawn strictly before up_to_idx.

    Used by the no-repeat novelty guard — a combination that already appeared
    cannot recur (0 repeats in 2,434 historical draws), so it is a guaranteed
    dead ticket. Filtering it costs nothing and is strictly correct.
    """
    combos = set()
    for i in range(up_to_idx):
        nums = frozenset(int(n) for n in NUMBERS if e1_mat[i, n])
        if len(nums) == 14:
            combos.add(nums)
    return combos


def _novel_ticket(scores: np.ndarray, past: set[frozenset]) -> list[int]:
    """Top-14 by score, guaranteed not to equal any past E1 combo.

    The natural top-14 is novel ~99.95% of the time. On the rare collision,
    swap the weakest in-ticket number for the strongest out-of-ticket number
    until the set is novel (always terminates — 25 numbers, finite swaps).
    """
    order = sorted(NUMBERS, key=lambda n: -scores[n])
    ticket = order[:14]
    if frozenset(ticket) not in past:
        return ticket
    # Collision: try single-number swaps, weakest-in for strongest-out.
    inside = order[:14]
    outside = order[14:]
    for drop_i in range(13, -1, -1):          # weakest in-ticket first
        for add in outside:                    # strongest out first
            cand = frozenset(inside[:drop_i] + inside[drop_i + 1:] + [add])
            if len(cand) == 14 and cand not in past:
                return sorted(cand, key=lambda n: -scores[n])
    return ticket  # exhausted (impossible in practice); return natural top-14


# ────────────────────────────────────────────────────────────────────────────
# Stage 2 — Bias calibration
# ────────────────────────────────────────────────────────────────────────────

def _load_bias() -> np.ndarray:
    if BIAS_PATH.exists():
        data = json.loads(BIAS_PATH.read_text())
        bv = data.get("bias_vector", [0.0] * 26)
        return np.array(bv, dtype=np.float64)
    return np.zeros(26, dtype=np.float64)


def _apply_bias(probs: np.ndarray, bias: np.ndarray) -> np.ndarray:
    adj = probs.copy()
    for n in NUMBERS:
        adj[n] = max(1e-9, probs[n] * (1.0 + bias[n]))
    adj[1:26] /= adj[1:26].sum()
    return adj


def _resolve_bias_signal_conflict(
    bias: np.ndarray, delta: np.ndarray,
    signal_threshold: float = 0.60, bias_floor: float = -0.05,
) -> np.ndarray:
    """Clamp bias[n] to 0 when delta-EWMA puts n in the top tier AND bias suppresses it.

    Prevents the rank-calibrated bias from overriding a strong acceleration signal.
    """
    raw = delta[1:26]
    rng = raw.max() - raw.min()
    if rng <= 0:
        return bias
    norm = (raw - raw.min()) / rng
    out = bias.copy()
    for i, n in enumerate(NUMBERS):
        if norm[i] >= signal_threshold and out[n] < bias_floor:
            out[n] = 0.0
    return out


def _fit_bias(
    sid_order: list[int], e1_mat: np.ndarray, any_mat: np.ndarray,
    fit_lo: int, fit_hi: int,
    clip: float = 0.40,
) -> np.ndarray:
    """Rank-calibrated bias over the fit window.

    For each draw in [fit_lo, fit_hi], build EWMA state up to that draw,
    rank all numbers by delta score, record rank of numbers that appeared in E1.
    bias[n] = -(avg_rank_when_in_actual - 13) / 13, clipped to [-clip, +clip].
    """
    sid_to_idx = {s: i for i, s in enumerate(sid_order)}
    targets = [s for s in sid_order if fit_lo <= s <= fit_hi]
    print(f"[fit-bias] fitting on {len(targets)} series ({fit_lo}..{fit_hi})")

    rank_sums: dict[int, list[int]] = {n: [] for n in NUMBERS}
    for k, sid in enumerate(targets):
        idx = sid_to_idx[sid]
        if idx < WARMUP:
            continue
        ewma_f, ewma_s = _build_ewma_state(e1_mat, idx)
        raw = _raw_probs(ewma_f, ewma_s)
        order = sorted(NUMBERS, key=lambda nn: -raw[nn])
        rank_of = {n: r for r, n in enumerate(order, 1)}
        for n in NUMBERS:
            if e1_mat[idx, n]:
                rank_sums[n].append(rank_of[n])
        print(f"  [{k+1}/{len(targets)}] sid={sid}", flush=True)

    bias = np.zeros(26, dtype=np.float64)
    for n in NUMBERS:
        if rank_sums[n]:
            avg_rank = float(np.mean(rank_sums[n]))
            bias[n] = float(np.clip(-(avg_rank - 13.0) / 13.0, -clip, +clip))
    return bias


# ────────────────────────────────────────────────────────────────────────────
# Full pipeline
# ────────────────────────────────────────────────────────────────────────────

def _print_dist(label: str, probs: np.ndarray, ticket_set: set[int], cliff_pct: float = 0.15) -> None:
    ranked = sorted(NUMBERS, key=lambda n: -probs[n])
    print(f"\n  {label} — ranked distribution (cliff threshold {cliff_pct*100:.0f}% of leader):")
    print(f"  {'Rank':>4}  {'N':>3}  {'Prob':>7}  {'Gap->next':>10}  {'In ticket':>9}")
    print(f"  {'-'*48}")
    leader_prob = probs[ranked[0]]
    for i, n in enumerate(ranked):
        p    = probs[n]
        gap  = p - probs[ranked[i + 1]] if i < len(ranked) - 1 else 0.0
        in_t = "*" if n in ticket_set else ""
        cliff = "  <--CLIFF" if gap >= cliff_pct * leader_prob else ""
        print(f"  {i+1:>4}{in_t:1}  {n:>2}   {p*100:>6.2f}%   {gap*100:>8.2f}%{cliff}")
    print()


def predict(
    sid: int,
    sid_order: list[int],
    e1_mat: np.ndarray,
    any_mat: np.ndarray,
    bias: np.ndarray = None,  # kept for call-site compat, ignored
    verbose: bool = True,
    dist: bool = False,
) -> list[int]:
    idx = sid_order.index(sid)

    # Production signal: favourites + hot-hand, with no-repeat novelty guard.
    # delta-EWMA/momentum/bias all measured null on E1 (session 35) — not used.
    t0 = time.time()
    scores = _blend_scores(e1_mat, idx)
    past   = _past_e1_combos(e1_mat, idx)
    ticket = _novel_ticket(scores, past)
    ticket_set = set(ticket)
    novel  = frozenset(ticket) not in past

    if verbose:
        top5 = sorted(NUMBERS, key=lambda n: -scores[n])[:5]
        lo = max(0, idx - HOT_WINDOW)
        hotnums = sorted(n for n in NUMBERS
                         if e1_mat[lo:idx, n].sum() >= 4 and n in ticket_set)
        print(f"  [fav+hot]  top5={top5}  hot-in-ticket={hotnums}  "
              f"(history={idx} draws, novel={novel}, {time.time()-t0:.2f}s)")

    if dist:
        probs = _norm01(scores)
        probs[1:26] = np.maximum(probs[1:26], 1e-9)
        probs[1:26] /= probs[1:26].sum()
        _print_dist("fav+hot", probs, ticket_set)

    return ticket


# ────────────────────────────────────────────────────────────────────────────
# OOS evaluation
# ────────────────────────────────────────────────────────────────────────────

def oos_eval(oos_lo: int = 3193):
    print("Loading history...", flush=True)
    sid_order, e1_mat, any_mat = _fetch_all()

    targets = [s for s in sid_order if s >= oos_lo]
    print(f"OOS targets: {len(targets)} series ({targets[0]}..{targets[-1]})")
    print()

    scores = []
    hits12 = 0; hits11 = 0

    for k, sid in enumerate(targets):
        idx    = sid_order.index(sid)
        ticket = predict(sid, sid_order, e1_mat, any_mat, verbose=False)
        actual = set(n for n in NUMBERS if e1_mat[idx, n])
        h      = len(set(ticket) & actual)
        scores.append(h)
        if h >= 12: hits12 += 1
        if h >= 11: hits11 += 1
        print(f"  [{k+1:3d}/{len(targets)}] sid={sid}  hits={h}/14  ticket={ticket}", flush=True)

    n   = len(scores)
    avg = sum(scores) / n
    print()
    print(f"{'='*60}")
    print(f"OOS E1 results  n={n}  ({targets[0]}..{targets[-1]})")
    print(f"  avg   = {avg:.3f}  (IID baseline 7.840)")
    print(f"  max   = {max(scores)}")
    print(f"  11+   = {hits11}/{n}  ({100*hits11/n:.1f}%)")
    print(f"  12+   = {hits12}/{n}  ({100*hits12/n:.1f}%)")
    print(f"  edge  = {avg - 7.840:+.3f} vs random")
    print(f"{'='*60}")
    return scores


# ────────────────────────────────────────────────────────────────────────────
# Fit bias
# ────────────────────────────────────────────────────────────────────────────

def fit_bias_cmd(fit_lo: int = 3193, fit_hi: int = 3227):
    print("Loading history...", flush=True)
    sid_order, e1_mat, any_mat = _fetch_all()
    bias = _fit_bias(sid_order, e1_mat, any_mat, fit_lo, fit_hi)
    payload = {
        "fit_window": f"{fit_lo}-{fit_hi}",
        "fit_method": "rank-vs-random: bias = -(avg_rank_in_actual - 13) / 13",
        "signal": "delta-EWMA(fast=0.30, slow=0.08)",
        "bias_vector": [round(float(bias[i]), 6) for i in range(26)],
        "calibration_window_start": fit_lo,
        "calibration_window_end": fit_hi,
    }
    BIAS_PATH.write_text(json.dumps(payload, indent=2))
    print(f"\nBias fitted and saved -> {BIAS_PATH}")
    print(f"  {bias[1:26].tolist()}")
    return bias


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if "--oos" in args:
        lo = int(args[args.index("--from") + 1]) if "--from" in args else 3193
        oos_eval(lo)
        return

    if not args:
        print("Usage: python ml_models/signal_predictor.py <series_id>")
        print("       python ml_models/signal_predictor.py <series_id> --dist")
        print("       python ml_models/signal_predictor.py --oos [--from 3193]")
        sys.exit(1)

    show_dist = "--dist" in args
    sid = int(next(a for a in args if not a.startswith("--")))
    print("Loading history...", flush=True)
    sid_order, e1_mat, any_mat = _fetch_all()

    if sid not in sid_order:
        sid_order = sid_order + [sid]
        e1_mat  = np.vstack([e1_mat,  np.zeros((1, 26), dtype=bool)])
        any_mat = np.vstack([any_mat, np.zeros((1, 26), dtype=bool)])

    print(f"\nPREDICTING E1 for series {sid}  (training on {sid_order.index(sid)} series)")
    print()
    ticket = predict(sid, sid_order, e1_mat, any_mat, verbose=True, dist=show_dist)

    print()
    print(f"{'='*60}")
    print(f"FINAL TICKET (E1) - series {sid}:")
    print(f"  {' '.join(f'{n:02d}' for n in ticket)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
