// The shared component library and component groups.
//
// Most components live inside a block. These are the ones that do not: definitions written once
// under `components:` and referred to from elsewhere, and the groups that bundle them with
// multiplicities. Small sections, but they are part of the file, so they are part of the editor.

import { h, fill, num, uniqueName } from "../dom.js";
import { store } from "../store.js";
import { drawCrossSection } from "../render/xsection.js";
import { api } from "../api.js";
import {
  comboInput, confirm, field, group, groupWith, numberInput, promptName, selectInput, textInput, toast,
} from "../ui.js";

export function componentsView(ctx) {
  const components = store.section("components") || {};
  const groups = store.section("component groups") || {};
  const names = Object.keys(components);
  const selected = names.includes(ctx.state.selected) ? ctx.state.selected : names[0];

  renderNavigator(ctx, names, Object.keys(groups), selected);

  if (!names.length && !Object.keys(groups).length) {
    fill(ctx.stage, h("div.empty", { style: { marginTop: "16vh" } },
      h("strong", "No shared components"),
      h("p", { style: { maxWidth: "46ch", margin: "6px auto" } },
        "Components defined here can be referred to from anywhere in the file. " +
        "Most designs never need them: components written directly inside a block are usually clearer."),
      h("button.btn", { type: "button", onclick: () => addComponent(ctx) }, "Define one anyway")));
    fill(ctx.inspector);
    return;
  }

  if (selected) renderStage(ctx, selected, components[selected]);
  else fill(ctx.stage, h("div.empty", "Pick a component."));

  renderInspector(ctx, selected, groups);
}

function renderNavigator(ctx, names, groupNames, selected) {
  const components = store.section("components") || {};
  fill(
    ctx.nav,
    h("div.panel__head", h("span.panel__title", "Shared parts"), h("span.panel__count", String(names.length))),
    h("div.panel__body",
      names.length
        ? h("div", ...names.map((name) =>
            h("button.item", {
              type: "button",
              "aria-selected": String(name === selected),
              onclick: () => {
                ctx.state.selected = name;
                ctx.refresh();
              },
            },
            h("span.item__name", name),
            h("span.item__meta", components[name]?.shape || ""),
          )))
        : h("div.empty", "None defined."),
      groupNames.length
        ? h("div", { style: { marginTop: "12px" } },
            h("div.field__label", { style: { padding: "0 8px 4px" } }, "Groups"),
            ...groupNames.map((name) =>
              h("button.item", { type: "button" }, h("span.item__name", name))))
        : null,
    ),
    h("div.panel__foot", h("div.row",
      h("button.btn.btn--sm", { type: "button", onclick: () => addComponent(ctx) }, "New"),
      h("button.btn.btn--sm.btn--danger", { type: "button", disabled: !selected,
        onclick: () => removeComponent(ctx, selected) }, "Delete"),
    )),
  );
}

function renderStage(ctx, name, component) {
  const wrap = h("div.sheet-wrap", h("div.empty", "Drawing…"));
  fill(ctx.stage, h("div.stage__bar", h("span.stage__title", name),
    h("span.item__meta", component?.shape || "")), h("div.stage__body", wrap));

  // A lone component draws the same way a block does: as a block with one thing in it.
  api
    .previewBlock({ [name]: component })
    .then((preview) => {
      const { node, materials } = drawCrossSection(preview, { size: 420 });
      fill(wrap, h("div", { style: { display: "grid", gap: "12px", justifyItems: "center" } },
        node,
        h("div.chipbar", { style: { justifyContent: "center" } },
          ...materials.map((material) =>
            h("span.chip", h("span.chip__dot", { style: { background: material.fill,
              border: `1.5px solid ${material.stroke}` } }), material.name.split(":").pop()))),
      ));
    })
    .catch((error) => fill(wrap, h("div.empty", h("strong", "Cannot draw this"), error.message)));
}

function renderInspector(ctx, name, groups) {
  if (!name) {
    fill(ctx.inspector);
    return;
  }
  const component = store.section("components")[name];
  const catalog = store.catalog || {};
  const shapes = catalog.shapes || [];
  const entry = shapes.find((shape) => shape.name === component.shape);
  const set = (label, mutate) => store.edit(label, (draft) => mutate(draft.components[name]));

  fill(
    ctx.inspector,
    h("div.panel__head", h("span.panel__title", "Component")),
    h("div.panel__body", { style: { padding: 0 } },
      group("Definition",
        field("Name", textInput({ value: name, mono: true,
          onChange: (value) => rename(ctx, name, value) })),
        field("Shape", selectInput({
          value: component.shape,
          options: shapes.map((shape) => shape.name),
          onChange: (value) => set("change shape", (target) => {
            target.shape = value;
          }),
        }), { hint: entry?.doc }),
        field("Material", comboInput({
          value: component.material,
          options: (catalog.materials || []).map((material) => material.name),
          onChange: (value) => set("change material", (target) => {
            target.material = value;
          }),
        })),
        h("div.row",
          field("Cold", numberInput({ value: component.Tinput,
            onChange: (value) => set("change Tinput", (target) => {
              target.Tinput = value;
            }) }), { unit: "°C" }),
          field("Hot", numberInput({ value: component.Thot,
            onChange: (value) => set("change Thot", (target) => {
              target.Thot = value;
            }) }), { unit: "°C" }),
        ),
        ...(entry?.dimensions || []).map((dimension) =>
          field(dimension, numberInput({
            value: component[dimension],
            onChange: (value) => set(`change ${dimension}`, (target) => {
              target[dimension] = value;
            }),
          }), { unit: "cm" }),
        ),
      ),
      Object.keys(groups).length
        ? group("Groups", ...Object.entries(groups).map(([groupName, members]) =>
            h("div", { style: { marginBottom: "10px" } },
              h("div.field__label", groupName),
              h("table.table", h("tbody", ...Object.entries(members || {}).map(([member, spec]) =>
                h("tr", h("td", member), h("td.num.mono", num(spec?.mult, 3)))))))))
        : null,
    ),
  );
}

async function addComponent(ctx) {
  const name = await promptName("New shared component", { taken: Object.keys(store.section("components") || {}) });
  if (!name) return;
  store.edit(`add component ${name}`, (draft) => {
    draft.components = draft.components || {};
    draft.components[name] = { shape: "Circle", material: "HT9", Tinput: 20.0, Thot: 20.0, od: 1.0, id: 0.0, mult: 1.0 };
  });
  ctx.state.selected = name;
  ctx.refresh();
}

async function removeComponent(ctx, name) {
  if (!(await confirm("Delete component", `Remove the shared component ${name}?`))) return;
  store.edit(`delete component ${name}`, (draft) => {
    delete draft.components[name];
  });
  ctx.state.selected = null;
  ctx.refresh();
}

function rename(ctx, from, to) {
  const target = (to || "").trim();
  if (!target || target === from) return;
  if (store.section("components")[target]) {
    toast(`There is already a component called ${target}`, { kind: "error" });
    return;
  }
  store.edit(`rename to ${target}`, (draft) => {
    const rebuilt = {};
    for (const [name, value] of Object.entries(draft.components)) rebuilt[name === from ? target : name] = value;
    draft.components = rebuilt;
  });
  ctx.state.selected = target;
  ctx.refresh();
}
