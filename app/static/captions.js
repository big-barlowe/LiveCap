"use strict";
// Shared by the public page and the admin page: captions, language, read-aloud, text size, connection.

const $ = (id) => document.getElementById(id);
const main = $("captions"), lines = $("lines"), partial = $("partial"), listening = $("listening"), empty = $("empty");
const MAX_LINES = 400;
const SENTENCE_GAP = 0.25; // seconds of silence between sentences
const SCHEDULE_AHEAD = 1.5; // seconds; generous because a background tab's timers run about once a second
const MAX_BEHIND = 20; // seconds of reading waiting before old sentences are dropped to stay live
const ASSUMED_CLIP_SECONDS = 3; // for clips still being fetched

const LiveCap = {
  socketPath: "/ws/captions",
  languages: [],
  outputLang: stored("livecap.output") || "orig",
  socket: null,
  theme: null,
  previewing: false, // the admin's theme panel is showing colours that aren't saved yet
  notices: {},
  onState() {},
  onDisconnect() {},
  onClose() {},
  emptyText() { return ""; },
  send(message) {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(message));
  },
  setNotice(key, text) {
    this.notices[key] = text;
    const notice = $("notice"), parts = Object.values(this.notices).filter(Boolean);
    notice.textContent = parts.join(" ");
    notice.hidden = !parts.length;
  },
};

let following = true;
let speaking = false;
let reconnectDelay = 500;

function stored(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function store(key, value) {
  try { localStorage.setItem(key, value); } catch { /* private window */ }
}

// ----- Branding -----

function setThemeCss(url) {
  // An unchanged address is left alone, so a reconnecting page doesn't fetch it again.
  if ($("theme-css").getAttribute("href") !== url) $("theme-css").href = url;
}

function setLogo(url) {
  const logo = $("brand-logo");
  if (logo.getAttribute("src") !== url && url) logo.src = url;
  if (!url) logo.removeAttribute("src");
  logo.hidden = !url;
}

function applyTheme(theme) {
  setThemeCss(theme.css);
  setLogo(theme.logo);
  const heading = $("brand-heading");
  if (heading) {
    heading.textContent = theme.heading;
    document.title = theme.heading;
  }
}

// ----- Captions -----

function atBottom() {
  return main.scrollHeight - main.scrollTop - main.clientHeight < 60;
}
function follow() {
  if (following) main.scrollTop = main.scrollHeight;
  $("jump").hidden = following;
}
main.addEventListener("scroll", () => {
  following = atBottom();
  $("jump").hidden = following;
}, { passive: true });
$("jump").addEventListener("click", () => { following = true; follow(); });

function renderSegment(message) {
  let line = lines.querySelector(`[data-id="${message.id}"]`);
  if (!line) {
    line = document.createElement("p");
    line.className = "line";
    line.dataset.id = message.id;
    let next = null;
    for (let node = lines.lastElementChild; node && Number(node.dataset.id) > message.id; node = node.previousElementSibling) next = node;
    lines.insertBefore(line, next);
    while (lines.children.length > MAX_LINES) lines.firstElementChild.remove();
  }
  line.textContent = message.text;
  empty.hidden = true;
  follow();
}

function setPartial(text) {
  partial.textContent = text;
  partial.hidden = !text;
  listening.hidden = !speaking || Boolean(text);
  empty.hidden = empty.hidden || Boolean(text);
  follow();
}

function showEmpty() {
  if (lines.children.length || partial.textContent) { empty.hidden = true; return; }
  empty.hidden = false;
  empty.textContent = LiveCap.emptyText();
}

function clearLines() {
  lines.textContent = "";
  setPartial("");
  showEmpty();
}

function resetCaptions() {
  reader.clear();
  clearLines();
}

function applyDirection() {
  const entry = LiveCap.languages.find((l) => l.code === LiveCap.outputLang);
  main.dir = entry && entry.rtl ? "rtl" : "ltr";
  main.lang = LiveCap.outputLang === "orig" ? "" : LiveCap.outputLang;
}

// ----- Read aloud -----
// Each clip is scheduled to start after the one before has finished, so a sentence can never cut another off.

const reader = {
  on: false,
  context: null,
  clips: [],
  sources: new Set(),
  nextStart: 0,
  session: 0,
  seen: new Set(),

  clear() {
    this.session += 1;
    this.clips = [];
    this.seen.clear();
    this.sources.forEach((source) => { try { source.stop(); } catch { /* already finished */ } });
    this.sources.clear();
    this.nextStart = 0;
  },
  waiting() {
    const playing = this.context ? Math.max(0, this.nextStart - this.context.currentTime) : 0;
    return this.clips.reduce((sum, clip) => sum + (clip.buffer ? clip.buffer.duration + SENTENCE_GAP : ASSUMED_CLIP_SECONDS), playing);
  },
  add(id) {
    if (!this.on || this.seen.has(id)) return;
    this.seen.add(id);
    // A longer wait asks the server for a slightly faster reading, which sounds natural unlike a sped-up recording.
    const waiting = this.waiting();
    const boost = waiting < 4 ? 0 : waiting < 7 ? 0.1 : waiting < 11 ? 0.2 : 0.3;
    const clip = { buffer: null, failed: false };
    this.clips.push(clip);
    const session = this.session;
    fetch(`/api/speech/${LiveCap.outputLang}/${id}.wav?boost=${boost}`)
      .then((response) => (response.ok ? response.arrayBuffer() : Promise.reject(new Error(response.status))))
      .then((data) => this.context.decodeAudioData(data))
      .then((buffer) => { clip.buffer = buffer; })
      .catch(() => { clip.failed = true; })
      .finally(() => { if (session === this.session) this.pump(); });
  },
  pump() {
    if (!this.on || !this.context) return;
    // iOS can suspend the sound after an interruption, such as a call or other media starting.
    if (this.context.state !== "running") this.context.resume().catch(() => {});
    while (this.waiting() > MAX_BEHIND && this.clips.length > 1 && this.clips[0].buffer) this.clips.shift();
    const now = this.context.currentTime;
    while (this.clips.length && this.nextStart - now < SCHEDULE_AHEAD) {
      const clip = this.clips[0];
      if (clip.failed) { this.clips.shift(); continue; }
      if (!clip.buffer) break;
      this.clips.shift();
      const source = this.context.createBufferSource();
      source.buffer = clip.buffer;
      source.connect(this.context.destination);
      source.onended = () => this.sources.delete(source);
      const start = Math.max(now + 0.03, this.nextStart);
      source.start(start);
      this.sources.add(source);
      this.nextStart = start + clip.buffer.duration + SENTENCE_GAP;
    }
  },
};
setInterval(() => reader.pump(), 250);

function setPlaying(on) {
  reader.on = on;
  if (!on) {
    reader.clear();
  } else {
    // Created and resumed inside the click, as browsers require before any sound can play.
    reader.context = reader.context || new (window.AudioContext || window.webkitAudioContext)();
    reader.context.resume();
    reader.seen = new Set([...lines.children].map((l) => Number(l.dataset.id)));
  }
  updatePlay();
}
$("play").addEventListener("click", () => setPlaying(!reader.on));

function canSpeak() {
  if (LiveCap.outputLang === "orig") return true;
  const entry = LiveCap.languages.find((l) => l.code === LiveCap.outputLang);
  return Boolean(entry && entry.voice);
}
function updatePlay() {
  const play = $("play"), allowed = canSpeak();
  if (!allowed && reader.on) { reader.on = false; reader.clear(); }
  play.disabled = !allowed;
  play.title = allowed ? "" : "No voice is available for this language";
  play.setAttribute("aria-pressed", String(reader.on));
  $("play-text").textContent = !allowed ? "No voice" : reader.on ? "Stop" : "Play";
}

// ----- Controls -----

$("output-lang").addEventListener("change", (e) => {
  LiveCap.outputLang = e.target.value;
  store("livecap.output", LiveCap.outputLang);
  LiveCap.setNotice("language", "");
  applyDirection();
  resetCaptions();
  updatePlay();
  LiveCap.send({ type: "language", lang: LiveCap.outputLang });
});

let captionSize = Number(stored("livecap.size")) || 2;
function applySize() {
  document.documentElement.style.setProperty("--caption-size", `${captionSize}rem`);
  store("livecap.size", captionSize);
  follow();
}
$("smaller").addEventListener("click", () => { captionSize = Math.max(1, captionSize - 0.25); applySize(); });
$("larger").addEventListener("click", () => { captionSize = Math.min(5, captionSize + 0.25); applySize(); });

// ----- Connection -----

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${proto}//${location.host}${LiveCap.socketPath}?lang=${encodeURIComponent(LiveCap.outputLang)}`);
  LiveCap.socket = socket;
  // A dropped connection must not interrupt the sentence being read.
  socket.onopen = () => { reconnectDelay = 500; reader.seen.clear(); clearLines(); };
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    switch (message.type) {
      case "state":
        LiveCap.onState(message);
        speaking = Boolean(message.speaking);
        setPartial(partial.textContent);
        showEmpty();
        break;
      case "segment":
        renderSegment(message);
        if (!message.backlog) reader.add(message.id);
        break;
      case "partial":
        setPartial(message.text);
        break;
      case "reset":
        resetCaptions();
        break;
      case "theme":
        LiveCap.theme = message;
        if (!LiveCap.previewing) applyTheme(message);
        break;
      case "language":
        if (message.lang !== LiveCap.outputLang) break;
        LiveCap.setNotice("language",
          message.state === "downloading" ? "Getting this language ready. Captions will follow shortly."
          : message.state === "error" ? `Translation is unavailable right now, so this is the original text. ${message.detail}`.trim() : "");
        break;
    }
  };
  socket.onclose = (event) => {
    LiveCap.onClose(event);
    LiveCap.onDisconnect();
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 8000);
  };
}

async function startCaptions() {
  const config = await (await fetch("/api/config")).json();
  LiveCap.languages = config.outputLanguages;
  const output = $("output-lang");
  output.add(new Option("Original", "orig"));
  config.outputLanguages.forEach((l) => output.add(new Option(l.name, l.code)));
  if (![...output.options].some((o) => o.value === LiveCap.outputLang)) LiveCap.outputLang = "orig";
  output.value = LiveCap.outputLang;
  applyDirection();
  applySize();
  updatePlay();
  connect();
}
