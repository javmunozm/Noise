"""
Force Evaluator -- Recurrence-Ranking Engine via Beam-Search Cascade.

Architecture: chained funnel (beam search), NOT independent per-k silos.
  Level 1: rank singles by weighted frequency; keep top beam_width.
  Level k: extend each surviving (k-1)-combo by one new number;
           score, sort, keep top beam_width -> feeds level k+1.

Comparator:
  score(T)           = recency-weighted joint frequency of T
  retention(P, n)    = score(P + {n}) / score(P)   in [0,1]
  compare(A, B)      = ratio of scores

Ordering modes: "score" | "retention" | "hybrid"

Usage:
  python ml_models/force_evaluator.py <series_id> [--hl 26] [--bw 50] [--mode score]
  python ml_models/force_evaluator.py --oos [--from 3193] [--hl 26] [--bw 50]
"""
from __future__ import annotations

import json
import sys
import time
from math import comb
from pathlib import Path
from typing import Literal

import numpy as np
import pyodbc

ROOT = Path(__file__).resolve().parents[1]

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

OrderMode = Literal["score", "retention", "hybrid"]

DEFAULT_HALF_LIFE = 20
DEFAULT_BEAM_WIDTH = 100
DEFAULT_MODE: OrderMode = "hybrid"

REGISTRY_PATH = ROOT / "ml_models" / "force_evaluator_registry.json"
OOS_CACHE     = ROOT / "ml_models" / "oos_cache.json"


# ----------------------------------------------------------------
# Registry (anti-repetition)
# ----------------------------------------------------------------

def _load_registry(path: Path) -> list[frozenset]:
    if path.exists():
        raw = json.loads(path.read_text())
        return [frozenset(entry) for entry in raw]
    return []


def _save_registry(registry: list[frozenset], path: Path) -> None:
    path.write_text(json.dumps([sorted(s) for s in registry], indent=2))


# ----------------------------------------------------------------
# DB helpers
# ----------------------------------------------------------------

def _fetch_db_combos(up_to_sid: int | None = None) -> set[frozenset]:
    """Return all 14-number drawn sets from dbo.Draws (all events) with DrawId < up_to_sid.

    Unique validator: any generated ticket matching one of these was already
    drawn in the DB and must not be emitted. Collision probability ~1/1832;
    the guard is free and correct.
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
    conn = pyodbc.connect(CONN_STR, readonly=True)
    cur = conn.cursor()
    cur.execute(
        "SELECT DrawId, N01,N02,N03,N04,N05,N06,N07,N08,N09,N10,N11,N12,N13,N14 "
        "FROM dbo.Draws WHERE EventIndex=1 ORDER BY DrawId ASC"
    )
    rows = cur.fetchall()
    conn.close()
    sids, draws = [], []
    for r in rows:
        sid = int(r[0])
        if up_to_sid is not None and sid >= up_to_sid:
            continue
        sids.append(sid)
        draws.append([int(x) for x in r[1:15]])
    return sids, draws


# ----------------------------------------------------------------
# Core engine class
# ----------------------------------------------------------------

class ForceEvaluator:
    """Recurrence-ranking engine via beam-search cascade."""

    def __init__(
        self,
        half_life: float = DEFAULT_HALF_LIFE,
        beam_width: int = DEFAULT_BEAM_WIDTH,
        mode: OrderMode = DEFAULT_MODE,
        registry_path: Path = REGISTRY_PATH,
    ):
        self.half_life = half_life
        self.beam_width = beam_width
        self.mode = mode
        self.registry_path = registry_path

        self._draws: list[list[int]] = []
        self._weights: list[float] = []
        self._wfreq: dict[tuple[int, ...], float] = {}  # combo -> weighted freq
        self._fitted = False

    # ── Fit ──────────────────────────────────────────────────────

    def fit(self, draws: list[list[int]]) -> None:
        """Validate and load draws (oldest first). Precompute all weighted frequencies."""
        assert draws, "draws must be non-empty"
        for i, d in enumerate(draws):
            assert len(d) == DRAW_SIZE and len(set(d)) == DRAW_SIZE, \
                f"Draw {i}: expected {DRAW_SIZE} distinct numbers, got {d}"
            assert all(1 <= n <= 25 for n in d), \
                f"Draw {i}: numbers out of range 1..25: {d}"

        self._draws = draws
        N = len(draws)
        # age=0 for newest, age=N-1 for oldest
        self._weights = [0.5 ** ((N - 1 - i) / self.half_life) for i in range(N)]

        # Precompute weighted frequency for ALL combos of size 1..MAX_K
        # We only store combos that actually appeared (sparse dict).
        self._wfreq = {}
        for i, (draw, w) in enumerate(zip(draws, self._weights)):
            s = sorted(draw)
            # For each k, all C(14,k) sub-combos
            self._add_subsets(s, w)

        self._fitted = True

    def _add_subsets(self, draw_sorted: list[int], w: float) -> None:
        from itertools import combinations as combs
        for k in range(1, MAX_K + 1):
            for c in combs(draw_sorted, k):
                self._wfreq[c] = self._wfreq.get(c, 0.0) + w

    # ── Comparator ───────────────────────────────────────────────

    def score(self, combo: tuple[int, ...] | list[int]) -> float:
        """Recency-weighted joint frequency of this combination."""
        key = tuple(sorted(combo))
        return self._wfreq.get(key, 0.0)

    def retention(self, parent: tuple[int, ...], n: int) -> float:
        """Fraction of parent's recurrence preserved when adding n."""
        ps = self.score(parent)
        if ps <= 0:
            return 0.0
        child = tuple(sorted(list(parent) + [n]))
        return self.score(child) / ps

    def compare(self, a: tuple[int, ...], b: tuple[int, ...]) -> float:
        """score(a)/score(b). >1 means a is more recurrent."""
        sb = self.score(b)
        if sb <= 0:
            return float("inf") if self.score(a) > 0 else 1.0
        return self.score(a) / sb

    def _sort_key(self, combo: tuple[int, ...], parent: tuple[int, ...] | None = None) -> float:
        """Ordering key: higher = better. Negated for sort-ascending calls."""
        s = self.score(combo)
        if self.mode == "score":
            return s
        if self.mode == "retention":
            if parent is None or len(combo) == 1:
                return s  # level 1: no parent, fall back to score
            n = next(x for x in combo if x not in set(parent))
            return self.retention(parent, n)
        # hybrid: geometric mean of score and retention
        if parent is None or len(combo) == 1:
            return s
        n = next(x for x in combo if x not in set(parent))
        r = self.retention(parent, n)
        return (s * r) ** 0.5

    # ── Cascade (beam search) ─────────────────────────────────────

    def build_cascade(
        self,
        beam_width: int | None = None,
        exclude: set[int] | None = None,
    ) -> dict[int, list[tuple[tuple[int, ...], float]]]:
        """
        Chained funnel: level k extends survivors of level k-1.

        Returns dict {k: [(combo, score), ...]} for k=1..MAX_K (or until beam empty).
        Only combinations that actually occurred in history (score > 0) survive.
        """
        assert self._fitted, "call fit() first"
        bw = beam_width or self.beam_width
        exc = exclude or set()

        eligible = [n for n in POOL if n not in exc]
        levels: dict[int, list[tuple[tuple[int, ...], float]]] = {}

        # Level 1: rank singles by weighted frequency
        singles = [(tuple([n]), self.score((n,))) for n in eligible if self.score((n,)) > 0]
        singles.sort(key=lambda x: -x[1])
        beam = singles[:bw]
        if not beam:
            return levels
        levels[1] = beam

        # Levels 2..MAX_K: extend each survivor by one number
        for k in range(2, MAX_K + 1):
            candidates: dict[tuple[int, ...], tuple[float, tuple[int, ...]]] = {}
            # candidate -> (sort_key, parent)
            for parent_combo, _ in beam:
                parent_set = set(parent_combo)
                for n in eligible:
                    if n in parent_set:
                        continue
                    child = tuple(sorted(list(parent_combo) + [n]))
                    s = self.score(child)
                    if s <= 0:
                        continue  # never co-occurred -> prune
                    key = self._sort_key(child, parent_combo)
                    # Keep best sort_key if same child reached via multiple parents
                    if child not in candidates or key > candidates[child][0]:
                        candidates[child] = (key, parent_combo)

            if not candidates:
                break  # cascade stops: no co-occurrences at this depth

            # Sort by sort_key descending, keep top bw
            ranked = sorted(candidates.items(), key=lambda x: -x[1][0])[:bw]
            beam = [(combo, self.score(combo)) for combo, _ in ranked]
            levels[k] = beam

        return levels

    # ── Validation ───────────────────────────────────────────────

    def validate(self) -> dict:
        """Check counting identities and coherence metrics."""
        assert self._fitted, "call fit() first"
        total_w = sum(self._weights)
        report = {"total_weight": total_w, "checks": [], "passed": True}

        for k in range(1, MAX_K + 1):
            expected = total_w * comb(DRAW_SIZE, k)
            actual = sum(v for key, v in self._wfreq.items() if len(key) == k)
            diff = abs(actual - expected)
            ok = diff < 0.01
            msg = (
                f"k={k}: expected={expected:.4f} actual={actual:.4f} diff={diff:.6f} "
                + ("PASS" if ok else f"FAIL (diff={diff:.4f})")
            )
            report["checks"].append(msg)
            if not ok:
                report["passed"] = False

        # Coherence: rank-correlation between singles ranking and marginal from pairs
        from itertools import combinations as combs
        single_scores = {n: self.score((n,)) for n in POOL}
        # Marginal from pairs: for each n, sum of pair scores containing n
        pair_marginal = {n: 0.0 for n in POOL}
        for key, v in self._wfreq.items():
            if len(key) == 2:
                pair_marginal[key[0]] += v
                pair_marginal[key[1]] += v

        single_rank = sorted(POOL, key=lambda n: -single_scores[n])
        pair_rank = sorted(POOL, key=lambda n: -pair_marginal[n])
        sr = {n: i for i, n in enumerate(single_rank)}
        pr = {n: i for i, n in enumerate(pair_rank)}
        d2 = sum((sr[n] - pr[n]) ** 2 for n in POOL)
        n25 = 25
        rho = 1 - 6 * d2 / (n25 * (n25 ** 2 - 1))
        report["spearman_rho_single_vs_pair_marginal"] = round(rho, 4)
        report["checks"].append(f"Spearman rho (singles vs pair-marginal): {rho:.4f}")

        return report

    # ── Generate ─────────────────────────────────────────────────

    def generate(
        self,
        beam_width: int | None = None,
        update_registry: bool = True,
        db_combos: set[frozenset] | None = None,
    ) -> dict:
        """
        Run cascade, build Group A and B, enforce anti-repetition.

        Returns {"group_a": list, "group_b": list, "fourteen": list,
                 "score_a": float, "score_b": float, "levels": dict}
        """
        assert self._fitted, "call fit() first"
        bw = beam_width or self.beam_width

        levels = self.build_cascade(bw)
        if MAX_K not in levels or not levels[MAX_K]:
            # Cascade stopped early; use deepest available level
            deepest = max(levels.keys())
            # Pad best combo up to 7 with top influence numbers
            best = levels[deepest][0][0]
            extra = sorted(
                [n for n in POOL if n not in set(best)],
                key=lambda n: -self.score((n,)),
            )
            while len(best) < 7:
                best = tuple(sorted(list(best) + [extra.pop(0)]))
            group_a = list(best)
        else:
            group_a = list(levels[MAX_K][0][0])

        score_a = self.score(tuple(sorted(group_a)))

        # Group B: best 7-tuple disjoint from A
        exclude_a = set(group_a)
        levels_b = self.build_cascade(bw, exclude=exclude_a)

        if MAX_K in levels_b and levels_b[MAX_K]:
            group_b = list(levels_b[MAX_K][0][0])
        else:
            # Re-run excluding A's numbers; use deepest level or fill by score
            deepest_b = max(levels_b.keys()) if levels_b else 1
            if deepest_b in levels_b and levels_b[deepest_b]:
                best_b = levels_b[deepest_b][0][0]
            else:
                best_b = tuple()
            extra_b = sorted(
                [n for n in POOL if n not in exclude_a and n not in set(best_b)],
                key=lambda n: -self.score((n,)),
            )
            best_b = list(best_b)
            while len(best_b) < 7:
                best_b.append(extra_b.pop(0))
            group_b = best_b[:7]

        score_b = self.score(tuple(sorted(group_b)))
        fourteen = sorted(set(group_a) | set(group_b))
        assert len(fourteen) == 14, f"Union not 14: {fourteen}"

        # Anti-repetition enforcement: block ledger + DB drawn combos.
        # Only consult the on-disk registry when we are also going to write to
        # it (i.e. a live prediction). Backtests pass update_registry=False and
        # must NOT see tickets written by later live runs -- reading it there
        # leaks future state into the past and makes OOS unreproducible.
        registry = _load_registry(self.registry_path) if update_registry else []
        blocked = set(registry) | (db_combos or set())
        all_scores = {n: self.score((n,)) for n in POOL}
        attempts = 0
        while frozenset(fourteen) in blocked and attempts < 25:
            attempts += 1
            source = "registry" if frozenset(fourteen) in set(registry) else "DB"
            weakest = min(fourteen, key=lambda n: all_scores[n])
            candidates = sorted(
                [n for n in POOL if n not in set(fourteen)],
                key=lambda n: -all_scores[n],
            )
            if not candidates:
                break
            fourteen = sorted((set(fourteen) - {weakest}) | {candidates[0]})

        if update_registry:
            registry.append(frozenset(fourteen))
            _save_registry(registry, self.registry_path)

        return {
            "group_a": group_a,
            "group_b": group_b,
            "fourteen": fourteen,
            "score_a": score_a,
            "score_b": score_b,
            "levels": levels,
        }


# ----------------------------------------------------------------
# CLI helpers
# ----------------------------------------------------------------

def _run_for_sid(
    sid: int,
    half_life: float = DEFAULT_HALF_LIFE,
    beam_width: int = DEFAULT_BEAM_WIDTH,
    mode: OrderMode = DEFAULT_MODE,
    verbose: bool = True,
    update_registry: bool = True,
) -> list[int]:
    sids, draws = _fetch_e1(up_to_sid=sid)
    assert draws, f"No E1 draws found before sid={sid}"

    fe = ForceEvaluator(half_life=half_life, beam_width=beam_width, mode=mode)
    fe.fit(draws)

    t0 = time.time()
    db_combos = _fetch_db_combos(up_to_sid=sid)
    result = fe.generate(update_registry=update_registry, db_combos=db_combos)
    elapsed = time.time() - t0

    if verbose:
        levels = result["levels"]
        print(f"\nCascade survivors (beam_width={beam_width}, mode={mode}, hl={half_life}):")
        for k in sorted(levels.keys()):
            top = levels[k][0]
            print(f"  k={k}: best={list(top[0])}  score={top[1]:.4f}  "
                  f"(beam size={len(levels[k])})")

        print(f"\nGroup A (top 7-tuple):      {sorted(result['group_a'])}  score={result['score_a']:.4f}")
        print(f"Group B (best disjoint 7):  {sorted(result['group_b'])}  score={result['score_b']:.4f}")
        print(f"\n{'='*60}")
        print(f"FINAL TICKET -- series {sid}  (heuristic estimate, not a guarantee):")
        print(f"  {' '.join(f'{n:02d}' for n in result['fourteen'])}")
        print(f"{'='*60}")
        print(f"  elapsed: {elapsed:.1f}s  |  training draws: {len(draws)}")

        # Validation
        val = fe.validate()
        print(f"\nValidation: {'PASS' if val['passed'] else 'FAIL'}")
        for msg in val["checks"]:
            print(f"  {msg}")

    return result["fourteen"]


def oos_eval(
    oos_lo: int = 3193,
    half_life: float = DEFAULT_HALF_LIFE,
    beam_width: int = DEFAULT_BEAM_WIDTH,
    mode: OrderMode = DEFAULT_MODE,
) -> list[int]:
    # Get all E1 sids
    conn = pyodbc.connect(CONN_STR, readonly=True)
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT DrawId FROM dbo.Draws WHERE EventIndex=1 ORDER BY DrawId ASC"
    )
    all_sids = [int(r[0]) for r in cur.fetchall()]
    conn.close()

    targets = [s for s in all_sids if s >= oos_lo]
    print(f"OOS targets: {len(targets)} series ({targets[0]}..{targets[-1]})")
    print(f"  half_life={half_life}  beam_width={beam_width}  mode={mode}\n")

    # Pre-fetch all draws once
    sid_order_full, draws_full = _fetch_e1()
    sid_to_idx = {s: i for i, s in enumerate(sid_order_full)}

    # Pre-load all (DrawId, combo) rows once; grow the validator set incrementally
    # instead of re-querying dbo.Draws on every OOS iteration (was O(n^2) DB work).
    all_combos_by_sid = _fetch_db_combos_by_sid()
    combo_ptr = 0
    db_combos: set[frozenset] = set()

    scores_all = []
    hits11 = 0; hits12 = 0

    for k, sid in enumerate(targets):
        idx = sid_to_idx[sid]
        draws_train = draws_full[:idx]
        if not draws_train:
            continue

        fe = ForceEvaluator(half_life=half_life, beam_width=beam_width, mode=mode)
        fe.fit(draws_train)
        while combo_ptr < len(all_combos_by_sid) and all_combos_by_sid[combo_ptr][0] < sid:
            db_combos.add(all_combos_by_sid[combo_ptr][1])
            combo_ptr += 1
        result = fe.generate(update_registry=False, db_combos=db_combos)
        ticket = result["fourteen"]

        actual = set(draws_full[idx])
        h = len(set(ticket) & actual)
        scores_all.append(h)
        if h >= 12: hits12 += 1
        if h >= 11: hits11 += 1
        print(f"  [{k+1:3d}/{len(targets)}] sid={sid}  hits={h}/14  ticket={ticket}", flush=True)

    if not scores_all:
        print("No results.")
        return []

    n = len(scores_all)
    avg = sum(scores_all) / n
    print()
    print(f"{'='*60}")
    print(f"OOS E1 results (force-evaluator)  n={n}  ({targets[0]}..{targets[-1]})")
    print(f"  avg   = {avg:.3f}  (IID baseline 7.840)")
    print(f"  max   = {max(scores_all)}")
    print(f"  11+   = {hits11}/{n}  ({100*hits11/n:.1f}%)")
    print(f"  12+   = {hits12}/{n}  ({100*hits12/n:.1f}%)")
    print(f"  edge  = {avg - 7.840:+.3f} vs random")
    print(f"{'='*60}")

    cache = json.loads(OOS_CACHE.read_text()) if OOS_CACHE.exists() else {}
    cache["force_evaluator"] = {
        "oos_lo": oos_lo, "n": n, "avg": round(avg, 3),
        "max": int(max(scores_all)), "hits11": hits11, "hits12": hits12,
        "last_sid": int(targets[-1]), "last_hits": int(scores_all[-1]),
        "params": {"hl": half_life, "bw": beam_width, "mode": mode},
    }
    OOS_CACHE.write_text(json.dumps(cache, indent=2))

    return scores_all


# ----------------------------------------------------------------
# Demo block
# ----------------------------------------------------------------

def _demo() -> None:
    import random as rnd
    rnd.seed(42)
    # Biased synthetic history: numbers 1-14 appear ~2x as often
    biased_pool = list(range(1, 15)) * 2 + list(range(15, 26))
    history = []
    for _ in range(200):
        draw = sorted(rnd.sample(biased_pool, 14))
        # Deduplicate in case of collision from biased sampling
        while len(set(draw)) < 14:
            draw = sorted(rnd.sample(biased_pool, 14))
        history.append(list(dict.fromkeys(draw))[:14])  # ensure 14 distinct
        # Rebuild if needed
        if len(set(history[-1])) < 14:
            history[-1] = sorted(rnd.sample(range(1, 26), 14))

    fe = ForceEvaluator(half_life=26, beam_width=30, mode="score")
    fe.fit(history)

    print("=== DEMO: Cascade survivors ===")
    levels = fe.build_cascade()
    for k in sorted(levels.keys()):
        top = levels[k][0]
        print(f"  k={k}: best={list(top[0])}  score={top[1]:.4f}")

    print("\n=== Retention comparator example ===")
    if 2 in levels and levels[2]:
        parent = levels[1][0][0]
        # Show retention for adding each number to parent
        retentions = [
            (n, fe.retention(parent, n))
            for n in POOL if n not in set(parent)
        ]
        retentions.sort(key=lambda x: -x[1])
        print(f"  Parent: {list(parent)}  score={fe.score(parent):.4f}")
        print(f"  Top-5 extensions by retention:")
        for n, r in retentions[:5]:
            child = tuple(sorted(list(parent) + [n]))
            print(f"    +{n} -> retention={r:.4f}  child_score={fe.score(child):.4f}")

    print("\n=== compare(A, B) example ===")
    if 2 in levels and len(levels[2]) >= 2:
        a, b = levels[2][0][0], levels[2][1][0]
        ratio = fe.compare(a, b)
        print(f"  compare({list(a)}, {list(b)}) = {ratio:.4f}  (A is {ratio:.2f}x more recurrent)")

    print("\n=== Validation ===")
    val = fe.validate()
    for msg in val["checks"]:
        print(f"  {msg}")

    print("\n=== Generate twice (second must differ) ===")
    fe2 = ForceEvaluator(half_life=26, beam_width=30, mode="score",
                         registry_path=Path("demo_registry.json"))
    fe2.fit(history)
    r1 = fe2.generate()
    r2 = fe2.generate()
    print(f"  First:  {r1['fourteen']}")
    print(f"  Second: {r2['fourteen']}")
    print(f"  Differ: {r1['fourteen'] != r2['fourteen']}")
    # Cleanup demo registry
    Path("demo_registry.json").unlink(missing_ok=True)


# ----------------------------------------------------------------
# CLI entry point
# ----------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]

    if "--demo" in args:
        _demo()
        return

    if "--oos" in args:
        lo = int(args[args.index("--from") + 1]) if "--from" in args else 3193
        hl = float(args[args.index("--hl") + 1]) if "--hl" in args else DEFAULT_HALF_LIFE
        bw = int(args[args.index("--bw") + 1]) if "--bw" in args else DEFAULT_BEAM_WIDTH
        mo = args[args.index("--mode") + 1] if "--mode" in args else DEFAULT_MODE
        oos_eval(lo, hl, bw, mo)
        return

    sid_args = [a for a in args if not a.startswith("--")]
    if not sid_args:
        print("Usage: python ml_models/force_evaluator.py <series_id> [--hl 26] [--bw 50] [--mode score]")
        print("       python ml_models/force_evaluator.py --oos [--from 3193] [--hl 26] [--bw 50] [--mode score]")
        print("       python ml_models/force_evaluator.py --demo")
        sys.exit(1)

    sid = int(sid_args[0])
    hl = float(args[args.index("--hl") + 1]) if "--hl" in args else DEFAULT_HALF_LIFE
    bw = int(args[args.index("--bw") + 1]) if "--bw" in args else DEFAULT_BEAM_WIDTH
    mo = args[args.index("--mode") + 1] if "--mode" in args else DEFAULT_MODE

    print("Loading history...", flush=True)
    _run_for_sid(sid, half_life=hl, beam_width=bw, mode=mo)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        _demo()
    else:
        main()
