"""One-time ingestion: create dbo.Draws and load data/all_draws.json into it.

Run: python ml_models/db_init.py [--drop]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pyodbc

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "all_draws.json"

CONN_STR = (
    "Driver={ODBC Driver 17 for SQL Server};"
    "Server=DESKTOP-QR14EDK\\SQLEXPRESS01;"
    "Database=LuckyDb;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

DDL = """
IF OBJECT_ID('dbo.Draws', 'U') IS NOT NULL DROP TABLE dbo.Draws;
CREATE TABLE dbo.Draws (
    DrawId     INT        NOT NULL,
    EventIndex TINYINT    NOT NULL,
    N01 TINYINT NOT NULL, N02 TINYINT NOT NULL, N03 TINYINT NOT NULL,
    N04 TINYINT NOT NULL, N05 TINYINT NOT NULL, N06 TINYINT NOT NULL,
    N07 TINYINT NOT NULL, N08 TINYINT NOT NULL, N09 TINYINT NOT NULL,
    N10 TINYINT NOT NULL, N11 TINYINT NOT NULL, N12 TINYINT NOT NULL,
    N13 TINYINT NOT NULL, N14 TINYINT NOT NULL,
    DrawDate DATE NULL,
    Sources  NVARCHAR(50) NULL,
    CONSTRAINT PK_Draws PRIMARY KEY CLUSTERED (DrawId, EventIndex)
);
"""

INSERT = """
INSERT INTO dbo.Draws
  (DrawId, EventIndex,
   N01,N02,N03,N04,N05,N06,N07,N08,N09,N10,N11,N12,N13,N14,
   DrawDate, Sources)
VALUES (?, ?, ?,?,?,?,?,?,?,?,?,?,?,?,?,?, ?, ?)
"""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drop", action="store_true",
                    help="drop and recreate Draws table")
    args = ap.parse_args(argv)

    if not DATA.exists():
        print(f"[!] {DATA} not found", file=sys.stderr)
        return 2

    bundle = json.loads(DATA.read_text())
    draws = bundle["draws"]
    print(f"[i] loaded {len(draws)} draws from {DATA.name}")

    cn = pyodbc.connect(CONN_STR, autocommit=False)
    cur = cn.cursor()

    if args.drop:
        print("[i] dropping + recreating dbo.Draws")
        cur.execute(DDL)
        cn.commit()
    else:
        cur.execute("SELECT OBJECT_ID('dbo.Draws','U')")
        if cur.fetchone()[0] is None:
            print("[i] creating dbo.Draws")
            cur.execute(DDL)
            cn.commit()

    rows: list[tuple] = []
    for draw_id_str, payload in draws.items():
        draw_id = int(draw_id_str)
        date = payload.get("date")
        sources = ",".join(payload.get("sources", []))
        for ev_idx, event in enumerate(payload.get("events", []), start=1):
            if len(event) != 14:
                print(f"[!] skip draw {draw_id} event {ev_idx}: len={len(event)}")
                continue
            nums = sorted(int(x) for x in event)
            rows.append((draw_id, ev_idx, *nums, date, sources))

    print(f"[i] prepared {len(rows)} event rows for insert")
    cur.fast_executemany = True
    cur.executemany(INSERT, rows)
    cn.commit()

    cur.execute("SELECT COUNT(*), COUNT(DISTINCT DrawId) FROM dbo.Draws")
    n_events, n_draws = cur.fetchone()
    cur.execute("SELECT MIN(DrawId), MAX(DrawId) FROM dbo.Draws")
    mn, mx = cur.fetchone()
    print(f"[ok] dbo.Draws: {n_events:,} events across {n_draws:,} draws "
          f"(DrawId {mn}..{mx})")
    cn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
