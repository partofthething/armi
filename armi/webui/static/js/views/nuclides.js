// Nuclide flags and custom isotopics.
//
// Nuclide flags decide what ARMI tracks: which nuclides get cross sections, which ones deplete,
// and which naturally-occurring elements get expanded into isotopes. It is a long list where every
// row has the same three questions, so it is a table, with the filters you actually want at the
// top rather than a search box and nothing else.

import { h, fill, num } from "../dom.js";
import { store } from "../store.js";
import { checkbox, confirm, field, group, groupWith, numberInput, promptName, selectInput, tagList, textInput, toast } from "../ui.js";

export function nuclidesView(ctx) {
  // The tab lives in the shared `selected` slot so it rides along in the address bar like every
  // other view's selection does.
  ctx.state.tab = ctx.state.selected === "isotopics" ? "isotopics" : "flags";
  ctx.state.selected = ctx.state.tab;
  renderNavigator(ctx);
  if (ctx.state.tab === "flags") renderFlags(ctx);
  else renderIsotopics(ctx);
}

function renderNavigator(ctx) {
  const flags = store.section("nuclide flags") || {};
  const isotopics = store.section("custom isotopics") || {};
  const tab = (id, label, count) =>
    h("button.item", {
      type: "button",
      "aria-selected": String(ctx.state.tab === id),
      onclick: () => {
        ctx.state.selected = id;
        ctx.refresh();
      },
    }, h("span.item__name", label), h("span.item__meta", String(count)));

  fill(
    ctx.nav,
    h("div.panel__head", h("span.panel__title", "Nuclides")),
    h("div.panel__body",
      tab("flags", "Nuclide flags", Object.keys(flags).length),
      tab("isotopics", "Custom isotopics", Object.keys(isotopics).length),
    ),
  );
}

// -- nuclide flags ----------------------------------------------------------------------------------

function renderFlags(ctx) {
  const flags = store.section("nuclide flags") || {};
  const names = Object.keys(flags);
  ctx.state.filter = ctx.state.filter || "all";
  const query = (ctx.state.query || "").trim().toUpperCase();

  const visible = names.filter((name) => {
    const flag = flags[name] || {};
    if (query && !name.includes(query)) return false;
    if (ctx.state.filter === "burn") return !!flag.burn;
    if (ctx.state.filter === "xs") return !!flag.xs;
    if (ctx.state.filter === "expanded") return !!(flag.expandTo && flag.expandTo.length);
    return true;
  });

  const set = (name, key, value) =>
    store.edit(`${key} for ${name}`, (draft) => {
      draft["nuclide flags"][name] = { ...(draft["nuclide flags"][name] || {}), [key]: value };
    });

  const filterChip = (id, label) =>
    h("button.chip", {
      type: "button",
      "aria-pressed": String(ctx.state.filter === id),
      onclick: () => {
        ctx.state.filter = id;
        ctx.refresh();
      },
    }, label);

  const rows = visible.map((name) => {
    const flag = flags[name] || {};
    return h(
      "tr",
      h("td.mono", { style: { width: "82px" } }, name),
      h("td", { style: { width: "56px" } },
        h("input", { type: "checkbox", checked: !!flag.burn,
          onchange: (event) => set(name, "burn", event.target.checked) })),
      h("td", { style: { width: "56px" } },
        h("input", { type: "checkbox", checked: !!flag.xs,
          onchange: (event) => set(name, "xs", event.target.checked) })),
      h("td", tagList({
        values: (flag.expandTo || []).map(String),
        placeholder: "nuclide",
        onChange: (values) => set(name, "expandTo", values.length ? values : null),
      })),
      h("td", { style: { width: "26px" } },
        h("button.btn.btn--quiet.btn--sm", { type: "button", title: `Remove ${name}`,
          onclick: () => removeFlag(ctx, name) }, "×")),
    );
  });

  fill(
    ctx.stage,
    h("div.stage__bar",
      h("span.stage__title", "Nuclide flags"),
      h("span.item__meta", `${visible.length} of ${names.length}`),
      h("div.chipbar",
        filterChip("all", "All"),
        filterChip("burn", "Depleting"),
        filterChip("xs", "Cross sections"),
        filterChip("expanded", "Expanded")),
      h("input.searchbox", {
        type: "search", placeholder: "Find a nuclide", value: ctx.state.query || "",
        style: { width: "180px" },
        oninput: (event) => {
          ctx.state.query = event.target.value;
          ctx.refresh();
          ctx.stage.querySelector(".searchbox")?.focus();
        },
      }),
      h("span.spacer"),
      h("button.btn.btn--sm", { type: "button", onclick: () => addFlag(ctx) }, "Add nuclide"),
    ),
    h("div.stage__body", { style: { padding: "0 16px 16px" } },
      names.length
        ? h("table.table",
            h("thead", h("tr", h("th", "Nuclide"), h("th", "Deplete"), h("th", "XS"),
              h("th", "Expand into"), h("th", ""))),
            h("tbody", ...rows))
        : h("div.empty", h("strong", "No nuclide flags"),
            "Without these, ARMI works out what to track from the materials alone.")),
  );

  fill(
    ctx.inspector,
    h("div.panel__head", h("span.panel__title", "Nuclide flags")),
    h("div.panel__body", { style: { padding: 0 } },
      group("What these do",
        h("p.field__hint", { style: { lineHeight: 1.7 } },
          "Deplete marks a nuclide as one whose number density changes with burnup. " +
          "XS asks for a cross section for it. Expand into replaces a natural element with the " +
          "isotopes listed, which is how an elemental material becomes something a lattice code can read."),
      ),
      group("Summary",
        h("table.table", h("tbody",
          h("tr", h("td", "Tracked"), h("td.num.mono", String(names.length))),
          h("tr", h("td", "Depleting"), h("td.num.mono",
            String(names.filter((name) => flags[name]?.burn).length))),
          h("tr", h("td", "With cross sections"), h("td.num.mono",
            String(names.filter((name) => flags[name]?.xs).length))),
          h("tr", h("td", "Expanded"), h("td.num.mono",
            String(names.filter((name) => flags[name]?.expandTo?.length).length))),
        ))),
    ),
  );
}

async function addFlag(ctx) {
  const name = await promptName("Track a nuclide", {
    label: "Nuclide or element, as ARMI names it",
    taken: Object.keys(store.section("nuclide flags") || {}),
  });
  if (!name) return;
  store.edit(`track ${name}`, (draft) => {
    draft["nuclide flags"] = draft["nuclide flags"] || {};
    draft["nuclide flags"][name.toUpperCase()] = { burn: false, xs: true };
  });
  ctx.refresh();
}

async function removeFlag(ctx, name) {
  store.edit(`stop tracking ${name}`, (draft) => {
    delete draft["nuclide flags"][name];
  });
  ctx.refresh();
}

// -- custom isotopics -------------------------------------------------------------------------------

function renderIsotopics(ctx) {
  const isotopics = store.section("custom isotopics") || {};
  const names = Object.keys(isotopics);
  const selected = names.includes(ctx.state.isotopic) ? ctx.state.isotopic : names[0];

  const rows = names.map((name) =>
    h("button.item", {
      type: "button",
      "aria-selected": String(name === selected),
      onclick: () => {
        ctx.state.isotopic = name;
        ctx.refresh();
      },
    }, h("span.item__name", name), h("span.item__meta", isotopics[name]?.["input format"] || "")),
  );

  fill(
    ctx.stage,
    h("div.stage__bar",
      h("span.stage__title", "Custom isotopics"),
      h("span.item__meta", `${names.length} defined`),
      h("span.spacer"),
      h("button.btn.btn--sm", { type: "button", onclick: () => addIsotopic(ctx) }, "New"),
      selected ? h("button.btn.btn--sm.btn--danger", { type: "button",
        onclick: () => removeIsotopic(ctx, selected) }, "Delete") : null,
    ),
    h("div.stage__body", { style: { padding: "16px" } },
      names.length
        ? h("div", { style: { display: "grid", gridTemplateColumns: "240px minmax(0,1fr)", gap: "16px" } },
            h("div", ...rows),
            selected ? isotopicTable(ctx, selected, isotopics[selected]) : null)
        : h("div.empty", h("strong", "No custom isotopics"),
            "Define one to give a component a composition of your own instead of the material's.")),
  );

  fill(
    ctx.inspector,
    h("div.panel__head", h("span.panel__title", "Custom isotopics")),
    h("div.panel__body", { style: { padding: 0 } },
      selected ? isotopicProperties(ctx, selected, isotopics[selected]) : null,
      group("Used by", usedByPanel(selected)),
    ),
  );
}

function isotopicTable(ctx, name, isotopic) {
  const reserved = new Set(["input format", "density", "name"]);
  const entries = Object.entries(isotopic || {}).filter(([key]) => !reserved.has(key));
  const total = entries.reduce((sum, [, value]) => sum + (Number(value) || 0), 0);
  // Fractions have to come to one. Number densities are absolute, and their sum means nothing.
  const isFractions = String(isotopic?.["input format"] || "").includes("fraction");

  const set = (nuclide, value) =>
    store.edit(`change ${nuclide}`, (draft) => {
      if (value == null) delete draft["custom isotopics"][name][nuclide];
      else draft["custom isotopics"][name][nuclide] = value;
    });

  const nuclideInput = h("input.input.mono", { type: "text", placeholder: "nuclide, e.g. U235",
    style: { width: "160px" },
    onkeydown: (event) => {
      if (event.key !== "Enter" || !event.target.value.trim()) return;
      event.preventDefault();
      set(event.target.value.trim().toUpperCase(), 0.0);
      ctx.refresh();
    } });

  return h(
    "div",
    h("table.table",
      h("thead", h("tr", h("th", "Nuclide"), h("th", { class: "num" }, "Fraction"), h("th", ""))),
      h("tbody",
        ...entries.map(([nuclide, value]) =>
          h("tr",
            h("td.mono", nuclide),
            h("td", { style: { width: "150px" } }, numberInput({ value, onChange: (next) => set(nuclide, next) })),
            h("td", { style: { width: "26px" } },
              h("button.btn.btn--quiet.btn--sm", { type: "button",
                onclick: () => {
                  set(nuclide, null);
                  ctx.refresh();
                } }, "×")))),
        h("tr", h("td", { style: { color: "var(--text-faint)" } }, "total"),
          h("td.num.mono", {
            style: {
              color: !isFractions ? "var(--text-dim)" : Math.abs(total - 1) < 1e-6 ? "var(--good)" : "var(--amber)",
            },
            title: isFractions ? "Fractions should come to 1" : "Number densities do not sum to anything in particular",
          }, num(total, 6)),
          h("td", "")),
      )),
    h("div", { style: { marginTop: "10px" } }, nuclideInput),
    h("div.field__hint",
      isFractions
        ? "Press Enter to add. Fractions should come to 1."
        : "Press Enter to add. Number densities are in atoms per barn-cm."),
  );
}

function isotopicProperties(ctx, name, isotopic) {
  const set = (label, mutate) => store.edit(label, (draft) => mutate(draft["custom isotopics"][name]));
  return group(
    "Definition",
    field("Name", textInput({ value: name, mono: true,
      onChange: (value) => renameIsotopic(ctx, name, value) })),
    field("Input format", selectInput({
      value: isotopic?.["input format"] || "mass fractions",
      options: ["mass fractions", "number fractions", "number densities"],
      onChange: (value) => set("change input format", (target) => {
        target["input format"] = value;
      }),
    })),
    field("Density", numberInput({
      value: isotopic?.density,
      onChange: (value) => set("change density", (target) => {
        target.density = value;
      }),
    }), { unit: "g/cm³", hint: "Leave blank to use the material's own density." }),
  );
}

function usedByPanel(name) {
  if (!name) return h("div.field__hint", "Nothing selected.");
  const uses = [];
  for (const [blockName, block] of Object.entries(store.section("blocks"))) {
    for (const [componentName, component] of Object.entries(block || {})) {
      if (component && typeof component === "object" && component.isotopics === name) {
        uses.push(`${blockName} → ${componentName}`);
      }
    }
  }
  return uses.length
    ? h("div", ...uses.map((use) => h("div.field__hint", { style: { fontFamily: "var(--mono)" } }, use)))
    : h("div.field__hint", "No component uses this yet.");
}

async function addIsotopic(ctx) {
  const name = await promptName("New custom isotopics", { taken: Object.keys(store.section("custom isotopics") || {}) });
  if (!name) return;
  store.edit(`add isotopics ${name}`, (draft) => {
    draft["custom isotopics"] = draft["custom isotopics"] || {};
    draft["custom isotopics"][name] = { "input format": "mass fractions", density: null };
  });
  ctx.state.isotopic = name;
  ctx.refresh();
}

async function removeIsotopic(ctx, name) {
  if (!(await confirm("Delete custom isotopics", `Remove ${name}?`))) return;
  store.edit(`delete isotopics ${name}`, (draft) => {
    delete draft["custom isotopics"][name];
  });
  ctx.state.isotopic = null;
  ctx.refresh();
}

function renameIsotopic(ctx, from, to) {
  const target = (to || "").trim();
  if (!target || target === from) return;
  if (store.section("custom isotopics")[target]) {
    toast(`There is already one called ${target}`, { kind: "error" });
    return;
  }
  store.edit(`rename to ${target}`, (draft) => {
    const rebuilt = {};
    for (const [name, value] of Object.entries(draft["custom isotopics"])) {
      rebuilt[name === from ? target : name] = value;
    }
    draft["custom isotopics"] = rebuilt;
    for (const block of Object.values(draft.blocks || {})) {
      for (const component of Object.values(block || {})) {
        if (component && typeof component === "object" && component.isotopics === from) {
          component.isotopics = target;
        }
      }
    }
  });
  ctx.state.isotopic = target;
  ctx.refresh();
}
