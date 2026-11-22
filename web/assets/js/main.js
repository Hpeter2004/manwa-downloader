/**
 * 应用入口 — 只做装配，不写业务。
 *
 * 顺序很重要：装外壳 → 拉 /api/meta（决定权限）→ 建路由 → 起路由。
 * 路由要在拿到 meta 之后才开始跑，否则守卫拿不到能力清单。
 */

import { api } from "./core/api.js";
import { replace } from "./core/dom.js";
import { createGuard } from "./core/guard.js";
import { createRouter } from "./core/router.js";
import { createStore } from "./core/store.js";
import {
  installGlobalErrorHandlers, installLoadingIndicator, reportError,
} from "./core/feedback.js";
import { applyTheme, getTheme, onSystemThemeChange } from "./core/theme.js";
import { createShell } from "./components/shell.js";
import { errorState, skeletonPage } from "./components/states.js";
import { notify } from "./components/toast.js";

import * as dashboard from "./pages/dashboard.js";
import * as comics from "./pages/comics.js";
import * as settings from "./pages/settings.js";
import * as notfound from "./pages/notfound.js";

const NAV = [
  { path: "/", label: "总览" },
  { path: "/comics", label: "漫画表格" },
  { path: "/settings", label: "设置" },
];

const ROUTES = [
  { path: "/", ...dashboard },
  { path: "/comics", ...comics },
  { path: "/settings", ...settings },
  { path: "*", ...notfound },
];

const store = createStore({ session: null });

// 主题要在任何渲染之前定下来，避免首屏闪一下
applyTheme(getTheme());
onSystemThemeChange((theme) => applyTheme(theme));
installLoadingIndicator();
installGlobalErrorHandlers();

const shell = createShell({ navItems: NAV });
const app = document.getElementById("app");
app.append(shell.fragment);

async function boot() {
  replace(shell.outlet, skeletonPage("list"));

  try {
    const meta = await api.meta();
    const guard = createGuard(meta);

    store.set({ session: meta });
    shell.setSession(meta);

    const ctx = { api, guard, notify, store, router: null };
    const router = createRouter({
      routes: ROUTES,
      outlet: shell.outlet,
      ctx,
      onNavigate: ({ path }) => shell.setActive(path),
    });
    ctx.router = router;

    // 页面里能拿到 router / guard / session，统一从 ctx 走，不各自 import 全局
    router.start();
  } catch (err) {
    reportError(err, "无法连接控制台");
    replace(shell.outlet, errorState(err, { title: "连接失败", onRetry: boot }));
  }
}

boot();
