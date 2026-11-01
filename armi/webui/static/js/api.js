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
};
