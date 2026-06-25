"""
WMS Codebase Intelligence — SP Documentation Browser artifact builder.

Reads:  <audit dir>/sp_docs/*.md   (produced by codebase_audit_phase3_prep.py)
Writes: <output dir>/wms_sp_docs_browser.html

Paths default to directories beside this script and can be overridden with the
WMS_AUDIT_DIR and WMS_OUTPUT_DIR environment variables.
"""
import os, re, json
from pathlib import Path
from datetime import datetime

BASE_DIR   = Path(__file__).resolve().parent
AUDIT_DIR  = Path(os.getenv("WMS_AUDIT_DIR",  BASE_DIR / "codebase_audit"))
OUTPUT_DIR = Path(os.getenv("WMS_OUTPUT_DIR", BASE_DIR / "output"))
DOCS_DIR = AUDIT_DIR / "sp_docs"
DEST = OUTPUT_DIR / "wms_sp_docs_browser.html"

state_path = DOCS_DIR / '_state.json'
state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}

PURPOSE_PENDING_MARK = "<!-- PURPOSE_PENDING -->"

# Build list of SPs with their markdown content and indexing data
sps = []
for md in sorted(DOCS_DIR.glob('*.md')):
    if md.name.startswith('_'): continue
    name = md.stem
    key = name.lower()
    text = md.read_text(encoding='utf-8')
    st = state.get(key, {})

    # Extract the purpose paragraph
    purp_match = re.search(r'## Purpose\s*\n\s*\n(.*?)(?=\n## |\Z)', text, re.DOTALL)
    purpose_text = (purp_match.group(1).strip() if purp_match else '')
    has_purpose = bool(purpose_text) and purpose_text != PURPOSE_PENDING_MARK

    # Pull counts for the row
    reads = re.search(r'\*\*Reads\*\*\s*\((\d+)\)', text)
    writes = re.search(r'\*\*Writes\*\*\s*\((\d+)\)', text)
    callers = re.search(r'\*\*Called by\*\*\s*\((\d+)\)', text)
    callees = re.search(r'\*\*Calls\*\*\s*\((\d+)\)', text)
    findings = len(re.findall(r'^- \*\*\w+\*\* — ', text, re.MULTILINE))

    sps.append({
        'n': name,
        'k': key,
        'md': text,
        'has_purpose': has_purpose,
        'r': int(reads.group(1)) if reads else 0,
        'w': int(writes.group(1)) if writes else 0,
        'cb': int(callers.group(1)) if callers else 0,
        'c': int(callees.group(1)) if callees else 0,
        'f': findings,
    })

# Pending count for header
pending = sum(1 for s in sps if not s['has_purpose'])

payload = {
    'generated_at': datetime.now().isoformat(timespec='seconds'),
    'total': len(sps),
    'pending': pending,
    'sps': sps,
}
payload_json = json.dumps(payload, separators=(',', ':'))

HTML = r"""<style>
  :root { color-scheme: light; }
  body { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; color:#1f2937; margin:0; padding:14px; background:#f7f8fa; }
  h1 { font-size:18px; margin:0 0 4px 0; font-weight:700; color:#0f172a; }
  .sub { color:#6b7280; font-size:12px; margin:0 0 14px 0; }
  .kpis { display:grid; grid-template-columns:repeat(4, 1fr); gap:8px; margin-bottom:14px; }
  .kpi { background:#fff; border:1px solid #e5e7eb; border-radius:6px; padding:10px; }
  .kpi .label { font-size:10px; color:#6b7280; text-transform:uppercase; letter-spacing:.5px; }
  .kpi .value { font-size:21px; font-weight:700; margin-top:3px; color:#0f172a; }
  .toolbar { display:flex; gap:8px; align-items:center; margin-bottom:10px; background:#fff; border:1px solid #e5e7eb; border-radius:6px; padding:10px; }
  .toolbar input { flex:1; padding:6px 10px; border:1px solid #d1d5db; border-radius:4px; font-size:13px; }
  .toolbar select { padding:6px 10px; border:1px solid #d1d5db; border-radius:4px; background:#fff; font-size:12px; }
  .toolbar .info { font-size:11px; color:#6b7280; }
  .layout { display:grid; grid-template-columns:340px 1fr; gap:12px; }
  .panel { background:#fff; border:1px solid #e5e7eb; border-radius:6px; padding:12px; max-height:80vh; overflow-y:auto; }
  .list .row { padding:6px 8px; border-radius:4px; cursor:pointer; display:flex; flex-direction:column; gap:2px; border-bottom:1px solid #f1f5f9; }
  .list .row:hover { background:#f1f5f9; }
  .list .row.sel { background:#dbeafe; }
  .list .row .name { font-family:"SF Mono",Consolas,monospace; font-size:12px; color:#0f172a; display:flex; justify-content:space-between; gap:6px; align-items:center; }
  .list .row .name .badge { font-size:9px; padding:1px 6px; border-radius:8px; font-weight:600; }
  .pending { background:#fef3c7; color:#92400e; }
  .done    { background:#dcfce7; color:#166534; }
  .list .row .stats { font-size:10px; color:#6b7280; }
  .doc { font-size:13px; line-height:1.5; }
  .doc h1 { font-size:18px; margin:0 0 8px 0; color:#0f172a; }
  .doc h2 { font-size:13px; text-transform:uppercase; letter-spacing:.5px; color:#475569; font-weight:700; margin:14px 0 6px 0; border-top:1px solid #e5e7eb; padding-top:10px; }
  .doc h3 { font-size:12px; color:#475569; font-weight:700; margin:10px 0 4px 0; }
  .doc code { background:#f1f5f9; padding:1px 4px; border-radius:3px; font-size:11.5px; font-family:"SF Mono",Consolas,monospace; }
  .doc pre { background:#f8fafc; padding:8px 10px; border-radius:4px; border:1px solid #e5e7eb; overflow-x:auto; font-size:11px; }
  .doc ul { padding-left:22px; margin:4px 0; }
  .doc li { margin:2px 0; }
  .doc .pending-mark { background:#fef3c7; color:#92400e; padding:4px 8px; border-radius:4px; font-size:11.5px; font-style:italic; display:inline-block; }
  .empty { color:#9ca3af; font-style:italic; }
</style>

<h1>WMS Codebase Intelligence — SP Documentation Browser</h1>
<p class="sub" id="header-sub">Loading…</p>

<div class="kpis" id="kpis"></div>

<div class="toolbar">
  <input type="text" id="q" placeholder="Search SP name or content…" autocomplete="off">
  <select id="status">
    <option value="">All</option>
    <option value="done">Has purpose summary</option>
    <option value="pending">Purpose pending</option>
  </select>
  <select id="sort">
    <option value="name">Sort: name</option>
    <option value="f">Sort: most findings</option>
    <option value="cb">Sort: most callers</option>
    <option value="w">Sort: most writes</option>
  </select>
  <span class="info" id="result-count"></span>
</div>

<div class="layout">
  <div class="panel">
    <div id="list" class="list"></div>
  </div>
  <div class="panel">
    <div id="doc" class="doc">
      <p class="empty">Select a stored procedure on the left to view its documentation.</p>
    </div>
  </div>
</div>

<script>
const DATA = __PAYLOAD__;
const fmt = n => Number(n).toLocaleString();

document.getElementById('header-sub').textContent =
  `Generated ${DATA.generated_at} · ${fmt(DATA.total)} stored procedures · ${fmt(DATA.total-DATA.pending)} with purpose summary · ${fmt(DATA.pending)} pending LLM generation.`;

document.getElementById('kpis').innerHTML = [
  {l:'Total SPs', v:fmt(DATA.total)},
  {l:'With Summary', v:fmt(DATA.total - DATA.pending)},
  {l:'Pending', v:fmt(DATA.pending)},
  {l:'% Documented', v:DATA.total ? ((100*(DATA.total-DATA.pending)/DATA.total).toFixed(1)+'%') : '—'},
].map(k => `<div class="kpi"><div class="label">${k.l}</div><div class="value">${k.v}</div></div>`).join('');

// Tiny markdown renderer (limited but enough for our generated content)
function mdToHtml(md) {
  // Escape
  md = md.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  // Code blocks
  md = md.replace(/```([\s\S]*?)```/g, (m,c) => `<pre>${c.trim()}</pre>`);
  // Inline code
  md = md.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Headings
  md = md.replace(/^### (.*?)$/gm, '<h3>$1</h3>');
  md = md.replace(/^## (.*?)$/gm, '<h2>$1</h2>');
  md = md.replace(/^# (.*?)$/gm, '<h1>$1</h1>');
  // Bold
  md = md.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  // Italics
  md = md.replace(/_([^_]+)_/g, '<em>$1</em>');
  // Pending mark
  md = md.replace(/&lt;!-- PURPOSE_PENDING --&gt;/g, '<span class="pending-mark">Plain-English summary will be generated by the next batch run.</span>');
  // Lists (simple)
  md = md.replace(/^((?:- .*?\n?)+)/gm, m => '<ul>' + m.split(/\n/).filter(l=>l.trim()).map(l => '<li>' + l.replace(/^- /,'') + '</li>').join('') + '</ul>');
  // Paragraphs
  md = md.split(/\n{2,}/).map(p => {
    if (/^<(h\d|ul|pre)/.test(p.trim())) return p;
    return p.trim() ? '<p>' + p.replace(/\n/g,'<br>') + '</p>' : '';
  }).join('\n');
  return md;
}

const $list = document.getElementById('list');
const $doc  = document.getElementById('doc');
const $q    = document.getElementById('q');
const $st   = document.getElementById('status');
const $srt  = document.getElementById('sort');
const $cnt  = document.getElementById('result-count');

function rebuild() {
  const q  = $q.value.trim().toLowerCase();
  const st = $st.value;
  const srt = $srt.value;
  let rows = DATA.sps.slice();
  if (st === 'done')    rows = rows.filter(s => s.has_purpose);
  if (st === 'pending') rows = rows.filter(s => !s.has_purpose);
  if (q) rows = rows.filter(s => s.k.includes(q) || s.md.toLowerCase().includes(q));
  if (srt === 'name') rows.sort((a,b)=> a.n.localeCompare(b.n));
  else rows.sort((a,b)=> (b[srt]||0) - (a[srt]||0));
  rows = rows.slice(0, 800);
  $cnt.textContent = `${rows.length}${rows.length>=800?'+ ':' '}match${rows.length===1?'':'es'}`;
  $list.innerHTML = rows.map((s, i) => {
    const badge = s.has_purpose ? '<span class="badge done">DOC</span>' : '<span class="badge pending">PENDING</span>';
    return `<div class="row" data-idx="${DATA.sps.indexOf(s)}">
      <div class="name"><span>${s.n}</span>${badge}</div>
      <div class="stats">r:${s.r} · w:${s.w} · cb:${s.cb} · c:${s.c} · findings:${s.f}</div>
    </div>`;
  }).join('');
}

$list.addEventListener('click', e => {
  const row = e.target.closest('.row');
  if (!row) return;
  document.querySelectorAll('.row.sel').forEach(r=>r.classList.remove('sel'));
  row.classList.add('sel');
  const sp = DATA.sps[parseInt(row.dataset.idx)];
  $doc.innerHTML = mdToHtml(sp.md);
});

$q.addEventListener('input',  rebuild);
$st.addEventListener('change', rebuild);
$srt.addEventListener('change', rebuild);

rebuild();
</script>
"""

HTML = HTML.replace('__PAYLOAD__', payload_json)
DEST.parent.mkdir(parents=True, exist_ok=True)
DEST.write_text(HTML, encoding='utf-8')

size_kb = DEST.stat().st_size / 1024
print(f"Wrote SP Docs Browser HTML ({size_kb:.0f} KB) to:")
print(f"  {DEST}")
print(f"\nIndexed {len(sps)} SPs ({pending} pending purpose summary).")
