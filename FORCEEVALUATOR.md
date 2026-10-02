# Prompt to generate the engine

Build a model fro this Python program that analyzes a
history of draws in which each draw consists of **14 distinct numbers** drawn from a
**pool of 1 to 25**.

This is not an exact-prediction system: it is a **recurrence-ranking engine**. Treat it
strictly as that, and do not associate it with any other context.

## Core rule: cascade, NOT independent silos

The single most important requirement: **do not rank the combination sizes separately.**
I don't want seven disconnected rankings (one for singles, one for pairs, etc.) computed
flatly and in isolation. I want a **chained funnel** in which each level is built from the
winners of the previous level:

```
singles (k=1) -> pairs (k=2) -> triples (k=3) -> ... -> 7-tuples (k=7)
```

Implement it as a **beam search**:

1. Level 1: rank the individual numbers by their weighted frequency. Keep the top
   `beam_width` as the seed.
2. For each level k from 2 to 7: take each surviving combination from level k-1 and try
   to **extend it by adding one number** it does not already contain. Score each
   resulting candidate with the comparator (see below), sort, and keep only the top
   `beam_width`. Those are the ones allowed to grow into the next level.
3. If at any level no combinations remain that actually occurred in the history, the
   cascade stops there.

`k` must NOT be treated as a fixed, self-contained loop over 1..7; it must be a
progression in which the output of one level feeds the next.

## Comparator between combinations

Explicitly define how two combinations are compared against each other:

- `score(T)` = the **recency-weighted joint frequency** of combination T (how often ALL
  members of T co-occur together in the history, giving more weight to recent draws). It
  is the common unit that lets you compare pair against pair, triple against triple, etc.
- `retention(parent, n)` = `score(parent + {n}) / score(parent)`, a value in [0,1]. It is
  the fraction of the parent's recurrence that is preserved when adding `n`. This is the
  piece that **connects one level to the next**: the cascade should preferentially extend
  by the number that best retains the parent's recurrence.
- `compare(A, B)` = a function returning which combination is more recurrent and by what
  factor (ratio of scores).

The cascade's ordering must be **configurable** among three modes: by `score` (raw
frequency), by `retention` (internal cohesion), or a weighted hybrid of both. Default:
by `score`.

## Recency weighting

More recent draws must weigh more. Use **exponential decay** with a configurable
`half_life` expressed in number of draws:

```
weight(age) = 0.5 ** (age / half_life)
```

where `age = 0` is the most recent draw. A small `half_life` makes recent data dominate
more. All frequency accumulation must use these weights.

## Validation

Include a validation function that checks exact counting identities: for each k, the sum
of all weighted frequencies must equal `total_weight * C(14, k)`. If any count does not
match, it must be reported. Also report a coherence metric (e.g. rank correlation between
the singles ranking and the marginal derived from the pairs).

## Two groups of 7 and the final 14

From the cascade:

- **Group A** = the best 7-tuple produced by the funnel.
- **Group B** = the best 7-tuple **disjoint** from A. If the beam contains no disjoint
  one, re-run the cascade excluding A's numbers and take the best.
- The **final 14** = the union of A and B. Since they are disjoint, they yield exactly 14
  with no artificial filler.

## Anti-repetition constraint (mandatory)

A 14-number combination that has already been generated **must not appear again**. Keep a
persistent on-disk registry (JSON file) of every set of 14 emitted, stored as a sorted
tuple. When generating, if the candidate already exists in the registry, resolve it with
**the smallest possible deviation**: swap the weakest member of the set (lowest consensus
score) for the best available external number, repeating until a new set is obtained. I'm
aware that forbidding repeats pushes against the recurrence objective; that's why the swap
must be minimal.

## Interface and parameters

Encapsulate everything in a class with at least these methods:

- `fit(draws)` — receives the list of draws in chronological order (oldest first);
  validates that each has 14 numbers in the range 1..25.
- `build_cascade(beam_width, exclude=set())` — returns, for each level k, the list of
  surviving combinations with their score, so the funnel can be inspected.
- `score(combo)`, `retention(parent, n)`, `compare(a, b)` — the comparator.
- `validate()` — the validation report.
- `generate(beam_width)` — returns `{group_A, group_B, fourteen}` and records the result
  for anti-repetition.

Configurable parameters: `half_life`, `beam_width`, the comparator's ordering mode, and
the registry file path.

## Deliverable

Clean, commented code, with error handling (asserts on input validation, try/except where
appropriate) and a runnable demonstration block (`if __name__ == "__main__":`) that
generates a biased synthetic history, shows the best survivor of each cascade level,
prints an example of the retention comparator, runs validation, and generates twice to
show that the second combination differs from the first.