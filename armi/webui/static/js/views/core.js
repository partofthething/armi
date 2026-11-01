// The core map.
//
// Assemblies are placed by their specifier, which is the two-character code the map is written in.
// The palette lists assembly designs rather than bare codes, because "z1cra" is what you are
// thinking about and "A1" is only how it gets written down.

import { api } from "../api.js";
import { h, fill, num } from "../dom.js";
import { store } from "../store.js";
import { confirm, field, group, numberInput, selectInput, toast } from "../ui.js";
import { mountLatticeEditor, palettePanel, ringOf, tally } from "./latticeEditor.js";
import { swatchStyle } from "../palette.js";

export function coreView(ctx) {
  const systems = store.section("systems");
  const grids = store.section("grids");
  const coreSystem = Object.entries(systems).find(([, system]) => system && system["grid name"]) || [];
  const gridName = ctx.state.gridName || coreSystem[1]?.["grid name"] || Object.keys(grids)[0];

  if (!gridName || !grids[gridName]) {
    fill(ctx.nav, h("div.empty", h("strong", "No core grid"), "Add one in Lattices, then point a system at it."));
    fill(ctx.stage, h("div.empty", { style: { marginTop: "18vh" } }, h("strong", "This blueprint has no core map yet")));
    fill(ctx.inspector);
    return;
  }

  const assemblies = store.section("assemblies");
  const byName = store.assemblyNames();
  const palette = byName
    .map((name) => ({ key: assemblies[name]?.specifier, label: name, detail: describeAssembly(assemblies[name]) }))
    .filter((entry) => entry.key);

  const editor = mountLatticeEditor(ctx, {
    gridName,
    title: `Core map — ${gridName}`,
    palette,
    extraTools: [
      h("button.btn.btn--sm", {
        type: "button",
        title: "Rewrite this grid as an explicit full core",
        onclick: () => expandToFull(ctx, gridName),
      }, "Expand to full"),
    ],
    onSelectCell: () => ctx.refresh(),
  });
  if (!editor) return;

  renderNavigator(ctx, gridName, palette, editor);
  renderInspector(ctx, gridName, palette, editor);
}

function describeAssembly(design) {
  if (!design) return "";
  const stack = design.blocks || [];
  const height = (design.height || []).reduce((total, each) => total + (Number(each) || 0), 0);
  return `${stack.length} blocks, ${num(height, 2)} cm`;
}

// -- navigator ----------------------------------------------------------------------------------

function renderNavigator(ctx, gridName, palette, editor) {
  const assemblies = store.section("assemblies");
  const counts = editor.counts;
  const total = Object.values(counts).reduce((sum, each) => sum + each, 0);

  const rows = palette
    .slice()
    .sort((a, b) => (counts[b.key] || 0) - (counts[a.key] || 0) || a.key.localeCompare(b.key))
    .map((entry) =>
      h(
        "button.item",
        {
          type: "button",
          "aria-selected": String(ctx.state.active === entry.key),
          onclick: () => {
            ctx.state.active = entry.key;
            ctx.refresh();
          },
          ondblclick: () => ctx.go("assemblies", { selected: entry.label }),
          title: `Double-click to edit ${entry.label}`,
        },
        h("span.item__swatch", { style: swatchStyle(editor.looks.get(entry.key)) }),
        h("span.item__name", entry.label),
        h("span.item__meta", entry.key),
        h("span.item__meta", counts[entry.key] ? `×${counts[entry.key]}` : "—"),
      ),
    );

  fill(
    ctx.nav,
    h("div.panel__head", h("span.panel__title", "Assembly types"), h("span.panel__count", `${total} placed`)),
    h("div.panel__body", rows.length ? h("div", ...rows) : h("div.empty", "No assemblies defined yet.")),
    h(
      "div.panel__foot",
      h("button.btn.btn--sm", { type: "button", style: { width: "100%" },
        onclick: () => ctx.go("assemblies", { creating: true }) }, "New assembly design"),
    ),
  );
}

// -- inspector ----------------------------------------------------------------------------------

function renderInspector(ctx, gridName, palette, editor) {
  const grid = store.section("grids")[gridName];
  const geometry = store.geometry[gridName] || {};
  const selected = ctx.state.selected;
  const assemblies = store.section("assemblies");
  const specToName = store.specifiers;

  const sections = [
    palettePanel(ctx, {
      palette,
      looks: editor.looks,
      counts: editor.counts,
      title: "Place an assembly",
    }),
  ];

  if (selected) {
    const [i, j] = selected.split(",").map(Number);
    const value = (grid.cells || {})[selected];
    const name = value ? specToName[value] : null;
    sections.push(
      group(
        "Selected position",
        h("dl.kv", { style: { margin: 0, display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 10px", fontSize: "12.5px" } },
          h("dt", { style: { color: "var(--text-faint)" } }, "index"),
          h("dd.mono", { style: { margin: 0 } }, `${i}, ${j}`),
          h("dt", { style: { color: "var(--text-faint)" } }, "ring"),
          h("dd.mono", { style: { margin: 0 } }, String(ringOf(i, j, geometry.shape))),
          h("dt", { style: { color: "var(--text-faint)" } }, "specifier"),
          h("dd.mono", { style: { margin: 0 } }, value || "empty"),
          h("dt", { style: { color: "var(--text-faint)" } }, "design"),
          h("dd", { style: { margin: 0 } }, name || "—"),
        ),
        name
          ? h("button.btn.btn--sm", { type: "button", style: { marginTop: "8px" },
              onclick: () => ctx.go("assemblies", { selected: name }) }, `Edit ${name}`)
          : null,
      ),
    );
  }

  sections.push(gridPropertiesPanel(ctx, gridName, grid, geometry));
  sections.push(coreStatsPanel(grid, specToName, assemblies));

  fill(
    ctx.inspector,
    h("div.panel__head", h("span.panel__title", "Core")),
    h("div.panel__body", { style: { padding: 0 } }, ...sections),
  );
}

function gridPropertiesPanel(ctx, gridName, grid, geometry) {
  const catalog = store.catalog || {};
  const geomEntry = (catalog.geometries || []).find((entry) => entry.geom === grid.geom);
  const pitch = grid["lattice pitch"] || {};
  const pitchKeys = geomEntry?.pitchKeys || Object.keys(pitch);

  const setGrid = (label, mutate) =>
    store.edit(label, (draft) => {
      mutate(draft.grids[gridName]);
    });

  return group(
    "Grid",
    field(
      "Geometry",
      selectInput({
        value: grid.geom,
        options: (catalog.geometries || []).map((entry) => entry.geom),
        onChange: (value) => setGrid("change geometry", (target) => {
          target.geom = value;
        }),
      }),
    ),
    field(
      "Symmetry",
      selectInput({
        value: grid.symmetry,
        options: geomEntry?.symmetries || [grid.symmetry],
        onChange: (value) => setGrid("change symmetry", (target) => {
          target.symmetry = value;
        }),
      }),
    ),
    h(
      "div.row",
      ...pitchKeys.map((key) =>
        field(
          `Pitch ${key}`,
          numberInput({
            value: pitch[key],
            onChange: (value) => setGrid("change pitch", (target) => {
              target["lattice pitch"] = { ...(target["lattice pitch"] || {}), [key]: value };
            }),
          }),
          { unit: "cm" },
        ),
      ),
    ),
    geometry.centered === false
      ? h("div.field__hint", { style: { color: "var(--amber)" } },
          "This grid is no longer centred on the origin, so it will be written as an explicit " +
          "list of positions instead of a readable lattice map. Squaring it up about (0, 0) " +
          "restores the map.")
      : null,
    geometry.hasPitch === false
      ? h("div.field__hint", "No lattice pitch given, so this is a unit grid. Real dimensions come from the blocks that sit on it.")
      : null,
    geometry.error ? h("div.field__hint", { style: { color: "var(--alarm)" } }, geometry.error) : null,
  );
}

function coreStatsPanel(grid, specToName, assemblies) {
  const counts = tally(grid.cells || {});
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const positions = entries.reduce((sum, [, count]) => sum + count, 0);
  const fuel = entries
    .filter(([spec]) => /fuel/i.test(assemblies[specToName[spec]]?.flags || ""))
    .reduce((sum, [, count]) => sum + count, 0);

  return group(
    "Loading",
    h(
      "table.table",
      h("thead", h("tr", h("th", "Type"), h("th", "Design"), h("th", { class: "num" }, "Count"))),
      h(
        "tbody",
        ...entries.map(([spec, count]) =>
          h("tr", h("td.mono", spec), h("td", specToName[spec] || "—"), h("td.num.mono", String(count))),
        ),
        h("tr", h("td", { colSpan: 2, style: { color: "var(--text-faint)" } }, "total"),
          h("td.num.mono", String(positions))),
        fuel ? h("tr", h("td", { colSpan: 2, style: { color: "var(--text-faint)" } }, "fuel"),
          h("td.num.mono", String(fuel))) : null,
      ),
    ),
  );
}

async function expandToFull(ctx, gridName) {
  const ok = await confirm(
    "Expand to full core",
    "This rewrites the grid with every position spelled out, and the symmetry condition is dropped. " +
      "It cannot be folded back up automatically.",
    "Expand",
  );
  if (!ok) return;
  try {
    await store.sync();
    const payload = await api.expandGrid(gridName);
    store._accept(payload);
    store.undoStack = [];
    store.redoStack = [];
    store.emit("document");
    toast("Expanded to a full core", { kind: "ok" });
  } catch (error) {
    toast("Could not expand this grid", { kind: "error", detail: error.message });
  }
}
