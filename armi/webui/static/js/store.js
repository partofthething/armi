// The document the editor is editing, and everything that watches it.
//
// One rule keeps this honest: the browser is not the authority on what a blueprint is. Every edit
// is applied locally so the screen keeps up with the mouse, then pushed to ARMI, which rebuilds the
// blueprints from scratch and hands back what it actually parsed. If ARMI refuses, the edit is
// rolled back and the reason is shown. That is why you cannot paint a core into a state that will
// not load.

import { api } from "./api.js";
import { debounce } from "./dom.js";

const UNDO_DEPTH = 120;

class Store extends EventTarget {
  constructor() {
    super();
    this.document = null;   // the blueprints, as plain data
    this.geometry = {};     // per-grid layout, computed by ARMI
    this.catalog = null;    // materials, shapes, flags, symmetries
    this.session = null;    // which files are open
    this.settings = null;   // the settings document, once asked for
    this.dirty = false;
    this.lastError = null;
    // Whether there is an edit ARMI has not seen yet. Without this, `sync` would push the document
    // on every call, and a view that syncs while rendering would re-render itself forever.
    this.pending = false;

    this.undoStack = [];
    this.redoStack = [];

    // Painting a core map fires an edit per cell; batching keeps that to one round trip.
    this._push = debounce(() => this._flush(), 180);
  }

  emit(name, detail) {
    this.dispatchEvent(new CustomEvent(name, { detail }));
  }

  on(name, handler) {
    this.addEventListener(name, handler);
    return () => this.removeEventListener(name, handler);
  }

  // -- loading ---------------------------------------------------------------------------------

  async boot() {
    this.session = await api.state();
    this.catalog = await api.catalog();
    if (this.session.blueprints.open) await this.reload();
    this.emit("session");
  }

  async reload() {
    const payload = await api.blueprints();
    this._accept(payload);
    this.undoStack = [];
    this.redoStack = [];
    this.pending = false;
    this.emit("document");
  }

  _accept(payload) {
    this.document = payload.document;
    this.geometry = payload.geometry || {};
    if (payload.state) this.session = payload.state;
    this.dirty = this.session?.blueprints?.dirty ?? this.dirty;
  }

  // -- editing ---------------------------------------------------------------------------------

  /**
   * Change the document.
   *
   * @param {string} label what the change was, shown if it has to be undone
   * @param {function} mutate given a deep copy of the document, changes it in place
   */
  edit(label, mutate) {
    if (!this.document) return;
    const before = structuredClone(this.document);
    const draft = structuredClone(this.document);
    const outcome = mutate(draft);
    if (outcome === false) return;

    this.undoStack.push({ label, document: before });
    if (this.undoStack.length > UNDO_DEPTH) this.undoStack.shift();
    this.redoStack = [];

    this.document = draft;
    this.dirty = true;
    this.pending = true;
    this.emit("document");
    this.emit("dirty");
    this._push();
  }

  undo() {
    if (!this.undoStack.length) return;
    const entry = this.undoStack.pop();
    this.redoStack.push({ label: entry.label, document: this.document });
    this.document = entry.document;
    this.dirty = true;
    this.pending = true;
    this.emit("document");
    this.emit("dirty");
    this._push();
    return entry.label;
  }

  redo() {
    if (!this.redoStack.length) return;
    const entry = this.redoStack.pop();
    this.undoStack.push({ label: entry.label, document: this.document });
    this.document = entry.document;
    this.dirty = true;
    this.pending = true;
    this.emit("document");
    this.emit("dirty");
    this._push();
    return entry.label;
  }

  get undoLabel() {
    return this.undoStack.at(-1)?.label || null;
  }

  get redoLabel() {
    return this.redoStack.at(-1)?.label || null;
  }

  /** Send pending edits now rather than waiting out the debounce. */
  async sync() {
    if (!this.pending) return;
    this._push.cancel();
    await this._flush();
  }

  async _flush() {
    if (!this.document) return;
    const sent = this.document;
    const before = JSON.stringify(sent);
    try {
      const payload = await api.putBlueprints(sent);
      this.lastError = null;
      // A newer edit landed while this was in flight; that one will push its own state.
      if (sent !== this.document) return;
      this.pending = false;
      this._accept(payload);
      this.dirty = true;
      this.emit("validated");
      // ARMI normalises as it parses, so announce a new document only when it actually came back
      // different. Re-rendering after every push would be wasted work at best, and a loop for any
      // view that syncs while it renders.
      if (JSON.stringify(this.document) !== before) this.emit("document");
    } catch (error) {
      this.lastError = error.message;
      // Put the document back to the last thing ARMI accepted, so the editor cannot wander off
      // into a state that will never save.
      this.pending = false;
      const recovered = this.undoStack.pop();
      if (recovered) {
        this.document = recovered.document;
        this.emit("document");
      }
      this.emit("invalid", error.message);
    } finally {
      this.emit("dirty");
    }
  }

  // -- files -----------------------------------------------------------------------------------

  async save(path, flatten = false) {
    await this.sync();
    const result = await api.saveBlueprints(path, flatten);
    this.session = result.state;
    this.dirty = false;
    this.emit("dirty");
    this.emit("session");
    return result.path;
  }

  /**
   * Open a file, whatever kind it turns out to be.
   *
   * Which files a case is made of is a question about their contents, so ARMI answers it: hand it
   * a case settings file and the blueprints that case loads come back too.
   */
  async open(path) {
    const payload = await api.open(path);
    this.settings = null;
    this._accept(payload);
    this.undoStack = [];
    this.redoStack = [];
    this.dirty = false;
    this.pending = false;
    this.emit("document");
    this.emit("session");
    this.emit("dirty");
  }

  async create(geom, symmetry) {
    const payload = await api.newBlueprints(geom, symmetry);
    this._accept(payload);
    this.undoStack = [];
    this.redoStack = [];
    this.pending = false;
    this.emit("document");
    this.emit("session");
    this.emit("dirty");
  }

  // -- convenience over the document -------------------------------------------------------------

  section(name) {
    return (this.document && this.document[name]) || {};
  }

  /** Assembly name to its one or two character core-map specifier. */
  get specifiers() {
    const out = {};
    for (const [name, design] of Object.entries(this.section("assemblies"))) {
      if (name === "heights" || name === "axial mesh points") continue;
      if (design && design.specifier) out[design.specifier] = name;
    }
    return out;
  }

  assemblyNames() {
    return Object.keys(this.section("assemblies")).filter(
      (name) => name !== "heights" && name !== "axial mesh points",
    );
  }

  /** Which grid a block draws its pins on, if any. */
  gridOfBlock(blockName) {
    return this.section("blocks")[blockName]?.["grid name"] || null;
  }

  /** Every block that lays its components out on this grid. */
  blocksUsingGrid(gridName) {
    return Object.entries(this.section("blocks"))
      .filter(([, block]) => block && block["grid name"] === gridName)
      .map(([name]) => name);
  }

  /** Every assembly that puts this block in its stack. */
  assembliesUsingBlock(blockName) {
    return this.assemblyNames().filter((name) =>
      (this.section("assemblies")[name]?.blocks || []).includes(blockName),
    );
  }
}

export const store = new Store();
