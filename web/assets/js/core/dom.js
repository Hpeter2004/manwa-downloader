/**
 * DOM 工厂 — 用函数式写法建节点，避免到处 innerHTML 拼字符串。
 * 只有 "html" 属性会走 innerHTML，且仅用于受信任的静态片段。
 */

const SKIP = new Set([null, undefined, false, true, ""]);

function append(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (SKIP.has(child)) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

/**
 * h("div", { class: "card" }, "文本", h("span", null, "子节点"))
 * 事件用 on + 事件名：{ onClick: fn } / { onSubmit: fn }
 */
export function h(tag, props = null, ...children) {
  const el = document.createElement(tag);

  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value === null || value === undefined || value === false) continue;

      if (key === "class" || key === "className") {
        el.className = value;
      } else if (key === "style" && typeof value === "object") {
        Object.assign(el.style, value);
      } else if (key === "dataset") {
        Object.assign(el.dataset, value);
      } else if (key === "html") {
        el.innerHTML = value;
      } else if (key.startsWith("on") && typeof value === "function") {
        el.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (key in el) {
        el[key] = value;
      } else {
        el.setAttribute(key, value === true ? "" : value);
      }
    }
  }

  append(el, children);
  return el;
}

export function frag(...children) {
  const f = document.createDocumentFragment();
  append(f, children);
  return f;
}

/** 用新内容整体替换容器内容（不销毁容器本身，路由和订阅都靠它保持稳定）。 */
export function replace(node, ...children) {
  node.replaceChildren();
  append(node, children);
  return node;
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

export function qs(selector, root = document) {
  return root.querySelector(selector);
}
