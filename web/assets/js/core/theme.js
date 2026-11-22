/**
 * 主题 — light / dark / auto 三态，落在 <html data-theme>。
 *
 * 首屏那段内联脚本用的是同一套 key 与判定逻辑，改这里要同步改 index.html。
 */

const KEY = "manwa.theme";
export const THEMES = ["light", "dark", "auto"];

const query = window.matchMedia("(prefers-color-scheme: dark)");

export function getTheme() {
  try {
    const saved = localStorage.getItem(KEY);
    return THEMES.includes(saved) ? saved : "auto";
  } catch {
    return "auto";
  }
}

/** auto 时跟随系统，返回真正生效的亮/暗 */
export function effectiveTheme(theme = getTheme()) {
  if (theme === "auto") return query.matches ? "dark" : "light";
  return theme;
}

export function applyTheme(theme) {
  const next = THEMES.includes(theme) ? theme : "auto";
  document.documentElement.dataset.theme = effectiveTheme(next);
  try {
    localStorage.setItem(KEY, next);
  } catch {
    // 隐私模式写不进去，本次会话仍然生效
  }
  return next;
}

/** 系统主题变化时，只有 auto 模式需要跟着变 */
export function onSystemThemeChange(handler) {
  const listener = () => {
    if (getTheme() === "auto") handler(effectiveTheme("auto"));
  };
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}
