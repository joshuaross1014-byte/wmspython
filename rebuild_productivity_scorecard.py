"""
Rebuild of the Operator Productivity Scorecard artifact with clearer,
plain-English labels. Reads yesterday's data live from the WMS, regenerates the
HTML, and writes it to the output directory.

A separate scheduled refresh task updates the data daily — this script only
changes the static labels/legends of the page.

Output path defaults to ./output beside this script and can be overridden with
the WMS_OUTPUT_DIR environment variable.
"""
import os, sys, re, json
from pathlib import Path
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wms_connect import run_query

OUTPUT_DIR = Path(os.getenv("WMS_OUTPUT_DIR", Path(__file__).resolve().parent / "output"))
DEST = OUTPUT_DIR / "wms_operator_productivity_scorecard.html"

print(f"[{datetime.now():%H:%M:%S}] Pulling operator productivity data...")
SQL = """
DECLARE @y date = DATEADD(day, -1, CAST(GETDATE() AS date));
DECLARE @start7 date = DATEADD(day, -7, CAST(GETDATE() AS date));

WITH categorized AS (
    SELECT tl.employee_id, tl.wh_id, CAST(tl.start_tran_date AS date) AS d,
           CASE
             WHEN tl.tran_type IN ('301','211') THEN 'PICK'
             WHEN tl.tran_type = '153'          THEN 'RECEIVE'
             WHEN tl.tran_type IN ('154','212') THEN 'PUTAWAY'
             WHEN tl.tran_type = '201'          THEN 'MOVE'
             WHEN tl.tran_type IN ('321','341') THEN 'LOAD_SHIP'
             WHEN tl.tran_type IN ('800','802','880') THEN 'CYCLE_COUNT'
             ELSE 'OTHER'
           END AS cat,
           tl.tran_qty, tl.elapsed_time
    FROM dbo.t_tran_log tl WITH (NOLOCK)
    WHERE tl.start_tran_date >= @start7
      AND tl.tran_type IN ('301','211','153','154','212','201','321','341','800','802','880')
      AND tl.employee_id IS NOT NULL AND tl.employee_id <> ''
)
SELECT 'A_yesterday' AS sect,
       CAST(c.employee_id AS varchar(50)) COLLATE DATABASE_DEFAULT AS k1,
       CAST(ISNULL(e.name, c.employee_id) AS varchar(80)) COLLATE DATABASE_DEFAULT AS k2,
       CAST(c.wh_id AS varchar(20)) COLLATE DATABASE_DEFAULT AS k3,
       CAST(SUM(CASE WHEN c.cat='PICK'        THEN 1 ELSE 0 END) AS varchar(20)) COLLATE DATABASE_DEFAULT AS v1,
       CAST(SUM(CASE WHEN c.cat='RECEIVE'     THEN 1 ELSE 0 END) AS varchar(20)) COLLATE DATABASE_DEFAULT AS v2,
       CAST(SUM(CASE WHEN c.cat='PUTAWAY'     THEN 1 ELSE 0 END) AS varchar(20)) COLLATE DATABASE_DEFAULT AS v3,
       CAST(SUM(CASE WHEN c.cat='MOVE'        THEN 1 ELSE 0 END) AS varchar(20)) COLLATE DATABASE_DEFAULT AS v4,
       CAST(SUM(CASE WHEN c.cat='LOAD_SHIP'   THEN 1 ELSE 0 END) AS varchar(20)) COLLATE DATABASE_DEFAULT AS v5,
       CAST(SUM(CASE WHEN c.cat='CYCLE_COUNT' THEN 1 ELSE 0 END) AS varchar(20)) COLLATE DATABASE_DEFAULT AS v6,
       CAST(COUNT(*) AS varchar(20)) COLLATE DATABASE_DEFAULT AS v7,
       CAST(SUM(CAST(c.tran_qty AS bigint)) AS varchar(20)) COLLATE DATABASE_DEFAULT AS v8,
       CAST(SUM(CAST(c.elapsed_time AS bigint)) AS varchar(20)) COLLATE DATABASE_DEFAULT AS v9
FROM categorized c
LEFT JOIN dbo.t_employee e WITH (NOLOCK) ON e.id = c.employee_id
WHERE c.d = @y
GROUP BY c.employee_id, e.name, c.wh_id
UNION ALL
SELECT 'B_trend', CAST(c.d AS varchar(50)) COLLATE DATABASE_DEFAULT, NULL, NULL,
       CAST(COUNT(*) AS varchar(20)) COLLATE DATABASE_DEFAULT,
       CAST(SUM(CAST(c.tran_qty AS bigint)) AS varchar(20)) COLLATE DATABASE_DEFAULT,
       CAST(COUNT(DISTINCT c.employee_id) AS varchar(20)) COLLATE DATABASE_DEFAULT,
       CAST(SUM(CAST(c.elapsed_time AS bigint)) AS varchar(20)) COLLATE DATABASE_DEFAULT,
       NULL, NULL, NULL, NULL, NULL
FROM categorized c GROUP BY c.d
UNION ALL
SELECT 'C_mix', CAST(c.cat AS varchar(50)) COLLATE DATABASE_DEFAULT, NULL, NULL,
       CAST(COUNT(*) AS varchar(20)) COLLATE DATABASE_DEFAULT,
       CAST(SUM(CAST(c.tran_qty AS bigint)) AS varchar(20)) COLLATE DATABASE_DEFAULT,
       CAST(COUNT(DISTINCT c.employee_id) AS varchar(20)) COLLATE DATABASE_DEFAULT,
       NULL, NULL, NULL, NULL, NULL, NULL
FROM categorized c WHERE c.d = @y GROUP BY c.cat
UNION ALL
SELECT 'D_wh', CAST(c.wh_id AS varchar(50)) COLLATE DATABASE_DEFAULT, NULL, NULL,
       CAST(COUNT(*) AS varchar(20)) COLLATE DATABASE_DEFAULT,
       CAST(SUM(CAST(c.tran_qty AS bigint)) AS varchar(20)) COLLATE DATABASE_DEFAULT,
       CAST(COUNT(DISTINCT c.employee_id) AS varchar(20)) COLLATE DATABASE_DEFAULT,
       CAST(SUM(CAST(c.elapsed_time AS bigint)) AS varchar(20)) COLLATE DATABASE_DEFAULT,
       NULL, NULL, NULL, NULL, NULL
FROM categorized c WHERE c.d = @y GROUP BY c.wh_id
ORDER BY sect, k1;
"""

df = run_query(SQL)
print(f"  -> {len(df)} rows pulled.")

# Parse into structured data
ops, trend, mix, by_wh = [], [], [], []
for _, row in df.iterrows():
    sect = row['sect']
    if sect == 'A_yesterday':
        sec = int(row['v9'])
        hours = round(sec / 3600.0, 2)
        tx = int(row['v7'])
        ops.append({
            'id': row['k1'], 'name': row['k2'], 'wh': row['k3'],
            'pick': int(row['v1']), 'receive': int(row['v2']), 'putaway': int(row['v3']),
            'move': int(row['v4']), 'load': int(row['v5']), 'cycle': int(row['v6']),
            'tx': tx, 'qty': int(row['v8']), 'sec': sec,
            'hours': hours, 'tph': round(tx/hours, 1) if hours > 0 else 0,
        })
    elif sect == 'B_trend':
        sec = int(row['v4']); tx = int(row['v1'])
        hours = round(sec / 3600.0, 1)
        trend.append({'date': row['k1'], 'tx': tx, 'qty': int(row['v2']),
                      'ops': int(row['v3']), 'sec': sec, 'hours': hours,
                      'tph': round(tx/hours, 1) if hours > 0 else 0})
    elif sect == 'C_mix':
        mix.append({'cat': row['k1'], 'tx': int(row['v1']),
                    'qty': int(row['v2']), 'ops': int(row['v3'])})
    elif sect == 'D_wh':
        sec = int(row['v4']); tx = int(row['v1'])
        hours = round(sec / 3600.0, 1)
        by_wh.append({'wh': row['k1'], 'tx': tx, 'qty': int(row['v2']),
                      'ops': int(row['v3']), 'sec': sec, 'hours': hours,
                      'tph': round(tx/hours, 1) if hours > 0 else 0})

# Drop today's incomplete row from trend
today_str = date.today().isoformat()
trend = [t for t in trend if t['date'] != today_str]

yesterday_str = (date.today() - timedelta(days=1)).isoformat()
gen_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

OPS_JSON   = json.dumps(ops,   separators=(',',':'))
TREND_JSON = json.dumps(trend, separators=(',',':'))
MIX_JSON   = json.dumps(mix,   separators=(',',':'))
WH_JSON    = json.dumps(by_wh, separators=(',',':'))

HTML = r"""<style>
  :root { color-scheme: light; }
  body { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; color:#1f2937; margin:0; padding:16px; background:#f7f8fa; }
  h1 { font-size:20px; margin:0 0 4px 0; font-weight:700; color:#0f172a; }
  h2 { font-size:13px; margin:0 0 8px 0; font-weight:700; color:#475569; letter-spacing:.3px; }
  .sub { color:#6b7280; font-size:13px; margin:0 0 8px 0; }
  .help-box { background:#eff6ff; border:1px solid #bfdbfe; border-radius:8px; padding:12px 14px; margin:0 0 16px 0; font-size:12.5px; color:#1e3a8a; line-height:1.55; }
  .help-box strong { color:#1e3a8a; }
  .kpis { display:grid; grid-template-columns:repeat(6, 1fr); gap:10px; margin-bottom:16px; }
  .kpi { background:#ffffff; border:1px solid #e5e7eb; border-radius:8px; padding:12px; box-shadow:0 1px 2px rgba(0,0,0,.03); }
  .kpi .label { font-size:11.5px; color:#374151; font-weight:600; letter-spacing:.2px; }
  .kpi .label .what { display:block; font-size:10px; color:#9ca3af; font-weight:500; margin-top:1px; text-transform:none; letter-spacing:0; }
  .kpi .value { font-size:24px; font-weight:700; margin-top:6px; color:#0f172a; }
  .kpi .delta { font-size:11px; color:#6b7280; margin-top:3px; }
  .row { display:grid; grid-template-columns:1.4fr 1fr; gap:12px; margin-bottom:16px; }
  .row3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:12px; margin-bottom:16px; }
  .panel { background:#ffffff; border:1px solid #e5e7eb; border-radius:8px; padding:14px; box-shadow:0 1px 2px rgba(0,0,0,.03); }
  .panel .panel-hint { font-size:11px; color:#6b7280; margin:-4px 0 8px 0; font-style:italic; }
  .panel .chart-wrap { height:260px; position:relative; }
  .mini-table { width:100%; border-collapse:collapse; font-size:12px; }
  .mini-table th, .mini-table td { padding:6px 8px; text-align:left; border-bottom:1px solid #f1f5f9; }
  .mini-table th { color:#6b7280; font-weight:600; font-size:11px; }
  .mini-table td.num { text-align:right; font-variant-numeric:tabular-nums; }
  .mini-table tr:last-child td { border-bottom:none; }
  .top-row { background:rgba(16,185,129,.06); }
  .bot-row { background:rgba(239,68,68,.06); }
  .notice { font-size:11.5px; color:#475569; margin-top:10px; line-height:1.5; background:#f8fafc; padding:8px 10px; border-radius:6px; border-left:3px solid #94a3b8; }
  .notice strong { color:#0f172a; }
  #fullTable { font-size:12px; }
</style>

<h1>Operator Productivity Scorecard</h1>
<p class="sub" id="header-sub">Loading...</p>

<div class="help-box">
  <strong>How to read this page.</strong>
  Every bar-code scan an operator makes on their RF gun (picking an item, receiving a pallet, putting something away, etc.) is one "task" recorded in the WMS.
  This page summarizes yesterday's work across all warehouses: how many people were on the floor, how many tasks they completed, how many cases / units they moved, and how fast they worked.
  "<strong>Tasks per Hour</strong>" is the productivity score — total tasks divided by total time spent scanning. Higher is faster.
</div>

<div class="kpis" id="kpis"></div>

<div class="row">
  <div class="panel">
    <h2>Last 7 Days — Tasks Completed &amp; Speed</h2>
    <div class="panel-hint">Bars = total tasks for that day. Line = average tasks per hour across all operators.</div>
    <div class="chart-wrap"><canvas id="trendChart"></canvas></div>
  </div>
  <div class="panel">
    <h2>Activity by Warehouse — Yesterday</h2>
    <div class="panel-hint">Bars = total tasks. Line = tasks per hour at that site.</div>
    <div class="chart-wrap"><canvas id="whChart"></canvas></div>
  </div>
</div>

<div class="row3">
  <div class="panel">
    <h2>What Were Operators Doing?</h2>
    <div class="panel-hint">Units moved yesterday by activity type.</div>
    <div class="chart-wrap"><canvas id="mixChart"></canvas></div>
  </div>
  <div class="panel">
    <h2>Top 10 — Fastest Operators</h2>
    <div class="panel-hint">Ranked by tasks per hour. Only operators who scanned at least 2 hours are included.</div>
    <table class="mini-table" id="topTable">
      <thead><tr><th>#</th><th>Operator</th><th>Warehouse</th><th class="num">Tasks/Hr</th><th class="num">Total Tasks</th><th class="num">Hours</th></tr></thead>
      <tbody></tbody>
    </table>
  </div>
  <div class="panel">
    <h2>Bottom 10 — Needs Review</h2>
    <div class="panel-hint">Slowest by tasks per hour. Min 2 hours of scanning. Could be training need, system issue, or task type.</div>
    <table class="mini-table" id="botTable">
      <thead><tr><th>#</th><th>Operator</th><th>Warehouse</th><th class="num">Tasks/Hr</th><th class="num">Total Tasks</th><th class="num">Hours</th></tr></thead>
      <tbody></tbody>
    </table>
  </div>
</div>

<div class="panel" style="margin-bottom:16px;">
  <h2>All Operators — Yesterday's Activity</h2>
  <div class="panel-hint">Click column headers to sort. Type in the search box to filter by name or warehouse.</div>
  <div id="fullTable"></div>
  <div class="notice">
    <strong>About the numbers.</strong>
    "Hours" here is <em>active scanning time</em> — the clock running between when an operator opens a task on the gun and finishes scanning the item. It does <strong>not</strong> include breaks, downtime, training, or non-RF work.
    So a person who clocked an 8-hour shift might only show 5–6 hours of scan time. That's normal.
    "Tasks/Hr" = total tasks ÷ total scan hours, so it's a measure of <em>how fast they work when they're working</em>, not a measure of how long their shift was.
    Operators with under 2 hours of scan time are still listed here but excluded from the Top/Bottom 10 panels to avoid noise from short sessions.
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4.5.0/dist/chart.umd.js" integrity="sha384-iU8HYtnGQ8Cy4zl7gbNMOhsDTTKX02BTXptVP/vqAWIaTfM7isw76iyZCsjL2eVi" crossorigin="anonymous"></script>
<script src="https://cdn.jsdelivr.net/npm/gridjs@5.0.2/dist/gridjs.umd.js" integrity="sha384-/XXDzxe4FsGiAe50i/u9pY/Vy/uX654MHB1xoc1BJNnH1WXHhqHga9g3q5tF4gj7" crossorigin="anonymous"></script>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/gridjs@5.0.2/dist/theme/mermaid.min.css" integrity="sha384-jZvDSsmGB9oGGT/4l9bHXGoAv1OxvG/cFmSo0dZaSqmBgvQTKDBFAMftlXTmMbNW" crossorigin="anonymous">

<script>
const GENERATED_AT = "__GEN_AT__";
const COVERED_DATE = "__COVERED_DATE__";

const OPS = __OPS__;
const TREND = __TREND__;
const MIX = __MIX__;
const BY_WH = __BY_WH__;

const fmt  = n => (n==null ? "—" : Number(n).toLocaleString());
const fmt1 = n => (n==null ? "—" : Number(n).toLocaleString(undefined,{maximumFractionDigits:1}));

const totalTx  = OPS.reduce((a,b)=>a+b.tx,0);
const totalQty = OPS.reduce((a,b)=>a+b.qty,0);
const totalHrs = OPS.reduce((a,b)=>a+b.hours,0);
const orgTph   = totalHrs > 0 ? totalTx/totalHrs : 0;
const eligibleOps = OPS.filter(o => o.hours >= 2);
const top1 = [...eligibleOps].sort((a,b)=>b.tph-a.tph)[0];

document.getElementById('header-sub').textContent =
  `Yesterday: ${COVERED_DATE}  ·  Updated: ${GENERATED_AT}  ·  ${OPS.length} operators worked, ${eligibleOps.length} for 2+ hours.`;

const kpis = [
  {label:'People Working',     hint:'operators on the floor', value: fmt(OPS.length)},
  {label:'Tasks Completed',    hint:'total RF-gun scans',     value: fmt(totalTx)},
  {label:'Units Moved',        hint:'cases / pieces touched', value: fmt(totalQty)},
  {label:'Hours Worked',       hint:'active scanning time',   value: fmt1(totalHrs)},
  {label:'Tasks per Hour',     hint:'company-wide average',   value: fmt1(orgTph)},
  {label:'Top Performer',      hint:'fastest operator',
   value: top1 ? top1.name.slice(0,18) : '—',
   delta: top1 ? `${fmt1(top1.tph)} tasks/hr · ${top1.wh}` : ''}
];
document.getElementById('kpis').innerHTML = kpis.map(k =>
  `<div class="kpi"><div class="label">${k.label}<span class="what">${k.hint}</span></div><div class="value">${k.value}</div>${k.delta?`<div class="delta">${k.delta}</div>`:''}</div>`
).join('');

Chart.defaults.color = '#475569';
Chart.defaults.borderColor = '#e5e7eb';
Chart.defaults.font.family = '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
Chart.defaults.font.size = 11;

new Chart(document.getElementById('trendChart'), {
  data: {
    labels: TREND.map(t=>t.date.slice(5)),
    datasets: [
      {type:'bar',  label:'Tasks Completed', data: TREND.map(t=>t.tx),  backgroundColor:'rgba(59,130,246,.55)', borderColor:'#3b82f6', borderWidth:1, yAxisID:'y'},
      {type:'line', label:'Tasks per Hour',  data: TREND.map(t=>t.tph), borderColor:'#f59e0b', backgroundColor:'rgba(245,158,11,.18)', tension:.3, yAxisID:'y1', pointRadius:3}
    ]
  },
  options: {
    responsive:true, maintainAspectRatio:false,
    scales: {
      y:  {position:'left',  beginAtZero:true, title:{display:true,text:'Tasks completed'}, grid:{color:'rgba(0,0,0,.04)'}},
      y1: {position:'right', beginAtZero:true, title:{display:true,text:'Tasks per hour'}, grid:{display:false}}
    },
    plugins: {legend:{position:'bottom',labels:{boxWidth:10,padding:8}}}
  }
});

new Chart(document.getElementById('whChart'), {
  data: {
    labels: BY_WH.map(w=>w.wh),
    datasets: [
      {type:'bar',  label:'Tasks Completed', data: BY_WH.map(w=>w.tx),  backgroundColor:'rgba(16,185,129,.55)', borderColor:'#10b981', borderWidth:1, yAxisID:'y'},
      {type:'line', label:'Tasks per Hour',  data: BY_WH.map(w=>w.tph), borderColor:'#f59e0b', backgroundColor:'rgba(245,158,11,.18)', tension:.3, yAxisID:'y1', pointRadius:3}
    ]
  },
  options: {
    responsive:true, maintainAspectRatio:false,
    scales: {
      y:  {position:'left',  beginAtZero:true, grid:{color:'rgba(0,0,0,.04)'}},
      y1: {position:'right', beginAtZero:true, grid:{display:false}}
    },
    plugins: {legend:{position:'bottom',labels:{boxWidth:10,padding:8}}}
  }
});

const CAT_LABELS = {
  PICK:'Picking',
  RECEIVE:'Receiving',
  PUTAWAY:'Putaway',
  MOVE:'Moving / Replen',
  LOAD_SHIP:'Loading / Shipping',
  CYCLE_COUNT:'Cycle Counting'
};
const mixColors = {PICK:'#3b82f6', RECEIVE:'#10b981', PUTAWAY:'#f59e0b', MOVE:'#a78bfa', LOAD_SHIP:'#ec4899', CYCLE_COUNT:'#06b6d4'};
const mixSorted = [...MIX].sort((a,b)=>b.qty-a.qty);
new Chart(document.getElementById('mixChart'), {
  type:'bar',
  data: {
    labels: mixSorted.map(m=>CAT_LABELS[m.cat]||m.cat),
    datasets: [{label:'Units', data: mixSorted.map(m=>m.qty), backgroundColor: mixSorted.map(m=>mixColors[m.cat]||'#94a3b8'), borderWidth:0}]
  },
  options: {
    indexAxis:'y', responsive:true, maintainAspectRatio:false,
    scales: { x:{beginAtZero:true, grid:{color:'rgba(0,0,0,.04)'}}, y:{grid:{display:false}} },
    plugins: {
      legend:{display:false},
      tooltip:{callbacks:{
        title: ctx => CAT_LABELS[mixSorted[ctx[0].dataIndex].cat]||mixSorted[ctx[0].dataIndex].cat,
        label: ctx => `Units: ${fmt(mixSorted[ctx.dataIndex].qty)}`,
        afterLabel: ctx => `Tasks: ${fmt(mixSorted[ctx.dataIndex].tx)} · People doing this: ${mixSorted[ctx.dataIndex].ops}`
      }}
    }
  }
});

const topSorted = [...eligibleOps].sort((a,b)=>b.tph-a.tph).slice(0,10);
const botSorted = [...eligibleOps].sort((a,b)=>a.tph-b.tph).slice(0,10);
const renderMini = (arr, tbodyId, klass) => {
  document.querySelector(`#${tbodyId} tbody`).innerHTML = arr.map((o,i) =>
    `<tr class="${klass}"><td>${i+1}</td><td>${o.name}</td><td>${o.wh}</td><td class="num">${fmt1(o.tph)}</td><td class="num">${fmt(o.tx)}</td><td class="num">${fmt1(o.hours)}</td></tr>`
  ).join('');
};
renderMini(topSorted, 'topTable', 'top-row');
renderMini(botSorted, 'botTable', 'bot-row');

const rows = OPS
  .slice()
  .sort((a,b)=>b.tph-a.tph)
  .map(o => [o.id, o.name, o.wh, o.pick, o.receive, o.putaway, o.move, o.load, o.cycle, o.tx, o.qty, +o.hours.toFixed(2), +o.tph.toFixed(1)]);

new gridjs.Grid({
  columns: [
    {name:'ID', width:'70px'},
    {name:'Operator', width:'180px'},
    {name:'Warehouse', width:'100px'},
    {name:'Picks'}, {name:'Receives'}, {name:'Putaways'}, {name:'Moves'}, {name:'Loading'}, {name:'Counts'},
    {name:'Total Tasks'}, {name:'Units'}, {name:'Hours'},
    {name:'Tasks/Hr', formatter: cell => gridjs.html(`<strong>${cell}</strong>`)}
  ],
  data: rows,
  sort: true,
  search: true,
  pagination: { limit: 20, summary: true }
}).render(document.getElementById('fullTable'));
</script>
"""

HTML = (HTML
        .replace('__GEN_AT__', gen_at)
        .replace('__COVERED_DATE__', yesterday_str)
        .replace('__OPS__', OPS_JSON)
        .replace('__TREND__', TREND_JSON)
        .replace('__MIX__', MIX_JSON)
        .replace('__BY_WH__', WH_JSON))

DEST.parent.mkdir(parents=True, exist_ok=True)
DEST.write_text(HTML, encoding='utf-8')

size_kb = DEST.stat().st_size / 1024
print(f"\nWrote new artifact HTML ({size_kb:.0f} KB) to:")
print(f"  {DEST}")
print(f"\nYesterday ({yesterday_str}): {len(ops)} operators, {sum(o['tx'] for o in ops):,} tasks, {sum(o['qty'] for o in ops):,} units.")
