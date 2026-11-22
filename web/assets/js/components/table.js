/**
 * 漫画表格 — 列表渲染 + 行内删除。
 *
 * 删除按钮做两段式确认（先"删除"再"确认"），不弹 confirm：
 * 浏览器原生 confirm 在 Electron / 打包环境里体验差，而且没法做竞态控制。
 */

import { h } from "../core/dom.js";
import { emptyState } from "./states.js";

const DISARM_MS = 3000;

function fmtDate(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  if (Number.isNaN(d.getTime())) return "—";
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function deleteButton(entry, onDelete) {
  const btn = h(
    "button",
    {
      class: "btn btn-sm btn-danger",
      type: "button",
      title: `从表格移除 ${entry.name}`,
    },
    "删除",
  );
  let armed = false;
  let timer = null;

  const reset = () => {
    armed = false;
    btn.textContent = "删除";
    btn.classList.remove("is-busy");
    clearTimeout(timer);
  };

  btn.addEventListener("click", async () => {
    if (!armed) {
      armed = true;
      btn.textContent = "确认?";
      btn.classList.add("is-busy");
      timer = setTimeout(() => {
        armed = false;
        btn.textContent = "删除";
        btn.classList.remove("is-busy");
      }, DISARM_MS);
      return;
    }
    clearTimeout(timer);
    btn.textContent = "删除中";
    btn.classList.add("is-busy");
    try {
      await onDelete(entry);
    } finally {
      reset();
    }
  });

  return btn;
}

export function comicTable(items, { canWrite = false, onDelete } = {}) {
  if (!items.length) {
    return emptyState({
      icon: "＋",
      title: "表格是空的",
      desc: canWrite
        ? "用上面的输入框登记一本漫画，或者在终端里跑一次下载，这里会自动出现。"
        : "在终端里下载任意一本漫画后，这里会自动出现记录。",
    });
  }

  const head = h(
    "tr",
    null,
    h("th", { class: "num", scope: "col" }, "#"),
    h("th", { scope: "col" }, "Comic ID"),
    h("th", { scope: "col" }, "漫画名称"),
    h("th", { class: "num", scope: "col" }, "章节"),
    h("th", { scope: "col" }, "最后使用"),
    canWrite ? h("th", { class: "col-actions", scope: "col" }, "") : null,
  );

  const rows = items.map((item, index) =>
    h(
      "tr",
      null,
      h("td", { class: "num dim" }, String(index + 1)),
      h("td", null, h("span", { class: "cid" }, item.cid)),
      h("td", null, h("div", { class: "cell-name", title: item.name }, item.name)),
      h("td", { class: "num" }, item.chapters ? String(item.chapters) : "—"),
      h("td", { class: "muted nowrap" }, fmtDate(item.updated)),
      canWrite ? h("td", { class: "col-actions" }, deleteButton(item, onDelete)) : null,
    ),
  );

  return h(
    "div",
    { class: "table-wrap" },
    h(
      "table",
      { class: "table" },
      h("thead", null, head),
      h("tbody", null, ...rows),
    ),
  );
}
