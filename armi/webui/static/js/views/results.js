// Results: a core's parameters through time.
//
// Pick a parameter on the left, drag the slider underneath to move through time and the one on the
// right to move the cut, and scroll to zoom. Close enough in, blocks open up into their pins, ducts
// and coolant, each coloured by its own value when the parameter is a component one.
//
// Everything that moves with the sliders is computed here, in the browser, from arrays already
// in hand: a parameter's whole history arrives in one request, so scrubbing through sixty years
// is a matter of indexing, not of asking the server. Only component values travel per node, and
// those are prefetched around wherever you are.

import { api } from "../api.js";
import { h, fill } from "../dom.js";
import { pickPath } from "../picker.js";
import { store } from "../store.js";
import { checkbox, toast } from "../ui.js";
import { CoreCanvas, chord } from "../results/canvas.js";
import { COLORMAPS, colormap, formatValue, paintBar, scaler } from "../results/colormaps.js";
import { Results } from "../results/data.js";

const PREFERENCES = "armi.results.preferences";
const DETAIL_AT = 230; // pixels across a block before its insides are drawn
const PREFETCH = 3;
const DEFAULT_PARAMS = ["pdens", "power", "percentBu", "flux"];

let viewer = null;

export function resultsView(ctx) {
  const info = store.session?.results;
  if (!info?.open) {
    viewer?.destroy();
    viewer = null;
    renderClosed(ctx);
    return;
  }
  if (!viewer || viewer.path !== info.path) {
    viewer?.destroy();
    viewer = new ResultsViewer(info.path);
  }
  viewer.attach(ctx);
}

function renderClosed(ctx) {
  fill(ctx.nav, h("div.empty", h("strong", "No results open"), "Open an ARMI database (.h5) to look at it."));
  fill(
    ctx.stage,
    h(
      "div.empty",
      { style: { marginTop: "18vh" } },
      h("strong", "No results loaded"),
      h("p", "Open a database written by an ARMI run, or a case settings file that has been run."),
      h("button.btn.btn--primary", { type: "button", onclick: () => openResults(ctx) }, "Open a database"),
    ),
  );
  fill(ctx.inspector);
}

export async function openResults(ctx) {
  const path = await pickPath("Open a results database", { startDir: store.session?.cwd || null });
  if (!path) return;
  try {
    const payload = /\.(h5|hdf5)$/i.test(path) ? await api.openResults(path) : await api.open(path);
    store.session = payload.state || (await api.state());
    store.emit("session");
    if (!store.session.results?.open) {
      toast("That case has no results database next to it", { kind: "error" });
      return;
    }
    ctx.go("results");
  } catch (error) {
    toast("Could not open those results", { kind: "error", detail: error.message });
  }
}

function loadPreferences() {
  try {
    return JSON.parse(localStorage.getItem(PREFERENCES) || "{}") || {};
  } catch {
    return {};
  }
}

function savePreferences(state) {
  const keep = ["cmap", "reversed", "log", "rangeMode", "showValues", "textPx", "digits", "fullCore", "detail",
    "aggregate", "follow", "plane", "fps"];
  try {
    localStorage.setItem(PREFERENCES, JSON.stringify(Object.fromEntries(keep.map((key) => [key, state[key]]))));
  } catch {
    // Private windows and blocked storage: preferences just do not stick.
  }
}

class ResultsViewer {
  constructor(path) {
    this.path = path;
    this.results = null;
    this.error = null;
    this.state = {
      t: 0,
      param: null, // {level, name, index, reduce, nuclide}
      plane: "xy",
      slice: { xy: null, xz: null, yz: null }, // index into each plane's stops
      cmap: "viridis",
      reversed: false,
      log: false,
      rangeMode: "history",
      fixed: { min: null, max: null },
      showValues: true,
      textPx: 11,
      digits: 3,
      fullCore: true,
      detail: "auto",
      aggregate: "max",
      follow: "assembly",
      selected: null, // {name, k, i, j, z}
      fps: 8,
      levelTab: "block",
      search: "",
      ...loadPreferences(),
    };
    this.series = null;
    this.canvas = new CoreCanvas({
      onHover: (hit) => this._hover(hit),
      onSelect: (hit) => this._select(hit),
      onView: () => this._viewChanged(),
      onDrawn: (ms) => {
        this.ui && (this.ui.perf.textContent = `${ms.toFixed(1)} ms`);
      },
    });
    this._build();
    this._keys = (event) => this._onKey(event);
    document.addEventListener("keydown", this._keys);
    this._loading = this._load();
  }

  destroy() {
    this._stop();
    document.removeEventListener("keydown", this._keys);
    this.canvas.destroy();
    this.results?.close();
  }

  get attached() {
    return this.ui && document.body.contains(this.ui.stage);
  }

  attach(ctx) {
    this.ctx = ctx;
    fill(ctx.nav, this.ui.nav);
    fill(ctx.stage, this.ui.stage);
    fill(ctx.inspector, this.ui.inspector);
    this.canvas.request();
    if (this.results) this.render();
  }

  // -- loading ----------------------------------------------------------------------------------

  async _load() {
    this.ui.message.textContent = "Reading the database…";
    try {
      this.results = await Results.load();
    } catch (error) {
      this.error = error;
      this.ui.message.textContent = `Could not read this database: ${error.message}`;
      return;
    }
    const results = this.results;
    results.onChange((what) => {
      if (what === "positions") this._updateProgress();
      if (!this.attached) return;
      if (what === "positions" && !results.frameAt(this.state.t)) return;
      this.render();
    });

    const state = this.state;
    state.t = Math.min(state.t, results.steps.length - 1);
    if (!state.param) state.param = this._defaultParam();
    state.slice.xy ??= nearestIndex(results.zStops, (results.zRange[0] + results.zRange[1]) / 2);

    this._buildNavigator();
    this._buildTimeline();
    await results.ensurePositions(state.t);
    this.ui.message.textContent = "";
    await this._loadParam();
    if (!this._sliceChosen) state.slice.xy = this._peakSlice();
    this.canvas.fit();
    results.prefetchPositions(state.t);
  }

  /** The height stop where the parameter on show is largest across the core, to open on. */
  _peakSlice() {
    const results = this.results;
    const stops = results.zStops;
    if (!this.series || this.state.param.level !== "block") return this.state.slice.xy ?? 0;
    // Look at the middle of the history: the first node is often before anything has happened.
    const t = Math.floor(results.steps.length / 2);
    const topo = results.topologyAt(t);
    const base = results.blockOffset[t];
    let best = this.state.slice.xy ?? 0;
    let bestValue = -Infinity;
    stops.forEach((z, index) => {
      let total = 0;
      for (let a = 0; a < topo.assemblyCount; a += 1) {
        const b = results.blockAtHeight(t, a, z);
        const value = b >= 0 ? this.series[base + b] : NaN;
        if (value === value) total += value;
      }
      if (total > bestValue) {
        bestValue = total;
        best = index;
      }
    });
    return best;
  }

  _defaultParam() {
    const names = new Set(this.results.parameters.block.map((entry) => entry.name));
    const name = DEFAULT_PARAMS.find((each) => names.has(each)) || this.results.parameters.block[0]?.name;
    return { level: "block", name };
  }

  async _loadParam() {
    const param = this.state.param;
    const results = this.results;
    this.series = null;
    this.componentRange = null;
    results.forgetComponents();
    this._caching = null;
    this._updateProgress();
    if (!param?.name && !param?.nuclide) return this.render();
    this._busy(true);
    try {
      if (param.level === "component") {
        await results.designs(results.steps[this.state.t].topology);
        await results.components(this.state.t, param.name, param.nuclide);
        // Then everything else, nearest first, so dragging the time slider never waits.
        results.prefetchComponents(param.name, param.nuclide, this.state.t, (done, total) => {
          if (this.state.param !== param) return;
          this._caching = done < total ? `caching ${done}/${total}` : null;
          this._updateProgress();
        }).catch(() => {});
        results.componentRange(param.name, param.nuclide).then((range) => {
          if (this.state.param === param) {
            this.componentRange = range;
            this.render();
          }
        });
      } else {
        this.series = await results.series(param.level, param.name, param.index, param.reduce);
        this.seriesKey = `${param.level}|${param.name}|${param.index ?? ""}|${param.reduce ?? ""}`;
      }
    } catch (error) {
      toast(`Could not load ${param.name || param.nuclide}`, { kind: "error", detail: error.message });
    }
    this._busy(false);
    this._buildNavigator();
    this._syncToolbar();
    this.render();
  }

  _busy(on) {
    this.ui.stage.classList.toggle("results--busy", on);
  }

  _updateProgress() {
    const results = this.results;
    const loaded = results.positionsLoaded;
    const total = results.steps.length;
    const parts = [loaded < total ? `indexing ${loaded}/${total}` : null, this._caching];
    this.ui.progress.textContent = parts.filter(Boolean).join(" · ");
  }

  // -- layout -----------------------------------------------------------------------------------

  _build() {
    const ui = {};
    this.ui = ui;
    ui.nav = h("div.results-nav");
    ui.inspector = h("div.results-inspector");

    ui.message = h("div.results-message");
    ui.tooltip = h("div.results-tooltip", { hidden: true });
    this.canvas.node.appendChild(ui.tooltip);
    this.canvas.node.appendChild(ui.message);

    ui.sliceLabel = h("div.results-slice__label");
    ui.sliceSlider = h("input.results-slice__range", {
      type: "range", min: 0, max: 0, step: 1, value: 0,
      oninput: (event) => this._setSlice(Number(event.target.value)),
    });
    ui.sliceName = h("div.results-slice__name");
    ui.slice = h("div.results-slice", ui.sliceName, ui.sliceSlider, ui.sliceLabel);

    ui.toolbar = h("div.stage__bar.results-bar");
    ui.colorbar = h("canvas.results-colorbar__bar", { width: 256, height: 10 });
    ui.colorMin = h("span.results-colorbar__end");
    ui.colorMax = h("span.results-colorbar__end");
    ui.colorUnits = h("span.results-colorbar__units");
    ui.colorRow = h("div.results-colorbar", ui.colorMin, ui.colorbar, ui.colorMax, ui.colorUnits);

    ui.timeSlider = h("input.results-time__range", {
      type: "range", min: 0, max: 0, step: 1, value: 0,
      oninput: (event) => this.setTime(Number(event.target.value)),
    });
    ui.timeLabel = h("span.results-time__label");
    ui.play = h("button.btn.btn--sm.results-time__play", { type: "button", title: "Play (Space)",
      onclick: () => this._togglePlay() }, "▶");
    ui.progress = h("span.results-time__progress");
    ui.perf = h("span.results-time__perf", { title: "Time to draw the last frame" });
    ui.time = h(
      "div.results-time",
      ui.play,
      h("button.btn.btn--sm", { type: "button", title: "Previous node (←)", onclick: () => this.step(-1) }, "‹"),
      h("button.btn.btn--sm", { type: "button", title: "Next node (→)", onclick: () => this.step(1) }, "›"),
      ui.timeSlider,
      ui.timeLabel,
    );

    ui.stage = h(
      "div.results-stage",
      ui.toolbar,
      h("div.results-main", this.canvas.node, ui.slice),
      h("div.results-foot", ui.colorRow, h("span.spacer"), ui.progress, ui.perf),
      ui.time,
    );
    this._buildToolbar();
    this._buildInspector();
  }

  _buildToolbar() {
    const state = this.state;
    const ui = this.ui;
    const segmented = (options, value, onChange) =>
      h("div.segmented", ...options.map(([key, label, title]) =>
        h("button", { type: "button", title: title || "", "aria-pressed": String(value === key),
          onclick: () => onChange(key) }, label)));

    const planes = segmented([["xy", "XY", "Plan view (top down)"], ["xz", "XZ", "Section along x"],
      ["yz", "YZ", "Section along y"]], state.plane, (plane) => {
      state.plane = plane;
      this._changed();
      this._buildToolbar();
      this._syncSlice();
    });

    const cmap = h("select.input.results-select", { title: "Colour map",
      onchange: (event) => { state.cmap = event.target.value; this._changed(); } },
    ...COLORMAPS.map((name) => h("option", { value: name, selected: name === state.cmap }, name)));
    const reverse = h("button.btn.btn--sm", { type: "button", title: "Reverse the colour map",
      "aria-pressed": String(state.reversed),
      onclick: () => { state.reversed = !state.reversed; this._changed(); this._buildToolbar(); } }, "⇄");
    const scale = segmented([["lin", "lin"], ["log", "log"]], state.log ? "log" : "lin", (value) => {
      state.log = value === "log";
      this._changed();
      this._buildToolbar();
    });
    const range = h("select.input.results-select", { title: "What the colour scale spans",
      onchange: (event) => { state.rangeMode = event.target.value; this._changed(); this._buildToolbar(); } },
    ...[["history", "All time"], ["node", "This node"], ["fixed", "Fixed"]].map(([key, label]) =>
      h("option", { value: key, selected: key === state.rangeMode }, label)));
    const fixedInputs = state.rangeMode === "fixed"
      ? [
          h("input.input.input--num.results-num", { type: "number", step: "any", placeholder: "min",
            value: state.fixed.min ?? "", onchange: (event) => {
              state.fixed.min = event.target.value === "" ? null : Number(event.target.value);
              this._changed();
            } }),
          h("input.input.input--num.results-num", { type: "number", step: "any", placeholder: "max",
            value: state.fixed.max ?? "", onchange: (event) => {
              state.fixed.max = event.target.value === "" ? null : Number(event.target.value);
              this._changed();
            } }),
        ]
      : [];

    const values = checkbox({ label: "Values", checked: state.showValues, onChange: (on) => {
      state.showValues = on;
      this._changed();
    } });
    const text = h("label.results-text", { title: "Size of the printed values" },
      h("span", "Aa"),
      h("input", { type: "range", min: 6, max: 28, step: 1, value: state.textPx,
        oninput: (event) => { state.textPx = Number(event.target.value); this._changed(); } }));
    const digits = h("select.input.results-select", { title: "Significant figures",
      onchange: (event) => { state.digits = Number(event.target.value); this._changed(); } },
    ...[2, 3, 4, 5, 6].map((n) => h("option", { value: n, selected: n === state.digits }, `${n} sig`)));

    const symmetric = this.results && [...this.results.frames.values()].some((frame) => frame.symmetric);
    const full = symmetric
      ? checkbox({ label: "Full core", checked: state.fullCore, onChange: (on) => {
          state.fullCore = on;
          this._changed();
          this._syncSlice();
        } })
      : null;

    const detail = h("select.input.results-select", { title: "Draw block insides when zoomed in",
      onchange: (event) => { state.detail = event.target.value; this._changed(); } },
    h("option", { value: "auto", selected: state.detail === "auto" }, "Pins: zoom"),
    h("option", { value: "off", selected: state.detail === "off" }, "Pins: off"));

    const param = state.param;
    const extra = [];
    const entry = param && this._paramEntry(param);
    if (param?.level === "component") {
      extra.push(h("select.input.results-select", { title: "How a block's components combine when zoomed out",
        onchange: (event) => { state.aggregate = event.target.value; this._changed(); } },
      ...[["max", "block max"], ["mean", "block mean"], ["min", "block min"]].map(([key, label]) =>
        h("option", { value: key, selected: key === state.aggregate }, label))));
    } else if (entry?.shape?.length) {
      const width = entry.shape.reduce((a, b) => a * b, 1);
      extra.push(h("select.input.results-select", { title: `${entry.name} has ${width} values per object`,
        onchange: (event) => {
          const value = event.target.value;
          const next = { ...param, index: null, reduce: null };
          if (/^\d+$/.test(value)) next.index = Number(value);
          else next.reduce = value;
          this.setParam(next);
        } },
      ...["max", "sum", "mean", "min"].map((key) => h("option", { value: key, selected: param.reduce === key }, key)),
      ...Array.from({ length: Math.min(width, 400) }, (_, k) =>
        h("option", { value: String(k), selected: param.index === k }, `[${k}]`))));
    }

    fill(
      this.ui.toolbar,
      planes,
      h("span.results-bar__sep"),
      cmap,
      reverse,
      scale,
      range,
      ...fixedInputs,
      h("span.results-bar__sep"),
      values,
      text,
      digits,
      h("span.results-bar__sep"),
      full,
      detail,
      ...extra,
      h("span.spacer"),
      h("button.btn.btn--sm", { type: "button", title: "Fit the core to the window (double-click)",
        onclick: () => this.canvas.fit() }, "Fit"),
    );
  }

  _syncToolbar() {
    this._buildToolbar();
  }

  _buildTimeline() {
    const results = this.results;
    this.ui.timeSlider.max = String(results.steps.length - 1);
    this.ui.timeSlider.value = String(this.state.t);
    this._syncSlice();
  }

  // -- navigator: the parameter list -------------------------------------------------------------

  _paramEntry(param) {
    if (!this.results || !param) return null;
    const list = this.results.parameters[param.level] || [];
    return list.find((entry) => entry.name === param.name) || null;
  }

  _buildNavigator() {
    const results = this.results;
    if (!results) return;
    const state = this.state;
    const tab = state.levelTab;
    const search = h("input.searchbox", { type: "search", placeholder: "Filter parameters", value: state.search,
      oninput: (event) => {
        state.search = event.target.value;
        renderList();
      } });
    const tabs = h("div.segmented.results-tabs", ...[["block", "Block"], ["assembly", "Assembly"],
      ["component", "Component"]].map(([key, label]) =>
      h("button", { type: "button", "aria-pressed": String(tab === key),
        onclick: () => { state.levelTab = key; this._buildNavigator(); } }, label)));
    const list = h("div.results-params");

    const renderList = () => {
      const needle = state.search.trim().toLowerCase();
      const match = (text) => !needle || text.toLowerCase().includes(needle);
      const current = state.param;
      const rows = [];
      const entries = results.parameters[tab] || [];
      const plain = entries.filter((entry) => entry.group !== "Number densities" && match(entry.name));
      const densities = entries.filter((entry) => entry.group === "Number densities" && match(entry.name));
      const item = (label, meta, param, title) =>
        h("button.item", {
          type: "button", title: title || "",
          "aria-selected": String(!!current && current.level === param.level && current.name === param.name &&
            (current.nuclide || null) === (param.nuclide || null)),
          onclick: () => this.setParam(param),
        }, h("span.item__name", label), meta ? h("span.item__meta", meta) : null);

      for (const entry of plain) {
        rows.push(item(entry.name, entry.units && entry.units !== "None" ? entry.units : (entry.shape?.length ? `×${entry.shape.join("×")}` : ""),
          { level: tab, name: entry.name }, entry.description));
      }
      if (tab === "component") {
        const nuclides = (results.parameters.componentNuclides || []).filter(match);
        if (nuclides.length) rows.push(h("div.results-params__group", "Number densities"));
        for (const nuclide of nuclides) {
          rows.push(item(nuclide, "1/bn-cm", { level: "component", name: "numberDensities", nuclide },
            `Number density of ${nuclide} in each component`));
        }
      } else if (densities.length) {
        rows.push(h("div.results-params__group", "Number densities"));
        for (const entry of densities) rows.push(item(entry.name.replace(/^n/, ""), "1/bn-cm", { level: tab, name: entry.name }));
      }
      fill(list, rows.length ? rows : h("div.empty", "Nothing matches."));
    };
    renderList();

    fill(
      this.ui.nav,
      h("div.panel__head", h("span.panel__title", "Parameters"),
        h("span.panel__count", `${results.steps.length} nodes`)),
      h("div.results-nav__controls", tabs, search),
      h("div.panel__body", list),
    );
  }

  // -- inspector --------------------------------------------------------------------------------

  _buildInspector() {
    const ui = this.ui;
    ui.paramHead = h("div.results-param");
    ui.selection = h("div.results-selection");
    ui.chart = h("canvas.results-chart", { width: 300, height: 150 });
    ui.chartNote = h("div.field__hint");
    ui.chart.addEventListener("click", (event) => this._chartClick(event));
    ui.chart.addEventListener("mousemove", (event) => this._chartHover(event));
    ui.followBox = h("div.results-follow");
    ui.blockTable = h("div.results-blockparams");
    ui.blockFilter = h("input.searchbox", { type: "search", placeholder: "Filter", oninput: () => this._renderBlockTable() });
    fill(
      ui.inspector,
      h("div.panel__head", h("span.panel__title", "Results"), h("span.panel__count.results-case")),
      h(
        "div.panel__body",
        { style: { padding: 0 } },
        h("section.group", ui.paramHead),
        h("section.group", h("h3.group__title", "Selected", ui.followBox), ui.selection),
        h("section.group", h("h3.group__title", "Over time"), ui.chart, ui.chartNote),
        h("section.group", h("h3.group__title", "Block at this node"), ui.blockFilter, ui.blockTable),
      ),
    );
  }

  _renderInspector() {
    const results = this.results;
    const state = this.state;
    const ui = this.ui;
    ui.inspector.querySelector(".results-case").textContent = results.caseTitle;
    const param = state.param;
    const entry = this._paramEntry(param);
    fill(
      ui.paramHead,
      h("div.results-param__name", param?.nuclide ? `${param.nuclide} number density` : param?.name || "—",
        param?.index != null ? `[${param.index}]` : param?.reduce ? ` (${param.reduce})` : ""),
      h("div.results-param__meta", `${param?.level || ""} parameter`, entry?.units && entry.units !== "None" ? ` · ${entry.units}` : ""),
      entry?.description ? h("div.results-param__desc", entry.description) : null,
    );

    fill(
      ui.followBox,
      h("div.segmented", ...[["assembly", "Assembly", "Follow the assembly as it is shuffled"],
        ["location", "Location", "Stay at this core position"]].map(([key, label, title]) =>
        h("button", { type: "button", title, "aria-pressed": String(state.follow === key),
          onclick: () => { state.follow = key; savePreferences(state); this.render(); } }, label))),
    );

    const where = this._resolveSelection(state.t);
    if (!state.selected) {
      fill(ui.selection, h("div.field__hint", "Click a block to follow it through time."));
    } else if (!where) {
      fill(ui.selection, h("div.field__hint", state.follow === "assembly"
        ? `${state.selected.name} is not in the core at this node.`
        : "Nothing sits at this position at this node."));
    } else {
      const topo = results.topologyAt(state.t);
      const frame = results.frameAt(state.t);
      const base = results.blockOffset[state.t];
      const i = frame?.i[where.a];
      const j = frame?.j[where.a];
      const rows = [
        ["assembly", topo.assemblyNames[where.a]],
        ["block", where.b >= 0 ? topo.blockNames[where.b] : "—"],
        ["position", i !== undefined ? `(${i}, ${j})${results.geometry.shape === "hex" ? ` ring ${hexRing(i, j)}` : ""}` : "…"],
        ["height", where.b >= 0 ? `${formatValue(results.zbottom[base + where.b], 4)} to ${formatValue(results.ztop[base + where.b], 4)} cm` : "—"],
        ["value", formatValue(this._valueAt(state.t, where.a, where.b), 5)],
      ];
      if (where.componentName && param?.level === "component") rows.splice(2, 0, ["component", where.componentName]);
      fill(ui.selection, h("dl.results-kv", ...rows.flatMap(([key, value]) => [h("dt", key), h("dd.mono", value)])));
    }
    this._renderChart();
    this._requestBlockTable();
  }

  _requestBlockTable() {
    clearTimeout(this._blockTimer);
    this._blockTimer = setTimeout(async () => {
      const where = this._resolveSelection(this.state.t);
      if (!where || where.b < 0) {
        this.blockDetail = null;
        this._renderBlockTable();
        return;
      }
      const key = `${this.state.t}|${where.b}`;
      if (this._blockKey === key) return;
      this._blockKey = key;
      this._blockCache ??= new Map();
      let detail = this._blockCache.get(key);
      if (!detail) {
        try {
          detail = await api.resultsBlock(this.results.steps[this.state.t].key, where.b);
          this._blockCache.set(key, detail);
          if (this._blockCache.size > 400) this._blockCache.delete(this._blockCache.keys().next().value);
        } catch {
          detail = null;
        }
      }
      if (this._blockKey !== key) return;
      this.blockDetail = detail;
      this._renderBlockTable();
    }, 60);
  }

  _renderBlockTable() {
    const detail = this.blockDetail;
    const table = this.ui.blockTable;
    if (!detail) {
      fill(table, h("div.field__hint", "Select a block to list its parameters."));
      return;
    }
    const needle = this.ui.blockFilter.value.trim().toLowerCase();
    const rows = Object.entries(detail.params)
      .filter(([name]) => !needle || name.toLowerCase().includes(needle))
      .sort(([a], [b]) => a.localeCompare(b));
    fill(
      table,
      h("table.table",
        h("tbody", ...rows.map(([name, value]) =>
          h("tr", {
            class: this.state.param?.level === "block" && this.state.param.name === name ? "is-current" : "",
            title: typeof value === "number" ? `Show ${name}` : "",
            onclick: typeof value === "number" ? () => this.setParam({ level: "block", name }) : null,
          }, h("td", name), h("td.num.mono", typeof value === "number" ? formatValue(value, 5) : String(value)))))),
    );
  }

  // -- the history chart ------------------------------------------------------------------------

  /** Values of the selection at every node: from the series in hand, or the server for components. */
  _history() {
    const results = this.results;
    const state = this.state;
    if (!state.selected || !state.param) return null;
    const n = results.steps.length;
    const values = new Float64Array(n).fill(NaN);
    const param = state.param;
    if (param.level === "component") {
      const key = this._componentHistoryKey();
      if (this._componentHistory?.key === key) return this._componentHistory.values;
      this._fetchComponentHistory(key);
      return null;
    }
    if (!this.series) return null;
    for (let t = 0; t < n; t += 1) {
      const where = this._resolveSelection(t);
      if (!where) continue;
      values[t] = this._seriesValue(t, where.a, where.b);
    }
    return values;
  }

  _componentHistoryKey() {
    const state = this.state;
    const blocks = [];
    for (let t = 0; t < this.results.steps.length; t += 1) {
      const where = this._resolveSelection(t);
      blocks.push(where && where.b >= 0 ? where.b : -1);
    }
    return JSON.stringify([state.param.name, state.param.nuclide || "", state.selected.componentName || "", blocks,
      state.aggregate]);
  }

  async _fetchComponentHistory(key) {
    if (this._componentHistoryPending === key) return;
    this._componentHistoryPending = key;
    const [param, nuclide, component, blocks, aggregate] = JSON.parse(key);
    try {
      const values = await api.resultsComponentHistory(param, nuclide, component, blocks, aggregate);
      if (this._componentHistoryPending !== key) return;
      this._componentHistory = { key, values };
      this._renderChart();
    } catch (error) {
      this.ui.chartNote.textContent = error.message;
    }
  }

  _renderChart() {
    const canvas = this.ui.chart;
    const width = Math.max(200, (this.ui.inspector.clientWidth || 320) - 26);
    const height = 150;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const context = canvas.getContext("2d");
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);

    const values = this._history();
    this._chartValues = values;
    const note = this.ui.chartNote;
    if (!this.state.selected) {
      note.textContent = "Select a block to see it over time.";
      return;
    }
    if (!values) {
      note.textContent = "Loading…";
      return;
    }
    const results = this.results;
    const times = results.steps.map((step, t) => (step.time ?? t));
    let lo = Infinity;
    let hi = -Infinity;
    for (const v of values) {
      if (Number.isNaN(v)) continue;
      lo = Math.min(lo, v);
      hi = Math.max(hi, v);
    }
    if (!Number.isFinite(lo)) {
      note.textContent = "No values for this selection.";
      return;
    }
    if (hi === lo) {
      hi += Math.abs(hi) * 0.05 || 1;
      lo -= Math.abs(lo) * 0.05 || 1;
    }
    const pad = { left: 52, right: 8, top: 8, bottom: 20 };
    const t0 = times[0];
    const t1 = times[times.length - 1] === t0 ? t0 + 1 : times[times.length - 1];
    const px = (time) => pad.left + ((time - t0) / (t1 - t0)) * (width - pad.left - pad.right);
    const py = (v) => pad.top + (1 - (v - lo) / (hi - lo)) * (height - pad.top - pad.bottom);
    this._chartGeometry = { px, times, pad, width };

    context.font = `10px ${getComputedStyle(document.body).getPropertyValue("--mono") || "monospace"}`;
    context.fillStyle = "#8d97a5";
    context.strokeStyle = "#2b333f";
    context.lineWidth = 1;
    context.textAlign = "right";
    context.textBaseline = "middle";
    for (const v of [lo, (lo + hi) / 2, hi]) {
      context.beginPath();
      context.moveTo(pad.left, py(v));
      context.lineTo(width - pad.right, py(v));
      context.stroke();
      context.fillText(formatValue(v, 3), pad.left - 5, py(v));
    }
    context.textAlign = "center";
    context.textBaseline = "top";
    const unit = results.steps[0].time != null ? " yr" : "";
    context.fillText(`${formatValue(t0, 3)}${unit}`, pad.left + 10, height - pad.bottom + 5);
    context.fillText(`${formatValue(t1, 3)}${unit}`, width - pad.right - 14, height - pad.bottom + 5);

    context.strokeStyle = "#2a78d6";
    context.lineWidth = 1.5;
    context.beginPath();
    let pen = false;
    values.forEach((v, t) => {
      if (Number.isNaN(v)) {
        pen = false;
        return;
      }
      if (pen) context.lineTo(px(times[t]), py(v));
      else context.moveTo(px(times[t]), py(v));
      pen = true;
    });
    context.stroke();

    const now = this.state.t;
    context.strokeStyle = "#ffb454";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(px(times[now]), pad.top);
    context.lineTo(px(times[now]), height - pad.bottom);
    context.stroke();
    if (!Number.isNaN(values[now])) {
      context.fillStyle = "#ffb454";
      context.beginPath();
      context.arc(px(times[now]), py(values[now]), 3, 0, 2 * Math.PI);
      context.fill();
    }
    note.textContent = this.state.follow === "assembly"
      ? `Following ${this.state.selected.name} wherever it is moved. Click to jump in time.`
      : "Following this core position. Click to jump in time.";
  }

  _chartStep(event) {
    const geometry = this._chartGeometry;
    if (!geometry) return null;
    const rect = this.ui.chart.getBoundingClientRect();
    const x = event.clientX - rect.left;
    let best = 0;
    geometry.times.forEach((time, t) => {
      if (Math.abs(geometry.px(time) - x) < Math.abs(geometry.px(geometry.times[best]) - x)) best = t;
    });
    return best;
  }

  _chartClick(event) {
    const t = this._chartStep(event);
    if (t !== null) this.setTime(t);
  }

  _chartHover(event) {
    const t = this._chartStep(event);
    if (t === null || !this._chartValues) return;
    const step = this.results.steps[t];
    this.ui.chart.title = `${stepLabel(step)}: ${formatValue(this._chartValues[t], 5)}`;
  }

  // -- state changes ----------------------------------------------------------------------------

  setParam(param) {
    this.state.param = param;
    this.state.levelTab = param.level;
    this._componentHistory = null;
    this._loadParam();
  }

  setTime(t) {
    const results = this.results;
    if (!results) return;
    const n = results.steps.length;
    this.state.t = Math.max(0, Math.min(n - 1, t));
    this.ui.timeSlider.value = String(this.state.t);
    const param = this.state.param;
    if (!results.frameAt(this.state.t)) results.ensurePositions(this.state.t);
    if (param?.level === "component") {
      const topology = results.steps[this.state.t].topology;
      results.designs(topology);
      for (let k = 0; k <= PREFETCH; k += 1) {
        for (const tt of [this.state.t + k, this.state.t - k]) {
          if (tt >= 0 && tt < n) results.components(tt, param.name, param.nuclide).catch(() => {});
        }
      }
    }
    this.render();
  }

  step(delta) {
    const n = this.results?.steps.length || 1;
    this.setTime((this.state.t + delta + n) % n);
  }

  _togglePlay() {
    if (this._timer) return this._stop();
    this.ui.play.textContent = "❚❚";
    this._timer = setInterval(() => {
      if (!this.attached) return this._stop();
      this.step(1);
    }, 1000 / this.state.fps);
  }

  _stop() {
    clearInterval(this._timer);
    this._timer = null;
    if (this.ui) this.ui.play.textContent = "▶";
  }

  _changed() {
    savePreferences(this.state);
    this.render();
  }

  _setSlice(index) {
    this._sliceChosen = true;
    this.state.slice[this.state.plane] = index;
    this.render();
  }

  _viewChanged() {
    // Crossing the zoom where block insides start to show needs data that may not be here yet.
    const view = this.canvas.views.xy;
    const geometry = this.results?.geometry;
    if (geometry && view && this.state.plane === "xy" && this.state.detail !== "off") {
      const pitch = geometry.shape === "hex" ? geometry.pitch : Math.min(...geometry.pitch);
      if (view.ppc * pitch >= DETAIL_AT) this._ensureDesigns();
    }
    this._positionTooltip();
  }

  _ensureDesigns() {
    const results = this.results;
    const topology = results.steps[this.state.t].topology;
    if (!results.designsReady(topology)) results.designs(topology).catch(() => {});
  }

  _onKey(event) {
    if (!this.attached || /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.tagName) && event.target.type !== "range") return;
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "ArrowRight") this.step(1);
    else if (event.key === "ArrowLeft") this.step(-1);
    else if (event.key === "Home") this.setTime(0);
    else if (event.key === "End") this.setTime(this.results.steps.length - 1);
    else if (event.key === " ") this._togglePlay();
    else if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      const stops = this._stops();
      const plane = this.state.plane;
      const current = this.state.slice[plane] ?? 0;
      this.state.slice[plane] = Math.max(0, Math.min(stops.length - 1, current + (event.key === "ArrowUp" ? 1 : -1)));
      this._syncSlice();
      this.render();
    } else return;
    event.preventDefault();
  }

  // -- selection --------------------------------------------------------------------------------

  _hover(hit) {
    this.hovered = hit;
    this._positionTooltip();
  }

  _positionTooltip() {
    const tip = this.ui.tooltip;
    const hit = this.hovered;
    if (!hit || !this.results) {
      tip.hidden = true;
      return;
    }
    const results = this.results;
    const t = this.state.t;
    const topo = results.topologyAt(t);
    const frame = results.frameAt(t);
    const entry = this._paramEntry(this.state.param);
    const units = entry?.units && entry.units !== "None" ? ` ${entry.units}` : "";
    const lines = [
      h("div.results-tooltip__title", topo.assemblyNames[hit.a], hit.b >= 0 ? `  ${topo.blockNames[hit.b]}` : ""),
    ];
    if (frame) {
      const i = hit.copyI ?? frame.i[hit.a];
      const j = hit.copyJ ?? frame.j[hit.a];
      lines.push(h("div", `(${i}, ${j})${results.geometry.shape === "hex" ? ` ring ${hexRing(i, j)}` : ""}${hit.copy ? " (symmetric copy)" : ""}`));
    }
    if (hit.b >= 0) {
      const base = results.blockOffset[t];
      lines.push(h("div", `z ${formatValue(results.zbottom[base + hit.b], 4)} – ${formatValue(results.ztop[base + hit.b], 4)} cm`));
    }
    lines.push(h("div.results-tooltip__value", `${formatValue(hit.value, 5)}${units}`));
    if (hit.component != null) {
      const component = this._componentInfo(hit.b, hit.component);
      if (component) {
        lines.push(h("div.results-tooltip__comp", `${component.name} (${component.type}${component.material ? `, ${component.material}` : ""})`));
        if (component.value !== undefined) lines.push(h("div.results-tooltip__value", `${formatValue(component.value, 5)}${units}`));
      }
    }
    fill(tip, ...lines);
    tip.hidden = false;
    const wrap = this.canvas.node.getBoundingClientRect();
    const left = Math.min(hit.sx + 14, wrap.width - tip.offsetWidth - 6);
    const top = Math.min(hit.sy + 14, wrap.height - tip.offsetHeight - 6);
    tip.style.left = `${Math.max(left, 4)}px`;
    tip.style.top = `${Math.max(top, 4)}px`;
  }

  _componentInfo(b, index) {
    const results = this.results;
    const t = this.state.t;
    const designs = results.designsReady(results.steps[t].topology);
    if (!designs) return null;
    const shape = designs.designs[designs.blockDesign[b]]?.shapes[index];
    if (!shape) return null;
    const out = { ...shape };
    const param = this.state.param;
    if (param?.level === "component") {
      const values = results.componentsAt(t, param.name, param.nuclide);
      if (values) out.value = values[designs.compStart[b] + index];
    }
    return out;
  }

  _select(hit) {
    if (!hit) return;
    const results = this.results;
    const t = this.state.t;
    const topo = results.topologyAt(t);
    const frame = results.frameAt(t);
    const base = results.blockOffset[t];
    const b = hit.b;
    const component = hit.component != null ? this._componentInfo(b, hit.component) : null;
    this.state.selected = {
      name: topo.assemblyNames[hit.a],
      k: b >= 0 ? b - topo.blockStart[hit.a] : -1,
      i: frame?.i[hit.a],
      j: frame?.j[hit.a],
      z: b >= 0 ? (results.zbottom[base + b] + results.ztop[base + b]) / 2 : this._currentZ(),
      componentName: component?.name || null,
    };
    this._componentHistory = null;
    this._blockKey = null;
    this.render();
  }

  /** Where the selection is at node `t`: its assembly and block indices there, or null. */
  _resolveSelection(t) {
    const selected = this.state.selected;
    const results = this.results;
    if (!selected || !results) return null;
    const topo = results.topologyAt(t);
    let a;
    if (this.state.follow === "assembly") {
      a = topo.assemblyIndex.get(selected.name);
      if (a === undefined) return null;
      const b = selected.k >= 0 && topo.blockStart[a] + selected.k < topo.blockStart[a + 1] ? topo.blockStart[a] + selected.k : -1;
      return { a, b, componentName: selected.componentName };
    }
    const frame = results.frameAt(t);
    if (!frame) return null;
    a = frame.where.get(`${selected.i},${selected.j}`);
    if (a === undefined) return null;
    return { a, b: results.blockAtHeight(t, a, selected.z), componentName: selected.componentName };
  }

  // -- values -----------------------------------------------------------------------------------

  _seriesValue(t, a, b) {
    const results = this.results;
    const series = this.series;
    if (!series) return NaN;
    if (this.state.param.level === "assembly") return series[results.assemblyOffset[t] + a];
    return b >= 0 ? series[results.blockOffset[t] + b] : NaN;
  }

  /** One number per block for a component parameter: its components combined. */
  _componentAggregate(t) {
    const results = this.results;
    const param = this.state.param;
    const values = results.componentsAt(t, param.name, param.nuclide);
    const designs = results.designsReady(results.steps[t].topology);
    if (!values || !designs) return null;
    const key = `${results.componentKey(t, param.name, param.nuclide)}|${this.state.aggregate}`;
    if (this._aggregateKey === key) return this._aggregate;
    const blocks = designs.blockDesign.length;
    const out = new Float32Array(blocks).fill(NaN);
    const mode = this.state.aggregate;
    for (let b = 0; b < blocks; b += 1) {
      let acc = mode === "min" ? Infinity : mode === "max" ? -Infinity : 0;
      let count = 0;
      for (let c = designs.compStart[b]; c < designs.compStart[b + 1]; c += 1) {
        const v = values[c];
        if (v !== v) continue;
        if (mode === "max") acc = Math.max(acc, v);
        else if (mode === "min") acc = Math.min(acc, v);
        else acc += v;
        count += 1;
      }
      if (count) out[b] = mode === "mean" ? acc / count : acc;
    }
    this._aggregateKey = key;
    this._aggregate = out;
    return out;
  }

  _valueAt(t, a, b) {
    const param = this.state.param;
    if (!param) return NaN;
    if (param.level === "component") {
      const aggregate = this._componentAggregate(t);
      return aggregate && b >= 0 ? aggregate[b] : NaN;
    }
    return this._seriesValue(t, a, b);
  }

  _range() {
    const state = this.state;
    const results = this.results;
    const param = state.param;
    let range = null;
    if (state.rangeMode === "fixed" && state.fixed.min != null && state.fixed.max != null) {
      range = { min: state.fixed.min, max: state.fixed.max, minPositive: state.fixed.min > 0 ? state.fixed.min : null };
    } else if (param?.level === "component") {
      const values = results.componentsAt(state.t, param.name, param.nuclide) || this._lastComponentValues;
      if (state.rangeMode === "history" && this.componentRange) range = this.componentRange;
      else if (values) range = results.rangeOf(values);
    } else if (this.series) {
      const offsets = param.level === "assembly" ? results.assemblyOffset : results.blockOffset;
      range = state.rangeMode === "node"
        ? results.rangeOf(this.series, offsets[state.t], offsets[state.t + 1])
        : results.historyRange(this.seriesKey, this.series);
      if (state.rangeMode === "fixed") {
        range = { ...range, min: state.fixed.min ?? range.min, max: state.fixed.max ?? range.max };
      }
    }
    if (!range || range.min == null) return { min: 0, max: 1, log: false };
    const log = state.log && (range.minPositive != null || range.min > 0);
    const min = log ? Math.max(range.min, range.minPositive ?? range.min) : range.min;
    return { min, max: range.max, log };
  }

  _currentZ() {
    const stops = this.results.zStops;
    return stops[Math.min(this.state.slice.xy ?? 0, stops.length - 1)] ?? 0;
  }

  // -- slices -----------------------------------------------------------------------------------

  /** The positions the right-hand slider steps through, for the current plane. */
  _stops() {
    const results = this.results;
    const plane = this.state.plane;
    if (plane === "xy") return results.zStops;
    const frame = results.frameAt(this.state.t) || [...results.frames.values()][0];
    if (!frame) return [0];
    const axis = plane === "xz" ? frame.y : frame.x;
    const values = new Set();
    axis.forEach((v) => values.add(Math.round(v * 1000) / 1000));
    if (this.state.fullCore) {
      for (const copy of frame.copies) values.add(Math.round((plane === "xz" ? copy[2] : copy[1]) * 1000) / 1000);
    }
    return [...values].sort((a, b) => a - b);
  }

  _syncSlice() {
    if (!this.results) return;
    const plane = this.state.plane;
    const stops = this._stops();
    let index = this.state.slice[plane];
    if (index == null || index >= stops.length) index = nearestIndex(stops, plane === "xy" ? this._currentZ() : 0);
    this.state.slice[plane] = index;
    this.ui.sliceSlider.max = String(Math.max(stops.length - 1, 0));
    this.ui.sliceSlider.value = String(index);
    this.ui.sliceName.textContent = plane === "xy" ? "z" : plane === "xz" ? "y" : "x";
  }

  // -- drawing ----------------------------------------------------------------------------------

  render() {
    if (!this.results || !this.attached) return;
    const state = this.state;
    const results = this.results;
    const t = state.t;
    const step = results.steps[t];
    const frame = results.frameAt(t);
    const param = state.param;
    this.ui.timeLabel.textContent = stepLabel(step);
    this._syncSlice();

    // Until this node's positions (and, for a component parameter, its values) are here, leave the
    // last picture up rather than drawing grey cells or another node's arrangement. Both arrive
    // within a few tens of milliseconds, and their arrival redraws.
    const waitingForValues = param?.level === "component" &&
      (!results.componentsAt(t, param.name, param.nuclide) || !results.designsReady(step.topology));
    if (!frame || waitingForValues) {
      if (!frame) results.ensurePositions(t).catch(() => {});
      if (waitingForValues) {
        results.components(t, param.name, param.nuclide).catch(() => {});
        results.designs(step.topology).catch(() => {});
      }
      this.ui.timeLabel.classList.add("is-pending");
      if (!this._lastFrame) this.ui.message.textContent = "Reading positions…";
      return;
    }
    this.ui.timeLabel.classList.remove("is-pending");
    this._lastFrame = frame;
    this.ui.message.textContent = "";

    if (param?.level === "component") {
      const values = results.componentsAt(t, param.name, param.nuclide);
      if (values) this._lastComponentValues = values;
      else results.components(t, param.name, param.nuclide).catch(() => {});
    }

    const range = this._range();
    const table = colormap(state.cmap, state.reversed);
    const index = scaler(range.min, range.max, range.log);
    const stops = this._stops();
    const sliceValue = stops[Math.min(state.slice[state.plane] ?? 0, stops.length - 1)] ?? 0;
    this.ui.sliceLabel.textContent = `${formatValue(sliceValue, 4)} cm`;

    const geometry = { ...results.geometry, pitchSize: results.geometry.shape === "hex" ? results.geometry.pitch : Math.min(...results.geometry.pitch) };
    const shape = results.cell;
    const scene = {
      plane: state.plane,
      cellShape: shape,
      geometry,
      table,
      showValues: state.showValues,
      textPx: state.textPx,
      digits: state.digits,
      detailAt: DETAIL_AT,
      cells: [],
    };

    const topo = results.topologyAt(t);
    const placements = [];
    for (let a = 0; a < frame.x.length && a < topo.assemblyCount; a += 1) placements.push([a, frame.x[a], frame.y[a], false]);
    if (state.fullCore) for (const [a, x, y, i, j] of frame.copies) placements.push([a, x, y, true, i, j]);

    if (state.plane === "xy") {
      const z = sliceValue;
      for (const [a, x, y, copy, copyI, copyJ] of placements) {
        const b = results.blockAtHeight(t, a, z);
        const value = this._valueAt(t, a, b);
        scene.cells.push({ a, b, x, y, value, k: index(value), copy, copyI, copyJ });
      }
      scene.bounds = boundsOf(placements, shape.radius);
      if (state.detail !== "off") {
        const designs = results.designsReady(step.topology);
        if (designs) {
          const componentValues = param?.level === "component"
            ? results.componentsAt(t, param.name, param.nuclide) || this._lastComponentValues
            : null;
          scene.detail = { ...designs, componentValues };
          scene.componentIndex = index;
        } else if (this.canvas.views.xy && this.canvas.views.xy.ppc * geometry.pitchSize >= DETAIL_AT) {
          this._ensureDesigns();
        }
      }
    } else {
      const axis = state.plane === "xz" ? "y" : "x";
      // Cut a hair off the stop so a cut along a row of cell centres does not also graze the flat
      // faces of the neighbouring rows exactly, which would count them twice.
      const cut = sliceValue + 1e-4 * geometry.pitchSize;
      const base = results.blockOffset[t];
      let lo = Infinity;
      let hi = -Infinity;
      const inset = [];
      for (const [a, x, y, copy] of placements) {
        const offset = axis === "y" ? cut - y : cut - x;
        const span = chord(shape.corners, axis, offset);
        inset.push([x, y, !!span]);
        if (!span) continue;
        const center = axis === "y" ? x : y;
        const u1 = center + span[0];
        const u2 = center + span[1];
        lo = Math.min(lo, u1);
        hi = Math.max(hi, u2);
        for (let b = topo.blockStart[a]; b < topo.blockStart[a + 1]; b += 1) {
          const value = this._valueAt(t, a, b);
          scene.cells.push({ a, b, u1, u2, z1: results.zbottom[base + b], z2: results.ztop[base + b], value, k: index(value), copy });
        }
      }
      const [zlo, zhi] = results.zRange;
      scene.bounds = Number.isFinite(lo)
        ? { minX: lo, maxX: hi, minY: zlo, maxY: zhi }
        : { minX: -1, maxX: 1, minY: zlo, maxY: zhi };
      scene.inset = { points: inset, axis, value: cut, bounds: boundsOf(placements, shape.radius) };
    }

    const selectedWhere = this._resolveSelection(t);
    if (selectedWhere) scene.selected = selectedWhere;
    this.canvas.setScene(scene);
    this.canvas.refreshHover();
    this._paintColorbar(table, range);
    this._renderInspector();
  }

  _paintColorbar(table, range) {
    paintBar(this.ui.colorbar, table);
    const entry = this._paramEntry(this.state.param);
    this.ui.colorMin.textContent = formatValue(range.min, 3);
    this.ui.colorMax.textContent = formatValue(range.max, 3);
    const units = this.state.param?.nuclide ? "1/bn-cm" : entry?.units && entry.units !== "None" ? entry.units : "";
    this.ui.colorUnits.textContent = `${units}${range.log ? " (log)" : ""}`;
  }
}

// -- helpers ----------------------------------------------------------------------------------------

function nearestIndex(values, target) {
  let best = 0;
  values.forEach((value, k) => {
    if (Math.abs(value - target) < Math.abs(values[best] - target)) best = k;
  });
  return best;
}

function boundsOf(placements, reach) {
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const [, x, y] of placements) {
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x);
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y);
  }
  if (!Number.isFinite(minX)) return { minX: -1, maxX: 1, minY: -1, maxY: 1 };
  return { minX: minX - reach, maxX: maxX + reach, minY: minY - reach, maxY: maxY + reach };
}

/** ARMI's ring number for a hex index: the centre is ring 1. */
function hexRing(i, j) {
  return Math.max(Math.abs(i), Math.abs(j), Math.abs(i + j)) + 1;
}

function stepLabel(step) {
  const when = step.time != null ? `${formatValue(step.time, 4)} yr · ` : "";
  return `${when}cycle ${step.cycle} node ${step.node}${step.label ? ` ${step.label}` : ""}`;
}
