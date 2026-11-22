/**
 * 应用外壳 — 顶栏 / 导航 / 内容出口 / 页脚。
 * 只负责"骨架 + 全局状态展示"，不关心当前是哪一页。
 */

import { h } from "../core/dom.js";
import { applyTheme, effectiveTheme, getTheme, onSystemThemeChange } from "../core/theme.js";

const MODE_TEXT = {
  viewer: { label: "只读", cls: "pill" },
  operator: { label: "可写", cls: "pill pill-ok" },
};

export function createShell({ navItems, version = "" }) {
  const outlet = h("main", { class: "main", id: "outlet" });
  const links = new Map();

  const nav = h("nav", { class: "nav", "aria-label": "主导航" });
  for (const item of navItems) {
    const link = h(
      "a",
      { class: "nav-link", href: `#${item.path}`, dataset: { path: item.path } },
      item.label,
    );
    links.set(item.path, link);
    nav.append(link);
  }

  const modePill = h("span", { class: "pill" }, "连接中");
  const themeBtn = h("button", {
    class: "btn btn-ghost btn-sm",
    type: "button",
    title: "切换深浅色",
    "aria-label": "切换深浅色",
  });
  const paintThemeBtn = () => {
    const dark = effectiveTheme() === "dark";
    themeBtn.textContent = dark ? "☀ 浅色" : "☾ 深色";
    themeBtn.title = dark ? "切换到浅色" : "切换到深色";
  };
  themeBtn.addEventListener("click", () => {
    const dark = effectiveTheme() === "dark";
    applyTheme(dark ? "light" : "dark");
    paintThemeBtn();
  });
  onSystemThemeChange(() => paintThemeBtn());
  paintThemeBtn();

  const topbar = h(
    "header",
    { class: "topbar" },
    h("a", { class: "brand", href: "#/" },
      h("span", { class: "brand-mark" }, "M"),
      h("span", null, "Manwa 控制台")),
    nav,
    h("div", { class: "topbar-right" }, modePill, themeBtn),
  );

  const footerLeft = h("span", null, version ? `Manwa Web Console v${version}` : "Manwa Web Console");
  const footerRight = h("span", { class: "mono" }, "");

  const footer = h("footer", { class: "footer" }, footerLeft, footerRight);

  const fragment = document.createDocumentFragment();
  fragment.append(topbar, outlet, footer);

  return {
    fragment,
    outlet,

    /** 导航高亮：由路由在每次渲染后调用 */
    setActive(path) {
      for (const [p, link] of links) {
        if (p === path) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
      }
    },

    setSession(meta) {
      const mode = MODE_TEXT[meta?.mode] || MODE_TEXT.viewer;
      modePill.textContent = mode.label;
      modePill.className = mode.cls;
      footerLeft.textContent = `Manwa Web Console v${meta?.version || version || "?"}`;
      footerRight.textContent = meta?.paths?.registry ? `数据：${meta.paths.registry}` : "";
    },
  };
}
