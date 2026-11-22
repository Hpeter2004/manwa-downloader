/**
 * 漫画表格页 — 搜索 / 登记 / 删除。
 *
 * 目录抓全后有十几万条，所以列表**不一次性拉全**：默认取前 200 条，
 * 搜索走服务端（`/api/comics?q=`），前端只负责渲染返回的这一页。
 */

import { api } from "../core/api.js";
import { h } from "../core/dom.js";
import { notify } from "../components/toast.js";
import { reportError } from "../core/feedback.js";
import { comicTable } from "../components/table.js";

export const title = "漫画表格";
export const skeleton = "table";

const PAGE = 200;
const SEARCH_DEBOUNCE = 260;

export async function load() {
  return await api.listComics({ limit: PAGE });
}

export function render(data, ctx) {
  const canWrite = ctx.guard.can("comics:write");
  let items = data.items || [];
  let total = data.total || 0;
  let keyword = "";
  let seq = 0;

  const tableSlot = h("div");
  const metaSlot = h("div", { class: "table-meta" });

  const describe = () => {
    const shown = items.length < total ? `，显示前 ${items.length}` : "";
    return keyword ? `「${keyword}」匹配 ${total} 条${shown}` : `共 ${total} 条${shown}`;
  };

  const paint = () => {
    tableSlot.replaceChildren(
      comicTable(items, {
        canWrite,
        onDelete: async (entry) => {
          try {
            await api.removeComic(entry.cid);
            notify(`已移除「${entry.name}」`, { type: "ok" });
            ctx.router.refresh();          // 重新走 load()，避免本地状态和文件不一致
          } catch (err) {
            reportError(err);
            throw err;                      // 让按钮回到可点状态
          }
        },
      }),
    );
    metaSlot.replaceChildren(
      h("span", null, describe()),
      h("span", null, "同名不同 ID 只保留先登记的一条，其余记为别名"),
    );
  };

  const search = h("input", {
    class: "input",
    type: "search",
    placeholder: "搜索名称或 Comic ID（服务端搜索）",
    "aria-label": "搜索漫画",
  });

  let timer = null;
  search.addEventListener("input", () => {
    keyword = search.value.trim();
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const token = ++seq;
      try {
        const res = await api.listComics({ q: keyword, limit: PAGE });
        if (token !== seq) return;         // 输入太快，丢弃过期响应
        items = res.items;
        total = res.total;
        paint();
      } catch (err) {
        reportError(err);
      }
    }, SEARCH_DEBOUNCE);
  });

  const refreshBtn = h("button", {
    class: "btn",
    type: "button",
    onClick: (event) => {
      event.currentTarget.classList.add("is-busy");
      ctx.router.refresh();
    },
  }, "刷新");

  const toolbar = h(
    "div",
    { class: "toolbar" },
    search,
    h("span", { class: "toolbar-spacer" }),
    refreshBtn,
  );

  paint();

  const parts = [
    h(
      "div",
      { class: "page-head" },
      h("div", null,
        h("h1", null, "漫画表格"),
        h("p", { class: "page-desc" }, "名称与 Comic ID 一一对应，随下载与抓取更新")),
      h("div", { class: "page-actions" },
        h("a", { class: "btn btn-ghost", href: "#/" }, "返回总览")),
    ),
    toolbar,
  ];

  if (canWrite) {
    const cidInput = h("input", {
      class: "input", type: "text", inputmode: "numeric",
      placeholder: "Comic ID（数字）", "aria-label": "Comic ID", style: { width: "160px" },
    });
    const nameInput = h("input", {
      class: "input", type: "text", placeholder: "漫画名称", "aria-label": "漫画名称",
      style: { width: "220px" },
    });
    const submit = h("button", { class: "btn btn-primary", type: "submit" }, "登记");

    const form = h(
      "form",
      {
        class: "row",
        onSubmit: async (event) => {
          event.preventDefault();
          const cid = cidInput.value.trim();
          const name = nameInput.value.trim();
          if (!cid || !name) {
            notify("Comic ID 和名称都要填", { type: "warn" });
            return;
          }
          if (!/^\d+$/.test(cid)) {
            notify("Comic ID 必须是数字", { type: "warn" });
            return;
          }
          submit.classList.add("is-busy");
          submit.disabled = true;
          try {
            await api.addComic(cid, name);
            notify(`已登记「${name}」`, { type: "ok" });
            ctx.router.refresh();
          } catch (err) {
            reportError(err);
          } finally {
            submit.classList.remove("is-busy");
            submit.disabled = false;
          }
        },
      },
      cidInput,
      nameInput,
      submit,
    );

    parts.push(
      h("div", { class: "card", style: { marginBottom: "var(--sp-4)" } },
        h("div", { class: "card-body" }, form)),
    );
  } else {
    parts.push(
      h("div", { class: "note", style: { marginBottom: "var(--sp-4)" } },
        h("span", null, "只读模式：登记与删除已隐藏。用"),
        h("code", null, "--serve --allow-write"),
        h("span", null, "启动即可编辑。")),
    );
  }

  parts.push(tableSlot, metaSlot);
  return h("div", null, ...parts);
}
