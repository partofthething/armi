// Tiny DOM helpers. Not a framework: the views build elements and replace subtrees, which is
// plenty for an editor whose heaviest view is a few hundred SVG cells.

/**
 * Build an element.
 * @param {string} spec tag with optional classes, e.g. "button.btn.btn--primary"
 * @param {object} [props] attributes; `class`, `style` objects, `on*` handlers and `dataset`
 * @param {...*} children strings, nodes, arrays, or null
 */
export function h(spec, props, ...children) {
  const [tag, ...classes] = spec.split(".");
  const el = document.createElement(tag || "div");
  if (classes.length) el.className = classes.join(" ");

  if (props && (props.nodeType || Array.isArray(props) || typeof props === "string")) {
    children.unshift(props);
    props = null;
  }
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = [el.className, value].filter(Boolean).join(" ");
    else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key in el && key !== "list" && typeof value !== "boolean") el[key] = value;
    else el.setAttribute(key, value === true ? "" : value);
  }
  append(el, children);
  return el;
}

/** Same as {@link h} but in the SVG namespace. */
export function s(spec, props, ...children) {
  const [tag, ...classes] = spec.split(".");
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  if (classes.length) el.setAttribute("class", classes.join(" "));
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "dataset") Object.assign(el.dataset, value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(4)) {
    if (child == null || child === false) continue;
    el.appendChild(child.nodeType ? child : document.createTextNode(String(child)));
  }
}

/** Empty a node and put these children in it. */
export function fill(node, ...children) {
  node.replaceChildren();
  append(node, children);
  return node;
}

/** Format a number for display, trimming the noise off floating point. */
export function num(value, digits = 5) {
  if (value == null || value === "") return "";
  const asNumber = Number(value);
  if (!Number.isFinite(asNumber)) return String(value);
  const fixed = asNumber.toFixed(digits);
  return fixed.includes(".") ? fixed.replace(/\.?0+$/, "") : fixed;
}

/** A stable name for a thing that does not have one yet. */
export function uniqueName(base, taken) {
  if (!taken.includes(base)) return base;
  for (let i = 2; ; i += 1) {
    const candidate = `${base} ${i}`;
    if (!taken.includes(candidate)) return candidate;
  }
}

/** Debounce, for typing into a field that triggers a server round trip. */
export function debounce(fn, ms = 220) {
  let timer = null;
  const wrapped = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
  wrapped.cancel = () => clearTimeout(timer);
  wrapped.flush = (...args) => {
    clearTimeout(timer);
    fn(...args);
  };
  return wrapped;
}
