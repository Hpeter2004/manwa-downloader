/**
 * 总览 — 一眼看清"有多少本、能不能改、数据在哪"。
 */

import { api } from "../core/api.js";
import { h } from "../core/dom.js";
import { emptyState } from "../components/states.js";

export const title = "总览";
export const skeleton = "stats";

export async function load() {
  const [meta, comics] = await Promise.all([api.meta(), api.listComics({ limit: 5 })]);
  return { meta, items: comics.items, total: comics.total };
}

function statCard(label, value, hint, mono = false) {
  return h(
    "div",
    { class: "card stat" },
    h("div", { class: "stat-label" }, label),
    h("div", {
      class: "stat-value",
      style: { fontSize: mono ? "var(--fs-md)" : null, wordBreak: "break-all" },
      title: typeof value === "string" ? value : "",
    }, value),
    h("div", { class: "stat-hint" }, hint),
  );
}

export function render({ meta, items }, ctx) {
  const canWrite = ctx.guard.can("comics:write");
  const recent = items.slice(0, 5);

  return h(
    "div",
    { class: "stack" },

    h(
      "section",
      { class: "dash-hero" },
      h(
        "div",
        null,
        h("h1", null, "漫画表格"),
        h("p", { class: "muted", style: { marginTop: "6px" } },
          "漫画名称与 Comic ID 一对一登记，重复标题只保留一条映射。"),
      ),
      h(
        "div",
        { class: "row" },
        h("a", { class: "btn btn-primary", href: "#/comics" }, "打开表格"),
        h("button", {
          class: "btn",
          type: "button",
          onClick: (event) => {
            const btn = event.currentTarget;
            btn.classList.add("is-busy");
            ctx.router.refresh();
          },
        }, "刷新"),
      ),
    ),

    h(
      "div",
      { class: "grid" },
      statCard("已登记漫画", String(meta.counts?.comics ?? items.length), "条记录"),
      statCard(
        "运行模式",
        meta.mode === "operator" ? "可写" : "只读",
        canWrite ? "可登记 / 删除" : "用 --serve --allow-write 开启写操作",
      ),
      statCard("数据文件", meta.paths?.registry || "—", "JSON，可直接编辑", true),
    ),

    h(
      "section",
      { class: "card" },
      h(
        "div",
        { class: "card-head" },
        h("span", { class: "card-title" }, "最近使用"),
        h("a", { class: "btn btn-sm btn-ghost", href: "#/comics" }, "查看全部"),
      ),
      recent.length
        ? h("div", { class: "dash-list" },
            ...recent.map((item) =>
              h(
                "div",
                { class: "dash-item" },
                h(
                  "div",
                  { class: "dash-item-main" },
                  h("span", { class: "cid" }, item.cid),
                  h("span", { class: "cell-name" }, item.name),
                ),
                h("span", { class: "muted nowrap" },
                  item.chapters ? `${item.chapters} 章` : "—"),
              ),
            ),
          )
        : emptyState({
            icon: "＋",
            title: "还没有记录",
            desc: "下载任意一本漫画，或者在表格页手动登记一条。",
          }),
    ),
  );
}
