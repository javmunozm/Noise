"""
Parameter sweep for force_evaluator beam-search cascade.

Tests all combinations of:
  half_life  : [10, 20, 26, 40, 60, 100]
  beam_width : [20, 50, 100, 200]
  mode       : ["score", "retention", "hybrid"]

Prints a ranked results table at the end.
Usage: python ml_models/sweep_force_evaluator.py [--from 3193]
"""
from __future__ import annotations
import sys
import time
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml_models"))

import pyodbc
from force_evaluator import ForceEvaluator, _fetch_e1, _fetch_db_combos

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DESKTOP-QR14EDK\\SQLEXPRESS01;"
    "Database=LuckyDb;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

HALF_LIFES  = [10, 20, 26, 40, 60, 100]
BEAM_WIDTHS = [20, 50, 100, 200]
MODES       = ["score", "retention", "hybrid"]
IID         = 7.840


def main():
    args = sys.argv[1:]
    oos_lo = int(args[args.index("--from") + 1]) if "--from" in args else 3193

    # Fetch all E1 sid list
    conn = pyodbc.connect(CONN_STR, readonly=True)
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT DrawId FROM dbo.Draws WHERE EventIndex=1 ORDER BY DrawId ASC"
    )
    all_sids = [int(r[0]) for r in cur.fetchall()]
    conn.close()

    targets = [s for s in all_sids if s >= oos_lo]
    n = len(targets)
    print(f"OOS window: {targets[0]}..{targets[-1]}  n={n}")
    print(f"Configs: {len(HALF_LIFES)*len(BEAM_WIDTHS)*len(MODES)}")
    print()

    # Pre-load all E1 draws and db_combos once per target
    sid_order_full, draws_full = _fetch_e1()
    sid_to_idx = {s: i for i, s in enumerate(sid_order_full)}

    # Cache db_combos per sid (each call is a DB query — batch them)
    print("Pre-fetching DB combos for each target...", flush=True)
    db_combos_cache: dict[int, set] = {}
    for sid in targets:
        db_combos_cache[sid] = _fetch_db_combos(up_to_sid=sid)
    print(f"  done ({len(targets)} targets cached)\n")

    configs = list(product(HALF_LIFES, BEAM_WIDTHS, MODES))
    results = []

    t_sweep_start = time.time()
    for cfg_i, (hl, bw, mode) in enumerate(configs):
        hits_all = []
        t0 = time.time()

        for sid in targets:
            idx = sid_to_idx[sid]
            draws_train = draws_full[:idx]
            if not draws_train:
                continue

            fe = ForceEvaluator(half_life=hl, beam_width=bw, mode=mode)
            fe.fit(draws_train)
            res = fe.generate(update_registry=False, db_combos=db_combos_cache[sid])
            ticket = res["fourteen"]

            actual = set(draws_full[idx])
            hits_all.append(len(set(ticket) & actual))

        avg      = sum(hits_all) / len(hits_all)
        mx       = max(hits_all)
        h11      = sum(1 for h in hits_all if h >= 11)
        h12      = sum(1 for h in hits_all if h >= 12)
        last10   = sum(hits_all[-10:]) / 10
        last5    = sum(hits_all[-5:]) / 5
        edge     = avg - IID
        elapsed  = time.time() - t0

        results.append((avg, edge, last10, last5, h11, h12, mx, hl, bw, mode, hits_all))
        eta = (time.time() - t_sweep_start) / (cfg_i + 1) * (len(configs) - cfg_i - 1)
        print(
            f"[{cfg_i+1:3d}/{len(configs)}] hl={hl:3d} bw={bw:3d} mode={mode:<9} "
            f"avg={avg:.3f} edge={edge:+.3f} last10={last10:.3f} last5={last5:.3f} "
            f"11+={h11} max={mx}  ({elapsed:.1f}s, ETA {eta:.0f}s)",
            flush=True,
        )

    # ── Results table ──────────────────────────────────────────
    results.sort(key=lambda x: -x[0])  # sort by avg descending

    print(f"\n{'='*95}")
    print(f"SWEEP RESULTS — ranked by full-OOS avg  (n={n}, IID={IID})")
    print(f"{'='*95}")
    print(f"  {'Rank':>4}  {'hl':>4}  {'bw':>4}  {'mode':<9}  "
          f"{'avg':>7}  {'edge':>7}  {'last10':>7}  {'last5':>7}  {'11+':>4}  {'12+':>4}  {'max':>4}")
    print(f"  {'-'*88}")
    for rank, (avg, edge, last10, last5, h11, h12, mx, hl, bw, mode, _) in enumerate(results, 1):
        print(f"  {rank:>4}  {hl:>4}  {bw:>4}  {mode:<9}  "
              f"{avg:>7.3f}  {edge:>+7.3f}  {last10:>7.3f}  {last5:>7.3f}  "
              f"{h11:>4}  {h12:>4}  {mx:>4}")

    # ── Best per mode ──────────────────────────────────────────
    print(f"\nBest per mode:")
    for mode in MODES:
        best = max((r for r in results if r[9] == mode), key=lambda x: x[0])
        print(f"  {best[9]:<9}  hl={best[7]:3d}  bw={best[8]:3d}  avg={best[0]:.3f}  edge={best[1]:+.3f}")

    # ── Best per beam_width ────────────────────────────────────
    print(f"\nBest per beam_width:")
    for bw in BEAM_WIDTHS:
        best = max((r for r in results if r[8] == bw), key=lambda x: x[0])
        print(f"  bw={best[8]:3d}  hl={best[7]:3d}  mode={best[9]:<9}  avg={best[0]:.3f}  edge={best[1]:+.3f}")

    # ── Stability: how many configs beat IID? ─────────────────
    above_iid = sum(1 for r in results if r[0] > IID)
    print(f"\nConfigs above IID ({IID}): {above_iid}/{len(results)} ({100*above_iid/len(results):.0f}%)")
    print(f"Configs above frozen (7.976): {sum(1 for r in results if r[0] > 7.976)}/{len(results)}")
    print(f"Configs above default (8.098): {sum(1 for r in results if r[0] > 8.098)}/{len(results)}")

    total_elapsed = time.time() - t_sweep_start
    print(f"\nTotal sweep time: {total_elapsed:.0f}s")


if __name__ == "__main__":
    main()
