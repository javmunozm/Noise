"""
pick_v2_set.py — preference-based single-ticket selector from Designed Family v2.

**NOT A PREDICTION TOOL.** Under IID (confirmed, session 11), every v2 set has
identical P(>=12) = 6.28% against the next draw. This tool selects which of the
8 pre-designed sets best matches user preferences. The output is a *preference
match*, not a predicted-winner.

Why not just pick randomly? Because if you're going to play exactly one of the
8, you probably have a belief (numbers you like, numbers you hate, a pattern
preference). This tool encodes that belief into a score and picks the set that
matches it. No more, no less.

Supported preferences (combine as desired):
  --include  A,B,C        Sets MUST contain all of these numbers (hard filter)
  --prefer   A,B,C        Sets scored +1 per preferred number present (soft)
  --exclude  A,B,C        Sets MUST NOT contain any of these (hard filter)
  --avoid    A,B,C        Sets scored -1 per avoided number present (soft)
  --parity   odd|even|balanced
                          odd=more odd numbers, even=more even, balanced=closest to 7
  --match    <sid>        Score by max overlap with any event of draw <sid>
  --anti     <sid>        Score by MINIMUM overlap with any event of draw <sid>
                          (contrarian: "avoid numbers that just came up")
  --sum-range LOW,HIGH    Sets with sum in [LOW,HIGH] get +1 bonus
  --high-count N          Prefer sets with exactly/around N numbers in 13..25

Usage:
  python ml_models/pick_v2_set.py --include 7,11,14
  python ml_models/pick_v2_set.py --prefer 7 --avoid 1,25
  python ml_models/pick_v2_set.py --match 3216
  python ml_models/pick_v2_set.py --parity odd --sum-range 170,190

If no flags supplied, prints all 8 sets with their intrinsic properties and
explains that every set has identical P(>=12) under IID.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml_models"))
from designed_family_predictor import DESIGNED_FAMILY  # noqa: E402

FULL_SERIES = ROOT / "data" / "full_series_data.json"


def parse_int_list(s: str) -> list[int]:
    return sorted({int(x.strip()) for x in s.replace(";", ",").split(",") if x.strip()})


def load_events(sid: int) -> list[set[int]]:
    data = json.load(open(FULL_SERIES))
    key = str(sid)
    if key not in data:
        raise SystemExit(f"Series {sid} not found in {FULL_SERIES.name}")
    return [set(e) for e in data[key]]


def score_set(s: list[int], args, events_for_sid: list[set[int]] | None) -> dict:
    """Return a score dict for one v2 set."""
    S = set(s)
    score = 0.0
    reasons = []
    hard_fail = None

    if args.include:
        inc = set(args.include)
        if not inc <= S:
            missing = sorted(inc - S)
            hard_fail = f"missing required: {missing}"
        else:
            reasons.append(f"contains all required {sorted(inc)}")

    if args.exclude:
        exc = set(args.exclude)
        overlap = exc & S
        if overlap:
            hard_fail = (hard_fail + "; " if hard_fail else "") + f"contains forbidden: {sorted(overlap)}"

    if args.prefer:
        pref = set(args.prefer)
        hit = len(pref & S)
        score += hit
        reasons.append(f"prefer-match +{hit}")

    if args.avoid:
        av = set(args.avoid)
        hit = len(av & S)
        score -= hit
        if hit:
            reasons.append(f"avoid-hit -{hit}")

    if args.parity:
        odds = sum(1 for n in s if n % 2 == 1)
        if args.parity == "odd":
            pts = odds - 7
            score += pts
            reasons.append(f"odd-count {odds} ({pts:+.0f})")
        elif args.parity == "even":
            pts = (14 - odds) - 7
            score += pts
            reasons.append(f"even-count {14 - odds} ({pts:+.0f})")
        elif args.parity == "balanced":
            pts = -abs(odds - 7)
            score += pts
            reasons.append(f"odd-count {odds}, balance {pts:+.0f}")

    if args.sum_range:
        lo, hi = args.sum_range
        total = sum(s)
        if lo <= total <= hi:
            score += 1
            reasons.append(f"sum {total} in [{lo},{hi}] +1")
        else:
            reasons.append(f"sum {total} out of [{lo},{hi}]")

    if args.high_count is not None:
        highs = sum(1 for n in s if n >= 13)
        pts = -abs(highs - args.high_count)
        score += pts
        reasons.append(f"high-count {highs}, target {args.high_count} ({pts:+.0f})")

    if events_for_sid is not None:
        overlaps = [len(S & e) for e in events_for_sid]
        max_ov = max(overlaps)
        min_ov = min(overlaps)
        if args.match is not None:
            score += max_ov - 7  # center on IID expectation
            reasons.append(f"match-max overlap={max_ov}")
        if args.anti is not None:
            score += (14 - max_ov) - 7  # bonus for LOW max overlap with draw
            reasons.append(f"anti-max overlap={max_ov} (lower=better)")

    return {
        "set": s,
        "score": score,
        "reasons": reasons,
        "hard_fail": hard_fail,
    }


def intrinsic_props(s: list[int]) -> str:
    odds = sum(1 for n in s if n % 2 == 1)
    highs = sum(1 for n in s if n >= 13)
    return f"sum={sum(s):>3}  odds={odds:>2}  highs={highs:>2}  min={min(s)}  max={max(s)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--include", type=parse_int_list, help="MUST contain (hard filter)")
    ap.add_argument("--prefer", type=parse_int_list, help="Favor these (soft +1 each)")
    ap.add_argument("--exclude", type=parse_int_list, help="MUST NOT contain (hard filter)")
    ap.add_argument("--avoid", type=parse_int_list, help="Penalize these (soft -1 each)")
    ap.add_argument("--parity", choices=["odd", "even", "balanced"])
    ap.add_argument("--match", type=int, metavar="SID",
                    help="Score by max overlap with events of series SID")
    ap.add_argument("--anti", type=int, metavar="SID",
                    help="Score by MINIMUM overlap (contrarian)")
    ap.add_argument("--sum-range", type=lambda s: [int(x) for x in s.split(",")],
                    metavar="LOW,HIGH")
    ap.add_argument("--high-count", type=int, metavar="N",
                    help="Prefer sets with ~N numbers in 13..25")
    args = ap.parse_args()

    any_flag = any([args.include, args.prefer, args.exclude, args.avoid, args.parity,
                    args.match, args.anti, args.sum_range, args.high_count is not None])

    # Load events if --match or --anti
    events_for_sid = None
    sid_label = None
    if args.match is not None:
        events_for_sid = load_events(args.match)
        sid_label = f"{args.match} (match)"
    elif args.anti is not None:
        events_for_sid = load_events(args.anti)
        sid_label = f"{args.anti} (anti)"

    print("=" * 72)
    print("pick_v2_set.py -- preference-based selection from Designed Family v2")
    print("=" * 72)

    if not any_flag:
        print("\nNo preferences supplied. Here are all 8 sets with intrinsic properties:\n")
        for i, s in enumerate(DESIGNED_FAMILY):
            print(f"  S{i+1}: {s}  {intrinsic_props(s)}")
        print("\nUnder IID, each set has identical P(>=12) = 6.28% vs the next draw.")
        print("Pass a preference flag (--prefer / --avoid / --match / etc) to rank.")
        return

    results = []
    for i, s in enumerate(DESIGNED_FAMILY):
        r = score_set(s, args, events_for_sid)
        r["label"] = f"S{i+1}"
        results.append(r)

    passing = [r for r in results if r["hard_fail"] is None]
    failing = [r for r in results if r["hard_fail"] is not None]

    if not passing:
        print("\n[!] No v2 set satisfies the hard filters (--include / --exclude).")
        print("    All 8 hard-failed:")
        for r in failing:
            print(f"    {r['label']}: {r['hard_fail']}")
        return

    passing.sort(key=lambda r: -r["score"])
    top = passing[0]

    print("\nPreference flags:")
    for k, v in vars(args).items():
        if v and (not isinstance(v, list) or len(v) > 0):
            print(f"  --{k.replace('_','-'):<12} {v}")
    if sid_label:
        print(f"  (events loaded for series {sid_label}: {len(events_for_sid)} events)")

    print(f"\nRanked v2 sets (score high-to-low):")
    print(f"  {'Set':<4} {'Score':>7}   {'Sum':>4} {'Odd':>4} {'High':>5}    Reasons")
    print(f"  {'-'*4} {'-'*7}   {'-'*4} {'-'*4} {'-'*5}    {'-'*60}")
    for r in passing:
        s = r["set"]
        odds = sum(1 for n in s if n % 2 == 1)
        highs = sum(1 for n in s if n >= 13)
        marker = " <-- BEST" if r is top else ""
        print(f"  {r['label']:<4} {r['score']:>+7.1f}   {sum(s):>4} {odds:>4} {highs:>5}    "
              f"{'; '.join(r['reasons'])}{marker}")

    if failing:
        print(f"\nHard-filtered out:")
        for r in failing:
            print(f"  {r['label']}: {r['hard_fail']}")

    print("\n" + "=" * 72)
    print("PICK:")
    print("=" * 72)
    print(f"  {top['label']}: {top['set']}")
    print(f"  Score: {top['score']:+.1f}   {intrinsic_props(top['set'])}")

    print(f"\n[DISCLAIMER] This selects by preference fit, not win probability.")
    print(f"Under IID, every v2 set has identical P(>=12) = 6.28% against the next draw.")
    print(f"Playing all 8 sets is the only way to capitalize on v2's 100% 12+ ceiling.")


if __name__ == "__main__":
    main()
