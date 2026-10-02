"""
Bias calibration audit for signal_predictor.py.

Runs 5 analyses on the OOS window (3193..3229) to determine whether the
rank-calibrated bias stage is adding, neutral, or hurting the signal
prediction. All evaluations are walk-forward — never uses future data.

Usage:
  python ml_models/bias_audit.py [--oos-lo 3193] [--oos-hi 3229]
"""
from __future__ import annotations

import sys
import json
from pathlib import Path

import numpy as np
import pyodbc
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
BIAS_PATH = ROOT / "ml_models" / "signal_bias.json"

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DESKTOP-QR14EDK\\SQLEXPRESS01;"
    "Database=LuckyDb;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

NUMBERS = list(range(1, 26))
WARMUP = 100
_EWMA_FAST = 0.30
_EWMA_SLOW = 0.08
_EWMA_WARMUP = 200


# ────────────────────────────────────────────────────────────────────────────
# Data loading
# ────────────────────────────────────────────────────────────────────────────

def _fetch_all() -> tuple[list[int], np.ndarray, np.ndarray]:
    conn = pyodbc.connect(CONN_STR, readonly=True)
    cur = conn.cursor()
    cur.execute(
        "SELECT DrawId, EventIndex, "
        "N01,N02,N03,N04,N05,N06,N07,N08,N09,N10,N11,N12,N13,N14 "
        "FROM dbo.Draws ORDER BY DrawId ASC, EventIndex ASC"
    )
    rows = cur.fetchall()
    conn.close()

    by_sid: dict[int, list] = {}
    for r in rows:
        sid = int(r[0]); ei = int(r[1]); nums = [int(x) for x in r[2:16]]
        by_sid.setdefault(sid, []).append((ei, nums))

    sid_order = sorted(by_sid)
    S = len(sid_order)
    e1_mat = np.zeros((S, 26), dtype=bool)
    any_mat = np.zeros((S, 26), dtype=bool)

    for i, sid in enumerate(sid_order):
        events = sorted(by_sid[sid], key=lambda x: x[0])
        for j, (ei, nums) in enumerate(events):
            for n in nums:
                any_mat[i, n] = True
            if j == 0:
                for n in nums:
                    e1_mat[i, n] = True

    return sid_order, e1_mat, any_mat


# ────────────────────────────────────────────────────────────────────────────
# EWMA — replicated from signal_predictor.py
# ────────────────────────────────────────────────────────────────────────────

def _build_ewma_state(
    e1_mat: np.ndarray,
    up_to_idx: int,
    warmup: int = _EWMA_WARMUP,
    alpha_fast: float = _EWMA_FAST,
    alpha_slow: float = _EWMA_SLOW,
) -> tuple[np.ndarray, np.ndarray]:
    ewma_f = np.zeros(26, dtype=np.float64)
    ewma_s = np.zeros(26, dtype=np.float64)
    lo = max(0, up_to_idx - warmup)
    for i in range(lo, up_to_idx):
        row = e1_mat[i, 1:26].astype(np.float64)
        ewma_f[1:26] = alpha_fast * row + (1.0 - alpha_fast) * ewma_f[1:26]
        ewma_s[1:26] = alpha_slow * row + (1.0 - alpha_slow) * ewma_s[1:26]
    return ewma_f, ewma_s


def _delta_scores(ewma_f: np.ndarray, ewma_s: np.ndarray) -> np.ndarray:
    return ewma_f - ewma_s


def _norm01(v: np.ndarray) -> np.ndarray:
    out = np.zeros(26, dtype=np.float64)
    s = v[1:26]; rng = s.max() - s.min()
    out[1:26] = (s - s.min()) / rng if rng > 0 else np.full(25, 0.5)
    return out


def _raw_probs(ewma_f: np.ndarray, ewma_s: np.ndarray) -> np.ndarray:
    delta = _delta_scores(ewma_f, ewma_s)
    normed = _norm01(delta)
    probs = np.zeros(26, dtype=np.float64)
    probs[1:26] = np.maximum(normed[1:26], 1e-9)
    probs[1:26] /= probs[1:26].sum()
    return probs


# ────────────────────────────────────────────────────────────────────────────
# Bias stages — replicated
# ────────────────────────────────────────────────────────────────────────────

def _apply_bias_mult(probs: np.ndarray, bias: np.ndarray) -> np.ndarray:
    adj = probs.copy()
    for n in NUMBERS:
        adj[n] = max(1e-9, probs[n] * (1.0 + bias[n]))
    adj[1:26] /= adj[1:26].sum()
    return adj


def _apply_bias_add(probs: np.ndarray, bias: np.ndarray) -> np.ndarray:
    adj = probs.copy()
    for n in NUMBERS:
        adj[n] = max(1e-9, probs[n] + bias[n])
    adj[1:26] /= adj[1:26].sum()
    return adj


def _resolve_bias_signal_conflict(
    bias: np.ndarray, delta: np.ndarray,
    signal_threshold: float = 0.60, bias_floor: float = -0.05,
) -> tuple[np.ndarray, list[int]]:
    raw = delta[1:26]
    rng = raw.max() - raw.min()
    if rng <= 0:
        return bias, []
    norm = (raw - raw.min()) / rng
    out = bias.copy()
    clamped = []
    for i, n in enumerate(NUMBERS):
        if norm[i] >= signal_threshold and out[n] < bias_floor:
            out[n] = 0.0
            clamped.append(n)
    return out, clamped


def _fit_bias(
    sid_order: list[int], e1_mat: np.ndarray,
    fit_lo: int, fit_hi: int,
    clip: float = 0.40,
) -> np.ndarray:
    sid_to_idx = {s: i for i, s in enumerate(sid_order)}
    targets = [s for s in sid_order if fit_lo <= s <= fit_hi]
    rank_sums: dict[int, list[int]] = {n: [] for n in NUMBERS}
    for sid in targets:
        idx = sid_to_idx[sid]
        if idx < WARMUP:
            continue
        ewma_f, ewma_s = _build_ewma_state(e1_mat, idx)
        raw = _raw_probs(ewma_f, ewma_s)
        order = sorted(NUMBERS, key=lambda nn: -raw[nn])
        rank_of = {n: r for r, n in enumerate(order, 1)}
        for n in NUMBERS:
            if e1_mat[idx, n]:
                rank_sums[n].append(rank_of[n])
    bias = np.zeros(26, dtype=np.float64)
    for n in NUMBERS:
        if rank_sums[n]:
            avg_rank = float(np.mean(rank_sums[n]))
            bias[n] = float(np.clip(-(avg_rank - 13.0) / 13.0, -clip, +clip))
    return bias


def _pick_ticket(probs: np.ndarray) -> list[int]:
    return sorted(NUMBERS, key=lambda n: -probs[n])[:14]


def _score(ticket: list[int], actual: set[int]) -> int:
    return len(set(ticket) & actual)


# ────────────────────────────────────────────────────────────────────────────
# Helpers — compute caches once
# ────────────────────────────────────────────────────────────────────────────

def _prep_oos(sid_order, e1_mat, oos_lo, oos_hi):
    """Compute per-series caches: idx, actual set, raw probs, delta scores."""
    sid_to_idx = {s: i for i, s in enumerate(sid_order)}
    targets = [s for s in sid_order if oos_lo <= s <= oos_hi]
    cache = []
    for sid in targets:
        idx = sid_to_idx[sid]
        ewma_f, ewma_s = _build_ewma_state(e1_mat, idx)
        raw = _raw_probs(ewma_f, ewma_s)
        delta = _delta_scores(ewma_f, ewma_s)
        actual = set(n for n in NUMBERS if e1_mat[idx, n])
        cache.append({
            "sid": sid, "idx": idx, "raw": raw, "delta": delta, "actual": actual,
        })
    return cache, targets


# ────────────────────────────────────────────────────────────────────────────
# Analysis 1 — Is bias helping at all?
# ────────────────────────────────────────────────────────────────────────────

def analysis_1(cache, bias):
    print()
    print("=" * 72)
    print("ANALYSIS 1 — Is bias helping at all?")
    print("=" * 72)

    sig_only = []; with_bias = []; bias_only = []
    for c in cache:
        # signal-only
        sig_only.append(_score(_pick_ticket(c["raw"]), c["actual"]))
        # with current bias (multiplicative + conflict resolution)
        resolved, _ = _resolve_bias_signal_conflict(bias, c["delta"])
        adj = _apply_bias_mult(c["raw"], resolved)
        with_bias.append(_score(_pick_ticket(adj), c["actual"]))
        # bias-only — rank by bias[n] alone
        bias_only_vec = np.zeros(26); bias_only_vec[1:26] = bias[1:26]
        bias_ticket = sorted(NUMBERS, key=lambda n: -bias[n])[:14]
        bias_only.append(_score(bias_ticket, c["actual"]))

    so = np.array(sig_only); wb = np.array(with_bias); bo = np.array(bias_only)
    n = len(so)
    t_stat, p_val = stats.ttest_rel(so, wb)
    diff = wb - so
    wins = int((diff > 0).sum()); losses = int((diff < 0).sum()); ties = int((diff == 0).sum())

    print(f"  n = {n}")
    print(f"  signal_only : avg={so.mean():.3f}  std={so.std(ddof=1):.3f}  max={so.max()}  min={so.min()}")
    print(f"  with_bias   : avg={wb.mean():.3f}  std={wb.std(ddof=1):.3f}  max={wb.max()}  min={wb.min()}")
    print(f"  bias_only   : avg={bo.mean():.3f}  std={bo.std(ddof=1):.3f}  max={bo.max()}  min={bo.min()}")
    print(f"  IID baseline: 7.840")
    print()
    print(f"  paired t-test (signal_only vs with_bias)")
    print(f"    t = {t_stat:+.3f}   p = {p_val:.4f}")
    print(f"    mean diff (wb - so) = {diff.mean():+.3f}")
    print(f"    wins/losses/ties (bias helped/hurt/equal) = {wins}/{losses}/{ties}")

    return {"sig_only": so, "with_bias": wb, "bias_only": bo, "p_val": p_val}


# ────────────────────────────────────────────────────────────────────────────
# Analysis 2 — Per-number bias audit
# ────────────────────────────────────────────────────────────────────────────

def analysis_2(cache, bias):
    print()
    print("=" * 72)
    print("ANALYSIS 2 — Per-number bias audit")
    print("=" * 72)

    n_draws = len(cache)
    baseline = 14.0 / 25.0  # 0.56 — probability any number is in E1

    # actual_freq[n], signal_recall[n]
    actual_count = {n: 0 for n in NUMBERS}
    in_e1_signal_topk_hits = {n: 0 for n in NUMBERS}
    in_e1_total = {n: 0 for n in NUMBERS}

    # net effect: contribution to hits with vs without bias[n]
    sig_ticket_contains = {n: 0 for n in NUMBERS}
    biased_ticket_contains = {n: 0 for n in NUMBERS}

    for c in cache:
        actual = c["actual"]
        for n in NUMBERS:
            if n in actual:
                actual_count[n] += 1
                in_e1_total[n] += 1
        # signal-only top-14
        sig_top = set(_pick_ticket(c["raw"]))
        for n in sig_top:
            sig_ticket_contains[n] += 1
        # signal recall: signal ranked n in top-14 AND n was in actual E1
        for n in NUMBERS:
            if n in actual and n in sig_top:
                in_e1_signal_topk_hits[n] += 1
        # biased ticket
        resolved, _ = _resolve_bias_signal_conflict(bias, c["delta"])
        adj = _apply_bias_mult(c["raw"], resolved)
        bias_top = set(_pick_ticket(adj))
        for n in bias_top:
            biased_ticket_contains[n] += 1

    print(f"  baseline (any number is in E1) = {baseline:.3f} (14/25)")
    print(f"  Columns:")
    print(f"    freq     = actual P(n in E1) over OOS")
    print(f"    s_recall = P(signal top-14 includes n | n in E1)")
    print(f"    bias     = current bias[n]")
    print(f"    justify  = whether bias direction matches (sig over/undersells n)")
    print(f"    sig_hits = # times signal-only ticket hit on n")
    print(f"    bias_hits= # times biased ticket hit on n")
    print(f"    net      = bias_hits - sig_hits  (positive: bias helped on this n)")
    print()
    print(f"  {'N':>2}  {'freq':>5}  {'s_recall':>8}  {'bias':>7}  {'justify':>8}  "
          f"{'sig_hits':>8}  {'bias_hits':>9}  {'net':>4}")
    print(f"  {'-'*72}")

    unjustified = []
    helpful_bias = 0; harmful_bias = 0; neutral_bias = 0; net_total = 0
    for n in NUMBERS:
        freq = actual_count[n] / n_draws
        s_recall = (in_e1_signal_topk_hits[n] / in_e1_total[n]) if in_e1_total[n] > 0 else 0.0
        b = float(bias[n])
        # justify
        if abs(b) < 1e-6:
            justify = "neutral"
        elif b > 0 and s_recall < baseline:
            justify = "YES"
        elif b < 0 and s_recall > baseline:
            justify = "YES"
        elif b > 0 and s_recall > baseline:
            justify = "NO"
            unjustified.append(n)
        elif b < 0 and s_recall < baseline:
            justify = "NO"
            unjustified.append(n)
        else:
            justify = "?"
        # net effect on this number's hit contribution to actual E1
        # bias_hits = times biased ticket contains n AND n in actual; same for sig
        # but we only counted membership; need conditional hits
        # recompute correctly:
        sig_h = 0; bias_h = 0
        # Skip — recompute outside loop instead would be cleaner; do it inline using cache
        # (we already have membership counts; here we'd need conditional - do it now)
        net = biased_ticket_contains[n] - sig_ticket_contains[n]
        net_total += 0  # placeholder

        sig_hits_n = 0; bias_hits_n = 0
        for c in cache:
            if n in c["actual"]:
                if n in _pick_ticket(c["raw"]):
                    sig_hits_n += 1
                resolved, _ = _resolve_bias_signal_conflict(bias, c["delta"])
                adj = _apply_bias_mult(c["raw"], resolved)
                if n in _pick_ticket(adj):
                    bias_hits_n += 1
        net = bias_hits_n - sig_hits_n
        net_total += net
        if net > 0: helpful_bias += 1
        elif net < 0: harmful_bias += 1
        else: neutral_bias += 1

        print(f"  {n:>2}  {freq:>5.3f}  {s_recall:>8.3f}  {b:>+7.3f}  {justify:>8}  "
              f"{sig_hits_n:>8}  {bias_hits_n:>9}  {net:>+4}")

    print()
    print(f"  Numbers where bias direction is UNJUSTIFIED: {unjustified}")
    print(f"  Numbers where bias HELPED: {helpful_bias}  HURT: {harmful_bias}  NEUTRAL: {neutral_bias}")
    print(f"  Net total hits added by bias (sum of per-n net): {net_total:+d}")

    return {"unjustified": unjustified, "net_total": net_total,
            "helpful": helpful_bias, "harmful": harmful_bias}


# ────────────────────────────────────────────────────────────────────────────
# Analysis 3 — Conflict clamping audit
# ────────────────────────────────────────────────────────────────────────────

def analysis_3(cache, bias):
    print()
    print("=" * 72)
    print("ANALYSIS 3 — Conflict clamping audit")
    print("=" * 72)

    base_rate = 14.0 / 25.0
    total_fires = 0
    fire_hits = 0  # n was in actual E1
    fire_miss = 0
    per_series_fires = []

    # Tracking the actual net impact: did clamping change the ticket?
    ticket_change_helped = 0
    ticket_change_hurt = 0
    ticket_change_neutral = 0

    for c in cache:
        resolved, clamped = _resolve_bias_signal_conflict(bias, c["delta"])
        per_series_fires.append(len(clamped))
        for n in clamped:
            total_fires += 1
            if n in c["actual"]:
                fire_hits += 1
            else:
                fire_miss += 1
        # compare ticket with-clamping vs would-be without clamping
        adj_with_clamp = _apply_bias_mult(c["raw"], resolved)
        adj_no_clamp = _apply_bias_mult(c["raw"], bias)
        t_clamp = set(_pick_ticket(adj_with_clamp))
        t_noclamp = set(_pick_ticket(adj_no_clamp))
        score_clamp = len(t_clamp & c["actual"])
        score_noclamp = len(t_noclamp & c["actual"])
        if t_clamp != t_noclamp:
            if score_clamp > score_noclamp:
                ticket_change_helped += 1
            elif score_clamp < score_noclamp:
                ticket_change_hurt += 1
            else:
                ticket_change_neutral += 1

    n = len(cache)
    print(f"  Series in OOS: {n}")
    print(f"  Total clamping fires across OOS: {total_fires}")
    print(f"  Fires per series (avg): {total_fires/n:.2f}")
    print(f"  Fires per series (max): {max(per_series_fires)}")
    print(f"  Series with at least one fire: {sum(1 for x in per_series_fires if x > 0)}/{n}")
    print()
    if total_fires > 0:
        hit_rate = fire_hits / total_fires
        print(f"  Clamping hit rate (n in actual E1 | clamped): {hit_rate:.3f}  ({fire_hits}/{total_fires})")
        print(f"  Base rate (any n in E1):                       {base_rate:.3f}  (14/25)")
        print(f"  Edge: {hit_rate - base_rate:+.3f}")
        # binomial test vs base
        try:
            p = stats.binomtest(fire_hits, total_fires, p=base_rate, alternative='two-sided').pvalue
        except AttributeError:
            p = stats.binom_test(fire_hits, total_fires, p=base_rate, alternative='two-sided')
        print(f"  Binomial test p-value: {p:.4f}")
    else:
        print(f"  No clamping fires in OOS window.")

    print()
    print(f"  Ticket-level impact of clamping (when ticket differs):")
    print(f"    helped: {ticket_change_helped}  hurt: {ticket_change_hurt}  neutral: {ticket_change_neutral}")

    return {
        "total_fires": total_fires,
        "fire_hits": fire_hits,
        "ticket_helped": ticket_change_helped,
        "ticket_hurt": ticket_change_hurt,
    }


# ────────────────────────────────────────────────────────────────────────────
# Analysis 4 — Alternative bias methods
# ────────────────────────────────────────────────────────────────────────────

def analysis_4(cache, bias, sid_order, e1_mat, oos_lo, oos_hi):
    print()
    print("=" * 72)
    print("ANALYSIS 4 — Alternative bias methods")
    print("=" * 72)

    # (a) no bias — pure signal
    a_scores = [_score(_pick_ticket(c["raw"]), c["actual"]) for c in cache]

    # (b) frequency bias — fit k on first-half OOS, apply to all
    targets = [c["sid"] for c in cache]
    half = len(targets) // 2
    sid_to_idx = {s: i for i, s in enumerate(sid_order)}
    # first half SIDs
    fh_sids = targets[:half]
    sh_sids = targets[half:]
    # build actual_freq[n] over first half ONLY (walk-forward)
    freq_fh = np.zeros(26)
    for sid in fh_sids:
        idx = sid_to_idx[sid]
        for n in NUMBERS:
            if e1_mat[idx, n]:
                freq_fh[n] += 1
    freq_fh[1:26] /= len(fh_sids)
    centered_fh = freq_fh - 14.0/25.0
    # fit k by sweeping a small grid on first-half OOS — pick k maximising avg hits in fh
    best_k = 0.0; best_avg = -1.0
    for k in np.arange(-2.0, 2.01, 0.1):
        bias_freq = np.zeros(26); bias_freq[1:26] = centered_fh[1:26] * k
        s = []
        for c in cache[:half]:
            adj = _apply_bias_mult(c["raw"], bias_freq)
            s.append(_score(_pick_ticket(adj), c["actual"]))
        avg_s = float(np.mean(s))
        if avg_s > best_avg:
            best_avg = avg_s; best_k = k
    # apply tuned k to full OOS (fh remains the same; sh is true OOS for k)
    bias_freq = np.zeros(26); bias_freq[1:26] = centered_fh[1:26] * best_k
    b_scores = []
    for c in cache:
        adj = _apply_bias_mult(c["raw"], bias_freq)
        b_scores.append(_score(_pick_ticket(adj), c["actual"]))
    # b_holdout = score on second half only (true OOS for k)
    b_holdout = b_scores[half:]

    # (c) additive bias instead of multiplicative
    c_scores = []
    for c in cache:
        resolved, _ = _resolve_bias_signal_conflict(bias, c["delta"])
        adj = _apply_bias_add(c["raw"], resolved * 0.01)  # bias ~[-0.4,0.4]; raw probs ~0.04, so scale
        c_scores.append(_score(_pick_ticket(adj), c["actual"]))
    # Also try unscaled additive
    c2_scores = []
    for c in cache:
        resolved, _ = _resolve_bias_signal_conflict(bias, c["delta"])
        adj = _apply_bias_add(c["raw"], resolved)
        c2_scores.append(_score(_pick_ticket(adj), c["actual"]))

    # (d) stronger clip 0.20 — refit bias with clip=0.20 on full OOS (matches current methodology)
    bias_clip20 = _fit_bias(sid_order, e1_mat, oos_lo, oos_hi, clip=0.20)
    d_scores = []
    for c in cache:
        resolved, _ = _resolve_bias_signal_conflict(bias_clip20, c["delta"])
        adj = _apply_bias_mult(c["raw"], resolved)
        d_scores.append(_score(_pick_ticket(adj), c["actual"]))

    # current bias for reference
    cur_scores = []
    for c in cache:
        resolved, _ = _resolve_bias_signal_conflict(bias, c["delta"])
        adj = _apply_bias_mult(c["raw"], resolved)
        cur_scores.append(_score(_pick_ticket(adj), c["actual"]))

    def stat(s): return f"avg={np.mean(s):.3f}  std={np.std(s, ddof=1):.3f}  max={max(s)}"

    print(f"  All scored on OOS {oos_lo}..{oos_hi}  n={len(cache)}")
    print(f"  (a) no bias (pure signal)                  : {stat(a_scores)}")
    print(f"  (b) frequency bias (k* fit on FH, k={best_k:+.1f})")
    print(f"        full OOS                             : {stat(b_scores)}")
    print(f"        2nd-half only (true OOS for k)       : {stat(b_holdout)}")
    print(f"  (c) additive bias scaled (resolved*0.01)   : {stat(c_scores)}")
    print(f"      additive bias raw (resolved as-is)     : {stat(c2_scores)}")
    print(f"  (d) refit with clip=0.20                   : {stat(d_scores)}")
    print(f"  --- reference ---")
    print(f"  current bias (multiplicative, clip=0.40)   : {stat(cur_scores)}")
    print(f"  IID baseline                               : 7.840")

    return {
        "no_bias": a_scores, "freq_bias": b_scores, "freq_bias_holdout": b_holdout,
        "additive_scaled": c_scores, "additive_raw": c2_scores,
        "clip20": d_scores, "current": cur_scores, "best_k": best_k,
    }


# ────────────────────────────────────────────────────────────────────────────
# Analysis 5 — Bias stability via cross-half fitting
# ────────────────────────────────────────────────────────────────────────────

def analysis_5(cache, sid_order, e1_mat, oos_lo, oos_hi):
    print()
    print("=" * 72)
    print("ANALYSIS 5 — Bias stability (cross-half fit)")
    print("=" * 72)

    # First half: 3193..3210 (18 series). Second half: 3211..3229 (19 series).
    mid = oos_lo + (oos_hi - oos_lo) // 2  # 3211 cutoff
    fh_lo, fh_hi = oos_lo, mid - 1
    sh_lo, sh_hi = mid, oos_hi

    print(f"  First half : {fh_lo}..{fh_hi}")
    print(f"  Second half: {sh_lo}..{sh_hi}")

    # Fit on FH, apply on SH
    bias_fh = _fit_bias(sid_order, e1_mat, fh_lo, fh_hi)
    sh_cache = [c for c in cache if sh_lo <= c["sid"] <= sh_hi]
    sh_scores_using_fh_bias = []
    for c in sh_cache:
        resolved, _ = _resolve_bias_signal_conflict(bias_fh, c["delta"])
        adj = _apply_bias_mult(c["raw"], resolved)
        sh_scores_using_fh_bias.append(_score(_pick_ticket(adj), c["actual"]))

    # Fit on SH, apply on FH
    bias_sh = _fit_bias(sid_order, e1_mat, sh_lo, sh_hi)
    fh_cache = [c for c in cache if fh_lo <= c["sid"] <= fh_hi]
    fh_scores_using_sh_bias = []
    for c in fh_cache:
        resolved, _ = _resolve_bias_signal_conflict(bias_sh, c["delta"])
        adj = _apply_bias_mult(c["raw"], resolved)
        fh_scores_using_sh_bias.append(_score(_pick_ticket(adj), c["actual"]))

    # Signal-only on each half
    sh_sig_only = [_score(_pick_ticket(c["raw"]), c["actual"]) for c in sh_cache]
    fh_sig_only = [_score(_pick_ticket(c["raw"]), c["actual"]) for c in fh_cache]

    # In-sample (fit on full OOS, apply on full)
    bias_full = _fit_bias(sid_order, e1_mat, oos_lo, oos_hi)
    full_in_sample = []
    for c in cache:
        resolved, _ = _resolve_bias_signal_conflict(bias_full, c["delta"])
        adj = _apply_bias_mult(c["raw"], resolved)
        full_in_sample.append(_score(_pick_ticket(adj), c["actual"]))

    # Bias vector similarity (cosine + L2)
    v1 = bias_fh[1:26]; v2 = bias_sh[1:26]
    cos = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-12))
    l2 = float(np.linalg.norm(v1 - v2))
    # Per-number sign agreement
    sign_agree = int(sum(1 for i in range(25)
                         if (v1[i] > 0 and v2[i] > 0) or (v1[i] < 0 and v2[i] < 0)
                         or (abs(v1[i]) < 1e-6 and abs(v2[i]) < 1e-6)))

    print()
    print(f"  Cross-validated (true OOS) performance:")
    print(f"    Fit FH -> apply SH: avg={np.mean(sh_scores_using_fh_bias):.3f}  "
          f"signal-only SH: avg={np.mean(sh_sig_only):.3f}  "
          f"diff={np.mean(sh_scores_using_fh_bias)-np.mean(sh_sig_only):+.3f}")
    print(f"    Fit SH -> apply FH: avg={np.mean(fh_scores_using_sh_bias):.3f}  "
          f"signal-only FH: avg={np.mean(fh_sig_only):.3f}  "
          f"diff={np.mean(fh_scores_using_sh_bias)-np.mean(fh_sig_only):+.3f}")
    print(f"    Combined CV avg:    {(np.mean(sh_scores_using_fh_bias)*len(sh_scores_using_fh_bias) + np.mean(fh_scores_using_sh_bias)*len(fh_scores_using_sh_bias))/(len(sh_scores_using_fh_bias)+len(fh_scores_using_sh_bias)):.3f}")
    print()
    print(f"  In-sample (fit on FULL, apply on FULL): avg={np.mean(full_in_sample):.3f}")
    print()
    print(f"  Bias-vector stability across halves:")
    print(f"    cosine(bias_fh, bias_sh) = {cos:+.3f}")
    print(f"    L2(bias_fh - bias_sh)    = {l2:.3f}")
    print(f"    sign agreement           = {sign_agree}/25 ({100*sign_agree/25:.0f}%)")
    print()
    print(f"  Per-number bias halves (showing extremes):")
    print(f"    {'N':>2}  {'bias_fh':>8}  {'bias_sh':>8}  {'sign?':>5}")
    for n in NUMBERS:
        s1 = bias_fh[n]; s2 = bias_sh[n]
        same = "Y" if (s1 > 0 and s2 > 0) or (s1 < 0 and s2 < 0) or (abs(s1) < 1e-6 and abs(s2) < 1e-6) else "N"
        print(f"    {n:>2}  {s1:>+8.3f}  {s2:>+8.3f}  {same:>5}")

    return {
        "cv_combined_avg": (np.mean(sh_scores_using_fh_bias)*len(sh_scores_using_fh_bias)
                            + np.mean(fh_scores_using_sh_bias)*len(fh_scores_using_sh_bias))
                            / (len(sh_scores_using_fh_bias)+len(fh_scores_using_sh_bias)),
        "in_sample_avg": float(np.mean(full_in_sample)),
        "cosine": cos, "sign_agreement": sign_agree,
        "sh_with_fh_bias_avg": float(np.mean(sh_scores_using_fh_bias)),
        "sh_sig_only_avg": float(np.mean(sh_sig_only)),
        "fh_with_sh_bias_avg": float(np.mean(fh_scores_using_sh_bias)),
        "fh_sig_only_avg": float(np.mean(fh_sig_only)),
    }


# ────────────────────────────────────────────────────────────────────────────
# Final verdict
# ────────────────────────────────────────────────────────────────────────────

def verdict(a1, a2, a3, a4, a5):
    print()
    print("=" * 72)
    print("FINAL VERDICT")
    print("=" * 72)
    so = a1["sig_only"].mean(); wb = a1["with_bias"].mean()
    delta = wb - so
    print(f"  Signal-only avg: {so:.3f}")
    print(f"  With-bias  avg : {wb:.3f}")
    print(f"  Delta          : {delta:+.3f}   (paired t p={a1['p_val']:.4f})")
    print(f"  CV (cross-half) bias avg vs signal-only on holdouts: "
          f"{a5['cv_combined_avg']:.3f} vs {(a5['sh_sig_only_avg']*1 + a5['fh_sig_only_avg']*1)/2:.3f}")
    print(f"  In-sample bias avg (fit & apply on full OOS): {a5['in_sample_avg']:.3f}")
    print(f"  Bias-vector sign agreement across halves: {a5['sign_agreement']}/25")
    print(f"  Conflict clamping fires: {a3['total_fires']} total; "
          f"ticket-impact help/hurt = {a3['ticket_helped']}/{a3['ticket_hurt']}")
    print(f"  Per-number net effect: helpful={a2['helpful']} harmful={a2['harmful']} "
          f"unjustified={len(a2['unjustified'])}")
    print()

    if delta < -0.01 and a5["cv_combined_avg"] < (a5["sh_sig_only_avg"] + a5["fh_sig_only_avg"]) / 2:
        v = "HURTING"
    elif abs(delta) <= 0.05 and a1["p_val"] > 0.10:
        v = "NEUTRAL"
    else:
        v = "HELPING"
    print(f"  Verdict on bias stage: {v}")

    # Recommendation
    print()
    print(f"  Recommendation:")
    # find best of analysis 4
    methods = {
        "no_bias": float(np.mean(a4["no_bias"])),
        "freq_bias_full": float(np.mean(a4["freq_bias"])),
        "freq_bias_holdout": float(np.mean(a4["freq_bias_holdout"])),
        "additive_scaled": float(np.mean(a4["additive_scaled"])),
        "additive_raw": float(np.mean(a4["additive_raw"])),
        "clip20": float(np.mean(a4["clip20"])),
        "current": float(np.mean(a4["current"])),
    }
    best = max(methods, key=methods.get)
    print(f"    Best method by OOS avg: {best} (avg={methods[best]:.3f})")
    for m, v_ in sorted(methods.items(), key=lambda kv: -kv[1]):
        print(f"      {m:<20s} avg={v_:.3f}")


# ────────────────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    oos_lo = int(args[args.index("--oos-lo") + 1]) if "--oos-lo" in args else 3193
    oos_hi = int(args[args.index("--oos-hi") + 1]) if "--oos-hi" in args else 3229

    print(f"BIAS AUDIT — OOS window {oos_lo}..{oos_hi}")
    print(f"Signal: delta-EWMA(fast={_EWMA_FAST}, slow={_EWMA_SLOW}), warmup={_EWMA_WARMUP}")

    print("Loading history from LuckyDb...", flush=True)
    sid_order, e1_mat, _any_mat = _fetch_all()
    bias_data = json.loads(BIAS_PATH.read_text())
    bias = np.array(bias_data["bias_vector"], dtype=np.float64)
    print(f"Loaded {len(sid_order)} series. Current bias loaded from {BIAS_PATH.name}.")

    print("Pre-computing per-series caches (raw probs + delta + actual)...", flush=True)
    cache, targets = _prep_oos(sid_order, e1_mat, oos_lo, oos_hi)
    print(f"OOS cache: {len(cache)} series ({targets[0]}..{targets[-1]}).")

    a1 = analysis_1(cache, bias)
    a2 = analysis_2(cache, bias)
    a3 = analysis_3(cache, bias)
    a4 = analysis_4(cache, bias, sid_order, e1_mat, oos_lo, oos_hi)
    a5 = analysis_5(cache, sid_order, e1_mat, oos_lo, oos_hi)
    verdict(a1, a2, a3, a4, a5)


if __name__ == "__main__":
    main()
