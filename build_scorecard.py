# ------------------------------------------------------------
# build_scorecard.py
# Purpose : Regenerate the Operator Productivity Scorecard dashboard from
#           live WMS (WMS_DB) production data. Replaces the Cowork scheduled
#           "wms-operator-productivity-refresh" task with a local, on-demand
#           generator that reuses wms_connect.py (same connection as the
#           wms MCP server and the watcher).
#
# Usage   : python build_scorecard.py [YYYY-MM-DD]
#           No date  -> yesterday (relative to today).
#           A date   -> that calendar day.
#
# Output  : ..\dashboards\scorecard_<date>.html  (self-contained HTML)
#
# Metric definitions (validated against the 2026-05-26 Cowork snapshot,
# 5 of 6 activity categories matched exactly on tasks/units/operators):
#   task         = one t_tran_log row in the curated tran_type set
#   units (qty)  = SUM(tran_qty)
#   active hours = SUM(elapsed_time) / 3600
#   tasks/hr     = tasks / active hours
#   day          = CAST(start_tran_date AS date)
#
# Curated tran_type -> activity mapping:
#   PICK        301 (Picking Pick)        + 211 (Directed Pickup Pick)
#   PUTAWAY     154 (Receipt Put)         + 212 (Directed Pickup Put)
#   LOAD_SHIP   321 (Loading Pick)        + 341 (Shipping Detail)
#   MOVE        201 (Move Pick)
#   RECEIVE     153 (Receipt of Shipment Rcpt)
#   CYCLE_COUNT 880 (Physical Inventory)
# Read-only: every dbo.* read uses WITH (NOLOCK).
# ------------------------------------------------------------

import os
import re
import sys
import json
import datetime as dt

import pandas as pd
from sqlalchemy import text

from wms_connect import get_engine

HERE        = os.path.dirname(os.path.abspath(__file__))
PROJECT     = os.path.dirname(HERE)                       # ...\Desktop\WMS
SOURCE_HTML = os.path.join(PROJECT, "WMS_Operator_Productivity_Scorecard_2026-05-26.html")
TEMPLATE    = os.path.join(HERE, "scorecard_template.html")
OUT_DIR     = os.path.join(PROJECT, "dashboards")

DATA_NAMES = ["GENERATED_AT", "COVERED_DATE", "OPS", "TREND", "MIX", "BY_WH", "ACT_WH"]

# Activity category -> tran_type codes (curated; one leg per logical task).
CAT_CODES = {
    "PICK":        ["301", "211"],
    "PUTAWAY":     ["154", "212"],
    "LOAD_SHIP":   ["321", "341"],
    "MOVE":        ["201"],
    "RECEIVE":     ["153"],
    "CYCLE_COUNT": ["880"],
}
# field name used by the dashboard JS for each category's per-operator count
CAT_FIELD = {"PICK": "pick", "RECEIVE": "receive", "PUTAWAY": "putaway",
             "MOVE": "move", "LOAD_SHIP": "load", "CYCLE_COUNT": "cycle"}

ALL_CODES = [c for codes in CAT_CODES.values() for c in codes]
CODES_IN  = ",".join(f"'{c}'" for c in ALL_CODES)

# SQL CASE that maps tran_type -> category, built from CAT_CODES.
_CASE = "CASE tran_type\n" + "\n".join(
    f"      WHEN '{c}' THEN '{cat}'" for cat, codes in CAT_CODES.items() for c in codes
) + "\n    END"


def ensure_template():
    """Derive scorecard_template.html from the Cowork artifact once: blank the
    seven data constants to __DATA_X__ tokens, keep all markup/CSS/JS intact."""
    if os.path.exists(TEMPLATE):
        return
    if not os.path.exists(SOURCE_HTML):
        sys.exit(f"FATAL: template missing and source artifact not found:\n  {SOURCE_HTML}")
    with open(SOURCE_HTML, encoding="utf-8") as f:
        html = f.read()
    for n in DATA_NAMES:
        html, n_sub = re.subn(rf"(?m)^const {n} = .*;$", f"const {n} = __DATA_{n}__;", html)
        if n_sub != 1:
            sys.exit(f"FATAL: expected exactly one 'const {n} =' line, found {n_sub}.")
    with open(TEMPLATE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Created template: {TEMPLATE}")


def run(engine, sql, params):
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


def tph(tx, sec):
    hours = (sec or 0) / 3600.0
    return round(tx / hours, 3) if hours > 0 else 0.0


def build(covered_date: str) -> dict:
    engine = get_engine()
    p = {"d": covered_date}
    where = f"CAST(start_tran_date AS date) = :d AND tran_type IN ({CODES_IN})"

    # --- Per-operator aggregates (+ per-category task counts) ---
    cat_cols = ",\n        ".join(
        f"SUM(CASE WHEN tl.tran_type IN ({','.join(repr(c) for c in codes)}) THEN 1 ELSE 0 END) AS {CAT_FIELD[cat]}"
        for cat, codes in CAT_CODES.items()
    )
    ops_df = run(engine, f"""
        SELECT tl.employee_id AS id,
               COALESCE(MAX(e.name), tl.employee_id) AS name,
               COUNT(*) AS tx,
               CAST(SUM(tl.tran_qty) AS int) AS qty,
               SUM(tl.elapsed_time) AS sec,
               {cat_cols}
        FROM t_tran_log tl WITH (NOLOCK)
        LEFT JOIN t_employee e WITH (NOLOCK) ON e.employee_id = tl.employee_id
        WHERE CAST(tl.start_tran_date AS date) = :d
          AND tl.tran_type IN ({CODES_IN})
        GROUP BY tl.employee_id
    """, p)

    # operator's busiest warehouse that day (modal wh by task count)
    whcnt = run(engine, f"""
        SELECT employee_id, wh_id, COUNT(*) AS c
        FROM t_tran_log WITH (NOLOCK)
        WHERE {where}
        GROUP BY employee_id, wh_id
    """, p)
    home_wh = (whcnt.sort_values("c", ascending=False)
                    .drop_duplicates("employee_id")
                    .set_index("employee_id")["wh_id"].to_dict())

    OPS = []
    for _, r in ops_df.iterrows():
        sec = int(r["sec"] or 0)
        rec = {
            "id":   str(r["id"]).strip(),
            "name": (str(r["name"]).strip() or str(r["id"]).strip()),
            "wh":   str(home_wh.get(r["id"], "")).strip(),
            "tx":   int(r["tx"]),
            "qty":  int(r["qty"] or 0),
            "hours": round(sec / 3600.0, 2),
            "tph":   tph(int(r["tx"]), sec),
        }
        for fld in CAT_FIELD.values():
            rec[fld] = int(r[fld])
        OPS.append(rec)
    OPS.sort(key=lambda o: o["tph"], reverse=True)

    # --- Activity mix (per category) ---
    mix_df = run(engine, f"""
        SELECT {_CASE} AS cat, COUNT(*) AS tx,
               CAST(SUM(tran_qty) AS int) AS qty,
               COUNT(DISTINCT employee_id) AS ops
        FROM t_tran_log WITH (NOLOCK)
        WHERE {where}
        GROUP BY {_CASE}
    """, p)
    MIX = [{"cat": r["cat"], "tx": int(r["tx"]), "qty": int(r["qty"] or 0), "ops": int(r["ops"])}
           for _, r in mix_df.iterrows()]
    MIX.sort(key=lambda m: m["cat"])

    # --- Activity x warehouse ---
    actwh_df = run(engine, f"""
        SELECT {_CASE} AS cat, wh_id AS wh, COUNT(*) AS tx,
               CAST(SUM(tran_qty) AS int) AS qty, SUM(elapsed_time) AS sec
        FROM t_tran_log WITH (NOLOCK)
        WHERE {where}
        GROUP BY {_CASE}, wh_id
    """, p)
    ACT_WH = [{"cat": r["cat"], "wh": str(r["wh"]).strip(), "tx": int(r["tx"]),
               "qty": int(r["qty"] or 0), "sec": int(r["sec"] or 0)}
              for _, r in actwh_df.iterrows()]

    # --- By-warehouse summary ---
    bywh_df = run(engine, f"""
        SELECT wh_id AS wh, COUNT(*) AS tx, SUM(elapsed_time) AS sec
        FROM t_tran_log WITH (NOLOCK)
        WHERE {where}
        GROUP BY wh_id
    """, p)
    BY_WH = [{"wh": str(r["wh"]).strip(), "tx": int(r["tx"]), "tph": tph(int(r["tx"]), int(r["sec"] or 0))}
             for _, r in bywh_df.iterrows()]
    BY_WH.sort(key=lambda w: w["tx"], reverse=True)

    # --- 7-day trend (ending on covered_date) ---
    trend_df = run(engine, f"""
        SELECT CONVERT(varchar(10), CAST(start_tran_date AS date), 23) AS date,
               COUNT(*) AS tx, SUM(elapsed_time) AS sec
        FROM t_tran_log WITH (NOLOCK)
        WHERE CAST(start_tran_date AS date) BETWEEN DATEADD(day, -6, :d) AND :d
          AND tran_type IN ({CODES_IN})
        GROUP BY CAST(start_tran_date AS date)
        ORDER BY CAST(start_tran_date AS date)
    """, p)
    TREND = [{"date": str(r["date"]), "tx": int(r["tx"]), "tph": tph(int(r["tx"]), int(r["sec"] or 0))}
             for _, r in trend_df.iterrows()]

    return {
        "GENERATED_AT": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "COVERED_DATE": covered_date,
        "OPS": OPS, "TREND": TREND, "MIX": MIX, "BY_WH": BY_WH, "ACT_WH": ACT_WH,
    }


def render(data: dict) -> str:
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()
    for name in DATA_NAMES:
        token = f"__DATA_{name}__"
        if token not in html:
            sys.exit(f"FATAL: token {token} not found in template.")
        html = html.replace(token, json.dumps(data[name]))
    return html


def main():
    if len(sys.argv) > 1:
        covered = sys.argv[1]
        dt.date.fromisoformat(covered)            # validate format
    else:
        covered = (dt.date.today() - dt.timedelta(days=1)).isoformat()

    ensure_template()
    data = build(covered)

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, f"scorecard_{covered}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(render(data))

    # Console summary (and validation oracle when run for 2026-05-26)
    print(f"\nCovered date : {covered}")
    print(f"Operators    : {len(data['OPS'])}")
    print(f"Activity mix (tx / units / operators):")
    for m in sorted(data["MIX"], key=lambda x: -x["tx"]):
        print(f"  {m['cat']:<12} {m['tx']:>8,}  {m['qty']:>10,}  {m['ops']:>4}")
    print(f"\nDashboard written: {out_path}")


if __name__ == "__main__":
    main()
