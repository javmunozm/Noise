"""
Pair Weight Evaluator — evaluador de combinaciones de pares.

Cada serie de 14 numeros genera C(14,2)=91 pares unicos.
El universo de pares posibles en 1-25 es C(25,2)=300.

Para cada par se calcula:
  - freq_full : cuantas veces aparecio en todo el historico E1
  - freq_l20  : cuantas veces en las ultimas 20 series E1
  - peso      : tasa_L20 / baseline_IID  (>1.0 = activo, <1.0 = inactivo)
  - z_l20     : z-score en ventana L20

Luego para cada set de prediccion se agrega el peso medio de sus 91 pares,
generando un ranking de alineacion con el regimen actual.

Uso:
    python ml_models/pair_weight_evaluator.py [series_id] [--window N] [--full]

    series_id  : draw a evaluar (default: ultimo+1)
    --window N : ventana de regimen en series E1 (default: 20)
    --full     : mostrar todos los pares de cada set (no solo los top/bot)
"""
from __future__ import annotations

import math
import sys
from itertools import combinations
from pathlib import Path

import pyodbc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ml_models"))

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DESKTOP-QR14EDK\\SQLEXPRESS01;"
    "Database=LuckyDb;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)
NUM_COLS = "N01,N02,N03,N04,N05,N06,N07,N08,N09,N10,N11,N12,N13,N14"
NUMBERS  = list(range(1, 26))
ALL_PAIRS = [(a, b) for a in range(1, 26) for b in range(a + 1, 26)]  # 300 pares
P_IID    = 14 * 13 / (25 * 24)   # 0.3033 — probabilidad baseline de un par por draw


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_e1_history(before_draw_id: int) -> list[list[int]]:
    cn = pyodbc.connect(CONN_STR, readonly=True)
    cur = cn.cursor()
    cur.execute(
        f"SELECT {NUM_COLS} FROM dbo.Draws "
        "WHERE DrawId < ? AND EventIndex = 1 ORDER BY DrawId ASC",
        (before_draw_id,)
    )
    rows = cur.fetchall()
    cn.close()
    return [sorted(int(x) for x in row) for row in rows]


# ---------------------------------------------------------------------------
# Pair statistics
# ---------------------------------------------------------------------------

def build_pair_table(
    e1_history: list[list[int]],
    window: int = 20,
) -> dict[tuple, dict]:
    """
    Construye tabla de pesos para los 300 pares.
    Cada entrada:
        freq_full, freq_window, peso, z_window, rate_window
    """
    n_full = len(e1_history)
    w_draws = e1_history[-window:] if n_full >= window else e1_history
    n_w = len(w_draws)

    freq_full: dict[tuple, int] = {p: 0 for p in ALL_PAIRS}
    freq_win:  dict[tuple, int] = {p: 0 for p in ALL_PAIRS}

    for draw in e1_history:
        for a, b in combinations(draw, 2):
            freq_full[(a, b)] += 1

    for draw in w_draws:
        for a, b in combinations(draw, 2):
            freq_win[(a, b)] += 1

    exp_full = n_full * P_IID
    std_full = math.sqrt(n_full * P_IID * (1 - P_IID))
    exp_win  = n_w   * P_IID
    std_win  = math.sqrt(n_w * P_IID * (1 - P_IID))

    table = {}
    for p in ALL_PAIRS:
        fw  = freq_win[p]
        ff  = freq_full[p]
        rate_w = fw / n_w if n_w > 0 else 0.0
        peso   = rate_w / P_IID          # relativo al baseline IID
        z_w    = (fw - exp_win) / std_win if std_win > 0 else 0.0
        z_f    = (ff - exp_full) / std_full if std_full > 0 else 0.0
        table[p] = {
            "freq_full":   ff,
            "freq_window": fw,
            "rate_window": rate_w,
            "peso":        peso,
            "z_window":    z_w,
            "z_full":      z_f,
            "n_window":    n_w,
            "n_full":      n_full,
        }
    return table


# ---------------------------------------------------------------------------
# Set scoring
# ---------------------------------------------------------------------------

def score_set(s: list[int], table: dict[tuple, dict]) -> dict:
    """Agrega estadisticas de los 91 pares de un set."""
    pairs = [(min(a, b), max(a, b)) for a, b in combinations(s, 2)]
    pesos = [table[p]["peso"] for p in pairs]
    zs    = [table[p]["z_window"] for p in pairs]

    activos    = sum(1 for pw in pesos if pw > 1.0)
    inactivos  = sum(1 for pw in pesos if pw == 0.0)
    n_hot      = sum(1 for z in zs if z >= 2.0)
    n_warm     = sum(1 for z in zs if 0.75 <= z < 2.0)
    n_cold     = sum(1 for z in zs if -2.0 < z <= -0.75)
    n_frozen   = sum(1 for z in zs if z <= -2.0)

    pair_details = sorted(
        [(p, table[p]) for p in pairs],
        key=lambda x: -x[1]["peso"]
    )

    return {
        "n_pairs":    len(pairs),
        "peso_medio": sum(pesos) / len(pesos),
        "peso_sum":   sum(pesos),
        "z_medio":    sum(zs) / len(zs),
        "activos":    activos,
        "inactivos":  inactivos,
        "HOT":        n_hot,
        "WARM":       n_warm,
        "COLD":       n_cold,
        "FROZEN":     n_frozen,
        "pair_details": pair_details,
    }


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_regime_summary(table: dict, window: int) -> None:
    print(f"\n{'='*72}")
    print(f"REGIMEN DE PARES — ultimas {window} series E1")
    print(f"{'='*72}")
    print(f"  Baseline IID: P(par) = {P_IID:.4f}  |  peso=1.00x")
    print(f"  {len(ALL_PAIRS)} pares posibles  |  91 pares por set de 14 numeros")

    # Top activos
    top = sorted(table.items(), key=lambda x: -x[1]["peso"])
    print(f"\n  TOP 15 pares mas activos (L{window}):")
    print(f"  {'Par':>6}  {'Peso':>6}  {'L20':>4}  {'z_L20':>6}  {'Full':>5}  {'z_Full':>7}  Estado")
    for p, r in top[:15]:
        estado = "HOT" if r["z_window"] >= 2.0 else ("warm" if r["z_window"] >= 0.75 else ".")
        print(f"  {p[0]:02d}-{p[1]:02d}  {r['peso']:>6.2f}x  {r['freq_window']:>4}  "
              f"{r['z_window']:>+6.2f}  {r['freq_full']:>5}  {r['z_full']:>+7.2f}  {estado}")

    print(f"\n  BOTTOM 15 pares menos activos (L{window}):")
    print(f"  {'Par':>6}  {'Peso':>6}  {'L20':>4}  {'z_L20':>6}  {'Full':>5}  {'z_Full':>7}  Estado")
    for p, r in top[-15:]:
        estado = "FROZEN" if r["z_window"] <= -2.0 else ("cold" if r["z_window"] <= -0.75 else ".")
        print(f"  {p[0]:02d}-{p[1]:02d}  {r['peso']:>6.2f}x  {r['freq_window']:>4}  "
              f"{r['z_window']:>+6.2f}  {r['freq_full']:>5}  {r['z_full']:>+7.2f}  {estado}")


def print_ranking(family: list[list[int]], set_scores: list[dict], series_id: int) -> None:
    ranked = sorted(range(len(family)), key=lambda i: -set_scores[i]["peso_medio"])

    print(f"\n{'='*72}")
    print(f"RANKING DE SETS — alineacion con regimen actual (serie {series_id})")
    print(f"{'='*72}")
    print(f"  peso_medio = promedio de pesos de los 91 pares del set")
    print(f"  activos    = pares con peso > 1.0 (aparecen mas que baseline)")
    print()
    print(f"  {'Rk':>2}  {'Set':>4}  {'PesoMedio':>10}  {'Activos/91':>11}  {'HOT':>4}  {'warm':>5}  {'cold':>5}  {'FRZN':>5}")
    for rk, i in enumerate(ranked, 1):
        sc = set_scores[i]
        print(f"  {rk:>2}  S{i+1:<3}  {sc['peso_medio']:>10.4f}  "
              f"{sc['activos']:>5}/91      {sc['HOT']:>4}  {sc['WARM']:>5}  {sc['COLD']:>5}  {sc['FROZEN']:>5}")

    print()
    for rk, i in enumerate(ranked, 1):
        s  = family[i]
        sc = set_scores[i]
        nums = " ".join(f"{n:02d}" for n in s)
        print(f"  S{i+1} | rk#{rk} | peso={sc['peso_medio']:.4f} | {nums}")

        top5 = sc["pair_details"][:5]
        bot5 = sc["pair_details"][-5:]

        top_str = "  ".join(
            f"{p[0]:02d}-{p[1]:02d}(x{r['peso']:.2f},n={r['freq_window']})"
            for p, r in top5
        )
        bot_str = "  ".join(
            f"{p[0]:02d}-{p[1]:02d}(x{r['peso']:.2f},n={r['freq_window']})"
            for p, r in bot5
        )
        print(f"    + activos:   {top_str}")
        print(f"    - inactivos: {bot_str}")
        print()


def print_full_pairs(family: list[list[int]], set_scores: list[dict]) -> None:
    """Imprime todos los 91 pares de cada set ordenados por peso."""
    for i, (s, sc) in enumerate(zip(family, set_scores)):
        print(f"\n  S{i+1}: {' '.join(f'{n:02d}' for n in s)}")
        print(f"  {'Par':>6}  {'Peso':>6}  {'n_L20':>6}  {'z_L20':>7}  {'n_Full':>7}")
        for p, r in sc["pair_details"]:
            print(f"  {p[0]:02d}-{p[1]:02d}  {r['peso']:>6.2f}x  {r['freq_window']:>6}  "
                  f"{r['z_window']:>+7.2f}  {r['freq_full']:>7}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(series_id: int, window: int = 20, show_full: bool = False) -> None:
    print(f"Cargando historico E1 antes de draw {series_id}...")
    e1_history = fetch_e1_history(series_id)
    print(f"  {len(e1_history)} series E1 disponibles.  Ventana de regimen: L{window}")

    table = build_pair_table(e1_history, window=window)

    from designed_family_predictor import get_prediction
    family = get_prediction(series_id)

    set_scores = [score_set(s, table) for s in family]

    print_regime_summary(table, window)
    print_ranking(family, set_scores, series_id)

    if show_full:
        print(f"\n{'='*72}")
        print("DETALLE COMPLETO — todos los pares por set (ordenados por peso)")
        print(f"{'='*72}")
        print_full_pairs(family, set_scores)

    print(f"{'='*72}")


if __name__ == "__main__":
    args = sys.argv[1:]
    window    = 20
    show_full = False
    series_id = None

    i = 0
    while i < len(args):
        if args[i] == "--window" and i + 1 < len(args):
            window = int(args[i + 1]); i += 2
        elif args[i] == "--full":
            show_full = True; i += 1
        else:
            series_id = int(args[i]); i += 1

    if series_id is None:
        from db_query import load_data, latest
        series_id = latest(load_data()) + 1

    run(series_id, window=window, show_full=show_full)
