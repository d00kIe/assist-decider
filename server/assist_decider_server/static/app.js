// Assist Decider live log. Renders untrusted text (utterances, entity names) with
// textContent only, never as HTML.
"use strict";

const LEVELS = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50 };
const MAX_CARDS = 200;
const MAX_LINES = 2000;
const $ = (id) => document.getElementById(id);
let paused = false;
let abort = null;

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function time(ts) {
  return new Date(ts * 1000).toLocaleTimeString();
}

function setConn(state, text) {
  const badge = $("conn");
  badge.className = "badge " + state;
  badge.textContent = text;
}

async function api(path, signal) {
  const res = await fetch(path, { signal, cache: "no-store" });
  if (!res.ok) throw new Error("HTTP " + res.status);
  return res;
}

// --- rendering ------------------------------------------------------------

function renderQuestion(q) {
  const box = el("div", "question");
  const head = el("div", "qhead");
  head.append(el("strong", null, q.instructions));
  const passed = q.confidence >= q.threshold && q.choice !== "none";
  head.append(el("span", "badge " + (passed ? "ok" : "warn"),
    `confidence ${q.confidence.toFixed(2)} / ${q.threshold.toFixed(2)}`));
  head.append(el("span", "muted", `${q.ms} ms`));
  box.append(head);
  const entries = Object.entries(q.probs).sort((a, b) => b[1] - a[1]);
  for (const [key, p] of entries) {
    const row = el("div", "opt" + (key === q.choice ? " chosen" : ""));
    const bar = el("span", "bar");
    bar.style.width = Math.round(p * 100) + "%";
    row.append(el("span", "pct", (p * 100).toFixed(1) + "%"), el("span", "label", `${key}: ${q.options[key] ?? ""}`), bar);
    box.append(row);
  }
  return box;
}

// --- decision tree ----------------------------------------------------------

const PHASES = {
  devices: "Devices",
  request: "Is it for the home, now?",
  kind: "Command or question",
  action: "What to do",
  check: "Sure enough?",
};
const REASONS = {
  low_confidence: "the model was not sure enough",
  no_target: "no device or room named, and no speaker's room",
  too_many_devices: "too many devices to ask about",
  no_action: "every action was ruled out",
  missing_value: "no value said or chosen",
  value_not_possible: "the number does not fit that action",
  conditional: "'if …' sentences are not supported",
  not_for_home: "not about the home's devices",
  inconsistent: "the model's answers contradict each other",
  unsupported: "a question about all of a kind is not supported yet",
  unsupported_language: "the model does not speak this language",
  no_exposed_entities: "nothing is exposed to Assist",
};

function node(cls, icon, label, detail) {
  const line = el("span", "node " + cls);
  line.append(el("span", "icon", icon), el("span", "check", label));
  if (detail) line.append(el("span", "result", detail));
  return line;
}

// A tree item; with `body` it folds open to show it.
function item(line, body, open) {
  const li = el("li");
  if (!body) {
    li.append(line);
    return li;
  }
  const details = el("details");
  details.open = !!open;
  const summary = el("summary");
  summary.append(line);
  details.append(summary, body);
  li.append(details);
  return li;
}

function renderStep(step, t) {
  const [cls, icon] = step.ok === true ? ["yes", "✓"] : step.ok === false ? ["no", "✗"] : ["info", "•"];
  const line = node(cls, icon, step.check, step.result);
  if (step.q !== undefined && t.questions[step.q]) {
    return item(line, renderQuestion(t.questions[step.q]), step.ok === false);
  }
  return item(line);
}

function renderTrace(t) {
  const card = el("article", "card");
  const head = el("div", "chead");
  head.append(el("span", "badge " + (t.status === "ok" ? "ok" : "err"),
    t.status + (t.reason ? ` · ${REASONS[t.reason] || t.reason}` : "")));
  head.append(el("q", "utterance", t.text));
  const meta = [t.language, t.satellite_area && "room " + t.satellite_area,
    time(t.ts), `${t.elapsed_ms} ms (model ${t.model_ms} ms)`];
  head.append(el("span", "muted", meta.filter(Boolean).join(" · ")));
  card.append(head);

  const tree = el("ul", "tree");
  if (t.numbers && t.numbers.length) {
    tree.append(item(node("info", "#", "numbers", t.numbers.map((x) => x.value + (x.unit ? " " + x.unit : "")).join(", "))));
  }
  // Consecutive steps of one phase form one branch: Devices, Command or question, What to do.
  let branch = null;
  let phase = null;
  for (const step of t.steps || []) {
    if (step.phase !== phase) {
      phase = step.phase;
      branch = el("ul");
      tree.append(item(node("phase", "◆", PHASES[phase] || phase), branch, true));
    }
    branch.append(renderStep(step, t));
  }
  for (const a of t.actions || []) {
    const slots = Object.entries(a.slots).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join("|") : v}`).join(" ");
    tree.append(item(node("yes", "→", a.intent, `${slots} · confidence ${a.confidence.toFixed(2)}`)));
  }
  if (t.reason) tree.append(item(node("err", "✗", "handed to Home Assistant", REASONS[t.reason] || t.reason)));
  card.append(tree);
  return card;
}

function addTrace(t) {
  if (paused) return;
  const list = $("decisions");
  const card = renderTrace(t);
  // Clicking a decision pins it on the Home tab; clicking it again follows the latest again.
  card.addEventListener("click", (ev) => {
    if (ev.target.closest("summary")) return; // folding a branch is not choosing a decision
    const unpin = pinned === t;
    for (const c of list.children) c.classList.remove("pinned");
    pinned = unpin ? null : t;
    if (!unpin) card.classList.add("pinned");
    focus = pinned || latest;
    renderHome();
  });
  list.prepend(card);
  while (list.children.length > MAX_CARDS) list.lastChild.remove();
  latest = t;
  if (!pinned) focus = t;
  if (!$("pane-home").hidden) loadHome();
}

// --- home: every room and entity, and how names are matched ---------------

let homeData = null;
let latest = null;
let pinned = null;
let focus = null;

// What one decision touched, per entity/area ID.
function touched(t) {
  const marks = new Map();
  const get = (id) => { if (!marks.has(id)) marks.set(id, {}); return marks.get(id); };
  for (const id of t.said || []) get(id).said = true;
  for (const q of t.questions || []) {
    for (const [key, id] of Object.entries(q.ids || {})) {
      const m = get(id);
      m.model = Math.max(m.model || 0, q.probs[key] || 0);
      if (key === q.choice) m.chosen = true;
    }
  }
  for (const a of t.actions || []) get(a.slots.name || a.slots.area).used = true;
  return marks;
}

function markBadges(m) {
  const out = [];
  if (!m) return out;
  if (m.said) out.push(el("span", "badge ok", "said"));
  if (m.model !== undefined) out.push(el("span", "badge " + (m.chosen ? "ok" : "info"), `model ${(m.model * 100).toFixed(0)}%${m.chosen ? " ✓" : ""}`));
  if (m.used) out.push(el("span", "badge ok", "used"));
  return out;
}

function fact(label, text) {
  const row = el("div", "small");
  row.append(el("span", "muted", label + " "), el("span", null, text));
  return row;
}

const quoted = (words) => words.map((w) => `“${w}”`).join(" · ");

function renderLegend() {
  const box = el("details", "legend");
  box.append(el("summary", null, "How devices are found"));
  const steps = el("ol", "small");
  for (const text of [
    "Names: a device, room or floor name or alias, word for word or with another ending (“linke Licht” → “Linkes Licht”). Text is compared lowercase with ä→ae, ö→oe, ü→ue, ß→ss (the “matched by” forms). The longest name wins.",
    "One word of a name counts when no other device, room or floor has it (“the right one” → Right Light), never for “exact name only” devices.",
    "Compound words: a one-word room name of 4+ letters also matches at the start of a longer word (“wohnzimmerlicht” → Wohnzimmer).",
    "A room or floor: the model says one device or all of one kind, which kind, and which device, worded as “model sees”. When a number is said, only devices that can take it are offered.",
    "Nothing named: the model says whether the previous command's devices are meant (“turn it off”), else here, this floor or the whole home.",
    "“Exact name only” devices (locks, alarms, garage/gate/door covers) are never picked from a room, and “all” never includes them.",
  ]) steps.append(el("li", null, text));
  box.append(steps);
  return box;
}

function renderEntity(e, marks) {
  const row = el("div", "ent" + (marks.has(e.id) ? " touched" : ""));
  const head = el("div", "ehead");
  head.append(el("strong", null, e.name), el("span", "tag", e.kind + (e.device_class ? " · " + e.device_class : "")));
  if (e.sensitive) head.append(el("span", "badge warn", "exact name only"));
  head.append(...markBadges(marks.get(e.id)));
  row.append(head, el("div", "small muted mono", e.id));
  if (e.aliases.length) row.append(fact("aliases", e.aliases.join(", ")));
  row.append(fact("matched by", quoted(e.words)));
  row.append(fact("model sees", e.sensitive ? "never offered for a room" : e.option));
  row.append(fact("can", e.actions.length ? e.actions.join(", ") : "nothing yet"));
  return row;
}

function renderHome() {
  const box = $("home");
  const marks = focus ? touched(focus) : new Map();
  $("home-focus").textContent = focus
    ? `Marked: what “${focus.text}” touched${pinned ? " (pinned, click it again to follow the latest)" : ""}.`
    : "Click a decision to mark what it touched.";
  if (!homeData || !homeData.entities.length) {
    box.replaceChildren(el("p", "muted", "No home yet: it arrives with the first request from Home Assistant."));
    return;
  }
  const needle = $("home-search").value.trim().toLowerCase();
  const only = $("home-only").checked;
  const show = (e) => (!only || marks.has(e.id)) &&
    (!needle || [e.name, e.id, ...e.aliases, ...e.words].some((s) => s.toLowerCase().includes(needle)));

  const byArea = new Map();
  for (const e of homeData.entities) {
    const key = e.area_id || "";
    if (!byArea.has(key)) byArea.set(key, []);
    byArea.get(key).push(e);
  }
  const floors = new Map(homeData.floors.map((f) => [f.id, f]));
  // Home Assistant's floor order; rooms without a floor, then entities without a room, last.
  const floorOrder = new Map(homeData.floors.map((f, i) => [f.id, i]));
  const rooms = [...homeData.areas].sort((a, b) => (floorOrder.get(a.floor_id) ?? 1e9) - (floorOrder.get(b.floor_id) ?? 1e9));
  rooms.push({ id: "", name: "No room", aliases: [], words: [], compound: [] });

  const out = [renderLegend()];
  let floorShown;
  for (const a of rooms) {
    const ents = (byArea.get(a.id) || []).filter(show);
    const roomHit = a.id && (marks.has(a.id) || (needle && [a.name, ...a.aliases].some((s) => s.toLowerCase().includes(needle))));
    if (!ents.length && !(roomHit && (!only || marks.has(a.id)))) continue;
    const floor = floors.get(a.floor_id);
    if (homeData.floors.length && floor?.id !== floorShown) {
      floorShown = floor?.id;
      out.push(el("h3", "floor", floor ? floor.name + (floor.aliases.length ? ` (${floor.aliases.join(", ")})` : "") : "No floor"));
    }
    const room = el("div", "room" + (marks.has(a.id) ? " touched" : ""));
    const head = el("div", "ehead");
    head.append(el("strong", null, a.name), ...markBadges(marks.get(a.id)));
    room.append(head);
    if (a.id) {
      if (a.aliases.length) room.append(fact("aliases", a.aliases.join(", ")));
      room.append(fact("matched by", quoted(a.words) + (a.compound.length ? ` · also at the start of compound words (${a.compound.map((w) => w + "…").join(", ")})` : "")));
    }
    for (const e of ents) room.append(renderEntity(e, marks));
    out.push(room);
  }
  box.replaceChildren(...out);
}

async function loadHome() {
  try {
    homeData = await (await api("/v1/home", abort && abort.signal)).json();
  } catch { /* keep the last home */ }
  renderHome();
}

function showTab(name) {
  for (const tab of ["log", "home"]) {
    $("pane-" + tab).hidden = tab !== name;
    $("tab-" + tab).classList.toggle("active", tab === name);
    $("tab-" + tab).setAttribute("aria-selected", String(tab === name));
  }
  if (name === "home") loadHome();
}

function addLog(e) {
  const line = el("div", "line lvl-" + e.level.toLowerCase());
  line.dataset.level = e.level;
  line.append(el("span", "muted", time(e.ts)), el("span", "lvl", e.level), el("span", "msg", e.msg));
  filterLine(line);
  const log = $("log");
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 20;
  if (!paused) log.append(line);
  while (log.children.length > MAX_LINES) log.firstChild.remove();
  if (atBottom) log.scrollTop = log.scrollHeight;
}

function filterLine(line) {
  const min = LEVELS[$("level").value];
  const needle = $("search").value.toLowerCase();
  line.hidden = LEVELS[line.dataset.level] < min || (needle && !line.textContent.toLowerCase().includes(needle));
}

// --- streaming -------------------------------------------------------------

async function connect() {
  abort = new AbortController();
  let delay = 1000;
  while (!abort.signal.aborted) {
    try {
      const info = await (await api("/v1/info", abort.signal)).json();
      $("info").textContent = `v${info.server_version} · ${info.provider}/${info.model} · ${info.device} · ${info.languages.join(", ")}`;
      $("decisions").replaceChildren();
      $("log").replaceChildren();
      const res = await api("/v1/events", abort.signal);
      setConn("ok", "live");
      delay = 1000;
      await readSse(res.body.getReader());
    } catch {
      if (abort.signal.aborted) return;
    }
    setConn("warn", "reconnecting…");
    await new Promise((r) => setTimeout(r, delay));
    delay = Math.min(delay * 2, 30000);
  }
}

async function readSse(reader) {
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) throw new Error("stream closed");
    buffer += decoder.decode(value, { stream: true });
    let cut;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const data = chunk.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n");
      if (!data) continue;
      const event = JSON.parse(data);
      if (event.type === "trace") addTrace(event);
      else if (event.type === "log") addLog(event);
    }
  }
}

// --- wiring ----------------------------------------------------------------

$("pause").addEventListener("click", () => {
  paused = !paused;
  $("pause").textContent = paused ? "Resume" : "Pause";
});
$("clear-log").addEventListener("click", () => $("log").replaceChildren());
$("clear-decisions").addEventListener("click", () => $("decisions").replaceChildren());
$("level").addEventListener("change", () => [...$("log").children].forEach(filterLine));
$("search").addEventListener("input", () => [...$("log").children].forEach(filterLine));
$("tab-log").addEventListener("click", () => showTab("log"));
$("tab-home").addEventListener("click", () => showTab("home"));
$("home-refresh").addEventListener("click", loadHome);
$("home-search").addEventListener("input", renderHome);
$("home-only").addEventListener("change", renderHome);

connect();
