// The lattice canvas: a core map or a pin map, drawn on the sheet and editable in place.
//
// Positions come from ARMI, not from geometry reinvented here. The server hands over the two basis
// vectors that carry an (i, j) index to a position in centimetres, which is enough to place a cell
// that does not exist yet, and it is the same arithmetic ARMI uses to build the real grid. Hex,
// corners-up hex and offset Cartesian all fall out of it without a special case.
//
// Colour says what a cell is. Line weight says what state it is in. A selected cell gets heavier
// ink, never a different fill, so a core map reads the same whether or not something is selected.

import { s } from "../dom.js";
import { EMPTY, paintOf, patternSpecs } from "../palette.js";

const SQRT3 = Math.sqrt(3);
const RULER = 26;            // px reserved for the rulers
const LABEL_FIT = 0.42;      // fraction of a cell the label may occupy

export class LatticeView {
  /**
   * @param {object} options
   * @param {function} options.onHover called with `{i, j, spec, x, y}` or null
   * @param {function} options.onPaint called with `{i, j, spec, event}` on a left click or drag
   * @param {function} options.onContext called with `{i, j, spec, event}` on right click
   */
  constructor(options = {}) {
    this.options = options;
    this.zoom = 1;
    this.pan = { x: 0, y: 0 };
    this.node = s("svg.sheet", { role: "img" });
    this._painting = false;
    this._panning = null;
    this._bind();
  }

  /**
   * Draw a grid.
   *
   * @param {object} spec
   * @param {object} spec.cells `"i,j"` to specifier
   * @param {object} spec.geometry from the server: `iStep`, `jStep`, `shape`, `cornersUp`
   * @param {Map} spec.looks specifier to its colour and hatch
   * @param {string|null} spec.selected the `"i,j"` currently selected
   * @param {boolean} spec.showLabels draw the specifier inside each cell
   * @param {boolean} spec.showIndices draw the (i, j) index under each specifier
   * @param {Set|null} spec.highlight cell keys to ring, used when a legend entry is picked
   * @param {boolean} spec.editable offer empty positions around the edge to paint into
   */
  render(spec) {
    this.spec = spec;
    const { cells, geometry } = spec;
    const iStep = geometry.iStep || [1, 0];
    const jStep = geometry.jStep || [0, 1];
    this.iStep = iStep;
    this.jStep = jStep;
    this.shape = geometry.shape === "hex" ? "hex" : "square";
    this.cornersUp = !!geometry.cornersUp;
    this.units = geometry.hasPitch === false ? "units" : "cm";

    // Cell size: for hex, the pitch is flat-to-flat; for Cartesian, the two step lengths.
    this.pitch = Math.hypot(...jStep) || 1;
    this.sizeX = Math.abs(iStep[0]) || this.pitch;
    this.sizeY = Math.abs(jStep[1]) || this.pitch;

    const occupied = Object.keys(cells);
    // Which empty positions may be painted is a question about the grid's symmetry, and ARMI
    // answers it; a locally guessed halo would offer cells the writer then discards.
    const paintable = spec.editable ? (geometry.paintable || []).filter((key) => !(key in cells)) : [];
    const everything = [...occupied, ...paintable];
    const bounds = this._bounds(everything.length ? everything : ["0,0"]);

    // Margin in centimetres, because everything on the sheet is. A pin lattice at 1.4 cm pitch
    // and a core at 21.8 cm both need room for their rulers, so it scales with the drawing.
    const reach = Math.max(bounds.maxX - bounds.minX, bounds.maxY - bounds.minY);
    const margin = Math.max(this.pitch * 1.1, reach * 0.075);
    this.margin = margin;

    const width = bounds.maxX - bounds.minX + 2 * margin;
    const height = bounds.maxY - bounds.minY + 2 * margin;
    const scale = this._fit(width, height);

    this.node.replaceChildren();
    this.node.setAttribute("width", Math.round(width * scale) + RULER);
    this.node.setAttribute("height", Math.round(height * scale) + RULER);
    this.node.setAttribute(
      "viewBox",
      `${bounds.minX - margin} ${-(bounds.maxY + margin)} ${width} ${height}`,
    );
    // Screen y grows downward, model y grows upward, so the whole drawing is flipped once here
    // rather than negating a y in twenty places.
    this.node.style.overflow = "visible";
    this.scale = scale;
    this.bounds = bounds;

    const defs = s("defs");
    for (const pattern of patternSpecs(spec.looks)) defs.appendChild(hatchPattern(pattern, this.pitch));
    this.node.appendChild(defs);

    const flipped = s("g", { transform: "scale(1,-1)" });
    this.node.appendChild(flipped);

    flipped.appendChild(this._rules(bounds, margin));

    // Paintable empties first, so real cells always sit on top of them.
    const ghosts = s("g");
    for (const key of paintable) ghosts.appendChild(this._cell(key, EMPTY, spec, true));
    flipped.appendChild(ghosts);

    const body = s("g");
    for (const [key, value] of Object.entries(cells)) body.appendChild(this._cell(key, value, spec, false));
    flipped.appendChild(body);

    flipped.appendChild(this._scaleBar(bounds, margin));
    return this.node;
  }

  // -- geometry ---------------------------------------------------------------------------------

  position(i, j) {
    return [i * this.iStep[0] + j * this.jStep[0], i * this.iStep[1] + j * this.jStep[1]];
  }

  /** Nearest cell index to a point in centimetres. */
  indexAt(x, y) {
    const [ax, ay] = this.iStep;
    const [bx, by] = this.jStep;
    const determinant = ax * by - ay * bx;
    if (!determinant) return null;
    const fi = (x * by - y * bx) / determinant;
    const fj = (y * ax - x * ay) / determinant;

    // Rounding in a skewed basis can land one cell over, so try the four corners and keep the
    // closest. Cheap, and exactly right for hex as well as Cartesian.
    let best = null;
    for (const i of [Math.floor(fi), Math.ceil(fi)]) {
      for (const j of [Math.floor(fj), Math.ceil(fj)]) {
        const [px, py] = this.position(i, j);
        const distance = (px - x) ** 2 + (py - y) ** 2;
        if (!best || distance < best.distance) best = { i, j, distance };
      }
    }
    return best;
  }

  outline(i, j) {
    const [x, y] = this.position(i, j);
    if (this.shape === "hex") {
      const radius = this.pitch / SQRT3;
      const turn = this.cornersUp ? Math.PI / 6 : 0;
      return Array.from({ length: 6 }, (_, k) => {
        const angle = turn + (k * Math.PI) / 3;
        return `${x + radius * Math.cos(angle)},${y + radius * Math.sin(angle)}`;
      }).join(" ");
    }
    const halfX = this.sizeX / 2;
    const halfY = this.sizeY / 2;
    return [
      `${x - halfX},${y - halfY}`,
      `${x + halfX},${y - halfY}`,
      `${x + halfX},${y + halfY}`,
      `${x - halfX},${y + halfY}`,
    ].join(" ");
  }

  _bounds(keys) {
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    const reachX = this.shape === "hex" ? this.pitch / SQRT3 : this.sizeX / 2;
    const reachY = this.shape === "hex" ? this.pitch / 2 : this.sizeY / 2;
    for (const key of keys) {
      const [i, j] = key.split(",").map(Number);
      const [x, y] = this.position(i, j);
      minX = Math.min(minX, x - reachX);
      maxX = Math.max(maxX, x + reachX);
      minY = Math.min(minY, y - reachY);
      maxY = Math.max(maxY, y + reachY);
    }
    return { minX, maxX, minY, maxY };
  }

  _fit(width, height) {
    const box = this.options.viewport?.() || { width: 900, height: 620 };
    const available = Math.min(
      (box.width - RULER - 36) / width,
      (box.height - RULER - 36) / height,
    );
    return Math.max(2, available) * this.zoom;
  }

  // -- pieces -----------------------------------------------------------------------------------

  _cell(key, value, spec, ghost) {
    const [i, j] = key.split(",").map(Number);
    const look = spec.looks.get(value) || spec.looks.get(EMPTY);
    const selected = spec.selected === key;
    const highlighted = spec.highlight ? spec.highlight.has(key) : false;

    // These outlines carry state, so they are specified in screen pixels and pinned there with
    // non-scaling-stroke. A width in centimetres would make the same edge bold on a core map and
    // invisible on a unit grid.
    const group = s("g", { dataset: { cell: key }, style: "cursor:pointer" });
    group.appendChild(
      s("polygon", {
        points: this.outline(i, j),
        fill: ghost ? "transparent" : paintOf(look),
        stroke: selected ? "#11151a" : ghost ? "#b9bfc7" : look.stroke,
        "stroke-width": selected ? 3 : ghost ? 1 : 1.1,
        "stroke-dasharray": ghost ? "3 2.5" : null,
        "vector-effect": "non-scaling-stroke",
      }),
    );

    if (highlighted && !ghost) {
      group.appendChild(
        s("polygon", {
          points: this.outline(i, j),
          fill: "none",
          stroke: "#11151a",
          "stroke-width": 2.2,
          "stroke-dasharray": "4 3",
          "vector-effect": "non-scaling-stroke",
        }),
      );
    }

    if (!ghost && spec.showLabels) {
      const [x, y] = this.position(i, j);
      const room = this.shape === "hex" ? this.pitch * 0.82 : Math.min(this.sizeX, this.sizeY) * 0.92;
      const size = Math.min(room * LABEL_FIT, (room * 0.95) / Math.max(2, String(value).length));
      const shift = spec.showIndices ? size * 0.42 : 0;
      group.appendChild(
        s(
          "text",
          {
            x,
            y: -(y - size * 0.34 + shift),
            transform: "scale(1,-1)",
            "text-anchor": "middle",
            "font-size": size,
            "font-weight": 500,
            fill: "#1b1f24",
            style: "pointer-events:none;user-select:none",
          },
          value,
        ),
      );
      if (spec.showIndices) {
        group.appendChild(
          s(
            "text",
            {
              x,
              y: -(y - size * 1.35),
              transform: "scale(1,-1)",
              "text-anchor": "middle",
              "font-size": size * 0.62,
              fill: "#6b7480",
              style: "pointer-events:none;user-select:none",
            },
            `${i},${j}`,
          ),
        );
      }
    }
    return group;
  }

  /** Centimetre rules along the left and bottom edges, the way a drawing is dimensioned. */
  _rules(bounds, margin) {
    const group = s("g", { style: "pointer-events:none" });
    const span = Math.max(bounds.maxX - bounds.minX, bounds.maxY - bounds.minY);
    const step = niceStep(span);
    const hair = span * 0.0016;
    const type = span * 0.017;

    for (let x = Math.ceil(bounds.minX / step) * step; x <= bounds.maxX; x += step) {
      group.appendChild(
        s("line", {
          x1: x, x2: x, y1: bounds.minY - margin * 0.22, y2: bounds.maxY + margin * 0.22,
          stroke: "#e4e7ea", "stroke-width": hair,
        }),
      );
      group.appendChild(
        s("text", {
          x, y: -(bounds.minY - margin * 0.42), transform: "scale(1,-1)",
          "text-anchor": "middle", "font-size": type, fill: "#9aa2ac",
        }, round(x)),
      );
    }
    for (let y = Math.ceil(bounds.minY / step) * step; y <= bounds.maxY; y += step) {
      group.appendChild(
        s("line", {
          x1: bounds.minX - margin * 0.22, x2: bounds.maxX + margin * 0.22, y1: y, y2: y,
          stroke: "#e4e7ea", "stroke-width": hair,
        }),
      );
      group.appendChild(
        s("text", {
          x: bounds.minX - margin * 0.32, y: -(y - type * 0.35), transform: "scale(1,-1)",
          "text-anchor": "end", "font-size": type, fill: "#9aa2ac",
        }, round(y)),
      );
    }
    return group;
  }

  _scaleBar(bounds, margin) {
    const span = bounds.maxX - bounds.minX;
    const length = niceStep(span) * 2;
    const x0 = bounds.minX;
    const y0 = bounds.minY - margin * 0.82;
    const type = span * 0.019;
    return s(
      "g",
      { style: "pointer-events:none" },
      s("line", { x1: x0, x2: x0 + length, y1: y0, y2: y0, stroke: "#4c545e", "stroke-width": span * 0.0035 }),
      s("line", { x1: x0, x2: x0, y1: y0 - span * 0.008, y2: y0 + span * 0.008, stroke: "#4c545e", "stroke-width": span * 0.0035 }),
      s("line", { x1: x0 + length, x2: x0 + length, y1: y0 - span * 0.008, y2: y0 + span * 0.008, stroke: "#4c545e", "stroke-width": span * 0.0035 }),
      // Label beside the bar, not above it, so it never lands on the ruler numbers.
      s("text", {
        x: x0 + length + span * 0.012, y: -(y0 - type * 0.35), transform: "scale(1,-1)",
        "text-anchor": "start", "font-size": type, fill: "#6b7480",
      }, `${round(length)} ${this.units}`),
    );
  }

  // -- interaction --------------------------------------------------------------------------------

  _bind() {
    const node = this.node;

    node.addEventListener("pointerdown", (event) => {
      if (event.button === 1 || event.altKey) {
        this._panning = { x: event.clientX, y: event.clientY, pan: { ...this.pan } };
        node.setPointerCapture(event.pointerId);
        event.preventDefault();
        return;
      }
      if (event.button !== 0) return;
      const hit = this._hit(event);
      if (!hit) return;
      this._painting = true;
      this._lastPainted = null;
      node.setPointerCapture(event.pointerId);
      this._paint(hit, event);
    });

    node.addEventListener("pointermove", (event) => {
      if (this._panning) {
        this.pan = {
          x: this._panning.pan.x + (event.clientX - this._panning.x),
          y: this._panning.pan.y + (event.clientY - this._panning.y),
        };
        node.style.transform = `translate(${this.pan.x}px, ${this.pan.y}px)`;
        return;
      }
      const hit = this._hit(event);
      this.options.onHover?.(hit);
      if (this._painting && hit) this._paint(hit, event);
    });

    const release = (event) => {
      this._painting = false;
      this._panning = null;
      if (node.hasPointerCapture?.(event.pointerId)) node.releasePointerCapture(event.pointerId);
    };
    node.addEventListener("pointerup", release);
    node.addEventListener("pointercancel", release);
    node.addEventListener("pointerleave", () => this.options.onHover?.(null));

    node.addEventListener("contextmenu", (event) => {
      const hit = this._hit(event);
      if (!hit) return;
      event.preventDefault();
      this.options.onContext?.({ ...hit, event });
    });

    node.addEventListener("wheel", (event) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      this.setZoom(this.zoom * (event.deltaY < 0 ? 1.12 : 1 / 1.12), { x: event.clientX, y: event.clientY });
    }, { passive: false });
  }

  _paint(hit, event) {
    const key = `${hit.i},${hit.j}`;
    if (this._lastPainted === key) return;
    this._lastPainted = key;
    this.options.onPaint?.({ ...hit, event });
  }

  _hit(event) {
    if (!this.spec) return null;
    const point = this.node.createSVGPoint?.() || new DOMPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    const local = point.matrixTransform(this.node.getScreenCTM().inverse());
    // Undo the single vertical flip the whole drawing sits inside.
    const found = this.indexAt(local.x, -local.y);
    if (!found) return null;
    const key = `${found.i},${found.j}`;
    const reach = (this.shape === "hex" ? this.pitch / SQRT3 : Math.max(this.sizeX, this.sizeY) / 2) ** 2;
    if (found.distance > reach) return null;
    return { i: found.i, j: found.j, key, spec: this.spec.cells[key] ?? null, x: local.x, y: -local.y };
  }

  /** Zoom to ``zoom``, keeping the drawing under ``anchor`` (a client point) where it is. */
  setZoom(zoom, anchor = null) {
    this.zoom = Math.min(8, Math.max(0.25, zoom));
    if (!this.spec) return;
    const held = anchor && this._toModel(anchor);
    this.render(this.spec);
    if (!held) return;
    const now = this._toClient(held);
    this.pan = { x: this.pan.x + anchor.x - now.x, y: this.pan.y + anchor.y - now.y };
    this.node.style.transform = `translate(${this.pan.x}px, ${this.pan.y}px)`;
  }

  _toModel({ x, y }) {
    return new DOMPoint(x, y).matrixTransform(this.node.getScreenCTM().inverse());
  }

  _toClient(point) {
    return new DOMPoint(point.x, point.y).matrixTransform(this.node.getScreenCTM());
  }

  resetView() {
    this.zoom = 1;
    this.pan = { x: 0, y: 0 };
    this.node.style.transform = "";
    if (this.spec) this.render(this.spec);
  }
}

function hatchPattern({ id, texture, fill, ink }, pitch) {
  const tile = Math.max(pitch / 9, 0.05);
  const stroke = tile * 0.3;
  const pattern = s("pattern", {
    id,
    width: tile,
    height: tile,
    patternUnits: "userSpaceOnUse",
    patternTransform: texture === "backhatch" ? "rotate(135)" : texture === "hatch" ? "rotate(45)" : null,
  });
  pattern.appendChild(s("rect", { width: tile, height: tile, fill }));
  if (texture === "dots") {
    pattern.appendChild(s("circle", { cx: tile / 2, cy: tile / 2, r: tile * 0.19, fill: ink }));
  } else {
    pattern.appendChild(s("line", { x1: 0, y1: 0, x2: 0, y2: tile, stroke: ink, "stroke-width": stroke }));
  }
  return pattern;
}

/** A round number near a twentieth of the span, for ruler ticks. */
function niceStep(span) {
  const target = span / 9;
  const magnitude = 10 ** Math.floor(Math.log10(target));
  for (const multiple of [1, 2, 2.5, 5, 10]) {
    if (magnitude * multiple >= target) return magnitude * multiple;
  }
  return magnitude * 10;
}

function round(value) {
  return Math.abs(value) < 1e-9 ? "0" : Number(value.toFixed(2)).toString();
}
