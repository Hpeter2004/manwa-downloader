/**
 * 权限 — 本地工具没有账号体系，"权限"落在**能力开关**上：
 * 后端以 --allow-write 启动时 /api/meta 会下发 comics:write，前端据此
 * 显示或隐藏写操作。真正的拦截在后端（readonly / csrf 两个错误码），
 * 前端这层只负责不给出点不动的按钮。
 */

export const CAP = {
  READ: "comics:read",
  WRITE: "comics:write",
};

export function createGuard(meta) {
  const granted = new Set(meta?.capabilities || []);
  const mode = meta?.mode || "viewer";

  return {
    mode,
    writable: granted.has(CAP.WRITE),
    capabilities: [...granted],

    can(capability) {
      return granted.has(capability);
    },

    /** 路由守卫用：缺能力时抛出带 code 的错误，交给页面统一渲染。 */
    assert(capability) {
      if (!granted.has(capability)) {
        const err = new Error("当前是只读模式，没有该操作权限");
        err.code = "forbidden";
        throw err;
      }
    },
  };
}
