/**
 * 404 — 兜底路由。hash 路由下用户可能手动敲错地址，这里给一条回主路。
 */

import { h } from "../core/dom.js";
import { emptyState } from "../components/states.js";

export const title = "页面不存在";

export function render(_data, ctx) {
  return h(
    "div",
    { class: "card card-body", style: { marginTop: "var(--sp-6)" } },
    emptyState({
      icon: "404",
      title: "这个页面不存在",
      desc: `没有匹配的路由：${ctx.router.path}`,
      action: h("a", { class: "btn btn-primary", href: "#/" }, "回总览"),
    }),
  );
}
