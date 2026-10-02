"""
Hybrid 8-family predictor — per-series E1 signal injection.

Replaces the most-redundant v2 set with a signal-driven set computed from
L30−1.5×L5 on E1 history up to (but not including) the target series.

Signal: score[n] = freq_L30[n] − 1.5 × freq_L5[n]
  → selects numbers persistent over 30 draws but not short-term peaked.
  → OOS single-ticket avg 7.78 vs 7.19 random (p<0.0001).

Construction (per series):
  1. Load all E1 draws up to draw before target series.
  2. Compute L30 and L5 frequency vectors.
  3. Shift scores to positive, sample 20,000 weighted 14-combinations.
  4. Pick the candidate with maximum total signal score → signal_set.
  5. Find which v2 set has the highest overlap with signal_set (most redundant).
  6. Replace that v2 set with signal_set.
  7. Verify all 8 sets novel against filter_pool.json.

Usage:
  python ml_models/hybrid_family_predictor.py 3222
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pyodbc

ROOT = Path(__file__).resolve().parents[1]

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DESKTOP-QR14EDK\\SQLEXPRESS01;"
    "Database=LuckyDb;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

NUM_COLS = "N01,N02,N03,N04,N05,N06,N07,N08,N09,N10,N11,N12,N13,N14"

V2_FAMILY = [
    [4, 5, 6, 7, 8, 9, 11, 16, 18, 19, 20, 21, 22, 25],
    [1, 3, 4, 5, 6, 11, 12, 13, 15, 16, 17, 19, 24, 25],
    [1, 2, 5, 7, 8, 10, 11, 12, 13, 14, 15, 16, 20, 23],
    [1, 3, 7, 9, 11, 14, 15, 17, 18, 19, 20, 21, 22, 24],
    [2, 3, 6, 7, 8, 10, 13, 14, 17, 18, 19, 22, 24, 25],
    [2, 3, 4, 6, 9, 11, 12, 14, 17, 18, 21, 22, 23, 25],
    [1, 2, 4, 5, 6, 8, 9, 10, 15, 21, 22, 23, 24, 25],
    [3, 4, 9, 10, 12, 13, 16, 17, 18, 19, 20, 21, 23, 24],
]

NUMBERS = list(range(1, 26))


def _conn():
    return pyodbc.connect(CONN_STR, readonly=True)


def fetch_e1_history(before_draw_id: int) -> list[list[int]]:
    """Return all E1 events (sorted number lists) from draws strictly before before_draw_id."""
    cn = _conn()
    cur = cn.cursor()
    try:
        cur.execute(
            f"SELECT {NUM_COLS} FROM dbo.Draws "
            "WHERE DrawId < ? AND EventIndex = 1 "
            "ORDER BY DrawId ASC",
            (before_draw_id,)
        )
        rows = cur.fetchall()
        return [sorted(int(x) for x in row) for row in rows]
    finally:
        cn.close()


def compute_signal(e1_history: list[list[int]], l_long: int = 30, l_short: int = 5) -> dict[int, float]:
    """
    score[n] = freq_L30[n] - 1.5 * freq_L5[n]
    Uses the last l_long and l_short draws from e1_history.
    Returns raw scores (may be negative).
    """
    long_window = e1_history[-l_long:] if len(e1_history) >= l_long else e1_history
    short_window = e1_history[-l_short:] if len(e1_history) >= l_short else e1_history

    freq_long: dict[int, float] = {n: 0.0 for n in NUMBERS}
    freq_short: dict[int, float] = {n: 0.0 for n in NUMBERS}

    n_long = len(long_window)
    n_short = len(short_window)

    for draw in long_window:
        for n in draw:
            freq_long[n] += 1.0 / n_long

    for draw in short_window:
        for n in draw:
            freq_short[n] += 1.0 / n_short

    scores = {n: freq_long[n] - 1.5 * freq_short[n] for n in NUMBERS}
    return scores


def build_signal_set(scores: dict[int, float], n_samples: int = 20000, seed: int = 42) -> list[int]:
    """
    Weighted sampling: shift scores to positive, sample 14 numbers without
    replacement proportional to shifted scores, repeat n_samples times,
    keep the candidate with maximum total raw score.
    """
    rng = random.Random(seed)

    min_score = min(scores.values())
    shift = abs(min_score) + 1e-6
    weights = [scores[n] + shift for n in NUMBERS]
    total_weight = sum(weights)
    probs = [w / total_weight for w in weights]

    best_set: list[int] | None = None
    best_score = float("-inf")

    for _ in range(n_samples):
        # Weighted sampling without replacement via reservoir approach
        # Use a simple rejection-based weighted sample
        selected: list[int] = []
        remaining_nums = list(NUMBERS)
        remaining_probs = list(probs)

        while len(selected) < 14:
            total = sum(remaining_probs)
            r = rng.random() * total
            cumsum = 0.0
            for idx, p in enumerate(remaining_probs):
                cumsum += p
                if r <= cumsum:
                    selected.append(remaining_nums[idx])
                    remaining_nums.pop(idx)
                    remaining_probs.pop(idx)
                    break

        total_score = sum(scores[n] for n in selected)
        if total_score > best_score:
            best_score = total_score
            best_set = sorted(selected)

    return best_set


def find_most_redundant(signal_set: list[int], family: list[list[int]]) -> int:
    """Return index of v2 set with highest overlap with signal_set (most redundant)."""
    sig = set(signal_set)
    overlaps = [len(sig & set(s)) for s in family]
    return overlaps.index(max(overlaps))


def load_filter_pool() -> set[frozenset]:
    """Load historical events for novelty checking."""
    pool_path = ROOT / "data" / "filter_pool.json"
    if pool_path.exists():
        payload = json.loads(pool_path.read_text())
        return {frozenset(e) for e in payload["events"]}
    # fallback
    data_path = ROOT / "data" / "full_series_data.json"
    data = json.loads(data_path.read_text())
    hist = set()
    for sid in data:
        for e in data[sid]:
            hist.add(frozenset(sorted(int(x) for x in e)))
    return hist


def build_hybrid_family(
    series_id: int,
    n_samples: int = 20000,
    seed: int = 42,
    verbose: bool = True,
) -> tuple[list[list[int]], int, list[int]]:
    """
    Build the hybrid 8-family for the given series_id.

    Returns:
        (hybrid_family, replaced_idx, signal_set)
        hybrid_family: 8 sets, with v2[replaced_idx] swapped for signal_set
        replaced_idx: 0-based index of v2 set that was replaced
        signal_set: the signal-derived set
    """
    if verbose:
        print(f"[hybrid] Loading E1 history before draw {series_id}...")
    e1_history = fetch_e1_history(series_id)
    if verbose:
        print(f"[hybrid] {len(e1_history)} E1 draws available.")

    if len(e1_history) < 5:
        raise ValueError(f"Insufficient E1 history ({len(e1_history)} draws) for series {series_id}.")

    scores = compute_signal(e1_history)

    if verbose:
        top5 = sorted(scores.items(), key=lambda x: -x[1])[:5]
        bot5 = sorted(scores.items(), key=lambda x: x[1])[:5]
        print(f"[hybrid] Top-5 signal nums: {[f'{n}({v:.3f})' for n, v in top5]}")
        print(f"[hybrid] Bot-5 signal nums: {[f'{n}({v:.3f})' for n, v in bot5]}")

    if verbose:
        print(f"[hybrid] Sampling {n_samples} candidates...")
    signal_set = build_signal_set(scores, n_samples=n_samples, seed=seed)
    if verbose:
        print(f"[hybrid] Signal set: {signal_set}")

    replaced_idx = find_most_redundant(signal_set, V2_FAMILY)
    overlap = len(set(signal_set) & set(V2_FAMILY[replaced_idx]))
    if verbose:
        print(f"[hybrid] Replacing S{replaced_idx + 1} (overlap={overlap}/14): {V2_FAMILY[replaced_idx]}")

    hybrid = [list(s) for s in V2_FAMILY]
    hybrid[replaced_idx] = signal_set

    # Novelty check
    pool = load_filter_pool()
    collisions = []
    for i, s in enumerate(hybrid):
        if frozenset(s) in pool:
            collisions.append(i + 1)

    if collisions:
        if verbose:
            print(f"[hybrid] WARNING: collision on S{collisions} — signal set matches historical event.")
        # Fallback: return v2 at that slot, keep signal set position empty
        # In practice this is extremely rare; caller should handle
        raise ValueError(f"Signal set collision with historical event(s): S{collisions}")

    if verbose:
        print(f"[hybrid] All 8 sets novel. Done.")

    return hybrid, replaced_idx, signal_set


def get_prediction(series_id: int, verbose: bool = False) -> list[list[int]]:
    """
    Public API: return the 8-set hybrid family for series_id.
    Falls back to static v2 family if signal injection fails.
    """
    try:
        hybrid, _, _ = build_hybrid_family(series_id, verbose=verbose)
        return hybrid
    except Exception as e:
        import warnings
        warnings.warn(f"Hybrid signal injection failed ({e}); falling back to static v2 family.")
        return [list(s) for s in V2_FAMILY]


if __name__ == "__main__":
    try:
        sys.path.insert(0, str(ROOT / "ml_models"))
        from db_query import load_data, latest as _latest
        data = load_data()
        default_sid = _latest(data) + 1
    except Exception:
        default_sid = None

    sid = int(sys.argv[1]) if len(sys.argv) > 1 else default_sid
    if sid is None:
        print("Usage: python ml_models/hybrid_family_predictor.py <series_id>")
        sys.exit(1)

    print("=" * 78)
    print(f"HYBRID 8-FAMILY PREDICTION  —  Series {sid}")
    print("=" * 78)

    hybrid, replaced_idx, signal_set = build_hybrid_family(sid, verbose=True)

    print("\n8 PREDICTION SETS (v2 + E1 signal injection):")
    for i, s in enumerate(hybrid):
        tag = " <- E1 signal set" if i == replaced_idx else ""
        numbers = " ".join(f"{n:02d}" for n in s)
        print(f"  S{i+1}: {numbers}{tag}")

    print(f"\nReplaced: S{replaced_idx + 1} (static v2) -> signal-derived E1 set")
    print("=" * 78)
