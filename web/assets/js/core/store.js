/**
 * 极简全局状态 — 一个可变对象 + 订阅者。
 *
 * 刻意不做 proxy / 依赖追踪：这个控制台的状态很小（会话、计数、筛选条件），
 * 手写订阅比引入状态库更容易看懂，也少一层调试成本。
 */

export function createStore(initial) {
  let state = Object.freeze({ ...initial });
  const listeners = new Set();

  return {
    get state() {
      return state;
    },

    get(key) {
      return state[key];
    },

    /** set({a:1}) 或 set(prev => ({a: prev.a + 1})) */
    set(patch) {
      const next = typeof patch === "function" ? patch(state) : patch;
      state = Object.freeze({ ...state, ...next });
      for (const fn of [...listeners]) fn(state);
    },

    /** 订阅整体变化，返回取消订阅函数 */
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },

    /** 只关心某几个 key 时用这个，避免无关变化触发重绘 */
    watch(keys, fn) {
      const tracked = [].concat(keys);
      let last = tracked.map((k) => state[k]);
      return this.subscribe((s) => {
        const now = tracked.map((k) => s[k]);
        if (now.some((v, i) => v !== last[i])) {
          last = now;
          fn(s);
        }
      });
    },
  };
}
