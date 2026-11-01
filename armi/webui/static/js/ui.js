// Shared interface pieces: form controls, dialogs, toasts.
//
// The controls here all follow one convention. They report a change and nothing else; deciding
// what that change means to the document is the view's job. That keeps every edit flowing through
// a single labelled `store.edit` call, which is what makes undo work everywhere without each view
// having to think about it.

import { h, fill } from "./dom.js";

let datalistSeq = 0;

/**
 * Wrap a control with its label, unit and hint.
 *
 * `warn: true` marks the hint as something gone wrong rather than something to know, which is the
 * difference between explaining a field and reporting a problem with what is in it.
 */
export function field(label, control, { unit, hint, warn } = {}) {
  return h(
    "label.field",
    h("span.field__label", label, unit ? h("span.field__unit", unit) : null),
    control,
    hint ? h("span.field__hint", { style: warn ? { color: "var(--alarm)" } : null }, hint) : null,
  );
}

export function textInput({ value, onChange, placeholder, mono, invalid, ...rest }) {
  return h("input.input", {
    type: "text",
    value: value ?? "",
    placeholder: placeholder || "",
    class: [mono ? "mono" : "", invalid ? "input--bad" : ""].filter(Boolean).join(" "),
    onchange: (event) => onChange(event.target.value),
    ...rest,
  });
}

export function numberInput({ value, onChange, step, placeholder, allowBlank = true }) {
  return h("input.input.input--num", {
    type: "number",
    step: step ?? "any",
    value: value ?? "",
    placeholder: placeholder || "",
    onchange: (event) => {
      const raw = event.target.value;
      if (raw === "") return onChange(allowBlank ? null : 0);
      const parsed = Number(raw);
      onChange(Number.isFinite(parsed) ? parsed : null);
    },
  });
}

export function selectInput({ value, options, onChange, blank }) {
  const select = h("select.input");
  if (blank) select.appendChild(h("option", { value: "" }, blank));
  for (const option of options) {
    const [optionValue, optionLabel] = Array.isArray(option) ? option : [option, option];
    select.appendChild(h("option", { value: optionValue, selected: optionValue === value }, optionLabel));
  }
  select.value = value ?? "";
  select.addEventListener("change", (event) => onChange(event.target.value || null));
  return select;
}

/**
 * A text box with suggestions. Used wherever the catalog knows the likely answers but the input
 * format allows anything, such as a material that lives in a namespace ARMI cannot see from here.
 */
export function comboInput({ value, options, onChange, placeholder }) {
  const id = `dl-${(datalistSeq += 1)}`;
  const list = h("datalist", { id }, ...options.map((option) => h("option", { value: option })));
  const input = h("input.input.mono", {
    type: "text",
    value: value ?? "",
    placeholder: placeholder || "",
    list: id,
    onchange: (event) => onChange(event.target.value || null),
  });
  return h("div", { style: { display: "contents" } }, input, list);
}

export function checkbox({ label, checked, onChange }) {
  return h(
    "label.inline-check",
    h("input", { type: "checkbox", checked: !!checked, onchange: (event) => onChange(event.target.checked) }),
    h("span", label),
  );
}

/**
 * An editable list of short strings, such as lattice IDs or expansion targets.
 *
 * The add control is a button rather than an always-open text box: in a table of eighty nuclides,
 * eighty placeholder inputs read as eighty values that are not there.
 */
export function tagList({ values, onChange, placeholder = "add", mono = true }) {
  const list = values || [];
  const row = h("div.chipbar");
  list.forEach((value, index) => {
    row.appendChild(
      h(
        "span.chip",
        { class: mono ? "mono" : "" },
        value,
        h(
          "button.btn.btn--quiet.btn--sm",
          {
            type: "button",
            title: `Remove ${value}`,
            style: { padding: "0 2px", lineHeight: 1 },
            onclick: () => onChange(list.filter((_, i) => i !== index)),
          },
          "\u00d7",
        ),
      ),
    );
  });

  const add = h("button.chip", { type: "button", title: `Add ${placeholder}`,
    style: { padding: "2px 9px" } }, "+");
  add.onclick = () => {
    const input = h("input.input.mono", {
      type: "text",
      placeholder,
      style: { width: "92px", padding: "2px 7px" },
      onkeydown: (event) => {
        if (event.key === "Escape") input.replaceWith(add);
        if (event.key !== "Enter" || !event.target.value.trim()) return;
        event.preventDefault();
        onChange([...list, event.target.value.trim()]);
      },
      onblur: () => {
        if (input.value.trim()) onChange([...list, input.value.trim()]);
        else input.replaceWith(add);
      },
    });
    add.replaceWith(input);
    input.focus();
  };
  row.appendChild(add);
  return row;
}

export function group(title, ...children) {
  const head = h("h3.group__title", title);
  return h("section.group", head, ...children.flat());
}

export function groupWith(title, action, ...children) {
  return h("section.group", h("h3.group__title", title, action), ...children.flat());
}

export function empty(headline, detail) {
  return h("div.empty", h("strong", headline), detail);
}

// -- toasts ---------------------------------------------------------------------------------------

export function toast(message, { kind = "info", detail = null, ms = 4200 } = {}) {
  const node = h(
    "div.toast",
    { dataset: { kind } },
    h("div", message),
    detail ? h("div.toast__detail", detail) : null,
  );
  document.getElementById("toasts").appendChild(node);
  setTimeout(() => node.remove(), ms);
  return node;
}

// -- dialogs --------------------------------------------------------------------------------------

/**
 * Show a modal.
 *
 * @param {object} spec
 * @param {string} spec.title
 * @param {Node|Node[]} spec.body
 * @param {Array} spec.actions `[label, kind, handler]`; the handler may return a value to resolve
 * @returns {Promise} what the chosen action returned, or null if dismissed
 */
export function dialog({ title, body, actions = [], wide = false }) {
  const overlay = document.getElementById("overlay");
  return new Promise((resolve) => {
    const close = (value) => {
      overlay.hidden = true;
      overlay.replaceChildren();
      document.removeEventListener("keydown", onKey);
      resolve(value);
    };
    const onKey = (event) => {
      if (event.key === "Escape") close(null);
    };

    const foot = h("div.dialog__foot");
    for (const [label, kind, handler] of actions) {
      foot.appendChild(
        h(
          `button.btn${kind === "primary" ? ".btn--primary" : kind === "danger" ? ".btn--danger" : ""}`,
          { type: "button", onclick: async () => close(handler ? await handler() : label) },
          label,
        ),
      );
    }
    foot.appendChild(h("button.btn.btn--quiet", { type: "button", onclick: () => close(null) }, "Cancel"));

    const panel = h(
      "div.dialog",
      { style: wide ? { width: "min(920px, 100%)" } : null },
      h("div.dialog__head", h("span.dialog__title", title)),
      h("div.dialog__body", body),
      foot,
    );
    fill(overlay, panel);
    overlay.hidden = false;
    overlay.onclick = (event) => {
      if (event.target === overlay) close(null);
    };
    document.addEventListener("keydown", onKey);
    panel.querySelector("input, select, button")?.focus();
  });
}

export async function confirm(title, message, confirmLabel = "Delete") {
  const answer = await dialog({
    title,
    body: h("p", { style: { margin: 0, color: "var(--text-dim)" } }, message),
    actions: [[confirmLabel, "danger", () => true]],
  });
  return answer === true;
}

/** Ask for a name, refusing ones already taken. */
export async function promptName(title, { value = "", taken = [], label = "Name" } = {}) {
  const input = h("input.input.mono", { type: "text", value });
  const error = h("span.field__hint", { style: { color: "var(--alarm)" } });
  const answer = await dialog({
    title,
    body: h("div", field(label, input), error),
    actions: [
      [
        "Create",
        "primary",
        () => {
          const name = input.value.trim();
          if (!name || (taken.includes(name) && name !== value)) return null;
          return name;
        },
      ],
    ],
  });
  return answer || null;
}
