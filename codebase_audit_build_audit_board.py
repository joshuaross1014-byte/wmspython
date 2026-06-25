"""
WMS Codebase Intelligence — Audit Board artifact builder.

Reads:  <audit dir>/audit_findings.json   (produced by codebase_audit_phase2.py)
Writes: <output dir>/wms_codebase_audit_board.html

Paths default to directories beside this script and can be overridden with the
WMS_AUDIT_DIR and WMS_OUTPUT_DIR environment variables.
"""
import os
import json
from pathlib import Path

BASE_DIR   = Path(__file__).resolve().parent
AUDIT_DIR  = Path(os.getenv("WMS_AUDIT_DIR",  BASE_DIR / "codebase_audit"))
OUTPUT_DIR = Path(os.getenv("WMS_OUTPUT_DIR", BASE_DIR / "output"))
SRC  = AUDIT_DIR / "audit_findings.json"
DEST = OUTPUT_DIR / "wms_codebase_audit_board.html"

with SRC.open(encoding='utf-8') as f:
    data = json.load(f)

# Slim findings: rename fields, truncate snippet
findings_compact = []
for r in data['findings']:
    findings_compact.append({
        's': r['sp'],
        'sv': r['severity'],
        'c': r['category'],
        'd': r['detail'][:200],
        't': r['target'],
        'x': (r.get('snippet') or '')[:200],
    })

payload = {
    'generated_at': data['generated_at'],
    'sp_count': data['sp_count_scanned'],
    'severity_counts': data['severity_counts'],
    'category_counts': data['category_counts'],
    'findings': findings_compact,
}
payload_json = json.dumps(payload, separators=(',', ':'))

HTML = r"""<style>
  :root { color-scheme: light; }
  body { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; color:#1f2937; margin:0; padding:14px; background:#f7f8fa; }
  h1 { font-size:18px; margin:0 0 4px 0; font-weight:700; color:#0f172a; }
  .sub { color:#6b7280; font-size:12px; margin:0 0 14px 0; }
  .kpis { display:grid; grid-template-columns:repeat(5, 1fr); gap:8px; margin-bottom:14px; }
  .kpi { background:#fff; border:1px solid #e5e7eb; border-radius:6px; padding:10px; }
  .kpi .label { font-size:10px; color:#6b7280; text-transform:uppercase; letter-spacing:.5px; }
  .kpi .value { font-size:21px; font-weight:700; margin-top:3px; color:#0f172a; }
  .kpi.high .value { color:#dc2626; }
  .kpi.med  .value { color:#d97706; }
  .kpi.low  .value { color:#0891b2; }
  .row2 { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:14px; }
  .panel { background:#fff; border:1px solid #e5e7eb; border-radius:6px; padding:12px; }
  .panel h3 { margin:0 0 8px 0; font-size:11px; text-transform:uppercase; letter-spacing:.5px; color:#6b7280; font-weight:700; }
  .panel .chart-wrap { height:240px; position:relative; }
  .toolbar { display:flex; gap:8px; align-items:center; margin-bottom:10px; flex-wrap:wrap; background:#fff; border:1px solid #e5e7eb; border-radius:6px; padding:10px; }
  .toolbar input { flex:1; min-width:200px; padding:6px 10px; border:1px solid #d1d5db; border-radius:4px; font-size:13px; }
  .toolbar select { padding:6px 10px; border:1px solid #d1d5db; border-radius:4px; background:#fff; font-size:12px; }
  .toolbar .info { font-size:11px; color:#6b7280; margin-left:auto; }
  .sev { display:inline-block; padding:2px 8px; border-radius:10px; font-size:10px; font-weight:700; text-transform:uppercase; }
  .sev-high { background:#fee2e2; color:#991b1b; }
  .sev-med  { background:#fef3c7; color:#92400e; }
  .sev-low  { background:#dbeafe; color:#1e40af; }
  .cat { font-family:"SF Mono",Consolas,monospace; font-size:11px; color:#475569; }
  .sp-name { font-family:"SF Mono",Consolas,monospace; font-size:12px; color:#0f172a; }
  .snippet { font-family:"SF Mono",Consolas,monospace; font-size:11px; color:#64748b; }
  #grid { font-size:12px; }
</style>

<h1>WMS Codebase Intelligence — Audit Board</h1>
<p class="sub" id="header-sub">Loading…</p>

<div class="kpis" id="kpis"></div>

<div class="row2">
  <div class="panel">
    <h3>Findings by Category</h3>
    <div class="chart-wrap"><canvas id="catChart"></canvas></div>
  </div>
  <div class="panel">
    <h3>Top 15 SPs by Finding Count</h3>
    <div class="chart-wrap"><canvas id="spChart"></canvas></div>
  </div>
</div>

<div class="panel" style="margin-bottom:14px;">
  <h3>All Findings — sortable, filterable, searchable</h3>
  <div class="toolbar">
    <input type="text" id="q" placeholder="Search SP name, detail, target…" autocomplete="off">
    <select id="sev">
      <option value="">All severities</option>
      <option value="high">High only</option>
      <option value="med">Medium only</option>
      <option value="low">Low only</option>
    </select>
    <select id="cat">
      <option value="">All categories</option>
    </select>
    <span class="info" id="result-count"></span>
  </div>
  <div id="grid"></div>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.js" integrity="sha384-iU8HYtnGQ8Cy4zl7gbNMOhsDTTKX02BTXptVP/vqAWIaTfM7isw76iyZCsjL2eVi" crossorigin="anonymous"></script>
<script src="https://cdn.jsdelivr.net/npm/gridjs@5.0.2/dist/gridjs.umd.js" integrity="sha384-/XXDzxe4FsGiAe50i/u9pY/Vy/uX654MHB1xoc1BJNnH1WXHhqHga9g3q5tF4gj7" crossorigin="anonymous"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/gridjs@5.0.2/dist/theme/mermaid.min.css" integrity="sha384-jZvDSsmGB9oGGT/4l9bHXGoAv1OxvG/cFmSo0dZaSqmBgvQTKDBFAMftlXTmMbNW" crossorigin="anonymous">

<script>
const DATA = __PAYLOAD__;
const fmt = n => Number(n).toLocaleString();

const sev = DATA.severity_counts;
const total = DATA.findings.length;

document.getElementById('header-sub').textContent =
  `Generated ${DATA.generated_at} · ${total.toLocaleString()} findings across ${DATA.sp_count} SPs.`;

const kpis = [
  {label:'Total Findings', value:fmt(total)},
  {label:'High Severity',  value:fmt(sev.high||0), klass:'high'},
  {label:'Medium Severity',value:fmt(sev.med||0),  klass:'med'},
  {label:'Low Severity',   value:fmt(sev.low||0),  klass:'low'},
  {label:'SPs With Findings', value:fmt(new Set(DATA.findings.map(f=>f.s)).size)},
];
document.getElementById('kpis').innerHTML = kpis.map(k =>
  `<div class="kpi ${k.klass||''}"><div class="label">${k.label}</div><div class="value">${k.value}</div></div>`
).join('');

Chart.defaults.color = '#475569';
Chart.defaults.borderColor = '#e5e7eb';
Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
Chart.defaults.font.size = 11;

// Populate category dropdown
const cats = Object.keys(DATA.category_counts).sort();
const catSel = document.getElementById('cat');
cats.forEach(c => {
  const o = document.createElement('option');
  o.value = c; o.textContent = `${c} (${DATA.category_counts[c]})`;
  catSel.appendChild(o);
});

// Category chart
const catSorted = cats.map(c=>({cat:c, n:DATA.category_counts[c]})).sort((a,b)=>b.n-a.n);
const catColors = {
  missing_nolock: '#f59e0b', cursor_usage: '#dc2626', never_called: '#6366f1',
  deprecated_ref: '#a855f7', implicit_conv_candidate: '#0891b2', dead_code: '#64748b'
};
new Chart(document.getElementById('catChart'), {
  type:'bar',
  data: { labels: catSorted.map(c=>c.cat), datasets:[{label:'Findings', data:catSorted.map(c=>c.n),
          backgroundColor: catSorted.map(c=>catColors[c.cat]||'#94a3b8'), borderWidth:0}] },
  options: {indexAxis:'y', responsive:true, maintainAspectRatio:false,
    scales:{x:{beginAtZero:true,grid:{color:'rgba(0,0,0,.04)'}},y:{grid:{display:false}}},
    plugins:{legend:{display:false}}}
});

// SP chart — top 15 by count
const spCount = {};
DATA.findings.forEach(f => spCount[f.s] = (spCount[f.s]||0) + 1);
const topSp = Object.entries(spCount).sort((a,b)=>b[1]-a[1]).slice(0,15);
new Chart(document.getElementById('spChart'), {
  type:'bar',
  data: {labels: topSp.map(x=>x[0]), datasets:[{label:'Findings', data:topSp.map(x=>x[1]),
         backgroundColor:'#3b82f6', borderWidth:0}]},
  options: {indexAxis:'y', responsive:true, maintainAspectRatio:false,
    scales:{x:{beginAtZero:true,grid:{color:'rgba(0,0,0,.04)'}},y:{grid:{display:false}}},
    plugins:{legend:{display:false}}}
});

// Grid.js findings table
let grid = null;
let currentRows = [];
function rebuildRows() {
  const q = document.getElementById('q').value.trim().toLowerCase();
  const sevF = document.getElementById('sev').value;
  const catF = document.getElementById('cat').value;
  let filt = DATA.findings;
  if (sevF) filt = filt.filter(f => f.sv === sevF);
  if (catF) filt = filt.filter(f => f.c === catF);
  if (q)    filt = filt.filter(f => f.s.toLowerCase().includes(q) || (f.d||'').toLowerCase().includes(q) || (f.t||'').toLowerCase().includes(q));
  document.getElementById('result-count').textContent = `${filt.length.toLocaleString()} of ${total.toLocaleString()}`;
  currentRows = filt.map(f => [
    f.sv, f.c, f.s, f.d, f.t, f.x
  ]);
  if (grid) {
    grid.updateConfig({data: currentRows}).forceRender();
  }
}

const sevFmt = c => {
  const klass = `sev sev-${c}`;
  return gridjs.html(`<span class="${klass}">${c}</span>`);
};

grid = new gridjs.Grid({
  columns: [
    {name:'Sev', width:'70px', formatter: sevFmt},
    {name:'Category', width:'160px', formatter: c => gridjs.html(`<span class="cat">${c}</span>`)},
    {name:'SP', width:'220px', formatter: c => gridjs.html(`<span class="sp-name">${c}</span>`)},
    {name:'Detail', formatter: c => gridjs.html(`<span>${c}</span>`)},
    {name:'Target', width:'150px', formatter: c => gridjs.html(`<span class="cat">${c||''}</span>`)},
    {name:'Snippet', formatter: c => gridjs.html(`<span class="snippet">${c||''}</span>`)},
  ],
  data: [],
  sort: true,
  pagination: {limit: 25, summary: true},
}).render(document.getElementById('grid'));

document.getElementById('q').addEventListener('input', rebuildRows);
document.getElementById('sev').addEventListener('change', rebuildRows);
document.getElementById('cat').addEventListener('change', rebuildRows);

rebuildRows();
</script>
"""

HTML = HTML.replace('__PAYLOAD__', payload_json)
DEST.parent.mkdir(parents=True, exist_ok=True)
DEST.write_text(HTML, encoding='utf-8')

size_kb = DEST.stat().st_size / 1024
print(f"Wrote audit board HTML ({size_kb:.0f} KB) to:")
print(f"  {DEST}")
print(f"\nPayload: {len(findings_compact)} findings.")
