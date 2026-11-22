/**
 * 全局反馈 — 顶部加载条 + 统一的错误出口。
 *
 * 加载条订阅的是 api 层的请求钩子，所以任何页面、任何按钮发起的请求都会自动
 * 有进度反馈，不需要每个调用点自己去 toggle。
 */

import { notify } from "../components/toast.js";
import { onRequestEnd, onRequestStart } from "./api.js";

let pending = 0;
let hideTimer = null;

function bar() {
  return document.getElementById("boot-bar");
}

export function beginLoading() {
  pending += 1;
  const el = bar();
  if (!el) return;
  clearTimeout(hideTimer);
  el.classList.remove("is-done");
  el.classList.add("is-active");
}

export function endLoading() {
  pending = Math.max(0, pending - 1);
  if (pending > 0) return;
  const el = bar();
  if (!el) return;
  el.classList.remove("is-active");
  el.classList.add("is-done");
  hideTimer = setTimeout(() => el.classList.remove("is-done"), 320);
}

/** 把加载条接到请求生命周期上（main 里调一次） */
export function installLoadingIndicator() {
  onRequestStart(beginLoading);
  onRequestEnd(endLoading);
}

/** 统一错误出口：日志 + toast。页面内联展示错误时用自己的 errorState。 */
export function reportError(err, fallback = "操作失败") {
  console.error("[manwa]", err);
  notify(err?.message || fallback, { type: "error", timeout: 5000 });
}

/** 兜住没人 catch 的异常，避免用户只看到按钮没反应 */
export function installGlobalErrorHandlers() {
  window.addEventListener("error", (event) => {
    if (event?.error?.isApiError) return; // 页面已经展示过
    notify("页面出现异常，已记录到控制台", { type: "error", timeout: 5000 });
  });
  window.addEventListener("unhandledrejection", (event) => {
    if (event?.reason?.isApiError) {
      event.preventDefault();
      reportError(event.reason);
    }
  });
}
