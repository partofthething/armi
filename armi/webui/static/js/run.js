// Running the open case and following its log.
//
// The run itself lives in the ARMI process (see armi/webui/runs.py); this is the console that
// shows it: a panel docked above the status bar with the log as it grows, how long it has been
// going, and a way to stop it. Closing the panel does not stop the run, and opening it again picks
// the log back up.

import { api } from "./api.js";
import { h } from "./dom.js";
import { toast } from "./ui.js";

const POLL_MS = 1000;
const MAX_CHARS = 400_000; // keep the browser responsive on chatty runs

const STATE_LABEL = {
  running: "Running",
  finished: "Finished",
  failed: "Failed",
  stopped: "Stopped",
};

let panel = null;
let logEl = null;
let stateEl = null;
let timeEl = null;
let stopBtn = null;
let offset = 0;
let timer = null;
let onStateChange = () => {};

function build() {
  logEl = h("pre.runpanel__log", { "aria-live": "off" });
  stateEl = h("span.runpanel__state", { "data-state": "idle" }, "Idle");
  timeEl = h("span.runpanel__time");
  stopBtn = h("button.btn.btn--sm.btn--danger", { type: "button", onclick: stop }, "Stop");
  const close = h("button.btn.btn--sm.btn--quiet", { type: "button", onclick: hide, title: "Hide (the run keeps going)" }, "Hide");
  panel = h(
    "section.runpanel",
    { hidden: true, "aria-label": "Case run" },
    h("div.runpanel__head", h("span.runpanel__title", "Run"), stateEl, timeEl, h("span.runpanel__spacer"), stopBtn, close),
    logEl,
  );
  document.querySelector(".shell").appendChild(panel);
}

function show() {
  if (!panel) build();
  panel.hidden = false;
  document.querySelector(".shell").classList.add("shell--running");
}

function hide() {
  if (panel) panel.hidden = true;
  document.querySelector(".shell").classList.remove("shell--running");
}

function elapsed(seconds) {
  const s = Math.floor(seconds);
  const m = Math.floor(s / 60);
  const hours = Math.floor(m / 60);
  return hours ? `${hours}h ${m % 60}m` : m ? `${m}m ${s % 60}s` : `${s}s`;
}

function render(status, replace = false) {
  if (!status || !status.state) return;
  const stuckToBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
  if (replace) logEl.textContent = "";
  if (status.text) {
    logEl.textContent += status.text;
    if (logEl.textContent.length > MAX_CHARS) logEl.textContent = logEl.textContent.slice(-MAX_CHARS);
  }
  if (stuckToBottom || replace) logEl.scrollTop = logEl.scrollHeight;
  offset = status.offset;
  stateEl.dataset.state = status.state;
  stateEl.textContent = STATE_LABEL[status.state] || status.state;
  stateEl.title = status.command;
  timeEl.textContent = elapsed(status.elapsed);
  timeEl.title = status.log;
  stopBtn.disabled = status.state !== "running";
  onStateChange(status.state);
}

async function poll() {
  clearTimeout(timer);
  try {
    const status = await api.runStatus(offset);
    render(status);
    if (status.state === "running") {
      timer = setTimeout(poll, POLL_MS);
    } else if (status.state === "finished") {
      toast("The run finished", { kind: "ok", detail: status.log });
    } else if (status.state === "failed") {
      toast("The run failed", { kind: "error", detail: `Exit code ${status.returncode}. See ${status.log}` });
    }
  } catch (error) {
    toast("Lost track of the run", { kind: "error", detail: error.message });
  }
}

async function stop() {
  try {
    render(await api.stopRun());
  } catch (error) {
    toast("Could not stop the run", { kind: "error", detail: error.message });
  }
}

export const runPanel = {
  /** Start a run of the saved case and follow it. */
  async start() {
    show();
    const status = await api.startRun();
    render(status, true);
    timer = setTimeout(poll, POLL_MS);
  },
  /** Show the panel for whatever run this editor already has, if any. */
  async reopen() {
    const status = await api.runStatus(null);
    if (!status.state) {
      toast("Nothing has been run yet");
      return;
    }
    show();
    render(status, true);
    if (status.state === "running") timer = setTimeout(poll, POLL_MS);
  },
  toggle() {
    if (panel && !panel.hidden) hide();
    else this.reopen();
  },
  onState(callback) {
    onStateChange = callback;
  },
};
