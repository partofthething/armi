// Rail icons. Each one is a picture of the thing it leads to rather than a generic glyph, because
// eight abstract symbols in a column are eight things to memorise.

import { s } from "./dom.js";

const PATHS = {
  // A core map: a ring of assemblies around a centre.
  core: [
    "M12 3.2 18.6 7v8L12 18.8 5.4 15V7z",
    "M12 9.4 14.4 10.8v2.8L12 15 9.6 13.6v-2.8z",
  ],
  // A pin lattice.
  grid: [
    "M4 4h16v16H4z",
    "M9.3 4v16M14.7 4v16M4 9.3h16M4 14.7h16",
  ],
  // A block in cross section: clad, gap, fuel.
  block: [
    "M4.5 4.5h15v15h-15z",
    "M12 7.6a4.4 4.4 0 1 0 0 8.8 4.4 4.4 0 0 0 0-8.8z",
    "M12 10.3a1.7 1.7 0 1 0 0 3.4 1.7 1.7 0 0 0 0-3.4z",
  ],
  // An assembly, stacked axially.
  assembly: [
    "M8 3h8v18H8z",
    "M8 7.5h8M8 12h8M8 16.5h8",
  ],
  // A component library.
  component: [
    "M12 3.4 20 8v8l-8 4.6L4 16V8z",
    "M12 3.4V12l8 4M12 12 4 16",
  ],
  // Nuclide chart.
  nuclide: [
    "M4 20V4",
    "M4 20h16",
    "M6.4 17.2h2.4v-3h-2.4zM9.6 14.2H12v-3H9.6zM12.8 11.2h2.4v-3h-2.4zM16 8.2h2.4v-3H16z",
  ],
  // Instrument dial for settings.
  settings: [
    "M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4z",
    "M12 2.6v2.6M12 18.8v2.6M21.4 12h-2.6M5.2 12H2.6M18.6 5.4l-1.8 1.8M7.2 16.8l-1.8 1.8M18.6 18.6l-1.8-1.8M7.2 7.2 5.4 5.4",
  ],
  // Results: a value rising through time.
  results: [
    "M4 20h16",
    "M4 20V4",
    "M6.5 16.5 10 12l3 2.5 5-7",
  ],
  // The file itself.
  yaml: [
    "M6 3h8l4 4v14H6z",
    "M14 3v4h4",
    "M9 12h6M9 15.5h6M9 19h3.5",
  ],
};

export function icon(name) {
  const paths = PATHS[name] || PATHS.yaml;
  return s(
    "svg",
    { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", "stroke-width": 1.5,
      "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" },
    ...paths.map((d) => s("path", { d })),
  );
}
