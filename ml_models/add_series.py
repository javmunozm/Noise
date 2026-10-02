"""
Add a new Kino draw to both data files and refresh the filter pool.

Updates (atomically, in order):
  1. data/full_series_data.json  <- append {draw_id: [events]}  (legacy format)
  2. data/all_draws.json          <- append {draw_id: {date, events, sources:['curated']}}
     (or merge sources if draw already present)
  3. data/filter_pool.json        <- regenerated from all_draws.json

Usage:
  python ml_models/add_series.py <draw_id> <YYYY-MM-DD> \\
      "1,2,3,4,5,6,7,8,9,10,11,12,13,14" \\
      "1,3,5,..." ...

  # Or interactive (no events on CLI -> prompts one per line, blank to end):
  python ml_models/add_series.py <draw_id> <YYYY-MM-DD>

Validation:
  - date must be YYYY-MM-DD
  - each event must be 14 distinct ints in [1, 25]
  - draw_id must not already exist in full_series_data.json (bail unless --force)
"""
from __future__ import annotations
import json
import re
import sys
from datetime import date as _date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CURATED = ROOT / "data" / "full_series_data.json"
ALL = ROOT / "data" / "all_draws.json"
POOL = ROOT / "data" / "filter_pool.json"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_event(s: str) -> list[int]:
    nums = [int(x.strip()) for x in s.replace(";", ",").split(",") if x.strip()]
    if len(nums) != 14:
        raise ValueError(f"event must have 14 numbers, got {len(nums)}: {nums}")
    if len(set(nums)) != 14:
        raise ValueError(f"event has duplicates: {nums}")
    if min(nums) < 1 or max(nums) > 25:
        raise ValueError(f"event values must be in [1,25]: {nums}")
    return sorted(nums)


def read_events_from_cli(argv: list[str]) -> list[list[int]]:
    return [parse_event(a) for a in argv]


def read_events_interactive() -> list[list[int]]:
    print("Paste events, one per line (comma-separated, 14 ints in 1..25).")
    print("Press Enter on a blank line to finish.")
    out = []
    while True:
        try:
            line = input(f"event {len(out)+1}> ").strip()
        except EOFError:
            break
        if not line:
            break
        out.append(parse_event(line))
    if not out:
        raise SystemExit("no events provided")
    return out


def canonicalize_and_union(existing: list[list[int]], new: list[list[int]]) -> list[list[int]]:
    seen = set()
    out = []
    for e in list(existing) + list(new):
        key = tuple(sorted(int(x) for x in e))
        if key not in seen and len(key) == 14 and min(key) >= 1 and max(key) <= 25:
            seen.add(key)
            out.append(list(key))
    return out


def update_curated(draw_id: str, events: list[list[int]], force: bool) -> bool:
    data = json.load(open(CURATED))
    if draw_id in data and not force:
        print(f"[!] draw {draw_id} already present in {CURATED.name}. "
              f"Pass --force to overwrite.")
        return False
    data[draw_id] = [list(e) for e in events]
    json.dump(data, open(CURATED, "w"))
    return True


def update_all_draws(draw_id: str, date_str: str, events: list[list[int]]) -> dict:
    payload = json.load(open(ALL))
    draws = payload["draws"]
    rec = draws.get(draw_id, {"date": None, "events": [], "sources": []})
    rec["events"] = canonicalize_and_union(rec.get("events", []), events)
    if not rec.get("date"):
        rec["date"] = date_str
    if "curated" not in rec["sources"]:
        rec["sources"].append("curated")
    draws[draw_id] = rec

    # Refresh meta
    ordered = {k: draws[k] for k in sorted(draws, key=int)}
    total_events = sum(len(r["events"]) for r in ordered.values())
    payload["draws"] = ordered
    payload["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload["source_counts"]["unified_draws"] = len(ordered)
    payload["source_counts"]["unified_events"] = total_events
    json.dump(payload, open(ALL, "w"))
    return rec


def rebuild_filter_pool() -> tuple[int, int]:
    """Regenerate filter_pool.json from all_draws.json. Returns (input_events, union)."""
    unified = json.load(open(ALL))
    pool = set()
    input_events = 0
    for rec in unified["draws"].values():
        for e in rec["events"]:
            key = tuple(sorted(int(x) for x in e))
            if len(key) == 14 and len(set(key)) == 14 and min(key) >= 1 and max(key) <= 25:
                pool.add(key)
                input_events += 1
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": ALL.name,
        "source_counts": {"input_events": input_events, "union_unique": len(pool)},
        "events": sorted([list(e) for e in pool]),
    }
    json.dump(payload, open(POOL, "w"))
    return input_events, len(pool)


def main(argv):
    force = "--force" in argv
    argv = [a for a in argv if a != "--force"]
    if len(argv) < 2:
        print(__doc__)
        sys.exit(2)
    draw_id = str(int(argv[0]))
    date_str = argv[1]
    if not DATE_RE.match(date_str):
        raise SystemExit(f"date must be YYYY-MM-DD, got {date_str!r}")
    try:
        _date.fromisoformat(date_str)
    except ValueError as e:
        raise SystemExit(f"invalid date: {e}")

    events = read_events_from_cli(argv[2:]) if len(argv) > 2 else read_events_interactive()

    # 1. full_series_data.json
    if not update_curated(draw_id, events, force):
        sys.exit(1)
    # 2. all_draws.json
    rec = update_all_draws(draw_id, date_str, events)
    # 3. filter_pool.json
    input_events, union = rebuild_filter_pool()

    print(f"\n[ok] draw {draw_id} ({date_str}) added:")
    print(f"     {len(events)} events -> full_series_data.json")
    print(f"     all_draws.json now has {len(rec['events'])} events for this draw "
          f"(sources: {rec['sources']})")
    print(f"     filter_pool.json: {input_events} input events, {union} unique")


if __name__ == "__main__":
    main(sys.argv[1:])
