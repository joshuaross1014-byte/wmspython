"""
WMS Codebase Intelligence — Phase 1: Lineage + Data Dictionary
================================================================
Reads sys.sql_modules + sys.tables, parses each SP body for:
  - Table reads  (FROM / JOIN on t_*)
  - Table writes (INSERT / UPDATE / DELETE / MERGE / TRUNCATE on t_*)
  - SP-to-SP calls (EXEC / EXECUTE)

Outputs to the audit directory (default: ./codebase_audit beside this script,
overridable with WMS_AUDIT_DIR):
  - lineage_map.json       — full graph (SPs, tables, calls, callers, writers, readers)
  - data_dictionary.csv    — every table with col/row counts + writers/readers
  - sp_body_hashes.json    — SHA-256 per SP body, for weekly change detection

Read-only. No DB writes (confirm-before-write safety rule).
"""

import os
import re
import sys
import csv
import json
import hashlib
from pathlib import Path
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wms_connect import run_query

OUT_DIR = Path(os.getenv("WMS_AUDIT_DIR", Path(__file__).resolve().parent / "codebase_audit"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ----------------------------------------------------------------------
# Parsing helpers
# ----------------------------------------------------------------------
def strip_comments(sql: str) -> str:
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    sql = re.sub(r'--[^\n]*', '', sql)
    return sql

RE_TABLE_READ        = re.compile(r'\b(?:FROM|JOIN|USING)\s+(?:\[?dbo\]?\.)?\[?(t_\w+)\]?', re.IGNORECASE)
# Also catches comma-separated table lists in FROM clauses: ", dbo.t_foo"
RE_TABLE_READ_COMMA  = re.compile(r',\s+(?:\[?dbo\]?\.)?\[?(t_\w+)\]?(?=\s|,|$)', re.IGNORECASE)
RE_TABLE_INSERT      = re.compile(r'INSERT\s+(?:INTO\s+)?(?:\[?dbo\]?\.)?\[?(t_\w+)\]?', re.IGNORECASE)
RE_TABLE_UPDATE      = re.compile(r'UPDATE\s+(?:\[?dbo\]?\.)?\[?(t_\w+)\]?(?!\s*\.)', re.IGNORECASE)
RE_TABLE_DELETE      = re.compile(r'DELETE\s+(?:FROM\s+)?(?:\[?dbo\]?\.)?\[?(t_\w+)\]?', re.IGNORECASE)
RE_TABLE_MERGE       = re.compile(r'MERGE\s+(?:INTO\s+)?(?:\[?dbo\]?\.)?\[?(t_\w+)\]?', re.IGNORECASE)
RE_TABLE_TRUNCATE    = re.compile(r'TRUNCATE\s+TABLE\s+(?:\[?dbo\]?\.)?\[?(t_\w+)\]?', re.IGNORECASE)
RE_SP_CALL           = re.compile(r'\bEXEC(?:UTE)?\s+(?:\[?dbo\]?\.)?\[?((?:sp|usp|p)_\w+)\]?', re.IGNORECASE)

def parse_sp_body(name_lower: str, body: str):
    body = strip_comments(body)
    reads_set = {m.lower() for m in RE_TABLE_READ.findall(body)}
    reads_set.update(m.lower() for m in RE_TABLE_READ_COMMA.findall(body))
    reads = sorted(reads_set)
    writes = set()
    writes.update(RE_TABLE_INSERT.findall(body))
    writes.update(RE_TABLE_UPDATE.findall(body))
    writes.update(RE_TABLE_DELETE.findall(body))
    writes.update(RE_TABLE_MERGE.findall(body))
    writes.update(RE_TABLE_TRUNCATE.findall(body))
    writes = sorted({w.lower() for w in writes})
    sp_calls = sorted({m.lower() for m in RE_SP_CALL.findall(body) if m.lower() != name_lower})
    return reads, writes, sp_calls

# ----------------------------------------------------------------------
def main():
    t0 = datetime.now()
    print(f"[{t0:%H:%M:%S}] Pulling SP definitions...")
    df_sps = run_query("""
        SELECT
            o.name        AS sp_name,
            m.definition  AS body,
            o.modify_date AS modify_date,
            DATALENGTH(m.definition) / 2 AS body_len
        FROM sys.sql_modules m WITH (NOLOCK)
        JOIN sys.objects o WITH (NOLOCK) ON o.object_id = m.object_id
        WHERE o.type = 'P' AND o.is_ms_shipped = 0
        ORDER BY o.name
    """)
    print(f"  -> {len(df_sps)} stored procedures.")

    print(f"[{datetime.now():%H:%M:%S}] Pulling table list + metadata...")
    df_tables = run_query("""
        SELECT
            t.name AS table_name,
            (SELECT COUNT(*) FROM sys.columns c WITH (NOLOCK) WHERE c.object_id = t.object_id) AS col_count,
            ISNULL((SELECT SUM(p.rows) FROM sys.partitions p WITH (NOLOCK)
                    WHERE p.object_id = t.object_id AND p.index_id IN (0,1)), 0) AS row_count
        FROM sys.tables t WITH (NOLOCK)
        WHERE t.is_ms_shipped = 0
          AND t.name LIKE 't[_]%'
        ORDER BY t.name
    """)
    print(f"  -> {len(df_tables)} t_* tables.")

    # Parse each SP body
    print(f"[{datetime.now():%H:%M:%S}] Parsing SP bodies...")
    sp_data = {}
    sp_hashes = {}
    table_writers = defaultdict(set)
    table_readers = defaultdict(set)
    sp_called_by  = defaultdict(set)

    for _, row in df_sps.iterrows():
        actual_name = row['sp_name']
        name = actual_name.lower()
        body = row['body'] or ''
        sp_hashes[name] = hashlib.sha256(body.encode('utf-8', errors='ignore')).hexdigest()
        reads, writes, sp_calls = parse_sp_body(name, body)
        sp_data[name] = {
            'name': actual_name,
            'modify_date': row['modify_date'].isoformat() if row['modify_date'] is not None else None,
            'body_len': int(row['body_len']),
            'reads': reads,
            'writes': writes,
            'calls': sp_calls,
            # called_by filled in below
        }
        for t in writes:
            table_writers[t].add(name)
        for t in reads:
            table_readers[t].add(name)
        for c in sp_calls:
            sp_called_by[c].add(name)

    for name in sp_data:
        sp_data[name]['called_by'] = sorted(sp_called_by.get(name, set()))

    tables = {}
    for _, row in df_tables.iterrows():
        tname = row['table_name'].lower()
        tables[tname] = {
            'name': row['table_name'],
            'col_count': int(row['col_count']),
            'row_count': int(row['row_count']),
            'written_by': sorted(table_writers.get(tname, set())),
            'read_by':    sorted(table_readers.get(tname, set())),
        }

    # ------------------------------------------------------------------
    # Outputs
    # ------------------------------------------------------------------
    generated_at = datetime.now().isoformat(timespec='seconds')

    lineage_out = {
        'generated_at':  generated_at,
        'sp_count':      len(sp_data),
        'table_count':   len(tables),
        'sps':           sp_data,
        'tables':        tables,
    }
    (OUT_DIR / 'lineage_map.json').write_text(json.dumps(lineage_out, indent=2), encoding='utf-8')

    (OUT_DIR / 'sp_body_hashes.json').write_text(json.dumps({
        'generated_at': generated_at,
        'hashes': sp_hashes,
    }, indent=2), encoding='utf-8')

    with open(OUT_DIR / 'data_dictionary.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['table_name', 'col_count', 'row_count',
                    'written_by_count', 'read_by_count',
                    'written_by', 'read_by'])
        for tname, t in sorted(tables.items()):
            w.writerow([
                t['name'], t['col_count'], t['row_count'],
                len(t['written_by']), len(t['read_by']),
                ';'.join(t['written_by']), ';'.join(t['read_by']),
            ])

    # Stats
    never_called  = [n for n in sp_data if not sp_data[n]['called_by']]
    no_writes     = [n for n in sp_data if not sp_data[n]['writes']]
    tables_no_w   = [t for t in tables.values() if not t['written_by']]
    tables_no_r   = [t for t in tables.values() if not t['read_by']]

    elapsed = (datetime.now() - t0).total_seconds()
    print()
    print(f"[{datetime.now():%H:%M:%S}] Done in {elapsed:.1f}s. Outputs:")
    print(f"  - lineage_map.json      ({len(sp_data)} SPs, {len(tables)} tables)")
    print(f"  - data_dictionary.csv")
    print(f"  - sp_body_hashes.json")
    print()
    print(f"Quick stats:")
    print(f"  - SPs with NO callers (entry points or dead code): {len(never_called)}")
    print(f"  - SPs with no table writes (read-only SPs):        {len(no_writes)}")
    print(f"  - Tables with no writers in any SP body:           {len(tables_no_w)}")
    print(f"  - Tables with no readers in any SP body:           {len(tables_no_r)}")

if __name__ == '__main__':
    main()
