"use strict";

LiveCap.socketPath = "/ws/admin";
let adminConfig, state = {};

LiveCap.emptyText = () => (state.source ? "Listening. Captions appear here as words are recognized." : "Choose an audio source to start captioning.");
LiveCap.onClose = (event) => {
  // The server restarted, so this page's session is stale: signing in again issues a new one.
  if (event.code === 4401) location.reload();
};
LiveCap.onDisconnect = () => {
  $("live-dot").className = "dot bad";
  $("status-text").textContent = "Reconnecting…";
};

// ----- Microphone -----

const micCapture = {
  stream: null, context: null, node: null, socket: null,

  async start() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      const url = `https://${location.hostname}:${adminConfig.httpsPort}${location.pathname}`;
      LiveCap.setNotice("mic", `Browsers only allow the microphone on a secure page. Open ${url} and accept the certificate warning.`);
      return false;
    }
    try {
      const deviceId = $("mic-device").value;
      // Echo cancellation keeps the read-aloud voice from being captioned again.
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, ...(deviceId ? { deviceId: { exact: deviceId } } : {}) },
      });
      this.context = new AudioContext({ latencyHint: "interactive" });
      await this.context.audioWorklet.addModule("/static/mic-worklet.js");
      await this.context.resume();
      this.node = new AudioWorkletNode(this.context, "mic-capture");
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      this.socket = new WebSocket(`${proto}//${location.host}/ws/mic?rate=${this.context.sampleRate}`);
      this.socket.binaryType = "arraybuffer";
      const opened = this.socket;
      this.socket.onclose = (event) => {
        if (this.socket !== opened) return;
        this.stop();
        LiveCap.setNotice("mic", event.code === 4001 ? "Another browser took over the microphone input." : "");
      };
      this.node.port.onmessage = (event) => {
        if (this.socket && this.socket.readyState === WebSocket.OPEN) this.socket.send(event.data);
      };
      this.context.createMediaStreamSource(this.stream).connect(this.node);
      LiveCap.setNotice("mic", "");
      fillDevices();
      return true;
    } catch (error) {
      this.stop();
      LiveCap.setNotice("mic", error.name === "NotAllowedError"
        ? "Microphone permission was denied. Allow it in the browser's site settings, then choose Microphone again."
        : `Could not open the microphone: ${error.message}`);
      return false;
    }
  },
  stop() {
    const open = this.socket;
    this.socket = null;
    if (open) open.close();
    if (this.node) this.node.port.onmessage = null;
    if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
    if (this.context) this.context.close().catch(() => {});
    this.stream = this.context = this.node = null;
  },
};

async function fillDevices() {
  if (!navigator.mediaDevices) return;
  const select = $("mic-device"), current = select.value;
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "audioinput");
  select.textContent = "";
  devices.forEach((d, i) => select.add(new Option(d.label || `Microphone ${i + 1}`, d.deviceId)));
  if (current) select.value = current;
}
$("mic-device").addEventListener("change", async () => {
  if (!micCapture.socket) return;
  micCapture.stop();
  await micCapture.start();
});

// ----- Controls -----

document.querySelectorAll("[data-source]").forEach((button) => {
  button.addEventListener("click", async () => {
    const source = button.dataset.source;
    if (source === "mic") {
      if (!micCapture.socket) await micCapture.start();
      return;
    }
    micCapture.stop();
    LiveCap.setNotice("mic", "");
    LiveCap.send({ type: "source", source });
  });
});
$("input-lang").addEventListener("change", (e) => LiveCap.send({ type: "inputLang", lang: e.target.value }));

// ----- Theme -----

const MAX_LOGO_BYTES = 5 * 1024 * 1024;
const themePanel = {
  logoFile: null, // chosen but not saved yet
  logoRemoved: false,
  logoPreview: null,
  colors: {},

  build() {
    document.querySelectorAll(".color-field").forEach((field) => {
      const key = field.dataset.color, name = field.querySelector(".label").textContent;
      const row = field.querySelector(".color-row");
      const swatch = Object.assign(document.createElement("input"), { type: "color" });
      swatch.setAttribute("aria-label", `${name}: color picker`);
      const text = Object.assign(document.createElement("input"), { type: "text", placeholder: "Default", maxLength: 7, autocomplete: "off", spellcheck: false });
      text.setAttribute("aria-label", `${name}: color code, such as #1a2b3c`);
      const clear = Object.assign(document.createElement("button"), { type: "button", className: "plain", textContent: "Default" });
      clear.setAttribute("aria-label", `${name}: use the default`);
      row.append(swatch, text, clear);
      swatch.addEventListener("input", () => { text.value = swatch.value; this.colorChanged(key); });
      text.addEventListener("input", () => this.colorChanged(key));
      clear.addEventListener("click", () => { text.value = ""; this.colorChanged(key); });
      this.colors[key] = { swatch, text };
    });
  },
  // Accepts what people paste from a brand guide: #abc, abc, ABCDEF.
  normalize(value) {
    const hex = value.trim().replace(/^#/, "").toLowerCase();
    if (/^[0-9a-f]{3}$/.test(hex)) return `#${[...hex].map((c) => c + c).join("")}`;
    return /^[0-9a-f]{6}$/.test(hex) ? `#${hex}` : null;
  },
  colorChanged(key) {
    const { swatch, text } = this.colors[key];
    const color = this.normalize(text.value);
    const valid = color !== null || !text.value.trim();
    text.setAttribute("aria-invalid", String(!valid));
    if (color) swatch.value = color;
    if (valid) this.preview();
  },
  values() {
    const values = { heading: $("theme-heading").value, appearance: $("theme-appearance").value };
    for (const [key, { text }] of Object.entries(this.colors)) {
      const color = this.normalize(text.value);
      if (color === null && text.value.trim()) return null;
      values[key] = color || "";
    }
    return values;
  },
  preview() {
    const values = this.values();
    if (values) setThemeCss(`/api/admin/branding/preview.css?${new URLSearchParams(values)}`);
  },
  fill(settings, logo) {
    $("theme-heading").value = settings.heading;
    $("theme-heading").placeholder = LiveCap.theme.defaultHeading;
    $("theme-appearance").value = settings.appearance;
    for (const [key, { swatch, text }] of Object.entries(this.colors)) {
      text.value = settings[key];
      text.setAttribute("aria-invalid", "false");
      swatch.value = settings[key] || "#808080";
    }
    this.setLogoDraft(null, !logo);
  },
  setLogoDraft(file, removed) {
    if (this.logoPreview) URL.revokeObjectURL(this.logoPreview);
    this.logoFile = file;
    this.logoRemoved = removed;
    this.logoPreview = file ? URL.createObjectURL(file) : null;
    const saved = LiveCap.theme.logo;
    setLogo(this.logoPreview || (removed ? null : saved));
    $("logo-status").textContent = file ? file.name : removed ? "No logo" : "Current logo";
    $("logo-remove").disabled = removed && !file;
    $("logo-file").value = "";
  },
  showError(text) {
    $("theme-error").textContent = text;
    $("theme-error").hidden = !text;
  },
  open() {
    if (!LiveCap.theme) {
      LiveCap.setNotice("theme", "The theme can't be edited until this page connects to the server. Try again in a moment.");
      return;
    }
    LiveCap.setNotice("theme", "");
    LiveCap.previewing = true;
    this.showError("");
    this.fill(LiveCap.theme.settings, LiveCap.theme.logo);
    $("theme-panel").showModal();
  },
  async request(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `The server answered with error ${response.status}.`);
    }
    LiveCap.theme = await response.json();
  },
  async save() {
    const values = this.values();
    if (!values) {
      this.showError("A color code isn't valid. Use a code like #1a2b3c, or choose Default.");
      return;
    }
    this.showError("");
    $("theme-save").disabled = true;
    try {
      if (this.logoFile) {
        await this.request("/api/admin/branding/logo", { method: "PUT", body: this.logoFile });
      } else if (this.logoRemoved && LiveCap.theme.logo) {
        await this.request("/api/admin/branding/logo", { method: "DELETE" });
      }
      await this.request("/api/admin/branding", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values) });
      $("theme-panel").close();
    } catch (error) {
      this.showError(`Not saved. ${error.message}`);
    } finally {
      $("theme-save").disabled = false;
    }
  },
};
themePanel.build();
$("edit-theme").addEventListener("click", () => themePanel.open());
$("theme-cancel").addEventListener("click", () => $("theme-panel").close());
$("theme-appearance").addEventListener("change", () => themePanel.preview());
$("theme-reset").addEventListener("click", () => {
  themePanel.fill({ heading: "", appearance: "auto", accent: "", header: "", background: "" }, null);
  themePanel.preview();
});
$("logo-remove").addEventListener("click", () => themePanel.setLogoDraft(null, true));
$("logo-file").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  if (file.size > MAX_LOGO_BYTES) {
    e.target.value = "";
    themePanel.showError("That image is larger than 5 MB. Choose a smaller one.");
    return;
  }
  themePanel.showError("");
  themePanel.setLogoDraft(file, false);
});
$("theme-form").addEventListener("submit", (e) => {
  e.preventDefault();
  themePanel.save();
});
// Every way out comes through here, including the Esc key. What is saved goes back on show.
$("theme-panel").addEventListener("close", () => {
  LiveCap.previewing = false;
  themePanel.setLogoDraft(null, false);
  applyTheme(LiveCap.theme);
});

// ----- Status -----

LiveCap.onState = (message) => {
  state = message;
  document.querySelectorAll("[data-source]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.source === state.source));
  });
  $("input-lang").value = state.inputLang;
  $("mic-field").hidden = state.source !== "mic" || !micCapture.socket;

  const live = state.source === "mic" || (state.source === "obs" && state.obsLive.length > 0);
  $("live-dot").className = `dot ${state.error ? "bad" : live ? "live" : ""}`;
  const where = state.source === "mic" ? "Live from microphone"
    : state.source === "obs" ? (state.obsLive.length ? `Live from OBS (${state.obsLive[0].toUpperCase()})` : "Waiting for OBS")
    : "Not capturing";
  $("status-text").textContent = live ? `${where} · ${state.speaking ? "hearing speech" : "no speech"}` : where;
  const stats = [`${state.viewers} viewer${state.viewers === 1 ? "" : "s"}`];
  if (state.backlog > 1.5) stats.push(`${state.backlog}s behind`);
  if (state.droppedSeconds) stats.push(`${state.droppedSeconds}s of audio skipped`);
  $("stats").textContent = stats.join(" · ");

  const host = location.hostname;
  LiveCap.setNotice("obs", state.source === "obs" && !state.obsLive.length
    ? `Waiting for OBS. In OBS → Settings → Stream choose Custom, with Server rtmp://${host}:${adminConfig.rtmpPort}/live and Stream Key stream. Or use srt://${host}:${adminConfig.srtPort} with an empty key.`
    : "");
  LiveCap.setNotice("feedback", micCapture.socket && reader.on
    ? "Reading aloud near the microphone garbles the captions, because the voice and the speaker get mixed together. Use headphones for reading aloud, or listen on a device out of the microphone's range."
    : "");
  LiveCap.setNotice("error", state.error ? `Transcription error: ${state.error}` : "");
};

(async () => {
  const response = await fetch("/api/admin/config");
  if (response.status === 401) { location.reload(); return; }
  adminConfig = await response.json();
  const input = $("input-lang");
  Object.entries(adminConfig.inputLanguages).forEach(([code, name]) => input.add(new Option(name, code)));
  input.add(new Option("Auto-detect", "auto"));
  fillDevices();
  await startCaptions();
})();
