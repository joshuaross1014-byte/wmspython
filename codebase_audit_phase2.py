"""
WMS Codebase Intelligence — Phase 2: Audit findings
===================================================
Scans every SP body for known anti-patterns and produces findings.
Reads sys.sql_modules + the existing lineage_map.json (for called_by lookups).

Outputs to the audit directory (default: ./codebase_audit beside this script,
overridable with WMS_AUDIT_DIR):
  - audit_findings.json   — array of finding objects
  - audit_findings.csv    — same, flattened

Read-only against the DB. No mutations (confirm-before-write safety rule).
"""
import os, re, sys, csv, json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wms_connect import run_query

OUT_DIR = Path(os.getenv("WMS_AUDIT_DIR", Path(__file__).resolve().parent / "codebase_audit"))

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def strip_comments(sql: str) -> str:
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    sql = re.sub(r'--[^\n]*', '', sql)
    return sql

# Entry-point name patterns — SPs matching these are NOT flagged as never-called
ENTRYPOINT_PATTERNS = [
    re.compile(r'^usp_hr_',    re.IGNORECASE),   # RF handheld
    re.compile(r'^usp_rf_',    re.IGNORECASE),   # RF gun
    re.compile(r'^sp_blitz',   re.IGNORECASE),   # 3rd party DBA tools
    re.compile(r'^sp_whoisactive$', re.IGNORECASE),
    re.compile(r'^sp_executesql$',  re.IGNORECASE),
    re.compile(r'^xp_',        re.IGNORECASE),   # extended procs
    re.compile(r'^get_',       re.IGNORECASE),   # report/UI entry
    re.compile(r'^p_',         re.IGNORECASE),   # informal entry
    re.compile(r'_report$',    re.IGNORECASE),
    re.compile(r'^exportdata$',re.IGNORECASE),
    re.compile(r'^search$',    re.IGNORECASE),
    re.compile(r'^usp_pick',   re.IGNORECASE),
    re.compile(r'^usp_put',    re.IGNORECASE),
    re.compile(r'^usp_move',   re.IGNORECASE),
    re.compile(r'^usp_load',   re.IGNORECASE),
    re.compile(r'^usp_recv',   re.IGNORECASE),
    re.compile(r'^usp_ship',   re.IGNORECASE),
    re.compile(r'^usp_cc',     re.IGNORECASE),
]

def is_entrypoint(name: str) -> bool:
    return any(p.search(name) for p in ENTRYPOINT_PATTERNS)

def line_around(body: str, pos: int, ctx: int = 80) -> str:
    """Return the line containing pos, trimmed and cleaned."""
    line_start = body.rfind('\n', 0, pos) + 1
    line_end   = body.find('\n', pos)
    if line_end == -1: line_end = len(body)
    snippet = body[line_start:line_end].strip()
    if len(snippet) > 160:
        # center around the position
        rel = pos - line_start
        a = max(0, rel - ctx)
        b = min(len(snippet), rel + ctx)
        snippet = ('…' if a > 0 else '') + snippet[a:b] + ('…' if b < len(snippet) else '')
    return snippet

# ----------------------------------------------------------------------
# Audit checks — each returns a list of finding dicts
# ----------------------------------------------------------------------
# --------------------------------------------------------------------
# Missing-NOLOCK detector (tightened 2026-05-21).
#
# The earlier version matched FROM/JOIN <table> and then peeked the next
# ~80 chars for WITH (NOLOCK). That mis-flagged the common pattern
# `FROM dbo.t_foo f WITH (NOLOCK)` because the alias `f` showed up before
# the hint.
#
# The new detector parses table + optional alias + optional table-hint as
# a single unit, so the alias is recognized as part of the read instead of
# pushing the hint outside the inspection window. It also accepts the
# READUNCOMMITTED hint (semantically equivalent to NOLOCK) and combined
# hint forms like WITH (NOLOCK, INDEX(ix_id)).
# --------------------------------------------------------------------
_NOT_AN_ALIAS = (
    r'(?!WITH\b|WHERE\b|ON\b|JOIN\b|INNER\b|LEFT\b|RIGHT\b|FULL\b|CROSS\b|OUTER\b'
    r'|GROUP\b|ORDER\b|HAVING\b|UNION\b|EXCEPT\b|INTERSECT\b|AND\b|OR\b|SET\b'
    r'|INTO\b|VALUES\b|HINT\b|AS\b)'
)
RE_READ_WITH_OPTIONAL_HINT = re.compile(
    r'\b(?:FROM|JOIN|USING)\s+'                                          # FROM / JOIN / MERGE USING
    r'(?:\[?dbo\]?\.)?\[?(t_\w+)\]?'                                     # group 1: table name
    r'(?:\s+(?:AS\s+\[?\w+\]?|' + _NOT_AN_ALIAS + r'\[?\w+\]?))?'        # optional alias
    r'(\s+WITH\s*\(\s*(?:NOLOCK|READUNCOMMITTED)[^)]*\))?',              # group 2: NOLOCK/READUNCOMMITTED hint
    re.IGNORECASE
)

# Comma-continued table list in a FROM clause: ", dbo.t_foo [alias] [WITH (...)]"
RE_READ_COMMA_WITH_OPTIONAL_HINT = re.compile(
    r',\s+'
    r'(?:\[?dbo\]?\.)?\[?(t_\w+)\]?'                                     # group 1: table name
    r'(?:\s+(?:AS\s+\[?\w+\]?|' + _NOT_AN_ALIAS + r'\[?\w+\]?))?'
    r'(\s+WITH\s*\(\s*(?:NOLOCK|READUNCOMMITTED)[^)]*\))?',
    re.IGNORECASE
)

def check_missing_nolock(name, body):
    findings = []
    for regex in (RE_READ_WITH_OPTIONAL_HINT, RE_READ_COMMA_WITH_OPTIONAL_HINT):
        for m in regex.finditer(body):
            tbl = m.group(1).lower()
            hint = m.group(2)
            if hint:
                continue
            findings.append({
                'sp': name, 'severity': 'med', 'category': 'missing_nolock',
                'detail': f'{m.group(0).strip()[:120]} (no NOLOCK/READUNCOMMITTED hint)',
                'target': tbl, 'snippet': line_around(body, m.start())
            })
    return findings

RE_CURSOR = re.compile(r'\bDECLARE\s+\w+\s+(?:CURSOR|SCROLL\s+CURSOR)', re.IGNORECASE)
def check_cursor(name, body):
    findings = []
    for m in RE_CURSOR.finditer(body):
        findings.append({
            'sp': name, 'severity': 'high', 'category': 'cursor_usage',
            'detail': 'DECLARE CURSOR found',
            'target': '', 'snippet': line_around(body, m.start())
        })
    return findings

RE_DEPRECATED = [
    (re.compile(r'\b(t_\w+_\d{8})\b'),                'date_suffixed'),
    (re.compile(r'\b(t_\w+_(?:OLD|BAK|BACKUP|TEMP|TMP|ARCHIVE|ARCH))\b', re.IGNORECASE), 'name_suffixed'),
    (re.compile(r'\b((?:sp|usp|p)_\w+_(?:OLD|BAK|BACKUP|TEMP|TMP))\b', re.IGNORECASE),    'sp_suffixed'),
]
def check_deprecated(name, body):
    findings = []
    seen = set()
    for rx, kind in RE_DEPRECATED:
        for m in rx.finditer(body):
            tok = m.group(1)
            if tok.lower() in seen: continue
            seen.add(tok.lower())
            findings.append({
                'sp': name, 'severity': 'med', 'category': 'deprecated_ref',
                'detail': f'{kind}: {tok}',
                'target': tok.lower(), 'snippet': line_around(body, m.start())
            })
    return findings

# Implicit-conversion heuristics:
#  - "id-like" column compared to a quoted literal: e.g.,  hold_id = '5'
#  - quoted literal compared to "id-like" column:   e.g.,  '5' = hold_id
RE_IMPLICIT_ID_TO_QSTR = re.compile(
    r"\b(\w*?_id|hu_id|sto_id|item_number|wh_id|lot_number)\s*=\s*'(\d+)'",
    re.IGNORECASE
)
def check_implicit_convert(name, body):
    findings = []
    seen = set()
    for m in RE_IMPLICIT_ID_TO_QSTR.finditer(body):
        col = m.group(1).lower()
        # Skip identifiers known to be nvarchar (wh_id, hu_id, item_number, lot_number)
        if col in ('wh_id', 'hu_id', 'item_number', 'lot_number', 'sto_id'):
            continue  # these are nvarchar — quoted literal is correct
        key = (col, m.group(0))
        if key in seen: continue
        seen.add(key)
        findings.append({
            'sp': name, 'severity': 'low', 'category': 'implicit_conv_candidate',
            'detail': f"{col} compared to quoted literal '{m.group(2)}' — verify column type",
            'target': col, 'snippet': line_around(body, m.start())
        })
    return findings

RE_DEAD_IF_FALSE = re.compile(r'\bIF\s+(?:1\s*=\s*0|0\s*=\s*1)\b', re.IGNORECASE)
RE_GOTO         = re.compile(r'\bGOTO\s+(\w+)\b', re.IGNORECASE)
RE_LABEL        = re.compile(r'(?m)^\s*(\w+)\s*:\s*$')
def check_dead_code(name, body):
    findings = []
    for m in RE_DEAD_IF_FALSE.finditer(body):
        findings.append({
            'sp': name, 'severity': 'low', 'category': 'dead_code',
            'detail': 'IF 1=0 / IF 0=1 — unreachable branch',
            'target': '', 'snippet': line_around(body, m.start())
        })
    # GOTO labels that don't exist
    gotos = [(m.group(1).lower(), m.start()) for m in RE_GOTO.finditer(body)]
    labels = {m.group(1).lower() for m in RE_LABEL.finditer(body)}
    for label, pos in gotos:
        if label not in labels:
            findings.append({
                'sp': name, 'severity': 'low', 'category': 'dead_code',
                'detail': f'GOTO {label} — no matching label',
                'target': label, 'snippet': line_around(body, pos)
            })
    return findings

# ----------------------------------------------------------------------
def main():
    t0 = datetime.now()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Load called_by from Phase 1
    lineage = json.loads((OUT_DIR / 'lineage_map.json').read_text(encoding='utf-8'))
    called_by = {k: v['called_by'] for k, v in lineage['sps'].items()}

    print(f"[{t0:%H:%M:%S}] Pulling SP bodies for audit...")
    df = run_query("""
        SELECT o.name AS sp_name, m.definition AS body
        FROM sys.sql_modules m WITH (NOLOCK)
        JOIN sys.objects o WITH (NOLOCK) ON o.object_id = m.object_id
        WHERE o.type = 'P' AND o.is_ms_shipped = 0
        ORDER BY o.name
    """)
    print(f"  -> {len(df)} SPs scanned.")

    findings = []
    sp_audit_count = {}
    for _, row in df.iterrows():
        actual_name = row['sp_name']
        body = row['body'] or ''
        body_clean = strip_comments(body)

        sp_findings = []
        sp_findings += check_missing_nolock(actual_name, body_clean)
        sp_findings += check_cursor(actual_name, body_clean)
        sp_findings += check_deprecated(actual_name, body_clean)
        sp_findings += check_implicit_convert(actual_name, body_clean)
        sp_findings += check_dead_code(actual_name, body_clean)
        findings.extend(sp_findings)
        if sp_findings:
            sp_audit_count[actual_name] = len(sp_findings)

    # Never-called SPs (not entry points)
    never_called = []
    for k, cb in called_by.items():
        if cb: continue
        nm = lineage['sps'][k]['name']
        if is_entrypoint(nm): continue
        findings.append({
            'sp': nm, 'severity': 'med', 'category': 'never_called',
            'detail': 'No SP in the database EXECs this and it does not match any entry-point naming pattern',
            'target': '', 'snippet': ''
        })
        never_called.append(nm)

    # Category counts for stats
    cat_counts = {}
    sev_counts = {'high': 0, 'med': 0, 'low': 0}
    for f in findings:
        cat_counts[f['category']] = cat_counts.get(f['category'], 0) + 1
        sev_counts[f['severity']] = sev_counts.get(f['severity'], 0) + 1

    # Write outputs
    audit_out = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'sp_count_scanned': len(df),
        'finding_count': len(findings),
        'severity_counts': sev_counts,
        'category_counts': cat_counts,
        'findings': findings,
    }
    (OUT_DIR / 'audit_findings.json').write_text(json.dumps(audit_out, indent=2), encoding='utf-8')

    with open(OUT_DIR / 'audit_findings.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['sp', 'severity', 'category', 'detail', 'target', 'snippet'])
        for r in findings:
            w.writerow([r['sp'], r['severity'], r['category'], r['detail'], r['target'], r['snippet']])

    elapsed = (datetime.now() - t0).total_seconds()
    print()
    print(f"[{datetime.now():%H:%M:%S}] Done in {elapsed:.1f}s.")
    print(f"\n{len(findings)} findings across {len(sp_audit_count)} SPs (out of {len(df)}).")
    print(f"\nSeverity breakdown:")
    for sev in ('high','med','low'):
        print(f"  {sev:6}: {sev_counts.get(sev,0)}")
    print(f"\nCategory breakdown:")
    for cat, n in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat:30}: {n}")
    print(f"\nNever-called SPs (excluding entry-point patterns): {len(never_called)}")

if __name__ == '__main__':
    main()
