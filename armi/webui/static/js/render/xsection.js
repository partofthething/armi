// A block, drawn in cross section.
//
// This is a schematic of the input at cold dimensions, not a picture of a constructed block. It
// draws whatever is currently typed in, including half-finished states, which is the point: you
// want to see the pin you are describing while you are still describing it.
//
// Components are coloured by material, because that is the question a cross section answers - what
// is this ring made of - and materials are what a reviewer scans for.

import { s } from "../dom.js";
import { materialLook } from "../palette.js";

/**
 * Draw a block preview.
 *
 * @param {object} preview from `POST /api/preview/block`
 * @param {object} options
 * @param {number} options.size pixel size of the square drawing
 * @param {string|null} options.emphasis component name to pick out with heavier ink
 * @param {function} [options.onPick] called with a component name when one is clicked
 * @param {object} [options.view] ``{zoom, cx, cy}``, the part of the block on show. The drawing reads it and
 *   writes to it as you zoom (ctrl-wheel or pinch) and pan (drag), so passing the same object to the next
 *   drawing keeps the view across redraws. Double-click fits the block again.
 * @returns {{node: SVGElement, materials: Array}} the drawing and the material key it used
 */
export function drawCrossSection(preview, options = {}) {
  const size = options.size || 460;
  const extent = preview.extent || 1;
  const pad = extent * 0.1;
  const span = (extent + pad) * 2;

  const view = options.view || {};
  const node = s("svg.sheet", { width: size, height: size, role: "img" });

  const materials = materialKey(preview.components);
  const flipped = s("g", { transform: "scale(1,-1)" });
  node.appendChild(flipped);

  // Screen pixels, for the same reason as the lattice: these edges say what a thing is, and they
  // have to stay legible whether the block is 1 cm across or 30.
  const hair = 1;
  for (const component of preview.components) {
    const look = materials.get(component.material || "—");
    const emphasised = options.emphasis === component.name;
    const group = s("g", {
      dataset: { component: component.name },
      style: options.onPick ? "cursor:pointer" : null,
      onclick: options.onPick ? () => options.onPick(component.name) : null,
    });
    for (const [x, y] of component.positions) {
      const shape = markFor(component, x, y, look, hair, emphasised);
      if (shape) group.appendChild(shape);
    }
    if (group.childNodes.length) flipped.appendChild(group);
  }

  const bar = s("g", { style: "pointer-events:none" });
  node.appendChild(bar);
  const show = () => {
    const width = span / (view.zoom || 1);
    const x = (view.cx || 0) - width / 2;
    const y = (view.cy || 0) - width / 2;
    node.setAttribute("viewBox", `${x} ${y} ${width} ${width}`);
    bar.replaceChildren(...scaleBar(x, y + width, width));
  };
  show();
  zoomAndPan(node, view, span, size, show);
  return { node, materials: [...materials.entries()].map(([name, look]) => ({ name, ...look })) };
}

function markFor(component, x, y, look, hair, emphasised) {
  const outline = component.outline;
  const stroke = emphasised ? "#11151a" : look.stroke;
  const width = emphasised ? 2.6 : 1;
  const common = {
    fill: look.fill,
    stroke,
    "stroke-width": width,
    "vector-effect": "non-scaling-stroke",
  };

  switch (outline.draw) {
    case "annulus": {
      if (!outline.outer) return null;
      const outer = outline.outer / 2;
      const inner = (outline.inner || 0) / 2;
      if (inner > 0) {
        // Even-odd on two subpaths is what makes the bore actually show through, rather than
        // painting a smaller disc of background over the top of it.
        const ring = (r, cx, cy) =>
          `M ${cx - r},${cy} a ${r},${r} 0 1,0 ${2 * r},0 a ${r},${r} 0 1,0 ${-2 * r},0 Z`;
        return s("path", { ...common, d: `${ring(outer, x, y)} ${ring(inner, x, y)}`, "fill-rule": "evenodd" });
      }
      return s("circle", { ...common, cx: x, cy: y, r: outer });
    }
    case "hexagon": {
      if (!outline.outer) return null;
      return s("polygon", { ...common, points: hexPoints(x, y, outline.outer, outline.inner) });
    }
    case "square": {
      if (!outline.outer) return null;
      return squareish(common, x, y, outline.outer, outline.outer, outline.inner, outline.inner);
    }
    case "rectangle": {
      if (!outline.lengthOuter && !outline.widthOuter) return null;
      return squareish(
        common, x, y,
        outline.lengthOuter || outline.widthOuter,
        outline.widthOuter || outline.lengthOuter,
        outline.lengthInner, outline.widthInner,
      );
    }
    case "triangle": {
      if (!outline.base || !outline.height) return null;
      const halfBase = outline.base / 2;
      return s("polygon", {
        ...common,
        points: `${x - halfBase},${y - outline.height / 2} ${x + halfBase},${y - outline.height / 2} ${x},${y + outline.height / 2}`,
      });
    }
    case "helix": {
      if (!outline.helixDiameter) return null;
      // A wire wrap is a circle traced around the pin; showing where it runs is more useful than
      // trying to draw one turn of it in plan view.
      return s("circle", {
        cx: x, cy: y, r: outline.helixDiameter / 2,
        fill: "none", stroke: look.stroke,
        "stroke-width": outline.outer || hair * 3,
        "stroke-dasharray": `${(outline.outer || hair * 3) * 2.4} ${(outline.outer || hair * 3) * 1.8}`,
        opacity: 0.85,
      });
    }
    default:
      return null;
  }
}

function squareish(common, x, y, width, height, innerWidth, innerHeight) {
  const halfW = width / 2;
  const halfH = height / 2;
  if (innerWidth > 0 && innerHeight > 0) {
    const iw = innerWidth / 2;
    const ih = innerHeight / 2;
    return s("path", {
      ...common,
      "fill-rule": "evenodd",
      d:
        `M ${x - halfW},${y - halfH} H ${x + halfW} V ${y + halfH} H ${x - halfW} Z ` +
        `M ${x - iw},${y - ih} H ${x + iw} V ${y + ih} H ${x - iw} Z`,
    });
  }
  return s("rect", { ...common, x: x - halfW, y: y - halfH, width, height });
}

function hexPoints(cx, cy, flatToFlat, innerFlatToFlat) {
  const ring = (f) =>
    Array.from({ length: 6 }, (_, k) => {
      const angle = (k * Math.PI) / 3;
      const r = f / Math.sqrt(3);
      return `${cx + r * Math.cos(angle)},${cy + r * Math.sin(angle)}`;
    }).join(" ");
  return innerFlatToFlat > 0 ? ring(flatToFlat) : ring(flatToFlat);
}

/** One colour per material, assigned in a stable order so a block looks the same every visit. */
function materialKey(components) {
  const names = [...new Set(components.map((component) => component.material || "—"))].sort((a, b) =>
    a.localeCompare(b),
  );
  const key = new Map();
  names.forEach((name, index) => key.set(name, materialLook(name, index)));
  return key;
}

/** A scale bar in the bottom margin of whatever part of the drawing is on show (screen coordinates). */
function scaleBar(left, bottom, width) {
  const length = niceStep(width * 0.9);
  const x0 = left + width * 0.045;
  const y0 = bottom - width * 0.02;
  const type = width * 0.026;
  return [
    s("line", { x1: x0, x2: x0 + length, y1: y0, y2: y0, stroke: "var(--sheet)", "stroke-width": width * 0.012 }),
    s("line", { x1: x0, x2: x0 + length, y1: y0, y2: y0, stroke: "#4c545e", "stroke-width": width * 0.004 }),
    s("text", {
      x: x0 + length + width * 0.014, y: y0 + type * 0.35,
      "text-anchor": "start", "font-size": type, fill: "#6b7480", "font-family": "var(--mono)",
      stroke: "var(--sheet)", "stroke-width": type * 0.25, "paint-order": "stroke",
    }, `${Number(length.toPrecision(3))} cm`),
  ];
}

/** Ctrl-wheel (or pinch) zooms about the cursor, a drag pans, a double-click fits; all through ``view``. */
function zoomAndPan(node, view, span, size, show) {
  const toDrawing = (event) => new DOMPoint(event.clientX, event.clientY).matrixTransform(node.getScreenCTM().inverse());
  const visible = () => span / (view.zoom || 1);

  node.addEventListener("wheel", (event) => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    const held = toDrawing(event);
    const before = visible();
    view.zoom = Math.min(200, Math.max(0.5, (view.zoom || 1) * Math.exp(-event.deltaY * 0.0015)));
    // Keep the point under the cursor where it is: it sits at the same fraction of the new view.
    const ratio = visible() / before;
    view.cx = held.x + ((view.cx || 0) - held.x) * ratio;
    view.cy = held.y + ((view.cy || 0) - held.y) * ratio;
    show();
  }, { passive: false });

  let drag = null;
  let justDragged = false;
  // The click that ends a drag is not a pick.
  node.addEventListener("click", (event) => {
    if (justDragged) event.stopPropagation();
  }, { capture: true });
  node.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 && event.button !== 1) return;
    drag = { x: event.clientX, y: event.clientY, cx: view.cx || 0, cy: view.cy || 0, moved: false, id: event.pointerId };
  });
  node.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < 4) return; // still a click
    if (!drag.moved) {
      drag.moved = true;
      node.setPointerCapture(drag.id);
      node.style.cursor = "grabbing";
    }
    const perPixel = visible() / size;
    view.cx = drag.cx - dx * perPixel;
    view.cy = drag.cy - dy * perPixel;
    show();
  });
  const release = () => {
    if (drag?.moved) {
      node.style.cursor = "";
      justDragged = true;
      setTimeout(() => { justDragged = false; }, 0);
    }
    drag = null;
  };
  node.addEventListener("pointerup", release);
  node.addEventListener("pointercancel", release);
  node.addEventListener("dblclick", () => {
    view.zoom = 1;
    view.cx = 0;
    view.cy = 0;
    show();
  });
}

function niceStep(span) {
  const target = span / 4;
  const magnitude = 10 ** Math.floor(Math.log10(target));
  for (const multiple of [1, 2, 2.5, 5, 10]) {
    if (magnitude * multiple >= target) return magnitude * multiple;
  }
  return magnitude * 10;
}
