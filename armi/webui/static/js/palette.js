// Giving every class in a lattice a look of its own.
//
// A lattice map is a choropleth: any assembly type can end up next to any other, so the colours
// have to be told apart in every pairing, not just in the order they appear in a legend. Four hues
// is as far as that goes on a pale sheet before pairs start colliding, which is fewer hues than a
// core has assembly types.
//
// So colour is not the only channel. A class gets a hue and a hatch, the way a section drawing has
// always distinguished materials, and every cell carries its specifier as text on top. Sixteen
// classes are separable that way, and none of them lean on colour alone.

const HUES = ["#2a78d6", "#eb6834", "#1baf7a", "#4a3aa7"];
const TEXTURES = ["solid", "hatch", "backhatch", "dots"];
const SHEET = "#f5f6f7";

/** The specifier that means "nothing here" in an ARMI lattice map. */
export const EMPTY = "-";

function mix(hex, toward, amount) {
  const parse = (value) => [1, 3, 5].map((i) => parseInt(value.slice(i, i + 2), 16));
  const [r1, g1, b1] = parse(hex);
  const [r2, g2, b2] = parse(toward);
  const channel = (a, b) => Math.round(a + (b - a) * amount);
  return `#${[channel(r1, r2), channel(g1, g2), channel(b1, b2)]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("")}`;
}

/**
 * Work out a stable look for each class in a lattice.
 *
 * Slots are handed out in sorted order rather than the order cells happen to appear, so a given
 * set of specifiers looks the same every time the file is opened.
 *
 * @param {string[]} keys the distinct specifiers in play
 * @returns {Map<string, object>} key to `{hue, texture, fill, stroke, slot}`
 */
export function assign(keys) {
  const distinct = [...new Set(keys.filter((key) => key && key !== EMPTY))].sort((a, b) =>
    a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" }),
  );
  const looks = new Map();
  distinct.forEach((key, slot) => {
    const hue = HUES[slot % HUES.length];
    const texture = TEXTURES[Math.floor(slot / HUES.length) % TEXTURES.length];
    looks.set(key, {
      slot,
      hue,
      texture,
      stroke: hue,
      fill: mix(hue, SHEET, 0.8),
      hatch: mix(hue, SHEET, 0.42),
      id: `pat-${slot}`,
    });
  });
  looks.set(EMPTY, { slot: -1, hue: null, texture: "none", stroke: "#c8ccd2", fill: "transparent", id: null });
  return looks;
}

/** How a cell should be painted: a pattern reference when it is hatched, a flat tint otherwise. */
export function paintOf(look) {
  if (!look) return "transparent";
  return look.texture === "solid" || look.texture === "none" ? look.fill : `url(#${look.id})`;
}

/**
 * SVG `<pattern>` markup for the hatched classes.
 *
 * Returned as data rather than nodes so both the on-screen map and an exported standalone SVG can
 * build them the same way.
 */
export function patternSpecs(looks) {
  const specs = [];
  for (const look of looks.values()) {
    if (!look.id || look.texture === "solid" || look.texture === "none") continue;
    specs.push({ id: look.id, texture: look.texture, fill: look.fill, ink: look.hatch });
  }
  return specs;
}

/** A small swatch of the real thing, for legends and palettes. */
export function swatchStyle(look) {
  if (!look || !look.hue) {
    return { background: SHEET, border: "1px dashed #aeb4bc" };
  }
  const base = { background: look.fill, border: `1.5px solid ${look.stroke}` };
  if (look.texture === "hatch") {
    base.backgroundImage = `repeating-linear-gradient(45deg, ${look.hatch} 0 2px, transparent 2px 5px)`;
  } else if (look.texture === "backhatch") {
    base.backgroundImage = `repeating-linear-gradient(135deg, ${look.hatch} 0 2px, transparent 2px 5px)`;
  } else if (look.texture === "dots") {
    base.backgroundImage = `radial-gradient(${look.hatch} 1.1px, transparent 1.2px)`;
    base.backgroundSize = "4px 4px";
  }
  return base;
}

// Pins are too small for a hatch to read, so a cross section separates materials past the fourth
// by tint depth instead: same four hues, three depths, twelve materials that stay apart.
const TINTS = [0.72, 0.44, 0.87];

/** Colour for a material name, used in block cross sections. */
export function materialLook(name, index) {
  const hue = HUES[index % HUES.length];
  const tint = TINTS[Math.floor(index / HUES.length) % TINTS.length];
  return { hue, fill: mix(hue, SHEET, tint), stroke: mix(hue, "#11151a", tint > 0.8 ? 0.15 : 0) };
}

export const SHEET_COLOR = SHEET;
