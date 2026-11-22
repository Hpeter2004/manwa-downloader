/**
 * 状态组件 — 加载 / 空 / 错误三态。
 * 每个页面都复用这三件套，保证"没数据"和"出错了"长一个样。
 */

import { h } from "../core/dom.js";

/** 首屏骨架：形状跟着真实内容走，别用一个大灰块糊住整页 */
export function skeletonPage(kind = "list") {
  if (kind === "table") {
    const rows = Array.from({ length: 5 }, () =>
      h("div", { class: "skeleton skeleton-line", style: { height: "16px" } }),
    );
    return h(
      "div",
      { class: "card card-body stack", "aria-busy": "true", "aria-label": "加载中" },
      h("div", { class: "skeleton", style: { height: "18px", width: "180px" } }),
      ...rows,
    );
  }

  if (kind === "stats") {
    return h(
      "div",
      { class: "grid", "aria-busy": "true", "aria-label": "加载中" },
      ...Array.from({ length: 3 }, () =>
        h("div", { class: "card" },
          h("div", { class: "card-body" },
            h("div", { class: "skeleton", style: { height: "14px", width: "60%" } }),
            h("div", { class: "skeleton", style: { height: "28px", width: "40%", marginTop: "12px" } }),
          )),
      ),
    );
  }

  return h(
    "div",
    { class: "card card-body stack", "aria-busy": "true", "aria-label": "加载中" },
    h("div", { class: "skeleton", style: { height: "20px", width: "40%" } }),
    h("div", { class: "skeleton", style: { height: "14px", width: "70%" } }),
    h("div", { class: "skeleton", style: { height: "14px", width: "55%" } }),
  );
}

export function emptyState({ icon = "∅", title = "暂无内容", desc = "", action = null } = {}) {
  return h(
    "div",
    { class: "state" },
    h("div", { class: "state-icon" }, icon),
    h("div", { class: "state-title" }, title),
    desc ? h("div", { class: "state-desc" }, desc) : null,
    action,
  );
}

/**
 * 错误态 — 展示可读信息而不是堆栈，堆栈留给 console。
 * err.code 来自后端信封，方便定位是只读、CSRF 还是真崩了。
 */
export function errorState(err, { title = "加载失败", onRetry = null } = {}) {
  const retryBtn = onRetry
    ? h("button", { class: "btn btn-primary", type: "button", onClick: onRetry }, "重试")
    : null;

  return h(
    "div",
    { class: "state state-error", role: "alert" },
    h("div", { class: "state-icon" }, "!"),
    h("div", { class: "state-title" }, title),
    h("div", { class: "state-desc" }, err?.message || "未知错误"),
    err?.code ? h("div", { class: "state-code" }, `错误码：${err.code}`) : null,
    retryBtn,
  );
}
