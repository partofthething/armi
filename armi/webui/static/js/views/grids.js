// Lattices: the pin maps, and any other grid in the file.
//
// A lattice ID is not a material. It is a label that components claim with `latticeIDs`, and what
// a position actually contains is the union of every component that claims its label, across every
// block drawn on this lattice. Two fuel pins differ in enrichment because their labels are claimed
// by components with different isotopics, or because the assembly modifies the material
// differently at that elevation.
//
// That chain is three files deep if you read it by hand, so the inspector walks it for you: pick a
// position, see what is in it, what it is made of, and which enrichment or poison loading applies
// at each elevation of every assembly that uses it.

import { h, fill, num, uniqueName } from "../dom.js";
import { store } from "../store.js";
import { confirm, field, group, groupWith, numberInput, promptName, selectInput, textInput, toast } from "../ui.js";
import { mountLatticeEditor, palettePanel } from "./latticeEditor.js";
import { swatchStyle } from "../palette.js";

export function gridsView(ctx) {
  const grids = store.section("grids");
  const names = Object.keys(grids);
  const gridName = names.includes(ctx.state.selected) ? ctx.state.selected : names[0];

  renderNavigator(ctx, names, gridName);

  if (!gridName) {
    fill(ctx.stage, h("div.empty", { style: { marginTop: "18vh" } },
      h("strong", "No lattices yet"), "Add one to start laying out pins."));
    fill(ctx.inspector);
    return;
  }

  const cells = grids[gridName].cells || {};
  const declared = new Set(Object.values(cells));
  for (const blockName of store.blocksUsingGrid(gridName)) {
    for (const component of Object.values(store.section("blocks")[blockName] || {})) {
      if (component && typeof component === "object") {
        for (const id of component.latticeIDs || []) declared.add(String(id));
      }
    }
  }
  for (const id of ctx.state.extraIds || []) declared.add(id);

  const palette = [...declared].sort().map((id) => ({
    key: id,
    label: labelForId(gridName, id),
  }));

  const editor = mountLatticeEditor(ctx, {
    gridName,
    title: `Lattice — ${gridName}`,
    palette,
    onSelectCell: () => ctx.refresh(),
  });
  if (!editor) return;

  renderInspector(ctx, gridName, palette, editor);
}

/** What a lattice ID amounts to, in a few words. */
function labelForId(gridName, id) {
  const uses = usageOf(gridName, id);
  if (!uses.length) return "unused";
  const materials = [...new Set(uses.map((use) => shortMaterial(use.material)).filter(Boolean))];
  return materials.slice(0, 2).join(", ") + (materials.length > 2 ? "…" : "");
}

function shortMaterial(name) {
  return name ? String(name).split(":").pop() : null;
}

/** Every component, in every block on this lattice, that claims this ID. */
function usageOf(gridName, id) {
  const out = [];
  for (const blockName of store.blocksUsingGrid(gridName)) {
    const block = store.section("blocks")[blockName] || {};
    for (const [componentName, component] of Object.entries(block)) {
      if (!component || typeof component !== "object") continue;
      const ids = (component.latticeIDs || []).map(String);
      if (!ids.includes(String(id))) continue;
      out.push({
        block: blockName,
        component: componentName,
        material: component.material,
        isotopics: component.isotopics,
        shape: component.shape,
        od: component.od ?? component.op ?? component.widthOuter,
      });
    }
  }
  return out;
}

/**
 * Material modifications that reach a component, per assembly and elevation.
 *
 * Modifications are written on the assembly as one value per axial position, so the same block
 * design can be low-enriched at the bottom of the core and higher up the middle. This pulls out
 * only the positions where the block in question actually sits.
 */
function modificationsFor(blockName, componentName) {
  const rows = [];
  for (const assemblyName of store.assembliesUsingBlock(blockName)) {
    const assembly = store.section("assemblies")[assemblyName] || {};
    const stack = assembly.blocks || [];
    const where = stack.map((each, index) => (each === blockName ? index : -1)).filter((index) => index >= 0);
    const mods = assembly["material modifications"] || {};

    const collect = (source, scope) => {
      for (const [modifier, values] of Object.entries(source || {})) {
        if (modifier === "byComponent") continue;
        const at = where.map((index) => (Array.isArray(values) ? values[index] : values));
        if (at.every((value) => value == null)) continue;
        rows.push({ assembly: assemblyName, modifier, scope, values: at, positions: where });
      }
    };
    collect(mods, "block");
    collect(mods.byComponent?.[componentName], componentName);
  }
  return rows;
}

// -- navigator ------------------------------------------------------------------------------------

function renderNavigator(ctx, names, selected) {
  const rows = names.map((name) => {
    const grid = store.section("grids")[name];
    const users = store.blocksUsingGrid(name);
    const isCore = Object.values(store.section("systems")).some((system) => system?.["grid name"] === name);
    return h(
      "button.item",
      {
        type: "button",
        "aria-selected": String(name === selected),
        onclick: () => {
          ctx.state.selected = name;
          ctx.state.lattice = null;
          ctx.state.selectedCell = null;
          ctx.refresh();
        },
      },
      h("span.item__name", name),
      h("span.item__meta", isCore ? "core" : users.length ? `${users.length} blocks` : "unused"),
      h("span.item__meta", `${Object.keys(grid.cells || {}).length}`),
    );
  });

  fill(
    ctx.nav,
    h("div.panel__head", h("span.panel__title", "Lattices"), h("span.panel__count", String(names.length))),
    h("div.panel__body", rows.length ? h("div", ...rows) : h("div.empty", "None defined.")),
    h(
      "div.panel__foot",
      h("div.row",
        h("button.btn.btn--sm", { type: "button", onclick: () => addGrid(ctx) }, "New"),
        h("button.btn.btn--sm", { type: "button", disabled: !selected,
          onclick: () => duplicateGrid(ctx, selected) }, "Duplicate"),
        h("button.btn.btn--sm.btn--danger", { type: "button", disabled: !selected,
          onclick: () => removeGrid(ctx, selected) }, "Delete"),
      ),
    ),
  );
}

// -- inspector ------------------------------------------------------------------------------------

function renderInspector(ctx, gridName, palette, editor) {
  const grid = store.section("grids")[gridName];
  const geometry = store.geometry[gridName] || {};
  const selectedCell = ctx.state.selected && grid.cells ? ctx.state.selected : null;
  const activeId = ctx.state.active;

  const sections = [
    palettePanel(ctx, {
      palette,
      looks: editor.looks,
      counts: editor.counts,
      title: "Lattice IDs",
      onAdd: () => addLatticeId(ctx),
    }),
    gridProperties(ctx, gridName, grid, geometry),
  ];

  if (activeId) sections.push(idDetail(ctx, gridName, activeId, editor));

  const users = store.blocksUsingGrid(gridName);
  sections.push(
    group(
      "Used by",
      users.length
        ? h("div", ...users.map((name) =>
            h("button.item", { type: "button", onclick: () => ctx.go("blocks", { selected: name }) },
              h("span.item__name", name), h("span.item__meta", "block"))))
        : h("div.field__hint", "No block draws its components on this lattice yet."),
    ),
  );

  fill(
    ctx.inspector,
    h("div.panel__head", h("span.panel__title", gridName)),
    h("div.panel__body", { style: { padding: 0 } }, ...sections),
  );
}

function gridProperties(ctx, gridName, grid, geometry) {
  const catalog = store.catalog || {};
  const geomEntry = (catalog.geometries || []).find((entry) => entry.geom === grid.geom);
  const pitch = grid["lattice pitch"] || {};
  const pitchKeys = geomEntry?.pitchKeys || Object.keys(pitch);
  const setGrid = (label, mutate) => store.edit(label, (draft) => mutate(draft.grids[gridName]));

  return group(
    "Lattice",
    field("Name", textInput({
      value: gridName,
      mono: true,
      onChange: (value) => renameGrid(ctx, gridName, value),
    })),
    h("div.row",
      field("Geometry", selectInput({
        value: grid.geom,
        options: (catalog.geometries || []).map((entry) => entry.geom),
        onChange: (value) => setGrid("change geometry", (target) => {
          target.geom = value;
        }),
      })),
      field("Symmetry", selectInput({
        value: grid.symmetry,
        options: geomEntry?.symmetries || [grid.symmetry],
        onChange: (value) => setGrid("change symmetry", (target) => {
          target.symmetry = value;
        }),
      })),
    ),
    h("div.row", ...pitchKeys.map((key) =>
      field(`Pitch ${key}`, numberInput({
        value: pitch[key],
        onChange: (value) => setGrid("change pitch", (target) => {
          target["lattice pitch"] = { ...(target["lattice pitch"] || {}), [key]: value };
        }),
      }), { unit: "cm" }),
    )),
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

/** What sits at one lattice ID, all the way down to enrichment. */
function idDetail(ctx, gridName, id, editor) {
  const uses = usageOf(gridName, id);
  const count = editor.counts[id] || 0;

  // Several blocks can draw on one lattice, so the same component name turns up more than once.
  // Which block it came from is what tells them apart, so that gets a column.
  const componentRows = uses.map((use) =>
    h(
      "tr",
      h("td", h("button.btn.btn--quiet.btn--sm", {
        type: "button",
        style: { padding: 0 },
        onclick: () => ctx.go("blocks", { selected: use.block, component: use.component }),
      }, use.component)),
      h("td", { style: { color: "var(--text-faint)", fontSize: "11.5px" } }, use.block),
      h("td", { style: { color: "var(--text-dim)" } }, shortMaterial(use.material) || "—"),
      h("td.num.mono", use.od != null && typeof use.od === "number" ? num(use.od, 4) : ""),
    ),
  );

  // One modification can reach the same component through several blocks. Say it once.
  const seen = new Set();
  const modRows = [];
  for (const use of uses) {
    for (const row of modificationsFor(use.block, use.component)) {
      const values = summarise(row.values);
      const key = `${row.assembly}|${row.modifier}|${row.scope}|${values}`;
      if (seen.has(key)) continue;
      seen.add(key);
      modRows.push(
        h(
          "tr",
          h("td.mono", { style: { fontSize: "11.5px" } }, row.modifier),
          h("td", { style: { color: "var(--text-dim)", fontSize: "11.5px" } }, row.assembly),
          h("td.mono", { style: { fontSize: "11.5px" } }, values),
        ),
      );
    }
  }

  return groupWith(
    `At ${id}`,
    h("span.legend__count", `${count} position${count === 1 ? "" : "s"}`),
    h("div", { style: { display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" } },
      h("span.legend__chip", { style: swatchStyle(editor.looks.get(id)) }),
      h("span.field__hint", uses.length ? `${uses.length} components claim this label` : "Nothing claims this label yet"),
    ),
    uses.length
      ? h("table.table",
          h("thead", h("tr", h("th", "Component"), h("th", "Block"), h("th", "Material"),
            h("th", { class: "num" }, "OD"))),
          h("tbody", ...componentRows))
      : h("div.field__hint", "Give a component this ID in its `latticeIDs` list and it will appear here."),
    modRows.length
      ? h("div", { style: { marginTop: "12px" } },
          h("div.field__label", { style: { marginBottom: "4px" } }, "Material modifications by elevation"),
          h("table.table",
            h("thead", h("tr", h("th", "Modifier"), h("th", "Assembly"), h("th", "Values"))),
            h("tbody", ...modRows)))
      : null,
  );
}

function summarise(values) {
  const clean = values.filter((value) => value != null);
  if (!clean.length) return "—";
  const distinct = [...new Set(clean.map(String))];
  if (distinct.length === 1) return distinct[0];
  const numbers = clean.filter((value) => typeof value === "number");
  if (numbers.length === clean.length) {
    return `${num(Math.min(...numbers), 4)} – ${num(Math.max(...numbers), 4)}`;
  }
  return distinct.slice(0, 3).join(", ");
}

// -- actions --------------------------------------------------------------------------------------

async function addLatticeId(ctx) {
  const id = await promptName("New lattice ID", { label: "Identifier, as it appears in the map" });
  if (!id) return;
  ctx.state.extraIds = [...(ctx.state.extraIds || []), id];
  ctx.state.active = id;
  ctx.refresh();
}

async function addGrid(ctx) {
  const name = await promptName("New lattice", { taken: Object.keys(store.section("grids")) });
  if (!name) return;
  const geometries = store.catalog?.geometries || [];
  const entry = geometries[0] || { geom: "hex", symmetries: ["full"], pitchKeys: ["hex"] };
  store.edit(`add lattice ${name}`, (draft) => {
    draft.grids = draft.grids || {};
    draft.grids[name] = {
      geom: entry.geom,
      symmetry: entry.symmetries.includes("full") ? "full" : entry.symmetries[0],
      "lattice pitch": Object.fromEntries(entry.pitchKeys.map((key) => [key, 1.0])),
      cells: { "0,0": "1" },
    };
  });
  ctx.state.selected = name;
  ctx.state.lattice = null;
  ctx.refresh();
}

async function duplicateGrid(ctx, gridName) {
  const name = uniqueName(`${gridName} copy`, Object.keys(store.section("grids")));
  store.edit(`duplicate ${gridName}`, (draft) => {
    draft.grids[name] = structuredClone(draft.grids[gridName]);
  });
  ctx.state.selected = name;
  ctx.state.lattice = null;
  ctx.refresh();
}

async function removeGrid(ctx, gridName) {
  const users = store.blocksUsingGrid(gridName);
  const systems = Object.entries(store.section("systems")).filter(([, s]) => s?.["grid name"] === gridName);
  if (users.length || systems.length) {
    toast(`${gridName} is still in use`, {
      kind: "error",
      detail: [...users.map((u) => `block ${u}`), ...systems.map(([s]) => `system ${s}`)].join("\n"),
    });
    return;
  }
  if (!(await confirm("Delete lattice", `Remove ${gridName} and everything placed on it?`))) return;
  store.edit(`delete lattice ${gridName}`, (draft) => {
    delete draft.grids[gridName];
  });
  ctx.state.selected = null;
  ctx.state.lattice = null;
  ctx.refresh();
}

function renameGrid(ctx, from, to) {
  const target = (to || "").trim();
  if (!target || target === from) return;
  if (store.section("grids")[target]) {
    toast(`There is already a lattice called ${target}`, { kind: "error" });
    return;
  }
  store.edit(`rename lattice to ${target}`, (draft) => {
    const grids = {};
    for (const [name, value] of Object.entries(draft.grids)) grids[name === from ? target : name] = value;
    draft.grids = grids;
    for (const block of Object.values(draft.blocks || {})) {
      if (block && block["grid name"] === from) block["grid name"] = target;
    }
    for (const system of Object.values(draft.systems || {})) {
      if (system && system["grid name"] === from) system["grid name"] = target;
    }
  });
  ctx.state.selected = target;
  ctx.state.lattice = null;
  ctx.refresh();
}
