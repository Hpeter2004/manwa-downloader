/**
 * Toast — 轻量操作反馈。挂在 #toast-root，不阻塞交互。
 * 结构简单，没有队列上限之外的策略：最多同时 4 条，超出丢最旧的。
 */

import { h } from "../core/dom.js";

const MAX_TOASTS = 4;
const TYPES = { info: "", ok: "is-ok", warn: "is-warn", error: "is-error" };

function root() {
  let node = document.getElementById("toast-root");
  if (!node) {
    node = h("div", { id: "toast-root", class: "toast-root", "aria-live": "polite" });
    document.body.append(node);
  }
  return node;
}

function dismiss(el) {
  if (!el.isConnected || el.classList.contains("is-leaving")) return;
  el.classList.add("is-leaving");
  el.addEventListener("animationend", () => el.remove(), { once: true });
  // 动画被 prefers-reduced-motion 关掉时兜底
  setTimeout(() => el.remove(), 260);
}

/**
 * notify("已保存", { type: "ok" })
 * type: info | ok | warn | error
 */
export function notify(message, { type = "info", timeout = 3200 } = {}) {
  const parent = root();
  while (parent.children.length >= MAX_TOASTS) parent.firstElementChild?.remove();

  const el = h(
    "div",
    {
      class: `toast ${TYPES[type] ?? ""}`.trim(),
      role: type === "error" ? "alert" : "status",
      onClick: () => dismiss(el),
    },
    message,
  );

  parent.append(el);
  if (timeout > 0) setTimeout(() => dismiss(el), timeout);
  return () => dismiss(el);
}
