"""Terse read-only query helper over dbo.Draws.

Usage:
  python ml_models/db_query.py stats
  python ml_models/db_query.py get 3217
  python ml_models/db_query.py latest 5
  python ml_models/db_query.py range 3210 3217
  python ml_models/db_query.py pool --count
  python ml_models/db_query.py pool --all
  python ml_models/db_query.py novelty "4,5,6,7,8,9,11,16,18,19,20,21,22,25"
  python ml_models/db_query.py score 3217 "4,5,6,7,8,9,11,16,18,19,20,21,22,25"
  python ml_models/db_query.py score-family 3217
  python ml_models/db_query.py has-draw 3217
  python ml_models/db_query.py count-by-year

All outputs designed to be terse -> pipe-friendly -> cheap for LLM context.
"""
from __future__ import annotations

import argparse
import sys
from typing import Iterable

import pyodbc

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DESKTOP-QR14EDK\\SQLEXPRESS01;"
    "Database=LuckyDb;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

NUM_COLS = "N01,N02,N03,N04,N05,N06,N07,N08,N09,N10,N11,N12,N13,N14"

# Production: Designed 8-family v2 (hardcoded, data-independent)
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


def _conn():
    return pyodbc.connect(CONN_STR, readonly=True)


# ---------- compat shims (ported from production_predictor.py 2026-04-25) ----------
# Used by designed_family_predictor.py for default-SID lookup. Prefer the DB
# subcommands above for new code.

def load_data() -> dict:
    """Merged historical + curated series data as {sid: [events]}, JSON-sourced."""
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    data: dict = {}
    for name in ("historical_series_data.json", "full_series_data.json"):
        p = root / "data" / name
        if p.exists():
            data.update(json.loads(p.read_text()))
    if not data:
        raise FileNotFoundError("no series data found in data/")
    return data


def latest(data: dict) -> int:
    """Max series id in a series data dict."""
    return max(int(s) for s in data.keys())


def _parse_set(s: str) -> list[int]:
    nums = [int(x.strip()) for x in s.replace(";", ",").split(",") if x.strip()]
    if len(nums) != 14:
        raise SystemExit(f"expected 14 numbers, got {len(nums)}")
    if len(set(nums)) != 14 or min(nums) < 1 or max(nums) > 25:
        raise SystemExit("numbers must be 14 distinct ints in [1,25]")
    return sorted(nums)


def _event_rows(cur, where: str = "", params: Iterable = ()) -> list[tuple[int, int, tuple[int, ...]]]:
    sql = f"SELECT DrawId, EventIndex, {NUM_COLS} FROM dbo.Draws"
    if where:
        sql += " WHERE " + where
    sql += " ORDER BY DrawId, EventIndex"
    cur.execute(sql, tuple(params))
    out = []
    for row in cur.fetchall():
        out.append((row[0], row[1], tuple(row[2:16])))
    return out


# ---------- commands ----------

def cmd_stats(args, cur):
    cur.execute("""
      SELECT COUNT(*)            AS events,
             COUNT(DISTINCT DrawId) AS draws,
             MIN(DrawId), MAX(DrawId),
             MIN(DrawDate), MAX(DrawDate)
        FROM dbo.Draws
    """)
    r = cur.fetchone()
    print(f"events={r[0]:,} draws={r[1]:,} ids={r[2]}..{r[3]} dates={r[4]}..{r[5]}")


def cmd_get(args, cur):
    rows = _event_rows(cur, "DrawId = ?", (args.draw_id,))
    if not rows:
        print(f"no draw {args.draw_id}")
        return
    cur.execute("SELECT TOP 1 DrawDate FROM dbo.Draws WHERE DrawId = ?", (args.draw_id,))
    date = cur.fetchone()[0]
    print(f"{args.draw_id} {date} {len(rows)}events")
    for _, ei, nums in rows:
        print(f"  E{ei}: {','.join(f'{n:02d}' for n in nums)}")


def cmd_latest(args, cur):
    cur.execute("""
      SELECT TOP (?) DrawId, DrawDate, COUNT(*) AS nev
        FROM dbo.Draws
       GROUP BY DrawId, DrawDate
       ORDER BY DrawId DESC
    """, (args.n,))
    for r in cur.fetchall():
        print(f"{r[0]} {r[1]} {r[2]}ev")


def cmd_range(args, cur):
    rows = _event_rows(cur, "DrawId BETWEEN ? AND ?", (args.lo, args.hi))
    print(f"range {args.lo}..{args.hi}: {len(rows)} events")
    if args.verbose:
        for did, ei, nums in rows:
            print(f"  {did}/E{ei}: {','.join(f'{n:02d}' for n in nums)}")


def cmd_pool(args, cur):
    rows = _event_rows(cur)
    uniq = {nums for _, _, nums in rows}
    if args.count or not args.all:
        print(f"total_events={len(rows):,} unique={len(uniq):,}")
    if args.all:
        for nums in sorted(uniq):
            print(",".join(f"{n:02d}" for n in nums))


def cmd_novelty(args, cur):
    target = tuple(_parse_set(args.numbers))
    # Build WHERE against each slot sorted
    conds = " AND ".join(f"N{i+1:02d} = ?" for i in range(14))
    cur.execute(f"SELECT TOP 1 DrawId, EventIndex FROM dbo.Draws WHERE {conds}", target)
    hit = cur.fetchone()
    if hit:
        print(f"COLLISION draw={hit[0]} event=E{hit[1]}")
    else:
        print(f"NOVEL (no match in dbo.Draws)")


def _score_one(candidate: list[int], events: list[tuple[int, int, tuple[int, ...]]]) -> list[tuple[int, int, int]]:
    cs = set(candidate)
    return [(did, ei, len(cs & set(nums))) for did, ei, nums in events]


def cmd_score(args, cur):
    cand = _parse_set(args.numbers)
    events = _event_rows(cur, "DrawId = ?", (args.draw_id,))
    if not events:
        print(f"no draw {args.draw_id}")
        return
    hits = _score_one(cand, events)
    print(f"draw={args.draw_id} best={max(h[2] for h in hits)}")
    for did, ei, k in hits:
        print(f"  E{ei}: {k}/14")


def cmd_score_family(args, cur):
    events = _event_rows(cur, "DrawId = ?", (args.draw_id,))
    if not events:
        print(f"no draw {args.draw_id}")
        return
    n_ev = len(events)
    # matrix[set_idx][event_idx] = overlap
    matrix = [[0] * n_ev for _ in range(len(V2_FAMILY))]
    for si, s in enumerate(V2_FAMILY):
        cs = set(s)
        for ei_idx, (_, _, nums) in enumerate(events):
            matrix[si][ei_idx] = len(cs & set(nums))
    per_set_best = [max(r) for r in matrix]
    per_event_best = [max(matrix[si][ei_idx] for si in range(len(V2_FAMILY)))
                      for ei_idx in range(n_ev)]
    best = max(per_event_best)
    print(f"draw={args.draw_id} best={best}/14 "
          f"set_best={per_set_best} evt_best={per_event_best} "
          f"avg={sum(per_event_best)/n_ev:.2f}")


def cmd_score_matrix(args, cur):
    """Print full 8×n_events match matrix for a draw.
    Rows = sets (S1..S8), Cols = events (E1..E7).
    Optionally uses the hybrid family for the given series_id.
    """
    events = _event_rows(cur, "DrawId = ?", (args.draw_id,))
    if not events:
        print(f"no draw {args.draw_id}")
        return
    n_ev = len(events)

    family = V2_FAMILY
    label = "v2"
    if args.hybrid:
        try:
            import sys, os
            sys.path.insert(0, os.path.dirname(__file__))
            from hybrid_family_predictor import build_hybrid_family
            family, replaced_idx, _ = build_hybrid_family(args.draw_id, verbose=False)
            label = f"hybrid(S{replaced_idx+1}->signal)"
        except Exception as e:
            print(f"[warn] hybrid load failed: {e}; using v2")

    matrix = [[0] * n_ev for _ in range(len(family))]
    for si, s in enumerate(family):
        cs = set(s)
        for ei_idx, (_, _, nums) in enumerate(events):
            matrix[si][ei_idx] = len(cs & set(nums))

    header = "        " + "  ".join(f"E{ei+1:1d}" for ei in range(n_ev))
    print(f"draw={args.draw_id} family={label}")
    print(header)
    for si, row in enumerate(matrix):
        cells = "  ".join(f"{v:2d}" for v in row)
        best = max(row)
        print(f"  S{si+1}:  {cells}  | best={best}")
    per_event_best = [max(matrix[si][ei] for si in range(len(family))) for ei in range(n_ev)]
    print("  max: " + "  ".join(f"{v:2d}" for v in per_event_best))
    print(f"  E1_best={per_event_best[0]}  overall_best={max(per_event_best)}")


def cmd_has_draw(args, cur):
    cur.execute("SELECT COUNT(*) FROM dbo.Draws WHERE DrawId = ?", (args.draw_id,))
    n = cur.fetchone()[0]
    print(f"{args.draw_id}: {n} events" if n else f"{args.draw_id}: missing")


def cmd_count_by_year(args, cur):
    cur.execute("""
      SELECT YEAR(DrawDate) AS y, COUNT(DISTINCT DrawId) AS d, COUNT(*) AS e
        FROM dbo.Draws
       WHERE DrawDate IS NOT NULL
       GROUP BY YEAR(DrawDate)
       ORDER BY y
    """)
    for r in cur.fetchall():
        print(f"{r[0]} draws={r[1]} events={r[2]}")


# ---------- dispatch ----------

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats")

    p = sub.add_parser("get"); p.add_argument("draw_id", type=int)
    p = sub.add_parser("latest"); p.add_argument("n", type=int, nargs="?", default=5)
    p = sub.add_parser("range")
    p.add_argument("lo", type=int); p.add_argument("hi", type=int)
    p.add_argument("-v", "--verbose", action="store_true")

    p = sub.add_parser("pool")
    p.add_argument("--count", action="store_true")
    p.add_argument("--all", action="store_true")

    p = sub.add_parser("novelty"); p.add_argument("numbers")
    p = sub.add_parser("score")
    p.add_argument("draw_id", type=int); p.add_argument("numbers")
    p = sub.add_parser("score-family"); p.add_argument("draw_id", type=int)
    p = sub.add_parser("score-matrix")
    p.add_argument("draw_id", type=int)
    p.add_argument("--hybrid", action="store_true", help="use hybrid family for this draw_id")
    p = sub.add_parser("has-draw"); p.add_argument("draw_id", type=int)
    sub.add_parser("count-by-year")

    args = ap.parse_args(argv)
    cn = _conn(); cur = cn.cursor()
    try:
        fn = {
            "stats": cmd_stats, "get": cmd_get, "latest": cmd_latest,
            "range": cmd_range, "pool": cmd_pool, "novelty": cmd_novelty,
            "score": cmd_score, "score-family": cmd_score_family,
            "score-matrix": cmd_score_matrix,
            "has-draw": cmd_has_draw, "count-by-year": cmd_count_by_year,
        }[args.cmd]
        fn(args, cur)
    finally:
        cn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
