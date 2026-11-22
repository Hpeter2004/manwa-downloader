/**
 * 请求封装 — 全站唯一的 fetch 出口。
 *
 * 只做四件事：拼 URL、统一解析 {ok,data,error} 信封、超时、把网络错误收敛成
 * ApiError。业务代码永远不用自己判断 res.ok，也不用自己写 try/catch 里的状态码。
 */

/** 后端统一信封里的错误；页面只认 code 和 message。 */
export class ApiError extends Error {
  constructor(message, { status = 0, code = "unknown" } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.isApiError = true;
  }
}

/** 写操作必须带这个头，后端靠它挡跨站请求（CSRF）。 */
const WRITE_HEADER = { "X-Requested-With": "manwa-web" };
const DEFAULT_TIMEOUT = 12000;

const startHooks = new Set();
const endHooks = new Set();

export function onRequestStart(fn) {
  startHooks.add(fn);
  return () => startHooks.delete(fn);
}
export function onRequestEnd(fn) {
  endHooks.add(fn);
  return () => endHooks.delete(fn);
}

async function request(path, { method = "GET", body, timeout = DEFAULT_TIMEOUT } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const headers = { Accept: "application/json" };
  const init = { method, headers, signal: controller.signal, credentials: "same-origin" };

  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  if (method !== "GET") Object.assign(headers, WRITE_HEADER);

  for (const fn of startHooks) fn(method, path);

  try {
    const res = await fetch(path, init);

    let payload = null;
    try {
      payload = await res.json();
    } catch {
      // 后端没返回 JSON（比如 502 的 HTML 网关页）
    }
    if (!payload) {
      throw new ApiError(`响应不是合法 JSON（HTTP ${res.status}）`, {
        status: res.status,
        code: "bad_response",
      });
    }
    if (!res.ok || payload.ok === false) {
      const err = payload.error || {};
      throw new ApiError(err.message || `请求失败（HTTP ${res.status}）`, {
        status: res.status,
        code: err.code || "http_error",
      });
    }
    return payload.data;
  } catch (err) {
    if (err.name === "AbortError") {
      throw new ApiError("请求超时，请确认控制台仍在运行", { code: "timeout" });
    }
    if (err.isApiError) throw err;
    throw new ApiError(`网络错误：${err.message}`, { code: "network" });
  } finally {
    clearTimeout(timer);
    for (const fn of endHooks) fn(method, path);
  }
}

export const api = {
  meta: () => request("/api/meta"),
  health: () => request("/api/health"),
  listComics: () => request("/api/comics"),
  addComic: (cid, name) => request("/api/comics", { method: "POST", body: { cid, name } }),
  removeComic: (cid) => request(`/api/comics/${encodeURIComponent(cid)}`, { method: "DELETE" }),
};
