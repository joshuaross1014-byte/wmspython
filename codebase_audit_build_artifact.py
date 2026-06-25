"""
WMS Codebase Intelligence — Phase 1 artifact builder.

Reads:  <audit dir>/lineage_map.json   (produced by codebase_audit_phase1.py)
Writes: <output dir>/wms_lineage_explorer.html

Paths default to directories beside this script and can be overridden with the
WMS_AUDIT_DIR and WMS_OUTPUT_DIR environment variables.

Self-contained HTML artifact: search bar + entity detail pane showing
parents/children, table writers/readers, with type/category filtering.
"""

import os
import json
from pathlib import Path
from datetime import datetime

BASE_DIR   = Path(__file__).resolve().parent
AUDIT_DIR  = Path(os.getenv("WMS_AUDIT_DIR",  BASE_DIR / "codebase_audit"))
OUTPUT_DIR = Path(os.getenv("WMS_OUTPUT_DIR", BASE_DIR / "output"))
SRC  = AUDIT_DIR / "lineage_map.json"
DEST = OUTPUT_DIR / "wms_lineage_explorer.html"

with SRC.open(encoding='utf-8') as f:
    data = json.load(f)

# Compact representation: assign integer IDs to all names, store relations as ID arrays.
sp_names = sorted(data['sps'].keys())
tbl_names = sorted(data['tables'].keys())

sp_id  = {n:i for i,n in enumerate(sp_names)}
tbl_id = {n:i for i,n in enumerate(tbl_names)}

sps_compact = []
for n in sp_names:
    s = data['sps'][n]
    sps_compact.append({
        'k': n,                                                  # lowercase key
        'n': s['name'],                                          # display name
        'm': (s['modify_date'] or '')[:10],                      # YYYY-MM-DD
        'l': s['body_len'],
        'r': sorted(tbl_id[t]  for t in s['reads']  if t in tbl_id),
        'w': sorted(tbl_id[t]  for t in s['writes'] if t in tbl_id),
        'c': sorted(sp_id[c]   for c in s['calls']  if c in sp_id),
        'b': sorted(sp_id[cb]  for cb in s['called_by'] if cb in sp_id),
    })

tbls_compact = []
for n in tbl_names:
    t = data['tables'][n]
    tbls_compact.append({
        'k': n,
        'n': t['name'],
        'col': t['col_count'],
        'row': t['row_count'],
        'w': sorted(sp_id[s] for s in t['written_by'] if s in sp_id),
        'r': sorted(sp_id[s] for s in t['read_by']    if s in sp_id),
    })

payload = {
    'generated_at': data['generated_at'],
    'sps': sps_compact,
    'tables': tbls_compact,
}
payload_json = json.dumps(payload, separators=(',', ':'))

HTML = r"""<style>
  :root { color-scheme: light; }
  body { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; color:#1f2937; margin:0; padding:14px; background:#f7f8fa; }
  h1 { font-size:18px; margin:0 0 4px 0; font-weight:700; color:#0f172a; }
  .sub { color:#6b7280; font-size:12px; margin:0 0 14px 0; }
  .kpis { display:grid; grid-template-columns:repeat(6, 1fr); gap:8px; margin-bottom:14px; }
  .kpi { background:#fff; border:1px solid #e5e7eb; border-radius:6px; padding:10px; }
  .kpi .label { font-size:10px; color:#6b7280; text-transform:uppercase; letter-spacing:.5px; }
  .kpi .value { font-size:19px; font-weight:700; margin-top:3px; color:#0f172a; }
  .toolbar { display:flex; gap:8px; align-items:center; margin-bottom:12px; background:#fff; border:1px solid #e5e7eb; border-radius:6px; padding:10px; }
  .toolbar input { flex:1; padding:6px 10px; border:1px solid #d1d5db; border-radius:4px; font-size:13px; }
  .toolbar select, .toolbar button { padding:6px 10px; border:1px solid #d1d5db; border-radius:4px; background:#fff; font-size:12px; cursor:pointer; }
  .layout { display:grid; grid-template-columns:320px 1fr; gap:12px; }
  .panel { background:#fff; border:1px solid #e5e7eb; border-radius:6px; padding:12px; max-height:75vh; overflow-y:auto; }
  .panel h3 { margin:0 0 8px 0; font-size:11px; text-transform:uppercase; letter-spacing:.5px; color:#6b7280; font-weight:700; }
  .list .row { padding:5px 6px; border-radius:4px; cursor:pointer; font-size:12px; font-family:"SF Mono",Consolas,monospace; display:flex; justify-content:space-between; gap:6px; }
  .list .row:hover { background:#f1f5f9; }
  .list .row.sel { background:#dbeafe; color:#1e40af; font-weight:600; }
  .list .row .sub { color:#9ca3af; font-size:10px; }
  .badge { display:inline-block; padding:1px 6px; border-radius:8px; font-size:9px; font-weight:600; vertical-align:middle; margin-right:4px; }
  .badge-sp { background:#dbeafe; color:#1e40af; }
  .badge-tbl { background:#dcfce7; color:#166534; }
  .badge-warn { background:#fef3c7; color:#92400e; }
  .badge-info { background:#e0e7ff; color:#3730a3; }
  .detail .name { font-size:18px; font-weight:700; color:#0f172a; font-family:"SF Mono",Consolas,monospace; }
  .detail .meta { color:#6b7280; font-size:11px; margin:4px 0 14px 0; }
  .detail .section { margin-bottom:14px; }
  .detail .section-title { font-size:11px; text-transform:uppercase; letter-spacing:.5px; color:#475569; font-weight:700; margin-bottom:6px; }
  .pills { display:flex; flex-wrap:wrap; gap:4px; }
  .pill { display:inline-block; padding:3px 8px; border-radius:4px; font-size:11px; font-family:"SF Mono",Consolas,monospace; cursor:pointer; border:1px solid transparent; }
  .pill-sp { background:#f0f9ff; color:#075985; border-color:#bae6fd; }
  .pill-tbl { background:#f0fdf4; color:#166534; border-color:#bbf7d0; }
  .pill:hover { filter:brightness(0.95); }
  .empty { color:#9ca3af; font-style:italic; font-size:11px; }
  .stats-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:6px; font-size:11px; }
  .stats-grid div { padding:4px 6px; background:#f8fafc; border-radius:4px; }
  .stats-grid strong { color:#0f172a; }
</style>

<h1>WMS Codebase Intelligence — Lineage Explorer</h1>
<p class="sub" id="header-sub">Loading…</p>

<div class="kpis" id="kpis"></div>

<div class="toolbar">
  <input type="text" id="search" placeholder="Search SPs and tables… (start typing)" autocomplete="off">
  <select id="type-filter">
    <option value="">All types</option>
    <option value="sp">Stored Procedures</option>
    <option value="tbl">Tables</option>
  </select>
  <select id="risk-filter">
    <option value="">All</option>
    <option value="orphan-sp">Orphan SPs (no callers)</option>
    <option value="orphan-tbl-w">Tables with no writers</option>
    <option value="orphan-tbl-r">Tables with no readers</option>
    <option value="read-only">Read-only SPs</option>
    <option value="hot-tbl">Hot tables (≥10 writers)</option>
    <option value="hub-sp">Hub SPs (≥5 callers)</option>
  </select>
  <span id="result-count" style="font-size:11px;color:#6b7280;"></span>
</div>

<div class="layout">
  <div class="panel">
    <h3>Results</h3>
    <div id="list" class="list"></div>
  </div>
  <div class="panel detail" id="detail">
    <p class="empty">Select an entity from the left to see its parents, children, and relationships.</p>
  </div>
</div>

<script>
const DATA = __PAYLOAD__;

// Build indexes
const SP_BY_ID = DATA.sps;               // array of SP objects, index = id
const TBL_BY_ID = DATA.tables;           // array of table objects, index = id
const SP_KEY_TO_ID  = new Map(SP_BY_ID.map((s,i)  => [s.k, i]));
const TBL_KEY_TO_ID = new Map(TBL_BY_ID.map((t,i) => [t.k, i]));

const fmt = n => Number(n).toLocaleString();

// KPIs
const orphanSPs   = SP_BY_ID.filter(s => s.b.length === 0).length;
const readOnlySPs = SP_BY_ID.filter(s => s.w.length === 0).length;
const orphanTblsW = TBL_BY_ID.filter(t => t.w.length === 0).length;
const orphanTblsR = TBL_BY_ID.filter(t => t.r.length === 0).length;
const totalRows   = TBL_BY_ID.reduce((s,t)=>s+(t.row||0),0);

document.getElementById('header-sub').textContent =
  `Generated ${DATA.generated_at} · ${SP_BY_ID.length} SPs, ${TBL_BY_ID.length} t_* tables. Click any pill to navigate.`;

const kpis = [
  {label:'Stored Procs',       value:fmt(SP_BY_ID.length)},
  {label:'Tables (t_*)',       value:fmt(TBL_BY_ID.length)},
  {label:'Total Table Rows',   value:fmt(totalRows)},
  {label:'Orphan SPs',         value:fmt(orphanSPs)},
  {label:'Read-only SPs',      value:fmt(readOnlySPs)},
  {label:'Tables w/ no writers', value:fmt(orphanTblsW)},
];
document.getElementById('kpis').innerHTML = kpis.map(k =>
  `<div class="kpi"><div class="label">${k.label}</div><div class="value">${k.value}</div></div>`
).join('');

// Search + filter
const $list   = document.getElementById('list');
const $search = document.getElementById('search');
const $type   = document.getElementById('type-filter');
const $risk   = document.getElementById('risk-filter');
const $count  = document.getElementById('result-count');
const $detail = document.getElementById('detail');

function buildResults() {
  const q = $search.value.trim().toLowerCase();
  const typeF = $type.value;
  const riskF = $risk.value;
  let results = [];

  // SP candidates
  if (typeF !== 'tbl') {
    let sps = SP_BY_ID;
    if (riskF === 'orphan-sp')  sps = sps.filter(s => s.b.length === 0);
    if (riskF === 'read-only')  sps = sps.filter(s => s.w.length === 0);
    if (riskF === 'hub-sp')     sps = sps.filter(s => s.b.length >= 5);
    if (q) sps = sps.filter(s => s.k.includes(q));
    results.push(...sps.map(s => ({type:'sp', id: SP_KEY_TO_ID.get(s.k), name: s.n, sub:`${s.r.length}r · ${s.w.length}w · ${s.b.length}cb`})));
  }
  // Table candidates
  if (typeF !== 'sp') {
    let tbls = TBL_BY_ID;
    if (riskF === 'orphan-tbl-w') tbls = tbls.filter(t => t.w.length === 0);
    if (riskF === 'orphan-tbl-r') tbls = tbls.filter(t => t.r.length === 0);
    if (riskF === 'hot-tbl')      tbls = tbls.filter(t => t.w.length >= 10);
    if (q) tbls = tbls.filter(t => t.k.includes(q));
    results.push(...tbls.map(t => ({type:'tbl', id: TBL_KEY_TO_ID.get(t.k), name: t.n, sub:`${fmt(t.row)} rows · ${t.r.length}r · ${t.w.length}w`})));
  }

  results.sort((a,b)=> a.name.localeCompare(b.name));
  $count.textContent = `${results.length} match${results.length===1?'':'es'}`;

  // Cap at 500 for perf
  const cap = results.slice(0, 500);
  $list.innerHTML = cap.map(r =>
    `<div class="row" data-type="${r.type}" data-id="${r.id}">
       <span><span class="badge badge-${r.type}">${r.type==='sp'?'SP':'TBL'}</span>${r.name}</span>
       <span class="sub">${r.sub}</span>
     </div>`
  ).join('') + (results.length>cap.length ? `<div class="empty" style="padding:6px;">+ ${results.length-cap.length} more — refine search</div>` : '');
}

$list.addEventListener('click', e => {
  const row = e.target.closest('.row');
  if (!row) return;
  document.querySelectorAll('.row.sel').forEach(r => r.classList.remove('sel'));
  row.classList.add('sel');
  if (row.dataset.type === 'sp') showSp(parseInt(row.dataset.id));
  else showTbl(parseInt(row.dataset.id));
});

function showSp(id) {
  const s = SP_BY_ID[id];
  const callerPills = s.b.length ? s.b.map(i=>`<span class="pill pill-sp" data-jumpsp="${i}">${SP_BY_ID[i].n}</span>`).join('') : '<span class="empty">(no SPs call this — entry point or dead code)</span>';
  const calleePills = s.c.length ? s.c.map(i=>`<span class="pill pill-sp" data-jumpsp="${i}">${SP_BY_ID[i].n}</span>`).join('') : '<span class="empty">(does not call any SP)</span>';
  const readPills   = s.r.length ? s.r.map(i=>`<span class="pill pill-tbl" data-jumptbl="${i}">${TBL_BY_ID[i].n}</span>`).join('') : '<span class="empty">(no table reads detected)</span>';
  const writePills  = s.w.length ? s.w.map(i=>`<span class="pill pill-tbl" data-jumptbl="${i}">${TBL_BY_ID[i].n}</span>`).join('') : '<span class="empty">(read-only — no INSERT/UPDATE/DELETE/MERGE/TRUNCATE)</span>';
  $detail.innerHTML = `
    <div class="name">${s.n} <span class="badge badge-sp">SP</span></div>
    <div class="meta">Modified: ${s.m||'?'} · ${fmt(s.l)} chars · ${s.b.length} caller(s) · ${s.c.length} callee(s) · reads ${s.r.length} table(s), writes ${s.w.length}</div>
    <div class="section">
      <div class="section-title">↑ Called by (parents)</div>
      <div class="pills">${callerPills}</div>
    </div>
    <div class="section">
      <div class="section-title">↓ Calls (children)</div>
      <div class="pills">${calleePills}</div>
    </div>
    <div class="section">
      <div class="section-title">→ Writes to</div>
      <div class="pills">${writePills}</div>
    </div>
    <div class="section">
      <div class="section-title">← Reads from</div>
      <div class="pills">${readPills}</div>
    </div>
  `;
}

function showTbl(id) {
  const t = TBL_BY_ID[id];
  const writerPills = t.w.length ? t.w.map(i=>`<span class="pill pill-sp" data-jumpsp="${i}">${SP_BY_ID[i].n}</span>`).join('') : '<span class="empty">(no SP writes to this — possibly host-loaded or stale)</span>';
  const readerPills = t.r.length ? t.r.map(i=>`<span class="pill pill-sp" data-jumpsp="${i}">${SP_BY_ID[i].n}</span>`).join('') : '<span class="empty">(no SP reads this)</span>';
  $detail.innerHTML = `
    <div class="name">${t.n} <span class="badge badge-tbl">TBL</span></div>
    <div class="meta">${fmt(t.row)} rows · ${t.col} columns · ${t.w.length} writer SP(s) · ${t.r.length} reader SP(s)</div>
    <div class="section">
      <div class="section-title">Written by (${t.w.length})</div>
      <div class="pills">${writerPills}</div>
    </div>
    <div class="section">
      <div class="section-title">Read by (${t.r.length})</div>
      <div class="pills">${readerPills}</div>
    </div>
  `;
}

$detail.addEventListener('click', e => {
  const sp = e.target.dataset.jumpsp;
  const tb = e.target.dataset.jumptbl;
  if (sp !== undefined) { showSp(parseInt(sp)); $search.value=''; buildResults(); }
  if (tb !== undefined) { showTbl(parseInt(tb)); $search.value=''; buildResults(); }
});

let searchTimer;
$search.addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(buildResults, 150); });
$type.addEventListener('change', buildResults);
$risk.addEventListener('change', buildResults);

buildResults();
</script>
"""

HTML = HTML.replace('__PAYLOAD__', payload_json)
DEST.parent.mkdir(parents=True, exist_ok=True)
DEST.write_text(HTML, encoding='utf-8')

size_kb = DEST.stat().st_size / 1024
print(f"Wrote artifact HTML ({size_kb:.0f} KB) to:")
print(f"  {DEST}")
print(f"\nPayload: {len(sp_names)} SPs, {len(tbl_names)} tables.")
print(f"Compact JSON size: {len(payload_json)/1024:.0f} KB")
