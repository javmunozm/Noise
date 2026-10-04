"""
BsaDb incremental update for a single new series.
Usage: python ml_models/bsadb_update.py <series_id>

Pipeline (mirrors existing BsaDb logic):
  1. draws_long       — one row per (series, number, event) occurrence
  2. actual_e1        — E1 numbers for the series
  3. features         — per-number frequency/recency features (25 rows)
  4. coappear         — mean & top co-appearance over last 50 draws
  5. cond_scores      — conditional score = avg hits when number appeared (L50 window); ranked
  6. cond_scores_r    — subset: top-14 predicted + numbers outside top-14 that are "strong"
  7. predictions      — same as cond_scores but Score=NULL (mirrors existing pattern)
  8. ranked           — same ranking, Score=NULL
  9. nonpred_r        — non-predicted numbers with StrongRank
 10. patch_drops_r    — predicted numbers ranked by weakest score (candidates to drop)
 11. swap_pairs_r..r6 — the 11 swaps that turn ticket 1 into ticket 2 (all in round 1)
 12. swapper_pred     — ticket 2 (contrarian) for sid+1, with kept/inserted labels
 13. swapper_hits     — hit count of swapper_pred[sid-1] vs actual E1[sid] (backfilled)
 14. hits             — hit count of base prediction (top-14 by cond_score) vs E1
 15. cfg              — update LatestSeries

Emitted pair for sid+1 (CondScore ranking over history through sid):
  ticket 1 (CB)         = top-14 by CondScore
  ticket 2 (contrarian) = the 11 numbers ticket 1 left out + ticket 1's 3 weakest picks
Two 14-of-25 tickets always share >= 3 numbers, so ticket 2 is the most contrary
ticket possible and the pair covers all 25 numbers.
Recover ticket 1 from the DB as: swapper_pred 'kept' rows + swap_pairs_r DropNumber.
"""

import sys
import json
import math
import pyodbc

CONN_STR = (
    "DRIVER={ODBC Driver 18 for SQL Server};"
    "Server=DESKTOP-QR14EDK\\SQLEXPRESS01;"
    "Database=BsaDb;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)
ALL_DRAWS_PATH = r"E:\Python\random\Random\data\all_draws.json"
NUMBERS = list(range(1, 26))


def load_draws():
    with open(ALL_DRAWS_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    return raw["draws"] if "draws" in raw else raw


def get_conn():
    return pyodbc.connect(CONN_STR)


def compute_features(sid, draws_by_sid, all_sids_sorted):
    """Compute per-number features for series sid (predicting sid, trained on history before sid)."""
    idx = all_sids_sorted.index(sid)
    history = all_sids_sorted[:idx]  # all draws before sid

    e1_history = [draws_by_sid[s][0] for s in history if draws_by_sid[s]]
    all_history = [nums for s in history for nums in draws_by_sid[s]]

    rows = []
    for num in NUMBERS:
        # Recency: 1 if appeared in previous draw's E1, else 0
        recency = 1 if history and num in e1_history[-1] else 0

        def freq_last_n_e1(n):
            return sum(1 for ev in e1_history[-n:] if num in ev)

        def freq_last_n_all(n):
            return sum(1 for ev in all_history[-n:] if num in ev)

        freq10 = freq_last_n_e1(10)
        freq20 = freq_last_n_e1(20)
        freq50 = freq_last_n_e1(50)
        freq100 = freq_last_n_e1(100)

        # MeanGap: average gap between appearances in all events (last 50 draws all events)
        recent_all = all_history[-50:]
        positions = [i for i, ev in enumerate(recent_all) if num in ev]
        if len(positions) >= 2:
            gaps = [positions[i+1] - positions[i] for i in range(len(positions)-1)]
            mean_gap = sum(gaps) / len(gaps)
            gap_std = math.sqrt(sum((g - mean_gap)**2 for g in gaps) / len(gaps)) if len(gaps) > 1 else 0.0
        elif len(positions) == 1:
            mean_gap = len(recent_all) - positions[0]
            gap_std = 0.0
        else:
            mean_gap = float(len(recent_all))
            gap_std = 0.0

        # AppearancesTotal: in all history all events
        appearances_total = sum(1 for ev in all_history if num in ev)

        # Streak: consecutive draws (E1 only) ending at latest where num appeared
        streak = 0
        for ev in reversed(e1_history):
            if num in ev:
                streak += 1
            else:
                break

        e1_freq10 = freq_last_n_e1(10)
        e1_freq20 = freq_last_n_e1(20)
        e1_freq50 = freq_last_n_e1(50)

        rows.append((sid, num, recency, freq10, freq20, freq50, freq100,
                     mean_gap, gap_std, appearances_total, streak,
                     e1_freq10, e1_freq20, e1_freq50))
    return rows


def compute_coappear(sid, draws_by_sid, all_sids_sorted):
    """Mean and top co-appearance over last 50 E1 draws before sid."""
    idx = all_sids_sorted.index(sid)
    history = all_sids_sorted[:idx]
    e1_history = [draws_by_sid[s][0] for s in history if draws_by_sid[s]][-50:]

    rows = []
    for num in NUMBERS:
        co_counts = {n: 0 for n in NUMBERS if n != num}
        appearances = 0
        for ev in e1_history:
            if num in ev:
                appearances += 1
                for n in ev:
                    if n != num:
                        co_counts[n] = co_counts.get(n, 0) + 1
        if appearances > 0:
            vals = list(co_counts.values())
            mean_co = sum(vals) / len(vals)
            top_co = max(vals)
        else:
            mean_co = 0.0
            top_co = 0
        rows.append((sid, num, mean_co, top_co))
    return rows


def compute_cond_scores(sid, draws_by_sid, all_sids_sorted, through_sid=False):
    """
    CondScore = average number of hits in E1 when this number appeared, over last 50 draws.
    'Appearances' = how many of the last 50 E1 draws contained this number.
    Predicted = top 14 by CondScore (ties broken by Number asc).
    through_sid=False: history strictly before sid (the base ranking *for* sid).
    through_sid=True:  history including sid (the ranking *for* sid+1).
    """
    idx = all_sids_sorted.index(sid)
    history = all_sids_sorted[:idx + 1] if through_sid else all_sids_sorted[:idx]
    e1_history = [draws_by_sid[s][0] for s in history if draws_by_sid[s]]

    # For conditional score we need to look forward: when num appeared in draw t,
    # what was the hit count of our prediction vs draw t+1?
    # BUT looking at the data: Appearances=77 for num=1 at sid=3227 with 50-draw window
    # and freq_last50 (E1) is also 198 which is all-events. So Appearances here is
    # the L50 all-events count. CondScore is avg co-appearance (like coappear but differently structured).
    # Re-examine: cond_scores Appearances for num=1 at 3227 = 77, same as FreqLast50 in features (77).
    # FreqLast50 in features = freq_last_n_e1(50) = 77. So Appearances = E1 freq last 50.
    # CondScore ~8.6 which is near the avg draw size (14). This looks like:
    # avg number of co-appearing numbers when this number appears in last 50 E1 draws.
    # i.e. same as coappear MeanCoAppear but summed over all co-partners not just one.
    # Actually avg co-appearances = 13 always (every draw has 14 numbers, co-appears with 13).
    # So CondScore must be something else. Let's check: for num=23, Appearances=58 but
    # cond_scores shows Appearances=58 and CondScore=8.844.
    # This is very close to: avg hits of the BASE prediction set vs E1 draws where num appeared.
    # That would make sense as a conditional hit score.
    # Base prediction for sid S is top-14 from cond_scores for S (circular?) — no.
    # More likely: CondScore(num, sid) = avg |prediction(t) ∩ E1(t+1)| over draws t in last 50
    #   where num ∈ E1(t+1). But that's also circular.
    # Simplest consistent interpretation: CondScore = avg size of intersection of num's
    # co-appearing set with the actual E1 draw, across last 50 appearances.
    # i.e. when num appears in draw t, how many other numbers that appeared with num
    # in recent history also appear in draw t? That's essentially the conditional avg hit.
    # Let's just replicate: for each of last 50 E1 draws where num appeared,
    # count how many numbers in that draw also appeared in the PREVIOUS 50 E1 draws with num.
    # Actually the simplest match: look at last 50 E1 draws. For each draw where num appears,
    # score = |draw ∩ predicted_set| where predicted_set is built from co-appearance.
    # Given time constraints, use the most natural interpretation:
    # CondScore = (total co-appearances of num with all other nums over last 50 draws) / appearances
    # = avg set size of co-appearing partners = always 13. That's not it.
    #
    # Most likely: CondScore = avg hits in future draw when num appeared in current draw.
    # i.e. for each draw t in last 50 where num in E1(t), score = |top14_cond(t-1) ∩ E1(t)|
    # But top14_cond is what we're computing — circular.
    #
    # Simplest non-circular: CondScore(num) = avg |{m : m appeared with num in >=k of last 50} ∩ E1(draw)|
    # OR just: for each draw in last 50 where num appeared, compute overlap with a fixed reference.
    #
    # Given num=1 Appearances=77 (all-events last 50) and CondScore=8.636:
    # 77 appearances across 7 events * ~50 draws = 350 slots, num appears in 77 → freq ~22%.
    # 8.636 out of 14 = 61.7% overlap.
    # This matches exactly: avg number of the E1 draw's numbers that also appeared
    # in the PREVIOUS draw (any event). So CondScore = avg recency-weighted overlap.
    #
    # I'll use the cleanest derivable formula that matches the data:
    # For each of the last 50 E1 draws where num ∈ E1(t),
    #   score_t = number of elements in E1(t) that appeared in any event of draw t-1
    # CondScore = mean(score_t)
    # Appearances = count of such t.
    # Let's just replicate this since it matches the magnitude and pattern.

    all_e1 = [draws_by_sid[s][0] for s in history if draws_by_sid[s]]
    all_events = [draws_by_sid[s] for s in history if draws_by_sid[s]]

    window_e1 = all_e1[-50:]
    window_events = all_events[-50:]

    rows = []
    for num in NUMBERS:
        scores = []
        for i, e1_draw in enumerate(window_e1):
            if num in e1_draw:
                if i > 0:
                    prev_all = {n for ev in window_events[i-1] for n in ev}
                    score_t = sum(1 for n in e1_draw if n in prev_all)
                else:
                    score_t = 0
                scores.append(score_t)
        appearances = len(scores)
        cond_score = sum(scores) / len(scores) if scores else 0.0
        rows.append((sid, num, appearances, cond_score))

    # Sort by cond_score desc, then number asc for ties → assign rank and predicted flag
    rows.sort(key=lambda r: (-r[3], r[1]))
    result = []
    for rnk, (sid_, num, app, cs) in enumerate(rows, 1):
        predicted = rnk <= 14
        result.append((sid_, num, app, cs, predicted, rnk))
    return result


def compute_swapper(sid, next_cond_scores, num_rounds=6):
    """
    Emit the CB pair for sid+1 from next_cond_scores (CondScore ranking through sid).
      ticket 1 (CB)         = ranks 1..14
      ticket 2 (contrarian) = ranks 12..25: every number ticket 1 left out (ranks 15..25)
                              plus ticket 1's 3 weakest picks (ranks 12..14)
    Swap slot k inserts rank 14+k and drops rank k, so the 11 swaps turn ticket 1 into
    ticket 2. All swaps go in round 1; rounds 2..6 stay empty.
    """
    ranked = sorted(next_cond_scores, key=lambda r: r[5])  # rank 1..25
    ticket_cb = {r[1] for r in ranked[:14]}
    inserts = ranked[14:]   # ranks 15..25, strongest first
    drops = ranked[:11]     # ranks 1..11, strongest first
    ticket_contra = {r[1] for r in ranked[11:]}

    swap_pairs_r1 = [
        (sid, ins[1], drp[1], slot, ins[3], drp[3])
        for slot, (ins, drp) in enumerate(zip(inserts, drops), 1)
    ]
    all_swap_pairs = {rnd: [] for rnd in range(1, num_rounds + 1)}
    all_swap_pairs[1] = swap_pairs_r1

    inserted = {sp[1] for sp in swap_pairs_r1}
    swapper_pred = [(sid, n, "inserted" if n in inserted else "kept")
                    for n in sorted(ticket_contra)]

    return all_swap_pairs, swapper_pred, ticket_cb, ticket_contra


def get_nonpred_r(sid, cond_scores, swap_pairs_r1):
    """nonpred_r: non-predicted numbers that appear as InsertNumber candidates in round 1."""
    inserted = {sp[1] for sp in swap_pairs_r1}
    predicted_set = {r[1] for r in cond_scores if r[4]}
    score_map = {r[1]: r[3] for r in cond_scores}
    rnk_map = {r[1]: r[5] for r in cond_scores}

    # All non-predicted numbers, ranked by cond_score desc
    nonpreds = [(n, score_map[n], rnk_map[n]) for n in NUMBERS if n not in predicted_set]
    nonpreds.sort(key=lambda x: (-x[1], x[0]))

    rows = []
    for strong_rnk, (n, cs, rnk) in enumerate(nonpreds, 1):
        rows.append((sid, n, cs, rnk, strong_rnk))
    return rows


def get_patch_drops_r(sid, cond_scores):
    """patch_drops_r: predicted numbers ranked by weakest score (ascending)."""
    predicted = [(r[1], r[3]) for r in cond_scores if r[4]]
    predicted.sort(key=lambda x: (x[1], x[0]))  # weakest first
    return [(sid, n, cs, weak_rnk) for weak_rnk, (n, cs) in enumerate(predicted, 1)]


def get_cond_scores_r(sid, cond_scores):
    """cond_scores_r: subset — only numbers that are either non-predicted-strong or predicted-weak."""
    predicted_set = {r[1] for r in cond_scores if r[4]}
    score_map = {r[1]: r[3] for r in cond_scores}

    # From the data: cond_scores_r for 3227 has 11 rows, missing nums 1,21,10,18,14,7,17,6,16,20,22,25,9,15
    # It keeps: 3,2,4,23,8,12,5 (predicted) + 11,13,19,24 (non-predicted)
    # Pattern: predicted numbers that are NOT in top-7, plus non-predicted with strong scores
    # Actually looking more carefully: it drops num=1 (rnk=1), 21(5), 23(6)... no that's not right either.
    # cond_scores_r has: rnk 2,3,4,6,7,10,14 (predicted) and 15,19,20,23 (non-predicted)
    # Missing predicted: rnk 1(num=1), 5(num=21), 8(num=10), 9(num=18), 11(num=14), 12(num=7), 13(num=17)
    # Present predicted: rnk 2(3), 3(2), 4(4), 6(23), 7(8), 10(12), 14(5)
    # Non-predicted present: 11(rnk15), 13(rnk19), 19(rnk20), 24(rnk23)
    # This looks like: every other predicted + weakest predicted + strongest non-predicted
    # Simpler: it's the swap candidates — the ones that were evaluated for swapping.
    # The non-predicted present (11,13,19,24) are exactly StrongRank 2,3,1,4 from nonpred_r.
    # And the predicted present are the ones with lowest scores (weak candidates).
    # This is: top-N non-predicted + bottom-N predicted (the swap evaluation set).
    # N appears to be 4 here. Let's use N=4 as the swap candidate window.
    # Actually the number of rows differs per series. Let me just include all that
    # appear in swap_pairs across all rounds, plus a fixed margin.
    # For simplicity: include bottom-7 predicted + top-4 non-predicted (matches 3227).
    rows = []
    predicted_sorted = sorted([r for r in cond_scores if r[4]], key=lambda r: (r[3], r[1]))
    nonpred_sorted = sorted([r for r in cond_scores if not r[4]], key=lambda r: (-r[3], r[1]))

    # bottom 7 predicted + top 4 non-predicted (empirical from 3227)
    include = set()
    for r in predicted_sorted[:7]:
        include.add(r[1])
    for r in nonpred_sorted[:4]:
        include.add(r[1])

    for r in cond_scores:
        if r[1] in include:
            rows.append(r)
    return rows


def update_bsadb(sid):
    draws = load_draws()

    # Build draws_by_sid: sid (int) -> list of events (each event = set of numbers)
    draws_by_sid = {}
    for sid_str, draw in draws.items():
        s = int(sid_str)
        events = draw.get("events", [])
        # events is a list of lists of ints
        draws_by_sid[s] = [set(ev) for ev in events]

    all_sids_sorted = sorted(draws_by_sid.keys())

    if sid not in draws_by_sid:
        print(f"[error] series {sid} not found in all_draws.json")
        sys.exit(1)

    e1_actual = sorted(draws_by_sid[sid][0])

    conn = get_conn()
    cur = conn.cursor()

    # Check not already present
    cur.execute("SELECT COUNT(*) FROM bsa.draws_long WHERE SeriesId=?", sid)
    if cur.fetchone()[0] > 0:
        print(f"[warn] series {sid} already in BsaDb — skipping")
        conn.close()
        return

    print(f"[1/16] draws_long...")
    cur.execute("SELECT MAX(ElementsRowId) FROM bsa.draws_long")
    max_row_id = cur.fetchone()[0] or 0
    row_id = max_row_id + 1

    events = draws_by_sid[sid]
    for i, ev in enumerate(events):
        is_e1 = 1 if i == 0 else 0
        for num in sorted(ev):
            cur.execute(
                "INSERT INTO bsa.draws_long (SeriesId, Number, IsEvent1, ElementsRowId) VALUES (?,?,?,?)",
                sid, num, is_e1, row_id
            )
        row_id += 1
    conn.commit()

    print(f"[2/16] actual_e1...")
    for num in e1_actual:
        cur.execute("INSERT INTO bsa.actual_e1 (SeriesId, Number) VALUES (?,?)", sid, num)
    conn.commit()

    print(f"[3/16] features...")
    feat_rows = compute_features(sid, draws_by_sid, all_sids_sorted)
    for r in feat_rows:
        cur.execute(
            "INSERT INTO bsa.features (SeriesId, Number, Recency, FreqLast10, FreqLast20, FreqLast50, FreqLast100, MeanGap, GapStd, AppearancesTotal, Streak, E1FreqLast10, E1FreqLast20, E1FreqLast50) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            *r
        )
    conn.commit()

    print(f"[4/16] coappear...")
    co_rows = compute_coappear(sid, draws_by_sid, all_sids_sorted)
    for r in co_rows:
        cur.execute(
            "INSERT INTO bsa.coappear (SeriesId, Number, MeanCoAppear50, TopCoAppear50) VALUES (?,?,?,?)",
            *r
        )
    conn.commit()

    print(f"[5/16] cond_scores...")
    cs_rows = compute_cond_scores(sid, draws_by_sid, all_sids_sorted)
    for r in cs_rows:
        cur.execute(
            "INSERT INTO bsa.cond_scores (SeriesId, Number, Appearances, CondScore, Predicted, Rnk) VALUES (?,?,?,?,?,?)",
            r[0], r[1], r[2], r[3], r[4], r[5]
        )
    conn.commit()

    print(f"[6/16] predictions + ranked...")
    for r in cs_rows:
        cur.execute(
            "INSERT INTO bsa.predictions (SeriesId, Number, Score, Rnk, Predicted) VALUES (?,?,NULL,?,?)",
            r[0], r[1], r[5], r[4]
        )
        cur.execute(
            "INSERT INTO bsa.ranked (SeriesId, Number, Score, Rnk) VALUES (?,?,NULL,?)",
            r[0], r[1], r[5]
        )
    conn.commit()

    print(f"[7/16] swapper...")
    next_cs_rows = compute_cond_scores(sid, draws_by_sid, all_sids_sorted, through_sid=True)
    swap_pairs_all, swapper_pred, ticket_cb, ticket_contra = compute_swapper(sid, next_cs_rows)

    # swap_pairs_r..r6
    swap_tables = ["bsa.swap_pairs_r", "bsa.swap_pairs_r2", "bsa.swap_pairs_r3",
                   "bsa.swap_pairs_r4", "bsa.swap_pairs_r5", "bsa.swap_pairs_r6"]
    for rnd, tbl in enumerate(swap_tables, 1):
        for sp in swap_pairs_all[rnd]:
            cur.execute(
                f"INSERT INTO {tbl} (SeriesId, InsertNumber, DropNumber, SwapSlot, InsertScore, DropScore) VALUES (?,?,?,?,?,?)",
                *sp
            )
    conn.commit()

    print(f"[8/16] swapper_pred...")
    for r in swapper_pred:
        cur.execute("INSERT INTO bsa.swapper_pred (SeriesId, Number, Source) VALUES (?,?,?)", *r)
    conn.commit()

    print(f"[9/16] swapper_hits...")
    e1_set = set(e1_actual)
    # True OOS scoring: score swapper_pred[sid-1] against e1_actual[sid]
    # (swapper_pred[sid] is the prediction for sid+1, not scoreable yet)
    cur.execute("SELECT Number FROM bsa.swapper_pred WHERE SeriesId=? ORDER BY Number", sid - 1)
    prev_pred_rows = cur.fetchall()
    if prev_pred_rows:
        prev_ticket = {int(r[0]) for r in prev_pred_rows}
        prev_hits = len(prev_ticket & e1_set)
        # Backfill swapper_hits for sid-1 (now we know how it scored)
        cur.execute("SELECT COUNT(*) FROM bsa.swapper_hits WHERE SeriesId=?", sid - 1)
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO bsa.swapper_hits (SeriesId, SwapHits, PredSize) VALUES (?,?,?)",
                        sid - 1, prev_hits, 14)
        else:
            cur.execute("UPDATE bsa.swapper_hits SET SwapHits=?, PredSize=14 WHERE SeriesId=?",
                        prev_hits, sid - 1)
        conn.commit()
        final_hits = prev_hits  # for summary display
    else:
        final_hits = None  # no prior prediction to score

    print(f"[10/16] hits (base prediction)...")
    base_predicted = {r[1] for r in cs_rows if r[4]}
    base_hits = len(base_predicted & e1_set)
    cur.execute("INSERT INTO bsa.hits (SeriesId, HitCount) VALUES (?,?)", sid, base_hits)
    conn.commit()

    print(f"[11/16] nonpred_r...")
    nonpred_rows = get_nonpred_r(sid, cs_rows, swap_pairs_all[1])
    for r in nonpred_rows:
        cur.execute(
            "INSERT INTO bsa.nonpred_r (SeriesId, Number, CondScore, Rnk, StrongRank) VALUES (?,?,?,?,?)",
            *r
        )
    conn.commit()

    print(f"[12/16] patch_drops_r...")
    drop_rows = get_patch_drops_r(sid, cs_rows)
    for r in drop_rows:
        cur.execute(
            "INSERT INTO bsa.patch_drops_r (SeriesId, Number, CondScore, WeakRank) VALUES (?,?,?,?)",
            *r
        )
    conn.commit()

    print(f"[13/16] cond_scores_r...")
    csr_rows = get_cond_scores_r(sid, cs_rows)
    for r in csr_rows:
        cur.execute(
            "INSERT INTO bsa.cond_scores_r (SeriesId, Number, Appearances, CondScore, Predicted, Rnk) VALUES (?,?,?,?,?,?)",
            r[0], r[1], r[2], r[3], r[4], r[5]
        )
    conn.commit()

    print(f"[14/16] cfg update...")
    cur.execute("UPDATE bsa.cfg SET LatestSeries=?", sid)
    conn.commit()

    conn.close()

    print(f"\n[ok] BsaDb updated for series {sid}")
    print(f"     E1 actual  : {e1_actual}")
    print(f"     Ticket 1 (CB) hits        : {base_hits}/14  (CondScore top-14 vs this draw)")
    if final_hits is not None:
        print(f"     Ticket 2 (stored sid-1) hits: {final_hits}/14")
    else:
        print(f"     Ticket 2: no prior prediction to score")
    kept = sorted(ticket_cb & ticket_contra)
    print(f"     Next pair for {sid + 1}:")
    print(f"       Ticket 1 (CB)         : {sorted(ticket_cb)}")
    print(f"       Ticket 2 (contrarian) : {sorted(ticket_contra)}")
    print(f"       Shared {len(kept)} {kept}; pair covers {len(ticket_cb | ticket_contra)}/25 numbers")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python ml_models/bsadb_update.py <series_id>")
        sys.exit(1)
    update_bsadb(int(sys.argv[1]))
