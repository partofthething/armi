// The settings editor.
//
// A case has a few hundred settings and you came here to change two of them, so the job is
// narrowing. Settings are grouped by whoever defined them, filtered to the ones that differ from
// their defaults by default, and searchable by name and description at once.
//
// Every setting carries its own description in ARMI, so nothing is explained twice here.
//
// A setting whose plugin described an editor for it gets that control instead of the generic box
// its type would earn. The widgets are general -- one of a list, several of a list, a table of
// rows -- and nothing here knows what any particular setting means.

import { api } from "../api.js";
import { h, fill } from "../dom.js";
import { store } from "../store.js";
import { checkbox, comboInput, dialog, field, numberInput, selectInput, textInput, toast } from "../ui.js";

export function settingsView(ctx) {
  if (!store.settings) {
    fill(ctx.nav);
    fill(ctx.stage, h("div.empty", { style: { marginTop: "18vh" } }, "Loading settings…"));
    fill(ctx.inspector);
    api
      .settings()
      .then((data) => {
        store.settings = data;
        ctx.refresh();
      })
      .catch((error) =>
        fill(ctx.stage, h("div.empty", h("strong", "Could not read the settings"), error.message)),
      );
    return;
  }

  const data = store.settings;
  ctx.state.group = ctx.state.group || "all";
  if (ctx.state.changedOnly === undefined) ctx.state.changedOnly = true;
  const query = (ctx.state.query || "").trim().toLowerCase();

  const visible = data.settings.filter((setting) => {
    if (ctx.state.group !== "all" && setting.group !== ctx.state.group) return false;
    if (ctx.state.changedOnly && setting.isDefault && !query) return false;
    if (!query) return true;
    return (
      setting.name.toLowerCase().includes(query) ||
      (setting.description || "").toLowerCase().includes(query)
    );
  });

  renderNavigator(ctx, data);
  renderStage(ctx, data, visible);
  renderInspector(ctx, data);
}

function renderNavigator(ctx, data) {
  const counts = {};
  for (const setting of data.settings) {
    counts[setting.group] = (counts[setting.group] || 0) + (setting.isDefault ? 0 : 1);
  }
  const row = (id, label, count) =>
    h("button.item", {
      type: "button",
      "aria-selected": String(ctx.state.group === id),
      onclick: () => {
        ctx.state.group = id;
        ctx.refresh();
      },
    }, h("span.item__name", label), h("span.item__meta", count ? `${count} changed` : ""));

  fill(
    ctx.nav,
    h("div.panel__head", h("span.panel__title", "Settings"),
      h("span.panel__count", String(data.settings.length))),
    h("div.panel__body",
      row("all", "Everything", data.settings.filter((setting) => !setting.isDefault).length),
      ...data.groups.map((group) => row(group, group, counts[group] || 0)),
    ),
  );
}

/**
 * Settings in the file that this ARMI does not define.
 *
 * Almost always a plugin that is not loaded. They are still in the file and still matter; they
 * just cannot be shown or checked here, so the one thing the editor can usefully do is say so.
 */
function ignoredBanner(data) {
  if (!data.ignored?.length) return null;
  return h(
    "div",
    {
      style: {
        margin: "12px 0 4px", padding: "10px 12px", maxWidth: "980px",
        border: "1px solid var(--amber-dim)", borderLeftWidth: "3px",
        borderRadius: "var(--radius)", background: "var(--chrome-2)",
      },
    },
    h("div", { style: { marginBottom: "4px" } },
      `${data.ignored.length} setting${data.ignored.length === 1 ? "" : "s"} in this file ` +
      "are not defined by the running application, so they are not shown below."),
    h("div.mono", { style: { fontSize: "11.5px", color: "var(--text-dim)" } }, data.ignored.join(", ")),
    h("div.field__hint", { style: { marginTop: "6px" } },
      "Usually a plugin that is not loaded. Saving over this file would drop them, so that is " +
      "refused; start ARMI with the plugin that owns them, or save somewhere else."),
  );
}

function renderStage(ctx, data, visible) {
  const rows = visible.map((setting) => settingRow(ctx, setting));

  fill(
    ctx.stage,
    h("div.stage__bar",
      h("span.stage__title", ctx.state.group === "all" ? "All settings" : ctx.state.group),
      h("span.item__meta", `${visible.length} shown`),
      checkbox({
        label: "Only what differs from the default",
        checked: ctx.state.changedOnly,
        onChange: (value) => {
          ctx.state.changedOnly = value;
          ctx.refresh();
        },
      }),
      h("input.searchbox", {
        type: "search", placeholder: "Find by name or description",
        value: ctx.state.query || "", style: { width: "260px" },
        oninput: (event) => {
          ctx.state.query = event.target.value;
          ctx.refresh();
          const box = ctx.stage.querySelector(".searchbox");
          box?.focus();
          box?.setSelectionRange(box.value.length, box.value.length);
        },
      }),
      h("span.spacer"),
      h("button.btn.btn--sm.btn--primary", { type: "button", onclick: () => saveSettings(ctx) }, "Save settings"),
    ),
    h("div.stage__body", { style: { padding: "12px 16px 24px" } },
      ignoredBanner(data),
      rows.length
        ? h("div", { style: { display: "grid", gap: "2px", maxWidth: "980px" } }, ...rows)
        : h("div.empty", h("strong", "Nothing to show"),
            ctx.state.changedOnly
              ? "Everything in this group is at its default. Untick the filter to see them."
              : "No setting matches that search.")),
  );
}

function settingRow(ctx, setting) {
  // A table needs the width of the page, so it goes under its description rather than beside it.
  if (setting.editor?.widget === "table") return wideSettingRow(ctx, setting);
  return h(
    "div",
    {
      style: {
        display: "grid",
        gridTemplateColumns: "minmax(200px, 300px) minmax(220px, 340px) 1fr",
        gap: "12px",
        alignItems: "start",
        padding: "8px 10px",
        borderBottom: "1px solid var(--line-soft)",
        borderLeft: `2px solid ${setting.isDefault ? "transparent" : "var(--amber-dim)"}`,
      },
    },
    h("div",
      h("div.mono", { style: { fontSize: "12.5px" } }, setting.name),
      setting.label && setting.label !== setting.name
        ? h("div.field__hint", setting.label)
        : null,
    ),
    h("div", { style: { display: "grid", gap: "4px", justifyItems: "start" } },
      control(ctx, setting),
      setting.isDefault
        ? null
        : h("button.btn.btn--quiet.btn--sm", {
            type: "button",
            style: { padding: "0 2px" },
            title: `Back to ${JSON.stringify(setting.default)}`,
            onclick: () => change(ctx, setting.name, setting.default),
          }, "reset to default"),
    ),
    h("div.field__hint", { style: { lineHeight: 1.6, maxWidth: "62ch" } }, setting.description),
  );
}

function wideSettingRow(ctx, setting) {
  return h(
    "div",
    {
      style: {
        display: "grid", gap: "8px", padding: "10px",
        borderBottom: "1px solid var(--line-soft)",
        borderLeft: `2px solid ${setting.isDefault ? "transparent" : "var(--amber-dim)"}`,
      },
    },
    h("div", { style: { display: "flex", gap: "12px", alignItems: "baseline" } },
      h("div.mono", { style: { fontSize: "12.5px" } }, setting.name),
      h("div.field__hint", { style: { lineHeight: 1.6 } }, setting.label || ""),
      h("span.spacer"),
      setting.isDefault
        ? null
        : h("button.btn.btn--quiet.btn--sm", {
            type: "button",
            title: `Back to ${JSON.stringify(setting.default)}`,
            onclick: () => change(ctx, setting.name, setting.default),
          }, "reset to default"),
    ),
    h("div.field__hint", { style: { lineHeight: 1.6, maxWidth: "80ch" } }, setting.description),
    control(ctx, setting),
  );
}

function control(ctx, setting) {
  const apply = (value) => change(ctx, setting.name, value);
  const body =
    (setting.editor && describedControl(setting.editor, setting.value, apply)) ||
    genericControl(setting, apply);
  if (!setting.editor?.hint) return body;
  return h("div", { style: { display: "grid", gap: "4px", justifyItems: "start" } },
    body, h("div.field__hint", setting.editor.hint));
}

function genericControl(setting, apply) {
  if (setting.kind === "bool") {
    return checkbox({ label: setting.value ? "on" : "off", checked: !!setting.value, onChange: apply });
  }
  if (setting.options && setting.enforced) {
    return selectInput({ value: setting.value, options: setting.options.map(String), onChange: apply });
  }
  if (setting.kind === "int" || setting.kind === "float") {
    return numberInput({
      value: setting.value,
      step: setting.kind === "int" ? 1 : "any",
      onChange: (value) => apply(setting.kind === "int" && value != null ? Math.round(value) : value),
    });
  }
  if (setting.kind === "list") {
    const values = Array.isArray(setting.value) ? setting.value : [];
    return h("textarea.input", {
      rows: Math.min(6, Math.max(2, values.length)),
      value: values.join("\n"),
      placeholder: "one per line",
      onchange: (event) =>
        apply(event.target.value.split("\n").map((line) => line.trim()).filter(Boolean)),
    });
  }
  if (setting.kind === "dict") {
    return h("textarea.input", {
      rows: 3,
      value: JSON.stringify(setting.value ?? {}, null, 1),
      onchange: (event) => {
        try {
          apply(JSON.parse(event.target.value));
        } catch (error) {
          toast(`${setting.name} needs valid JSON`, { kind: "error", detail: error.message });
        }
      },
    });
  }
  return textInput({ value: setting.value ?? "", mono: true, onChange: apply });
}

/**
 * The control a plugin asked for, or nothing if it asked for none, or for one this build does
 * not have -- in which case the setting falls back to the control its type would have earned.
 *
 * Widgets are shapes of value, not shapes of problem: one of a list, several of a list, a table
 * of rows. What the values mean is the plugin's business.
 */
function describedControl(editor, value, apply) {
  if (editor.widget === "choice") return choiceControl(editor, value, apply);
  if (editor.widget === "choices") return choicesControl(editor, value, apply);
  if (editor.widget === "table") return tableControl(editor, value, apply);
  return null;
}

/** One value out of a list. */
function choiceControl(editor, value, apply) {
  const values = editor.options.map((option) => String(option.value));
  if (editor.allowOther) {
    return comboInput({ value: value ?? "", options: values, onChange: (next) => apply(next ?? "") });
  }
  return selectInput({
    value: value ?? "",
    options: editor.options.map((option) => [String(option.value), option.label]),
    blank: editor.allowEmpty ? "— none —" : null,
    onChange: (next) => apply(next ?? ""),
  });
}

/** Several values out of a list, for a list-valued setting. */
function choicesControl(editor, value, apply) {
  const chosen = Array.isArray(value) ? value.map(String) : [];
  const offered = editor.options.map((option) => String(option.value));
  const extra = chosen.filter((each) => !offered.includes(each));

  // Rebuilt in the offered order every time, so the list does not depend on what was ticked first.
  const rebuild = (name, on) =>
    apply([...offered.filter((each) => (each === name ? on : chosen.includes(each))), ...extra]);

  return h(
    "div",
    { style: { display: "grid", gap: "6px", justifyItems: "start" } },
    ...editor.options.map((option) =>
      h("div", { style: { display: "grid", gap: "1px" } },
        checkbox({
          label: option.label,
          checked: chosen.includes(String(option.value)),
          onChange: (on) => rebuild(String(option.value), on),
        }),
        option.help ? h("div.field__hint", { style: { paddingLeft: "22px" } }, option.help) : null,
      ),
    ),
    editor.allowOther
      ? field(
          "Anything else",
          textInput({
            value: extra.join(", "),
            mono: true,
            placeholder: editor.placeholder || "comma separated",
            onChange: (text) =>
              apply([
                ...offered.filter((each) => chosen.includes(each)),
                ...text.split(",").map((each) => each.trim()).filter(Boolean),
              ]),
          }),
        )
      : null,
  );
}

/** A dict of dicts, one row per key. */
function tableControl(editor, value, apply) {
  const rows = value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const keys = Object.keys(rows);
  const rowLabel = editor.rowLabel || "Row";

  const withRows = (entries) => apply(Object.fromEntries(entries));
  const setCell = (key, column, next) =>
    withRows(keys.map((each) => [each, each === key ? { ...rows[each], [column]: next } : rows[each]]));
  const rename = (key, next) => {
    const name = (next || "").trim();
    if (!name || (name !== key && keys.includes(name))) return;
    withRows(keys.map((each) => [each === key ? name : each, rows[each]]));
  };
  const remove = (key) => withRows(keys.filter((each) => each !== key).map((each) => [each, rows[each]]));
  const add = () => {
    let name = `new ${rowLabel.toLowerCase()}`;
    for (let n = 2; keys.includes(name); n += 1) name = `new ${rowLabel.toLowerCase()} ${n}`;
    withRows([...keys.map((each) => [each, rows[each]]), [name, { ...(editor.newRow || {}) }]]);
  };

  return h(
    "div",
    { style: { display: "grid", gap: "8px", justifyItems: "start", maxWidth: "1200px" } },
    keys.length
      ? // Fixed layout so the columns share the width evenly rather than each one shrinking to
        // the control it holds, which truncates the values the table is there to show.
        h("table.table", { style: { width: "100%", tableLayout: "fixed" } },
          h("thead", h("tr",
            h("th", rowLabel),
            ...editor.columns.map((column) =>
              h("th", column.label, column.unit ? h("span.field__unit", ` ${column.unit}`) : null)),
            h("th", ""))),
          h("tbody", ...keys.map((key) =>
            h("tr",
              h("td", textInput({ value: key, mono: true, onChange: (next) => rename(key, next) })),
              ...editor.columns.map((column) =>
                h("td", cellControl(column, (rows[key] || {})[column.key], (next) => setCell(key, column.key, next)))),
              h("td", { style: { width: "72px" } },
                h("button.btn.btn--quiet.btn--sm", {
                  type: "button", title: `Remove ${key}`, onclick: () => remove(key),
                }, "remove")),
            ))))
      : h("div.field__hint", `No ${rowLabel.toLowerCase()} defined.`),
    h("button.btn.btn--sm", { type: "button", onclick: add }, `Add ${rowLabel.toLowerCase()}`),
  );
}

/** Fill the cell. A table's columns are as wide as the table divides them, not as wide as the
 * default control, which otherwise truncates the very values it is there to show. */
function stretch(node) {
  const target = node.querySelector ? node.querySelector("input, select") || node : node;
  Object.assign(target.style, { width: "100%", minWidth: "0" });
  return node;
}

function cellControl(column, value, apply) {
  return stretch(cellInput(column, value, apply));
}

function cellInput(column, value, apply) {
  if (column.widget === "number") {
    return numberInput({ value: value ?? null, placeholder: column.placeholder || "", onChange: apply });
  }
  if (column.widget === "choice" && column.options) {
    const values = column.options.map((option) => String(option.value));
    return column.allowOther
      ? comboInput({ value: value ?? "", options: values, onChange: (next) => apply(next ?? "") })
      : selectInput({
          value: value ?? "",
          options: column.options.map((option) => [String(option.value), option.label]),
          onChange: (next) => apply(next ?? ""),
        });
  }
  return textInput({ value: value ?? "", placeholder: column.placeholder || "", onChange: apply });
}

async function change(ctx, name, value) {
  try {
    store.settings = await api.putSettings({ [name]: value });
    ctx.refresh();
  } catch (error) {
    toast(`${name} would not take that`, { kind: "error", detail: error.message });
  }
}

async function saveSettings(ctx, path = null) {
  try {
    const result = await api.saveSettings(path || store.settings?.path || null, "short");
    store.session = result.state;
    toast(`Wrote ${result.path.split(/[\\/]/).pop()}`, { kind: "ok" });
  } catch (error) {
    if (error.status === 409) return offerSaveElsewhere(ctx, error);
    toast("Nothing was written", { kind: "error", detail: error.message });
  }
}

/** Offered when saving would drop settings this ARMI cannot see. */
async function offerSaveElsewhere(ctx, error) {
  const current = store.settings?.path || "settings.yaml";
  const input = h("input.input.mono", { type: "text", value: current.replace(/\.ya?ml$/i, "-edited.yaml") });
  const target = await dialog({
    title: "Saving here would drop settings",
    body: h("div",
      h("p", { style: { marginTop: 0, color: "var(--text-dim)", lineHeight: 1.7 } }, error.message),
      field("Save to", input)),
    actions: [["Save there", "primary", () => input.value.trim() || null]],
  });
  if (target) await saveSettings(ctx, target);
}

function renderInspector(ctx, data) {
  const changed = data.settings.filter((setting) => !setting.isDefault);
  fill(
    ctx.inspector,
    h("div.panel__head", h("span.panel__title", "Case"),
      h("span.panel__count", `${changed.length} off default`)),
    h("div.panel__body", { style: { padding: 0 } },
      h("section.group",
        h("h3.group__title", "File"),
        h("div.crumb", data.path || "not saved to a file yet"),
        h("div.field__hint",
          "Saving writes only the settings that differ from their defaults, which is what keeps " +
          "an ARMI settings file short enough to read."),
      ),
      h("section.group",
        h("h3.group__title", "Changed from default"),
        changed.length
          ? h("table.table", h("tbody", ...changed.map((setting) =>
              h("tr",
                h("td.mono", { style: { fontSize: "11.5px" } }, setting.name),
                h("td.mono", { style: { fontSize: "11.5px", color: "var(--text-dim)",
                  wordBreak: "break-all" } }, formatValue(setting.value))))))
          : h("div.field__hint", "This case is entirely at defaults."),
      ),
    ),
  );
}

function formatValue(value) {
  if (Array.isArray(value)) return value.join(", ") || "[]";
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value);
}
