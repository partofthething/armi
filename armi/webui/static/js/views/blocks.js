// Blocks, and the components inside them.
//
// The cross section is the point of this view. A block is a list of radii in a YAML file, and the
// only way to know whether the list says what you meant is to look at it. So the drawing redraws
// as you type, at cold input dimensions, and it draws even while the block is too incomplete to
// construct.

import { api } from "../api.js";
import { h, fill, num, uniqueName, debounce } from "../dom.js";
import { store } from "../store.js";
import { drawCrossSection } from "../render/xsection.js";
import {
  checkbox, comboInput, confirm, field, group, groupWith, numberInput,
  promptName, selectInput, tagList, textInput, toast,
} from "../ui.js";

const DIMENSION_UNITS = {
  od: "cm", id: "cm", op: "cm", ip: "cm", iR: "cm", oR: "cm",
  widthOuter: "cm", widthInner: "cm", lengthOuter: "cm", lengthInner: "cm",
  heightOuter: "cm", heightInner: "cm", base: "cm", height: "cm",
  holeOD: "cm", holeOP: "cm", holeRadFromCenter: "cm", helixDiameter: "cm", axialPitch: "cm",
  inner_radius: "cm", outer_radius: "cm", radius_differential: "cm",
  inner_theta: "rad", outer_theta: "rad", azimuthal_differential: "rad",
  area: "cm²", volume: "cm³",
};

export function blocksView(ctx) {
  const blocks = store.section("blocks");
  const names = Object.keys(blocks);
  const blockName = names.includes(ctx.state.selected) ? ctx.state.selected : names[0];

  renderNavigator(ctx, names, blockName);

  if (!blockName) {
    fill(ctx.stage, h("div.empty", { style: { marginTop: "18vh" } },
      h("strong", "No blocks yet"),
      "A block is one axial slice of an assembly: the pins, the coolant, the duct."));
    fill(ctx.inspector);
    return;
  }

  renderStage(ctx, blockName);
  renderInspector(ctx, blockName);
}

// -- navigator ------------------------------------------------------------------------------------

function renderNavigator(ctx, names, selected) {
  const rows = names.map((name) => {
    const block = store.section("blocks")[name] || {};
    const componentCount = Object.values(block).filter((value) => value && typeof value === "object").length;
    const users = store.assembliesUsingBlock(name);
    return h(
      "button.item",
      {
        type: "button",
        "aria-selected": String(name === selected),
        onclick: () => {
          ctx.state.selected = name;
          ctx.state.component = null;
          ctx.refresh();
        },
      },
      h("span.item__name", name),
      h("span.item__meta", `${componentCount}c`),
      h("span.item__meta", users.length ? `${users.length}a` : "—"),
    );
  });

  fill(
    ctx.nav,
    h("div.panel__head", h("span.panel__title", "Blocks"), h("span.panel__count", String(names.length))),
    h("div.panel__body", rows.length ? h("div", ...rows) : h("div.empty", "None defined.")),
    h("div.panel__foot", h("div.row",
      h("button.btn.btn--sm", { type: "button", onclick: () => addBlock(ctx) }, "New"),
      h("button.btn.btn--sm", { type: "button", disabled: !selected,
        onclick: () => duplicateBlock(ctx, selected) }, "Duplicate"),
      h("button.btn.btn--sm.btn--danger", { type: "button", disabled: !selected,
        onclick: () => removeBlock(ctx, selected) }, "Delete"),
    )),
  );
}

// -- stage ----------------------------------------------------------------------------------------

function renderStage(ctx, blockName) {
  const block = store.section("blocks")[blockName] || {};
  const gridName = block["grid name"];
  const body = h("div.stage__body");
  const wrap = h("div.sheet-wrap", h("div.empty", "Drawing…"));
  body.appendChild(wrap);

  const bar = h(
    "div.stage__bar",
    h("span.stage__title", blockName),
    gridName
      ? h("button.btn.btn--sm", { type: "button", onclick: () => ctx.go("grids", { selected: gridName }) },
          `on lattice ${gridName}`)
      : h("span.item__meta", "concentric, no lattice"),
    h("span.spacer"),
    h("span.item__meta", { id: "xs-extent" }, ""),
  );

  fill(ctx.stage, bar, body);
  paint(ctx, blockName, wrap);
}

const requestPreview = debounce(async (block, done, failed) => {
  try {
    done(await api.previewBlock(block));
  } catch (error) {
    failed(error);
  }
}, 120);

function paint(ctx, blockName, wrap) {
  const block = store.section("blocks")[blockName];
  requestPreview(
    block,
    (preview) => {
      if (store.section("blocks")[blockName] !== block) return;
      // Leave room beside the drawing for the area table, and under it for the material legend.
      const size = Math.min((wrap.clientWidth || 880) - 360, (wrap.clientHeight || 600) - 90, 720);
      const { node, materials } = drawCrossSection(preview, {
        size: Math.max(300, size),
        emphasis: ctx.state.component,
        onPick: (name) => {
          ctx.state.component = name;
          ctx.refresh();
        },
      });
      const unresolved = preview.components.filter((component) => component.unresolved);
      fill(
        wrap,
        h("div", { style: { display: "grid", gridTemplateColumns: "auto minmax(240px, 300px)",
          gap: "20px", alignItems: "start", justifyItems: "center" } },
          h("div", { style: { display: "grid", gap: "12px", justifyItems: "center" } },
            node,
            materialLegend(materials),
            unresolved.length
              ? h("div.field__hint", { style: { color: "var(--alarm)" } },
                  `Cannot resolve ${unresolved.map((c) => `${c.name}.${c.unresolved.join("/")}`).join(", ")}`)
              : null,
          ),
          compositionTable(ctx, preview),
        ),
      );
      const extent = document.getElementById("xs-extent");
      if (extent) extent.textContent = `${num(preview.extent * 2, 3)} cm across`;
    },
    (error) => fill(wrap, h("div.empty", h("strong", "Cannot draw this block"), error.message)),
  );
}

/** Area per component, which for a homogenised block is the entire content of the input. */
function compositionTable(ctx, preview) {
  const footprint = preview.footprint;
  const rows = preview.components
    .filter((component) => component.area != null)
    .sort((a, b) => b.area - a.area)
    .map((component) =>
      h(
        "tr",
        {
          style: ctx.state.component === component.name ? { outline: "1px solid var(--amber-dim)" } : null,
          onclick: () => {
            ctx.state.component = component.name;
            ctx.refresh();
          },
        },
        h("td", { style: { cursor: "pointer" } }, component.name),
        h("td.num.mono", component.count > 1 ? `\u00d7${num(component.count, 0)}` : ""),
        h("td.num.mono", num(component.area, 3)),
        h("td.num.mono", { style: { color: "var(--text-faint)" } },
          footprint ? `${((component.area / footprint) * 100).toFixed(1)}%` : ""),
      ),
    );

  const shapeless = preview.components.filter((component) => component.area == null);

  return h(
    "div",
    { style: { width: "100%", alignSelf: "start" } },
    h("div.field__label", { style: { marginBottom: "6px" } }, "Area",
      h("span.field__unit", "cm\u00b2")),
    rows.length
      ? h("table.table",
          h("thead", h("tr", h("th", "Component"), h("th", { class: "num" }, "n"),
            h("th", { class: "num" }, "Area"), h("th", { class: "num" }, "of block"))),
          h("tbody", ...rows,
            footprint
              ? h("tr", h("td", { style: { color: "var(--text-faint)" } }, "block"), h("td", ""),
                  h("td.num.mono", num(footprint, 3)), h("td.num.mono", ""))
              : null))
      : h("div.field__hint", "Nothing here has an area yet."),
    shapeless.length
      ? h("div.field__hint", { style: { marginTop: "8px" } },
          `No area for ${shapeless.map((component) => component.name).join(", ")}.`)
      : null,
  );
}

function materialLegend(materials) {
  return h(
    "div.chipbar",
    { style: { justifyContent: "center", maxWidth: "620px" } },
    ...materials.map((material) =>
      h("span.chip", h("span.chip__dot", { style: { background: material.fill, border: `1.5px solid ${material.stroke}` } }),
        material.name.split(":").pop()),
    ),
  );
}

// -- inspector ------------------------------------------------------------------------------------

function renderInspector(ctx, blockName) {
  const block = store.section("blocks")[blockName] || {};
  const componentNames = Object.keys(block).filter((key) => block[key] && typeof block[key] === "object");
  const selected = componentNames.includes(ctx.state.component) ? ctx.state.component : null;

  const sections = [
    group(
      "Block",
      field("Name", textInput({ value: blockName, mono: true, onChange: (value) => renameBlock(ctx, blockName, value) })),
      field(
        "Pin lattice",
        selectInput({
          value: block["grid name"] || "",
          blank: "none — lay components out concentrically",
          options: Object.keys(store.section("grids")),
          onChange: (value) =>
            store.edit("change block lattice", (draft) => {
              if (value) draft.blocks[blockName]["grid name"] = value;
              else delete draft.blocks[blockName]["grid name"];
            }),
        }),
        { hint: "Components claim positions on this lattice with their IDs." },
      ),
    ),
    componentListPanel(ctx, blockName, componentNames, selected),
  ];

  if (selected) sections.push(componentPanel(ctx, blockName, selected));

  const users = store.assembliesUsingBlock(blockName);
  sections.push(
    group("In assemblies",
      users.length
        ? h("div", ...users.map((name) =>
            h("button.item", { type: "button", onclick: () => ctx.go("assemblies", { selected: name }) },
              h("span.item__name", name))))
        : h("div.field__hint", "Not used in any assembly stack yet.")),
  );

  fill(
    ctx.inspector,
    h("div.panel__head", h("span.panel__title", "Block")),
    h("div.panel__body", { style: { padding: 0 } }, ...sections),
  );
}

function componentListPanel(ctx, blockName, componentNames, selected) {
  const block = store.section("blocks")[blockName];
  const rows = componentNames.map((name) => {
    const component = block[name];
    return h(
      "button.item",
      {
        type: "button",
        "aria-selected": String(name === selected),
        onclick: () => {
          ctx.state.component = name;
          ctx.refresh();
        },
      },
      h("span.item__name", name),
      h("span.item__meta", component.shape || "?"),
      h("span.item__meta", (component.material || "").split(":").pop()),
    );
  });

  return groupWith(
    "Components",
    h("button.btn.btn--sm", { type: "button", onclick: () => addComponent(ctx, blockName) }, "Add"),
    rows.length ? h("div", ...rows) : h("div.field__hint", "This block is empty."),
  );
}

function componentPanel(ctx, blockName, componentName) {
  const component = store.section("blocks")[blockName][componentName];
  const catalog = store.catalog || {};
  const shapes = catalog.shapes || [];
  const entry = shapes.find((shape) => shape.name === component.shape) || null;
  const siblings = Object.keys(store.section("blocks")[blockName])
    .filter((key) => key !== componentName && typeof store.section("blocks")[blockName][key] === "object");

  const setComponent = (label, mutate) =>
    store.edit(label, (draft) => mutate(draft.blocks[blockName][componentName]));

  // Dimensions can point at another component's dimension, which is how a gap knows where the
  // fuel stopped. Offering those as suggestions saves a trip to the documentation.
  const linkSuggestions = siblings.flatMap((sibling) => {
    const other = store.section("blocks")[blockName][sibling];
    const otherEntry = shapes.find((shape) => shape.name === other.shape);
    return (otherEntry?.dimensions || []).map((dim) => `${sibling}.${dim}`);
  });

  const dimensionFields = (entry?.dimensions || []).map((dimension) => {
    const value = component[dimension];
    const linked = typeof value === "string";
    return h(
      "div",
      { style: { display: "grid", gap: "2px", marginBottom: "8px" } },
      h("span.field__label", dimension,
        h("span.field__unit", DIMENSION_UNITS[dimension] || ""),
        h("button.btn.btn--quiet.btn--sm", {
          type: "button",
          style: { marginLeft: "auto", padding: "0 6px" },
          title: linked ? "Use a number instead" : "Link to another component's dimension",
          onclick: () => setComponent(`change ${dimension}`, (target) => {
            target[dimension] = linked ? null : `${siblings[0] || ""}.od`;
          }),
        }, linked ? "number" : "link"),
      ),
      linked
        ? comboInput({
            value,
            options: linkSuggestions,
            placeholder: "component.dimension",
            onChange: (next) => setComponent(`link ${dimension}`, (target) => {
              target[dimension] = next;
            }),
          })
        : numberInput({
            value,
            onChange: (next) => setComponent(`change ${dimension}`, (target) => {
              target[dimension] = next;
            }),
          }),
    );
  });

  return groupWith(
    componentName,
    h("button.btn.btn--sm.btn--danger", { type: "button",
      onclick: () => removeComponent(ctx, blockName, componentName) }, "Remove"),
    field("Name", textInput({
      value: componentName,
      mono: true,
      onChange: (value) => renameComponent(ctx, blockName, componentName, value),
    })),
    field("Shape", selectInput({
      value: component.shape,
      options: shapes.map((shape) => [shape.name, shape.name]),
      onChange: (value) => setComponent("change shape", (target) => {
        target.shape = value;
      }),
    }), { hint: entry?.doc }),
    field("Material", comboInput({
      value: component.material,
      options: (catalog.materials || []).flatMap((material) => [material.name, material.qualified]),
      placeholder: "material class name",
      onChange: (value) => setComponent("change material", (target) => {
        target.material = value;
      }),
    })),
    h("div.row",
      field("Cold", numberInput({
        value: component.Tinput,
        onChange: (value) => setComponent("change Tinput", (target) => {
          target.Tinput = value;
        }),
      }), { unit: "°C" }),
      field("Hot", numberInput({
        value: component.Thot,
        onChange: (value) => setComponent("change Thot", (target) => {
          target.Thot = value;
        }),
      }), { unit: "°C" }),
    ),
    ...dimensionFields,
    entry?.takesMult
      ? field("Multiplicity", numberInput({
          value: component.mult,
          onChange: (value) => setComponent("change mult", (target) => {
            target.mult = value;
          }),
        }), { hint: "Left blank when lattice IDs decide the count." })
      : null,
    field("Lattice IDs", tagList({
      values: (component.latticeIDs || []).map(String),
      placeholder: "ID",
      onChange: (values) => setComponent("change lattice IDs", (target) => {
        target.latticeIDs = values.length ? values : null;
      }),
    }), { hint: "Positions on the block's lattice this component fills." }),
    field("Custom isotopics", selectInput({
      value: component.isotopics || "",
      blank: "use the material's own",
      options: Object.keys(store.section("custom isotopics") || {}),
      onChange: (value) => setComponent("change isotopics", (target) => {
        target.isotopics = value;
      }),
    })),
    field("Flags", textInput({
      value: component.flags || "",
      mono: true,
      placeholder: "inherited from the name",
      onChange: (value) => setComponent("change flags", (target) => {
        target.flags = value || null;
      }),
    })),
  );
}

// -- actions --------------------------------------------------------------------------------------

async function addBlock(ctx) {
  const name = await promptName("New block", { taken: Object.keys(store.section("blocks")) });
  if (!name) return;
  store.edit(`add block ${name}`, (draft) => {
    draft.blocks = draft.blocks || {};
    draft.blocks[name] = {
      coolant: { shape: "DerivedShape", material: "Sodium", Tinput: 20.0, Thot: 20.0 },
    };
  });
  ctx.state.selected = name;
  ctx.state.component = "coolant";
  ctx.refresh();
}

function duplicateBlock(ctx, blockName) {
  const name = uniqueName(`${blockName} copy`, Object.keys(store.section("blocks")));
  store.edit(`duplicate ${blockName}`, (draft) => {
    draft.blocks[name] = structuredClone(draft.blocks[blockName]);
  });
  ctx.state.selected = name;
  ctx.refresh();
}

async function removeBlock(ctx, blockName) {
  const users = store.assembliesUsingBlock(blockName);
  if (users.length) {
    toast(`${blockName} is still stacked in ${users.length} assembl${users.length === 1 ? "y" : "ies"}`, {
      kind: "error",
      detail: users.join("\n"),
    });
    return;
  }
  if (!(await confirm("Delete block", `Remove ${blockName} and all its components?`))) return;
  store.edit(`delete block ${blockName}`, (draft) => {
    delete draft.blocks[blockName];
  });
  ctx.state.selected = null;
  ctx.refresh();
}

function renameBlock(ctx, from, to) {
  const target = (to || "").trim();
  if (!target || target === from) return;
  if (store.section("blocks")[target]) {
    toast(`There is already a block called ${target}`, { kind: "error" });
    return;
  }
  store.edit(`rename block to ${target}`, (draft) => {
    const blocks = {};
    for (const [name, value] of Object.entries(draft.blocks)) blocks[name === from ? target : name] = value;
    draft.blocks = blocks;
    for (const assembly of Object.values(draft.assemblies || {})) {
      if (!assembly || !Array.isArray(assembly.blocks)) continue;
      assembly.blocks = assembly.blocks.map((name) => (name === from ? target : name));
    }
  });
  ctx.state.selected = target;
  ctx.refresh();
}

async function addComponent(ctx, blockName) {
  const block = store.section("blocks")[blockName];
  const taken = Object.keys(block);
  const name = await promptName("New component", { taken });
  if (!name) return;
  store.edit(`add component ${name}`, (draft) => {
    draft.blocks[blockName][name] = { shape: "Circle", material: "HT9", Tinput: 20.0, Thot: 20.0, od: 1.0, id: 0.0 };
  });
  ctx.state.component = name;
  ctx.refresh();
}

async function removeComponent(ctx, blockName, componentName) {
  if (!(await confirm("Remove component", `Take ${componentName} out of ${blockName}?`, "Remove"))) return;
  store.edit(`remove ${componentName}`, (draft) => {
    delete draft.blocks[blockName][componentName];
  });
  ctx.state.component = null;
  ctx.refresh();
}

function renameComponent(ctx, blockName, from, to) {
  const target = (to || "").trim();
  if (!target || target === from) return;
  const block = store.section("blocks")[blockName];
  if (block[target]) {
    toast(`${blockName} already has a component called ${target}`, { kind: "error" });
    return;
  }
  store.edit(`rename component to ${target}`, (draft) => {
    const rebuilt = {};
    for (const [name, value] of Object.entries(draft.blocks[blockName])) {
      rebuilt[name === from ? target : name] = value;
    }
    draft.blocks[blockName] = rebuilt;
    // Dimension links name their target, so they have to follow the rename.
    for (const component of Object.values(rebuilt)) {
      if (!component || typeof component !== "object") continue;
      for (const [key, value] of Object.entries(component)) {
        if (typeof value === "string" && value.startsWith(`${from}.`)) {
          component[key] = `${target}.${value.slice(from.length + 1)}`;
        }
      }
    }
  });
  ctx.state.component = target;
  ctx.refresh();
}
