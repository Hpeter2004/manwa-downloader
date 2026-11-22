"""Local web console — the comic table with a UI instead of a terminal table.

Design constraints, in order:

1. **No new dependencies.** ``aiohttp`` is already the download engine, and
   ``aiohttp.web`` ships with it, so the server costs nothing. The frontend is
   native ES modules served as static files — no npm, no bundler, no build step.
2. **The console must not be a hole in the machine.** A localhost HTTP server is
   reachable by any page in the same browser, so writes require a custom header
   plus a matching Origin (classic CSRF guard), and the default bind address is
   127.0.0.1 — never 0.0.0.0.
3. **Read-only unless asked.** ``--serve`` starts in viewer mode: the API refuses
   writes and the UI hides the affordances. ``--allow-write`` opts in.

The API answers one envelope, always, so the frontend has exactly one shape to
parse::

    {"ok": true,  "data": ...}
    {"ok": false, "error": {"code": "...", "message": "..."}}
"""

import sys
from pathlib import Path

from aiohttp import web

from . import __version__
from .comics import get_registry
from .config import logger, tprint

# Header the frontend must send on every write. A cross-site form or a plain
# <img>/<script> cannot set it, which is what makes this a CSRF guard.
WRITE_HEADER = "X-Requested-With"
WRITE_VALUE = "manwa-web"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
NAME_MAX_LEN = 120


# ============================================================
# Paths
# ============================================================

def web_root() -> Path:
    """Directory holding index.html + assets (bundled when frozen)."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "web"
    return Path(__file__).parent.parent / "web"


# ============================================================
# Response helpers
# ============================================================

def ok(data=None):
    return web.json_response({"ok": True, "data": data})


def fail(status: int, code: str, message: str):
    return web.json_response(
        {"ok": False, "error": {"code": code, "message": message}},
        status=status,
    )


def _caps(writable: bool) -> list:
    """Capabilities the UI may rely on. The server enforces the same list."""
    caps = ["comics:read"]
    if writable:
        caps.append("comics:write")
    return caps


def _write_guard(request):
    """Return an error response when this write must be refused, else None."""
    if not request.app["writable"]:
        return fail(403, "readonly",
                    "当前是只读模式，请用 --serve --allow-write 重新启动")
    if request.headers.get(WRITE_HEADER) != WRITE_VALUE:
        return fail(403, "csrf", f"写操作需要 {WRITE_HEADER}: {WRITE_VALUE} 请求头")
    origin = request.headers.get("Origin")
    if origin and origin.split("://", 1)[-1].rstrip("/") != \
            request.headers.get("Host", ""):
        return fail(403, "csrf", "Origin 与 Host 不一致，已拒绝该请求")
    return None


# ============================================================
# Handlers
# ============================================================

async def handle_index(request):
    path = request.app["web_root"] / "index.html"
    if not path.is_file():
        return fail(500, "missing_ui", f"前端文件缺失：{path}")
    # no-store: a stale console is worse than a slow one.
    return web.FileResponse(path, headers={"Cache-Control": "no-store"})


async def handle_meta(request):
    registry = get_registry()
    writable = request.app["writable"]
    return ok({
        "app": "ManwaDownloader",
        "version": __version__,
        "mode": "operator" if writable else "viewer",
        "capabilities": _caps(writable),
        "counts": {"comics": len(registry)},
        "paths": {"registry": str(registry.path)},
    })


async def handle_health(request):
    return ok({"status": "ok"})


async def handle_comics(request):
    registry = get_registry()
    registry.load()             # another process (a download) may have written
    items = registry.entries()
    return ok({"items": items, "total": len(items)})


async def handle_comic_upsert(request):
    denied = _write_guard(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except Exception:                            # noqa: BLE001
        return fail(400, "bad_json", "请求体不是合法 JSON")
    if not isinstance(body, dict):
        return fail(400, "bad_json", "请求体必须是 JSON 对象")

    cid = str(body.get("cid", "")).strip()
    name = str(body.get("name", "")).strip()
    if not cid or not name:
        return fail(400, "invalid", "cid 与 name 都不能为空")
    if not cid.isdigit():
        return fail(400, "invalid", "Comic ID 必须是数字")
    if len(name) > NAME_MAX_LEN:
        return fail(400, "invalid", f"漫画名称超过 {NAME_MAX_LEN} 字")

    bound = get_registry().upsert(cid, name)
    if bound != cid:
        # The registry keeps name -> cid unique; surface it instead of silently
        # binding to a different comic.
        return fail(409, "name_taken", f"该名称已绑定 Comic ID {bound}")
    return ok({"cid": cid, "name": name})


async def handle_comic_delete(request):
    denied = _write_guard(request)
    if denied is not None:
        return denied
    cid = request.match_info.get("cid", "").strip()
    if not get_registry().remove(cid):
        return fail(404, "not_found", f"Comic ID {cid} 不在表格里")
    return ok({"cid": cid})


# ============================================================
# Middleware
# ============================================================

@web.middleware
async def error_middleware(request, handler):
    """One JSON error shape for the API; HTML errors stay untouched."""
    try:
        return await handler(request)
    except web.HTTPException as exc:
        if request.path.startswith("/api/"):
            return fail(exc.status, exc.reason or "http_error",
                        exc.reason or "请求失败")
        raise
    except Exception as exc:                     # noqa: BLE001
        logger.error("web: %s %s failed: %s", request.method, request.path,
                     exc, exc_info=True)
        if request.path.startswith("/api/"):
            return fail(500, "internal", str(exc) or "服务器内部错误")
        raise


@web.middleware
async def no_cache_assets(request, handler):
    """Frontend files are edited during development; revalidate instead of cache."""
    response = await handler(request)
    if request.path.startswith("/assets/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


# ============================================================
# App factory
# ============================================================

def create_app(*, writable: bool = False, root: Path | None = None) -> web.Application:
    """Build the aiohttp app. ``writable`` gates every mutating endpoint."""
    app = web.Application(middlewares=[error_middleware, no_cache_assets])
    app["writable"] = bool(writable)
    app["web_root"] = Path(root) if root is not None else web_root()

    app.router.add_get("/", handle_index)
    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/meta", handle_meta)
    app.router.add_get("/api/comics", handle_comics)
    app.router.add_post("/api/comics", handle_comic_upsert)
    app.router.add_delete("/api/comics/{cid}", handle_comic_delete)
    # add_static() raises on a missing directory; a broken bundle should end up
    # as the friendly "前端文件缺失" page instead of a startup traceback.
    assets = app["web_root"] / "assets"
    if assets.is_dir():
        app.router.add_static("/assets/", assets,
                              show_index=False, follow_symlinks=False)
    return app


def run_web(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
            writable: bool = False) -> int:
    """Start the console and block until Ctrl+C. Returns a process exit code."""
    root = web_root()
    if not (root / "index.html").is_file():
        tprint(f"X 前端目录缺失：{root}")
        tprint("  源码运行时请在项目根目录执行；打包后请确认 web/ 已随 spec 一起打进去。")
        return 1

    app = create_app(writable=writable, root=root)
    mode = "可写（可登记 / 删除）" if writable else "只读"
    tprint("=" * 60)
    tprint(f"  Manwa Web Console v{__version__}")
    tprint(f"  地址：http://{host}:{port}/")
    tprint(f"  模式：{mode}   数据：{get_registry().path}")
    tprint("  Ctrl+C 退出")
    tprint("=" * 60)

    try:
        web.run_app(app, host=host, port=port, print=None, access_log=None)
    except OSError as exc:
        tprint(f"X 端口 {port} 启动失败：{exc}")
        tprint(f"  换一个端口：--serve --port {port + 1}")
        return 1
    return 0
