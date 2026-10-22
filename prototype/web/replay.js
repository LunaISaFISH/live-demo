// Monitoring replay: plays back a recorded whole-record run (experiment_alerts.py).
// The detectors' days and the coach's reviews are recorded; nothing here calls the model.
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
async function api(url) {
  const r = await fetch(url);
  const body = await r.json();
  if (!r.ok) throw new Error(body.error || r.statusText);
  return body;
}

const KINDS = ["intraday_hr", "resting_hr", "readiness", "sleep_debt", "load_spike", "under_active", "quiet"];
const DUE = {event: "One-off event", new: "New condition", recheck: "Recheck the coach asked for"};
const STEP_MS = 850, DAY_MS = 110, HOLD_MS = 2400;

let RUN = null;        // the recorded run of the chosen participant
let DAY = 0;           // index of the last day the detectors have seen
let NEXT = 0;          // index of the next review to play
let CURRENT = null;    // the review on screen while it is being revealed: {i, items}
let SHOWN = null;      // the review last shown
let SPEED = 1;
let TOKEN = 0;         // bumped to stop whatever is playing
let PLAYING = false;
let SERIES = null;     // the chart's data, kept for a redraw on resize

const sleep = ms => new Promise(r => setTimeout(r, ms / SPEED));
const dayIndex = day => RUN.days.findIndex(d => d.day === day);
const longDate = day => new Date(`${day}T00:00:00`).toLocaleDateString("en-GB", {day: "numeric", month: "long", year: "numeric"});
const addDays = (day, n) => { const d = new Date(`${day}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const label = kind => RUN.labels[kind] || kind;
const uncite = text => String(text ?? "").replace(/\s*\[[0-9a-f]{8}-[\w-]+\]/g, "");

async function load(pid) {
  stop();
  RUN = null;
  $("rp-steps").innerHTML = `<li class="empty">Loading the recorded run…</li>`;
  RUN = await api(`/api/replay?pid=${pid}`);
  const first = RUN.reviews[0];
  $("rp-recorded").textContent = first
    ? `Recorded on ${first.recorded} with ${first.model}: ${plural(RUN.reviews.length, "review")} over ${RUN.days.length} days. ` +
      `The code only flagged each pattern; every decision shown is the coach's own.`
    : "";
  restart();
}

function restart() {
  stop();
  DAY = 0; NEXT = 0; SHOWN = null; SERIES = null;
  $("rp-steps").innerHTML = `<li class="empty">Nothing has fired yet. Press Play: the detectors run day by day, and each signal is handed to the coach.</li>`;
  $("rp-chart").innerHTML = `<div class="chart-empty">The record around each signal appears here, cut at the moment the coach saw it.</div>`;
  $("rp-chart-title").textContent = "The record as the coach saw it";
  $("rp-chart-rule").textContent = "";
  $("rp-status").textContent = "";
  drawLanes();
  setDay(0);
}

// ---------- playback

function stop() {
  TOKEN++;
  settle();
  PLAYING = false;
  $("rp-play").textContent = "Play";
  $("rp-status").classList.remove("live");
}

async function play() {
  if (!RUN) return;
  if (DAY >= RUN.days.length - 1 && NEXT >= RUN.reviews.length) restart();
  const token = ++TOKEN;
  PLAYING = true;
  $("rp-play").textContent = "Pause";
  while (token === TOKEN) {
    const due = RUN.reviews[NEXT];
    if (due && due.day === RUN.days[DAY].day) {
      await reveal(NEXT, token);
      if (token !== TOKEN) return;
      await sleep(HOLD_MS);
      continue;
    }
    if (DAY >= RUN.days.length - 1) break;
    setDay(DAY + 1);
    await sleep(DAY_MS);
  }
  if (token === TOKEN) {
    stop();
    $("rp-status").textContent = "end of the record";
  }
}

// One review, its steps appearing in the order the coach took them.
async function reveal(i, token) {
  const r = RUN.reviews[i];
  setDay(dayIndex(r.day));
  const items = renderReview(r, i);
  CURRENT = {i, items};
  SHOWN = i;
  drawMarkers();
  setDay(DAY);
  loadChart(r);
  $("rp-status").textContent = "reviewing";
  $("rp-status").classList.add("live");
  for (const li of items) {
    if (token !== TOKEN) return;
    li.classList.remove("pending");
    li.scrollIntoView({block: "nearest", behavior: "smooth"});
    await sleep(STEP_MS);
  }
  if (CURRENT?.i === i) settle();
}

// A review stopped half-way is completed at once, so the next one never
// starts from a partial screen.
function settle() {
  if (!CURRENT) return;
  CURRENT.items.forEach(li => li.classList.remove("pending"));
  const r = RUN.reviews[CURRENT.i];
  NEXT = CURRENT.i + 1;
  CURRENT = null;
  $("rp-status").classList.remove("live");
  $("rp-status").textContent = `${r.seconds} s of model time, ${r.tokens.toLocaleString("en-GB")} tokens`;
  drawMarkers();
  setDay(DAY);
}

async function nextReview() {
  if (!RUN) return;
  stop();
  if (NEXT >= RUN.reviews.length) return;
  await reveal(NEXT, ++TOKEN);
}

async function jumpTo(i) {
  stop();
  NEXT = i;
  await reveal(i, ++TOKEN);
}

// ---------- the review, as steps

function source(line) {
  let m;
  if ((m = line.match(/^read_record (\d+)d as of (\S+)/)))
    return ["read the record", `${m[1]} days of daily history, up to ${m[2]}`];
  if ((m = line.match(/^inspect_sensor_window (.+) to (.+)$/)))
    return ["read the sensors", `minute data from ${m[1]} to ${m[2].slice(11, 16)}`];
  if ((m = line.match(/skills\/(?:references\/)?([\w-]+?)(?:\/SKILL)?\.md/)))
    return ["opened a skill file", m[1]];
  return ["tool", line];
}

function renderReview(r, i) {
  const rv = r.review, alert = r.notification.notify;
  const list = xs => xs?.length ? `<ul>${xs.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p>None recorded.</p>`;
  const recheck = rv.recheck_in_days
    ? `<p class="rp-recheck">Look again in ${plural(rv.recheck_in_days, "day")}, on ${addDays(r.day, rv.recheck_in_days)}, if it still holds.</p>` : "";
  const steps = [
    ["signal", `<p class="rp-kicker">${esc(DUE[r.due] || r.due)} · review ${i + 1} of ${RUN.reviews.length}</p>
      <p class="rp-title">${esc(label(r.kind))}</p><p>${esc(r.summary)}</p>
      <p class="rp-meta">${esc(r.at)} · severity ${r.severity}</p>`],
    ["handed", `<p class="rp-kicker">What the monitor handed over</p><pre>${esc(r.facts)}</pre>`],
    ...r.sources.map(s => { const [what, detail] = source(s);
      return ["tool", `<p class="rp-kicker">Coach ${esc(what)}</p><p class="mono">${esc(detail)}</p>`]; }),
    ["found", `<p class="rp-kicker">What it found</p>${list(rv.observations)}`],
    ["limits", `<p class="rp-kicker">What the record cannot show</p>${list(rv.limitations)}`],
    ["verdict", `<p class="rp-kicker">Verdict</p><p><span class="rp-verdict">${esc(rv.verdict)}</span> ${esc(rv.note)}</p>`],
    [`decision ${alert ? "alert" : "calm"}`, `<p class="rp-decision">${alert ? "Alert" : "No Alert"}</p>
      <p>${esc(r.notification.reason)}</p>${recheck}`],
    ["said", `<p class="rp-kicker">Note shown with the decision</p><p>${esc(uncite(r.reply))}</p>`],
  ];
  $("rp-steps").innerHTML = "";
  return steps.map(([cls, html]) => {
    const li = document.createElement("li");
    li.className = `${cls} pending`;
    li.innerHTML = html;
    $("rp-steps").append(li);
    return li;
  });
}

// ---------- time and the detector lanes

function setDay(i) {
  DAY = i;
  const day = RUN.days[i].day;
  const r = SHOWN != null ? RUN.reviews[SHOWN] : null;
  $("rp-date").textContent = r?.day === day && r.kind === "intraday_hr" ? `${longDate(day)}, ${r.at.slice(11, 16)}` : longDate(day);
  const done = RUN.reviews.slice(0, NEXT);
  const alerts = done.filter(x => x.notification.notify).length;
  $("rp-tally").innerHTML = `Day <strong>${i + 1}</strong> of ${RUN.days.length} · ` +
    `<strong>${plural(done.length, "review")}</strong> · <strong class="${alerts ? "alerting" : ""}">${plural(alerts, "Alert")}</strong> so far`;
  const g = GEOM;
  if (!g) return;
  const x = g.x(i + 1);
  $("lane-cover").setAttribute("x", x);
  $("lane-cover").setAttribute("width", Math.max(0, g.right - x));
  $("lane-head").setAttribute("x1", x); $("lane-head").setAttribute("x2", x);
}

let GEOM = null;
function drawLanes() {
  const box = $("rp-lanes");
  const W = Math.max(480, box.clientWidth), LABEL = 168, TOP = 22, LANE = 22, GAP = 7, right = W - 6;
  const n = RUN.days.length, cell = (right - LABEL) / n;
  const x = i => LABEL + i * cell;
  const y = k => TOP + KINDS.indexOf(k) * (LANE + GAP);
  const H = TOP + KINDS.length * (LANE + GAP);
  GEOM = {x, y, cell, LANE, right};
  const lanes = KINDS.map(k => `<text class="lane-label" x="0" y="${y(k) + LANE / 2 + 4}">${esc(label(k))}</text>
    <rect class="lane-bg" x="${LABEL}" y="${y(k)}" width="${right - LABEL}" height="${LANE}" rx="4"/>`).join("");
  const months = RUN.days.map((d, i) => d.day.endsWith("-01")
    ? `<line class="lane-month" x1="${x(i)}" x2="${x(i)}" y1="${TOP - 6}" y2="${H - GAP}"/>
       <text class="lane-axis" x="${x(i) + 3}" y="${TOP - 9}">${new Date(`${d.day}T00:00:00`).toLocaleDateString("en-GB", {month: "short"})}</text>` : "").join("");
  const cells = RUN.days.flatMap((d, i) => d.kinds.map(k => k === "intraday_hr"
    ? `<rect class="cell event" x="${x(i) + cell / 2 - 1}" y="${y(k) + 3}" width="2.5" height="${LANE - 6}" rx="1"/>`
    : `<rect class="cell condition" x="${x(i)}" y="${y(k) + 4}" width="${Math.max(1, cell + .4)}" height="${LANE - 8}"/>`)).join("");
  box.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Detector lanes over ${n} days">
    <defs><pattern id="hidden-days" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
      <rect width="8" height="8" fill="#ebe9df"/><rect width="4" height="8" fill="#dedbcf"/></pattern></defs>
    ${lanes}${months}${cells}
    <rect id="lane-cover" x="${LABEL}" y="${TOP - 4}" width="${right - LABEL}" height="${H - TOP}" fill="url(#hidden-days)"/>
    <line id="lane-head" class="lane-head" x1="${LABEL}" x2="${LABEL}" y1="${TOP - 6}" y2="${H - GAP + 2}"/>
    <g id="lane-marks"></g></svg>`;
  drawMarkers();
}

function drawMarkers() {
  const g = GEOM, layer = $("lane-marks");
  if (!g || !layer) return;
  layer.innerHTML = RUN.reviews.map((r, i) => {
    const seen = i < NEXT || i === CURRENT?.i;
    if (!seen) return "";
    const cx = g.x(dayIndex(r.day)) + g.cell / 2, cy = g.y(r.kind) + g.LANE / 2;
    const cls = `${r.notification.notify ? "alert" : "calm"}${i === SHOWN ? " current" : ""}`;
    return `<g class="mark ${cls}" data-i="${i}" role="button" tabindex="0"
      aria-label="Review ${i + 1}: ${esc(label(r.kind))} on ${esc(r.day)}, ${r.notification.notify ? "Alert" : "no Alert"}">
      ${i === SHOWN ? `<circle class="mark-ring" cx="${cx}" cy="${cy}" r="10"/>` : ""}
      <circle class="mark-dot" cx="${cx}" cy="${cy}" r="5.5"/></g>`;
  }).join("");
}

// ---------- the chart around one signal

async function loadChart(r) {
  const at = r.kind === "intraday_hr" ? `&at=${encodeURIComponent(r.at)}` : "";
  $("rp-chart-title").textContent = `${label(r.kind)}, as the coach saw it on ${r.day}`;
  try {
    SERIES = await api(`/api/monitor-series?pid=${RUN.pid}&as_of=${r.day}&kind=${r.kind}&days=42${at}`);
    $("rp-chart-rule").textContent = `Rule: ${SERIES.rule}`;
    drawChart();
  } catch (e) {
    $("rp-chart").innerHTML = `<div class="chart-empty">Could not load the record around this signal: ${esc(e.message)}</div>`;
  }
}

function drawChart() {
  const data = SERIES, el = $("rp-chart");
  if (!data) return;
  const pts = data.points || [], vals = pts.map(p => p.value).filter(Number.isFinite);
  if (!vals.length) {
    el.innerHTML = `<div class="chart-empty">No usable ${esc(data.label)} values in this window.</div>`;
    return;
  }
  const W = Math.max(280, el.clientWidth), H = Math.max(180, el.clientHeight), L = 46, R = 20, T = 34, B = 30;
  const lo0 = Math.min(...vals, data.threshold), hi0 = Math.max(...vals, data.threshold);
  const pad = (hi0 - lo0 || 1) * .12, lo = lo0 - pad, hi = hi0 + pad;
  const x = i => L + i * (W - L - R) / Math.max(1, pts.length - 1);
  const y = v => T + (hi - v) * (H - T - B) / (hi - lo);
  const segs = [];
  let seg = [];
  pts.forEach((p, i) => {
    if (Number.isFinite(p.value)) seg.push(`${x(i).toFixed(1)},${y(p.value).toFixed(1)}`);
    else if (seg.length) { segs.push(seg); seg = []; }
  });
  if (seg.length) segs.push(seg);
  const intraday = data.scope === "intraday";
  const last = pts.length - 1, lastOk = Number.isFinite(pts[last].value);
  const fmt = v => Math.abs(v) >= 10 ? Math.round(v) : v.toFixed(1);
  const tick = i => intraday ? pts[i].date.slice(11, 16) : pts[i].date.slice(5);
  el.innerHTML = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(data.label)} up to ${esc(data.as_of)}">
    <text class="chart-axis" x="4" y="14">${esc(data.unit)}</text>
    <line class="chart-grid" x1="${L}" x2="${W - R}" y1="${y(hi0)}" y2="${y(hi0)}"/>
    <line class="chart-grid" x1="${L}" x2="${W - R}" y1="${y(lo0)}" y2="${y(lo0)}"/>
    <text class="chart-axis" x="${L - 8}" y="${y(hi0) + 4}" text-anchor="end">${fmt(hi0)}</text>
    <text class="chart-axis" x="${L - 8}" y="${y(lo0) + 4}" text-anchor="end">${fmt(lo0)}</text>
    <line class="chart-threshold" x1="${L}" x2="${W - R}" y1="${y(data.threshold)}" y2="${y(data.threshold)}"/>
    <text class="chart-label" x="${L + 6}" y="${y(data.threshold) - 6}">trigger ${data.direction === "below" ? "below" : "above"} ${fmt(data.threshold)} ${esc(data.unit)}</text>
    ${segs.map(s => `<polyline class="chart-line arrived" points="${s.join(" ")}"/>`).join("")}
    ${pts.map((p, i) => Number.isFinite(p.value) && p.firing
      ? `<circle class="chart-firing" cx="${x(i)}" cy="${y(p.value)}" r="4"><title>${esc(p.date)}: ${esc(p.raw)}</title></circle>` : "").join("")}
    ${lastOk ? `<circle class="chart-signal-ring" cx="${x(last)}" cy="${y(pts[last].value)}" r="9"/>
      <circle class="chart-signal-core" cx="${x(last)}" cy="${y(pts[last].value)}" r="4"/>
      <text class="chart-callout-text rp-callout" x="${x(last) - 14}" y="${Math.max(14, y(pts[last].value) - 16)}" text-anchor="end">${esc(pts[last].raw)}</text>` : ""}
    <text class="chart-axis" x="${L}" y="${H - 10}">${esc(tick(0))}</text>
    <text class="chart-axis" x="${W - R}" y="${H - 10}" text-anchor="end">${esc(tick(last))}</text>
  </svg>`;
}

// ---------- controls

$("rp-play").onclick = () => PLAYING ? stop() : play();
$("rp-next").onclick = nextReview;
$("rp-restart").onclick = restart;
$("pid").onchange = e => {
  history.replaceState(null, "", `?pid=${e.target.value}`);
  load(e.target.value);
};
document.querySelectorAll("[data-speed]").forEach(b => b.onclick = () => {
  SPEED = Number(b.dataset.speed);
  document.querySelectorAll("[data-speed]").forEach(o => {
    o.classList.toggle("on", o === b); o.setAttribute("aria-checked", o === b ? "true" : "false");
  });
});
const openMark = e => {
  const mark = e.target.closest(".mark");
  if (mark) jumpTo(Number(mark.dataset.i));
};
$("rp-lanes").addEventListener("click", openMark);
$("rp-lanes").addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openMark(e); } });
document.addEventListener("keydown", e => {
  if (e.target.closest("button, select, input, .mark")) return;
  if (e.key === " ") { e.preventDefault(); PLAYING ? stop() : play(); }
  if (e.key === "ArrowRight") nextReview();
});
let resizing;
addEventListener("resize", () => {
  clearTimeout(resizing);
  resizing = setTimeout(() => { if (RUN) { drawLanes(); setDay(DAY); drawChart(); } }, 150);
});

const start = new URLSearchParams(location.search).get("pid");
if (start && [...$("pid").options].some(o => o.value === start)) $("pid").value = start;
load($("pid").value).catch(e => { $("rp-steps").innerHTML = `<li class="empty">Could not load the recorded run: ${esc(e.message)}</li>`; });
