// Talking to the local ARMI process.
//
// The session token arrives in the page URL and goes back on every call. Without it the server
// refuses, which is what stops some other page you happen to have open from driving your editor.

const TOKEN = new URLSearchParams(location.search).get("t") || "";

/** Raised for any non-2xx reply, carrying the server's own explanation. */
export class ApiError extends Error {
  constructor(message, status, payload) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

async function call(method, path, body) {
  const response = await fetch(path, {
    method,
    headers: {
      "X-ARMI-Token": TOKEN,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    throw new ApiError(payload?.error || `${method} ${path} failed (${response.status})`, response.status, payload);
  }
  return payload;
}

/** A GET whose answer is raw float32, as the results endpoints send. */
async function callFloats(path) {
  const response = await fetch(path, { headers: { "X-ARMI-Token": TOKEN } });
  if (!response.ok) {
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    throw new ApiError(payload?.error || `GET ${path} failed (${response.status})`, response.status, payload);
  }
  return new Float32Array(await response.arrayBuffer());
}

const query = (params) => {
  const out = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== "") out.set(key, value);
  }
  return out.toString();
};

export const api = {
  state: () => call("GET", "/api/state"),
  catalog: () => call("GET", "/api/catalog"),
  files: (dir, kind = "yaml") => {
    const query = new URLSearchParams({ kind });
    if (dir) query.set("dir", dir);
    return call("GET", `/api/files?${query}`);
  },

  open: (path) => call("POST", "/api/open", { path }),
  blueprints: () => call("GET", "/api/blueprints"),
  putBlueprints: (document) => call("PUT", "/api/blueprints", { document }),
  openBlueprints: (path) => call("POST", "/api/blueprints/open", { path }),
  newBlueprints: (geom, symmetry) => call("POST", "/api/blueprints/new", { geom, symmetry }),
  saveBlueprints: (path, flatten) => call("POST", "/api/blueprints/save", { path, flatten }),
  yaml: () => call("GET", "/api/blueprints/yaml"),
  expandGrid: (name) => call("POST", "/api/grids/expand", { name }),
  previewBlock: (block) => call("POST", "/api/preview/block", { block }),

  settings: () => call("GET", "/api/settings"),
  putSettings: (values) => call("PUT", "/api/settings", { values }),
  saveSettings: (path, style) => call("POST", "/api/settings/save", { path, style }),

  startRun: () => call("POST", "/api/run", {}),
  runStatus: (offset) => call("GET", offset === null || offset === undefined ? "/api/run" : `/api/run?offset=${offset}`),
  stopRun: () => call("POST", "/api/run/stop", {}),

  openResults: (path) => call("POST", "/api/results/open", { path }),
  results: () => call("GET", "/api/results"),
  resultsPositions: (steps) => call("GET", `/api/results/positions?${query({ steps: steps.join(",") })}`),
  resultsSeries: (level, param, index, reduce) =>
    callFloats(`/api/results/series?${query({ level, param, index, reduce })}`),
  resultsComponentsMany: (steps, param, nuclide) =>
    callFloats(`/api/results/components?${query({ steps: steps.join(","), param, nuclide })}`),
  resultsComponents: (step, param, nuclide) =>
    callFloats(`/api/results/components?${query({ step, param, nuclide })}`),
  resultsComponentHistory: async (param, nuclide, component, blocks, aggregate) => {
    const payload = await call("POST", "/api/results/componentHistory", { param, nuclide, component, blocks, aggregate });
    return Float64Array.from(payload.values, (value) => (value === null ? NaN : value));
  },
  resultsComponentRange: (param, nuclide) =>
    call("GET", `/api/results/componentRange?${query({ param, nuclide })}`),
  resultsDesigns: (topology) => call("GET", `/api/results/designs?${query({ topology })}`),
  resultsBlock: (step, block) => call("GET", `/api/results/block?${query({ step, block })}`),
};
