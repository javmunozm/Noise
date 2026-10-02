"""PM Agent - production v2 status reporter.

Reads dbo.Draws via pyodbc; scores the static Designed 8-family v2 against
the OOS window (post-3192) and reports any-event vs E1-only performance.

Sub-second, ~15 lines of output. No JSON parsing, no V12 baseline loop,
no set-wins ASCII chart (v2 plays all 8 sets every draw — no per-set
selection happens, so set-wins is meaningless).

Usage:
  python ml_models/pm_agent.py report
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pyodbc

ROOT = Path(__file__).resolve().parents[1]
OOS_CACHE = ROOT / "ml_models" / "oos_cache.json"

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DESKTOP-QR14EDK\\SQLEXPRESS01;"
    "Database=LuckyDb;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

BSA_CONN_STR = (
    "Driver={ODBC Driver 18 for SQL Server};"
    "Server=DESKTOP-QR14EDK\\SQLEXPRESS01;"
    "Database=BsaDb;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

# Production: Designed 8-family v2 (frozen 2026-04-19).
# Mirrored in db_query.py and ml_models/designed_family_predictor.py.
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
V2_SETS = [frozenset(s) for s in V2_FAMILY]

OOS_START = 3193

# IID baselines for v2 (all 8 12+-neighborhoods provably disjoint, hence additive).
P_SET_EVENT_12 = 5160 / 4_457_400          # 0.001157  per (set, event), single trial
P_E1_12        = 8 * P_SET_EVENT_12         # 0.00926   per draw, 8 sets vs 1 event
P_ANY_12       = 1 - (1 - P_E1_12) ** 7     # 0.06280   per draw, 8 sets vs 7 events


def _conn():
    return pyodbc.connect(CONN_STR, readonly=True)


def _draws_with_events(cur, lo: int, hi: int) -> dict[int, list[tuple[int, frozenset[int]]]]:
    """Returns {DrawId: [(EventIndex, frozenset(numbers)), ...]} ordered by EventIndex."""
    cur.execute(
        "SELECT DrawId, EventIndex,"
        " N01,N02,N03,N04,N05,N06,N07,N08,N09,N10,N11,N12,N13,N14"
        " FROM dbo.Draws WHERE DrawId BETWEEN ? AND ?"
        " ORDER BY DrawId, EventIndex",
        (lo, hi),
    )
    out: dict[int, list[tuple[int, frozenset[int]]]] = {}
    for r in cur.fetchall():
        out.setdefault(r[0], []).append((r[1], frozenset(r[2:16])))
    return out


def _score_draw(events: list[tuple[int, frozenset[int]]]):
    """Return (per_event_best, per_event_idx, per_set_best, best, best_set_idx)."""
    n_ev = len(events)
    matrix = [[len(V2_SETS[si] & events[ei][1]) for ei in range(n_ev)] for si in range(8)]
    per_set_best = [max(r) for r in matrix]
    per_event_best = [max(matrix[si][ei] for si in range(8)) for ei in range(n_ev)]
    per_event_idx = [events[ei][0] for ei in range(n_ev)]
    best = max(per_event_best)
    best_set_idx = max(range(8), key=lambda si: per_set_best[si])
    return per_event_best, per_event_idx, per_set_best, best, best_set_idx


def report(cur):
    cur.execute(
        "SELECT COUNT(*), COUNT(DISTINCT DrawId), MIN(DrawId), MAX(DrawId),"
        " MIN(DrawDate), MAX(DrawDate) FROM dbo.Draws"
    )
    n_ev, n_dr, min_id, max_id, min_dt, max_dt = cur.fetchone()
    print(f"DATASET    draws={n_dr:,}  events={n_ev:,}  ids={min_id}..{max_id}  "
          f"latest={max_id} ({max_dt})")
    print(f"PRODUCTION Designed 8-family v2 (static, zero-variance, novelty-checked)")

    # Latest-draw scoring
    latest = _draws_with_events(cur, max_id, max_id).get(max_id, [])
    if latest:
        peb, pei, _, best, bsi = _score_draw(latest)
        avg = sum(peb) / len(peb)
        e_pos = peb.index(best)
        print(f"LATEST     {max_id} best={best}/14 (S{bsi+1} vs E{pei[e_pos]})  "
              f"evt_best={peb}  avg={avg:.2f}")

    oos = _draws_with_events(cur, OOS_START, max_id)
    oos_ids = sorted(oos.keys())
    n = len(oos_ids)
    if n == 0:
        print(f"\nOOS [{OOS_START}..{max_id}]: no draws")
        return

    any_12 = any_13 = e1_12 = e1_13 = 0
    any_seq: list[int] = []
    e1_seq: list[int] = []
    per_ei_count = {i: 0 for i in range(1, 8)}

    for did in oos_ids:
        peb, pei, _, best, _ = _score_draw(oos[did])
        any_seq.append(best)
        if best >= 12: any_12 += 1
        if best >= 13: any_13 += 1
        e1_score = next((peb[i] for i, ei in enumerate(pei) if ei == 1), 0)
        e1_seq.append(e1_score)
        if e1_score >= 12: e1_12 += 1
        if e1_score >= 13: e1_13 += 1
        for i, score in enumerate(peb):
            if score == best:
                per_ei_count[pei[i]] = per_ei_count.get(pei[i], 0) + 1

    # Dry streak from latest backward (counts trailing draws with no >=12)
    dry_any = next((i for i, b in enumerate(reversed(any_seq)) if b >= 12), n)
    dry_e1  = next((i for i, b in enumerate(reversed(e1_seq))  if b >= 12), n)
    p_any_dry = (1 - P_ANY_12) ** dry_any
    p_e1_dry  = (1 - P_E1_12)  ** dry_e1

    print()
    print(f"OOS [{OOS_START}..{max_id}]  n={n} series                  IID baseline")
    print(f"  any-event:  {any_12:2d}@12+ ({any_12/n*100:5.1f}%)  {any_13}@13+  dry={dry_any:2d}     "
          f"{P_ANY_12*100:.2f}% per draw")
    print(f"  E1 only:    {e1_12:2d}@12+ ({e1_12/n*100:5.1f}%)  {e1_13}@13+  dry={dry_e1:2d}     "
          f"{P_E1_12*100:.2f}% per draw  (KINO-only)")
    print()
    counts = "  ".join(f"E{i}={per_ei_count.get(i, 0)}" for i in range(1, 8))
    print(f"PER-EVENT BEST-HIT COUNTS (OOS, n={n}, ties counted on every winner)")
    print(f"  {counts}    (E2 = curated-only 'surplus' on post-3000 draws)")
    print()
    flag_any = "FLAG" if p_any_dry < 0.10 else "ok  "
    flag_e1  = "FLAG" if p_e1_dry  < 0.10 else "ok  "
    print(f"DRY-STREAK WATCH  (flag if streak P < 10% under IID)")
    print(f"  any-event {dry_any:3d} / {n:3d}  P={p_any_dry*100:5.1f}%   {flag_any}")
    print(f"  E1-only   {dry_e1:3d} / {n:3d}  P={p_e1_dry*100:5.1f}%   {flag_e1}")
    print()
    # --- E1 selector performance table ---
    e1_avg = sum(e1_seq) / n if n else 0.0
    e1_last = e1_seq[-1] if e1_seq else 0
    print(f"E1 SELECTOR PERFORMANCE (OOS from {OOS_START}, E1 only)")
    print(f"  {'Selector':<26}  {'sets':>4}  {'n':>4}  {'avg/set':>7}  {'edge':>6}  {'12+':>4}  {'last':>4}")
    print(f"  {'-'*65}")
    # Designed family: best-of-8 vs E1
    print(f"  {'Designed 8-family v2':<26}  {'8':>4}  {n:>4}  {e1_avg:>7.3f}  "
          f"{e1_avg-7.840:>+6.3f}  {e1_12:>4}  {e1_last:>4}")

    # Single-ticket predictors from cache.
    # The cache is only refreshed by each predictor's `--oos` run; it does NOT
    # auto-update when a new draw is ingested. Flag any row whose cached
    # last_sid lags the current dataset so stale numbers are never reported as
    # current (e.g. a cache scored through 3245 sitting next to a 3246 dataset).
    cache = json.loads(OOS_CACHE.read_text()) if OOS_CACHE.exists() else {}
    stale_rows: list[tuple[str, int]] = []
    for key, label, oos_cmd in [
        ("recurrence", "Recurrence (hl=26)",
         "python ml_models/recurrence_predictor.py --oos --from 3193"),
        ("force_evaluator", "Force (hl=20,bw=100,hyb)",
         "python ml_models/force_evaluator.py --oos --from 3193"),
    ]:
        c = cache.get(key)
        if c:
            lh = c.get("last_hits", "--")
            cached_sid = c.get("last_sid")
            stale = cached_sid is not None and cached_sid < max_id
            flag = f"  STALE@{cached_sid} (run --oos)" if stale else ""
            if stale:
                stale_rows.append((label, oos_cmd))
            print(f"  {label:<26}  {'1':>4}  {c['n']:>4}  {c['avg']:>7.3f}  "
                  f"{c['avg']-7.840:>+6.3f}  {c['hits12']:>4}  {lh:>4}{flag}")
        else:
            print(f"  {label:<26}  {'1':>4}  {'--':>4}  {'--':>7}  {'--':>6}  {'--':>4}  {'--':>4}  "
                  f"(run --oos to populate)")

    # BsaDb EWMA (live query)
    try:
        bcn = pyodbc.connect(BSA_CONN_STR, readonly=True)
        bcur = bcn.cursor()
        bcur.execute(
            "SELECT COUNT(*), AVG(CAST(SwapHits AS FLOAT)), "
            "SUM(CASE WHEN SwapHits>=12 THEN 1 ELSE 0 END), MIN(SeriesId), "
            "MAX(SwapHits) FROM bsa.swapper_hits WHERE SeriesId >= 3228"
        )
        bn, bavg, b12, blo, blast = bcur.fetchone()
        bcur.execute("SELECT TOP 1 SwapHits FROM bsa.swapper_hits ORDER BY SeriesId DESC")
        b_last_hits = bcur.fetchone()[0]
        bcn.close()
        if bn:
            print(f"  {'BsaDb EWMA':<26}  {'1':>4}  {bn:>4}  {bavg:>7.3f}  "
                  f"{bavg-7.840:>+6.3f}  {b12:>4}  {b_last_hits:>4}  (live, from {blo})")
        else:
            print(f"  {'BsaDb EWMA':<26}  no data yet")
    except Exception as exc:
        print(f"  {'BsaDb EWMA':<26}  (unavailable: {exc})")

    print(f"  {'IID baseline (1 set)':<26}  {'1':>4}  {n:>4}  {7.840:>7.3f}")
    print()
    if stale_rows:
        print(f"!! WARNING: {len(stale_rows)} selector row(s) are STALE "
              f"(cached OOS scored before draw {max_id}). Numbers above are NOT current.")
        for label, oos_cmd in stale_rows:
            print(f"     refresh {label}:  {oos_cmd}")
        print()
    print(f"NEXT  python ml_models/designed_family_predictor.py {max_id+1}")


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "report":
        print("usage: pm_agent.py report", file=sys.stderr)
        return 2
    cn = _conn()
    try:
        report(cn.cursor())
    finally:
        cn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
