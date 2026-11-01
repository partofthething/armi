// The editor's own file picker.
//
// A browser cannot see the file system, and a local tool should not make people paste absolute
// paths, so the server lists a directory and this walks it. The open dialog uses it for inputs, and
// a path setting uses it for whatever kind of path the setting holds.

import { api } from "./api.js";
import { h, fill } from "./dom.js";
import { dialog } from "./ui.js";

/**
 * Let the user pick a path; resolve to it, or to null if they cancel.
 *
 * @param {string} title shown at the top of the dialog
 * @param {object} [options]
 * @param {string} [options.startDir] where to start; the case's directory by default
 * @param {"yaml"|"any"|"dir"} [options.kind] YAML inputs, any file, or a folder
 * @param {string} [options.actionLabel] the button that accepts the choice
 */
export async function pickPath(title, { startDir = null, kind = "yaml", actionLabel = "Open" } = {}) {
  let dir = startDir;
  const listing = h("div");
  const crumb = h("div.crumb");
  const choosingFolder = kind === "dir";

  const load = async (target) => {
    const data = await api.files(target, kind);
    dir = data.dir;
    crumb.textContent = data.dir;
    delete listing.dataset.chosen;
    fill(
      listing,
      data.parent
        ? h("button.item", { type: "button", onclick: () => load(data.parent) },
            h("span.item__name", "↑ up a level"))
        : null,
      ...data.directories.map((entry) =>
        h("button.item", { type: "button", onclick: () => load(entry.path) },
          h("span.item__name", `${entry.name}/`)),
      ),
      ...data.files.map((entry) =>
        h("button.item", { type: "button", dataset: { path: entry.path },
            onclick: (event) => {
              for (const row of listing.querySelectorAll(".item")) row.setAttribute("aria-selected", "false");
              event.currentTarget.setAttribute("aria-selected", "true");
              listing.dataset.chosen = entry.path;
            },
            ondblclick: () => {
              listing.dataset.chosen = entry.path;
              document.querySelector(".dialog__foot .btn--primary")?.click();
            } },
          h("span.item__name", entry.name),
          h("span.item__meta", `${Math.max(1, Math.round(entry.size / 1024))} kB`)),
      ),
      choosingFolder || data.files.length
        ? null
        : h("div.empty", kind === "yaml" ? "No YAML files in this folder." : "No files in this folder."),
    );
  };

  await load(dir);
  return dialog({
    title,
    body: h("div", crumb, listing),
    actions: [[choosingFolder ? "Choose this folder" : actionLabel, "primary",
      () => (choosingFolder ? dir : listing.dataset.chosen || null)]],
  });
}
