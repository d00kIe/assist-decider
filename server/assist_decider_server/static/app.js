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

function token() {
  try { return sessionStorage.getItem("assist-decider-token"); } catch { return null; }
}

function setConn(state, text) {
  const badge = $("conn");
  badge.className = "badge " + state;
  badge.textContent = text;
}

function showLogin(message) {
  if (abort) abort.abort();
  $("main").hidden = true;
  $("login").hidden = false;
  $("login-error").textContent = message || "";
  setConn("off", "disconnected");
}

async function api(path, signal) {
  const res = await fetch(path, { headers: { Authorization: "Bearer " + token() }, signal, cache: "no-store" });
  if (res.status === 401 || res.status === 429) {
    try { sessionStorage.removeItem("assist-decider-token"); } catch { /* ignore */ }
    throw Object.assign(new Error(res.status === 429 ? "Too many failed attempts, wait a while" : "Wrong token"), { auth: true });
  }
  if (!res.ok) throw new Error("HTTP " + res.status);
  return res;
}

// --- rendering ------------------------------------------------------------

function renderQuestion(q) {
  const box = el("div", "question");
  const head = el("div", "qhead");
  head.append(el("strong", null, q.key), el("span", "muted", q.instructions));
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

function renderTrace(t) {
  const card = el("article", "card");
  const head = el("div", "chead");
  const status = t.status === "ok" ? (t.segments.some((s) => s.escalate) ? "partial" : "ok") : "escalate";
  head.append(el("span", "badge " + (status === "ok" ? "ok" : status === "partial" ? "warn" : "err"),
    status + (t.reason ? ` · ${t.reason}` : "")));
  head.append(el("q", "utterance", t.text));
  head.append(el("span", "muted", `${t.language}${t.satellite_area ? " · " + t.satellite_area : ""} · ${time(t.ts)} · ${t.elapsed_ms} ms (model ${t.model_ms} ms)`));
  card.append(head);
  if (t.blocked) card.append(el("div", "muted small", `not attempted: "${t.blocked.word}" (${t.blocked.reason}) is not something an intent call can express`));
  for (const seg of t.segments || []) {
    const s = el("div", "segment");
    s.append(el("div", "stext", "› " + seg.text));
    const facts = [];
    if (seg.numbers && seg.numbers.length) facts.push("numbers: " + seg.numbers.map((n) => n.value + (n.unit ? " " + n.unit : "")).join(", "));
    if (seg.dropped_intents && Object.keys(seg.dropped_intents).length) facts.push("dropped: " + Object.entries(seg.dropped_intents).map(([k, v]) => `${k} (${v})`).join(", "));
    if (seg.intent_shortcut) facts.push("intent: " + seg.intent_shortcut);
    if (seg.shortcut) facts.push("target: " + seg.shortcut);
    if (seg.note) facts.push(seg.note);
    if (seg.escalate) facts.push("escalated: " + seg.escalate);
    for (const f of facts) s.append(el("div", "muted small", f));
    for (const q of seg.questions || []) s.append(renderQuestion(q));
    for (const a of seg.actions || []) s.append(el("pre", "action", `${a.intent} ${JSON.stringify(a.slots)}`));
    card.append(s);
  }
  return card;
}

function addTrace(t) {
  if (paused) return;
  const list = $("decisions");
  list.prepend(renderTrace(t));
  while (list.children.length > MAX_CARDS) list.lastChild.remove();
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
      $("login").hidden = true;
      $("main").hidden = false;
      $("decisions").replaceChildren();
      $("log").replaceChildren();
      const res = await api("/v1/events", abort.signal);
      setConn("ok", "live");
      delay = 1000;
      await readSse(res.body.getReader());
    } catch (err) {
      if (abort.signal.aborted) return;
      if (err.auth) return showLogin(err.message);
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

$("login").addEventListener("submit", (ev) => {
  ev.preventDefault();
  try { sessionStorage.setItem("assist-decider-token", $("token").value.trim()); } catch { /* ignore */ }
  $("token").value = "";
  connect();
});
$("pause").addEventListener("click", () => {
  paused = !paused;
  $("pause").textContent = paused ? "Resume" : "Pause";
});
$("clear-log").addEventListener("click", () => $("log").replaceChildren());
$("clear-decisions").addEventListener("click", () => $("decisions").replaceChildren());
$("level").addEventListener("change", () => [...$("log").children].forEach(filterLine));
$("search").addEventListener("input", () => [...$("log").children].forEach(filterLine));

if (token()) connect(); else showLogin();
