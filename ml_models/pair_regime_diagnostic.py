"""
Pair Regime Diagnostic — combination likelihood evaluator.

For each predicted set, scores every pair inside it against the L20 E1
pair-frequency regime, then aggregates into a single combination likelihood
score for that set.  Output is a ranked table showing which sets are
riding the current regime and which are fighting it.

Usage:
    python ml_models/pair_regime_diagnostic.py [series_id] [--window N]

    series_id  : target draw (default: latest+1)
    --window N : regime window in E1 draws (default: 20)

Output sections:
    1. Regime map — all 300 pairs colour-coded HOT / WARM / NEUTRAL / COLD
    2. Per-set scores — each set's pair breakdown + overall regime score
    3. Ranking — sets ordered best-to-worst regime alignment
    4. Pair detail per set — every pair with its z-score and L20 count
"""
from __future__ import annotations

import math
import sys
from itertools import combinations
from pathlib import Path

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
NUM_COLS = "N01,N02,N03,N04,N05,N06,N07,N08,N09,N10,N11,N12,N13,N14"

P_PAIR = 14 * 13 / (25 * 24)   # ~0.3033 — baseline pair appearance rate per draw


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_e1_history(before_draw_id: int) -> list[list[int]]:
    cn = pyodbc.connect(CONN_STR, readonly=True)
    cur = cn.cursor()
    cur.execute(
        f"SELECT {NUM_COLS} FROM dbo.Draws "
        "WHERE DrawId < ? AND EventIndex = 1 ORDER BY DrawId ASC",
        (before_draw_id,)
    )
    rows = cur.fetchall()
    cn.close()
    return [sorted(int(x) for x in row) for row in rows]


# ---------------------------------------------------------------------------
# Pair regime computation
# ---------------------------------------------------------------------------

def pair_regime(e1_history: list[list[int]], window: int = 20) -> dict[tuple, dict]:
    """
    For every pair in C(25,2) compute:
        count   — appearances in last `window` E1 draws
        z       — z-score vs IID baseline
        rate    — empirical appearance rate in window
        label   — HOT / WARM / NEUTRAL / COLD / FROZEN
    """
    w = e1_history[-window:] if len(e1_history) >= window else e1_history
    n = len(w)

    counts: dict[tuple, int] = {}
    for draw in w:
        for a, b in combinations(draw, 2):
            key = (min(a, b), max(a, b))
            counts[key] = counts.get(key, 0) + 1

    exp = n * P_PAIR
    std = math.sqrt(n * P_PAIR * (1 - P_PAIR))

    result = {}
    for a in range(1, 26):
        for b in range(a + 1, 26):
            key = (a, b)
            c = counts.get(key, 0)
            z = (c - exp) / std if std > 0 else 0.0
            rate = c / n if n > 0 else 0.0
            if z >= 2.0:
                label = "HOT"
            elif z >= 0.75:
                label = "WARM"
            elif z <= -2.0:
                label = "FROZEN"
            elif z <= -0.75:
                label = "COLD"
            else:
                label = "NEUTRAL"
            result[key] = {"count": c, "z": z, "rate": rate, "label": label}

    return result


# ---------------------------------------------------------------------------
# Set scoring
# ---------------------------------------------------------------------------

def score_set(s: list[int], regime: dict[tuple, dict]) -> dict:
    """
    Aggregate regime score for a set.

    combination_score = mean z-score across all C(14,2)=91 pairs in the set.
    Also counts how many pairs fall into each label bucket.
    """
    pairs = [(min(a, b), max(a, b)) for a, b in combinations(s, 2)]
    z_values = [regime[p]["z"] for p in pairs]
    labels = [regime[p]["label"] for p in pairs]

    return {
        "n_pairs": len(pairs),
        "mean_z": sum(z_values) / len(z_values),
        "sum_z": sum(z_values),
        "HOT":     labels.count("HOT"),
        "WARM":    labels.count("WARM"),
        "NEUTRAL": labels.count("NEUTRAL"),
        "COLD":    labels.count("COLD"),
        "FROZEN":  labels.count("FROZEN"),
        "pairs": [(p, regime[p]) for p in pairs],
    }


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

LABEL_TAG = {
    "HOT":     "HOT   ",
    "WARM":    "warm  ",
    "NEUTRAL": "     .",
    "COLD":    "cold  ",
    "FROZEN":  "FROZEN",
}


def print_regime_map(regime: dict[tuple, dict], window: int) -> None:
    print(f"\n{'='*72}")
    print(f"PAIR REGIME MAP  (last {window} E1 draws)   baseline rate={P_PAIR:.3f}")
    print(f"{'='*72}")
    print(f"  Labels:  HOT z>=2.0  |  warm z>=0.75  |  . neutral  |  cold z<=-0.75  |  FROZEN z<=-2.0")
    print()

    # Group by first number for compact display
    for a in range(1, 26):
        parts = []
        for b in range(a + 1, 26):
            r = regime[(a, b)]
            if r["label"] in ("HOT", "FROZEN"):
                parts.append(f"{a:02d}-{b:02d}({r['label'][0]} z={r['z']:+.1f})")
        if parts:
            print(f"  {', '.join(parts)}")

    # Summary counts
    counts_by_label = {}
    for r in regime.values():
        counts_by_label[r["label"]] = counts_by_label.get(r["label"], 0) + 1
    print(f"\n  Pair counts: " + "  ".join(f"{k}={v}" for k, v in sorted(counts_by_label.items())))


def print_set_detail(idx: int, s: list[int], sc: dict, regime: dict) -> None:
    print(f"\n  S{idx+1}: {' '.join(f'{n:02d}' for n in s)}")
    print(f"       regime score={sc['mean_z']:+.3f}  "
          f"HOT={sc['HOT']}  warm={sc['WARM']}  .={sc['NEUTRAL']}  cold={sc['COLD']}  FROZEN={sc['FROZEN']}")

    # Show only non-neutral pairs
    notable = [(p, r) for p, r in sc["pairs"] if r["label"] != "NEUTRAL"]
    notable.sort(key=lambda x: -x[1]["z"])
    if notable:
        lines = []
        for p, r in notable:
            lines.append(f"{p[0]:02d}-{p[1]:02d}:{LABEL_TAG[r['label']].strip()}(z={r['z']:+.2f},n={r['count']})")
        # wrap at 3 per line
        for i in range(0, len(lines), 3):
            prefix = "       " if i > 0 else "       "
            print(f"{prefix}{',  '.join(lines[i:i+3])}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(series_id: int, window: int = 20) -> None:
    print(f"Loading E1 history before draw {series_id}...")
    e1_history = fetch_e1_history(series_id)
    print(f"Loaded {len(e1_history)} E1 draws.  Regime window = last {window}.")

    regime = pair_regime(e1_history, window=window)

    # Get prediction
    from designed_family_predictor import get_prediction
    family = get_prediction(series_id)

    # Score every set
    set_scores = [score_set(s, regime) for s in family]

    # --- Regime map (only HOT/FROZEN for brevity) ---
    print_regime_map(regime, window)

    # --- Ranked summary table ---
    ranked = sorted(range(len(family)), key=lambda i: -set_scores[i]["mean_z"])

    print(f"\n{'='*72}")
    print(f"SET RANKING BY REGIME ALIGNMENT  (series {series_id})")
    print(f"{'='*72}")
    print(f"  {'Rank':>4}  {'Set':>4}  {'Score':>7}  {'HOT':>4}  {'warm':>5}  {'neut':>5}  {'cold':>5}  {'FRZN':>5}  Numbers")
    for rank, i in enumerate(ranked, 1):
        s = family[i]
        sc = set_scores[i]
        nums = " ".join(f"{n:02d}" for n in s)
        print(f"  {rank:>4}  S{i+1:<3}  {sc['mean_z']:>+7.3f}  "
              f"{sc['HOT']:>4}  {sc['WARM']:>5}  {sc['NEUTRAL']:>5}  "
              f"{sc['COLD']:>5}  {sc['FROZEN']:>5}  {nums}")

    # --- Per-set pair detail ---
    print(f"\n{'='*72}")
    print(f"PER-SET PAIR DETAIL  (non-neutral pairs only)")
    print(f"{'='*72}")
    for i, (s, sc) in enumerate(zip(family, set_scores)):
        print_set_detail(i, s, sc, regime)

    # --- Hot pairs not covered by any set ---
    hot_pairs = [p for p, r in regime.items() if r["label"] == "HOT"]
    uncovered = []
    for p in hot_pairs:
        covered = any(p[0] in s and p[1] in s for s in family)
        if not covered:
            uncovered.append((p, regime[p]))
    if uncovered:
        print(f"\n  HOT pairs not covered by any set:")
        for p, r in sorted(uncovered, key=lambda x: -x[1]["z"]):
            print(f"    {p[0]:02d}-{p[1]:02d}  z={r['z']:+.2f}  count={r['count']}")
    else:
        print(f"\n  All HOT pairs are covered by at least one set.")

    # --- Frozen pairs carried by sets (waste) ---
    frozen_carried = []
    for p, r in regime.items():
        if r["label"] == "FROZEN":
            in_sets = [i+1 for i, s in enumerate(family) if p[0] in s and p[1] in s]
            if in_sets:
                frozen_carried.append((p, r, in_sets))
    if frozen_carried:
        print(f"\n  FROZEN pairs carried inside sets (regime misalignment):")
        for p, r, sets in sorted(frozen_carried, key=lambda x: x[1]["z"]):
            print(f"    {p[0]:02d}-{p[1]:02d}  z={r['z']:+.2f}  count={r['count']}  in S{sets}")

    print(f"\n{'='*72}")


if __name__ == "__main__":
    args = sys.argv[1:]
    window = 20
    series_id = None

    i = 0
    while i < len(args):
        if args[i] == "--window" and i + 1 < len(args):
            window = int(args[i + 1])
            i += 2
        else:
            series_id = int(args[i])
            i += 1

    if series_id is None:
        from db_query import load_data, latest
        data = load_data()
        series_id = latest(data) + 1

    run(series_id, window=window)
