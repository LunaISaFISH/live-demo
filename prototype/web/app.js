const $ = (id) => document.getElementById(id);
const api = (p, o) => fetch(p, o).then(r => r.json());
let PID = null, CLOCK = null, TURN = 0, BUSY = false, CONTEXT_BUSY = false, MONITOR_RUN = null;

const WELCOME = `<div class="welcome">
  <span class="coach-avatar" aria-hidden="true">FC</span>
  <div><strong>I only know what happened up to the date above.</strong>
  <p>Ask how you are doing, plan your next weeks, or move time forward and let me watch.</p></div>
</div>`;

// The day each athlete opens on when no conversation is under way: late
// enough to have history, early enough that a month is still hidden.
const DEMO_DAY = "2020-03-01";

// The three representative participants the page offers; together they
// reach every workflow path (see README), each named for what it shows.
const PARTICIPANTS = ["p05", "p12", "p16"];
const PERSONA = {
  p05: "very active, injured ankle",
  p12: "sleep barely recorded",
  p16: "mostly inactive",
};

// What each mode is for: its starters, its input prompt and one line of
// orientation. The mode also picks what the context below shows.
const MODES = {
  checkin: {
    hint: "Ask how you are doing or about any past period. The coach asks what the record cannot show, then answers with its sources.",
    placeholder: "Ask about training, recovery, or your history",
    starters: ["why have I been so tired lately?", "am I doing enough exercise?",
               "compare this month's sleep with last month", "I had chest pain just now"],
  },
  plan: {
    hint: "Say what you want to train for. The coach asks what it needs, drafts a plan below, and only you can save it.",
    placeholder: "Your goal, or a change to the draft",
    starters: ["plan my next month", "what should I do tomorrow?",
               "My goal is to rebuild endurance. I have no pain, can train Mon Wed Fri, have running shoes, and dislike swimming. Plan my week.",
               "I hate swimming, never suggest it"],
  },
  monitor: {
    hint: "Advance time above. Detectors flag what changes; the coach reviews each flag and decides whether to alert you.",
    placeholder: "Ask the coach about what it flagged",
    starters: ["why was I alerted?", "how has my activity changed since November?"],
  },
};
let MODE = "checkin";

function setMode(mode) {
  MODE = mode;
  document.querySelectorAll(".modes button").forEach(b => {
    const on = b.dataset.mode === mode;
    b.classList.toggle("on", on); b.setAttribute("aria-checked", on ? "true" : "false");
  });
  $("mode-hint").textContent = MODES[mode].hint;
  if (!window.AWAITING_REPLY) $("msg").placeholder = MODES[mode].placeholder;
  renderStarters("starters", MODES[mode].starters);
  $("context-checkin").classList.toggle("hidden", mode === "monitor");
  $("context-monitor").classList.toggle("hidden", mode !== "monitor");
  if (PID) loadPlan();
  if (PID && mode === "monitor") loadMonitorSeries();
}

function setView(view) {
  document.querySelectorAll(".views button").forEach(b => {
    const on = b.dataset.view === view;
    b.classList.toggle("on", on); b.setAttribute("aria-pressed", on ? "true" : "false");
  });
  $("view-coach").classList.toggle("hidden", view !== "coach");
  $("view-evidence").classList.toggle("hidden", view !== "evidence");
}

// How each observation status is coloured and read.
const STATUS_PILL = {
  insufficient: "warn", within_baseline: "dim", meets_guideline: "ok",
  notable: "info", reduced: "warn", elevated: "bad", below_guideline: "bad",
};

// ---------------------------------------------------------------- boot
async function boot() {
  const about = await api("/api/about");
  window.ABOUT = about;
  $("tool-count").textContent = about.semantic_tools.length;
  $("library").innerHTML = about.guidelines.map(d =>
    `<li><a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.document)}</a>
     <span class="note">${esc(d.publisher)} · ${d.sections} passages</span></li>`).join("");
  $("signal-metric").innerHTML = Object.entries(about.monitor_metrics)
    .map(([kind, spec]) => `<option value="${kind}">${esc(spec.label)}</option>`).join("");
  $("signal-metric").onchange = () => loadMonitorSeries();

  const people = (await api("/api/participants")).filter(p => PARTICIPANTS.includes(p.pid));
  $("pid").innerHTML = people.map(p =>
    `<option value="${p.pid}">${p.pid} · ${esc(PERSONA[p.pid] || `${p.sessions} sessions`)}</option>`).join("");
  window.PEOPLE = Object.fromEntries(people.map(p => [p.pid, p]));
  $("pid").onchange = () => select($("pid").value);
  $("clock").onchange = () => moveClock($("clock").value);
  $("clock-back").onclick = () => shiftClock(-1);
  $("clock-next").onclick = () => shiftClock(1);
  $("clock-latest").onclick = () => moveClock(window.PEOPLE[PID].to);
  $("next-signal").onclick = advanceToNextSignal;
  document.querySelectorAll(".modes button").forEach(b => b.onclick = () => setMode(b.dataset.mode));
  // Arrow keys move along a segmented control or a tab bar, as they do natively.
  document.querySelectorAll(".modes, .tabs, .views").forEach(group => group.addEventListener("keydown", e => {
    if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    const items = [...group.querySelectorAll("button")];
    const next = items[(items.indexOf(document.activeElement) + (e.key === "ArrowRight" ? 1 : -1) + items.length) % items.length];
    if (next) { e.preventDefault(); next.focus(); next.click(); }
  }));
  document.querySelectorAll(".views button").forEach(b => b.onclick = () => setView(b.dataset.view));
  setMode("checkin");

  $("ask").onsubmit = e => { e.preventDefault(); send($("msg").value); };
  // Reset starts over; New chat starts a new conversation that keeps the
  // coach's workspace, so memory and saved plans carry into it.
  const restart = async (keep) => {
    if (BUSY || CONTEXT_BUSY) return;
    if (!keep && !confirm(`Reset ${PID}? This clears the conversation, the coach's memory and plans, ` +
                          `and the monitoring run. The audit log is archived.`)) return;
    contextControls(true);
    try {
      const response = await fetch("/api/reset", {method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(keep ? {pid: PID, keep} : {pid: PID})});
      if (!response.ok) throw new Error((await response.json()).error || "Could not reset");
      await setServerClock(CLOCK);
      clearConversation();
      await refresh();
    } catch (e) {
      $("clock-message").textContent = e.message;
    } finally {
      contextControls(false);
    }
  };
  $("reset").onclick = () => { document.querySelector(".more").open = false; restart(null); };
  $("new-chat").onclick = () => restart("workspace");
  // Wire the tabs as an ARIA tablist. Native <button> keyboard focus and
  // Enter/Space activation are untouched; we only keep role/aria-selected/
  // aria-controls correct, initialised here and maintained on click.
  document.querySelectorAll(".tabs button").forEach(b => {
    const panel = $("tab-" + b.dataset.tab);
    b.setAttribute("role", "tab");
    b.setAttribute("aria-controls", "tab-" + b.dataset.tab);
    b.setAttribute("aria-selected", b.classList.contains("on") ? "true" : "false");
    if (panel) panel.setAttribute("role", "tabpanel");
    b.onclick = () => {
      document.querySelectorAll(".tabs button").forEach(x => {
        x.classList.remove("on"); x.setAttribute("aria-selected", "false");
      });
      b.classList.add("on"); b.setAttribute("aria-selected", "true");
      // On a narrow screen the tab bar scrolls; keep the chosen tab in view.
      b.scrollIntoView({inline: "nearest", block: "nearest"});
      document.querySelectorAll(".tab").forEach(t => t.classList.add("hidden"));
      $("tab-" + b.dataset.tab).classList.remove("hidden");
    };
  });

  // The holding conditions are always one click away.
  $("conditions-pill").onclick = () => {
    setView("coach"); setMode("monitor");
    $("record").scrollIntoView({block: "start", behavior: "smooth"});
  };

  $("in-domain").innerHTML =
    ["training", "sleep", "recovery", "physiology", "lifestyle", "injury"]
      .map(d => `<option>${d}</option>`).join("");
  $("inspect-form").onsubmit = e => { e.preventDefault(); runInspect(); };

  loadEvaluation();
  // A reload comes back to the athlete the person was looking at.
  const last = localStorage.getItem("fitcoach.pid");
  select(window.PEOPLE[last] ? last : people[0].pid);
}

// An empty conversation: the chat, the activity trail and the monitor run.
function clearConversation() {
  $("log").innerHTML = WELCOME;
  $("starters").hidden = false;
  $("steps").innerHTML = '<li class="empty">Each step appears here as it happens: the workflow ' +
    'state, what the coach reads and runs, what code returns, and every check on its answer.</li>';
  MONITOR_RUN = null;
  window.AWAITING_REPLY = false;
  $("msg").placeholder = MODES[MODE].placeholder;
  $("status").textContent = "idle"; $("status").className = "status";
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

function renderStarters(id, list) {
  const el = $(id);
  el.innerHTML = list.map((s, i) => `<button data-s="${esc(s)}">${esc(s)}</button>`).join("");
  el.onclick = e => {
    const s = e.target.dataset.s;
    if (s === undefined) return;
    $("msg").value = s;
    $("msg").focus();
    $("msg").scrollIntoView({block: "center"});
  };
}

function setBusy(value) {
  BUSY = value;
  contextControls(CONTEXT_BUSY);
}

function contextControls(busy) {
  CONTEXT_BUSY = busy;
  const locked = busy || BUSY;
  for (const id of ["pid", "clock", "send", "reset", "new-chat"]) $(id).disabled = locked;
  const person = window.PEOPLE?.[PID];
  $("clock-back").disabled = locked || !person || CLOCK <= person.from || window.NO_GOING_BACK;
  $("clock-next").disabled = locked || !person || CLOCK >= person.to;
  $("clock-latest").disabled = locked || !person || CLOCK === person.to;
}

async function select(pid) {
  if (BUSY || CONTEXT_BUSY) { $("pid").value = PID; return; }
  contextControls(true);
  PID = pid;
  $("pid").value = pid;
  localStorage.setItem("fitcoach.pid", pid);
  const p = window.PEOPLE[pid];
  // A live conversation keeps its own simulation date; otherwise open on the
  // demo day, so a month is still hidden and monitoring can be replayed.
  const transcript = await api(`/api/transcript?pid=${pid}`)
    .catch(() => ({turns: [], clock: null}));
  CLOCK = transcript.clock || (p.to > DEMO_DAY ? DEMO_DAY : p.to);
  ["clock", "in-start", "in-end", "in-cstart", "in-cend"].forEach(id => {
    $(id).min = p.from; $(id).max = p.to;
  });
  $("clock").value = CLOCK;
  $("clock-value").textContent = CLOCK;
  clearConversation();
  $("drill").classList.add("hidden");
  $("inspect-out").innerHTML = "";
  try {
    await setServerClock(CLOCK);
    $("clock-message").textContent = "";
  } catch (e) {
    $("clock-message").textContent = e.message;
  }
  try {
    // Whatever the clock said, the page shows this athlete, never the last one.
    await refresh();
    restoreTranscript(transcript);
  } catch (e) {
    $("clock-message").textContent = e.message;
  } finally {
    contextControls(false);
  }
}

function refresh() {
  return Promise.all([loadProfile(), loadPlan(), loadSnapshot(), loadMemory(),
                      loadAudit(), loadSignals(), loadMonitorSeries()]);
}

async function setServerClock(day) {
  const r = await fetch("/api/clock", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({pid: PID, as_of: day})});
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || "Could not move simulation clock");
  return data;
}

async function moveClock(day, duringMonitorRun = false) {
  if ((!duringMonitorRun && BUSY) || CONTEXT_BUSY || !day || day === CLOCK) {
    $("clock").value = CLOCK;
    return;
  }
  contextControls(true);
  const previous = CLOCK;
  try {
    await setServerClock(day);
    CLOCK = day;
    $("clock").value = day;
    $("clock-value").textContent = day;
    $("clock-message").textContent = "";
    // The background monitor posts its own, more precise, boundary message.
    if (!duringMonitorRun) clockNotice(previous, day);
    await refresh();
  } catch (e) {
    $("clock").value = CLOCK;
    $("clock-message").textContent = e.message;
  } finally {
    contextControls(false);
  }
}

// Moves in a row share one notice, from where they started, instead of
// filling the chat with one line per day.
function clockNotice(previous, day) {
  const last = $("log").lastElementChild;
  const from = last?.dataset.clockFrom || previous;
  if (last?.dataset.clockFrom) last.remove();
  const gap = Math.round((new Date(day) - new Date(from)) / 86400000);
  if (!gap) return;
  const change = gap > 0
    ? (gap === 1 ? "1 more completed day is now visible." : `${gap} more completed days are now visible.`)
    : (gap === -1 ? "The last day is hidden again." : `The last ${-gap} days are hidden again.`);
  bubble("event", "simulation clock", `${from} → ${day}. ${change}`).dataset.clockFrom = from;
}

function shiftClock(days) {
  const next = new Date(`${CLOCK}T00:00:00Z`);
  next.setUTCDate(next.getUTCDate() + days);
  const value = next.toISOString().slice(0, 10);
  if (value < $("clock").min || value > $("clock").max) return;
  moveClock(value);
}

async function loadProfile() {
  const p = await api(`/api/profile?pid=${PID}&as_of=${CLOCK}`);
  const live = MONITOR_RUN && ["candidate", "reviewing"].includes(MONITOR_RUN.state) &&
    MONITOR_RUN.at && !MONITOR_RUN.at.endsWith("23:59:59") && MONITOR_RUN.day === p.as_of;
  const day = new Date(`${p.as_of}T00:00:00`);
  const long = day.toLocaleDateString("en-GB", {weekday: "long", day: "numeric", month: "long", year: "numeric"});
  $("clock-title").textContent = live ? `${long}, ${MONITOR_RUN.at.slice(11, 16)}` : long;
  $("record-title").textContent = `What the coach can see on ${day.toLocaleDateString("en-GB", {day: "numeric", month: "long"})}`;
  const span = (a, b) => Math.round((new Date(b) - new Date(a)) / 86400000) + 1;
  $("timeline-known").style.transform = `scaleX(${span(p.from, p.as_of) / span(p.from, p.dataset_to)})`;
  $("timeline-from").textContent = `Record starts ${p.from}`;
  $("timeline-to").textContent = `ends ${p.dataset_to}`;
  $("clock-summary").innerHTML = live
    ? `Reviewing a signal at <strong>${esc(MONITOR_RUN.at)}</strong>: sensor data stops there, ` +
      `daily history at the end of the day before.`
    : p.hidden_days
    ? `The coach knows everything up to 23:59 on this day. <strong>${plural(p.hidden_days, "later day")}</strong> ` +
      `${p.hidden_days === 1 ? "is" : "are"} hidden until time moves forward.`
    : `This is the last recorded day, so nothing is hidden. To replay monitoring, start a New chat and move the date back.`;
  $("record-facts").innerHTML = [
    [p.sessions, "training sessions recorded"],
    [p.nights, "nights of sleep recorded"],
    [p.reports, "self-reports on how they feel"],
  ].map(([v, k]) => `<div class="fact"><b>${esc(v)}</b><span>${k}</span></div>`).join("")
   + `<div class="fact ${p.verdict}"><b>${p.mvpa ?? "-"} <small>/ 150 min</small></b>` +
     `<span>moderate-to-vigorous activity a week (WHO: 150)</span></div>`;
  $("record-more").textContent = `records from ${p.from}, ${p.injuries} logged ${p.injuries === 1 ? "injury" : "injuries"}, ` +
    `mostly ${p.top || "unrecorded"}`;
  // During a conversation the clock only moves forward; say so on the control.
  window.NO_GOING_BACK = p.conversation_active;
  $("clock").min = p.conversation_active ? p.as_of : p.from;
  $("clock-back").title = p.conversation_active
    ? "Earlier dates need a new conversation: press New chat first" : "Previous day";
  $("clock-back").disabled = p.as_of <= p.from || p.conversation_active;
  $("clock-next").disabled = p.as_of >= p.dataset_to;
  $("clock-latest").disabled = p.as_of >= p.dataset_to;
  $("next-signal").disabled = BUSY || p.as_of >= p.dataset_to;

}

// ---------------------------------------------- semantic observations
function statusPill(status) {
  return `<span class="pill ${STATUS_PILL[status] || "dim"}">${esc(label(status))}</span>`;
}

// "data_quality.coverage" -> "data quality coverage"
function label(key) {
  return String(key ?? "").replace(/[._]/g, " ");
}

// Field names inside tool text ("median_avg_hr") read as words on the page.
function humanize(text) {
  return String(text ?? "").replace(/\b[a-z]+(?:_[a-z0-9]+)+\b/g, m => m.replace(/_/g, " "));
}

function evidenceValue(e) {
  return e.unit === "fraction" && typeof e.value === "number"
    ? `${Math.round(e.value * 100)}%` : e.value;
}

function renderObs(o) {
  const w = o.window ? `${o.window.start} → ${o.window.end}` : "";
  const dq = o.data_quality || {};
  const ev = (o.evidence || []).filter(e => typeof e.value !== "object");
  // obs-detail only carries scalars NOT already in the evidence table, so a
  // period detail field is never shown twice.
  const evNames = new Set(ev.map(e => e.name));
  const detail = o.detail && typeof o.detail === "object"
    ? Object.entries(o.detail).filter(
        ([k, v]) => typeof v !== "object" && !evNames.has(k))
    : [];
  return `<div class="obs">
    <div class="obs-head">
      <span class="obs-topic">${esc(label(o.topic))}</span>
      ${statusPill(o.status)}
      <span class="obs-window mono">${esc(w)}</span>
    </div>
    <p class="obs-summary">${esc(o.summary)}</p>
    ${o.reference ? `<p class="obs-ref">vs ${esc(o.reference)}</p>` : ""}
    ${ev.length ? `<table class="obs-ev"><tr><th>evidence</th><th>value</th>
      <th>unit</th><th>source</th></tr>` +
      ev.map(e => `<tr><td>${esc(label(e.name))}</td><td class="mono">${esc(evidenceValue(e))}</td>
        <td>${esc(e.unit === "fraction" ? "" : e.unit)}</td><td><span class="pill dim">${esc(e.source)}</span></td></tr>`).join("")
      + `</table>` : ""}
    ${detail.length ? `<div class="obs-detail">` + detail.map(([k, v]) =>
        `<span><b>${esc(v)}</b> ${esc(label(k))}</span>`).join("") + `</div>` : ""}
    <div class="obs-dq">
      data quality:${typeof dq.coverage === "number"
        ? ` coverage ${Math.round(dq.coverage * 100)}%,` : ""}
      <span class="pill ${dq.sufficiency === "sufficient" ? "ok" : "warn"}">${esc(label(dq.sufficiency))}</span>
      ${(o.limitations || []).length
        ? ` · limitations: ${o.limitations.map(x => esc(humanize(x))).join("; ")}` : ""}
    </div>
  </div>`;
}

async function loadSnapshot() {
  const s = await api(`/api/snapshot?pid=${PID}&as_of=${CLOCK}`);
  window.SNAPSHOT = s;
  $("overview-asof").textContent = `as of ${s.as_of}`;
  const findings = (s.top_findings || []).concat([s.data_quality]);
  $("overview-top").innerHTML = findings.length
    ? findings.map(renderObs).join("")
    : `<p class="note">Nothing stands out in the visible history; every domain
       is within this person's own range. Open a domain for its evidence.</p>`;
  $("overview-domains").innerHTML = Object.entries(s.domains).map(([name, d]) =>
    `<button class="chip" data-domain="${name}">
       ${esc(name)} ${statusPill(d.status)}</button>`).join("");
  $("overview-domains").querySelectorAll(".chip").forEach(b =>
    b.onclick = () => drillDown(b.dataset.domain));
}

async function drillDown(domain) {
  const back = new Date(`${CLOCK}T00:00:00Z`);
  back.setUTCDate(back.getUTCDate() - 27);
  const start = back.toISOString().slice(0, 10);
  const min = window.PEOPLE[PID].from;
  const r = await api(`/api/inspect?pid=${PID}&as_of=${CLOCK}&domain=${domain}` +
    `&start=${start < min ? min : start}&end=${CLOCK}`);
  $("drill").classList.remove("hidden");
  // The chip's status compares with this person's usual; the period below
  // only describes the same 28 days. Say which is which.
  const d = window.SNAPSHOT?.domains?.[domain] || {};
  $("drill").innerHTML = `<h3 class="sub">${esc(domain)} · last 28 days</h3>` +
    (d.status ? `<p class="note">Compared with your usual: ${statusPill(d.status)} ${esc(d.headline || "")}
       The records below describe the same days on their own, without that comparison.</p>` : "") +
    (r.error ? `<p class="note">${esc(r.error)}</p>`
             : r.observations.map(renderObs).join(""));
  $("drill").scrollIntoView({block: "nearest"});
}

async function runInspect() {
  const q = new URLSearchParams({pid: PID, as_of: CLOCK, include_statistics: "true",
    domain: $("in-domain").value, end: $("in-end").value || CLOCK});
  if (Boolean($("in-cstart").value) !== Boolean($("in-cend").value)) {
    $("inspect-out").innerHTML = `<p class="note">Fill both "Compare from" and "Compare to"
      to compare two periods, or clear both to look at one.</p>`;
    return;
  }
  if ($("in-start").value) q.set("start", $("in-start").value);
  if ($("in-cstart").value && $("in-cend").value) {
    q.set("compare_to_start", $("in-cstart").value);
    q.set("compare_to_end", $("in-cend").value);
  }
  const r = await api(`/api/inspect?${q}`);
  $("inspect-out").innerHTML = r.error
    ? `<p class="plan-error" role="alert">${esc(titleCase(r.error))}.</p>`
    : `<p class="note">${esc(r.domain)} over ${r.window.start} → ${r.window.end}` +
      (r.compare_to ? `, against ${r.compare_to.start} → ${r.compare_to.end}` : "") +
      `</p>` + r.observations.map(renderObs).join("") + comparisonTable(r.statistics?.comparison);
}

// Every metric of the two periods side by side, not only the headline one.
function comparisonTable(cmp) {
  if (!cmp || !Object.keys(cmp).length) return "";
  const show = v => v === null || v === undefined ? "Unavailable" : v;
  return `<h3 class="sub">Every measure, this period against the reference</h3>
    <div class="result-table-scroll"><table><tr><th>measure</th><th>this period</th>
      <th>reference</th><th>change</th><th>unit</th></tr>` +
    Object.entries(cmp).map(([k, v]) => `<tr><td>${esc(label(k))}</td><td>${esc(show(v.current))}</td>
      <td>${esc(show(v.reference))}</td><td>${esc(show(v.difference))}</td>
      <td>${esc(label(v.unit).replace("percentage points", "% points"))}</td></tr>`).join("") +
    `</table></div><p class="note">Change is this period minus the reference. A comparison does not establish a cause.</p>`;
}

// ------------------------------------------------------------ one turn
function bubble(cls, from, text) {
  const empty = $("log").querySelector(".empty");
  if (empty) empty.remove();
  // Starter prompts are for an empty conversation; once it starts they
  // only push the latest reply out of view.
  if (from !== "simulation clock") $("starters").hidden = true;
  const el = document.createElement("div");
  el.className = "msg " + cls;
  el.innerHTML = `<div class="from">${esc(from)}</div><div class="bubble"></div>`;
  el.querySelector(".bubble").textContent = text;
  $("log").append(el);
  $("log").scrollTop = $("log").scrollHeight;
  return el;
}

function renderDataResults(sections) {
  if (!sections?.length) return;
  const el = bubble("data-results", "Results", "");
  const box = el.querySelector(".bubble");
  const intro = document.createElement("p");
  intro.className = "results-note";
  intro.textContent = sections.every(x => x.title.startsWith("Evidence"))
    ? "The evidence behind this answer."
    : `Values from your records${sections.some(x => x.title === "Plan draft") ? " and the plan draft" : ""}. ` +
      "Missing values are shown as unavailable.";
  box.append(intro);
  for (const section of sections) {
    const details = document.createElement("details");
    const prose = /^(Evidence|Guidance)/.test(section.title);
    details.className = "result-section" + (prose ? " evidence-section" : "")
      + (section.title === "Plan draft" ? " plan-section" : "");
    details.open = !section.title.startsWith("Evidence") &&
      (sections.length <= 3 || /comparison|coverage|balance|Plan draft|Guidance/i.test(section.title));
    const heading = document.createElement("summary");
    heading.textContent = section.title;
    details.append(heading);
    if (section.context) {
      const context = document.createElement("p");
      context.className = "result-context";
      context.textContent = section.context;
      details.append(context);
    }
    const scroll = document.createElement("div");
    scroll.className = "result-table-scroll";
    scroll.tabIndex = 0;
    scroll.setAttribute("role", "region");
    scroll.setAttribute("aria-label", section.title);
    const table = document.createElement("table");
    const head = table.createTHead().insertRow();
    for (const name of section.columns) {
      const th = document.createElement("th");
      th.scope = "col";
      th.textContent = name;
      head.append(th);
    }
    const body = table.createTBody();
    for (const [i, values] of section.rows.entries()) {
      const row = body.insertRow();
      if (section.refs) row.dataset.ref = section.refs[i];
      for (const value of values) {
        const cell = row.insertCell();
        cell.textContent = value === null || value === undefined ? "Unavailable" : String(value);
      }
    }
    scroll.append(table);
    details.append(scroll);
    if (section.notes?.length) {
      const notes = document.createElement("ul");
      notes.className = "results-note";
      for (const text of section.notes) {
        const item = document.createElement("li");
        // Citations end with their URL; make it a real link.
        const [before, url] = text.split(/(https:\/\/\S+)$/);
        item.textContent = before;
        if (url) {
          const link = document.createElement("a");
          link.href = url; link.textContent = url;
          link.target = "_blank"; link.rel = "noopener";
          item.append(link);
        }
        notes.append(item);
      }
      details.append(notes);
    }
    box.append(details);
  }
}

// Who a transcript row belongs to. "agent" is the model's public output
// (messages and the tool calls it chose); "code" is the deterministic
// layer answering or judging; "workflow" is the derived state machine.
const ACTOR = {
  state: "workflow", assistant: "agent", tool: "agent", memory: "agent", file: "agent", shell: "agent",
  verdict: "agent", tool_result: "code",
  plan: "agent", denied: "code", notify: "agent", clarification: "agent", error: "server",
  compaction: "codex",
};

// A detector's name as the Monitor chart names it, not its code id.
const signalName = kind => window.ABOUT?.monitor_metrics?.[kind]?.label || kind;

function step(ev) {
  const empty = $("steps").querySelector(".empty");
  if (empty) empty.remove();
  const li = document.createElement("li");
  li.className = ev.kind;

  let summary = ev.text || ev.kind;
  let detail = null;
  if (ev.kind === "state") summary = `→ ${ev.state.replaceAll("_", " ")}`;
  else if (ev.kind === "assistant")
    summary = `"${ev.preview}${ev.chars > 200 ? "…" : ""}"`;
  else if (ev.kind === "tool") {
    if (ev.input && Object.keys(ev.input).length)
      detail = JSON.stringify(ev.input, null, 2);
  } else if (ev.kind === "tool_result") {
    summary = `↳ ${ev.name}: ${ev.error ? "ERROR " : ""}${ev.summary}`;
    if (ev.detail && ev.detail.length > (ev.summary || "").length)
      detail = ev.detail;
  } else if (ev.kind === "shell" && ev.script) {
    // The whole script the agent ran and what it printed, to check against.
    detail = ev.script + (ev.output ? `\n\n--- output ---\n${ev.output}` : "");
  }
  if (ev.error || (ev.kind === "shell" && ev.exit_code !== 0)) li.classList.add("failed");

  li.innerHTML =
    `<div class="step-line">` +
    `<span class="step-actor">${esc(ACTOR[ev.kind] || ev.kind)}</span>` +
    `<span class="step-sum"></span>` +
    `<span class="step-meta mono">${$("steps").children.length + 1}${ev.t ? " · " + esc(ev.t) : ""}</span>` +
    `</div>`;
  li.querySelector(".step-sum").textContent = summary;
  if (detail) {
    const d = document.createElement("details");
    d.innerHTML = `<summary>${ev.kind === "tool" ? "parameters" : ev.kind === "shell" ? "script and output" : "detail"}</summary><pre></pre>`;
    d.querySelector("pre").textContent = detail;
    li.append(d);
  }
  $("steps").append(li);
  $("steps").scrollTop = $("steps").scrollHeight;
}

async function send(message) {
  if (BUSY || CONTEXT_BUSY || !message.trim()) return;
  setBusy(true);
  $("send").disabled = true;
  $("msg").value = "";
  const turn = `${PID}-${Date.now()}-${++TURN}`;

  bubble("user", "you", message);
  $("steps").innerHTML = "";
  $("status").textContent = "running";
  $("status").className = "status live";

  const r = await fetch("/api/turn", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({pid: PID, message, turn, as_of: CLOCK})});

  if (!r.ok) {
    const data = await r.json();
    bubble("stop", "clock", data.error || "Could not start turn");
    setBusy(false); contextControls(false);
    return;
  }
  listen(turn);
}

// Why a turn ended, as the page names it. The code sets it; see agent.turn.
const STOP_LABEL = {answered: "complete", awaiting_user: "waiting for you",
  safety_advice: "stopped: safety advice", refused: "stopped: request declined",
  limit_reached: "stopped: limit reached", failed: "failed"};
const CITATION = /\[([0-9a-f]{8}-(?:e\d+|context|memory|plans))\]/g;

function renderReply(text, stop) {
  const title = stop === "safety_advice" ? "Safety advice - please stop and seek care"
    : stop === "refused" ? "Request declined"
    : stop === "awaiting_user" ? "Coach questions" : "Coach answer";
  const el = bubble(stop === "safety_advice" ? "stop" : "agent", title, "");
  const box = el.querySelector(".bubble");
  // Inline citations become links to the evidence row they name.
  // **bold** from the model becomes bold rather than literal asterisks.
  const put = t => t.split(/\*\*([^*\n]+)\*\*/).forEach((part, i) => {
    if (i % 2 === 0) return box.append(part);
    const strong = document.createElement("strong"); strong.textContent = part; box.append(strong);
  });
  let at = 0;
  for (const m of text.matchAll(CITATION)) {
    put(text.slice(at, m.index));
    const link = document.createElement("a");
    link.href = "#"; link.className = "cite"; link.textContent = `[${m[1].split("-")[1]}]`;
    link.title = "Show the evidence this cites";
    link.onclick = e => { e.preventDefault(); showEvidence(m[1]); };
    box.append(link);
    at = m.index + m[0].length;
  }
  put(text.slice(at));
  // bubble() scrolled while the bubble was empty. Bring the reply's first
  // line to the top of the log; a short reply simply ends the scroll.
  const log = $("log");
  log.scrollTop += el.getBoundingClientRect().top - log.getBoundingClientRect().top - 12;
}

function showEvidence(ref) {
  const rows = $("log").querySelectorAll(`tr[data-ref="${CSS.escape(ref)}"]`);
  const row = rows[rows.length - 1];
  if (!row) return;
  row.closest("details").open = true;
  row.scrollIntoView({block: "center", behavior: "smooth"});
  row.classList.remove("cited");
  void row.offsetWidth;            // restart the highlight
  row.classList.add("cited");
}

function renderAlertDecision(d) {
  bubble(d.notify ? "event" : "agent", d.notify ? "Alert" : "no Alert",
         `${d.decided_by === "coach" ? "The coach's decision" : "No review arrived, so the signal is shown"}: ` +
         `${d.reason} (verdict ${d.verdict}; severity ${d.inputs.severity}, ` +
         `${plural(d.inputs.persistence_days, "day")} running, coverage ${d.inputs.coverage}).` +
         (d.condition ? (d.recheck_in_days ? ` Looks again in ${plural(d.recheck_in_days, "day")} if it still holds.`
                                           : " Not looking again while it holds; it stays listed on the Monitor tab.") : "") +
         (d.run ? ` This run: ${plural(d.run.alerts, "Alert")}` +
           (d.run.scanned_days ? ` over ${plural(d.run.scanned_days, "scanned day")}.` : ".") : ""));
}

// The coach keeps its conversation on the server; show it again after a
// reload or an athlete switch instead of an empty panel it still remembers.
function restoreTranscript(t) {
  for (const turn of t.turns) {
    if (turn.kind === "monitor") {
      bubble("event", "monitoring event", `[${turn.signal?.at || ""}] ${signalName(turn.signal?.kind || "signal")} - handed to the coach`);
      if (turn.notification) renderAlertDecision(turn.notification);
    } else {
      bubble("user", "you", turn.message);
    }
    renderDataResults(turn.data_results);
    renderReply(turn.reply, turn.stop);
  }
  if (t.turns.length) {
    const divider = document.createElement("p");
    divider.className = "divider";
    divider.textContent = "Earlier in this conversation · the coach still remembers it";
    $("log").prepend(divider);
    window.AWAITING_REPLY = t.turns[t.turns.length - 1].stop === "awaiting_user";
    if (window.AWAITING_REPLY) $("msg").placeholder = "Answer the coach's questions";
  }
  const last = t.turns[t.turns.length - 1];
  if (last?.steps?.length) {
    $("steps").innerHTML = "";
    last.steps.forEach(step);
    step({kind: "state", state: last.stop === "answered" ? "complete" : last.stop});
    $("status").textContent = "restored";
  }
}

function listen(turn) {
  const es = new EventSource(`/api/stream?turn=${encodeURIComponent(turn)}`);
  window.CURRENT = turn;
  window.AWAITING_REPLY = false;
  window.TURN_FAILED = false;
  window.STOP = null;
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.kind === "reply") {
      window.AWAITING_REPLY = ev.stop === "awaiting_user";
      window.STOP = ev.stop;
      renderDataResults(ev.data_results);
      renderReply(ev.text, ev.stop);
      $("msg").placeholder = window.AWAITING_REPLY ? "Answer the coach's questions" : MODES[MODE].placeholder;
      return;
    }
    if (ev.kind === "notify") {
      if (MONITOR_RUN && MONITOR_RUN.state === "reviewing") {
        MONITOR_RUN = {...MONITOR_RUN, state: "decided",
          verdict: ev.verdict, notify: ev.notify, reason: ev.reason};
        renderMonitorRun();
      }
      renderAlertDecision(ev);
      step(ev);
      return;
    }
    if (ev.kind === "error") { window.TURN_FAILED = true; bubble("stop", "error", ev.text); return; }
    if (ev.kind === "done") {
      es.close(); setBusy(false); contextControls(true);
      $("status").textContent = window.TURN_FAILED ? "failed"
        : STOP_LABEL[window.STOP] || (window.AWAITING_REPLY ? "waiting for you" : "complete");
      $("status").className = "status";
      refresh().catch(e => { $("clock-message").textContent = e.message; })
        .finally(() => contextControls(false));
      return;
    }
    step(ev);
  };
  es.onerror = () => {
    es.close(); setBusy(false); contextControls(false);
    $("status").textContent = ""; $("status").className = "status";
  };
}

// ------------------------------------------------- the plan panel
// The coach writes its plan draft to its workspace; the page shows it next
// to the saved plan. Saving is the one human commit: the server makes the
// draft the next saved version. Changes are asked for in the chat.
const titleCase = v => String(v ?? "").replace(/^./, c => c.toUpperCase());
// "2020-03-23" -> "Mon 23 Mar"; the panel's heading carries the year.
const weekday = date => new Date(`${date}T00:00:00Z`).toUTCString().slice(0, 11).replace(",", "");
const describeDay = d => d.activity === "rest" ? "Rest"
  : `${titleCase(d.activity)}, ${d.minutes} min, ${d.intensity}`;

function planTable(rows) {
  // rows: [{day, status, before}] - status is added, changed, removed, same or "".
  // HTML: the old value stays on one line, so a wrap falls after "changed from".
  const change = r => r.status === "changed" ? `changed from <span class="nowrap">${esc(describeDay(r.before))}</span>`
    : r.status === "added" ? "new" : r.status === "removed" ? "dropped" : "";
  const withChanges = rows.some(r => change(r));
  // A change is noted under the activity, so the table keeps four columns.
  return `<div class="result-table-scroll"><table class="plan-table"><thead><tr><th>Day</th>
      <th>Activity</th><th>Min</th><th>Intensity</th></tr></thead><tbody>` +
    rows.map(r => `<tr class="${r.status}${r.day.activity === "rest" ? " rest" : ""}"><td class="plan-day" title="${esc(r.day.date)}">${weekday(r.day.date)}</td>
      <td>${esc(titleCase(r.day.activity))}${change(r) ? `<span class="plan-change">${change(r)}</span>` : ""}</td><td>${esc(r.day.minutes)}</td><td>${esc(r.day.intensity)}</td></tr>`).join("") +
    `</tbody></table></div>`;
}

async function loadPlan() {
  const w = await api(`/api/plan?pid=${PID}&as_of=${CLOCK}`);
  const panel = $("plan-panel");
  // In Plan mode the panel is always there, so the person sees where the
  // draft will land; elsewhere it appears once there is a plan.
  panel.classList.toggle("hidden", !w.draft && !w.active && MODE !== "plan");
  if (!w.draft && !w.active) {
    panel.innerHTML = MODE === "plan" ? `<div class="plan-head"><h2>Your plan</h2>
      <p class="plan-sub">No plan yet. Tell the coach your goal; its draft appears here, and only you can save it.</p></div>` : "";
    return;
  }
  const saved = w.active;
  const history = w.versions.length
    ? `<details class="plan-history"><summary>Saved versions (${w.versions.length})</summary>
         <table><thead><tr><th>Version</th><th>Saved on</th><th>Days</th></tr></thead><tbody>` +
      w.versions.slice().reverse().map(v => `<tr><td>${v.version}${v.version === saved.version ? " · current" : ""}</td>
         <td class="mono">${esc(v.saved_on)}</td><td>${v.days}</td></tr>`).join("") +
      `</tbody></table></details>` : "";
  const layout = (title, rows, side) =>
    `<div class="plan-head"><h2>${title}</h2><p class="plan-sub"></p></div>
     <div class="plan-body"><div class="plan-main">${planTable(rows)}</div>
     <aside class="plan-side">${side}${history}</aside></div>`;
  if (!w.draft) {
    panel.innerHTML = layout("Your plan", saved.days.map(day => ({day, status: ""})),
      `<p class="plan-hint">To change it, ask the coach in the chat. Its draft appears here for you to save.</p>`);
    panel.querySelector(".plan-sub").textContent = `Version ${saved.version}, saved on ${saved.saved_on}.`;
    return;
  }
  // Only the saved days from the draft's first date on are replaced; a draft
  // that starts after the saved plan ends continues it rather than changing it.
  const overlaps = w.draft_vs_active.some(r => r.before);
  const rows = w.draft_vs_active.map(r => ({day: r.after || r.before,
    status: overlaps ? r.status : "", before: r.before}));
  const count = status => rows.filter(r => r.status === status).length;
  panel.innerHTML = layout("Draft from your coach", rows,
    `<p class="plan-stale hidden"></p>
     <div class="plan-actions"><button class="primary" data-plan="save" type="button">Save as my plan</button>
     <button class="ghost" data-plan="discard" type="button">Discard draft</button></div>
     <p class="plan-hint">Not saved yet. To change it, tell the coach in the chat; the draft updates here.</p>
     <p class="plan-error" role="alert"></p>`);
  const last = saved && saved.days.map(d => d.date).sort().pop();
  panel.querySelector(".plan-sub").textContent = !saved ? "You have no saved plan yet."
    : !overlaps ? `Follows your saved plan (version ${saved.version}, through ${last}).`
    : `Replaces your saved plan (version ${saved.version}) from ${rows[0].day.date}: ` +
      [["changed", "changed"], ["added", "new"], ["removed", "dropped"]]
        .map(([status, word]) => count(status) ? `${count(status)} ${word}` : "").filter(Boolean)
        .join(", ").replace(/^$/, "no day changes") + ".";
  if (w.draft_stale) {
    const stale = panel.querySelector(".plan-stale");
    stale.classList.remove("hidden");
    stale.textContent = `This draft starts on ${w.draft.days.map(d => d.date).sort()[0]}, which is no longer in the future ` +
      `(today is ${CLOCK}), so it cannot be saved as it is. Ask the coach to update it.`;
    panel.querySelector('[data-plan="save"]').disabled = true;
  }
  panel.querySelectorAll("[data-plan]").forEach(button => button.onclick = () => decidePlan(button.dataset.plan));
}

async function decidePlan(decision) {
  if (BUSY || CONTEXT_BUSY) return;
  if (decision === "discard" && !confirm("Discard this draft? Your saved plan stays as it is.")) return;
  const panel = $("plan-panel");
  panel.querySelectorAll("[data-plan]").forEach(b => b.disabled = true);
  const r = await fetch("/api/plan", {method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify({pid: PID, decision, as_of: CLOCK})});
  const data = await r.json();
  if (!r.ok) {
    panel.querySelector(".plan-error").textContent = data.error || "Could not update the plan";
    panel.querySelectorAll("[data-plan]").forEach(b => b.disabled = false);
    return;
  }
  bubble("event", "your plan", decision === "save"
    ? `Saved as version ${data.version.version} of your plan (${plural(data.version.days.length, "day")}).`
    : "Draft discarded; your saved plan is unchanged.");
  await refresh();
}

// ------------------------------------------------------------- panels
async function loadMemory() {
  const notes = await api(`/api/memory?pid=${PID}&as_of=${CLOCK}`);
  $("memory").innerHTML = notes.length
    ? `<table><tr><th>when</th><th>kind</th><th>what the coach keeps</th><th>in their words</th></tr>` +
      notes.map(n => `<tr><td class="mono">${esc(n.when)}</td>` +
        `<td><span class="pill dim">${esc(label(n.kind))}</span></td>` +
        `<td>${esc(n.text)}</td><td class="quote">"${esc(n.quote)}"</td></tr>`).join("") + `</table>`
    : `<p class="note">Nothing yet. Tell the coach something the record
       cannot know - a preference, a goal, an injury that feels different.</p>`;
}

async function loadAudit() {
  const [rows, v] = await Promise.all([
    api(`/api/audit?pid=${PID}`), api(`/api/audit/verify?pid=${PID}`)]);
  $("verify").innerHTML = v.entries === 0
    ? `<span class="pill dim">no entries yet</span>`
    : (v.problems.length
        ? `<span class="pill bad">${plural(v.problems.length, "problem")}</span> ` +
          `<span class="mono">${esc(v.problems.join("; "))}</span>`
        : `<span class="pill ok">${v.entries === 1 ? "1 entry" : `${v.entries} entries`}, hash chain intact</span>
           <span class="note">six fields present, timestamps monotonic, each entry
           sealed with the hash of the one before, so an edited or removed
           entry is detected</span>`);
  $("audit").innerHTML = rows.length
    ? `<table class="audit-table"><tr><th>timestamp</th><th>input</th><th>source</th>
       <th>action</th><th>output</th><th>human decision</th></tr>` +
      rows.slice().reverse().map(r => `<tr>
        <td class="mono">${esc(r.timestamp.slice(0, 19).replace("T", " "))}<br>
          <span class="pill dim">${esc(r.input_kind)}</span>${r.status === "failed" ? ` <span class="pill bad">failed</span>` : ""}</td>
        <td title="${esc(r.input)}">${esc(clip(r.input, 90))}</td>
        <td class="mono">${(r.source || []).map(esc).join("<br>") || "-"}</td>
        <td class="mono">${(r.action || []).map(esc).join("<br>") || "-"}</td>
        <td title="${esc(shortCitations(r.output))}">${esc(clip(shortCitations(r.output), 160))}</td>
        <td>${decisionPill(r.human_decision)}${r.note ? `<br><span class="note">${esc(r.note)}</span>` : ""}</td>
      </tr>`).join("") + `</table>`
    : `<p class="note">No turns logged yet.</p>`;
}

// Cut at a word, and say so.
function clip(text, n) {
  const t = String(text ?? "");
  return t.length <= n ? t : t.slice(0, n).replace(/\s+\S*$/, "") + "…";
}

// "[8249fc88-e16]" -> "[e16]", as citations read in the chat.
const shortCitations = text => String(text ?? "").replace(CITATION, (_, ref) => `[${ref.split("-")[1]}]`);

function decisionPill(d) {
  const cls = {plan_saved: "ok", draft_discarded: "warn"}[d] || "dim";
  return `<span class="pill ${cls}">${esc(d)}</span>`;
}

async function loadMonitorSeries() {
  const kind = $("signal-metric").value || "resting_hr";
  try {
    const at = MONITOR_RUN?.kind === kind && MONITOR_RUN.at
      ? `&at=${encodeURIComponent(MONITOR_RUN.at)}` : "";
    const data = await api(`/api/monitor-series?pid=${PID}&as_of=${CLOCK}&kind=${kind}&days=42${at}`);
    renderMonitorChart(data);
  } catch (e) {
    $("signal-chart").innerHTML = `<p class="note">Could not load the signal timeline.</p>`;
  }
}

function renderMonitorChart(data) {
  const points = data.points || [];
  const valid = points.filter(p => Number.isFinite(p.value));
  const latest = valid.at(-1);
  $("signal-chart-summary").innerHTML =
    `<span><strong>${esc(data.label)}</strong> · ${esc(data.unit)}</span>` +
    `<span>Rule: ${esc(data.rule)}</span>` +
    `<span class="mono">${esc(data.window?.start || "")} -> ${esc(data.window?.end || data.as_of || "")}</span>` +
    (latest ? `<span>Latest recorded: <strong>${esc(latest.raw)}</strong></span>` : "") +
    (data.scope === "intraday"
      ? `<span>No line means no heart-rate sample passed the confidence filter.</span>` : "");
  if (!valid.length) {
    $("signal-chart").innerHTML = `<div class="chart-empty">No usable ${esc(data.label || "metric")} values in this visible window.</div>`;
    return;
  }

  // Drawn at the width it is shown, so its 11px labels stay 11px in a narrow column.
  const W = Math.max(360, $("signal-chart").clientWidth || 840), H = 270, L = 58, R = 22, T = 30, B = 42;
  const plotW = W - L - R, plotH = H - T - B;
  const values = valid.map(p => Number(p.value));
  const intraday = data.scope === "intraday";
  const rawMax = intraday
    ? Math.max(Number(data.threshold), ...values) + 12
    : Math.max(Number(data.ceiling) || 0, Number(data.threshold) || 0, ...values, 1);
  const rawMin = intraday
    ? Math.max(0, Math.min(Number(data.threshold), ...values) - 12)
    : Math.min(0, ...values);
  // Round the axis outward to a 1-2-5 step so the ticks read as whole numbers.
  const magnitude = 10 ** Math.floor(Math.log10((rawMax - rawMin) / 2 || 1));
  const step = [1, 2, 5, 10].map(m => m * magnitude).find(v => (rawMax - rawMin) / v <= 5) || 10 * magnitude;
  const max = Math.ceil(rawMax / step) * step;
  const min = Math.floor(rawMin / step) * step;
  const span = max - min || 1;
  const x = i => L + (points.length === 1 ? plotW / 2 : i * plotW / (points.length - 1));
  const y = v => T + (max - v) * plotH / span;
  const thresholdY = y(Number(data.threshold));
  const target = MONITOR_RUN && MONITOR_RUN.kind === data.kind
    ? (MONITOR_RUN.at || MONITOR_RUN.day) : null;
  let targetIndex = -1;
  if (target) points.forEach((p, i) => { if (p.date <= target) targetIndex = i; });
  const fromIndex = MONITOR_RUN?.from ? points.findIndex(p => p.date >= MONITOR_RUN.from) : -1;

  const segments = [];
  let segment = [];
  points.forEach((p, i) => {
    if (Number.isFinite(p.value)) segment.push(`${x(i).toFixed(1)},${y(p.value).toFixed(1)}`);
    else if (segment.length) { segments.push(segment); segment = []; }
  });
  if (segment.length) segments.push(segment);

  const ticks = Array.from({length: Math.round(span / step) + 1}, (_, i) => {
    const value = +(min + i * step).toFixed(6);
    const yy = y(value);
    const label = Number.isInteger(value) ? value : value.toFixed(1);
    return `<line class="chart-grid" x1="${L}" y1="${yy}" x2="${W-R}" y2="${yy}"/>
      <text class="chart-axis" x="${L-9}" y="${yy+4}" text-anchor="end">${label}</text>`;
  }).join("");
  const triggerZone = data.direction === "below"
    ? `<rect class="chart-trigger-zone" x="${L}" y="${thresholdY}" width="${plotW}" height="${T+plotH-thresholdY}"/>`
    : `<rect class="chart-trigger-zone" x="${L}" y="${T}" width="${plotW}" height="${Math.max(0, thresholdY-T)}"/>`;
  const replayZone = targetIndex >= 0 && fromIndex >= 0
    ? `<rect class="chart-replay-zone" x="${x(fromIndex)}" y="${T}" width="${Math.max(2, x(targetIndex)-x(fromIndex))}" height="${plotH}"/>`
    : "";
  const lines = segments.map(s => `<polyline class="chart-line${targetIndex >= 0 ? " arrived" : ""}" points="${s.join(" ")}"/>`).join("");
  const dots = points.map((p, i) => Number.isFinite(p.value)
    ? `<circle class="${p.firing ? "chart-firing" : "chart-point"}" cx="${x(i)}" cy="${y(p.value)}" r="${p.firing ? 4.5 : 2.5}"><title>${esc(p.date)}: ${esc(p.raw)}${p.firing ? " · detector active" : ""}</title></circle>`
    : "").join("");
  const signalPoint = targetIndex >= 0 && Number.isFinite(points[targetIndex].value)
    ? `<line class="chart-signal-guide" x1="${x(targetIndex)}" y1="${T}" x2="${x(targetIndex)}" y2="${T+plotH}"/>
       <circle class="chart-signal-ring" cx="${x(targetIndex)}" cy="${y(points[targetIndex].value)}" r="9"/>
       <circle class="chart-signal-core" cx="${x(targetIndex)}" cy="${y(points[targetIndex].value)}" r="4"/>
       <rect class="chart-callout" x="${Math.min(W-154, Math.max(L, x(targetIndex)-62))}" y="5" width="145" height="24" rx="6"/>
       <text class="chart-callout-text" x="${Math.min(W-144, Math.max(L+10, x(targetIndex)-52))}" y="21">SIGNAL · ${esc(target.slice(11, 16) || target)}</text>`
    : "";
  const labelIndexes = [...new Set([0, Math.floor((points.length - 1) / 2), points.length - 1])];
  const dates = labelIndexes.map(i => `<text class="chart-axis" x="${x(i)}" y="${H-14}" text-anchor="${i === 0 ? "start" : i === points.length-1 ? "end" : "middle"}">${esc(intraday ? points[i].date.slice(5, 16) : points[i].date.slice(5))}</text>`).join("");
  const thresholdLabel = `${data.direction === "below" ? "below" : "above"} ${data.threshold}`;

  const chart = $("signal-chart");
  chart.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(data.label)} from ${esc(data.window.start)} through ${esc(data.as_of)}">
    <title>${esc(data.label)} monitoring timeline</title>
    ${triggerZone}${replayZone}${ticks}
    <line class="chart-threshold" x1="${L}" y1="${thresholdY}" x2="${W-R}" y2="${thresholdY}"/>
    <text class="chart-label" x="${L+8}" y="${thresholdY-7}" text-anchor="start">trigger ${esc(thresholdLabel)}</text>
    ${lines}${dots}${signalPoint}${dates}
  </svg>`;
  if (targetIndex >= 0) requestAnimationFrame(() => { chart.scrollLeft = chart.scrollWidth; });
}

async function loadSignals() {
  const data = await api(`/api/signals?pid=${PID}&as_of=${CLOCK}`);
  // The passive layer: every condition holding today stays in view, with its
  // history and what the coach last made of it. Alerts are the coach's call.
  const pill = $("conditions-pill");
  pill.classList.toggle("hidden", !data.conditions.length);
  pill.textContent = `${plural(data.conditions.length, "condition")} holding`;
  const last = c => c.reviews[c.reviews.length - 1];
  const conditions = data.conditions.length === 0
    ? `<p class="note">No condition is holding on ${esc(data.as_of)}.</p>`
    : data.conditions.map(c => {
        const r = last(c);
        return `<div class="sig condition">
          <div class="sig-head">
            <div><span class="pill warn">${esc(signalName(c.kind))}</span>
              <span class="note inline nowrap">since ${esc(c.started)} · ${plural(c.days_running, "day")}</span></div>
            ${r ? `<span class="pill ${r.alert ? "bad" : "ok"}">${r.alert ? "Alert raised" : "no Alert"} ${esc(r.day)}</span>`
                : `<span class="pill dim">not reviewed yet</span>`}
          </div>
          <p>Now: ${esc(c.now)}</p>
          ${c.at_start !== c.now ? `<p>When it started: ${esc(c.at_start)}</p>` : ""}
          ${r ? `<p class="coach-take">Coach: ${esc(r.reason)}${c.next_review
                ? ` Looks again on ${esc(c.next_review)} if it still holds.` : ""}</p>` : ""}
        </div>`; }).join("");
  const events = data.events.length === 0
    ? `<p class="note">No one-off events in the 28 days visible through ${esc(data.as_of)}.</p>`
    : data.events.map(e => {
        const current = MONITOR_RUN && MONITOR_RUN.kind === e.kind && MONITOR_RUN.at === e.at;
        const reviewing = current && ["reviewing", "decided"].includes(MONITOR_RUN.state);
        const done = (data.reviewed || {})[e.key];
        const action = done
          ? `<span class="pill ${done.notify ? "bad" : "ok"}">${done.notify ? "Alert raised"
              : "no Alert"} · ${esc(done.verdict)}</span>`
          : `<button data-day="${e.at.slice(0, 10)}" data-kind="${e.kind}" data-at="${esc(e.at)}"
                    aria-label="Review the ${esc(signalName(e.kind).toLowerCase())} event at ${esc(e.at)} with the agent"
                    class="review" ${reviewing ? "disabled" : ""}>${reviewing ?
                      (MONITOR_RUN.state === "decided" ? "Reviewed" : "Reviewing...") :
                      "Review with the agent"}</button>`;
        return `<div class="sig${current ? " current" : ""}">
          <div class="sig-head">
            <div><span class="sig-day">${esc(e.at)}</span>
              <span class="pill warn">${esc(signalName(e.kind))}</span></div>
            ${action}
          </div>
          <p>${esc(e.summary)}</p>
        </div>`; }).join("");
  $("signals").innerHTML =
    `<p class="note">Detectors flag a pattern; the coach decides whether it is worth an Alert and when to
      look again. An Alert is a prompt to pay attention, not a diagnosis.${data.run.start_day
        ? ` Since ${esc(data.run.start_day)}: ${plural(data.run.alerts, "Alert")}.` : ""}</p>
     <h3 class="sub">Conditions holding</h3>${conditions}
     <h3 class="sub">One-off events, last 28 days</h3>${events}`;
  $("signals").querySelectorAll(".review").forEach(b => b.onclick = () => {
    if (BUSY) return;
    reviewSignal(b.dataset.day, b.dataset.kind, b.dataset.at || null);
  });
  renderMonitorRun();
}

// Why the simulator stopped for the coach.
const DUE_LABEL = {event: "new event", new: "new condition", recheck: "look again"};

function renderMonitorRun() {
  const el = $("monitor-run");
  if (!MONITOR_RUN) { el.innerHTML = ""; return; }
  const r = MONITOR_RUN;
  if (r.state === "advancing") {
    el.innerHTML = `<p class="run-title"><span class="pill info">advancing</span> Scanning after ${esc(r.from)}</p>
      <p class="run-meta">Each day is opened, checked, and discarded before the next day.</p>`;
  } else if (r.state === "done") {
    el.innerHTML = `<p class="run-title"><span class="pill dim">nothing due</span> Reached ${esc(r.day)}</p>
      <p class="run-meta">Advanced ${plural(r.advanced_days, "day")}. Nothing came up for the coach to review.</p>`;
  } else if (r.state === "error") {
    el.innerHTML = `<p class="run-title"><span class="pill bad">stopped</span> ${esc(r.error)}</p>`;
  } else if (r.state === "decided") {
    el.innerHTML = `<p class="run-title"><span class="pill ${r.notify ? "bad" : "ok"}">${r.notify ? "ALERT" : "NO ALERT"}</span> Coach's verdict: ${esc(r.verdict)}</p>
      <p class="run-meta">${esc(r.at || r.day)} · ${esc(signalName(r.kind))} · ${esc(r.reason)}</p>`;
  } else {
    el.innerHTML = `<p class="run-title"><span class="pill warn">${esc(DUE_LABEL[r.due] || "candidate")}</span> ${esc(signalName(r.kind))} at ${esc(r.at || r.day)}</p>
      <p class="run-meta">Advanced ${plural(r.advanced_days, "day")}. ${r.state === "reviewing" ?
        "The Agent is inspecting history bounded to this date." : esc(r.summary || "")}</p>`;
  }
}

async function advanceToNextSignal() {
  if (BUSY) return;
  setMode("monitor");
  setBusy(true); $("send").disabled = true; $("next-signal").disabled = true;
  MONITOR_RUN = {state: "advancing", from: CLOCK};
  renderMonitorRun();
  $("status").textContent = "advancing time"; $("status").className = "status live";
  try {
    const res = await fetch(`/api/next-signal?pid=${PID}&as_of=${CLOCK}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Could not advance the monitor");
    if (data.done) {
      MONITOR_RUN = {...data, state: "done"};
      await moveClock(data.day, true);
      renderMonitorRun();
      setBusy(false); $("send").disabled = false;
      $("status").textContent = window.AWAITING_REPLY ? "waiting for you" : "idle";
      $("status").className = "status";
      await loadProfile();
      return;
    }
    const signal = data.signals[0];
    MONITOR_RUN = {...data, ...signal, state: "candidate"};
    $("signal-metric").value = signal.kind;
    await moveClock(data.day, true);
    bubble("event", "background monitor",
      `${data.from} -> ${signal.at || data.day}. ${DUE_LABEL[signal.due]}: ${signalName(signal.kind)}; handing it to the Agent.`);
    setBusy(false);
    await reviewSignal(data.day, signal.kind, signal.at);
  } catch (e) {
    MONITOR_RUN = {state: "error", error: e.message};
    renderMonitorRun();
    setBusy(false); $("send").disabled = false;
    $("status").textContent = "idle"; $("status").className = "status";
    await loadProfile();
  }
}

async function reviewSignal(day, kind, at = null) {
  setBusy(true); $("send").disabled = true;
  $("next-signal").disabled = true;
  $("signal-metric").value = kind;
  if (!MONITOR_RUN || MONITOR_RUN.day !== day || MONITOR_RUN.kind !== kind) {
    MONITOR_RUN = {state: "reviewing", day, kind, at, advanced_days: 0};
  } else {
    MONITOR_RUN.state = "reviewing";
  }
  renderMonitorRun();
  $("signals").querySelectorAll(".review").forEach(b => {
    b.disabled = true;
    if (b.dataset.day === day && b.dataset.kind === kind) {
      b.textContent = "Reviewing...";
      b.closest(".sig").classList.add("current");
    }
  });
  await loadMonitorSeries();
  const turn = `${PID}-sig-${Date.now()}-${++TURN}`;
  bubble("event", "monitoring event", `[${at || day}] ${signalName(kind)} - handed to the coach`);
  $("steps").innerHTML = "";
  $("status").textContent = "reviewing"; $("status").className = "status live";
  const response = await fetch("/api/signal", {method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({pid: PID, day, kind, turn, as_of: CLOCK})});
  const data = await response.json();
  if (!response.ok) {
    MONITOR_RUN = {state: "error", error: data.error || "Signal review failed"};
    renderMonitorRun();
    setBusy(false); $("send").disabled = false;
    $("status").textContent = "idle"; $("status").className = "status";
    await loadProfile();
    return;
  }
  listen(turn);
}

async function runTests() {
  const btn = $("run-tests");
  btn.disabled = true;
  $("test-summary").textContent = "running all offline suites (about 20 seconds)...";
  $("test-results").innerHTML = "";
  try {
    const r = await api("/api/tests");
    $("test-summary").innerHTML =
      `<span class="pill ${r.exit === 0 ? "ok" : "bad"}">${esc(r.summary)}</span>`;
    $("test-results").innerHTML = r.suites.map(x => `
      <details class="tsuite"${x.failed ? " open" : ""}>
        <summary><span class="pill ${x.failed || x.exit ? "bad" : "ok"}">${x.passed} passed${x.failed ? `, ${x.failed} failed` : ""}</span>
          <code>${esc(x.file)}</code> <span class="tdesc">${esc(x.title)}</span></summary>` +
      x.groups.map(g => `<div class="tgroup"><h3>${esc(g.name)}</h3>` +
        g.checks.map(c => `<div class="tcheck ${c.ok ? "ok" : "no"}">${esc(c.name)}</div>`).join("") +
      `</div>`).join("") + `</details>`).join("");
  } catch (e) {
    $("test-summary").textContent = "could not run: " + e;
  }
  btn.disabled = false;
}

// The latest live-model run (e2e.py): what was sent, what happened, and
// the errors the code caught on the way. Written only when the run passed.
// e2e.py section number -> the workflow it exercises, for the case table.
const WORKFLOW_OF = {2: "Check in", 3: "Memory", 4: "Build a plan", 5: "Saving the plan",
                     6: "Safety (symptom, injection)", 7: "Monitor"};

async function loadLiveEvidence() {
  const ev = await api("/api/evidence");
  if (!ev.summary) {
    $("live-evidence").innerHTML = `<p class="note">No live run recorded yet. Run
      <code>e2e.py</code> against a running server to produce one.</p>`;
    return;
  }
  const when = new Date(ev.summary.generated_at_epoch * 1000).toISOString().slice(0, 16).replace("T", " ");
  const outcome = c => c.draft ? `plan draft ${c.draft}`
    : c.alert !== null ? (c.alert ? "Alert raised" : "no Alert")
    : c.stop === "awaiting_user" ? "asked for missing context"
    : c.outcome === "accepted" ? "answered" : (c.outcome || "-");
  const bySection = {};
  for (const c of ev.checks) (bySection[c.section] ||= []).push(c);
  $("live-evidence").innerHTML = `
    <p><span class="pill ${ev.summary.failed ? "bad" : "ok"}">${ev.summary.passed} passed, ${ev.summary.failed} failed</span>
      <span class="note">live model, participant ${esc(ev.summary.participant)},
      simulation date ${esc(ev.summary.simulation_date)}, run ${esc(when)} UTC</span></p>
    <h3 class="sub">Test cases sent to the live Agent</h3>
    <div class="result-table-scroll"><table class="cases">
      <tr><th>workflow</th><th>input</th><th>what happened</th><th>reply (start)</th></tr>
      ${ev.cases.map(c => `<tr><td>${esc(WORKFLOW_OF[c.section.match(/^\[(\d+)\]/)?.[1]] || c.section)}</td>
        <td>${esc(c.input)}</td><td>${esc(outcome(c))}</td>
        <td>${esc(clip(shortCitations(c.reply), 160))}</td></tr>`).join("")}
    </table></div>
    <h3 class="sub">Errors the code caught during the run</h3>
    ${ev.errors.length ? `<div class="result-table-scroll"><table class="cases">
      <tr><th>input</th><th>tool</th><th>what was rejected</th></tr>
      ${ev.errors.map(e => `<tr><td>${esc(e.input)}</td><td><code>${esc(e.tool)}</code></td><td>${esc(e.caught)}</td></tr>`).join("")}
    </table></div>` : `<p class="note">None recorded.</p>`}
    <h3 class="sub">Every check</h3>
    ${Object.entries(bySection).map(([name, list]) => `<details class="tsuite">
      <summary><span class="pill ${list.every(c => c.passed) ? "ok" : "bad"}">${list.filter(c => c.passed).length}/${list.length}</span> ${esc(name)}</summary>
      ${list.map(c => `<div class="tcheck ${c.passed ? "ok" : "no"}">${esc(c.name)}</div>`).join("")}</details>`).join("")}`;
}

function loadEvaluation() {
  $("run-tests").onclick = runTests;
  loadLiveEvidence().catch(() => {
    $("live-evidence").innerHTML = `<p class="note">Could not load the live run.</p>`; });
  $("evaluation").innerHTML = `
  <details class="tsuite"><summary><strong>Where to see each part of the system</strong></summary>
  <table>
    <tr><th>part</th><th>where to see it</th></tr>
    <tr><td>User input</td><td>The Coach conversation, with its Check in, Plan
      and Monitor modes and their starter prompts; monitoring reviews appear in
      it as labelled events</td></tr>
    <tr><td>Agent workflow</td><td>"How the answer is built", beside the
      conversation: workflow states, the coach's public messages, the shell
      commands and tool calls it made with what they returned, and any final
      answer returned for correction</td></tr>
    <tr><td>Grounding retrieval</td><td>Two sources. Records:
      <code>read_record</code> and <code>inspect_sensor_window</code>, every
      call stamped with its effective simulation date and shown as result
      tables under the answer, with the evidence each citation names. Documents: <code>search_guidelines</code>
      retrieves cited passages from the guideline library (Evidence, Sources),
      shown as "Guidance retrieved" with source and URL</td></tr>
    <tr><td>Human checkpoint</td><td>The plan panel: the Agent's draft, marked
      against the saved plan. <em>Save as my plan</em> makes it the next saved
      version; <em>Discard draft</em> drops it; changes are asked for in the
      chat and the draft updates in place. Only the person saves a plan</td></tr>
    <tr><td>Safety response</td><td>Before a plan the Agent reads the record and
      asks what it cannot show: goal, availability, pain and conditions, how
      the person has been lately, equipment and preferences; in monitoring the
      coach reviews each flagged signal and raises an Alert when the person
      should know, with care advice after an unexplained heart-rate rise; a
      described warning symptom gets care advice in that reply;
      rule-breaking requests are refused and cannot pass the tool allow-list</td></tr>
    <tr><td>Audit log</td><td>Evidence, Audit trail: six fields per entry, append-only,
      memory and draft changes in each turn's actions, save and discard
      decisions as their own entries, structurally verified</td></tr>
    <tr><td>Evaluation artefacts</td><td>This tab: all eight offline suites run
      on demand, and the latest live-model run with every test case sent
      (positive and negative), what happened, the errors the code caught, and
      every check. Files: <code>evidence/e2e-*.json</code></td></tr>
  </table></details>
  <p class="note">The suite asserts provenance rather than plausibility: an
   answer is judged by whether every part traces to this person's record, not
   by whether it reads well. Run <code>test_agent.py</code> for the offline
   half and <code>test_agent.py --live</code> for a real conversation.</p>
  <details class="tsuite"><summary><strong>What the offline groups establish</strong></summary>
  <table>
    <tr><th>group</th><th>what it establishes</th></tr>
    <tr><td>no future leakage</td><td>Every record read asked as of day N
      equals one from a record that ends at N - the agent's record read, the
      page's overview and period comparison, and the planning context</td></tr>
    <tr><td>one observation contract</td><td>Every observation has the same
      shape, every evidence item carries a unit and provenance, and
      coverage/sufficiency are always present</td></tr>
    <tr><td>missing is insufficient</td><td>A thin window returns
      <code>insufficient</code>, never a reassuring <code>within_baseline</code></td></tr>
    <tr><td>period comparison</td><td>A comparison computes both windows over
      exactly the dates requested, not a cumulative as_of dressed up as a period</td></tr>
    <tr><td>Agent-owned planning</td><td>The Agent receives decision-ready
      history, asks what the record cannot show, writes the schedule to its
      draft and cites the records behind it; code checks every citation is real
      and the draft's format, never the plan's content</td></tr>
    <tr><td>the workspace is checked</td><td>Memory entries must quote the
      person, saved plans are read-only, the draft changes only in an answer,
      monitoring turns change nothing, and a failed turn commits nothing</td></tr>
    <tr><td>monitoring detection is deterministic</td><td>Signals reproduce from the
      record as it stood that day and are deduped; the coach decides each Alert
      and its decision is recorded with the facts it was given</td></tr>
    <tr><td>the reachable surface is small</td><td>The agent exposes two
      record reads, one guideline search and the signal review, none taking a
      participant id; memory and plans are files in its sandboxed workspace</td></tr>
  </table></details>`;
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g,
    c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
}

// The side column has room for the readiness snapshot; a stacked page keeps it folded.
document.querySelector(".context-details").open = matchMedia("(min-width: 1200px)").matches;
boot();
