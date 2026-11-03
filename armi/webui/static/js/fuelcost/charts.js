// Small SVG charts for the fuel cost report, drawn the way the PDF draws them.
//
// Each function returns an <svg> sized by its viewBox, so it scales with the page. Hovering any mark
// shows its exact value through the SVG's own <title>, which costs nothing and works everywhere.

import { s } from "../dom.js";

export const CATEGORY_COLORS = {
  ore: "#2a78d6",
  conversion: "#eb6834",
  enrichment: "#1baf7a",
  fabrication: "#eda100",
  backEnd: "#e87ba4",
};

export const SHORT_LABELS = {
  ore: "Natural U",
  conversion: "Conversion",
  enrichment: "Enrichment",
  fabrication: "Fabrication",
  backEnd: "Spent fuel",
};

const INK = "#0b0b0b";
const SECONDARY = "#52514e";
const MUTED = "#898781";
const GRID = "#e1e0d9";
const LOWER = "#2a78d6";
const HIGHER = "#e34948";

/** A step that lands axis ticks on round numbers. */
export function niceTicks(lo, hi, count = 5) {
  const span = hi - lo || Math.abs(hi) || 1;
  const raw = span / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * power).find((m) => m >= raw) || 10 * power;
  const ticks = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) ticks.push(Number(v.toPrecision(12)));
  return ticks;
}

function svg(width, height) {
  return s("svg.fc-chart", { viewBox: `0 0 ${width} ${height}`, preserveAspectRatio: "xMidYMid meet" });
}

function text(x, y, content, attrs = {}) {
  return s("text", { x, y, "font-size": 11, fill: MUTED, ...attrs }, content);
}

function titled(node, title) {
  node.appendChild(s("title", {}, title));
  return node;
}

/**
 * Vertical stacked bars, one per load: the fuel bought for it, by front-end step.
 *
 * @param {object[]} bars each `{label, sublabel, segments: [{key, value, title}], total, title}`
 */
export function stackedBars(bars, { height = 240, format = (v) => v, keys }) {
  const width = 900;
  const pad = { left: 64, right: 12, top: 18, bottom: 40 };
  const chart = svg(width, height);
  const peak = Math.max(...bars.map((bar) => bar.total), 1e-9);
  const ticks = niceTicks(0, peak * 1.12, 4);
  const top = ticks[ticks.length - 1];
  const y = (v) => pad.top + (1 - v / top) * (height - pad.top - pad.bottom);
  const slot = (width - pad.left - pad.right) / Math.max(bars.length, 1);
  const barWidth = Math.min(46, slot * 0.68);

  for (const tick of ticks) {
    chart.appendChild(s("line", { x1: pad.left, x2: width - pad.right, y1: y(tick), y2: y(tick), stroke: GRID }));
    chart.appendChild(text(pad.left - 8, y(tick) + 4, format(tick), { "text-anchor": "end" }));
  }
  const labelEvery = Math.max(1, Math.ceil(bars.length / 12));
  bars.forEach((bar, i) => {
    const cx = pad.left + slot * (i + 0.5);
    let base = 0;
    const group = s("g");
    for (const key of keys) {
      const segment = bar.segments.find((each) => each.key === key);
      if (!segment || segment.value <= 0) continue;
      const y0 = y(base);
      const y1 = y(base + segment.value);
      group.appendChild(titled(s("rect", {
        x: cx - barWidth / 2, y: y1, width: barWidth, height: Math.max(y0 - y1 - 0.8, 0.5),
        fill: CATEGORY_COLORS[key],
      }), segment.title));
      base += segment.value;
    }
    chart.appendChild(titled(group, bar.title));
    if (bars.length <= 14 || i % labelEvery === 0) {
      chart.appendChild(text(cx, height - pad.bottom + 16, bar.label, { "text-anchor": "middle", fill: SECONDARY }));
      chart.appendChild(text(cx, height - pad.bottom + 30, bar.sublabel, { "text-anchor": "middle", "font-size": 10 }));
    }
    if (bars.length <= 14) {
      chart.appendChild(text(cx, y(bar.total) - 5, format(bar.total, true), { "text-anchor": "middle", "font-size": 10 }));
    }
  });
  return chart;
}

/** Stacked areas over time: what has been paid by each moment, by category. */
export function stackedArea(times, series, { height = 260, keys, format = (v) => v, marker = null }) {
  const width = 900;
  const pad = { left: 64, right: 12, top: 18, bottom: 34 };
  const chart = svg(width, height);
  const totals = times.map((_, k) => keys.reduce((sum, key) => sum + series[key][k], 0));
  const peak = Math.max(...totals, 1e-9);
  const yTicks = niceTicks(0, peak * 1.08, 5);
  const yTop = yTicks[yTicks.length - 1];
  const t0 = times[0];
  const t1 = times[times.length - 1];
  const x = (t) => pad.left + ((t - t0) / (t1 - t0 || 1)) * (width - pad.left - pad.right);
  const y = (v) => pad.top + (1 - v / yTop) * (height - pad.top - pad.bottom);

  for (const tick of yTicks) {
    chart.appendChild(s("line", { x1: pad.left, x2: width - pad.right, y1: y(tick), y2: y(tick), stroke: GRID }));
    chart.appendChild(text(pad.left - 8, y(tick) + 4, format(tick), { "text-anchor": "end" }));
  }
  for (const tick of niceTicks(t0, t1, 8)) {
    chart.appendChild(text(x(tick), height - pad.bottom + 18, `${tick}`, { "text-anchor": "middle" }));
  }
  chart.appendChild(text(width - pad.right, height - 4, "years from first startup", { "text-anchor": "end", "font-size": 10 }));

  const lower = times.map(() => 0);
  for (const key of keys) {
    const upper = lower.map((value, k) => value + series[key][k]);
    const points = [
      ...times.map((t, k) => `${x(t).toFixed(1)},${y(upper[k]).toFixed(1)}`),
      ...times.map((t, k) => `${x(t).toFixed(1)},${y(lower[k]).toFixed(1)}`).reverse(),
    ];
    chart.appendChild(titled(s("polygon", { points: points.join(" "), fill: CATEGORY_COLORS[key] }),
      `${SHORT_LABELS[key]}: ${format(series[key][series[key].length - 1])} over the plant's life`));
    upper.forEach((value, k) => {
      lower[k] = value;
    });
  }
  chart.appendChild(s("polyline", {
    points: times.map((t, k) => `${x(t).toFixed(1)},${y(lower[k]).toFixed(1)}`).join(" "),
    fill: "none", stroke: INK, "stroke-width": 1.2,
  }));
  if (t0 < 0) {
    chart.appendChild(s("line", { x1: x(0), x2: x(0), y1: pad.top, y2: height - pad.bottom, stroke: INK }));
    chart.appendChild(text(x(0) + 5, pad.top + 10, "first startup", { fill: SECONDARY }));
  }
  if (marker !== null && marker >= t0 && marker <= t1) {
    chart.appendChild(s("line", { x1: x(marker), x2: x(marker), y1: pad.top, y2: height - pad.bottom,
      stroke: "#ffb454", "stroke-width": 1.5, "stroke-dasharray": "4 3" }));
  }
  return chart;
}

/** Horizontal bars either side of a base value: what each input moves the cost to. */
export function tornado(rows, base, { format = (v) => v } = {}) {
  const width = 900;
  const rowHeight = 30;
  const pad = { left: 200, right: 20, top: 10, bottom: 28 };
  const height = pad.top + pad.bottom + rowHeight * rows.length;
  const chart = svg(width, height);
  const span = Math.max(...rows.flatMap((row) => [Math.abs(row.low - base), Math.abs(row.high - base)]), 1e-9);
  // Room either side of the longest bar for its label, as the PDF leaves.
  const lo = base - 1.7 * span;
  const hi = base + 1.7 * span;
  const x = (v) => pad.left + ((v - lo) / (hi - lo)) * (width - pad.left - pad.right);

  for (const tick of niceTicks(lo, hi, 7)) {
    chart.appendChild(s("line", { x1: x(tick), x2: x(tick), y1: pad.top, y2: height - pad.bottom, stroke: GRID }));
    chart.appendChild(text(x(tick), height - 8, format(tick), { "text-anchor": "middle" }));
  }
  rows.forEach((row, i) => {
    const cy = pad.top + rowHeight * (i + 0.5);
    chart.appendChild(text(pad.left - 12, cy + 4, row.name, { "text-anchor": "end", fill: INK, "font-size": 12 }));
    for (const [value, label] of [[row.low, row.lowLabel], [row.high, row.highLabel]]) {
      if (Math.abs(value - base) < 1e-9) continue;
      const x0 = x(Math.min(value, base));
      const x1 = x(Math.max(value, base));
      chart.appendChild(titled(s("rect", { x: x0, y: cy - 8, width: Math.max(x1 - x0, 1), height: 16,
        fill: value < base ? LOWER : HIGHER }), `${row.name} ${label}: ${format(value)}`));
      const right = value >= base;
      chart.appendChild(text(right ? x1 + 6 : x0 - 6, cy + 4, `${label}  ${format(value)}`,
        { "text-anchor": right ? "start" : "end", fill: SECONDARY }));
    }
  });
  chart.appendChild(s("line", { x1: x(base), x2: x(base), y1: pad.top - 4, y2: height - pad.bottom, stroke: INK, "stroke-width": 1.3 }));
  return chart;
}

/** From undiscounted to levelized: floating bars that add up to the total. */
export function waterfall(steps, { format = (v) => v } = {}) {
  const width = 900;
  const rowHeight = 32;
  const pad = { left: 190, right: 20, top: 8, bottom: 26 };
  const height = pad.top + pad.bottom + rowHeight * steps.length;
  const chart = svg(width, height);
  const peak = Math.max(...steps.map((step) => Math.max(step.start, step.end)), 1e-9) * 1.9;
  const x = (v) => pad.left + (v / peak) * (width - pad.left - pad.right);
  const colors = [MUTED, "#6f9ad8", "#9dbbe6", "#1f4e8c"];
  for (const tick of niceTicks(0, peak, 6)) {
    chart.appendChild(s("line", { x1: x(tick), x2: x(tick), y1: pad.top, y2: height - pad.bottom, stroke: GRID }));
    chart.appendChild(text(x(tick), height - 8, format(tick), { "text-anchor": "middle" }));
  }
  steps.forEach((step, i) => {
    const cy = pad.top + rowHeight * (i + 0.5);
    const x0 = x(Math.min(step.start, step.end));
    const x1 = x(Math.max(step.start, step.end));
    chart.appendChild(text(pad.left - 12, cy + 4, step.name, { "text-anchor": "end", fill: INK, "font-size": 12 }));
    chart.appendChild(s("rect", { x: x0, y: cy - 9, width: Math.max(x1 - x0, 1), height: 18, fill: colors[i % colors.length] }));
    const change = step.end - step.start;
    const label = step.start === 0 ? format(step.end) : `${change >= 0 ? "+" : "−"}${format(Math.abs(change))}`;
    chart.appendChild(text(x1 + 6, cy + 4, `${label}   ${step.note}`.trim(), { fill: SECONDARY }));
  });
  return chart;
}
