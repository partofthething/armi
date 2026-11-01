// The shared lattice editing surface.
//
// A core map and a pin map are the same interaction with different vocabulary: pick a class from a
// palette, paint it onto positions. Only the palette and the side panels differ, so both views are
// built from this and supply their own.

import { h, fill } from "../dom.js";
import { LatticeView } from "../render/lattice.js";
import { EMPTY, assign, swatchStyle } from "../palette.js";
import { toast } from "../ui.js";

/** Ring index of a position, counting the centre as ring 1. */
export function ringOf(i, j, shape) {
  return shape === "hex" ? Math.max(Math.abs(i), Math.abs(j), Math.abs(i + j)) + 1 : Math.max(Math.abs(i), Math.abs(j)) + 1;
}

/**
 * Put a lattice editor on the stage.
 *
 * @param {object} ctx the view context from main.js
 * @param {object} spec
 * @param {string} spec.gridName which grid to edit
 * @param {Array} spec.palette `{key, label, detail}` entries offered for painting
 * @param {function} spec.onSelectCell called with `{i, j, key, spec}` when a cell is clicked
 * @param {function} [spec.onNewClass] offered as an "add" button under the palette
 * @param {Node[]} [spec.extraTools] extra controls for the stage bar
 * @returns {object} `{looks, counts, lattice}` so the caller can build a matching legend
 */
export function mountLatticeEditor(ctx, spec) {
  const { store, state } = ctx;
  const grid = store.section("grids")[spec.gridName];
  if (!grid) {
    fill(ctx.stage, h("div.empty", h("strong", "That lattice is gone"), "It may have been renamed."));
    return null;
  }
  const geometry = store.geometry[spec.gridName] || {};
  const cells = grid.cells || {};
  const counts = tally(cells);
  const looks = assign([...Object.keys(counts), ...spec.palette.map((entry) => entry.key)]);

  state.tool = state.tool || "paint";
  if (state.active === undefined) state.active = spec.palette[0]?.key ?? null;
  state.showLabels = state.showLabels !== false;

  // -- the drawing ------------------------------------------------------------------------------

  const body = h("div.stage__body");
  const wrap = h("div.sheet-wrap");
  body.appendChild(wrap);

  const lattice =
    state.lattice && state.latticeFor === spec.gridName
      ? state.lattice
      : new LatticeView({
          viewport: () => ({ width: body.clientWidth || 900, height: body.clientHeight || 620 }),
          onHover: (hit) => {
            if (!hit) return ctx.status("", "", hintText(state));
            const [x, y] = [hit.x, hit.y].map((value) => value.toFixed(2));
            const units = geometry.hasPitch === false ? "grid units" : "cm";
            ctx.status(
              `i ${hit.i}  j ${hit.j}`,
              `${hit.spec ?? "empty"}   ring ${ringOf(hit.i, hit.j, geometry.shape)}   x ${x}  y ${y} ${units}`,
              hintText(state),
            );
          },
          onPaint: (hit) => applyTool(hit),
          onContext: (hit) => contextMenu(hit),
        });
  state.lattice = lattice;
  state.latticeFor = spec.gridName;

  const draw = () => {
    const live = store.section("grids")[spec.gridName] || {};
    lattice.render({
      cells: live.cells || {},
      geometry: store.geometry[spec.gridName] || {},
      looks: assign([...Object.keys(tally(live.cells || {})), ...spec.palette.map((entry) => entry.key)]),
      selected: state.selected,
      highlight: state.highlight,
      showLabels: state.showLabels,
      showIndices: state.showIndices,
      editable: state.tool !== "inspect",
    });
  };
  fill(wrap, lattice.node);
  draw();

  // -- tools ------------------------------------------------------------------------------------

  function applyTool(hit) {
    const key = `${hit.i},${hit.j}`;
    if (state.tool === "inspect") {
      state.selected = key;
      spec.onSelectCell?.({ ...hit, key });
      ctx.refresh();
      return;
    }
    if (state.tool === "pick") {
      const found = (store.section("grids")[spec.gridName].cells || {})[key];
      if (found) {
        state.active = found;
        state.tool = "paint";
        ctx.refresh();
      }
      return;
    }
    const value = state.tool === "erase" ? null : state.active;
    if (state.tool === "paint" && !value) {
      toast("Pick something from the palette first");
      return;
    }
    setCells({ [key]: value }, state.tool === "erase" ? "erase" : `place ${value}`);
    state.selected = key;
    spec.onSelectCell?.({ ...hit, key });
  }

  function setCells(changes, label) {
    store.edit(label, (draft) => {
      const target = draft.grids[spec.gridName];
      target.cells = { ...(target.cells || {}) };
      for (const [key, value] of Object.entries(changes)) {
        if (value == null) delete target.cells[key];
        else target.cells[key] = value;
      }
    });
  }

  function contextMenu(hit) {
    const live = store.section("grids")[spec.gridName].cells || {};
    const ring = ringOf(hit.i, hit.j, geometry.shape);
    const inRing = Object.keys(live).filter((key) => {
      const [i, j] = key.split(",").map(Number);
      return ringOf(i, j, geometry.shape) === ring;
    });
    const menu = [
      state.active && [`Fill ring ${ring} with ${state.active}`, () =>
        setCells(Object.fromEntries(inRing.map((key) => [key, state.active])), `fill ring ${ring}`)],
      [`Clear ring ${ring}`, () => setCells(Object.fromEntries(inRing.map((key) => [key, null])), `clear ring ${ring}`)],
      hit.spec && [`Select every ${hit.spec}`, () => {
        state.highlight = new Set(Object.entries(live).filter(([, value]) => value === hit.spec).map(([key]) => key));
        ctx.refresh();
      }],
      hit.spec && [`Pick up ${hit.spec}`, () => {
        state.active = hit.spec;
        ctx.refresh();
      }],
    ].filter(Boolean);
    popup(hit.event, menu);
  }

  // -- stage bar --------------------------------------------------------------------------------

  const tool = (id, label, title) =>
    h("button", {
      type: "button", title, "aria-pressed": String(state.tool === id),
      onclick: () => {
        state.tool = id;
        ctx.refresh();
      },
    }, label);

  const bar = h(
    "div.stage__bar",
    h("span.stage__title", spec.title || spec.gridName),
    h("span.item__meta", `${Object.keys(cells).length} placed`),
    h("div.segmented", tool("paint", "Paint", "Click or drag to place the active item"),
      tool("erase", "Erase", "Click or drag to clear positions"),
      tool("pick", "Pick", "Click a position to pick up what is in it"),
      tool("inspect", "Inspect", "Click a position to look at it without changing it")),
    h("span.spacer"),
    ...(spec.extraTools || []),
    h("button.btn.btn--sm", {
      type: "button", "aria-pressed": String(!!state.showLabels),
      onclick: () => {
        state.showLabels = !state.showLabels;
        ctx.refresh();
      },
    }, state.showLabels ? "Labels on" : "Labels off"),
    h("button.btn.btn--sm", {
      type: "button",
      onclick: () => {
        state.showIndices = !state.showIndices;
        ctx.refresh();
      },
    }, state.showIndices ? "Indices on" : "Indices off"),
    h("button.btn.btn--sm", { type: "button", title: "Zoom out", onclick: () => lattice.setZoom(lattice.zoom / 1.2) }, "−"),
    h("button.btn.btn--sm", { type: "button", title: "Fit to window", onclick: () => lattice.resetView() }, "Fit"),
    h("button.btn.btn--sm", { type: "button", title: "Zoom in", onclick: () => lattice.setZoom(lattice.zoom * 1.2) }, "+"),
  );

  fill(ctx.stage, bar, body);
  ctx.status("", "", hintText(state));

  return { looks, counts, lattice, setCells, draw };
}

/** The palette panel: what you are about to paint with. */
export function palettePanel(ctx, { palette, looks, counts, onAdd, title = "Palette" }) {
  const { state } = ctx;
  const rows = palette.map((entry) => {
    const look = looks.get(entry.key);
    return h(
      "button.legend__row",
      {
        type: "button",
        "aria-pressed": String(state.active === entry.key),
        title: entry.detail || entry.key,
        onclick: () => {
          state.active = entry.key;
          state.tool = state.tool === "inspect" ? "paint" : state.tool;
          ctx.refresh();
        },
      },
      h("span.legend__chip", { style: swatchStyle(look) }),
      h("span.legend__label", entry.key),
      h("span.legend__count", entry.label || ""),
      h("span.legend__count", counts[entry.key] ? `×${counts[entry.key]}` : ""),
    );
  });

  return h(
    "section.group",
    h(
      "h3.group__title",
      title,
      onAdd ? h("button.btn.btn--sm", { type: "button", onclick: onAdd }, "Add") : null,
    ),
    rows.length ? h("div.legend", ...rows) : h("div.field__hint", "Nothing to paint with yet."),
    h(
      "button.legend__row",
      {
        type: "button",
        style: { marginTop: "6px" },
        "aria-pressed": String(state.tool === "erase"),
        onclick: () => {
          state.tool = "erase";
          ctx.refresh();
        },
      },
      h("span.legend__chip", { style: swatchStyle(null) }),
      h("span.legend__label", EMPTY),
      h("span.legend__count", "erase"),
    ),
  );
}

export function tally(cells) {
  const counts = {};
  for (const value of Object.values(cells)) counts[value] = (counts[value] || 0) + 1;
  return counts;
}

function hintText(state) {
  const verb = { paint: "Click or drag to place", erase: "Click or drag to clear", pick: "Click to pick up", inspect: "Click to inspect" }[state.tool];
  return `${verb}  ·  right-click for ring actions  ·  alt-drag to pan  ·  ctrl-wheel to zoom`;
}

/** A small context menu at the pointer. */
function popup(event, entries) {
  document.querySelector(".ctxmenu")?.remove();
  const menu = h(
    "div.ctxmenu",
    {
      style: {
        position: "fixed",
        left: `${event.clientX}px`,
        top: `${event.clientY}px`,
        zIndex: 60,
        background: "var(--chrome-2)",
        border: "1px solid var(--line)",
        borderRadius: "5px",
        padding: "4px",
        boxShadow: "0 12px 30px rgba(0,0,0,0.5)",
        minWidth: "190px",
      },
    },
    ...entries.map(([label, action]) =>
      h("button.item", {
        type: "button",
        onclick: () => {
          menu.remove();
          action();
        },
      }, h("span.item__name", label)),
    ),
  );
  document.body.appendChild(menu);
  const dismiss = (dismissEvent) => {
    if (menu.contains(dismissEvent.target)) return;
    menu.remove();
    document.removeEventListener("pointerdown", dismiss);
  };
  setTimeout(() => document.addEventListener("pointerdown", dismiss), 0);
}
