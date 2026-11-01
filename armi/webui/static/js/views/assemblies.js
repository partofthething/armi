// Assemblies: a stack of blocks, and everything that varies up the stack.
//
// An assembly in ARMI is a set of parallel lists. The stack of blocks is one list; heights, cross
// section types, mesh points and every material modification are more lists, read by position. Get
// them out of step and the file is quietly wrong, so nothing here edits one list on its own: a
// position is inserted, moved or deleted everywhere at once.
//
// The axial profile is the other half of this view. Enrichment and poison loading are written as a
// value per elevation, which is unreadable as YAML and obvious as a column, so they are a column.

import { api } from "../api.js";
import { h, fill, num, uniqueName, debounce } from "../dom.js";
import { store } from "../store.js";
import { drawCrossSection } from "../render/xsection.js";
import {
  confirm, field, group, groupWith, numberInput, promptName, selectInput, textInput, toast,
} from "../ui.js";
import { assign, swatchStyle } from "../palette.js";

/** Lists that always have one entry per axial position. */
const PER_POSITION = ["blocks", "height", "axial mesh points", "xs types"];

export function assembliesView(ctx) {
  const names = store.assemblyNames();
  const name = names.includes(ctx.state.selected) ? ctx.state.selected : names[0];

  renderNavigator(ctx, names, name);

  if (!name) {
    fill(ctx.stage, h("div.empty", { style: { marginTop: "18vh" } },
      h("strong", "No assemblies yet"),
      "An assembly stacks blocks from the bottom of the core to the top."));
    fill(ctx.inspector);
    return;
  }

  const assembly = store.section("assemblies")[name];
  const stack = assembly.blocks || [];
  if (ctx.state.position == null || ctx.state.position >= stack.length) {
    ctx.state.position = stack.length ? Math.min(ctx.state.position ?? 0, stack.length - 1) : null;
  }

  renderStage(ctx, name, assembly);
  renderInspector(ctx, name, assembly);
}

// -- navigator ------------------------------------------------------------------------------------

function renderNavigator(ctx, names, selected) {
  const assemblies = store.section("assemblies");
  const looks = assign(names.map((name) => assemblies[name]?.specifier).filter(Boolean));

  const rows = names.map((name) => {
    const assembly = assemblies[name] || {};
    const height = (assembly.height || []).reduce((total, each) => total + (Number(each) || 0), 0);
    return h(
      "button.item",
      {
        type: "button",
        "aria-selected": String(name === selected),
        onclick: () => {
          ctx.state.selected = name;
          ctx.state.position = 0;
          ctx.refresh();
        },
      },
      h("span.item__swatch", { style: swatchStyle(looks.get(assembly.specifier)) }),
      h("span.item__name", name),
      h("span.item__meta", assembly.specifier || "??"),
      h("span.item__meta", `${num(height, 1)}cm`),
    );
  });

  fill(
    ctx.nav,
    h("div.panel__head", h("span.panel__title", "Assemblies"), h("span.panel__count", String(names.length))),
    h("div.panel__body", rows.length ? h("div", ...rows) : h("div.empty", "None defined.")),
    h("div.panel__foot", h("div.row",
      h("button.btn.btn--sm", { type: "button", onclick: () => addAssembly(ctx) }, "New"),
      h("button.btn.btn--sm", { type: "button", disabled: !selected,
        onclick: () => duplicateAssembly(ctx, selected) }, "Duplicate"),
      h("button.btn.btn--sm.btn--danger", { type: "button", disabled: !selected,
        onclick: () => removeAssembly(ctx, selected) }, "Delete"),
    )),
  );
}

// -- stage ----------------------------------------------------------------------------------------

function renderStage(ctx, name, assembly) {
  const stack = assembly.blocks || [];
  const heights = assembly.height || [];
  const total = heights.reduce((sum, each) => sum + (Number(each) || 0), 0);
  const modifiers = modifierNames(assembly);

  const bar = h(
    "div.stage__bar",
    h("span.stage__title", name),
    h("span.item__meta", `${stack.length} positions`),
    h("span.item__meta", `${num(total, 2)} cm tall`),
    h("span.spacer"),
    h("button.btn.btn--sm", { type: "button", onclick: () => addModifier(ctx, name) }, "Add modifier"),
    h("button.btn.btn--sm", { type: "button", onclick: () => insertPosition(ctx, name, stack.length) }, "Add position"),
  );

  const body = h(
    "div.stage__body",
    h(
      "div",
      { style: { display: "grid", gridTemplateColumns: "auto minmax(0, 1fr)", gap: "16px", padding: "16px", alignItems: "start" } },
      elevationColumn(ctx, name, assembly),
      profileTable(ctx, name, assembly, modifiers),
    ),
  );

  fill(ctx.stage, bar, body);
}

/** The stack drawn to scale, bottom at the bottom, and draggable. */
function elevationColumn(ctx, name, assembly) {
  const stack = assembly.blocks || [];
  const heights = assembly.height || [];
  const total = heights.reduce((sum, each) => sum + (Number(each) || 0), 0) || 1;
  const looks = assign([...new Set(stack)]);
  const pixels = 520;

  // Drawn top-down on screen but numbered bottom-up, the way an elevation is read.
  const rows = stack
    .map((blockName, index) => ({ blockName, index }))
    .reverse()
    .map(({ blockName, index }) => {
      const height = Number(heights[index]) || 0;
      const look = looks.get(blockName);
      return h(
        "div",
        {
          draggable: "true",
          dataset: { index: String(index) },
          title: `${blockName}  ${num(height, 3)} cm`,
          style: {
            ...swatchStyle(look),
            height: `${Math.max(16, (height / total) * pixels)}px`,
            display: "flex",
            alignItems: "center",
            gap: "6px",
            padding: "0 8px",
            cursor: "grab",
            borderRadius: "2px",
            outline: ctx.state.position === index ? "2px solid var(--amber)" : "none",
            outlineOffset: "-2px",
            overflow: "hidden",
          },
          onclick: () => {
            ctx.state.position = index;
            ctx.refresh();
          },
          ondragstart: (event) => {
            event.dataTransfer.setData("text/plain", String(index));
            event.dataTransfer.effectAllowed = "move";
            event.currentTarget.classList.add("dragging");
          },
          ondragend: (event) => event.currentTarget.classList.remove("dragging"),
          ondragover: (event) => {
            event.preventDefault();
            const box = event.currentTarget.getBoundingClientRect();
            const after = event.clientY > box.top + box.height / 2;
            event.currentTarget.classList.toggle("drop-before", !after);
            event.currentTarget.classList.toggle("drop-after", after);
          },
          ondragleave: (event) => event.currentTarget.classList.remove("drop-before", "drop-after"),
          ondrop: (event) => {
            event.preventDefault();
            event.currentTarget.classList.remove("drop-before", "drop-after");
            const from = Number(event.dataTransfer.getData("text/plain"));
            const box = event.currentTarget.getBoundingClientRect();
            // The column runs top-down, so dropping below a row means earlier in the stack.
            const after = event.clientY > box.top + box.height / 2;
            movePosition(ctx, name, from, after ? index : index + 1);
          },
        },
        h("span.mono", { style: { fontSize: "10.5px", color: "#4b535d", minWidth: "18px" } }, String(index)),
        h("span", { style: { fontSize: "11.5px", color: "#1b1f24", whiteSpace: "nowrap",
          overflow: "hidden", textOverflow: "ellipsis" } }, blockName),
      );
    });

  return h(
    "div",
    { style: { display: "grid", gap: "6px", justifyItems: "stretch" } },
    h("div.field__label", "Elevation"),
    h("div", { style: { width: "188px", display: "grid", gap: "2px", background: "var(--sheet)",
      padding: "6px", borderRadius: "var(--radius-lg)", boxShadow: "0 12px 30px rgba(0,0,0,0.45)" } },
      rows.length ? rows : h("div.field__hint", { style: { color: "#6b7480", padding: "20px 6px",
        textAlign: "center" } }, "Empty stack"),
    ),
    h("div.field__hint", { style: { textAlign: "center" } }, "Drag to reorder"),
  );
}

/** One row per elevation, one column per thing that varies with elevation. */
function profileTable(ctx, name, assembly, modifiers) {
  const stack = assembly.blocks || [];
  const blockNames = Object.keys(store.section("blocks"));
  const setList = (label, key, index, value) =>
    store.edit(label, (draft) => {
      const target = draft.assemblies[name];
      target[key] = [...(target[key] || [])];
      while (target[key].length < stack.length) target[key].push(null);
      target[key][index] = value;
    });

  const rows = stack.map((blockName, index) =>
    h(
      "tr",
      {
        style: ctx.state.position === index ? { outline: "1px solid var(--amber-dim)" } : null,
        onclick: () => {
          if (ctx.state.position === index) return;
          ctx.state.position = index;
          ctx.refresh();
        },
      },
      h("td.mono", { style: { color: "var(--text-faint)", width: "26px" } }, String(index)),
      h("td", selectInput({
        value: blockName,
        options: blockNames,
        onChange: (value) => setList(`change block at ${index}`, "blocks", index, value),
      })),
      h("td", { style: { width: "92px" } }, numberInput({
        value: assembly.height?.[index],
        onChange: (value) => setList(`change height at ${index}`, "height", index, value),
      })),
      h("td", { style: { width: "62px" } }, textInput({
        value: assembly["xs types"]?.[index] ?? "",
        mono: true,
        onChange: (value) => setList(`change xs type at ${index}`, "xs types", index, value.toUpperCase()),
      })),
      h("td", { style: { width: "62px" } }, numberInput({
        value: assembly["axial mesh points"]?.[index],
        onChange: (value) => setList(`change mesh at ${index}`, "axial mesh points", index, value),
      })),
      ...modifiers.map((modifier) =>
        h("td", { style: { width: "92px" } }, numberInput({
          value: readModifier(assembly, modifier, index),
          onChange: (value) => writeModifier(name, modifier, index, value, stack.length),
        })),
      ),
      h("td", { style: { width: "26px" } },
        h("button.btn.btn--quiet.btn--sm", {
          type: "button", title: "Remove this position",
          onclick: (event) => {
            event.stopPropagation();
            removePosition(ctx, name, index);
          },
        }, "×"),
      ),
    ),
  );

  return h(
    "div",
    { style: { display: "grid", gap: "6px", minWidth: 0 } },
    h("div.field__label", "Axial profile"),
    h(
      "div",
      { style: { overflowX: "auto", border: "1px solid var(--line)", borderRadius: "var(--radius-lg)" } },
      h(
        "table.table",
        h("thead", h("tr",
          h("th", "#"), h("th", "Block"), h("th", "Height"), h("th", "XS"), h("th", "Mesh"),
          ...modifiers.map((modifier) =>
            h("th", h("span", { title: modifier.scope === "block" ? "applies to the whole block"
              : `applies to ${modifier.scope} only` },
              modifier.name,
              modifier.scope !== "block" ? h("span.item__meta", ` · ${modifier.scope}`) : null))),
          h("th", ""),
        )),
        h("tbody", ...rows),
      ),
    ),
    h("div.field__hint", "Height in cm. XS is the cross section type letter. Blank modifier values fall back to the material's own composition."),
  );
}

// -- material modifications -------------------------------------------------------------------------

/** Every modifier in play, block-wide and per component. */
function modifierNames(assembly) {
  const mods = assembly["material modifications"] || {};
  const out = [];
  for (const key of Object.keys(mods)) {
    if (key !== "byComponent") out.push({ name: key, scope: "block" });
  }
  for (const [component, byModifier] of Object.entries(mods.byComponent || {})) {
    for (const key of Object.keys(byModifier || {})) out.push({ name: key, scope: component });
  }
  return out;
}

function readModifier(assembly, modifier, index) {
  const mods = assembly["material modifications"] || {};
  const source = modifier.scope === "block" ? mods[modifier.name] : mods.byComponent?.[modifier.scope]?.[modifier.name];
  return Array.isArray(source) ? source[index] : undefined;
}

function writeModifier(assemblyName, modifier, index, value, length) {
  store.edit(`change ${modifier.name} at ${index}`, (draft) => {
    const assembly = draft.assemblies[assemblyName];
    const mods = (assembly["material modifications"] = { ...(assembly["material modifications"] || {}) });
    let holder = mods;
    if (modifier.scope !== "block") {
      mods.byComponent = { ...(mods.byComponent || {}) };
      mods.byComponent[modifier.scope] = { ...(mods.byComponent[modifier.scope] || {}) };
      holder = mods.byComponent[modifier.scope];
    }
    const values = [...(holder[modifier.name] || [])];
    while (values.length < length) values.push(null);
    values[index] = value;
    holder[modifier.name] = values;
  });
}

async function addModifier(ctx, assemblyName) {
  const assembly = store.section("assemblies")[assemblyName];
  const components = [...new Set((assembly.blocks || []).flatMap((blockName) =>
    Object.entries(store.section("blocks")[blockName] || {})
      .filter(([, value]) => value && typeof value === "object")
      .map(([name]) => name),
  ))].sort();

  const nameInput = h("input.input.mono", { type: "text", placeholder: "U235_wt_frac" });
  const scopeSelect = selectInput({
    value: "block",
    options: [["block", "the whole block"], ...components.map((name) => [name, `only ${name}`])],
    onChange: () => {},
  });
  const chosen = await (await import("../ui.js")).dialog({
    title: "Add a material modifier",
    body: h("div",
      field("Modifier name", nameInput, { hint: "Whatever the material class accepts, such as U235_wt_frac or lbpSigma." }),
      field("Applies to", scopeSelect),
    ),
    actions: [["Add", "primary", () => (nameInput.value.trim() ? { name: nameInput.value.trim(), scope: scopeSelect.value } : null)]],
  });
  if (!chosen) return;
  writeModifier(assemblyName, chosen, 0, null, (assembly.blocks || []).length);
  ctx.refresh();
}

// -- position surgery -------------------------------------------------------------------------------

/** Keys on this assembly that carry one value per axial position. */
function parallelKeys(assembly) {
  const length = (assembly.blocks || []).length;
  const keys = new Set(PER_POSITION);
  for (const [key, value] of Object.entries(assembly)) {
    if (key === "material modifications") continue;
    if (Array.isArray(value) && value.length === length) keys.add(key);
  }
  return [...keys];
}

function eachModifierList(assembly, visit) {
  const mods = assembly["material modifications"];
  if (!mods) return;
  for (const [key, value] of Object.entries(mods)) {
    if (key === "byComponent") continue;
    if (Array.isArray(value)) mods[key] = visit(value);
  }
  for (const byModifier of Object.values(mods.byComponent || {})) {
    for (const [key, value] of Object.entries(byModifier || {})) {
      if (Array.isArray(value)) byModifier[key] = visit(value);
    }
  }
}

function movePosition(ctx, assemblyName, from, to) {
  if (from === to || from + 1 === to) return;
  store.edit(`move position ${from}`, (draft) => {
    const assembly = draft.assemblies[assemblyName];
    const shift = (list) => {
      const copy = [...list];
      const [taken] = copy.splice(from, 1);
      copy.splice(from < to ? to - 1 : to, 0, taken);
      return copy;
    };
    for (const key of parallelKeys(assembly)) {
      if (Array.isArray(assembly[key])) assembly[key] = shift(assembly[key]);
    }
    eachModifierList(assembly, shift);
  });
  ctx.state.position = from < to ? to - 1 : to;
  ctx.refresh();
}

function insertPosition(ctx, assemblyName, at) {
  const assembly = store.section("assemblies")[assemblyName];
  const source = Math.max(0, Math.min(at, (assembly.blocks || []).length - 1));
  store.edit("add a position", (draft) => {
    const target = draft.assemblies[assemblyName];
    const insert = (list, value) => {
      const copy = [...list];
      copy.splice(at, 0, value);
      return copy;
    };
    for (const key of parallelKeys(target)) {
      if (!Array.isArray(target[key])) continue;
      // Copy the neighbouring position rather than guessing, so a new slice starts out plausible.
      target[key] = insert(target[key], target[key][source] ?? null);
    }
    eachModifierList(target, (list) => insert(list, list[source] ?? null));
  });
  ctx.state.position = at;
  ctx.refresh();
}

async function removePosition(ctx, assemblyName, index) {
  const assembly = store.section("assemblies")[assemblyName];
  if ((assembly.blocks || []).length <= 1) {
    toast("An assembly needs at least one block", { kind: "error" });
    return;
  }
  store.edit(`remove position ${index}`, (draft) => {
    const target = draft.assemblies[assemblyName];
    const drop = (list) => list.filter((_, i) => i !== index);
    for (const key of parallelKeys(target)) {
      if (Array.isArray(target[key])) target[key] = drop(target[key]);
    }
    eachModifierList(target, drop);
  });
  ctx.state.position = Math.max(0, index - 1);
  ctx.refresh();
}

// -- inspector ------------------------------------------------------------------------------------

function renderInspector(ctx, name, assembly) {
  // A specifier claimed twice is not an error ARMI raises: it keys assembly designs by specifier
  // in a plain dict, so one design is silently lost and every position asking for that code gets
  // the other one. Nothing else will tell you, so this does.
  const sharing = store
    .assemblyNames()
    .filter((each) => each !== name && store.section("assemblies")[each]?.specifier === assembly.specifier);

  const setAssembly = (label, mutate) => store.edit(label, (draft) => mutate(draft.assemblies[name]));
  const position = ctx.state.position;
  const blockName = position != null ? assembly.blocks?.[position] : null;

  const sections = [
    group(
      "Assembly",
      field("Name", textInput({
        value: name, mono: true,
        onChange: (value) => renameAssembly(ctx, name, value),
      })),
      h("div.row",
        field("Specifier", textInput({
          value: assembly.specifier || "",
          mono: true,
          invalid: sharing.length > 0,
          onChange: (value) => setAssembly("change specifier", (target) => {
            target.specifier = value.toUpperCase();
          }),
        }), {
          hint: sharing.length
            ? `Also used by ${sharing.join(", ")}. Only one of them will be loaded.`
            : "How it is written in the core map.",
          warn: sharing.length > 0,
        }),
        field("Flags", textInput({
          value: assembly.flags || "",
          mono: true,
          placeholder: "from the name",
          onChange: (value) => setAssembly("change flags", (target) => {
            target.flags = value || null;
          }),
        })),
      ),
      h("div.row",
        field("Radial mesh", numberInput({
          value: assembly["radial mesh points"],
          onChange: (value) => setAssembly("change radial mesh", (target) => {
            target["radial mesh points"] = value;
          }),
        })),
        field("Azimuthal mesh", numberInput({
          value: assembly["azimuthal mesh points"],
          onChange: (value) => setAssembly("change azimuthal mesh", (target) => {
            target["azimuthal mesh points"] = value;
          }),
        })),
      ),
    ),
  ];

  if (blockName) sections.push(positionPanel(ctx, name, assembly, position, blockName));

  fill(
    ctx.inspector,
    h("div.panel__head", h("span.panel__title", "Assembly"), h("span.panel__count", assembly.specifier || "")),
    h("div.panel__body", { style: { padding: 0 } }, ...sections),
  );
}

const previewPosition = debounce(async (block, done, failed) => {
  try {
    done(await api.previewBlock(block));
  } catch (error) {
    failed(error);
  }
}, 140);

function positionPanel(ctx, assemblyName, assembly, index, blockName) {
  const holder = h("div", { style: { display: "grid", justifyItems: "center", gap: "8px" } },
    h("div.field__hint", "Drawing…"));
  const block = store.section("blocks")[blockName];
  if (block) {
    previewPosition(
      block,
      (preview) => {
        const { node } = drawCrossSection(preview, { size: 260 });
        fill(holder, node, h("div.field__hint", `${num(preview.extent * 2, 3)} cm across`));
      },
      (error) => fill(holder, h("div.field__hint", { style: { color: "var(--alarm)" } }, error.message)),
    );
  }

  const elevation = (assembly.height || []).slice(0, index).reduce((sum, each) => sum + (Number(each) || 0), 0);

  return groupWith(
    `Position ${index}`,
    h("button.btn.btn--sm", { type: "button", onclick: () => ctx.go("blocks", { selected: blockName }) }, "Edit block"),
    h("div.field__hint", { style: { marginBottom: "8px" } },
      `${blockName} — from ${num(elevation, 2)} to ${num(elevation + (Number(assembly.height?.[index]) || 0), 2)} cm`),
    holder,
    h("div.row", { style: { marginTop: "10px" } },
      h("button.btn.btn--sm", { type: "button", onclick: () => insertPosition(ctx, assemblyName, index) }, "Insert above"),
      h("button.btn.btn--sm", { type: "button", onclick: () => insertPosition(ctx, assemblyName, index + 1) }, "Insert below"),
    ),
  );
}

// -- actions --------------------------------------------------------------------------------------

async function addAssembly(ctx) {
  const name = await promptName("New assembly", { taken: store.assemblyNames() });
  if (!name) return;
  const firstBlock = Object.keys(store.section("blocks"))[0];
  if (!firstBlock) {
    toast("Define a block first", { kind: "error", detail: "An assembly is a stack of blocks." });
    return;
  }
  const used = new Set(Object.values(store.section("assemblies")).map((each) => each?.specifier));
  let specifier = "A1";
  for (let i = 1; used.has(specifier); i += 1) specifier = `A${i}`;

  store.edit(`add assembly ${name}`, (draft) => {
    draft.assemblies = draft.assemblies || {};
    draft.assemblies[name] = {
      specifier,
      flags: null,
      blocks: [firstBlock],
      height: [10.0],
      "axial mesh points": [1],
      "xs types": ["A"],
    };
  });
  ctx.state.selected = name;
  ctx.state.position = 0;
  ctx.refresh();
}

function duplicateAssembly(ctx, from) {
  const name = uniqueName(`${from} copy`, store.assemblyNames());
  const used = new Set(Object.values(store.section("assemblies")).map((each) => each?.specifier));
  let specifier = "A1";
  for (let i = 1; used.has(specifier); i += 1) specifier = `A${i}`;
  store.edit(`duplicate ${from}`, (draft) => {
    draft.assemblies[name] = structuredClone(draft.assemblies[from]);
    draft.assemblies[name].specifier = specifier;
  });
  ctx.state.selected = name;
  ctx.refresh();
}

async function removeAssembly(ctx, name) {
  const specifier = store.section("assemblies")[name]?.specifier;
  const placed = Object.values(store.section("grids"))
    .flatMap((grid) => Object.values(grid.cells || {}))
    .filter((value) => value === specifier).length;
  const message = placed
    ? `${name} is placed at ${placed} core positions. Those positions will be left pointing at a specifier that no longer exists.`
    : `Remove ${name}?`;
  if (!(await confirm("Delete assembly", message))) return;
  store.edit(`delete assembly ${name}`, (draft) => {
    delete draft.assemblies[name];
  });
  ctx.state.selected = null;
  ctx.refresh();
}

function renameAssembly(ctx, from, to) {
  const target = (to || "").trim();
  if (!target || target === from) return;
  if (store.section("assemblies")[target]) {
    toast(`There is already an assembly called ${target}`, { kind: "error" });
    return;
  }
  store.edit(`rename assembly to ${target}`, (draft) => {
    const rebuilt = {};
    for (const [name, value] of Object.entries(draft.assemblies)) rebuilt[name === from ? target : name] = value;
    draft.assemblies = rebuilt;
  });
  ctx.state.selected = target;
  ctx.refresh();
}
