"""
tries_forecast.py — how many distinct tickets the next series will take to land 14/14.

"Tries" = distinct 14-of-25 tickets played, in any order, until one matches one of
the series' events exactly (see docs/sim_14of14_3168_3287.md).

The count cannot be predicted per series: over the last 120 series (3168..3287) the
lag-1..3 correlation of log(tries) was -0.11..+0.05 for the CB-pair system and
-0.01..+0.05 for random, and the system and random counts for the same series were
uncorrelated (r=-0.03). What IS exact is its distribution. With N = C(25,14) =
4,457,400 equally likely combinations and k distinct events per series:

    P(hit within t tickets) = 1 - C(N-t, k) / C(N, k)
    E[tries]                = (N + 1) / (k + 1)          (557,175 for k=7)

This holds for every ticket-ordering rule (random, CB pair, recurrence, ...), because
a uniformly random target is equally likely to sit at any position of any order.
Checked against the 120-series simulation: system mean 551,314 / median 397,219,
random mean 542,760 / median 390,354 vs theory 557,175 / 420,228.

Usage:
  python ml_models/tries_forecast.py                 # forecast for the next series (7 events)
  python ml_models/tries_forecast.py --tickets 60000 # chance a 60,000-ticket budget hits
  python ml_models/tries_forecast.py --events 1      # E1 only
"""
from __future__ import annotations

import argparse
from math import comb, exp, log

N = comb(25, 14)  # 4,457,400


def prob_hit_within(tickets: int, events: int = 7) -> float:
    """P(at least one of `events` distinct draws is matched within `tickets` distinct tickets)."""
    t = max(0, min(int(tickets), N))
    if t > N - events:
        return 1.0
    return 1.0 - exp(sum(log(N - t - i) - log(N - i) for i in range(events)))


def expected_tries(events: int = 7) -> float:
    """Mean number of distinct tickets until the first exact hit."""
    return (N + 1) / (events + 1)


def tries_quantile(q: float, events: int = 7) -> int:
    """Smallest ticket count t with P(hit within t) >= q."""
    lo, hi = 0, N
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if prob_hit_within(mid, events) >= q:
            hi = mid
        else:
            lo = mid
    return hi


def forecast(events: int = 7) -> dict:
    """Distribution of the next series' tries: mean, quantiles, hit chance at common budgets."""
    return {
        "events": events,
        "mean": expected_tries(events),
        "quantiles": {q: tries_quantile(q, events) for q in (0.10, 0.25, 0.50, 0.75, 0.90, 0.99)},
        "hit_within": {t: prob_hit_within(t, events)
                       for t in (1, 2, 1_000, 10_000, 60_000, 100_000, 1_000_000)},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--events", type=int, default=7, help="distinct events per series (default 7)")
    ap.add_argument("--tickets", type=int, help="report the hit chance for this ticket budget")
    args = ap.parse_args()

    f = forecast(args.events)
    print(f"Next series, {args.events} event(s): tries until 14/14 (any ticket order)")
    print(f"  mean  {f['mean']:>12,.0f}")
    for q, t in f["quantiles"].items():
        print(f"  {int(q * 100):>2}%   {t:>12,}   (chance it takes at most this many)")
    print("  hit chance by budget:")
    for t, p in f["hit_within"].items():
        print(f"    {t:>9,} tickets  {100 * p:9.4f}%")
    if args.tickets is not None:
        p = prob_hit_within(args.tickets, args.events)
        print(f"  budget {args.tickets:,}: {100 * p:.4f}% (1 in {1 / p:,.0f})" if p else
              f"  budget {args.tickets:,}: 0%")


if __name__ == "__main__":
    main()
