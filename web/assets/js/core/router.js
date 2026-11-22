/**
 * 路由 — hash 路由（#/comics）。
 *
 * 用 hash 而不是 History API：控制台是单文件静态服务，刷新 /comics 会 404，
 * 需要后端补 rewrite 才能用。hash 路由零配置，离线双击 index.html 也能跑。
 *
 * 页面契约（pages/*.js 导出的东西）：
 *   title     页面标题
 *   skeleton  骨架类型（stats | table | list）
 *   requires  需要的权限能力，缺了就渲染错误态
 *   load(ctx) 取数据，抛错由路由统一兜住
 *   render(data, ctx) 返回 DOM 节点
 */

import { replace } from "./dom.js";
import { errorState, skeletonPage } from "../components/states.js";

export function createRouter({ routes, outlet, ctx, onNavigate }) {
  let seq = 0;

  function currentPath() {
    const raw = location.hash.replace(/^#/, "");
    return raw === "" ? "/" : raw;
  }

  function match(path) {
    return routes.find((r) => r.path === path)
      || routes.find((r) => r.path === "*")
      || null;
  }

  async function render() {
    const path = currentPath();
    const route = match(path);
    const token = ++seq;                        // 竞态令牌

    window.scrollTo(0, 0);

    if (!route) {
      replace(outlet, errorState(new Error(`没有匹配的路由：${path}`), {}));
      return;
    }

    document.title = route.title ? `${route.title} · Manwa 控制台` : "Manwa 控制台";
    onNavigate?.({ path, route });

    if (route.requires && !ctx.guard.can(route.requires)) {
      replace(outlet, errorState(
        Object.assign(new Error("当前是只读模式，这个页面需要写权限。用 --serve --allow-write 重新启动。"),
          { code: "forbidden" }),
        { title: "没有访问权限" },
      ));
      return;
    }

    replace(outlet, skeletonPage(route.skeleton || "list"));

    try {
      const data = route.load ? await route.load(ctx) : null;
      if (token !== seq) return;                // 用户已经切到别的页面了
      replace(outlet, route.render(data, ctx));
    } catch (err) {
      if (token !== seq) return;
      console.error("[manwa] route failed:", path, err);
      replace(outlet, errorState(err, { onRetry: render }));
    }
  }

  window.addEventListener("hashchange", render);

  return {
    start() {
      if (!location.hash) location.replace("#/");
      render();
    },
    /** 写操作成功后重新拉数据（不改变当前路由） */
    refresh: render,
    navigate(path) {
      if (currentPath() === path) render();
      else location.hash = path;
    },
    get path() {
      return currentPath();
    },
  };
}
