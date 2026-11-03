// Submitting the open case to run somewhere, and following it.
//
// The run itself is the ARMI process's business (see armi/webui/submit.py): it hands the case to a
// suite runner, on this machine, another one over SSH or a Slurm cluster, and reads the case
// folder for news. This is the console that shows it: a panel docked above the status bar with how
// many time nodes are in, the latest thing the run had to say, its log as it grows, and ways to stop
// it and to look at its results. Closing the panel does not stop the run; opening it again picks
// the log back up.

import { api } from "./api.js";
import { h, fill } from "./dom.js";
import { checkbox, dialog, field, selectInput, textInput, toast } from "./ui.js";

const POLL_MS = 1500;
const MAX_CHARS = 400_000; // keep the browser responsive on chatty runs
const REMEMBERED = "armi.submit.target";

const STATE_LABEL = {
  running: "Running",
  finished: "Finished",
  cached: "Up to date",
  failed: "Failed",
  stopped: "Stopped",
};

const KINDS = [
  ["local", "This machine"],
  ["ssh", "Another machine, over SSH"],
  ["slurm", "A Slurm cluster sharing this file system"],
];

let panel = null;
const el = {};
let offset = 0;
let timer = null;
let databaseSeen = null;
let last = null;
const listeners = { state: () => {}, progress: () => {}, results: () => {} };

// -- where to run ---------------------------------------------------------------------------------

function remembered() {
  const fallback = { kind: "local", followResults: true };
  try {
    return { ...fallback, ...JSON.parse(localStorage.getItem(REMEMBERED) || "{}") };
  } catch {
    return fallback;
  }
}

function remember(choice) {
  try {
    const { rerun, ...kept } = choice; // running again is a decision for each submission
    localStorage.setItem(REMEMBERED, JSON.stringify(kept));
  } catch {
    // Private windows and blocked storage: the form just starts blank next time.
  }
}

/** Ask where and how to run the case. Resolves to the choice, or null if the dialog is dismissed. */
export async function chooseTarget() {
  const choice = { ...remembered(), rerun: false };
  const set = (key) => (value) => {
    choice[key] = value;
  };
  const text = (key, placeholder) =>
    textInput({ value: choice[key], onChange: set(key), placeholder, mono: true, spellcheck: false });

  const sections = {
    local: h("p.field__hint", "Runs in the case folder, with the application this editor was started with."),
    ssh: h(
      "div",
      field("Host", text("host", "user@machine"), { hint: "As ssh takes it. Logs in with your keys; nothing is asked." }),
      field("Folder there", text("remoteRoot", "/scratch/me/runs"), {
        hint: "The case folder is copied into this one, and copied back as the run goes.",
      }),
      field("Python there", text("python", "python3"), { hint: "Needs ARMI and this application importable." }),
      field("Set up first", text("setup", "source ~/venv/bin/activate"), { hint: "Bash, run before the case." }),
    ),
    slurm: h(
      "div",
      field("CPUs", text("cpus", "1"), { hint: "--cpus-per-task, also the codes' OMP_NUM_THREADS." }),
      field("Wall time", text("walltimeHours", "cluster default"), { unit: "h" }),
      field("More sbatch options", text("sbatchArgs", "--partition=short --account=me")),
      field("Set up first", text("setup", "module load ... && source ~/venv/bin/activate"), {
        hint: "Bash, run on the compute node before the case. The node must see this file system.",
      }),
    ),
  };
  const holder = h("div");
  const show = (kind) => {
    choice.kind = kind || "local";
    fill(holder, sections[choice.kind]);
  };
  show(choice.kind);

  const chosen = await dialog({
    title: "Run the case",
    body: h(
      "div.submit",
      field("Where", selectInput({ value: choice.kind, options: KINDS, onChange: show })),
      holder,
      field("Give up after", text("timeoutHours", "never"), { unit: "h" }),
      h(
        "div.submit__checks",
        checkbox({ label: "Show results while it runs", checked: choice.followResults, onChange: set("followResults") }),
        h("span.field__hint", "Copies the database to the case folder after every time node (syncDbAfterWrite)."),
        checkbox({ label: "Run again even if the results are up to date", checked: false, onChange: set("rerun") }),
      ),
    ),
    actions: [["Run", "primary", () => choice]],
  });
  if (chosen) remember(chosen);
  return chosen;
}

/** The last choice of where to run, for running again without asking. */
export function lastTarget() {
  return { ...remembered(), rerun: false };
}

// -- the panel ------------------------------------------------------------------------------------

function build() {
  el.log = h("pre.runpanel__log", { "aria-live": "off" });
  el.state = h("span.runpanel__state", { "data-state": "idle" }, "Idle");
  el.title = h("span.runpanel__title", "Run");
  el.where = h("span.runpanel__where");
  el.bar = h("span.runpanel__bar-fill");
  el.nodes = h("span.runpanel__nodes");
  el.latest = h("span.runpanel__latest");
  el.time = h("span.runpanel__time");
  el.results = h(
    "button.btn.btn--sm",
    { type: "button", disabled: true, onclick: () => last && listeners.results(last) },
    "Results",
  );
  el.stop = h("button.btn.btn--sm.btn--danger", { type: "button", onclick: stop }, "Stop");
  const close = h(
    "button.btn.btn--sm.btn--quiet",
    { type: "button", onclick: hide, title: "Hide (the run keeps going)" },
    "Hide",
  );
  panel = h(
    "section.runpanel",
    { hidden: true, "aria-label": "Case run" },
    h(
      "div.runpanel__head",
      el.title,
      el.state,
      el.where,
      h("span.runpanel__progress", h("span.runpanel__bar", el.bar), el.nodes),
      el.latest,
      h("span.runpanel__spacer"),
      el.time,
      el.results,
      el.stop,
      close,
    ),
    el.log,
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
  last = status;
  const stuckToBottom = el.log.scrollHeight - el.log.scrollTop - el.log.clientHeight < 40;
  if (replace) el.log.textContent = "";
  if (status.text) {
    el.log.textContent += status.text;
    if (el.log.textContent.length > MAX_CHARS) el.log.textContent = el.log.textContent.slice(-MAX_CHARS);
  }
  if (stuckToBottom || replace) el.log.scrollTop = el.log.scrollHeight;
  offset = status.offset;

  el.title.textContent = status.title || "Run";
  el.title.title = status.settings || "";
  el.state.dataset.state = status.state;
  el.state.textContent = STATE_LABEL[status.state] || status.state;
  el.state.title = status.error || "";
  el.where.textContent = `on ${status.where}`;
  el.where.title = status.directory || "";

  const progress = status.progress || {};
  const done = progress.nodes || 0;
  const total = Math.max(progress.totalNodes || 1, done);
  el.bar.style.width = `${(100 * done) / total}%`;
  el.nodes.textContent = `${done}/${total} time nodes`;
  el.nodes.title = progress.lastNode ? `Last written: cycle ${progress.lastNode[0]}, node ${progress.lastNode[1]}` : "";
  el.latest.textContent = status.state === "failed" && status.error ? status.error : progress.latest || "";
  el.latest.title = el.latest.textContent;

  el.time.textContent = elapsed(status.elapsed);
  el.time.title = status.log;
  el.stop.disabled = status.state !== "running";
  el.results.disabled = !status.database;

  if (status.database && status.databaseVersion !== databaseSeen) {
    databaseSeen = status.databaseVersion;
    listeners.progress(status);
  }
  listeners.state(status.state);
}

async function poll() {
  clearTimeout(timer);
  try {
    const status = await api.runStatus(offset);
    render(status);
    if (status.state === "running") {
      timer = setTimeout(poll, POLL_MS);
      return;
    }
    if (status.state === "finished") {
      toast(`${status.title} finished`, { kind: "ok", detail: status.directory });
    } else if (status.state === "cached") {
      toast(`${status.title} is up to date`, {
        detail: "Its inputs have not changed since it last ran. Tick 'Run again' in the Run dialog to repeat it.",
      });
    } else if (status.state === "failed") {
      toast(`${status.title} failed`, { kind: "error", detail: status.error || `See ${status.log}` });
    }
  } catch (error) {
    toast("Lost track of the run", { kind: "error", detail: error.message });
  }
}

async function stop() {
  try {
    render(await api.stopRun());
    toast("Asked the run to stop", { detail: "It ends everything it started within a few seconds." });
    timer = setTimeout(poll, POLL_MS);
  } catch (error) {
    toast("Could not stop the run", { kind: "error", detail: error.message });
  }
}

export const runPanel = {
  /** Submit the saved case where `choice` says, and follow it. */
  async start(choice) {
    const { followResults, rerun, ...target } = choice;
    const status = await api.startRun({ target, rerun });
    show();
    databaseSeen = status.databaseVersion;
    render(status, true);
    timer = setTimeout(poll, POLL_MS);
    return status;
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
    listeners.state = callback;
  },
  /** Called with the run's status whenever its database has more time nodes in it. */
  onProgress(callback) {
    listeners.progress = callback;
  },
  /** Called with the run's status when its Results button is pressed. */
  onResults(callback) {
    listeners.results = callback;
  },
};
