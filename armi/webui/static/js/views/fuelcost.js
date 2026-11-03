// Fuel cycle cost: the economics plugin's report, worked out from the open results database.
//
// The server weighs the fuel in the database, costs it with the economics plugin's own code, and
// sends back every number the PDF report shows. This draws them as a page in the same order and
// colours, with the case's prices on the right to change. A change goes back to the server, which
// re-prices the same fuel in a few tens of milliseconds, so the page follows the inputs as you type.

import { api } from "../api.js";
import { h, fill, debounce } from "../dom.js";
import { store } from "../store.js";
import { toast } from "../ui.js";
import { CATEGORY_COLORS, SHORT_LABELS, stackedArea, stackedBars, tornado, waterfall } from "../fuelcost/charts.js";
import { resultsNow } from "./results.js";

const SECTIONS = [
  ["summary", "Summary"],
  ["flow", "Mine to spent fuel"],
  ["split", "Where each $/MWh goes"],
  ["steps", "Cost of each step"],
  ["utilization", "Fuel utilization"],
  ["loads", "Fuel bought for each load"],
  ["cycles", "Cycle by cycle"],
  ["cashflow", "Cumulative spend"],
  ["sensitivity", "What moves the cost"],
  ["levelizing", "Discounting and lead times"],
  ["basis", "Basis"],
];

const FRONT_END = ["ore", "conversion", "enrichment", "fabrication"];
const CATEGORIES = [...FRONT_END, "backEnd"];

// Per database: the inputs changed in the browser, and the last report.
const memory = new Map();

export function fuelCostView(ctx) {
  const info = store.session?.results;
  if (!info?.open) {
    fill(ctx.nav, h("div.empty", h("strong", "No results open"), "Open a run's database (.h5) to cost its fuel."));
    fill(ctx.stage, h("div.empty", { style: { marginTop: "18vh" } }, h("strong", "No results loaded"),
      h("p", "The fuel cycle cost is worked out from a run's database. Open one in Results first.")));
    fill(ctx.inspector);
    return;
  }
  if (!memory.has(info.path)) memory.set(info.path, { overrides: {}, report: null, error: null });
  const state = memory.get(info.path);
  const page = new FuelCostPage(ctx, state);
  page.render();
  if (!state.report && !state.error) page.recompute();
}

class FuelCostPage {
  constructor(ctx, state) {
    this.ctx = ctx;
    this.state = state;
    this.recomputeSoon = debounce(() => this.recompute(), 250);
  }

  async recompute() {
    const state = this.state;
    const asked = JSON.stringify(state.overrides);
    state.busy = true;
    this.ctx.stage.classList.add("fc--busy");
    try {
      const report = await api.fuelCost(state.overrides);
      if (JSON.stringify(state.overrides) !== asked) return;
      state.report = report;
      state.error = null;
    } catch (error) {
      state.error = error.message;
    }
    state.busy = false;
    this.ctx.stage.classList.remove("fc--busy");
    this.render();
  }

  render() {
    const { report, error } = this.state;
    this.renderNav();
    if (!report) {
      fill(this.ctx.stage, h("div.empty", { style: { marginTop: "18vh" } },
        h("strong", error ? "Could not cost this run's fuel" : "Weighing the fuel…"),
        error || "The core is weighed at the start of every cycle to find the fresh fuel each one loaded. "
          + "On a long history this takes a few seconds, once."));
      fill(this.ctx.inspector);
      return;
    }
    const scroller = this.ctx.stage.querySelector(".fc-scroll");
    const keep = scroller ? scroller.scrollTop : 0;
    fill(this.ctx.stage, h("div.fc-scroll", this.paper(report)));
    this.ctx.stage.querySelector(".fc-scroll").scrollTop = keep;
    // Leave the inputs alone while one is being typed in; rebuilding them would take the cursor away.
    if (!this.ctx.inspector.contains(document.activeElement) || !this.ctx.inspector.childElementCount) {
      this.renderInputs(report);
    }
    if (error) toast("Those inputs did not work", { kind: "error", detail: error });
  }

  renderNav() {
    const jump = (id) => this.ctx.stage.querySelector(`#fc-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    fill(
      this.ctx.nav,
      h("div.panel__head", h("span.panel__title", "Fuel cycle cost")),
      h("div.panel__body", ...SECTIONS.map(([id, label]) =>
        h("button.item", { type: "button", onclick: () => jump(id) }, h("span.item__name", label)))),
      h("div.panel__foot",
        h("button.btn.btn--sm", { type: "button", style: { width: "100%" }, onclick: () => this.downloadPdf() },
          "Download PDF report")),
    );
  }

  async downloadPdf() {
    try {
      const blob = await api.fuelCostPdf(this.state.overrides);
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `${this.state.report?.title || "fuel"}-fuel-cost.pdf`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 10000);
    } catch (error) {
      toast("Could not write the PDF", { kind: "error", detail: error.message });
    }
  }

  // -- the inputs -------------------------------------------------------------------------------

  renderInputs(report) {
    const state = this.state;
    const sections = new Map();
    for (const input of report.inputs) {
      if (!sections.has(input.section)) sections.set(input.section, []);
      sections.get(input.section).push(input);
    }
    const changed = Object.keys(state.overrides).length;
    const set = (name, value) => {
      const input = report.inputs.find((each) => each.name === name);
      if (value === null || value === "" || value === input.caseValue) delete state.overrides[name];
      else state.overrides[name] = value;
      this.recomputeSoon();
    };
    const refresh = () => this.renderInputs({
      ...report,
      inputs: report.inputs.map((each) => ({ ...each, value: state.overrides[each.name] ?? each.caseValue })),
    });
    const control = (input) => {
      const isChanged = input.name in state.overrides;
      const reset = isChanged
        ? h("button.btn.btn--quiet.btn--sm.fc-reset", { type: "button", title: `Back to the case's ${fmtInput(input.caseValue)}`,
            onclick: (event) => {
              event.preventDefault();
              set(input.name, null);
              refresh();
            } }, "↺")
        : null;
      let widget;
      if (input.kind === "choice") {
        widget = h("select.input", { onchange: (event) => {
          set(input.name, event.target.value);
          refresh();
        } },
          ...input.options.map((option) => h("option", { value: option, selected: option === input.value }, option)));
      } else {
        widget = h("input.input.input--num", {
          type: "number", step: "any", value: input.value ?? "",
          oninput: (event) => {
            const raw = event.target.value;
            const value = raw === "" ? null : Number(raw);
            if (raw !== "" && !Number.isFinite(value)) return;
            set(input.name, value);
          },
          onchange: refresh,
        });
      }
      return h("label.field.fc-field", { class: isChanged ? "fc-field--changed" : "", title: input.description || "" },
        h("span.field__label", input.label, input.units ? h("span.field__unit", input.units) : null, reset),
        widget,
        isChanged ? h("span.field__hint", `case: ${fmtInput(input.caseValue)}`) : null);
    };
    fill(
      this.ctx.inspector,
      h("div.panel__head", h("span.panel__title", "Inputs"),
        changed ? h("button.btn.btn--sm", { type: "button", onclick: () => {
          state.overrides = {};
          this.recompute();
        } }, `Reset ${changed}`) : h("span.panel__count", "the case's")),
      h("div.panel__body", { style: { padding: 0 } },
        h("p.fc-inputs-note", "Change a price to see what it does. Nothing here is written to the case; the fuel is "
          + "the run's and stays the same."),
        ...[...sections].map(([title, inputs]) => h("section.group", h("h3.group__title", title), ...inputs.map(control)))),
    );
  }

  // -- the page ---------------------------------------------------------------------------------

  paper(r) {
    const core = r.initialCore;
    const byKey = Object.fromEntries(r.categories.map((cat) => [cat.key, cat]));
    const changed = Object.keys(this.state.overrides).length;
    return h(
      "article.fc-paper",
      h("div.fc-kicker", "FUEL CYCLE COST", h("span", r.title)),
      h("h1.fc-title", { id: "fc-summary" }, "Fuel cycle cost"),
      h("div.fc-basis", r.basis),
      changed ? h("div.fc-changed", `${changed} input${changed === 1 ? "" : "s"} changed from the case; see the right-hand panel.`) : null,
      h("div.fc-tiles",
        tile("Initial core", money(core.total), `fresh fuel, ${fmt(core.heavyMetal / 1e3, 1)} tHM`),
        tile("Fuel cycle cost", `$${fmt(r.lcoe, 2)}`, `per MWh, levelized at ${pct(r.discountRate, 0)} real`, true),
        tile("Plant-life fuel", money(r.totalSpend), `${r.loads.length} loads + spent fuel, undiscounted`),
        tile("Electricity", `${fmt(r.totalGeneration / 1e6, 1)} TWh`, `net, over ${fmt(r.years, 0)} years`)),

      heading("flow", "Initial core, mine to spent fuel", "quantities and cost for the initial core and its first cycle"),
      this.flow(r, byKey),

      heading("split", "Where each $/MWh goes", `levelized over the plant's life, $${fmt(r.lcoe, 2)}/MWh`),
      this.split(r),

      heading("steps", "Cost of each step", "initial core, plant life and levelized"),
      this.stepTable(r),
      h("div.fc-points", h("div.fc-points__title", "KEY POINTS"), h("ul", ...r.keyPoints.map((point) => h("li", point)))),

      heading("utilization", "Fuel utilization", "over the plant's life, every load included"),
      h("div.fc-metrics", ...r.utilization.map(([name, value, unit]) =>
        h("div.fc-metric", h("div.fc-metric__name", name), h("div.fc-metric__value", value), h("div.fc-metric__unit", unit)))),

      heading("loads", "Fuel bought for each load", "$M by step, at the load's start; lead times not shown"),
      legend(FRONT_END),
      stackedBars(r.loads.map((load, i) => ({
        label: i === 0 ? "Initial" : `C${load.cycle + 1}`,
        sublabel: `${fmt(load.time, 1)} y`,
        total: load.total / 1e6,
        title: `${load.label}: ${money(load.total)}, ${fmt(load.heavyMetal / 1e3, 2)} tHM at ${fmt(100 * load.assay, 2)} wt%`,
        segments: FRONT_END.map((key) => ({ key, value: load.byCategory[key] / 1e6,
          title: `${load.label}, ${SHORT_LABELS[key]}: ${money(load.byCategory[key])}` })),
      })), { keys: FRONT_END, format: (v, exact) => `$${fmt(v, exact ? 1 : 0)}M` }),

      heading("cycles", "Cycle by cycle", "the fresh fuel loaded for each cycle against the electricity it makes"),
      this.cycleTable(r),
      h("p.fc-note", `Reloads: ${r.reloadsText}. Fuel still in the core after the last cycle is not credited.`),

      heading("cashflow", "Cumulative fuel spend", `$M, undiscounted, as paid; ${money(r.totalSpend)} over the plant's life`),
      legend(CATEGORIES),
      stackedArea(r.cumulative.times, r.cumulative.paid, { keys: CATEGORIES, format: (v) => `$${fmt(v, 0)}M`,
        marker: resultsNow() }),
      resultsNow() !== null ? h("p.fc-note", "The dashed line is the time on the Results view's slider.") : null,

      heading("sensitivity", "What moves the fuel cycle cost", `$/MWh levelized, base $${fmt(r.lcoe, 2)}`),
      tornado(r.sensitivities, r.lcoe, { format: (v) => `$${fmt(v, 2)}` }),
      h("p.fc-note", "Prices move ±50%. The discount rate moves 4 points down and 3 up; the capacity factor 15 points "
        + "down and 5 up, each cycle then making the same energy over a different calendar time."),

      heading("levelizing", "From fuel bought to levelized cost", "$/MWh: what the time value of money adds"),
      waterfall(r.waterfall, { format: (v) => `$${fmt(v, 2)}` }),

      heading("basis", "Basis and method", "the initial core by fuel group, and every assumption"),
      this.groupTable(r),
      this.basis(r),
    );
  }

  flow(r, byKey) {
    const core = r.initialCore;
    const p = r.prices;
    const nodes = [
      ["ore", "Natural uranium", fmt(core.u3o8Lb / 1e3, 0), "klb U3O8", `${fmt(core.oreUranium / 1e3, 1)} tU`],
      ["conversion", "Conversion", fmt(core.feed / 1e3, 1), "tU as UF6", `${pct(p.conversionLoss, 1)} loss`],
      ["enrichment", "Enrichment", fmt(core.swu / 1e3, 1), "tSWU", `${fmt(core.swu / core.uranium, 2)} SWU/kgU`],
      ["fabrication", "Fabrication", fmt(core.heavyMetal / 1e3, 2), "tHM", `${fmt(100 * core.assay, 2)} wt% U-235`],
      [null, "Reactor", fmt(r.firstCycle.generation / 1e3, 0), "GWh, cycle 1", `${fmt(r.firstCycle.efpy, 2)} EFPY`],
      ["backEnd", "Spent fuel", `$${fmt(p.backEndPerMWh, 2)}`, "per MWh generated", "storage and disposal"],
    ];
    const cards = [];
    nodes.forEach(([key, step, value, unit, detail], i) => {
      const dollars = key === "backEnd" ? r.firstCycle.backEnd : key ? byKey[key].initial : null;
      cards.push(h("div.fc-card", { style: { "--fc-color": key ? CATEGORY_COLORS[key] : "#0b0b0b" } },
        h("div.fc-card__step", step.toUpperCase()),
        h("div.fc-card__value", value),
        h("div.fc-card__unit", unit),
        h("div.fc-card__detail", detail),
        h("div.fc-card__cost", dollars === null ? h("span.fc-muted", "makes the electricity") : `$${fmt(dollars / 1e6, 2)}M`)));
      if (i < nodes.length - 1) cards.push(h("div.fc-arrow", "→"));
    });
    return h("div", h("div.fc-flow", ...cards),
      h("div.fc-tails", `Depleted tails: ${fmt(core.tails / 1e3, 1)} tU at ${fmt(100 * p.tailsAssay, 3)} wt%`));
  }

  split(r) {
    return h("div",
      h("div.fc-split", ...r.categories.map((cat) => {
        const share = cat.perMWh / r.lcoe;
        return h("div.fc-split__part", { style: { flexGrow: Math.max(share, 0.001), background: CATEGORY_COLORS[cat.key] },
          title: `${cat.label}: $${fmt(cat.perMWh, 2)}/MWh` }, share > 0.06 ? pct(share, 0) : "");
      })),
      h("div.fc-split__legend", ...r.categories.map((cat) =>
        h("div", h("span.fc-swatch", { style: { background: CATEGORY_COLORS[cat.key] } }),
          h("span.fc-muted", SHORT_LABELS[cat.key]),
          h("strong", `$${fmt(cat.perMWh, 2)} · ${pct(cat.perMWh / r.lcoe, 0)}`)))));
  }

  stepTable(r) {
    const core = r.initialCore;
    const quantity = {
      ore: `${fmt(core.u3o8Lb / 1e3, 0)} klb U3O8`,
      conversion: `${fmt(core.feed / 1e3, 1)} tU`,
      enrichment: `${fmt(core.swu / 1e3, 1)} tSWU`,
      fabrication: `${fmt(core.heavyMetal / 1e3, 2)} tHM`,
      backEnd: `${fmt(r.totalGeneration / 1e6, 1)} TWh, life`,
    };
    return table(
      ["Step", "Quantity, initial core", "Unit price", "Initial core $M", "Plant life $M", "Levelized $/MWh", "Share"],
      r.categories.map((cat) => [
        h("span", h("span.fc-swatch", { style: { background: CATEGORY_COLORS[cat.key] } }), cat.label),
        quantity[cat.key], cat.unitPrice,
        cat.initial === null || cat.initial === undefined ? "–" : fmt(cat.initial / 1e6, 2),
        fmt(cat.lifetime / 1e6, 1), fmt(cat.perMWh, 2), pct(cat.perMWh / r.lcoe, 0),
      ]),
      ["Total", "", "", fmt(core.total / 1e6, 2), fmt(r.totalSpend / 1e6, 1), fmt(r.lcoe, 2), "100%"],
    );
  }

  cycleTable(r) {
    const loads = r.loads;
    const sum = (key) => loads.reduce((total, load) => total + load[key], 0);
    return h("div.fc-scrollbox", table(
      ["Cycle", "Starts, y", "Fresh fuel tHM", "Natural U klb U3O8", "Separative tSWU", "Fuel bought $M", "Spent fuel $M",
        "Electricity GWh", "Fuel cost $/MWh"],
      r.cycles.map((c) => [
        String(c.cycle + 1), fmt(c.start, 1),
        c.heavyMetal === null ? "–" : fmt(c.heavyMetal / 1e3, 2),
        c.u3o8Lb === null ? "–" : fmt(c.u3o8Lb / 1e3, 0),
        c.swu === null ? "–" : fmt(c.swu / 1e3, 1),
        fmt(c.frontEnd / 1e6, 2), fmt(c.backEnd / 1e6, 2), fmt(c.generation / 1e3, 0), fmt(c.costPerMWh, 2),
      ]),
      ["Total", "", fmt(sum("heavyMetal") / 1e3, 2), fmt(sum("u3o8Lb") / 1e3, 0), fmt(sum("swu") / 1e3, 1),
        fmt(sum("total") / 1e6, 2), fmt(r.cycles.reduce((t, c) => t + c.backEnd, 0) / 1e6, 2),
        fmt(r.totalGeneration / 1e3, 0), fmt(r.undiscountedLcoe, 2)],
    ));
  }

  groupTable(r) {
    const core = r.initialCore;
    return table(
      ["Fuel group", "Heavy metal kg", "Natural U kg", "Separative kg-SWU", "Natural U $M", "Conversion $M",
        "Enrichment $M", "Fabrication $M", "Total $M"],
      r.groups.map((g) => [
        `${g.form}, ${fmt(100 * g.assay, 2)} wt%`, fmt(g.heavyMetal, 0), fmt(g.oreUranium, 0), fmt(g.swu, 0),
        ...FRONT_END.map((key) => fmt(g.costs[key] / 1e6, 2)), fmt(g.total / 1e6, 2),
      ]),
      ["Total", fmt(core.heavyMetal, 0), fmt(core.oreUranium, 0), fmt(core.swu, 0),
        ...FRONT_END.map((key) => fmt(core.byCategory[key] / 1e6, 2)), fmt(core.total / 1e6, 2)],
    );
  }

  basis(r) {
    const p = r.prices;
    const operation = [
      ["Thermal power", `${fmt(r.thermalPowerMW, 1)} MWt`],
      ["Net thermal efficiency", pct(r.thermalEfficiency, 1)],
      ["Net electric power", `${fmt(r.electricPowerMW, 1)} MWe`],
      ["Capacity factor", pct(r.capacityFactor, 1)],
      ["Cycles", `${r.cycles.length}, ${fmt(r.years / r.cycles.length, 2)} y each on average`],
      ["Operating life", `${fmt(r.years, 1)} y, ${fmt(r.fullPowerYears, 1)} EFPY`],
      ["Reloads", r.source === "run" ? "from the run" : r.reloadsText],
      ["Discount rate", `${pct(r.discountRate, 1)} real`],
    ];
    const prices = [
      ["Natural uranium", `$${fmt(p.orePerLbU3O8, 0)}/lb U3O8 ($${fmt(p.orePerKgU, 0)}/kgU)`],
      ["Conversion", `$${fmt(p.conversionPerKgU, 0)}/kgU, ${pct(p.conversionLoss, 1)} loss`],
      ["Enrichment", `$${fmt(p.swu, 0)}/SWU`],
      ["Feed / tails assay", `${fmt(100 * p.feedAssay, 3)} / ${fmt(100 * p.tailsAssay, 3)} wt%`],
      ...Object.entries(p.fabricationPerKgHM).map(([form, price]) => [`Fabrication, ${form}`, `$${fmt(price, 0)}/kgHM`]),
      ["Spent fuel", `$${fmt(p.backEndPerMWh, 2)}/MWh`],
      ["Paid before loading", FRONT_END.map((key) => `${SHORT_LABELS[key]} ${p.leadTimes[key]} y`).join(" · ")],
    ];
    const list = (title, rows) => h("div", h("h3.fc-subhead", title),
      h("dl.fc-kv", ...rows.flatMap(([key, value]) => [h("dt", key), h("dd", value)])));
    return h("div.fc-basis-grid", list("Plant and operation", operation), list("Prices and assumptions", prices));
  }
}

// -- helpers --------------------------------------------------------------------------------------

function heading(id, title, note) {
  return h("div.fc-heading", { id: `fc-${id}` }, h("h2", title), note ? h("span", note) : null);
}

function tile(label, value, note, accent = false) {
  return h("div.fc-tile", { class: accent ? "fc-tile--accent" : "" },
    h("div.fc-tile__label", label), h("div.fc-tile__value", value), h("div.fc-tile__note", note));
}

function legend(keys) {
  return h("div.fc-legend", ...keys.map((key) =>
    h("span", h("span.fc-swatch", { style: { background: CATEGORY_COLORS[key] } }), SHORT_LABELS[key])));
}

function table(headers, rows, total) {
  return h("table.fc-table",
    h("thead", h("tr", ...headers.map((header, i) => h("th", { class: i ? "num" : "" }, header)))),
    h("tbody", ...rows.map((row) => h("tr", ...row.map((cell, i) => h("td", { class: i ? "num" : "" }, cell))))),
    total ? h("tfoot", h("tr", ...total.map((cell, i) => h("td", { class: i ? "num" : "" }, cell)))) : null);
}

function fmt(value, digits = 2) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "–";
  return value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function pct(value, digits = 0) {
  return `${fmt(100 * value, digits)}%`;
}

function money(dollars) {
  return dollars >= 1e9 ? `$${fmt(dollars / 1e9, 2)}B` : `$${fmt(dollars / 1e6, 1)}M`;
}

function fmtInput(value) {
  return typeof value === "number" ? String(Number(value.toPrecision(6))) : String(value);
}
