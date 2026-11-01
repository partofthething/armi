// The file itself.
//
// Everything else in the editor is a way of not reading YAML. This is here because sometimes you
// do want to read it: to check a diff before committing, to copy a section into another case, or
// to see exactly what a change did. It shows what would be written, not what is on disk, so it
// doubles as a preview of the save.

import { api } from "../api.js";
import { h, fill } from "../dom.js";
import { store } from "../store.js";
import { toast } from "../ui.js";

export function yamlView(ctx) {
  const pre = h("pre.mono", {
    style: {
      margin: 0, padding: "16px 20px", fontSize: "12px", lineHeight: 1.55,
      color: "var(--text)", whiteSpace: "pre", overflow: "auto", minHeight: "100%",
    },
  }, "Rendering…");

  fill(
    ctx.stage,
    h("div.stage__bar",
      h("span.stage__title", "Output"),
      h("span.item__meta", store.session?.blueprints?.path || "not saved yet"),
      h("span.spacer"),
      h("button.btn.btn--sm", { type: "button", onclick: () => copy(pre) }, "Copy"),
      h("button.btn.btn--sm", { type: "button", onclick: () => download(pre) }, "Download"),
    ),
    h("div.stage__body", pre),
  );

  fill(ctx.nav,
    h("div.panel__head", h("span.panel__title", "Sections")),
    h("div.panel__body", h("div.field__hint",
      "This is what the save button writes. Anchors are rebuilt so repeated block designs stay " +
      "shared rather than copied, and grids come back out as lattice maps where they went in as one.")),
  );

  fill(ctx.inspector,
    h("div.panel__head", h("span.panel__title", "File")),
    h("div.panel__body", { style: { padding: 0 } },
      h("section.group", h("div.crumb", store.session?.blueprints?.path || "—"))),
  );

  store
    .sync()
    .then(() => api.yaml())
    .then((payload) => {
      pre.textContent = payload.yaml;
      const lines = payload.yaml.split("\n").length;
      ctx.status(`${lines} lines`, `${(payload.yaml.length / 1024).toFixed(1)} kB`, "");
    })
    .catch((error) => {
      pre.textContent = "";
      fill(ctx.stage.querySelector(".stage__body"),
        h("div.empty", h("strong", "Cannot render the file"), error.message));
    });
}

async function copy(pre) {
  try {
    await navigator.clipboard.writeText(pre.textContent);
    toast("Copied to the clipboard", { kind: "ok" });
  } catch {
    toast("The browser would not allow copying", { kind: "error" });
  }
}

function download(pre) {
  const name = (store.session?.blueprints?.path || "blueprints.yaml").split(/[\\/]/).pop();
  const url = URL.createObjectURL(new Blob([pre.textContent], { type: "text/yaml" }));
  const link = h("a", { href: url, download: name });
  link.click();
  URL.revokeObjectURL(url);
}
