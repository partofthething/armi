// Boot, shell wiring and routing.

import { api } from "./api.js";
import { h, fill } from "./dom.js";
import { icon } from "./icons.js";
import { pickPath } from "./picker.js";
import { store } from "./store.js";
import { dialog, field, selectInput, toast } from "./ui.js";

import { coreView } from "./views/core.js";
import { gridsView } from "./views/grids.js";
import { blocksView } from "./views/blocks.js";
import { assembliesView } from "./views/assemblies.js";
import { componentsView } from "./views/components.js";
import { nuclidesView } from "./views/nuclides.js";
import { settingsView } from "./views/settings.js";
import { yamlView } from "./views/yaml.js";

const VIEWS = [
  { id: "core", label: "Core", icon: "core", view: coreView, needsBp: true },
  { id: "grids", label: "Lattices", icon: "grid", view: gridsView, needsBp: true },
  { id: "blocks", label: "Blocks", icon: "block", view: blocksView, needsBp: true },
  { id: "assemblies", label: "Assem", icon: "assembly", view: assembliesView, needsBp: true },
  { id: "components", label: "Parts", icon: "component", view: componentsView, needsBp: true },
  { id: "nuclides", label: "Nuclides", icon: "nuclide", view: nuclidesView, needsBp: true },
  { id: "settings", label: "Settings", icon: "settings", view: settingsView, needsBp: false },
  { id: "yaml", label: "File", icon: "yaml", view: yamlView, needsBp: true },
];

const el = {
  rail: document.getElementById("rail"),
  navigator: document.getElementById("navigator"),
  stage: document.getElementById("stage"),
  inspector: document.getElementById("inspector"),
  appName: document.getElementById("app-name"),
  bpName: document.getElementById("bp-name"),
  csName: document.getElementById("cs-name"),
  saveState: document.getElementById("savestate"),
  statusPrimary: document.getElementById("status-primary"),
  statusSecondary: document.getElementById("status-secondary"),
  statusHint: document.getElementById("status-hint"),
  undo: document.getElementById("act-undo"),
  redo: document.getElementById("act-redo"),
  save: document.getElementById("act-save"),
};

let current = viewFromHash() || "core";
/** Per-view scratch state (which object is selected, which tool is active) survives re-renders. */
const viewState = new Map();

const context = {
  store,
  status,
  toast,
  /** Redraw the current view in place. */
  refresh: () => renderView(),
  /** Jump to another view, optionally telling it what to select. */
  go: (id, state) => {
    if (state) Object.assign(stateFor(id), state);
    setView(id);
  },
  stateFor,
};

function stateFor(id) {
  if (!viewState.has(id)) viewState.set(id, {});
  return viewState.get(id);
}

function status(primary, secondary, hint) {
  if (primary !== undefined) el.statusPrimary.textContent = primary ?? "";
  if (secondary !== undefined) el.statusSecondary.textContent = secondary ?? "";
  if (hint !== undefined) el.statusHint.textContent = hint ?? "";
}

// -- shell ------------------------------------------------------------------------------------

function buildRail() {
  fill(
    el.rail,
    ...VIEWS.map((entry) =>
      h(
        "button.rail__item",
        {
          type: "button",
          id: `rail-${entry.id}`,
          "aria-current": String(entry.id === current),
          onclick: () => setView(entry.id),
        },
        icon(entry.icon),
        h("span", entry.label),
      ),
    ),
  );
}

/**
 * Read the address bar.
 *
 * The hash carries the view and what is selected in it, as `#blocks/fuel z1cra`, so a reload comes
 * back where you were and a link points at the thing you were talking about.
 */
function readHash() {
  const [id, ...rest] = location.hash.replace(/^#/, "").split("/");
  if (!VIEWS.some((entry) => entry.id === id)) return null;
  return { id, selected: rest.length ? decodeURIComponent(rest.join("/")) : null };
}

function viewFromHash() {
  return readHash()?.id || null;
}

function writeHash() {
  const selected = stateFor(current).selected;
  const want = `#${current}${selected ? `/${encodeURIComponent(selected)}` : ""}`;
  if (location.hash !== want) history.replaceState(null, "", `${location.search}${want}`);
}

function setView(id) {
  current = id;
  for (const entry of VIEWS) {
    document.getElementById(`rail-${entry.id}`)?.setAttribute("aria-current", String(entry.id === id));
  }
  renderView();
}

function renderView() {
  const entry = VIEWS.find((each) => each.id === current) || VIEWS[0];
  writeHash();
  if (entry.needsBp && !store.document) {
    fill(el.navigator, h("div.empty", h("strong", "No blueprints open"), "Open a file to get started."));
    fill(
      el.stage,
      h(
        "div.empty",
        { style: { marginTop: "18vh" } },
        h("strong", "Nothing loaded"),
        h("p", "Open an existing blueprints or case settings file, or start a new core from scratch."),
        h(
          "div",
          { style: { display: "flex", gap: "8px", justifyContent: "center", marginTop: "14px" } },
          h("button.btn.btn--primary", { type: "button", onclick: openBlueprints }, "Open a file"),
          h("button.btn", { type: "button", onclick: newBlueprints }, "New blueprints"),
        ),
      ),
    );
    fill(el.inspector);
    status("", "", "");
    return;
  }
  status("", "", "");
  entry.view({ ...context, nav: el.navigator, stage: el.stage, inspector: el.inspector, state: stateFor(entry.id) });
}

function syncChrome() {
  const session = store.session || {};
  el.appName.textContent = session.app ? `${session.app} inputs` : "ARMI inputs";
  el.bpName.textContent = baseName(session.blueprints?.path) || "nothing open";
  el.csName.textContent = baseName(session.settings?.path) || "nothing open";
  el.bpName.title = session.blueprints?.path || "";
  el.csName.title = session.settings?.path || "";
  syncDirty();
}

function syncDirty() {
  const state = store.lastError ? "error" : store.dirty ? "dirty" : "clean";
  el.saveState.dataset.state = state;
  el.saveState.textContent =
    state === "error" ? "Will not load" : state === "dirty" ? "Unsaved changes" : "Saved";
  el.saveState.title = store.lastError || "";
  el.undo.disabled = !store.undoLabel;
  el.redo.disabled = !store.redoLabel;
  el.undo.title = store.undoLabel ? `Undo ${store.undoLabel} (Ctrl+Z)` : "Nothing to undo";
  el.redo.title = store.redoLabel ? `Redo ${store.redoLabel} (Ctrl+Shift+Z)` : "Nothing to redo";
  el.save.disabled = !store.document;
}

function baseName(path) {
  return path ? path.split(/[\\/]/).pop() : null;
}

// -- file actions -----------------------------------------------------------------------------

async function openBlueprints() {
  const path = await pickPath("Open a case, or blueprints on their own", { startDir: store.session?.cwd || null });
  if (!path) return;
  try {
    await store.open(path);
    syncChrome();
    renderView();
    const opened = store.session?.settings?.open && store.session?.blueprints?.open
      ? `Opened ${baseName(store.session.settings.path)} and its blueprints`
      : `Opened ${baseName(path)}`;
    toast(opened, { kind: "ok" });
  } catch (error) {
    toast("Could not open that file", { kind: "error", detail: error.message });
  }
}

async function newBlueprints() {
  const geometries = store.catalog?.geometries || [];
  let geom = geometries[0]?.geom || "hex";
  const symmetrySelect = h("select.input");
  const paint = () => {
    const entry = geometries.find((each) => each.geom === geom) || geometries[0];
    fill(symmetrySelect, ...(entry?.symmetries || []).map((sym) => h("option", { value: sym }, sym)));
  };
  const geomSelect = selectInput({
    value: geom,
    options: geometries.map((entry) => [entry.geom, entry.geom]),
    onChange: (value) => {
      geom = value;
      paint();
    },
  });
  paint();

  const chosen = await dialog({
    title: "New blueprints",
    body: h(
      "div",
      field("Grid geometry", geomSelect),
      field("Core symmetry", symmetrySelect),
      h("p.field__hint", "You get an empty core grid. Blocks, assemblies and lattices come next."),
    ),
    actions: [["Create", "primary", () => ({ geom, symmetry: symmetrySelect.value })]],
  });
  if (!chosen) return;
  await store.create(chosen.geom, chosen.symmetry);
  syncChrome();
  setView("core");
}

async function saveBlueprints() {
  if (!store.document) return;
  try {
    const path = await store.save(store.session?.blueprints?.path || undefined);
    syncChrome();
    toast(`Wrote ${baseName(path)}`, { kind: "ok" });
  } catch (error) {
    // Writing a file that was stitched together from !includes would collapse them all into one,
    // which is a decision for the person who set the files up that way, not for the editor.
    if (error.status === 409) return offerFlatten(error);
    toast("Nothing was written", { kind: "error", detail: error.message });
  }
}

async function offerFlatten(error) {
  const includes = store.session?.blueprints?.includes || [];
  const choice = await dialog({
    title: "This file is built from several",
    body: h(
      "div",
      h("p", { style: { marginTop: 0, color: "var(--text-dim)", lineHeight: 1.7 } },
        `${baseName(store.session?.blueprints?.path)} pulls in ${includes.length} other ` +
        "file" + (includes.length === 1 ? "" : "s") + " with !include. Saving over it writes " +
        "everything into the one file, and the others stop being used."),
      h("div.crumb", includes.join("\n")),
    ),
    actions: [
      ["Save to a new file", "primary", async () => "new"],
      ["Flatten into one file", "danger", async () => "flatten"],
    ],
  });
  if (choice === "flatten") {
    try {
      const path = await store.save(store.session?.blueprints?.path, true);
      syncChrome();
      toast(`Flattened into ${baseName(path)}`, { kind: "ok" });
    } catch (again) {
      toast("Nothing was written", { kind: "error", detail: again.message });
    }
  } else if (choice === "new") {
    await saveBlueprintsAs();
  }
}

/** Ask for a directory and a name, then write there. */
async function saveBlueprintsAs() {
  const here = store.session?.cwd || ".";
  const nameInput = h("input.input.mono", { type: "text",
    value: baseName(store.session?.blueprints?.path) || "blueprints.yaml" });
  const dirInput = h("input.input.mono", { type: "text", value: here });
  const target = await dialog({
    title: "Save blueprints as",
    body: h("div", field("Folder", dirInput), field("File name", nameInput)),
    actions: [["Save", "primary", () =>
      (nameInput.value.trim() ? `${dirInput.value.replace(/\/+$/, "")}/${nameInput.value.trim()}` : null)]],
  });
  if (!target) return;
  try {
    const path = await store.save(target);
    syncChrome();
    toast(`Wrote ${baseName(path)}`, { kind: "ok" });
  } catch (error) {
    toast("Nothing was written", { kind: "error", detail: error.message });
  }
}

/** Same picker, same endpoint; it just lands you on the settings afterwards. */
async function openSettings() {
  const path = await pickPath("Open case settings", { startDir: store.session?.cwd || null });
  if (!path) return;
  try {
    await store.open(path);
    syncChrome();
    setView("settings");
  } catch (error) {
    toast("Could not open that file", { kind: "error", detail: error.message });
  }
}

// -- keyboard ---------------------------------------------------------------------------------

function onKeyDown(event) {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName);
  const accel = event.ctrlKey || event.metaKey;

  if (accel && event.key.toLowerCase() === "s") {
    event.preventDefault();
    if (event.shiftKey) saveBlueprintsAs();
    else saveBlueprints();
    return;
  }
  if (accel && event.key.toLowerCase() === "z") {
    event.preventDefault();
    const label = event.shiftKey ? store.redo() : store.undo();
    if (label) toast(`${event.shiftKey ? "Redid" : "Undid"} ${label}`, { ms: 1600 });
    return;
  }
  if (typing || accel) return;

  const index = Number(event.key);
  if (index >= 1 && index <= VIEWS.length) {
    setView(VIEWS[index - 1].id);
  }
}

// -- start ------------------------------------------------------------------------------------

el.undo.onclick = () => store.undo();
el.redo.onclick = () => store.redo();
el.save.onclick = (event) => (event.shiftKey ? saveBlueprintsAs() : saveBlueprints());
document.getElementById("file-blueprints").onclick = openBlueprints;
document.getElementById("file-settings").onclick = openSettings;
document.addEventListener("keydown", onKeyDown);
window.addEventListener("hashchange", () => {
  const target = readHash();
  if (!target) return;
  if (target.selected) stateFor(target.id).selected = target.selected;
  if (target.id !== current || target.selected) setView(target.id);
});

store.on("dirty", syncDirty);
store.on("session", syncChrome);
store.on("document", () => renderView());
store.on("invalid", (event) => {
  toast("ARMI would not accept that change, so it was undone", { kind: "error", detail: event.detail });
});

const opening = readHash();
if (opening?.selected) stateFor(opening.id).selected = opening.selected;

buildRail();
syncChrome();

store
  .boot()
  .then(() => {
    syncChrome();
    renderView();
  })
  .catch((error) => {
    fill(
      el.stage,
      h("div.empty", { style: { marginTop: "18vh" } },
        h("strong", "Could not reach the ARMI process"),
        error.message),
    );
  });
