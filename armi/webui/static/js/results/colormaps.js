// Colour maps for results.
//
// Each map is a short list of stops sampled from its published definition, expanded to a 256-entry
// table once. Looking a colour up is then an index into an array of ready-made CSS strings, which
// matters when a slider drag redraws a few thousand cells per frame.

const STOPS = {
  viridis: ["#440154", "#482878", "#3e4989", "#31688e", "#26828e", "#1f9e89", "#35b779", "#6ece58", "#b5de2b", "#fde725"],
  plasma: ["#0d0887", "#46039f", "#7201a8", "#9c179e", "#bd3786", "#d8576b", "#ed7953", "#fb9f3a", "#fdca26", "#f0f921"],
  inferno: ["#000004", "#1b0c41", "#4a0c6b", "#781c6d", "#a52c60", "#cf4446", "#ed6925", "#fb9b06", "#f7d13d", "#fcffa4"],
  magma: ["#000004", "#180f3d", "#440f76", "#721f81", "#9e2f7f", "#cd4071", "#f1605d", "#fd9668", "#feca8d", "#fcfdbf"],
  cividis: ["#00224e", "#123570", "#3b496c", "#575d6d", "#707173", "#8a8678", "#a59c74", "#c3b369", "#e1cc55", "#fee838"],
  turbo: ["#30123b", "#4145ab", "#4675ed", "#39a2fc", "#1bcfd4", "#24eca6", "#61fc6c", "#a4fc3b", "#d1e834",
    "#f3c63a", "#fe9b2d", "#f36315", "#d93806", "#b11901", "#7a0403"],
  jet: ["#00007f", "#0000ff", "#007fff", "#00ffff", "#7fff7f", "#ffff00", "#ff7f00", "#ff0000", "#7f0000"],
  coolwarm: ["#3b4cc0", "#6788ee", "#9abbff", "#c9d7f0", "#edd1c2", "#f7a889", "#e26952", "#b40426"],
  RdBu: ["#053061", "#2166ac", "#4393c3", "#92c5de", "#d1e5f0", "#f7f7f7", "#fddbc7", "#f4a582", "#d6604d",
    "#b2182b", "#67001f"],
  gray: ["#000000", "#ffffff"],
};

export const COLORMAPS = Object.keys(STOPS);
const SIZE = 256;
const cache = new Map();

function parse(hex) {
  return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
}

/**
 * A colour table.
 *
 * @returns {{css: string[], rgb: Uint8Array, ink: string[]}} CSS colours, packed RGB, and for each
 *   entry the text colour that reads on top of it
 */
export function colormap(name, reversed = false) {
  const key = `${name}:${reversed}`;
  if (cache.has(key)) return cache.get(key);
  const stops = (STOPS[name] || STOPS.viridis).map(parse);
  const css = new Array(SIZE);
  const ink = new Array(SIZE);
  const rgb = new Uint8Array(SIZE * 3);
  for (let k = 0; k < SIZE; k += 1) {
    let f = k / (SIZE - 1);
    if (reversed) f = 1 - f;
    const position = f * (stops.length - 1);
    const lo = Math.min(Math.floor(position), stops.length - 2);
    const t = position - lo;
    const color = [0, 1, 2].map((c) => Math.round(stops[lo][c] + (stops[lo + 1][c] - stops[lo][c]) * t));
    rgb.set(color, k * 3);
    css[k] = `rgb(${color[0]},${color[1]},${color[2]})`;
    // Relative luminance, roughly; dark text on light fills and the other way round.
    const luminance = 0.2126 * srgb(color[0]) + 0.7152 * srgb(color[1]) + 0.0722 * srgb(color[2]);
    ink[k] = luminance > 0.36 ? "#111418" : "#f4f6f8";
  }
  const table = { css, ink, rgb };
  cache.set(key, table);
  return table;
}

function srgb(channel) {
  const c = channel / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

/**
 * Something that turns a value into a table index, for a range and a scale.
 *
 * Values outside the range clamp to its ends. Missing values (NaN) come back as -1 so the caller
 * can draw them as missing rather than as the bottom of the scale.
 */
export function scaler(min, max, log = false) {
  if (log) {
    const lo = Math.log10(Math.max(min, 1e-300));
    const hi = Math.log10(Math.max(max, min * 10, 1e-299));
    const span = hi - lo || 1;
    return (value) => {
      if (!(value > 0)) return Number.isNaN(value) ? -1 : 0;
      const f = (Math.log10(value) - lo) / span;
      return f <= 0 ? 0 : f >= 1 ? SIZE - 1 : Math.round(f * (SIZE - 1));
    };
  }
  const span = max - min || 1;
  return (value) => {
    if (Number.isNaN(value)) return -1;
    const f = (value - min) / span;
    return f <= 0 ? 0 : f >= 1 ? SIZE - 1 : Math.round(f * (SIZE - 1));
  };
}

/** Paint a colour bar into a canvas, left to right. */
export function paintBar(canvas, table) {
  const context = canvas.getContext("2d");
  const { width, height } = canvas;
  const image = context.createImageData(width, height);
  for (let x = 0; x < width; x += 1) {
    const k = Math.round((x / Math.max(width - 1, 1)) * (SIZE - 1)) * 3;
    for (let y = 0; y < height; y += 1) {
      const p = (y * width + x) * 4;
      image.data[p] = table.rgb[k];
      image.data[p + 1] = table.rgb[k + 1];
      image.data[p + 2] = table.rgb[k + 2];
      image.data[p + 3] = 255;
    }
  }
  context.putImageData(image, 0, 0);
}

/** A number short enough to print inside a cell. */
export function formatValue(value, digits = 3) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (value === 0) return "0";
  const magnitude = Math.abs(value);
  if (magnitude >= 1e5 || magnitude < 1e-3) return value.toExponential(Math.max(digits - 1, 0)).replace("e+", "e");
  return Number(value.toPrecision(digits)).toString();
}
