# ------------------------------------------------------------
# wms_compare.py
# Purpose : Compare two WMS (WMS_DB) databases — the CURRENT production
#           server vs. the NEW migrated/upgraded server — and report every
#           structural difference. Built for the 2026 platform upgrade where
#           the vendor claims "schema unchanged": this tool VERIFIES that
#           claim and flags ANY structural delta as a finding.
# Author  : Joshua Ross (assisted)   Created: 2026-06-02
#
# What it fingerprints per database:
#   - DB properties (compatibility level, collation, recovery model)
#   - Object inventory (tables/views/procs/functions/triggers)
#   - Module definitions (SP/view/function/trigger bodies, normalized + hashed)
#   - Table columns (type, length, precision, scale, nullability, identity)
#   - Indexes (key columns, unique, primary)
#   - Foreign keys, PK/unique/check/default constraints
#   - Sequences
#   - Row counts per table (INFORMATIONAL — data volume legitimately differs)
#   - Database role memberships + explicit permissions
#
# Two connection targets (SQL auth):
#   OLD  (current prod) : server/db constants below, creds WMS_USER / WMS_PASS
#   NEW  (migrated)     : WMS_NEW_SERVER / WMS_NEW_DB / WMS_NEW_USER / WMS_NEW_PASS
#
# Usage:
#   # 1) Capture a baseline of the current system today:
#   python wms_compare.py snapshot --target old --out ..\migration\snapshots\old.json
#
#   # 2) When the new server is up, snapshot it the same way:
#   python wms_compare.py snapshot --target new --out ..\migration\snapshots\new.json
#
#   # 3) Diff the two and produce an HTML report:
#   python wms_compare.py compare ..\migration\snapshots\old.json ^
#          ..\migration\snapshots\new.json --html ..\migration\reports\compare.html
#
#   # Or diff both live in one shot:
#   python wms_compare.py compare --live --html ..\migration\reports\compare.html
#
# Read-only: every dbo.* read uses WITH (NOLOCK). No writes of any kind.
# ------------------------------------------------------------

import os
import re
import sys
import json
import html
import hashlib
import argparse
import urllib.parse

import pandas as pd
from sqlalchemy import create_engine, text

# Load wmspython\.env (same mechanism as wms_connect.py); env vars still win.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
                override=False)
except ImportError:
    pass

DRIVER = "ODBC Driver 18 for SQL Server"

# Current production target defaults (matches wms_connect.py).
OLD = {
    "server": os.getenv("WMS_SERVER", "<DB_SERVER>"),
    "db":     os.getenv("WMS_DB", "WMS_DB"),
    "user":   os.getenv("WMS_USER", ""),
    "pwd":    os.getenv("WMS_PASS", ""),
    "label":  os.getenv("WMS_OLD_LABEL", "OLD (current prod)"),
}
# New migrated/upgraded target (fill these env vars when the box is ready).
NEW = {
    "server": os.getenv("WMS_NEW_SERVER", ""),
    "db":     os.getenv("WMS_NEW_DB", "WMS_DB"),
    "user":   os.getenv("WMS_NEW_USER", ""),
    "pwd":    os.getenv("WMS_NEW_PASS", ""),
    "label":  os.getenv("WMS_NEW_LABEL", "NEW (migrated)"),
}


def engine_for(target: dict):
    if not target["server"] or not target["user"]:
        sys.exit(f"FATAL: missing connection info for target '{target['label']}'. "
                 f"Set the server/user/pass env vars (see header).")
    odbc = (f"Driver={{{DRIVER}}};Server={target['server']};Database={target['db']};"
            f"UID={target['user']};PWD={target['pwd']};"
            "TrustServerCertificate=yes;Encrypt=yes;")
    url = "mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote_plus(odbc)
    return create_engine(url)


def q(engine, sql: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(text(sql), conn)


def norm_def(d: str) -> str:
    """Normalize a module body so cosmetic diffs (line endings, trailing
    whitespace, blank-line runs) don't register as changes."""
    if d is None:
        return ""
    d = d.replace("\r\n", "\n").replace("\r", "\n")
    d = "\n".join(line.rstrip() for line in d.split("\n"))
    d = re.sub(r"\n{3,}", "\n\n", d).strip()
    return d


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()[:16]


# ---- Fingerprint queries (all read-only, NOLOCK) -------------------------

SQL_DBPROPS = """
SELECT compatibility_level, collation_name, recovery_model_desc
FROM sys.databases WITH (NOLOCK) WHERE name = DB_NAME();"""

SQL_OBJECTS = """
SELECT s.name AS [schema], o.name, o.type_desc
FROM sys.objects o WITH (NOLOCK)
JOIN sys.schemas s WITH (NOLOCK) ON s.schema_id = o.schema_id
WHERE o.is_ms_shipped = 0 AND o.type IN ('U','V','P','FN','IF','TF','TR')
ORDER BY o.type_desc, s.name, o.name;"""

SQL_MODULES = """
SELECT s.name AS [schema], o.name, o.type_desc, m.definition
FROM sys.sql_modules m WITH (NOLOCK)
JOIN sys.objects o WITH (NOLOCK) ON o.object_id = m.object_id
JOIN sys.schemas s WITH (NOLOCK) ON s.schema_id = o.schema_id
WHERE o.is_ms_shipped = 0;"""

SQL_COLUMNS = """
SELECT s.name AS [schema], t.name AS [table], c.column_id, c.name AS [column],
       ty.name AS data_type, c.max_length, c.precision, c.scale,
       c.is_nullable, c.is_identity
FROM sys.columns c WITH (NOLOCK)
JOIN sys.tables t WITH (NOLOCK) ON t.object_id = c.object_id
JOIN sys.schemas s WITH (NOLOCK) ON s.schema_id = t.schema_id
JOIN sys.types ty WITH (NOLOCK) ON ty.user_type_id = c.user_type_id
ORDER BY t.name, c.column_id;"""

SQL_INDEXES = """
SELECT s.name AS [schema], t.name AS [table], i.name AS index_name,
       i.type_desc, i.is_unique, i.is_primary_key,
       STUFF((SELECT ',' + col.name + CASE WHEN ic.is_descending_key=1 THEN ' DESC' ELSE '' END
              FROM sys.index_columns ic WITH (NOLOCK)
              JOIN sys.columns col WITH (NOLOCK)
                ON col.object_id = ic.object_id AND col.column_id = ic.column_id
              WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id
                AND ic.is_included_column = 0
              ORDER BY ic.key_ordinal FOR XML PATH('')), 1, 1, '') AS key_cols
FROM sys.indexes i WITH (NOLOCK)
JOIN sys.tables t WITH (NOLOCK) ON t.object_id = i.object_id
JOIN sys.schemas s WITH (NOLOCK) ON s.schema_id = t.schema_id
WHERE i.type > 0;"""

SQL_FKEYS = """
SELECT fk.name, OBJECT_NAME(fk.parent_object_id) AS parent_table,
       OBJECT_NAME(fk.referenced_object_id) AS referenced_table
FROM sys.foreign_keys fk WITH (NOLOCK);"""

SQL_CONSTRAINTS = """
SELECT name, 'CHECK' AS kind, OBJECT_NAME(parent_object_id) AS [table] FROM sys.check_constraints WITH (NOLOCK)
UNION ALL
SELECT name, 'DEFAULT', OBJECT_NAME(parent_object_id) FROM sys.default_constraints WITH (NOLOCK)
UNION ALL
SELECT name, type_desc, OBJECT_NAME(parent_object_id) FROM sys.key_constraints WITH (NOLOCK);"""

SQL_SEQUENCES = """
SELECT s.name AS [schema], o.name
FROM sys.sequences o WITH (NOLOCK)
JOIN sys.schemas s WITH (NOLOCK) ON s.schema_id = o.schema_id;"""

SQL_ROWCOUNTS = """
SELECT t.name AS [table], SUM(p.rows) AS [rows]
FROM sys.tables t WITH (NOLOCK)
JOIN sys.partitions p WITH (NOLOCK) ON p.object_id = t.object_id AND p.index_id IN (0,1)
GROUP BY t.name;"""

SQL_ROLES = """
SELECT r.name AS role, m.name AS member
FROM sys.database_role_members rm WITH (NOLOCK)
JOIN sys.database_principals r WITH (NOLOCK) ON rm.role_principal_id = r.principal_id
JOIN sys.database_principals m WITH (NOLOCK) ON rm.member_principal_id = m.principal_id;"""

SQL_PERMS = """
SELECT dp.name AS grantee, pe.class_desc, pe.permission_name, pe.state_desc,
       ISNULL(OBJECT_SCHEMA_NAME(pe.major_id) + '.' + OBJECT_NAME(pe.major_id), '') AS object
FROM sys.database_permissions pe WITH (NOLOCK)
JOIN sys.database_principals dp WITH (NOLOCK) ON pe.grantee_principal_id = dp.principal_id
WHERE dp.type IN ('S','U','G');"""


def fingerprint(target: dict) -> dict:
    eng = engine_for(target)
    fp = {"label": target["label"], "server": target["server"], "db": target["db"]}

    props = q(eng, SQL_DBPROPS).iloc[0].to_dict()
    fp["dbprops"] = {k: str(v) for k, v in props.items()}

    fp["objects"] = {f"{r['schema']}.{r['name']}": r["type_desc"]
                     for _, r in q(eng, SQL_OBJECTS).iterrows()}

    fp["modules"] = {}
    for _, r in q(eng, SQL_MODULES).iterrows():
        fp["modules"][f"{r['schema']}.{r['name']}"] = sha(norm_def(r["definition"]))

    fp["columns"] = {}
    for _, r in q(eng, SQL_COLUMNS).iterrows():
        key = f"{r['table']}.{r['column']}"
        fp["columns"][key] = (f"{r['data_type']}({r['max_length']},{r['precision']},"
                              f"{r['scale']}) null={int(r['is_nullable'])} "
                              f"id={int(r['is_identity'])}")

    fp["indexes"] = {f"{r['table']}.{r['index_name']}":
                     f"{r['type_desc']} uniq={int(r['is_unique'])} "
                     f"pk={int(r['is_primary_key'])} cols=[{r['key_cols']}]"
                     for _, r in q(eng, SQL_INDEXES).iterrows() if r["index_name"]}

    fp["fkeys"] = {r["name"]: f"{r['parent_table']}->{r['referenced_table']}"
                   for _, r in q(eng, SQL_FKEYS).iterrows()}

    fp["constraints"] = {r["name"]: f"{r['kind']} on {r['table']}"
                         for _, r in q(eng, SQL_CONSTRAINTS).iterrows()}

    fp["sequences"] = {f"{r['schema']}.{r['name']}": "SEQUENCE"
                       for _, r in q(eng, SQL_SEQUENCES).iterrows()}

    fp["rowcounts"] = {r["table"]: int(r["rows"] or 0)
                       for _, r in q(eng, SQL_ROWCOUNTS).iterrows()}

    fp["roles"] = sorted(f"{r['member']}:{r['role']}"
                         for _, r in q(eng, SQL_ROLES).iterrows())
    fp["perms"] = sorted(f"{r['grantee']}|{r['permission_name']}|{r['state_desc']}|{r['object']}"
                         for _, r in q(eng, SQL_PERMS).iterrows())
    return fp


def diff_dict(old: dict, new: dict):
    """Return (only_old, only_new, changed) for two name->value dicts."""
    ok, nk = set(old), set(new)
    only_old = sorted(ok - nk)
    only_new = sorted(nk - ok)
    changed = sorted(k for k in (ok & nk) if old[k] != new[k])
    return only_old, only_new, changed


def diff_list(old: list, new: list):
    os_, ns_ = set(old), set(new)
    return sorted(os_ - ns_), sorted(ns_ - os_), []


# Sections compared. Each: (key, title, is_structural, is_dict)
SECTIONS = [
    ("objects",     "Object inventory (tables/views/procs/fns/triggers)", True,  True),
    ("modules",     "Module definitions (SP/view/fn/trigger bodies)",     True,  True),
    ("columns",     "Table columns",                                      True,  True),
    ("indexes",     "Indexes",                                            True,  True),
    ("fkeys",       "Foreign keys",                                       True,  True),
    ("constraints", "Constraints (PK/unique/check/default)",              True,  True),
    ("sequences",   "Sequences",                                          True,  True),
    ("roles",       "Database role memberships",                          True,  False),
    ("perms",       "Explicit permissions",                               True,  False),
    ("rowcounts",   "Row counts (INFORMATIONAL — volume may differ)",     False, True),
]


def compare(old: dict, new: dict) -> dict:
    report = {"old": {k: old[k] for k in ("label", "server", "db")},
              "new": {k: new[k] for k in ("label", "server", "db")},
              "dbprops": {"old": old.get("dbprops"), "new": new.get("dbprops")},
              "sections": []}
    for key, title, structural, is_dict in SECTIONS:
        o, n = old.get(key, {} if is_dict else []), new.get(key, {} if is_dict else [])
        if is_dict:
            only_old, only_new, changed = diff_dict(o, n)
            changed_detail = [{"key": k, "old": str(o[k]), "new": str(n[k])} for k in changed]
        else:
            only_old, only_new, _ = diff_list(o, n)
            changed_detail = []
        report["sections"].append({
            "key": key, "title": title, "structural": structural,
            "count_old": len(o), "count_new": len(n),
            "only_old": only_old, "only_new": only_new, "changed": changed_detail,
            "delta": len(only_old) + len(only_new) + len(changed_detail),
        })
    return report


def render_html(rep: dict) -> str:
    e = html.escape
    structural_delta = sum(s["delta"] for s in rep["sections"] if s["structural"])
    verdict = ("PASS — no structural differences" if structural_delta == 0
               else f"REVIEW — {structural_delta} structural difference(s) found")
    vcolor = "#1a7f37" if structural_delta == 0 else "#b35900"

    rows = []
    for s in rep["sections"]:
        color = "#1a7f37" if s["delta"] == 0 else ("#b35900" if s["structural"] else "#555")
        rows.append(f"<tr><td>{e(s['title'])}</td><td style='text-align:center'>{s['count_old']}</td>"
                    f"<td style='text-align:center'>{s['count_new']}</td>"
                    f"<td style='text-align:center;color:{color};font-weight:600'>{s['delta']}</td>"
                    f"<td style='text-align:center'>{'structural' if s['structural'] else 'info'}</td></tr>")
    summary_tbl = "".join(rows)

    details = []
    for s in rep["sections"]:
        if s["delta"] == 0:
            continue
        blocks = []
        if s["only_old"]:
            items = "".join(f"<li>{e(x)}</li>" for x in s["only_old"][:500])
            blocks.append(f"<p><b>Only in {e(rep['old']['label'])} (missing from new):</b></p><ul class='r'>{items}</ul>")
        if s["only_new"]:
            items = "".join(f"<li>{e(x)}</li>" for x in s["only_new"][:500])
            blocks.append(f"<p><b>Only in {e(rep['new']['label'])} (extra in new):</b></p><ul class='y'>{items}</ul>")
        if s["changed"]:
            items = "".join(f"<li>{e(c['key'])}<br><span class='o'>old:</span> {e(c['old'])}"
                            f"<br><span class='n'>new:</span> {e(c['new'])}</li>" for c in s["changed"][:500])
            blocks.append(f"<p><b>Changed:</b></p><ul class='c'>{items}</ul>")
        details.append(f"<h3>{e(s['title'])} — {s['delta']} difference(s)</h3>{''.join(blocks)}")

    dbp_old, dbp_new = rep["dbprops"]["old"] or {}, rep["dbprops"]["new"] or {}
    dbp_keys = sorted(set(dbp_old) | set(dbp_new))
    dbp_rows = "".join(
        f"<tr><td>{e(k)}</td><td>{e(str(dbp_old.get(k,'')))}</td><td>{e(str(dbp_new.get(k,'')))}</td>"
        f"<td style='text-align:center'>{'OK' if dbp_old.get(k)==dbp_new.get(k) else '&#9888;'}</td></tr>"
        for k in dbp_keys)

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>WMS Migration Comparison</title>
<style>
 body{{font-family:Segoe UI,Arial,sans-serif;margin:2rem;color:#1c1c1c}}
 h1{{margin-bottom:.2rem}} .sub{{color:#666;margin-top:0}}
 .verdict{{font-size:1.2rem;font-weight:700;padding:.6rem 1rem;border-radius:6px;
   display:inline-block;color:#fff;background:{vcolor}}}
 table{{border-collapse:collapse;margin:1rem 0;width:100%}}
 th,td{{border:1px solid #ddd;padding:.4rem .6rem;font-size:.9rem;vertical-align:top}}
 th{{background:#f3f3f3;text-align:left}}
 ul.r li{{color:#b30000}} ul.y li{{color:#946200}} ul.c li{{margin-bottom:.5rem}}
 .o{{color:#b30000;font-weight:600}} .n{{color:#1a7f37;font-weight:600}}
 ul{{max-height:420px;overflow:auto;border:1px solid #eee;padding:.5rem 1.5rem}}
 code{{background:#f3f3f3;padding:.1rem .3rem}}
</style></head><body>
<h1>WMS Migration — System Comparison</h1>
<p class="sub"><b>{e(rep['old']['label'])}</b> &nbsp;<code>{e(rep['old']['server'])}/{e(rep['old']['db'])}</code>
 &nbsp;vs&nbsp; <b>{e(rep['new']['label'])}</b> &nbsp;<code>{e(rep['new']['server'])}/{e(rep['new']['db'])}</code></p>
<p class="verdict">{e(verdict)}</p>
<h2>Database properties</h2>
<table><tr><th>Property</th><th>Old</th><th>New</th><th>Match</th></tr>{dbp_rows}</table>
<h2>Summary</h2>
<table><tr><th>Section</th><th>Old #</th><th>New #</th><th>Differences</th><th>Type</th></tr>{summary_tbl}</table>
<h2>Details</h2>
{''.join(details) if details else '<p>No differences in any section. &#9989;</p>'}
<p class="sub">Structural sections must show 0 for a clean "schema unchanged" migration.
Row-count differences are informational (data volume legitimately differs by environment).</p>
</body></html>"""


def cmd_snapshot(a):
    target = OLD if a.target == "old" else NEW
    fp = fingerprint(target)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(fp, f, indent=1)
    print(f"Snapshot of {fp['label']} ({fp['server']}/{fp['db']}) written: {a.out}")
    print(f"  objects={len(fp['objects'])} modules={len(fp['modules'])} "
          f"columns={len(fp['columns'])} indexes={len(fp['indexes'])} "
          f"fkeys={len(fp['fkeys'])} constraints={len(fp['constraints'])}")


def cmd_compare(a):
    if a.live:
        old, new = fingerprint(OLD), fingerprint(NEW)
    else:
        with open(a.old_json, encoding="utf-8") as f: old = json.load(f)
        with open(a.new_json, encoding="utf-8") as f: new = json.load(f)
    rep = compare(old, new)
    structural_delta = sum(s["delta"] for s in rep["sections"] if s["structural"])

    print(f"\n  {old['label']}  vs  {new['label']}")
    print("  " + "-" * 60)
    for s in rep["sections"]:
        flag = "" if s["delta"] == 0 else ("  <-- REVIEW" if s["structural"] else "  (info)")
        print(f"  {s['title'][:48]:<48} {s['delta']:>5}{flag}")
    print("  " + "-" * 60)
    print(f"  STRUCTURAL DIFFERENCES: {structural_delta}  "
          f"({'PASS' if structural_delta == 0 else 'REVIEW REQUIRED'})")

    if a.html:
        os.makedirs(os.path.dirname(os.path.abspath(a.html)), exist_ok=True)
        with open(a.html, "w", encoding="utf-8") as f:
            f.write(render_html(rep))
        print(f"\n  HTML report: {a.html}")
    sys.exit(0 if structural_delta == 0 else 2)


def main():
    p = argparse.ArgumentParser(description="Compare two WMS (WMS_DB) databases.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="Fingerprint one database to JSON.")
    s.add_argument("--target", choices=["old", "new"], required=True)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_snapshot)

    c = sub.add_parser("compare", help="Diff two snapshots (or both live).")
    c.add_argument("old_json", nargs="?")
    c.add_argument("new_json", nargs="?")
    c.add_argument("--live", action="store_true", help="Fingerprint both targets live instead of reading JSON.")
    c.add_argument("--html", help="Write an HTML report to this path.")
    c.set_defaults(func=cmd_compare)

    a = p.parse_args()
    if a.cmd == "compare" and not a.live and not (a.old_json and a.new_json):
        p.error("compare needs two snapshot files, or --live.")
    a.func(a)


if __name__ == "__main__":
    main()
