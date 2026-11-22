/**
 * 设置页 — 主题、运行模式、能力清单、数据文件位置。
 * 这里演示的是"全局状态改变后如何立刻生效"：主题写 localStorage + 改 data-theme，
 * 不刷新页面。
 */

import { api } from "../core/api.js";
import { h } from "../core/dom.js";
import { notify } from "../components/toast.js";
import { THEMES, applyTheme, getTheme } from "../core/theme.js";

export const title = "设置";
export const skeleton = "list";

const THEME_LABEL = { light: "浅色", dark: "深色", auto: "跟随系统" };

export async function load() {
  return await api.meta();
}

function row(title, desc, control) {
  return h(
    "div",
    { class: "setting-row" },
    h("div", { class: "setting-text" },
      h("div", { class: "setting-title" }, title),
      desc ? h("div", { class: "setting-desc" }, desc) : null),
    control,
  );
}

export function render(meta, ctx) {
  let theme = getTheme();

  const buttons = THEMES.map((name) =>
    h("button", {
      type: "button",
      "aria-pressed": String(name === theme),
      onClick: (event) => {
        theme = applyTheme(name);
        for (const btn of event.currentTarget.parentElement.children) {
          btn.setAttribute("aria-pressed", String(btn.dataset.theme === theme));
        }
        notify(`主题：${THEME_LABEL[theme]}`, { type: "ok", timeout: 1600 });
      },
      dataset: { theme: name },
    }, THEME_LABEL[name]),
  );

  const caps = (meta.capabilities || []).map((cap) =>
    h("code", { style: { marginRight: "6px" } }, cap));

  return h(
    "div",
    { class: "stack" },
    h("div", { class: "page-head" },
      h("div", null,
        h("h1", null, "设置"),
        h("p", { class: "page-desc" }, "外观与本机控制台状态")),
      h("div", { class: "page-actions" },
        h("a", { class: "btn btn-ghost", href: "#/" }, "返回总览"))),

    h("section", { class: "card" },
      row("主题", "浅色 / 深色 / 跟随系统，仅影响这个浏览器", h("div", { class: "segmented", role: "group", "aria-label": "主题" }, ...buttons)),
      row("运行模式",
        meta.mode === "operator" ? "可写：允许登记与删除" : "只读：写操作已被服务端拒绝",
        h("span", { class: `pill ${meta.mode === "operator" ? "pill-ok" : ""}` },
          meta.mode === "operator" ? "可写" : "只读")),
      row("能力清单", "前端据此显示或隐藏操作，真正的拦截在后端", h("div", null, ...caps)),
      row("数据文件", meta.paths?.registry || "—",
        h("button", {
          class: "btn btn-sm",
          type: "button",
          onClick: async () => {
            try {
              await navigator.clipboard.writeText(meta.paths?.registry || "");
              notify("路径已复制", { type: "ok", timeout: 1600 });
            } catch {
              notify("复制失败，请手动选择路径", { type: "warn" });
            }
          },
        }, "复制路径")),
      row("版本", "Manwa Downloader 控制台", h("span", { class: "mono" }, `v${meta.version || "?"}`)),
    ),

    h("div", { class: "note" },
      h("span", null,
        "提示：表格数据与终端共用同一个 .comics.json，两边改动互相可见；" +
        "开着本页时在终端下载，回到表格页刷新即可看到新记录。")),
  );
}
