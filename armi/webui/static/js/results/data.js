// What the results viewer knows about the open database, and how it gets more.
//
// The server sends a parameter's whole history as one array of float32: every node back to back,
// and within a node, blocks in the topology's canonical order (assemblies in layout order, blocks
// bottom to top). This module works out where each node starts in that array and where each block
// sits, so the view never has to think about offsets.
//
// Three kinds of data arrive at three speeds:
//   series     one parameter at every node, fetched once when it is chosen (a few MB)
//   positions  where the assemblies sat at each node, fetched for the node on screen first and
//              then for the rest in the background
//   components one component parameter at one node, fetched as you scrub, cached, and prefetched
//              around the current node

import { api } from "../api.js";

const COMPONENT_CACHE = 48;
const POSITION_BATCH = 30;
// Cache a component parameter at every node when the whole history fits in this much memory.
// Sixty years of a 313-assembly core is about 31 MB.
const COMPONENT_BUDGET_BYTES = 400e6;
const COMPONENT_BATCH_BYTES = 4e6;

export class Results {
  /** @param {object} summary from `GET /api/results` */
  constructor(summary) {
    this.summary = summary;
    this.path = summary.path;
    this.caseTitle = summary.caseTitle;
    this.steps = summary.steps;
    this.geometry = summary.geometry;
    this.parameters = summary.parameters;
    this.core = summary.core || []; // scalar core and reactor parameters, each with a value per node

    this.topologies = {};
    for (const [id, topo] of Object.entries(summary.topologies)) {
      const blockStart = new Int32Array(topo.blocksPerAssembly.length + 1);
      topo.blocksPerAssembly.forEach((count, a) => {
        blockStart[a + 1] = blockStart[a] + count;
      });
      const blockAssembly = new Int32Array(topo.blockCount);
      for (let a = 0; a < topo.blocksPerAssembly.length; a += 1) {
        blockAssembly.fill(a, blockStart[a], blockStart[a + 1]);
      }
      const assemblyIndex = new Map(topo.assemblyNames.map((name, a) => [name, a]));
      this.topologies[id] = { ...topo, blockStart, blockAssembly, assemblyIndex, assemblyCount: topo.assemblyNames.length };
    }

    // Where each node's values start in a block-level and an assembly-level series.
    const n = this.steps.length;
    this.blockOffset = new Int32Array(n + 1);
    this.assemblyOffset = new Int32Array(n + 1);
    this.steps.forEach((step, t) => {
      const topo = this.topologies[step.topology];
      this.blockOffset[t + 1] = this.blockOffset[t] + topo.blockCount;
      this.assemblyOffset[t + 1] = this.assemblyOffset[t] + topo.assemblyCount;
    });

    this.frames = new Map();
    this.stepFrame = new Array(n).fill(null);
    this._positionWaits = new Map();
    this._series = new Map();
    this._components = new Map();
    this._componentWaits = new Map();
    this._designs = new Map();
    this._ranges = new Map();
    this.listeners = new Set();

    const cell = cellShape(this.geometry);
    this.cell = cell;
  }

  static async load() {
    const summary = await api.results();
    const results = new Results(summary);
    await results.loadAxialMesh();
    return results;
  }

  onChange(listener) {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  _changed(what) {
    for (const listener of this.listeners) listener(what);
  }

  topologyAt(t) {
    return this.topologies[this.steps[t].topology];
  }

  // -- axial mesh -------------------------------------------------------------------------------

  /** Block bottoms and tops at every node, which every view needs to place a block. */
  async loadAxialMesh() {
    const names = new Set(this.parameters.block.map((entry) => entry.name));
    if (names.has("zbottom") && names.has("ztop")) {
      [this.zbottom, this.ztop] = await Promise.all([
        this.series("block", "zbottom"),
        this.series("block", "ztop"),
      ]);
    } else if (names.has("height")) {
      // No bounds stored, so stack the heights up from zero within each assembly.
      const height = await this.series("block", "height");
      this.zbottom = new Float32Array(height.length);
      this.ztop = new Float32Array(height.length);
      this.steps.forEach((step, t) => {
        const topo = this.topologyAt(t);
        const base = this.blockOffset[t];
        for (let a = 0; a < topo.assemblyCount; a += 1) {
          let z = 0;
          for (let b = topo.blockStart[a]; b < topo.blockStart[a + 1]; b += 1) {
            this.zbottom[base + b] = z;
            z += height[base + b] || 0;
            this.ztop[base + b] = z;
          }
        }
      });
    } else {
      throw new Error("This database has no block heights, so there is no way to place blocks axially.");
    }

    let lo = Infinity;
    let hi = -Infinity;
    const bounds = new Set();
    const t0 = 0;
    for (let k = this.blockOffset[t0]; k < this.blockOffset[t0 + 1]; k += 1) {
      const zb = this.zbottom[k];
      const zt = this.ztop[k];
      if (!Number.isFinite(zb) || !Number.isFinite(zt)) continue;
      lo = Math.min(lo, zb);
      hi = Math.max(hi, zt);
      bounds.add(Math.round(zb * 1000) / 1000);
      bounds.add(Math.round(zt * 1000) / 1000);
    }
    const sorted = [...bounds].sort((a, b) => a - b);
    // One stop in the middle of every slab between distinct block boundaries, so every block in
    // the core is reachable and nothing sits exactly on a boundary.
    this.zStops = [];
    for (let k = 0; k + 1 < sorted.length; k += 1) {
      if (sorted[k + 1] - sorted[k] > 1e-6) this.zStops.push((sorted[k] + sorted[k + 1]) / 2);
    }
    this.zRange = [lo, hi];
  }

  // -- positions --------------------------------------------------------------------------------

  /** The frame (assembly positions) at a node, if it has arrived. */
  frameAt(t) {
    const id = this.stepFrame[t];
    return id ? this.frames.get(id) : null;
  }

  async ensurePositions(t) {
    if (this.stepFrame[t]) return this.frameAt(t);
    await this._fetchPositions([t]);
    return this.frameAt(t);
  }

  async _fetchPositions(indices) {
    const wanted = indices.filter((t) => !this.stepFrame[t]);
    if (!wanted.length) return;
    const keys = wanted.map((t) => this.steps[t].key);
    const pendingKey = keys.join(",");
    if (!this._positionWaits.has(pendingKey)) {
      this._positionWaits.set(
        pendingKey,
        api.resultsPositions(keys).then((payload) => {
          for (const [id, frame] of Object.entries(payload.frames)) {
            if (!this.frames.has(id)) this.frames.set(id, this._frame(id, frame));
          }
          this.steps.forEach((step, t) => {
            if (payload.steps[step.key]) this.stepFrame[t] = payload.steps[step.key];
          });
          this._positionWaits.delete(pendingKey);
          this._changed("positions");
        }),
      );
    }
    await this._positionWaits.get(pendingKey);
  }

  /** Fetch everyone's positions, a batch at a time, starting near the node on screen. */
  async prefetchPositions(around = 0) {
    const order = [...this.steps.keys()].sort((a, b) => Math.abs(a - around) - Math.abs(b - around));
    for (let k = 0; k < order.length; k += POSITION_BATCH) {
      if (this.closed) return;
      const batch = order.slice(k, k + POSITION_BATCH).filter((t) => !this.stepFrame[t]);
      if (batch.length) await this._fetchPositions(batch);
    }
  }

  get positionsLoaded() {
    return this.stepFrame.filter(Boolean).length;
  }

  _frame(id, frame) {
    const { iStep, jStep } = this.geometry;
    const count = frame.i.length;
    const x = new Float64Array(count);
    const y = new Float64Array(count);
    for (let a = 0; a < count; a += 1) {
      x[a] = frame.i[a] * iStep[0] + frame.j[a] * jStep[0];
      y[a] = frame.i[a] * iStep[1] + frame.j[a] * jStep[1];
    }
    // Copies for an unfolded view of a partial core: [assembly, x, y] for every symmetric partner.
    const copies = [];
    if (frame.symmetric) {
      frame.symmetric.forEach((partners, a) => {
        for (const [i, j] of partners) {
          copies.push([a, i * iStep[0] + j * jStep[0], i * iStep[1] + j * jStep[1], i, j]);
        }
      });
    }
    const where = new Map();
    for (let a = 0; a < count; a += 1) where.set(`${frame.i[a]},${frame.j[a]}`, a);
    return { id, i: frame.i, j: frame.j, x, y, copies, where, symmetric: !!frame.symmetric };
  }

  // -- values -----------------------------------------------------------------------------------

  async series(level, name, index = null, reduce = null) {
    const key = `${level}|${name}|${index ?? ""}|${reduce ?? ""}`;
    if (!this._series.has(key)) {
      this._series.set(key, api.resultsSeries(level, name, index, reduce).catch((error) => {
        this._series.delete(key);
        throw error;
      }));
    }
    return this._series.get(key);
  }

  /**
   * A block series boiled down to one value per assembly at every node, in assembly order: its
   * blocks' largest (`max`), smallest (`min`), total (`sum`) or height-weighted average (`mean`).
   */
  assemblyFromBlocks(blockSeries, mode) {
    const out = new Float32Array(this.assemblyOffset[this.steps.length]).fill(NaN);
    this.steps.forEach((step, t) => {
      const topo = this.topologies[step.topology];
      const blocks = this.blockOffset[t];
      for (let a = 0; a < topo.assemblyCount; a += 1) {
        let acc = mode === "min" ? Infinity : mode === "max" ? -Infinity : 0;
        let weight = 0;
        for (let b = topo.blockStart[a]; b < topo.blockStart[a + 1]; b += 1) {
          const v = blockSeries[blocks + b];
          if (v !== v) continue;
          if (mode === "max") acc = Math.max(acc, v);
          else if (mode === "min") acc = Math.min(acc, v);
          else if (mode === "mean") {
            const height = this.ztop[blocks + b] - this.zbottom[blocks + b];
            acc += v * height;
            weight += height;
            continue;
          } else acc += v;
          weight += 1;
        }
        if (weight) out[this.assemblyOffset[t] + a] = mode === "mean" ? acc / weight : acc;
      }
    });
    return out;
  }

  /** Smallest and largest finite values, and the smallest positive one for a log scale. */
  rangeOf(values, from = 0, to = values.length) {
    let lo = Infinity;
    let hi = -Infinity;
    let positive = Infinity;
    for (let k = from; k < to; k += 1) {
      const v = values[k];
      if (v !== v) continue; // NaN
      if (v < lo) lo = v;
      if (v > hi) hi = v;
      if (v > 0 && v < positive) positive = v;
    }
    return {
      min: Number.isFinite(lo) ? lo : null,
      max: Number.isFinite(hi) ? hi : null,
      minPositive: Number.isFinite(positive) ? positive : null,
    };
  }

  /** Range over the whole history, cached per series. */
  historyRange(key, values) {
    if (!this._ranges.has(key)) this._ranges.set(key, this.rangeOf(values));
    return this._ranges.get(key);
  }

  /** Every instance's value of a per-instance component parameter at node `t`, fetched once. */
  async instances(t, param) {
    const key = `${this.steps[t].key}|${param}`;
    this._instances ??= new Map();
    if (!this._instances.has(key)) {
      this._instances.set(key, api.resultsInstances(this.steps[t].key, param).then((values) => {
        this._instances.set(key, values);
        if (this._instances.size > 64) this._instances.delete(this._instances.keys().next().value);
        this._changed("instances");
        return values;
      }).catch((error) => {
        this._instances.delete(key);
        throw error;
      }));
    }
    return this._instances.get(key);
  }

  /** What `instances()` fetched, if it is already here, or null. */
  instancesAt(t, param) {
    const values = this._instances?.get(`${this.steps[t].key}|${param}`);
    return values && !(values instanceof Promise) ? values : null;
  }

  componentKey(t, param, nuclide) {
    return `${this.steps[t].key}|${param}|${nuclide || ""}`;
  }

  /** Component values at a node, if they have arrived. */
  componentsAt(t, param, nuclide) {
    const entry = this._components.get(this.componentKey(t, param, nuclide));
    return entry || null;
  }

  async components(t, param, nuclide) {
    const key = this.componentKey(t, param, nuclide);
    if (this._components.has(key)) {
      const value = this._components.get(key);
      this._components.delete(key);
      this._components.set(key, value); // most recent last
      return value;
    }
    if (!this._componentWaits.has(key)) {
      this._componentWaits.set(
        key,
        api.resultsComponents(this.steps[t].key, param, nuclide).then((values) => {
          this._components.set(key, values);
          while (this._components.size > this._componentCap()) {
            this._components.delete(this._components.keys().next().value);
          }
          this._componentWaits.delete(key);
          this._changed("components");
          return values;
        }, (error) => {
          this._componentWaits.delete(key);
          throw error;
        }),
      );
    }
    return this._componentWaits.get(key);
  }

  _componentCap() {
    return this._cacheAll ? Infinity : COMPONENT_CACHE;
  }

  /**
   * Fetch a component parameter at every node in the background, nearest the current node first,
   * so scrubbing never waits on the server. Several nodes travel per request. Gives up quietly if
   * the whole history would not fit the memory budget, leaving the per-node prefetch to cope.
   *
   * @returns {Promise<boolean>} whether everything was cached (false if superseded or too big)
   */
  async prefetchComponents(param, nuclide, around, onProgress) {
    const token = (this._componentToken = {});
    const sizes = this.steps.map((step) => this.topologies[step.topology].componentCount * 4);
    const total = sizes.reduce((a, b) => a + b, 0);
    if (total > COMPONENT_BUDGET_BYTES) return false;
    this._cacheAll = true;
    const order = [...this.steps.keys()].sort((a, b) => Math.abs(a - around) - Math.abs(b - around));
    const missing = () => order.filter((t) => !this._components.has(this.componentKey(t, param, nuclide)));
    let todo = missing();
    while (todo.length) {
      if (this.closed || this._componentToken !== token) return false;
      const batch = [];
      let bytes = 0;
      for (const t of todo) {
        if (batch.length && bytes + sizes[t] > COMPONENT_BATCH_BYTES) break;
        batch.push(t);
        bytes += sizes[t];
      }
      batch.sort((a, b) => a - b);
      const values = await api.resultsComponentsMany(batch.map((t) => this.steps[t].key), param, nuclide);
      if (this._componentToken !== token) return false;
      let offset = 0;
      for (const t of batch) {
        const count = sizes[t] / 4;
        this._components.set(this.componentKey(t, param, nuclide), values.subarray(offset, offset + count));
        offset += count;
      }
      onProgress?.(this.steps.length - missing().length, this.steps.length);
      this._changed("components");
      todo = missing();
    }
    return true;
  }

  /** Stop a background component fetch and drop what it cached, as when another parameter is picked. */
  forgetComponents() {
    this._componentToken = null;
    this._cacheAll = false;
    this._components.clear();
  }

  async componentRange(param, nuclide) {
    const key = `range|${param}|${nuclide || ""}`;
    if (!this._ranges.has(key)) this._ranges.set(key, api.resultsComponentRange(param, nuclide));
    return this._ranges.get(key);
  }

  /** Block insides for a topology, and where each block's components start in a node's values. */
  async designs(topologyId) {
    if (!this._designs.has(topologyId)) {
      this._designs.set(
        topologyId,
        api.resultsDesigns(topologyId).then((payload) => {
          const blockDesign = Int32Array.from(payload.blockDesign);
          const compStart = new Int32Array(blockDesign.length + 1);
          for (let b = 0; b < blockDesign.length; b += 1) {
            compStart[b + 1] = compStart[b] + payload.designs[blockDesign[b]].length;
          }
          const designs = payload.designs.map(prepareDesign);
          const ready = { designs, blockDesign, compStart };
          this._designs.set(topologyId, ready);
          this._changed("designs");
          return ready;
        }),
      );
    }
    return this._designs.get(topologyId);
  }

  /** Designs if they have already arrived, without asking for them. */
  designsReady(topologyId) {
    const entry = this._designs.get(topologyId);
    return entry && !(entry instanceof Promise) ? entry : null;
  }

  /** A block's name at a node, or "…" until the names of that node's topology have arrived. */
  blockName(t, b) {
    const id = this.steps[t].topology;
    const names = this._names?.get(id);
    if (names && !(names instanceof Promise)) return names.blockNames[b] ?? "—";
    this._names ??= new Map();
    if (!names) {
      this._names.set(id, api.resultsNames(id).then((payload) => {
        this._names.set(id, payload);
        this._changed("names");
      }, () => this._names.delete(id)));
    }
    return "…";
  }

  // -- finding things ---------------------------------------------------------------------------

  /** The block of assembly `a` that spans height `z` at node `t`, as a canonical block index. */
  blockAtHeight(t, a, z) {
    const topo = this.topologyAt(t);
    const base = this.blockOffset[t];
    for (let b = topo.blockStart[a]; b < topo.blockStart[a + 1]; b += 1) {
      if (this.zbottom[base + b] <= z && z < this.ztop[base + b]) return b;
    }
    return -1;
  }

  close() {
    this.closed = true;
    this.listeners.clear();
  }
}

/** The outline of one core cell, as offsets from its centre in centimetres. */
export function cellShape(geometry) {
  if (geometry.shape === "hex") {
    const radius = geometry.pitch / Math.sqrt(3);
    const corners = [];
    for (let k = 0; k < 6; k += 1) {
      const angle = ((geometry.cornerAngle + 60 * k) * Math.PI) / 180;
      corners.push([radius * Math.cos(angle), radius * Math.sin(angle)]);
    }
    return { corners, radius, inner: geometry.pitch / 2 };
  }
  const [w, h] = geometry.pitch;
  return {
    corners: [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]],
    radius: Math.hypot(w, h) / 2,
    inner: Math.min(w, h) / 2,
  };
}

/**
 * Put a block design in drawing order.
 *
 * Everything is drawn back to front: the coolant that fills the block, then the ducts from the
 * outside in, then pins from the biggest diameter down, so a fuel slug lands on top of its clad.
 * Shapes keep their index in the block's component list, which is where their value lives.
 */
function prepareDesign(shapes) {
  const order = shapes.map((shape, index) => ({ ...shape, index, at: shape.at ? Float32Array.from(shape.at) : null }));
  const rank = (shape) => {
    if (shape.type === "DerivedShape") return [0, 0];
    if (shape.type === "Hexagon") return [1, -(shape.op || 0)];
    if (shape.type === "Square") return [1, -(shape.widthOuter || 0)];
    if (shape.type === "Rectangle") return [1, -(shape.lengthOuter || 0)];
    if (shape.type === "Circle") return [2, -(shape.od || 0)];
    return [3, 0];
  };
  order.sort((a, b) => {
    const [ra, sa] = rank(a);
    const [rb, sb] = rank(b);
    return ra - rb || sa - sb;
  });
  return { shapes, drawOrder: order.filter((shape) => drawable(shape)) };
}

function drawable(shape) {
  if (shape.type === "DerivedShape") return true;
  if (shape.type === "Hexagon") return shape.op > 0;
  if (shape.type === "Square") return shape.widthOuter > 0;
  if (shape.type === "Rectangle") return shape.lengthOuter > 0 && shape.widthOuter > 0;
  if (shape.type === "Circle") return shape.od > 0 && (shape.at || (shape.mult || 1) <= 1);
  return false;
}
