// Drawing a core's results on a canvas.
//
// SVG is what the editor uses, and it is the wrong tool here: a full core redrawn on every tick of a
// time slider is thousands of cells and labels, and zoomed in it is tens of thousands of pins.
// A canvas redraws all of that in a few milliseconds and keeps no per-cell state to update.
//
// The renderer knows nothing about ARMI. Each frame the view hands it a scene: cells to draw, a
// colour for each, and optionally what is inside the blocks. Everything is drawn in screen
// coordinates with y up, so labels are never mirrored.

import { formatValue } from "./colormaps.js";

const OUTLINE_PINS_AT = 120; // pixels across a block before its pins are outlined as well as filled
const MISSING = "#c9ced5";
const OUTLINE = "rgba(20, 24, 30, 0.38)";
const SHEET = "#f5f6f7";
const FONT = '"IBM Plex Mono", ui-monospace, Menlo, Consolas, monospace';

export class CoreCanvas {
  /**
   * @param {object} options
   * @param {function} options.onHover called with a hit (see {@link CoreCanvas#hitTest}) or null
   * @param {function} options.onSelect called with a hit when a cell is clicked
   * @param {function} options.onView called when the zoom or pan changes
   */
  constructor(options = {}) {
    this.options = options;
    this.canvas = document.createElement("canvas");
    this.canvas.className = "results-canvas";
    this.node = document.createElement("div");
    this.node.className = "results-canvas-wrap";
    this.node.appendChild(this.canvas);
    this.context = this.canvas.getContext("2d");
    this.views = {}; // per plane: {ppc, cx, cy}
    this.scene = null;
    this._frame = 0;
    this._bind();
    this._resize = new ResizeObserver(() => this.request());
    this._resize.observe(this.node);
  }

  destroy() {
    this._resize.disconnect();
  }

  /** Swap in a new scene and draw it on the next animation frame. */
  setScene(scene) {
    this.scene = scene;
    this.request();
  }

  request() {
    if (this._frame) return;
    this._frame = requestAnimationFrame(() => {
      this._frame = 0;
      this.draw();
    });
  }

  view() {
    const plane = this.scene?.plane || "xy";
    if (!this.views[plane]) this.views[plane] = this._fitView();
    return this.views[plane];
  }

  /** Zoom to show the whole scene. */
  fit() {
    if (!this.scene) return;
    this.views[this.scene.plane] = this._fitView();
    this.request();
    this._rehover();
    this.options.onView?.();
  }

  _fitView() {
    const bounds = this.scene?.bounds || { minX: -1, maxX: 1, minY: -1, maxY: 1 };
    const { width, height } = this._size();
    const spanX = Math.max(bounds.maxX - bounds.minX, 1e-6);
    const spanY = Math.max(bounds.maxY - bounds.minY, 1e-6);
    const ppc = 0.92 * Math.min(width / spanX, height / spanY);
    return { ppc, cx: (bounds.minX + bounds.maxX) / 2, cy: (bounds.minY + bounds.maxY) / 2 };
  }

  _size() {
    const rect = this.node.getBoundingClientRect();
    return { width: Math.max(rect.width, 50), height: Math.max(rect.height, 50) };
  }

  toScreen(x, y) {
    const view = this.view();
    const { width, height } = this._size();
    return [width / 2 + (x - view.cx) * view.ppc, height / 2 - (y - view.cy) * view.ppc];
  }

  toModel(sx, sy) {
    const view = this.view();
    const { width, height } = this._size();
    return [view.cx + (sx - width / 2) / view.ppc, view.cy - (sy - height / 2) / view.ppc];
  }

  // -- input ------------------------------------------------------------------------------------

  _bind() {
    const canvas = this.canvas;
    let drag = null;

    canvas.addEventListener("wheel", (event) => {
      if (!this.scene) return;
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const sx = event.clientX - rect.left;
      const sy = event.clientY - rect.top;
      const [mx, my] = this.toModel(sx, sy);
      const view = this.view();
      const factor = Math.exp(-event.deltaY * (event.deltaMode === 1 ? 0.05 : 0.0018));
      view.ppc = Math.min(Math.max(view.ppc * factor, 0.02), 4000);
      // Keep the point under the cursor where it was.
      const [nx, ny] = this.toModel(sx, sy);
      view.cx += mx - nx;
      view.cy += my - ny;
      this.request();
      this._rehover();
      this.options.onView?.();
    }, { passive: false });

    canvas.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      drag = { x: event.clientX, y: event.clientY, moved: false };
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener("pointermove", (event) => {
      const rect = canvas.getBoundingClientRect();
      if (drag) {
        const dx = event.clientX - drag.x;
        const dy = event.clientY - drag.y;
        if (drag.moved || Math.abs(dx) + Math.abs(dy) > 3) {
          drag.moved = true;
          const view = this.view();
          view.cx -= dx / view.ppc;
          view.cy += dy / view.ppc;
          drag.x = event.clientX;
          drag.y = event.clientY;
          canvas.style.cursor = "grabbing";
          this.request();
          this.options.onView?.();
          return;
        }
      }
      this._pointer = { sx: event.clientX - rect.left, sy: event.clientY - rect.top };
      this._rehover();
      canvas.style.cursor = this.hover ? "pointer" : "default";
      this.request();
    });
    canvas.addEventListener("pointerup", (event) => {
      if (drag && !drag.moved) {
        const rect = canvas.getBoundingClientRect();
        const hit = this.hitTest(event.clientX - rect.left, event.clientY - rect.top);
        this.options.onSelect?.(hit);
      }
      drag = null;
      canvas.style.cursor = "default";
    });
    canvas.addEventListener("pointerleave", () => {
      this._pointer = null;
      this.hover = null;
      this.options.onHover?.(null);
      this.request();
    });
    canvas.addEventListener("dblclick", () => this.fit());
  }

  /** Work out again what is under the pointer, after the picture moved beneath it. */
  _rehover() {
    const pointer = this._pointer;
    const hit = pointer ? this.hitTest(pointer.sx, pointer.sy) : null;
    this.hover = hit ? { ...hit, ...pointer } : null;
    this.options.onHover?.(this.hover);
  }

  /** Call when the scene under a still pointer has changed, such as on a new time node. */
  refreshHover() {
    if (this._pointer) this._rehover();
  }

  /**
   * What is under a screen point.
   *
   * @returns {object|null} the cell (its `a`, `b` and value), plus `component` (index within the
   *   block) when block insides are being drawn and the point is on one
   */
  hitTest(sx, sy) {
    const scene = this.scene;
    if (!scene) return null;
    const [x, y] = this.toModel(sx, sy);
    if (scene.plane === "xy") {
      const shape = scene.cellShape;
      let best = null;
      for (const cell of scene.cells) {
        const dx = x - cell.x;
        const dy = y - cell.y;
        const d2 = dx * dx + dy * dy;
        if (d2 > shape.radius * shape.radius) continue;
        if (!insideConvex(shape.corners, dx, dy)) continue;
        if (!best || d2 < best.d2) best = { cell, d2, dx, dy };
      }
      if (!best) return null;
      const hit = { ...best.cell };
      if (scene.detail && this._detailOn()) {
        const found = componentAt(scene.detail, best.cell, best.dx, best.dy, scene.geometry);
        if (found !== null) {
          hit.component = found.index;
          if (found.pin != null) hit.pin = found.pin;
        }
      }
      return hit;
    }
    for (const cell of scene.cells) {
      if (x >= cell.u1 && x <= cell.u2 && y >= cell.z1 && y < cell.z2) return { ...cell };
    }
    return null;
  }

  _detailOn() {
    const scene = this.scene;
    if (!scene?.detail || scene.plane !== "xy") return false;
    return this.view().ppc * scene.geometry.pitchSize >= scene.detailAt;
  }

  /** Whether block insides are being drawn right now. */
  get showingDetail() {
    return this._detailOn();
  }

  // -- drawing ----------------------------------------------------------------------------------

  draw() {
    const { width, height } = this._size();
    const ratio = window.devicePixelRatio || 1;
    const canvas = this.canvas;
    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
    }
    const context = this.context;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.fillStyle = SHEET;
    context.fillRect(0, 0, width, height);
    const scene = this.scene;
    if (!scene) return;

    const started = performance.now();
    if (scene.plane === "xy") this._drawPlan(context, width, height);
    else this._drawSection(context, width, height);
    if (scene.inset) this._drawInset(context, width, height);
    this.lastDrawMs = performance.now() - started;
    this.options.onDrawn?.(this.lastDrawMs);
  }

  _drawPlan(context, width, height) {
    const scene = this.scene;
    const view = this.view();
    const ppc = view.ppc;
    const shape = scene.cellShape;
    const corners = shape.corners.map(([cx, cy]) => [cx * ppc, -cy * ppc]);
    const reach = shape.radius * ppc;
    const detail = this._detailOn();
    const visible = [];

    context.lineJoin = "round";
    context.lineWidth = Math.min(1, Math.max(0.3, ppc * 0.06));
    context.strokeStyle = OUTLINE;
    for (const cell of scene.cells) {
      const [sx, sy] = this.toScreen(cell.x, cell.y);
      if (sx < -reach || sy < -reach || sx > width + reach || sy > height + reach) continue;
      visible.push([cell, sx, sy]);
      context.beginPath();
      polygon(context, corners, sx, sy);
      context.fillStyle = cell.k >= 0 ? scene.table.css[cell.k] : MISSING;
      context.fill();
      context.stroke();
    }

    if (detail) {
      for (const [cell, sx, sy] of visible) this._drawInside(context, cell, sx, sy, ppc, corners);
    }

    // Labels after every fill, so a neighbour never paints over one.
    if (scene.showValues) {
      const px = scene.textPx;
      context.font = `500 ${px}px ${FONT}`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      const room = 2 * shape.inner * ppc * 0.92;
      for (const [cell, sx, sy] of visible) {
        if (detail && scene.detail.componentValues) continue;
        const text = cell.label ?? formatValue(cell.value, scene.digits);
        if (px > room * 0.5) continue;
        const measured = context.measureText(text).width;
        if (measured > room) continue;
        if (detail) {
          // Over a drawn interior the block's own colour is not what is behind the text.
          context.fillStyle = "rgba(255,255,255,0.82)";
          context.fillRect(sx - measured / 2 - 3, sy - px * 0.65, measured + 6, px * 1.3);
          context.fillStyle = "#111418";
        } else {
          context.fillStyle = cell.k >= 0 ? scene.table.ink[cell.k] : "#3a414b";
        }
        context.fillText(text, sx, sy);
      }
    }

    this._outlineMarked(context, corners, ppc);
  }

  _outlineMarked(context, corners, ppc) {
    const scene = this.scene;
    const mark = (match, width, color) => {
      for (const cell of scene.cells) {
        if (!match(cell)) continue;
        const [sx, sy] = this.toScreen(cell.x, cell.y);
        context.beginPath();
        polygon(context, corners, sx, sy);
        context.lineWidth = width;
        context.strokeStyle = color;
        context.stroke();
      }
    };
    const selected = scene.selected;
    if (selected) {
      mark((cell) => cell.a === selected.a, 3.2, "#ffffff");
      mark((cell) => cell.a === selected.a, 1.8, "#111418");
    }
    const hover = this.hover;
    if (hover && (!selected || hover.a !== selected.a)) {
      mark((cell) => cell.a === hover.a, 1.6, "rgba(17,20,24,0.75)");
    }
    void ppc;
  }

  /** A block's components, coloured by their own values when there are any. */
  _drawInside(context, cell, sx, sy, ppc, corners) {
    const scene = this.scene;
    const detail = scene.detail;
    if (cell.b < 0) return;
    const design = detail.designs[detail.blockDesign[cell.b]];
    if (!design) return;
    const values = detail.componentValues;
    const start = detail.compStart[cell.b];
    const angle = scene.geometry.cornerAngle;

    context.save();
    context.beginPath();
    polygon(context, corners, sx, sy);
    context.clip();
    const thin = Math.min(0.8, Math.max(0.25, ppc * 0.02));
    context.lineWidth = thin;

    const instances = detail.instances;
    for (const shape of design.drawOrder) {
      if (values && instances && this._drawInstances(context, shape, instances, start, sx, sy, ppc)) continue;
      const path = new Path2D();
      if (shape.type === "DerivedShape") {
        polygonPath(path, corners, sx, sy, 1.02);
      } else if (shape.type === "Hexagon") {
        hexPath(path, sx, sy, (shape.op / Math.sqrt(3)) * ppc, angle);
        if (shape.ip > 0) hexPath(path, sx, sy, (shape.ip / Math.sqrt(3)) * ppc, angle);
      } else if (shape.type === "Square" || shape.type === "Rectangle") {
        const w = (shape.widthOuter || 0) * ppc;
        const l = (shape.lengthOuter || shape.widthOuter || 0) * ppc;
        path.rect(sx - l / 2, sy - w / 2, l, w);
        const wi = (shape.widthInner || 0) * ppc;
        const li = (shape.lengthInner || shape.widthInner || 0) * ppc;
        if (wi > 0 && li > 0) path.rect(sx - li / 2, sy - wi / 2, li, wi);
      } else if (shape.type === "Circle") {
        const outer = (shape.od / 2) * ppc;
        const inner = ((shape.id || 0) / 2) * ppc;
        const at = shape.at;
        const count = at ? at.length / 2 : 1;
        for (let p = 0; p < count; p += 1) {
          const px = sx + (at ? at[2 * p] * ppc : 0);
          const py = sy - (at ? at[2 * p + 1] * ppc : 0);
          path.moveTo(px + outer, py);
          path.arc(px, py, outer, 0, 2 * Math.PI);
          if (inner > 0.3) {
            path.moveTo(px + inner, py);
            path.arc(px, py, inner, 0, 2 * Math.PI);
          }
        }
      }
      if (values) {
        const value = values[start + shape.index];
        const k = scene.componentIndex(value);
        context.fillStyle = k >= 0 ? scene.table.css[k] : MISSING;
        context.fill(path, "evenodd");
      }
      // Zoomed far out, outlines would be all there is to see; the colours say it alone.
      if (shape.type !== "DerivedShape" && (!values || ppc * scene.geometry.pitchSize >= OUTLINE_PINS_AT)) {
        context.strokeStyle = values ? "rgba(20,24,30,0.35)" : "rgba(20,24,30,0.55)";
        context.stroke(path);
      }
    }
    context.restore();

    if (values && scene.showValues) {
      // The legend lists each component's one value, which says nothing about pins with values of
      // their own and covers them; those wait until the pins are big enough to carry their numbers.
      if (!this._labelPins(context, design, values, start, sx, sy, ppc) && !detail.instances && scene.blockLegend) {
        this._legendInside(context, design, values, start, sx, sy, ppc);
      }
    }
  }

  /**
   * Each instance of a component (each pin of a pin array) in its own colour, for a parameter with
   * a value per instance.
   *
   * @returns {boolean} whether the shape was drawn here
   */
  _drawInstances(context, shape, pins, start, sx, sy, ppc) {
    const at = shape.at;
    if (shape.type !== "Circle" || !at) return false;
    const component = start + shape.index;
    const from = pins.offsets[component];
    const count = pins.offsets[component + 1] - from;
    if (!count || count !== at.length / 2) return false;
    const scene = this.scene;
    const outer = (shape.od / 2) * ppc;
    const inner = ((shape.id || 0) / 2) * ppc;
    const outline = ppc * scene.geometry.pitchSize >= OUTLINE_PINS_AT;
    for (let p = 0; p < count; p += 1) {
      const px = sx + at[2 * p] * ppc;
      const py = sy - at[2 * p + 1] * ppc;
      const path = new Path2D();
      path.moveTo(px + outer, py);
      path.arc(px, py, outer, 0, 2 * Math.PI);
      if (inner > 0.3) {
        path.moveTo(px + inner, py);
        path.arc(px, py, inner, 0, 2 * Math.PI);
      }
      const k = scene.componentIndex(pins.values[from + p]);
      context.fillStyle = k >= 0 ? scene.table.css[k] : MISSING;
      context.fill(path, "evenodd");
      if (outline) {
        context.strokeStyle = "rgba(20,24,30,0.35)";
        context.stroke(path);
      }
    }
    return true;
  }

  /**
   * Values on the pins themselves, once they are big enough to carry them: a solid pin's in its
   * middle, and each ring's (clad, bond, liner) at the top of its band.
   *
   * @returns {boolean} whether the pins were big enough to label
   */
  _labelPins(context, design, values, start, sx, sy, ppc) {
    const scene = this.scene;
    const px = scene.textPx;
    const circles = design.drawOrder.filter((shape) => shape.type === "Circle");
    if (!circles.length) return false;
    const biggest = Math.max(...circles.map((shape) => shape.od)) * ppc / 2;
    if (biggest < px * 3.2) return false;
    const { width, height } = this._size();
    context.font = `500 ${px}px ${FONT}`;
    context.textAlign = "center";
    context.textBaseline = "middle";
    for (const shape of circles) {
      const outer = (shape.od / 2) * ppc;
      const inner = ((shape.id || 0) / 2) * ppc;
      const band = outer - inner;
      const solid = inner < 0.5;
      if (!solid && band < px * 1.15) continue;
      const at = shape.at;
      const count = at ? at.length / 2 : 1;
      // A parameter with a value per pin labels each pin with its own; otherwise every pin of the
      // component carries the component's value.
      const pins = scene.detail.instances;
      const component = start + shape.index;
      const from = pins ? pins.offsets[component] : 0;
      const perPin = pins && at && pins.offsets[component + 1] - from === count;
      const room = solid ? outer * 1.8 : Math.sqrt(Math.max(outer * outer - inner * inner, 0)) * 1.8;
      for (let p = 0; p < count; p += 1) {
        const x = sx + (at ? at[2 * p] * ppc : 0);
        const y = sy - (at ? at[2 * p + 1] * ppc : 0);
        if (x < -outer || y < -outer || x > width + outer || y > height + outer) continue;
        const value = perPin ? pins.values[from + p] : values[component];
        const text = formatValue(value, scene.digits);
        if (context.measureText(text).width > room) continue;
        const k = scene.componentIndex(value);
        context.fillStyle = k >= 0 ? scene.table.ink[k] : "#3a414b";
        context.fillText(text, x, solid ? y : y - (outer + inner) / 2);
      }
    }
    return true;
  }

  /** Each component's value, listed in a corner of its block. */
  _legendInside(context, design, values, start, sx, sy, ppc) {
    const scene = this.scene;
    const px = scene.textPx;
    const lines = design.shapes.map((shape, index) => [shape.name, formatValue(values[start + index], scene.digits)]);
    const room = scene.cellShape.inner * ppc;
    if (px * 1.25 * lines.length > room * 1.4) return;
    context.font = `500 ${px}px ${FONT}`;
    context.textBaseline = "middle";
    const nameWidth = Math.max(...lines.map(([name]) => context.measureText(name).width));
    const valueWidth = Math.max(...lines.map(([, value]) => context.measureText(value).width));
    const boxW = nameWidth + valueWidth + px * 1.6;
    const boxH = px * 1.3 * lines.length + px * 0.5;
    const x0 = sx - boxW / 2;
    const y0 = sy - boxH / 2;
    context.fillStyle = "rgba(255,255,255,0.88)";
    context.fillRect(x0, y0, boxW, boxH);
    context.strokeStyle = "rgba(20,24,30,0.4)";
    context.lineWidth = 0.8;
    context.strokeRect(x0, y0, boxW, boxH);
    lines.forEach(([name, value], row) => {
      const y = y0 + px * 0.25 + px * 1.3 * (row + 0.5);
      context.fillStyle = "#4b5563";
      context.textAlign = "left";
      context.fillText(name, x0 + px * 0.5, y);
      context.fillStyle = "#111418";
      context.textAlign = "right";
      context.fillText(value, x0 + boxW - px * 0.5, y);
    });
  }

  _drawSection(context, width, height) {
    const scene = this.scene;
    const view = this.view();
    const ppc = view.ppc;
    context.lineWidth = Math.min(1, Math.max(0.3, ppc * 0.05));
    context.strokeStyle = OUTLINE;
    const rects = [];
    for (const cell of scene.cells) {
      const [x1, y1] = this.toScreen(cell.u1, cell.z2);
      const [x2, y2] = this.toScreen(cell.u2, cell.z1);
      if (x2 < 0 || y2 < 0 || x1 > width || y1 > height) continue;
      rects.push([cell, x1, y1, x2 - x1, y2 - y1]);
      context.fillStyle = cell.k >= 0 ? scene.table.css[cell.k] : MISSING;
      context.fillRect(x1, y1, x2 - x1, y2 - y1);
      context.strokeRect(x1, y1, x2 - x1, y2 - y1);
    }

    if (scene.showValues) {
      const px = scene.textPx;
      context.font = `500 ${px}px ${FONT}`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      for (const [cell, x, y, w, h] of rects) {
        if (h < px * 1.1) continue;
        const text = formatValue(cell.value, scene.digits);
        if (context.measureText(text).width > w * 0.94) continue;
        context.fillStyle = cell.k >= 0 ? scene.table.ink[cell.k] : "#3a414b";
        context.fillText(text, x + w / 2, y + h / 2);
      }
    }

    const mark = (match, lineWidth, color) => {
      context.lineWidth = lineWidth;
      context.strokeStyle = color;
      for (const [cell, x, y, w, h] of rects) if (match(cell)) context.strokeRect(x, y, w, h);
    };
    const selected = scene.selected;
    if (selected) {
      mark((cell) => cell.a === selected.a && cell.b === selected.b, 3, "#ffffff");
      mark((cell) => cell.a === selected.a && cell.b === selected.b, 1.6, "#111418");
    }
    if (this.hover) mark((cell) => cell.a === this.hover.a && cell.b === this.hover.b, 1.4, "rgba(17,20,24,0.75)");

    this._axis(context, width, height);
  }

  /** Height marks up the left edge of a section view. */
  _axis(context, width, height) {
    const view = this.view();
    const [, top] = this.toModel(0, 0);
    const [, bottom] = this.toModel(0, height);
    const span = top - bottom;
    const step = niceStep(span / 8);
    context.font = `11px ${FONT}`;
    context.textAlign = "left";
    context.textBaseline = "middle";
    context.fillStyle = "#6b7280";
    context.strokeStyle = "rgba(20,24,30,0.25)";
    context.lineWidth = 1;
    for (let z = Math.ceil(bottom / step) * step; z <= top; z += step) {
      const [, sy] = this.toScreen(0, z);
      context.beginPath();
      context.moveTo(0, sy);
      context.lineTo(6, sy);
      context.stroke();
      context.fillText(`${formatValue(z, 4)}`, 9, sy);
    }
    void view;
    void width;
  }

  /** A small plan view in the corner of a section, with the cut marked. */
  _drawInset(context, width, height) {
    const inset = this.scene.inset;
    const size = Math.min(150, width * 0.22, height * 0.3);
    if (size < 60) return;
    const pad = 10;
    const x0 = width - size - pad;
    const y0 = pad;
    const span = Math.max(inset.bounds.maxX - inset.bounds.minX, inset.bounds.maxY - inset.bounds.minY) || 1;
    const scale = (size * 0.9) / span;
    const cx = (inset.bounds.minX + inset.bounds.maxX) / 2;
    const cy = (inset.bounds.minY + inset.bounds.maxY) / 2;
    const at = (x, y) => [x0 + size / 2 + (x - cx) * scale, y0 + size / 2 - (y - cy) * scale];

    context.fillStyle = "rgba(255,255,255,0.9)";
    context.fillRect(x0, y0, size, size);
    context.strokeStyle = "rgba(20,24,30,0.3)";
    context.lineWidth = 1;
    context.strokeRect(x0, y0, size, size);
    const corners = this.scene.cellShape.corners.map(([px, py]) => [px * scale, -py * scale]);
    context.fillStyle = "#d4d8de";
    context.strokeStyle = "#b5bbc4";
    context.lineWidth = 0.5;
    for (const [x, y, onCut] of inset.points) {
      const [sx, sy] = at(x, y);
      context.beginPath();
      polygon(context, corners, sx, sy);
      context.fillStyle = onCut ? "#8a94a3" : "#d4d8de";
      context.fill();
      context.stroke();
    }
    context.strokeStyle = "#eb6834";
    context.lineWidth = 1.6;
    context.beginPath();
    if (inset.axis === "y") {
      const [, sy] = at(0, inset.value);
      context.moveTo(x0 + 3, sy);
      context.lineTo(x0 + size - 3, sy);
    } else {
      const [sx] = at(inset.value, 0);
      context.moveTo(sx, y0 + 3);
      context.lineTo(sx, y0 + size - 3);
    }
    context.stroke();
  }
}

// -- geometry ---------------------------------------------------------------------------------------

function polygon(context, corners, sx, sy) {
  context.moveTo(sx + corners[0][0], sy + corners[0][1]);
  for (let k = 1; k < corners.length; k += 1) context.lineTo(sx + corners[k][0], sy + corners[k][1]);
  context.closePath();
}

function polygonPath(path, corners, sx, sy, grow = 1) {
  path.moveTo(sx + corners[0][0] * grow, sy + corners[0][1] * grow);
  for (let k = 1; k < corners.length; k += 1) path.lineTo(sx + corners[k][0] * grow, sy + corners[k][1] * grow);
  path.closePath();
}

function hexPath(path, sx, sy, radius, cornerAngle) {
  for (let k = 0; k < 6; k += 1) {
    const angle = ((cornerAngle + 60 * k) * Math.PI) / 180;
    const x = sx + radius * Math.cos(angle);
    const y = sy - radius * Math.sin(angle);
    if (k === 0) path.moveTo(x, y);
    else path.lineTo(x, y);
  }
  path.closePath();
}

/** Point in a convex polygon given as offsets from its centre, wound either way. */
export function insideConvex(corners, x, y) {
  let sign = 0;
  for (let k = 0; k < corners.length; k += 1) {
    const [ax, ay] = corners[k];
    const [bx, by] = corners[(k + 1) % corners.length];
    const cross = (bx - ax) * (y - ay) - (by - ay) * (x - ax);
    if (cross !== 0) {
      if (sign === 0) sign = Math.sign(cross);
      else if (Math.sign(cross) !== sign) return false;
    }
  }
  return true;
}

/** Which component of a block is at an offset from its centre, top-most first. */
function componentAt(detail, cell, dx, dy, geometry) {
  if (cell.b < 0) return null;
  const design = detail.designs[detail.blockDesign[cell.b]];
  if (!design) return null;
  const shapes = design.drawOrder;
  for (let k = shapes.length - 1; k >= 0; k -= 1) {
    const shape = shapes[k];
    if (shape.type === "Circle") {
      const at = shape.at;
      const count = at ? at.length / 2 : 1;
      let best = Infinity;
      let nearest = 0;
      for (let p = 0; p < count; p += 1) {
        const ex = dx - (at ? at[2 * p] : 0);
        const ey = dy - (at ? at[2 * p + 1] : 0);
        if (ex * ex + ey * ey < best) {
          best = ex * ex + ey * ey;
          nearest = p;
        }
      }
      const r = Math.sqrt(best);
      if (r <= shape.od / 2 && r >= (shape.id || 0) / 2) return { index: shape.index, pin: at ? nearest : null };
    } else if (shape.type === "Hexagon") {
      const reach = hexReach(dx, dy, geometry.cornerAngle);
      if (reach <= shape.op / 2 && reach >= (shape.ip || 0) / 2) return { index: shape.index, pin: null };
    } else if (shape.type === "Square" || shape.type === "Rectangle") {
      const l = (shape.lengthOuter || shape.widthOuter) / 2;
      const w = shape.widthOuter / 2;
      const li = (shape.lengthInner || shape.widthInner || 0) / 2;
      const wi = (shape.widthInner || 0) / 2;
      const inside = Math.abs(dx) <= l && Math.abs(dy) <= w;
      const inHole = Math.abs(dx) < li && Math.abs(dy) < wi;
      if (inside && !inHole) return { index: shape.index, pin: null };
    } else if (shape.type === "DerivedShape") {
      return { index: shape.index, pin: null };
    }
  }
  return null;
}

/** Distance to a point in the hex norm: half the flat-to-flat size of the hexagon through it. */
function hexReach(dx, dy, cornerAngle) {
  let reach = 0;
  for (let k = 0; k < 3; k += 1) {
    const angle = ((cornerAngle + 30 + 60 * k) * Math.PI) / 180;
    reach = Math.max(reach, Math.abs(dx * Math.cos(angle) + dy * Math.sin(angle)));
  }
  return reach;
}

function niceStep(raw) {
  const power = 10 ** Math.floor(Math.log10(Math.max(raw, 1e-9)));
  const scaled = raw / power;
  return (scaled < 1.5 ? 1 : scaled < 3.5 ? 2 : scaled < 7.5 ? 5 : 10) * power;
}

/**
 * Where a straight cut through a convex cell crosses it.
 *
 * @param {number[][]} corners the cell outline, as offsets from its centre
 * @param {"x"|"y"} axis which coordinate the cut holds fixed
 * @param {number} offset the cut's distance from the cell centre along that axis
 * @returns {number[]|null} the span along the other axis, relative to the centre
 */
export function chord(corners, axis, offset) {
  const fixed = axis === "y" ? 1 : 0;
  const free = 1 - fixed;
  let lo = Infinity;
  let hi = -Infinity;
  for (let k = 0; k < corners.length; k += 1) {
    const a = corners[k];
    const b = corners[(k + 1) % corners.length];
    const da = a[fixed] - offset;
    const db = b[fixed] - offset;
    if ((da <= 0 && db >= 0) || (da >= 0 && db <= 0)) {
      if (da === db) {
        lo = Math.min(lo, a[free], b[free]);
        hi = Math.max(hi, a[free], b[free]);
      } else {
        const t = da / (da - db);
        const u = a[free] + t * (b[free] - a[free]);
        lo = Math.min(lo, u);
        hi = Math.max(hi, u);
      }
    }
  }
  return hi > lo ? [lo, hi] : null;
}
