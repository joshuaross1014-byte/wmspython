"""
WMS Codebase Intelligence — Phase 3 prep: per-SP documentation scaffolding.

For every SP:
  - Writes  codebase_audit\\sp_docs\\_inputs\\{sp_name}.json  (body + metadata for LLM)
  - Writes  codebase_audit\\sp_docs\\{sp_name}.md            (heuristic structure)
  - Updates codebase_audit\\sp_docs\\_state.json             (hash + purpose-status tracker)
  - Updates codebase_audit\\sp_docs\\_queue.json             (SPs needing LLM purpose paragraph)

The .md file has a Purpose section that's empty (`<!-- PURPOSE_PENDING -->`) until the
scheduled task fills it in. Re-running this script leaves existing purpose text intact
and only queues SPs whose body hash has changed since the last run.

Read-only against the DB.
"""
import os, re, sys, json, hashlib
from pathlib import Path
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wms_connect import run_query

AUDIT_DIR = Path(os.getenv("WMS_AUDIT_DIR", Path(__file__).resolve().parent / "codebase_audit"))
DOCS_DIR  = AUDIT_DIR / "sp_docs"
INPUTS_DIR = DOCS_DIR / "_inputs"
DOCS_DIR.mkdir(parents=True, exist_ok=True)
INPUTS_DIR.mkdir(parents=True, exist_ok=True)

PURPOSE_PENDING_MARK = "<!-- PURPOSE_PENDING -->"
PURPOSE_HEADING = "## Purpose"

def strip_comments(sql: str) -> str:
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    sql = re.sub(r'--[^\n]*', '', sql)
    return sql

# --- Parameter parsing -------------------------------------------------------
RE_PROC_HEADER = re.compile(
    r'CREATE\s+(?:OR\s+ALTER\s+)?PROC(?:EDURE)?\s+(?:\[?dbo\]?\.)?\[?(\w+)\]?'
    r'(.*?)\s*\bAS\b',
    re.IGNORECASE | re.DOTALL
)
# Match @name [datatype] [= default] up to , or end-of-line
RE_PARAM = re.compile(
    r'@(\w+)\s+([\w]+(?:\s*\([^)]*\))?)(\s*=\s*[^,@]+?)?(?=\s*(?:,|@|$))',
    re.IGNORECASE
)

def extract_params(body: str):
    cleaned = strip_comments(body)
    m = RE_PROC_HEADER.search(cleaned)
    if not m:
        return []
    header = m.group(2) or ''
    params = []
    for pm in RE_PARAM.finditer(header):
        params.append({
            'name': pm.group(1),
            'type': pm.group(2).strip(),
            'default': (pm.group(3) or '').replace('=', '', 1).strip() or None
        })
    return params

# --- Leading-comment extraction ---------------------------------------------
RE_LEADING_BLOCK = re.compile(r'^\s*/\*(.*?)\*/', re.DOTALL)
RE_LEADING_LINES = re.compile(r'^(?:\s*--[^\n]*\n)+')
def extract_leading_comment(body: str) -> str:
    m = RE_LEADING_BLOCK.match(body)
    if m: return m.group(1).strip()
    m = RE_LEADING_LINES.match(body)
    if m:
        return '\n'.join(line.lstrip('-').strip() for line in m.group(0).splitlines()).strip()
    return ''

# --- Markdown rendering ------------------------------------------------------
def build_md(name, sp, params, leading_comment, findings, existing_purpose):
    purpose_block = existing_purpose if existing_purpose else PURPOSE_PENDING_MARK
    lines = [f"# {name}", ""]
    lines.append(f"**Modified:** {(sp.get('modify_date') or '')[:10] or 'unknown'}  ")
    lines.append(f"**Body length:** {sp.get('body_len', 0):,} chars  ")
    lines.append("")
    lines.append(PURPOSE_HEADING)
    lines.append("")
    lines.append(purpose_block)
    lines.append("")

    if leading_comment:
        lines.append("## Original header comment")
        lines.append("")
        lines.append("```")
        lines.append(leading_comment[:2000])
        lines.append("```")
        lines.append("")

    if params:
        lines.append("## Parameters")
        lines.append("")
        for p in params:
            default = f" = `{p['default']}`" if p['default'] else ''
            lines.append(f"- `@{p['name']}` ({p['type']}){default}")
        lines.append("")
    else:
        lines.append("## Parameters")
        lines.append("")
        lines.append("_None._")
        lines.append("")

    lines.append("## Tables")
    lines.append("")
    reads = sp.get('reads', [])
    writes = sp.get('writes', [])
    lines.append(f"- **Reads** ({len(reads)}): " + (", ".join(f"`{t}`" for t in reads) if reads else "_none detected_"))
    lines.append(f"- **Writes** ({len(writes)}): " + (", ".join(f"`{t}`" for t in writes) if writes else "_none detected_"))
    lines.append("")

    lines.append("## Cross-references")
    lines.append("")
    callers = sp.get('called_by', [])
    callees = sp.get('calls', [])
    lines.append(f"- **Called by** ({len(callers)}): " + (", ".join(f"`{c}`" for c in callers) if callers else "_no callers detected_"))
    lines.append(f"- **Calls** ({len(callees)}): " + (", ".join(f"`{c}`" for c in callees) if callees else "_no SP calls detected_"))
    lines.append("")

    if findings:
        sev_buckets = defaultdict(list)
        for f in findings:
            sev_buckets[f['severity']].append(f)
        lines.append("## Audit findings")
        lines.append("")
        for sev in ('high', 'med', 'low'):
            items = sev_buckets.get(sev, [])
            if not items: continue
            lines.append(f"### {sev.upper()} ({len(items)})")
            lines.append("")
            for f in items[:30]:
                detail = f['detail'].replace('|', '\\|')
                lines.append(f"- **{f['category']}** — {detail}")
            if len(items) > 30:
                lines.append(f"- _+ {len(items)-30} more {sev}-severity findings_")
            lines.append("")
    else:
        lines.append("## Audit findings")
        lines.append("")
        lines.append("_None._")
        lines.append("")

    return "\n".join(lines)

# --- Read existing purpose paragraph from md file (for idempotent re-runs) --
def extract_existing_purpose(md_path: Path):
    if not md_path.exists():
        return None
    text = md_path.read_text(encoding='utf-8')
    # Find Purpose section
    m = re.search(r'## Purpose\s*\n\s*\n(.*?)(?=\n## |\Z)', text, re.DOTALL)
    if not m: return None
    block = m.group(1).strip()
    if block == PURPOSE_PENDING_MARK or not block:
        return None
    return block

# --- Main --------------------------------------------------------------------
def main():
    t0 = datetime.now()

    lineage = json.loads((AUDIT_DIR / 'lineage_map.json').read_text(encoding='utf-8'))
    hashes  = json.loads((AUDIT_DIR / 'sp_body_hashes.json').read_text(encoding='utf-8'))['hashes']
    audit   = json.loads((AUDIT_DIR / 'audit_findings.json').read_text(encoding='utf-8'))

    state_path = DOCS_DIR / '_state.json'
    state = {}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding='utf-8'))

    findings_by_sp = defaultdict(list)
    for f in audit['findings']:
        findings_by_sp[f['sp']].append(f)

    print(f"[{t0:%H:%M:%S}] Pulling SP bodies for doc scaffolding...")
    df = run_query("""
        SELECT o.name AS sp_name, m.definition AS body
        FROM sys.sql_modules m WITH (NOLOCK)
        JOIN sys.objects o WITH (NOLOCK) ON o.object_id = m.object_id
        WHERE o.type = 'P' AND o.is_ms_shipped = 0
        ORDER BY o.name
    """)
    print(f"  -> {len(df)} SPs.")

    new_state = {}
    queue = []
    md_written = 0
    inputs_written = 0

    for _, row in df.iterrows():
        actual_name = row['sp_name']
        key = actual_name.lower()
        body = row['body'] or ''
        body_hash = hashes.get(key) or hashlib.sha256(body.encode('utf-8', errors='ignore')).hexdigest()

        sp_lineage = lineage['sps'].get(key, {})
        params = extract_params(body)
        leading = extract_leading_comment(body)
        findings = findings_by_sp.get(actual_name, [])

        md_path = DOCS_DIR / f"{actual_name}.md"
        existing_purpose = extract_existing_purpose(md_path)

        # Decide if SP needs LLM summarization
        prior = state.get(key) or {}
        needs_purpose = (
            not existing_purpose
            or prior.get('hash') != body_hash
            or prior.get('purpose_status') != 'done'
        )

        # Only invalidate existing_purpose if body changed
        if prior.get('hash') != body_hash:
            existing_purpose_for_md = None
        else:
            existing_purpose_for_md = existing_purpose

        md = build_md(actual_name, sp_lineage, params, leading, findings, existing_purpose_for_md)
        md_path.write_text(md, encoding='utf-8')
        md_written += 1

        # Write input JSON for the summarizer task
        body_excerpt = body if len(body) <= 8000 else (body[:6000] + "\n\n/* …truncated… */\n\n" + body[-1500:])
        input_payload = {
            'name': actual_name,
            'modify_date': sp_lineage.get('modify_date'),
            'body_len': sp_lineage.get('body_len', len(body)),
            'hash': body_hash,
            'params': params,
            'leading_comment': leading[:1500],
            'reads': sp_lineage.get('reads', []),
            'writes': sp_lineage.get('writes', []),
            'calls': sp_lineage.get('calls', []),
            'called_by': sp_lineage.get('called_by', []),
            'finding_count': len(findings),
            'severities': {sev: sum(1 for f in findings if f['severity']==sev) for sev in ('high','med','low')},
            'body_excerpt': body_excerpt,
        }
        (INPUTS_DIR / f"{actual_name}.json").write_text(json.dumps(input_payload, indent=2), encoding='utf-8')
        inputs_written += 1

        new_state[key] = {
            'name': actual_name,
            'hash': body_hash,
            'purpose_status': 'done' if existing_purpose_for_md else 'pending',
            'last_scaffolded': datetime.now().isoformat(timespec='seconds'),
        }
        if needs_purpose:
            queue.append(actual_name)

    state_path.write_text(json.dumps(new_state, indent=2), encoding='utf-8')
    (DOCS_DIR / '_queue.json').write_text(json.dumps({
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'total_pending': len(queue),
        'queue': queue,
    }, indent=2), encoding='utf-8')

    elapsed = (datetime.now() - t0).total_seconds()
    done_count = sum(1 for s in new_state.values() if s['purpose_status'] == 'done')
    print(f"\n[{datetime.now():%H:%M:%S}] Done in {elapsed:.1f}s.")
    print(f"  - .md files written:   {md_written}")
    print(f"  - input JSON written:  {inputs_written}")
    print(f"  - purpose done:        {done_count}")
    print(f"  - purpose pending:     {len(queue)}")
    print(f"\nQueue saved to {DOCS_DIR / '_queue.json'}")

if __name__ == '__main__':
    main()
