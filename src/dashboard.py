"""Track 6 - build the self-contained management dashboard (one HTML file, no internet or server needed).

Usage: python src/dashboard.py   ->  reports/dashboard/Atreides_Business_Dashboard.html
"""
import json
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "dashboard" / "Atreides_Business_Dashboard.html"
REPRICE = "2025-12-01"
PRODUCTS = ["Flex Fare", "Card (metered)", "Cash (metered)", "Other"]
PRODUCT_SQL = "CASE payment_code WHEN 0 THEN 0 WHEN 1 THEN 1 WHEN 2 THEN 2 ELSE 3 END"


def load():
    con = duckdb.connect(str(ROOT / "data" / "warehouse.duckdb"), read_only=True)
    months = [r[0] for r in con.execute("SELECT DISTINCT strftime(day, '%Y-%m') m FROM trip_stats ORDER BY m").fetchall()]
    boroughs = [r[0] for r in con.execute("""SELECT z.borough FROM trip_stats t JOIN zones z ON t.pickup_loc_id = z.loc_id
                                             GROUP BY 1 ORDER BY sum(trips) DESC""").fetchall()]
    cube = con.execute(f"""
        SELECT list_position(?, strftime(day, '%Y-%m')) - 1 AS mi, (dayofweek(day) IN (0, 6))::INT AS wk, hour,
               list_position(?, z.borough) - 1 AS bi, {PRODUCT_SQL} AS pi,
               sum(trips)::BIGINT, round(sum(revenue), 2), round(sum(base_fare), 2), round(sum(tips), 2),
               round(sum(miles), 1), round(sum(meter_minutes), 1)
        FROM trip_stats t JOIN zones z ON t.pickup_loc_id = z.loc_id GROUP BY ALL ORDER BY ALL""", [months, boroughs]).fetchall()
    heat = con.execute("""
        SELECT isodow(day) - 1 AS dow, hour, list_position(?, z.borough) - 1 AS bi,
               round(sum(revenue), 2), round(sum(meter_minutes), 1), sum(trips)::BIGINT
        FROM trip_stats t JOIN zones z ON t.pickup_loc_id = z.loc_id GROUP BY ALL ORDER BY ALL""", [boroughs]).fetchall()
    ledger = con.execute(f"""
        SELECT list_position(?, month) - 1 AS mi, {PRODUCT_SQL} AS pi, sum(n_rows)::BIGINT, round(sum(charge_total), 2)
        FROM ledger WHERE row_class = 'reversal' GROUP BY ALL ORDER BY ALL""", [months]).fetchall()
    total_raw = con.execute("SELECT sum(n_rows) FROM ledger").fetchone()[0]
    return {"months": months, "boroughs": boroughs, "products": PRODUCTS, "reprice_mi": months.index(REPRICE[:7]),
            "cube": cube, "heat": heat, "ledger": ledger, "total_raw": int(total_raw)}


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flex Fare Performance — Management Dashboard</title>
<style>
:root{
  color-scheme:light;
  --surface-0:#f4f3ef; --surface-1:#fcfcfb; --surface-2:#f0efec; --hairline:#e3e1db;
  --text-primary:#0b0b0b; --text-secondary:#52514e; --text-muted:#76746f;
  --series-1:#2a78d6; --series-2:#eb6834; --series-3:#1baf7a; --series-4:#8a8983;
  --accent:#2a78d6; --annot:#8a8983;
  --seq-0:#cde2fb; --seq-1:#9ec5f4; --seq-2:#6da7ec; --seq-3:#3987e5; --seq-4:#256abf; --seq-5:#184f95; --seq-6:#0d366b;
  --good:#1d7a3a; --warn:#a15c00;
}
@media (prefers-color-scheme: dark){
  :root:where(:not([data-theme="light"])){
    color-scheme:dark;
    --surface-0:#121211; --surface-1:#1a1a19; --surface-2:#383835; --hairline:#2e2e2c;
    --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#9a998f;
    --series-1:#3987e5; --series-2:#d95926; --series-3:#199e70; --series-4:#8a8983; --accent:#3987e5; --annot:#8a8983;
    --seq-0:#0d366b; --seq-1:#104281; --seq-2:#184f95; --seq-3:#256abf; --seq-4:#3987e5; --seq-5:#6da7ec; --seq-6:#b7d3f6;
    --good:#5cc27c; --warn:#e3a13b;
  }
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --surface-0:#121211; --surface-1:#1a1a19; --surface-2:#383835; --hairline:#2e2e2c;
  --text-primary:#ffffff; --text-secondary:#c3c2b7; --text-muted:#9a998f;
  --series-1:#3987e5; --series-2:#d95926; --series-3:#199e70; --series-4:#8a8983; --accent:#3987e5; --annot:#8a8983;
  --seq-0:#0d366b; --seq-1:#104281; --seq-2:#184f95; --seq-3:#256abf; --seq-4:#3987e5; --seq-5:#6da7ec; --seq-6:#b7d3f6;
  --good:#5cc27c; --warn:#e3a13b;
}
*{box-sizing:border-box}
body{margin:0;background:var(--surface-0);color:var(--text-primary);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1240px;margin:0 auto;padding:28px 24px 64px}
header h1{font-size:28px;line-height:1.2;margin:0 0 6px;letter-spacing:-.01em}
header p{margin:0;color:var(--text-secondary);max-width:860px}
.eyebrow{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--text-muted);margin-bottom:6px}
.filters{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;gap:16px;align-items:end;background:var(--surface-0);padding:14px 0 12px;margin:18px 0 8px;border-bottom:1px solid var(--hairline)}
.filters label{display:flex;flex-direction:column;font-size:12px;color:var(--text-secondary);gap:4px}
select,button{font:inherit;color:var(--text-primary);background:var(--surface-1);border:1px solid var(--hairline);border-radius:8px;padding:6px 10px}
button{cursor:pointer}
.seg{display:inline-flex;border:1px solid var(--hairline);border-radius:8px;overflow:hidden}
.seg button{border:0;border-radius:0;background:var(--surface-1)}
.seg button[aria-pressed="true"]{background:var(--text-primary);color:var(--surface-1)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:18px 0 8px}
.kpi{background:var(--surface-1);border:1px solid var(--hairline);border-radius:12px;padding:14px 16px}
.kpi .l{font-size:12px;color:var(--text-secondary)}
.kpi .v{font-size:26px;font-weight:600;letter-spacing:-.01em;margin-top:2px;font-variant-numeric:tabular-nums}
.kpi .s{font-size:12px;color:var(--text-muted);margin-top:2px}
.chapter{margin-top:40px}
.chapter h2{font-size:21px;margin:0 0 4px;letter-spacing:-.01em}
.chapter .lede{color:var(--text-secondary);margin:0 0 14px;max-width:900px}
.num{display:inline-block;min-width:26px;height:26px;border-radius:13px;background:var(--text-primary);color:var(--surface-1);text-align:center;font-weight:600;font-size:13px;line-height:26px;margin-right:8px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:14px}
.card{background:var(--surface-1);border:1px solid var(--hairline);border-radius:12px;padding:16px 16px 10px;min-width:0}
.card h3{font-size:15px;margin:0 0 2px}
.card .sub{font-size:12px;color:var(--text-muted);margin:0 0 8px}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--text-secondary);margin:0 0 6px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
svg{display:block;width:100%;height:auto;overflow:visible}
.axis text{fill:var(--text-muted);font-size:11px}
.axis line,.grid line{stroke:var(--hairline);stroke-width:1}
.dlabel{font-size:11px;fill:var(--text-secondary)}
details{margin-top:6px;font-size:12px;color:var(--text-secondary)}
details summary{cursor:pointer;color:var(--text-muted)}
.tbl{overflow-x:auto;margin-top:6px}
table{border-collapse:collapse;font-variant-numeric:tabular-nums;font-size:12px}
th,td{padding:3px 10px 3px 0;text-align:right;border-bottom:1px solid var(--hairline);white-space:nowrap}
th:first-child,td:first-child{text-align:left}
.insight{border-left:3px solid var(--accent);padding:2px 0 2px 12px;margin:10px 0 0;color:var(--text-primary)}
.recs{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
.rec{background:var(--surface-1);border:1px solid var(--hairline);border-radius:12px;padding:16px}
.rec h3{margin:0 0 6px;font-size:15px}
.rec p{margin:0 0 6px;color:var(--text-secondary)}
.rec .impact{font-weight:600;color:var(--text-primary)}
.scenario{display:grid;grid-template-columns:minmax(260px,1fr) minmax(260px,1fr);gap:18px;align-items:center}
.scenario input[type=range]{width:100%}
.big{font-size:34px;font-weight:600;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
#tip{position:fixed;pointer-events:none;z-index:20;background:var(--surface-1);color:var(--text-primary);border:1px solid var(--hairline);border-radius:8px;padding:8px 10px;font-size:12px;box-shadow:0 4px 18px rgba(0,0,0,.12);display:none;min-width:140px}
#tip .t{font-weight:600;margin-bottom:4px}
#tip .r{display:flex;justify-content:space-between;gap:14px;font-variant-numeric:tabular-nums}
#tip .r i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:6px}
.note{font-size:12px;color:var(--text-muted);margin-top:24px}
@media (max-width:640px){.scenario{grid-template-columns:1fr}.grid2{grid-template-columns:1fr}}
</style></head>
<body>
<div class="wrap">
<header>
  <div class="eyebrow">Team Atreides · Urban Flow Analytics · management dashboard</div>
  <h1>Flex Fare: is upfront pricing paying off?</h1>
  <p>Flex Fare — the app-booked, price-agreed-upfront product — grew from 1 in 6 to more than 1 in 4 trips this year and was repriced in
  December 2025. This dashboard follows one question from problem to action, over all 44.1 M valid trips (Apr 2025 – Mar 2026).</p>
</header>

<div class="filters" role="group" aria-label="Filters">
  <label>Pickup borough<select id="fBorough"></select></label>
  <label>From<select id="fFrom"></select></label>
  <label>To<select id="fTo"></select></label>
  <label>Days<span class="seg" id="fDays"><button data-v="all" aria-pressed="true">All</button><button data-v="0" aria-pressed="false">Weekdays</button><button data-v="1" aria-pressed="false">Weekends</button></span></label>
  <button id="fReset" title="Reset filters">Reset</button>
</div>

<div class="kpis" id="kpis"></div>

<section class="chapter">
  <h2><span class="num">1</span>The problem — Flex Fare is reshaping the business</h2>
  <p class="lede" id="lede1"></p>
  <div class="grid2">
    <div class="card"><h3>Flex Fare share of trips</h3><p class="sub">% of valid trips per month · dashed line = Dec-2025 repricing</p><div id="c_share"></div></div>
    <div class="card"><h3>Revenue per trip by product</h3><p class="sub">Total billed ÷ trips, per month (USD)</p><div id="c_rev"></div></div>
  </div>
</section>

<section class="chapter">
  <h2><span class="num">2</span>What the data tells us</h2>
  <p class="lede" id="lede2"></p>
  <div class="grid2">
    <div class="card"><h3>Price per mile: Flex vs the meter</h3><p class="sub">Base fare ÷ miles (USD)</p><div id="c_permile"></div></div>
    <div class="card"><h3>Recorded tips per trip</h3><p class="sub">Card-recorded tips ÷ trips (USD) · cash tips are not recorded</p><div id="c_tips"></div></div>
  </div>
  <p class="insight" id="insight2"></p>
</section>

<section class="chapter">
  <h2><span class="num">3</span>Why it is happening</h2>
  <p class="lede">Flex is a different kind of trip: booked in the app, priced before pickup, skewed to late night and longer rides. Its checkout has no recorded tip step, and refund reversals collapsed when the new pricing launched.</p>
  <div class="grid2">
    <div class="card"><h3>When trips happen</h3><p class="sub">% of each product's trips by pickup hour</p><div id="c_hour"></div></div>
    <div class="card"><h3>Refund reversals recorded</h3><p class="sub">Reversal records per month, all 48.6 M raw rows (borough and day filters do not apply)</p><div id="c_rev_rows"></div></div>
  </div>
</section>

<section class="chapter">
  <h2><span class="num">4</span>What the business should do</h2>
  <div class="card" style="margin-bottom:14px">
    <h3>Scenario — add a tip step to the Flex checkout</h3>
    <p class="sub">Uses Flex base fare since the repricing, annualised, for the current filters</p>
    <div class="scenario">
      <div>
        <label for="tipCap" style="font-size:13px;color:var(--text-secondary)">Flex tip rate reaches <b id="tipCapV">50%</b> of the card tip rate</label>
        <input id="tipCap" type="range" min="0" max="100" step="5" value="50">
        <div style="font-size:12px;color:var(--text-muted)" id="tipAssump"></div>
      </div>
      <div><div class="big" id="tipGain"></div><div style="color:var(--text-secondary)">extra driver income per year</div></div>
    </div>
  </div>
  <div class="recs" id="recs"></div>
  <div class="card" style="margin-top:14px"><h3>Where and when a meter-hour earns most</h3><p class="sub">Revenue per hour of meter time (USD), pickup weekday × hour · scale below the grid</p><div id="c_heat"></div></div>
</section>

<p class="note">Sources: Urban Flow Analytics taxi dataset (48.6 M records) after the data-quality rules in the technical report. Revenue = amount billed to riders excluding cash tips.
Flex Fare = settlement code 0. "Metered" = card and cash trips. Figures recompute live for the selected filters; every chart has a data table.</p>
</div>
<div id="tip" role="tooltip"></div>

<script>
const D = __DATA__;
const MONTHS = D.months, B = D.boroughs, P = D.products;
const MLAB = MONTHS.map(m => { const [y, mo] = m.split('-'); return new Date(+y, +mo - 1, 1).toLocaleString('en-US', {month: 'short'}) + (mo === '01' || m === MONTHS[0] ? " '" + y.slice(2) : ''); });
const SER = ['var(--series-1)', 'var(--series-2)', 'var(--series-3)', 'var(--series-4)'];
const state = {b: -1, m0: 0, m1: MONTHS.length - 1, wk: 'all'};
const $ = s => document.querySelector(s);
const ok = v => v != null && isFinite(v);
const fmt = {
  n: v => !ok(v) ? '—' : v >= 1e6 ? (v / 1e6).toFixed(1) + ' M' : v >= 1e3 ? (v / 1e3).toFixed(0) + ' k' : Math.round(v).toString(),
  usd: v => !ok(v) ? '—' : '$' + v.toFixed(2), usdM: v => !ok(v) ? '—' : v >= 1e6 ? '$' + (v / 1e6).toFixed(1) + ' M' : '$' + Math.round(v / 1e3) + ' k',
  pct: v => !ok(v) ? '—' : v.toFixed(1) + '%'
};

// ---------- aggregation over the cube ----------
// cube row: [mi, wk, hr, bi, pi, trips, revenue, base_fare, tips, miles, minutes]
function rows() {
  return D.cube.filter(r => r[0] >= state.m0 && r[0] <= state.m1 && (state.b < 0 || r[3] === state.b) && (state.wk === 'all' || r[1] === +state.wk));
}
function sumBy(rs, keyFn) {
  const m = new Map();
  for (const r of rs) {
    const k = keyFn(r); let a = m.get(k);
    if (!a) m.set(k, a = [0, 0, 0, 0, 0, 0]);
    for (let i = 0; i < 6; i++) a[i] += r[5 + i];
  }
  return m;
}
const T = 0, REV = 1, FARE = 2, TIPS = 3, MI = 4, MIN = 5;

// ---------- tooltip ----------
const tip = $('#tip');
function showTip(e, title, items) {
  tip.innerHTML = `<div class="t">${title}</div>` + items.map(([c, l, v]) => `<div class="r"><span>${c ? `<i style="background:${c}"></i>` : ''}${l}</span><b>${v}</b></div>`).join('');
  tip.style.display = 'block';
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let x = e.clientX + 14, y = e.clientY + 14;
  if (x + w > innerWidth - 8) x = e.clientX - w - 14;
  if (y + h > innerHeight - 8) y = e.clientY - h - 14;
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
}
const hideTip = () => tip.style.display = 'none';

// ---------- charts (plain SVG) ----------
const NS = 'http://www.w3.org/2000/svg';
function el(tag, attrs, parent) { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); parent && parent.appendChild(e); return e; }
function niceMax(v) { if (v <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(v))); for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p; return 10 * p; }
function table(host, head, body) {
  const d = document.createElement('details');
  d.innerHTML = `<summary>Show data table</summary><div class="tbl"><table><thead><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr></thead><tbody>${body.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  host.appendChild(d);
}
function legend(host, series) {
  if (series.length < 2) return;
  const l = document.createElement('div'); l.className = 'legend';
  l.innerHTML = series.map(s => `<span><i style="background:${s.color}"></i>${s.name}</span>`).join('');
  host.appendChild(l);
}

// line chart: xs labels, series [{name, color, values[] (null allowed)}], opts {fmt, marker (x index), markerLabel, yMin}
function empty(host) { host.innerHTML = '<p class="sub" style="padding:40px 0;text-align:center">No trips for this selection.</p>'; }
function lineChart(host, xs, series, o) {
  if (!series.some(s => s.values.some(ok))) return empty(host);
  host.innerHTML = ''; legend(host, series);
  const W = 560, H = 230, m = {l: 44, r: 92, t: 10, b: 26};
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': o.label || ''}, host);
  const all = series.flatMap(s => s.values).filter(v => v != null);
  const ymax = niceMax(Math.max(...all) * 1.05), ymin = o.yMin ?? 0;
  const x = i => m.l + (xs.length === 1 ? (W - m.l - m.r) / 2 : i * (W - m.l - m.r) / (xs.length - 1));
  const y = v => m.t + (1 - (v - ymin) / (ymax - ymin)) * (H - m.t - m.b);
  const g = el('g', {class: 'grid'}, svg), ax = el('g', {class: 'axis'}, svg);
  for (let k = 0; k <= 4; k++) {
    const v = ymin + k * (ymax - ymin) / 4;
    el('line', {x1: m.l, x2: W - m.r, y1: y(v), y2: y(v)}, g);
    el('text', {x: m.l - 8, y: y(v) + 4, 'text-anchor': 'end'}, ax).textContent = o.fmt(v).replace('.00', '');
  }
  const step = Math.ceil(xs.length / 8);
  xs.forEach((t, i) => { if (i % step === 0 || i === xs.length - 1) el('text', {x: x(i), y: H - 6, 'text-anchor': 'middle'}, ax).textContent = t; });
  if (o.marker != null && o.marker >= 0 && o.marker < xs.length) {
    el('line', {x1: x(o.marker), x2: x(o.marker), y1: m.t, y2: H - m.b, stroke: 'var(--annot)', 'stroke-width': 1, 'stroke-dasharray': '3 3'}, svg);
    el('text', {x: x(o.marker) + 4, y: m.t + 10, class: 'dlabel'}, svg).textContent = o.markerLabel || '';
  }
  // direct labels at line ends, nudged apart
  const ends = series.map((s, si) => { let i = s.values.length - 1; while (i >= 0 && s.values[i] == null) i--; return {si, i, yy: i >= 0 ? y(s.values[i]) : null}; }).filter(e => e.yy != null).sort((a, b) => a.yy - b.yy);
  for (let k = 1; k < ends.length; k++) if (ends[k].yy - ends[k - 1].yy < 13) ends[k].yy = ends[k - 1].yy + 13;
  series.forEach((s, si) => {
    let d = '', pen = false;
    s.values.forEach((v, i) => { if (v == null) { pen = false; return; } d += (pen ? 'L' : 'M') + x(i).toFixed(1) + ',' + y(v).toFixed(1); pen = true; });
    el('path', {d, fill: 'none', stroke: s.color, 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}, svg);
    s.values.forEach((v, i) => { if (v != null && s.values[i - 1] == null && s.values[i + 1] == null) el('circle', {cx: x(i), cy: y(v), r: 4, fill: s.color}, svg); });
  });
  ends.forEach(e => { const s = series[e.si]; el('text', {x: x(e.i) + 8, y: e.yy + 4, class: 'dlabel'}, svg).textContent = (series.length > 1 ? s.name.split(' ')[0] + ' ' : '') + o.fmt(s.values[e.i]); });
  // hover: crosshair + dots + tooltip
  const cross = el('line', {y1: m.t, y2: H - m.b, stroke: 'var(--text-muted)', 'stroke-width': 1, visibility: 'hidden'}, svg);
  const dots = series.map(s => el('circle', {r: 4.5, fill: s.color, stroke: 'var(--surface-1)', 'stroke-width': 2, visibility: 'hidden'}, svg));
  const hit = el('rect', {x: m.l - 10, y: 0, width: W - m.l - m.r + 20, height: H, fill: 'transparent'}, svg);
  hit.addEventListener('mousemove', e => {
    const pt = svg.createSVGPoint(); pt.x = e.clientX; pt.y = e.clientY;
    const lx = pt.matrixTransform(svg.getScreenCTM().inverse()).x;
    const i = Math.max(0, Math.min(xs.length - 1, Math.round((lx - m.l) / ((W - m.l - m.r) / Math.max(1, xs.length - 1)))));
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
    series.forEach((s, si) => { const v = s.values[i]; if (v == null) { dots[si].setAttribute('visibility', 'hidden'); return; } dots[si].setAttribute('cx', x(i)); dots[si].setAttribute('cy', y(v)); dots[si].setAttribute('visibility', 'visible'); });
    showTip(e, o.tipTitle ? o.tipTitle(i) : xs[i], series.map(s => [s.color, s.name, s.values[i] == null ? '—' : o.fmt(s.values[i])]));
  });
  hit.addEventListener('mouseleave', () => { cross.setAttribute('visibility', 'hidden'); dots.forEach(d => d.setAttribute('visibility', 'hidden')); hideTip(); });
  table(host, [o.xName || 'Period', ...series.map(s => s.name)], xs.map((t, i) => [o.tipTitle ? o.tipTitle(i) : t, ...series.map(s => s.values[i] == null ? '—' : o.fmt(s.values[i]))]));
}

function barChart(host, xs, values, o) {
  if (!values.some(v => v > 0)) return empty(host);
  host.innerHTML = '';
  const W = 560, H = 230, m = {l: 44, r: 12, t: 16, b: 26};
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': o.label || ''}, host);
  const ymax = niceMax(Math.max(...values) * 1.08);
  const bw = (W - m.l - m.r) / xs.length, y = v => m.t + (1 - v / ymax) * (H - m.t - m.b);
  const g = el('g', {class: 'grid'}, svg), ax = el('g', {class: 'axis'}, svg);
  for (let k = 0; k <= 4; k++) { const v = k * ymax / 4; el('line', {x1: m.l, x2: W - m.r, y1: y(v), y2: y(v)}, g); el('text', {x: m.l - 8, y: y(v) + 4, 'text-anchor': 'end'}, ax).textContent = o.fmt(v); }
  values.forEach((v, i) => {
    const x0 = m.l + i * bw + 1, w = Math.max(2, bw - 2), top = y(v), h = H - m.b - top, r = Math.min(4, w / 2, h);
    const d = `M${x0},${H - m.b}V${top + r}Q${x0},${top} ${x0 + r},${top}H${x0 + w - r}Q${x0 + w},${top} ${x0 + w},${top + r}V${H - m.b}Z`;
    const bar = el('path', {d, fill: o.colorAt ? o.colorAt(i) : 'var(--series-1)'}, svg);
    el('text', {x: x0 + w / 2, y: H - 6, 'text-anchor': 'middle'}, ax).textContent = xs[i];
    const hit = el('rect', {x: m.l + i * bw, y: m.t, width: bw, height: H - m.t - m.b, fill: 'transparent'}, svg);
    hit.addEventListener('mousemove', e => { bar.style.opacity = .8; showTip(e, o.tipTitle ? o.tipTitle(i) : xs[i], [[null, o.valueName, o.fmt(v)], ...(o.extra ? o.extra(i) : [])]); });
    hit.addEventListener('mouseleave', () => { bar.style.opacity = 1; hideTip(); });
  });
  const iMax = values.indexOf(Math.max(...values)), iLast = values.length - 1;
  [iMax, iLast].filter((v, k, a) => a.indexOf(v) === k).forEach(i => el('text', {x: m.l + i * bw + bw / 2, y: y(values[i]) - 5, 'text-anchor': 'middle', class: 'dlabel'}, svg).textContent = o.fmt(values[i]));
  table(host, [o.xName || 'Period', o.valueName], xs.map((t, i) => [o.tipTitle ? o.tipTitle(i) : t, o.fmt(values[i])]));
}

function heatmap(host, rowsL, colsL, grid, o) {
  if (!grid.flat().some(ok)) return empty(host);
  host.innerHTML = '';
  const cw = 22, ch = 22, m = {l: 40, t: 18, r: 8, b: 30}, W = m.l + colsL.length * cw + m.r, H = m.t + rowsL.length * ch + m.b;
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': o.label || '', style: `max-width:${W * 1.2}px`}, host);
  const vals = grid.flat().filter(v => v != null), lo = Math.min(...vals), hi = Math.max(...vals);
  const ax = el('g', {class: 'axis'}, svg);
  colsL.forEach((c, j) => { if (j % 3 === 0) el('text', {x: m.l + j * cw + cw / 2, y: m.t - 5, 'text-anchor': 'middle'}, ax).textContent = c; });
  rowsL.forEach((r, i) => el('text', {x: m.l - 6, y: m.t + i * ch + ch / 2 + 4, 'text-anchor': 'end'}, ax).textContent = r);
  grid.forEach((row, i) => row.forEach((v, j) => {
    if (v == null) return;
    const k = Math.min(6, Math.floor((v - lo) / (hi - lo + 1e-9) * 7));
    const cell = el('rect', {x: m.l + j * cw + 1, y: m.t + i * ch + 1, width: cw - 2, height: ch - 2, rx: 3, fill: `var(--seq-${k})`}, svg);
    cell.addEventListener('mousemove', e => { cell.setAttribute('stroke', 'var(--text-primary)'); showTip(e, `${rowsL[i]} ${colsL[j]}:00`, [[null, o.valueName, o.fmt(v)], ...(o.extra ? o.extra(i, j) : [])]); });
    cell.addEventListener('mouseleave', () => { cell.removeAttribute('stroke'); hideTip(); });
  }));
  // scale legend
  const lg = el('g', {}, svg), lx = m.l, ly = H - 16;
  for (let k = 0; k < 7; k++) el('rect', {x: lx + 40 + k * 26, y: ly - 8, width: 24, height: 8, rx: 2, fill: `var(--seq-${k})`}, lg);
  el('text', {x: lx + 34, y: ly, 'text-anchor': 'end', class: 'dlabel'}, lg).textContent = o.fmt(lo);
  el('text', {x: lx + 40 + 7 * 26 + 4, y: ly, class: 'dlabel'}, lg).textContent = o.fmt(hi);
  table(host, ['Day', ...colsL.map(c => c + ':00')], grid.map((r, i) => [rowsL[i], ...r.map(v => v == null ? '—' : o.fmt(v))]));
}

// ---------- render ----------
let render = function () {
  const rs = rows();
  const mIdx = []; for (let i = state.m0; i <= state.m1; i++) mIdx.push(i);
  const xs = mIdx.map(i => MLAB[i]), full = i => new Date(MONTHS[mIdx[i]] + '-01').toLocaleString('en-US', {month: 'long', year: 'numeric'});
  const byMP = sumBy(rs, r => r[0] + '|' + r[4]), byM = sumBy(rs, r => r[0]);
  const get = (mi, pi, f) => { const a = byMP.get(mi + '|' + pi); return a ? a[f] : 0; };
  const ratio = (mi, pi, num, den, k = 1) => { const d = get(mi, pi, den); return d > 0 ? k * get(mi, pi, num) / d : null; };
  const marker = D.reprice_mi - state.m0;

  // KPIs (whole selection)
  const tot = [0, 0, 0, 0, 0, 0], prod = [0, 1, 2, 3].map(() => [0, 0, 0, 0, 0, 0]), post = [0, 1, 2, 3].map(() => [0, 0, 0, 0, 0, 0]);
  for (const r of rs) for (let i = 0; i < 6; i++) { tot[i] += r[5 + i]; prod[r[4]][i] += r[5 + i]; if (r[0] >= D.reprice_mi) post[r[4]][i] += r[5 + i]; }
  const share = tot[T] ? 100 * prod[0][T] / tot[T] : 0;
  const pr = (a, f, g) => a[g] ? a[f] / a[g] : 0;
  const hasPost = post[0][T] > 0;
  $('#kpis').innerHTML = [
    ['Valid trips', fmt.n(tot[T]), `${fmt.usdM(tot[REV])} billed`],
    ['Flex Fare share', fmt.pct(share), `${fmt.n(prod[0][T])} Flex trips`],
    ['Flex revenue / trip' + (hasPost ? ' since repricing' : ''), fmt.usd(pr(hasPost ? post[0] : prod[0], REV, T)), `card ${fmt.usd(pr(hasPost ? post[1] : prod[1], REV, T))} · cash ${fmt.usd(pr(hasPost ? post[2] : prod[2], REV, T))}`],
    ['Recorded tip rate', fmt.pct(100 * pr(prod[0], TIPS, FARE)) + ' Flex', `vs ${fmt.pct(100 * pr(prod[1], TIPS, FARE))} on card trips`],
    ['Revenue per meter-hour', fmt.usd(tot[MIN] ? 60 * tot[REV] / tot[MIN] : 0), `avg speed ${(tot[MIN] ? 60 * tot[MI] / tot[MIN] : 0).toFixed(1)} mph`],
  ].map(([l, v, s]) => `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join('');

  const shares = mIdx.map(mi => { const a = byM.get(mi); return a && a[T] ? 100 * get(mi, 0, T) / a[T] : null; });
  const valid = shares.filter(ok), first = valid[0], peak = valid.length ? Math.max(...valid) : null, last = valid[valid.length - 1];
  const small = prod[0][T] < 1000, fxPost = pr(post[0], REV, T), cdPost = pr(post[1], REV, T);
  const vsCard = !hasPost ? 'The selection ends before the December repricing.' : fxPost > cdPost
    ? `Since the December repricing a Flex trip bills ${fmt.usd(fxPost)} vs ${fmt.usd(cdPost)} for a metered card trip — Flex is now the most lucrative product per trip here.`
    : `Since the December repricing a Flex trip bills ${fmt.usd(fxPost)} vs ${fmt.usd(cdPost)} for a metered card trip here (card trips in this selection are longer, e.g. airport runs).`;
  const caution = small ? ` ⚠ Only ${fmt.n(prod[0][T])} Flex trips in this selection — treat Flex comparisons as indicative only.` : '';
  $('#lede1').textContent = `In the selection, Flex Fare went from ${fmt.pct(first)} of trips (${xs[0]}) to a peak of ${fmt.pct(peak)} and ended at ${fmt.pct(last)} (${xs[xs.length - 1]}). ${vsCard}${caution}`;
  lineChart($('#c_share'), xs, [{name: 'Flex share', color: SER[0], values: shares}], {fmt: fmt.pct, marker, markerLabel: 'repricing', tipTitle: full, label: 'Flex Fare share of trips by month'});
  const three = [0, 1, 2].map(pi => ({name: P[pi], color: SER[pi]}));
  lineChart($('#c_rev'), xs, three.map((s, pi) => ({...s, values: mIdx.map(mi => ratio(mi, pi, REV, T))})), {fmt: fmt.usd, marker, markerLabel: 'repricing', tipTitle: full, yMin: 0, label: 'Revenue per trip by product'});

  const metered = mIdx.map(mi => { const f = get(mi, 1, FARE) + get(mi, 2, FARE), mi_ = get(mi, 1, MI) + get(mi, 2, MI); return mi_ ? f / mi_ : null; });
  lineChart($('#c_permile'), xs, [{name: 'Flex Fare', color: SER[0], values: mIdx.map(mi => ratio(mi, 0, FARE, MI))}, {name: 'Metered (card + cash)', color: SER[1], values: metered}],
    {fmt: fmt.usd, marker, markerLabel: 'repricing', tipTitle: full, label: 'Base fare per mile'});
  lineChart($('#c_tips'), xs, three.map((s, pi) => ({...s, values: mIdx.map(mi => ratio(mi, pi, TIPS, T))})), {fmt: fmt.usd, marker, markerLabel: 'repricing', tipTitle: full, label: 'Recorded tips per trip'});

  const fr = pr(hasPost ? post[0] : prod[0], FARE, MI), mr = (prod[1][FARE] + prod[2][FARE]) / Math.max(1, prod[1][MI] + prod[2][MI]);
  const dip = ok(peak) && ok(last) && last < peak - 3;
  $('#lede2').textContent = (fr > mr ? 'Flex now charges more per mile than the meter' : 'Flex charges no more per mile than the meter in this selection') +
    ', but almost none of the fare comes back to drivers as a recorded tip' + (dip ? ', and Flex share has fallen back from its peak.' : '.');
  $('#insight2').innerHTML = small ? `⚠ Only ${fmt.n(prod[0][T])} Flex trips in this selection — too few for a reliable price or tip comparison. Widen the filters.`
    : `<b>Flex riders pay ${fmt.usd(fr)} per mile vs ${fmt.usd(mr)} on the meter (${fr > mr ? '+' : ''}${(100 * (fr / mr - 1)).toFixed(0)}%)</b>, yet recorded tips on a Flex trip are ${fmt.usd(pr(prod[0], TIPS, T))} against ${fmt.usd(pr(prod[1], TIPS, T))} on a card trip. Higher prices without a tip step shift income from drivers to the platform` +
      (dip ? ` — and the drop in Flex share from ${fmt.pct(peak)} to ${fmt.pct(last)} is the first sign riders are noticing the price.` : '.');

  const byHP = sumBy(rs, r => r[2] + '|' + r[4]);
  const hs = [...Array(24).keys()];
  const hshare = pi => { const tt = hs.reduce((a, h) => a + (byHP.get(h + '|' + pi)?.[T] || 0), 0); return hs.map(h => tt ? 100 * (byHP.get(h + '|' + pi)?.[T] || 0) / tt : null); };
  lineChart($('#c_hour'), hs.map(h => String(h)), [{name: 'Flex Fare', color: SER[0], values: hshare(0)}, {name: 'Card (metered)', color: SER[1], values: hshare(1)}],
    {fmt: fmt.pct, xName: 'Pickup hour', tipTitle: i => `${i}:00–${i}:59`, label: 'Share of trips by pickup hour'});

  const led = mIdx.map(mi => D.ledger.filter(r => r[0] === mi).reduce((a, r) => a + r[2], 0));
  barChart($('#c_rev_rows'), xs, led, {fmt: fmt.n, valueName: 'Reversal records', tipTitle: full, label: 'Refund reversal records per month',
    colorAt: i => mIdx[i] >= D.reprice_mi ? 'var(--series-1)' : 'var(--series-4)',
    extra: i => { const f = D.ledger.filter(r => r[0] === mIdx[i] && r[1] === 0).reduce((a, r) => a + r[2], 0); return [[null, 'of which Flex', fmt.n(f)]]; }});

  // scenario
  const days = (state.m1 - Math.max(state.m0, D.reprice_mi) + 1) * 30.4;
  const flexFare = hasPost ? post[0][FARE] : prod[0][FARE], span = hasPost ? days : (state.m1 - state.m0 + 1) * 30.4;
  const annual = flexFare * 365 / Math.max(span, 1), cardRate = pr(prod[1], TIPS, FARE), flexRate = pr(prod[0], TIPS, FARE);
  const upd = () => { const c = +$('#tipCap').value / 100; $('#tipCapV').textContent = Math.round(c * 100) + '%';
    const target = Math.max(flexRate, c * cardRate); $('#tipGain').textContent = fmt.usdM(Math.max(0, (target - flexRate) * annual));
    $('#tipAssump').textContent = `Flex base fare ≈ ${fmt.usdM(annual)} / year · Flex tip rate today ${fmt.pct(100 * flexRate)} → ${fmt.pct(100 * target)} (card: ${fmt.pct(100 * cardRate)})`; };
  $('#tipCap').oninput = upd; upd();

  // heatmap: revenue per meter-hour by weekday x hour (heat rows: [dow, hr, bi, revenue, minutes, trips])
  const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], grid = DOW.map(() => Array(24).fill(null)), gt = DOW.map(() => Array(24).fill(0));
  const acc = new Map();
  for (const r of D.heat) { if (state.b >= 0 && r[2] !== state.b) continue; const k = r[0] * 24 + r[1]; const a = acc.get(k) || [0, 0, 0]; a[0] += r[3]; a[1] += r[4]; a[2] += r[5]; acc.set(k, a); }
  acc.forEach((a, k) => { if (a[2] >= 200) { grid[Math.floor(k / 24)][k % 24] = 60 * a[0] / a[1]; gt[Math.floor(k / 24)][k % 24] = a[2]; } });
  heatmap($('#c_heat'), DOW, hs.map(String), grid, {fmt: v => '$' + Math.round(v), valueName: 'Revenue / meter-hour', extra: (i, j) => [[null, 'Trips', fmt.n(gt[i][j])]], label: 'Revenue per meter-hour by weekday and hour'});

  // recommendations (numbers follow the filters)
  const tipHalf = Math.max(0, (0.5 * cardRate - flexRate) * annual);
  const recs = [
    ['Add a tip step to Flex checkout', `Flex tips are ${fmt.pct(100 * flexRate)} of fare vs ${fmt.pct(100 * cardRate)} on card. Offer 10/15/20% presets in the app after a Flex ride.`, `≈ ${fmt.usdM(tipHalf)} / year more for drivers at half the card rate`],
    ['Guard-rail Flex prices with the upfront fare model', `Flex now costs ${fmt.usd(fr)}/mile vs ${fmt.usd(mr)} on the meter. Use the Section 2.1 fare model as a "fair meter estimate" and cap Flex quotes at a set margin above it.`, 'Protects share — Flex fell back to ' + fmt.pct(last) + ' in ' + xs[xs.length - 1]],
    ['Watch Flex share and price monthly', 'Retrain the pricing and fare models monthly, alert when Flex share drops more than 3 points or its per-mile premium exceeds 30%.', 'Early warning before riders churn to the meter or competitors'],
    ['Keep the new refund process', `Reversal records fell from ${fmt.n(Math.max(...led))} to ${fmt.n(led[led.length - 1])} per month in the selection after the repricing. Audit the remaining disputes.`, 'Cleaner books and fewer chargebacks'],
    ['Staff the evenings, not the midday crawl', 'A meter-hour earns most late at night and least at weekday midday, when traffic is slowest (see heatmap). Shift incentives and shift starts to 18:00–02:00.', 'Higher earnings per driver-hour with the same fleet'],
  ];
  $('#recs').innerHTML = recs.map(([h, p, i]) => `<div class="rec"><h3>${h}</h3><p>${p}</p><div class="impact">${i}</div></div>`).join('');
};

// ---------- filters ----------
$('#fBorough').innerHTML = '<option value="-1">All boroughs</option>' + B.map((b, i) => `<option value="${i}">${b}</option>`).join('');
$('#fFrom').innerHTML = MONTHS.map((m, i) => `<option value="${i}">${new Date(m + '-01').toLocaleString('en-US', {month: 'short', year: 'numeric'})}</option>`).join('');
$('#fTo').innerHTML = $('#fFrom').innerHTML;
function syncControls() { $('#fBorough').value = state.b; $('#fFrom').value = state.m0; $('#fTo').value = state.m1; document.querySelectorAll('#fDays button').forEach(b => b.setAttribute('aria-pressed', b.dataset.v === String(state.wk))); }
$('#fBorough').onchange = e => { state.b = +e.target.value; render(); };
$('#fFrom').onchange = e => { state.m0 = Math.min(+e.target.value, state.m1); syncControls(); render(); };
$('#fTo').onchange = e => { state.m1 = Math.max(+e.target.value, state.m0); syncControls(); render(); };
document.querySelectorAll('#fDays button').forEach(b => b.onclick = () => { state.wk = b.dataset.v; syncControls(); render(); });
$('#fReset').onclick = () => { Object.assign(state, {b: -1, m0: 0, m1: MONTHS.length - 1, wk: 'all'}); syncControls(); render(); };
// shareable views: #borough=Queens&from=2025-12&to=2026-03&days=weekend
function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  if (h.has('borough')) state.b = B.indexOf(h.get('borough'));
  if (h.has('from') && MONTHS.includes(h.get('from'))) state.m0 = MONTHS.indexOf(h.get('from'));
  if (h.has('to') && MONTHS.includes(h.get('to'))) state.m1 = Math.max(state.m0, MONTHS.indexOf(h.get('to')));
  if (h.has('days')) state.wk = {weekday: '0', weekend: '1'}[h.get('days')] || 'all';
}
function writeHash() {
  const h = new URLSearchParams();
  if (state.b >= 0) h.set('borough', B[state.b]);
  if (state.m0 > 0) h.set('from', MONTHS[state.m0]);
  if (state.m1 < MONTHS.length - 1) h.set('to', MONTHS[state.m1]);
  if (state.wk !== 'all') h.set('days', state.wk === '1' ? 'weekend' : 'weekday');
  history.replaceState(null, '', h.toString() ? '#' + h : location.pathname);
}
const _render = render; render = function () { writeHash(); _render(); };
readHash(); syncControls(); render();
</script>
</body></html>
"""


if __name__ == "__main__":
    data = load()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(PAGE.replace("__DATA__", json.dumps(data, separators=(",", ":"), default=float)))
    print(OUT, f"{OUT.stat().st_size / 1e6:.1f} MB", len(data["cube"]), "cube rows")
