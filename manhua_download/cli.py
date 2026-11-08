"""CLI — argument parsing, interactive mode, async orchestration."""

import argparse
import asyncio
import os
import sys
import time
import traceback
from pathlib import Path

import aiohttp

from . import config as _cfg
from .config import (
    API_THREADS, CHAPTER_RETRY, CONNECTOR_LIMIT_PER_HOST, DEFAULT_CH_WORKERS,
    DEFAULT_WORKERS, DNS_CACHE_TTL, IMAGE_SOURCES, IO_THREADS,
    MAX_WORKERS, PIPELINE_AHEAD_MULT,
    apply_config, close_session, data_dir, get_aiohttp_headers, get_proxy,
    last_detect_failed, redetect_domain, set_output_dir, setup_log_file,
    setup_signal_handlers, tprint,
    cleanup_tmp_files, logger,
)
from .api import get_chapters, get_comic_name, show_comic_info
from .downloader import (
    Stats, dl_chapter, init_workers, prepare_chapter, run_blocking,
    shutdown_workers,
)
from .utils import sname, parse_cid, fmt_t, count_valid_images

# Rich imports (optional)
try:
    from rich.console import Console
    from rich.progress import (
        Progress, BarColumn, TextColumn,
        TimeRemainingColumn, SpinnerColumn,
    )
    HAS_RICH = True
    console = Console()
except ImportError:
    HAS_RICH = False
    console = None


# ============================================================
# Argument parsing
# ============================================================

def _clamp_source(value, n_sources: int) -> int:
    """Clamp a configured source index into range.

    The choices list is built from IMAGE_SOURCES, which apply_config() replaces
    *after* import. A config.json listing more (or fewer) sources than the
    built-in four used to make argparse reject a valid index — or accept one
    that then raised IndexError in async_main.
    """
    try:
        idx = int(value)
    except (TypeError, ValueError):
        return 0
    if n_sources <= 0:
        return 0
    return min(max(idx, 0), n_sources - 1)


def parse_args(argv, cfg: dict):
    """Parse CLI arguments with dynamic choices based on config."""
    n_sources = len(IMAGE_SOURCES)
    source_choices = list(range(n_sources))

    # Build source help text dynamically
    src_help_parts = []
    for idx, url in enumerate(IMAGE_SOURCES):
        domain = url.split("//")[-1]
        src_help_parts.append(f"{idx}={domain}")
    src_desc = " | ".join(src_help_parts)

    ap = argparse.ArgumentParser(
        prog="ManwaDownloader",
        description="Manwa Manga Downloader v5 (manwaqi.cc)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("comic", help="漫画URL或ID")
    ap.add_argument("--start", type=int, default=1, help="起始章节号（默认1）")
    ap.add_argument("--end", type=int, default=0, help="结束章节号（默认全部）")
    ap.add_argument(
        "--source", type=int,
        default=_clamp_source(cfg.get("default_source", 0), n_sources),
        choices=source_choices,
        help=f"图源: {src_desc}",
    )
    w_def = int(cfg.get("workers", DEFAULT_WORKERS))
    cw_def = int(cfg.get("ch_workers", DEFAULT_CH_WORKERS))
    ap.add_argument(
        "--workers", type=int, default=w_def,
        help=f"全局并发图片数（默认{w_def}，越大越快但更吃带宽）",
    )
    ap.add_argument(
        "--ch-workers", type=int, default=cw_def,
        help=f"同时进行的章节数（默认{cw_def}）",
    )
    ap.add_argument(
        "--retry", type=int, default=cfg.get("retry", CHAPTER_RETRY),
        help="章级别API重试次数（默认2）",
    )
    ap.add_argument("--no-resume", action="store_true", help="忽略已下载，重新下载")
    ap.add_argument("--output", type=str, default=".", help="输出目录（默认当前目录）")
    ap.add_argument("--info", action="store_true", help="仅显示漫画信息，不下载")
    return ap.parse_args(argv)


# ============================================================
# Async main orchestration
# ============================================================

async def async_main(args):
    """Single event loop orchestrates everything — no nested asyncio.run()."""
    comic_id = parse_cid(args.comic)
    if not comic_id:
        tprint(f"X Cannot parse: {args.comic}")
        sys.exit(1)

    if args.info:
        show_comic_info(comic_id)
        return

    img_w = max(1, min(args.workers, MAX_WORKERS))
    ch_w = max(1, min(args.ch_workers, 12))
    chapter_retry = max(0, min(args.retry, 10))
    prim_src = IMAGE_SOURCES[args.source]
    fallback_srcs = [
        s for i, s in enumerate(IMAGE_SOURCES) if i != args.source
    ]

    # Banner
    if HAS_RICH:
        console.rule(f"[bold cyan]Manwa Downloader v5 | ID: {comic_id} "
                     f"| src: {prim_src.split('//')[-1]}")
    else:
        tprint("=" * 60)
        tprint(
            f"  Manwa Downloader v5  |  ID: {comic_id}  |  "
            f"src: {prim_src.split('//')[-1]}"
        )
        tprint("=" * 60)

    # Thread pools for blocking work (API / disk / decrypt)
    init_workers(API_THREADS, IO_THREADS)

    api_sess = None  # per-thread sessions are created on demand

    # Comic name
    comic_name = get_comic_name(api_sess, comic_id)
    out_name = sname(comic_name, 30) if comic_name else f"comic_{comic_id}"
    _print_banner_line(comic_name)

    # Chapter list
    _print_getting_list()
    t0 = time.time()
    try:
        all_chs = await run_blocking(get_chapters, api_sess, comic_id)
    except Exception as e:
        err = str(e)
        # A 404/403 here almost never means "comic not found" — it means the
        # mirror we picked has no API. Swap mirrors and retry.
        transient = ("404", "403", "Not Found", "SSL", "Connection",
                     "Max retries", "timed out")
        _print_failed(err)
        if not any(k in err for k in transient):
            sys.exit(1)
        # Try once more even if the startup probe came up empty: the site moves
        # domains constantly, and redetect_domain() drops the caches and runs a
        # full re-discovery, so a transient blip at startup is recoverable.
        if last_detect_failed():
            tprint("  [i] 启动时所有镜像都没连上，正在重新探测并查找新域名...")
        else:
            tprint("  [>] Mirror looks unusable, re-detecting domain...")
        try:
            new_base = await run_blocking(redetect_domain)
            if last_detect_failed():
                tprint("  [i] 重新探测仍然没有任何镜像可用，停止。")
                tprint("      → 检查网络 / 开代理，或在 config.json 填 \"proxy\" 后再试。")
                sys.exit(1)
            tprint(f"  [>] Retry with {new_base}")
            all_chs = await run_blocking(get_chapters, api_sess, comic_id)
        except Exception as e2:
            _print_failed(str(e2))
            tprint("  [i] 换镜像后仍然失败，稍后再试或使用代理。")
            sys.exit(1)
    _print_chapter_count(len(all_chs), time.time() - t0)

    # The very first request of a run is the most likely to hit a cold-TLS
    # reset, so if the title failed, try once more now that the network is warm.
    if not comic_name and not last_detect_failed():
        comic_name = await run_blocking(get_comic_name, api_sess, comic_id)
        if comic_name:
            out_name = sname(comic_name, 30)
            _print_banner_line(comic_name)

    if not all_chs:
        tprint("! No chapters")
        sys.exit(0)

    # Build queue
    s = max(1, args.start) - 1
    e = min(len(all_chs), args.end) if args.end > 0 else len(all_chs)
    if s >= len(all_chs):
        tprint(f"X out of range (max {len(all_chs)})")
        sys.exit(1)

    chs = all_chs[s:e]
    out = Path(args.output) / out_name
    out.mkdir(parents=True, exist_ok=True)
    set_output_dir(Path(args.output))   # so Ctrl+C sweeps the right tree for .tmp

    _print_output_dir(out)
    _print_range(s + 1, e, img_w, ch_w)

    queue = await _build_queue(chs, s, e, out, args.no_resume)
    if not queue:
        _print_all_done()
        return

    _print_pending(len(queue))

    # === Download ===
    stats = Stats()
    t_start = stats.t0
    total_ok = total_skip = total_fail = 0
    counters = {"api_fail": 0}

    aiohttp_headers = get_aiohttp_headers()
    connect_to, read_to = _cfg.IMG_TIMEOUT
    connector = aiohttp.TCPConnector(
        limit=img_w + ch_w * 2 + 8,          # one socket per in-flight image
        limit_per_host=CONNECTOR_LIMIT_PER_HOST,   # 0 = unlimited per host
        force_close=False,                   # keep-alive: reuse TLS connections
        enable_cleanup_closed=True,
        ttl_dns_cache=DNS_CACHE_TTL,
        keepalive_timeout=_cfg.KEEPALIVE_TIMEOUT,
    )
    timeout = aiohttp.ClientTimeout(
        total=600,
        connect=connect_to,
        sock_connect=connect_to,
        sock_read=read_to,
    )

    # Progress
    pbar = None
    task_ch = None
    if HAS_RICH:
        pbar = Progress(
            SpinnerColumn("dots"),
            TextColumn("{task.description}"),
            BarColumn(bar_width=30),
            TextColumn("{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
            TextColumn("[cyan]({task.fields[imgs]} imgs)[/cyan]"),
            TextColumn("{task.fields[speed]}"),
            TimeRemainingColumn(elapsed_when_finished=True, compact=True),
            console=console,
        )
        task_ch = pbar.add_task("Chapters", total=len(queue), imgs=0, speed="")
        pbar.start()

    _last_tick = [0.0]

    def _tick(force: bool = False):
        now = time.time()
        if not force and now - _last_tick[0] < 0.1:
            return
        _last_tick[0] = now
        if pbar is None:
            return
        ela = stats.elapsed
        spd = ""
        if ela > 0 and stats.done:
            spd = f"{stats.done / ela:.1f} img/s"
            if stats.nbytes:
                spd += f" {stats.nbytes / ela / 1e6:.1f}MB/s"
        pbar.update(task_ch, advance=0, imgs=stats.done, speed=spd)

    stats.on_update = _tick

    img_sem = asyncio.Semaphore(img_w)
    ch_sem = asyncio.Semaphore(ch_w)

    # --- Chapter prefetch pipeline -------------------------------------
    # Image-list requests for upcoming chapters run in the API thread pool
    # while the current chapters download, so API latency is fully hidden.
    ahead = max(ch_w * PIPELINE_AHEAD_MULT, ch_w + 2)
    prefetch: dict[int, asyncio.Task] = {}

    def _folder(ci: int, title: str) -> Path:
        return out / f"{ci:04d}_{sname(title)}"

    def _schedule(j: int):
        if j < 0 or j >= len(queue) or j in prefetch:
            return
        ch, ci = queue[j]
        prefetch[j] = asyncio.create_task(
            run_blocking(prepare_chapter, ch, _folder(ci, ch["title"]),
                         prim_src, fallback_srcs, chapter_retry)
        )

    def _fill_window(start: int):
        for j in range(start, min(start + ahead, len(queue))):
            _schedule(j)

    async def _run_one(j: int) -> dict:
        ch, ci = queue[j]
        try:
            async with ch_sem:
                fut = prefetch.pop(j, None)
                if fut is None:
                    _schedule(j)
                    fut = prefetch.pop(j)
                try:
                    kind, payload = await fut
                except Exception as exc:                 # noqa: BLE001
                    logger.error("prepare failed for %s: %s", ch["title"], exc)
                    kind, payload = "fail", None

                _fill_window(j + 1)   # keep the look-ahead window topped up

                if kind == "skip":
                    tprint(
                        f"  [{ci:04d}/{e:04d}] {ch['title'][:30]:30s}"
                        f" OK {payload}p (skip, complete)"
                    )
                    return {"ok": 0, "skip": payload, "fail": 0, "total": payload}

                if kind == "fail" or not payload:
                    counters["api_fail"] += 1
                    tprint(
                        f"  [{ci:04d}/{e:04d}] {ch['title'][:30]:30s}"
                        f" !! API fail (all sources exhausted)"
                    )
                    return {"ok": 0, "skip": 0, "fail": 0, "total": 0}

                urls, used_src = payload
                return await dl_chapter(
                    session, ch, out, urls, used_src, fallback_srcs,
                    ci, e, img_sem, stats,
                )
        finally:
            if pbar is not None:
                _tick(force=True)
                pbar.update(task_ch, advance=1)

    results: list = []
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers=aiohttp_headers,
            proxy=get_proxy(),
        ) as session:
            _fill_window(0)
            tasks = [asyncio.create_task(_run_one(j)) for j in range(len(queue))]
            results = await asyncio.gather(*tasks, return_exceptions=True)
    except (KeyboardInterrupt, asyncio.CancelledError):
        _print_interrupted()
        cleanup_tmp_files(out)
        raise
    finally:
        for t in prefetch.values():
            t.cancel()
        prefetch.clear()

    if pbar:
        pbar.stop()

    # Collect stats
    for r in results:
        if isinstance(r, BaseException):
            tprint(f"\n  [!] Chapter crashed: {r}")
            logger.error("Chapter crashed: %s", traceback.format_exc())
            total_fail += 1
            continue
        total_ok += r.get("ok", 0)
        total_skip += r.get("skip", 0)
        total_fail += r.get("fail", 0)

    # Final summary
    ela = time.time() - t_start
    downloaded = total_ok + total_skip
    _print_summary(downloaded, total_fail, ela, out, counters["api_fail"])


# ============================================================
# Output helpers (centralized for H1: no mixed print/console)
# ============================================================

def _rich_print(msg: str, style: str = ""):
    """Unified output through rich console if available."""
    if HAS_RICH and console:
        console.print(msg, **({"style": style} if style else {}))
    else:
        tprint(msg)


def _print_banner_line(name):
    if HAS_RICH:
        console.print(f"  [green]{name}[/green]" if name else "  (name unknown)")
    else:
        tprint(f"  {name}" if name else "  (name unknown)")


def _print_getting_list():
    if HAS_RICH:
        console.print("[>] Getting chapter list...", end="")
    else:
        tprint("[>] Getting chapter list...", end=" ", flush=True)


def _print_chapter_count(n: int, elapsed: float):
    elapsed_fmt = fmt_t(elapsed)
    if HAS_RICH:
        console.print(f" [green]{n} chapters[/green] ({elapsed_fmt})")
    else:
        tprint(f"{n} chapters ({elapsed_fmt})")


def _print_failed(err: str):
    if HAS_RICH:
        console.print(f"\n[red]X Failed: {err}[/red]")
    else:
        tprint(f"\nX Failed: {err}")


def _print_output_dir(path: Path):
    if HAS_RICH:
        console.print(f"  Dir: [cyan]{path.resolve()}[/cyan]")
    else:
        tprint(f"  Dir: {path.resolve()}")


def _print_range(s, e, iw, cw):
    if HAS_RICH:
        console.print(f"  Ch: {s}-{e} | imgs in flight:{iw} | ch in flight:{cw}")
    else:
        tprint(f"  Ch: {s}-{e} | imgs in flight:{iw} | ch in flight:{cw}")


def _print_pending(n: int):
    if HAS_RICH:
        console.print(f"\n  Pending: [bold yellow]{n}[/bold yellow] chapters\n")
    else:
        tprint(f"  Pending: {n} chapters\n")


def _print_all_done():
    if HAS_RICH:
        console.print("\n[green]All done [OK][/green]")
    else:
        tprint("\n  All done [OK]")


def _print_interrupted():
    if HAS_RICH:
        console.print("\n[yellow][*] Interrupted, cleaning up...[/yellow]")
    else:
        tprint("\n[*] Interrupted, cleaning up...")


def _print_summary(downloaded: int, fail: int, ela: float, path: Path,
                   api_fail: int = 0):
    extra = ""
    if fail:
        extra += f" | {fail} failed imgs"
    if api_fail:
        extra += f" | {api_fail} failed chapters"
    if HAS_RICH:
        console.print()
        console.rule("[bold]Done[/bold]")
        console.print(
            f"  [green]{fmt_t(ela)}[/green] | {downloaded} images"
            + (f" | [red]{fail} failed imgs[/red]" if fail else "")
            + (f" | [red]{api_fail} failed chapters[/red]" if api_fail else "")
        )
        if downloaded > 0 and ela > 0:
            console.print(f"  Speed: [cyan]{downloaded / ela:.1f} img/s[/cyan]")
        console.print(f"  Path: [cyan]{path.resolve()}[/cyan]")
        console.rule()
    else:
        tprint(f"\n{'=' * 60}")
        tprint(f"  Done! {fmt_t(ela)} | {downloaded} images{extra}")
        if downloaded > 0 and ela > 0:
            tprint(f"  Speed: {downloaded / ela:.1f} img/s")
        tprint(f"  Path: {path.resolve()}")
        tprint(f"{'=' * 60}")


# ============================================================
# Queue builder — resume scan runs in threads, in parallel
# ============================================================

async def _build_queue(chs, s, e, out, no_resume):
    """Build download queue: drop VIP chapters and already-complete folders."""
    items = []
    for i, ch in enumerate(chs):
        ci = s + i + 1
        if ch.get("isVip"):
            tprint(f"  [{ci:04d}/{e:04d}] {ch['title'][:30]:30s} [VIP]")
            continue
        items.append((ch, ci))

    if no_resume:
        return items

    loop = asyncio.get_running_loop()

    def _is_complete(item) -> bool:
        ch, ci = item
        need = ch.get("picCount", 0)
        if need <= 0:
            return False
        dd = out / f"{ci:04d}_{sname(ch['title'])}"
        return count_valid_images(dd) >= need

    flags = await asyncio.gather(
        *[loop.run_in_executor(None, _is_complete, it) for it in items]
    )

    queue = []
    for item, done in zip(items, flags):
        ch, ci = item
        if done:
            tprint(f"  [{ci:04d}/{e:04d}] {ch['title'][:30]:30s} SKIP complete")
        else:
            queue.append(item)
    return queue


# ============================================================
# Interactive mode
# ============================================================

def interactive_mode():
    """Interactive prompt mode — constructs args directly (C3 fix)."""
    if HAS_RICH:
        console.rule("[bold cyan]Manwa Manga Downloader v5 (manwaqi.cc)")
        console.print()
        console.print("  Example inputs:")
        console.print("    [green]1728[/green]               = YiRenZhiXia")
        console.print(
            "    [green]https://www.manwaqi.cc/comic/1728[/green]"
        )
    else:
        tprint("=" * 60)
        tprint("  Manwa Manga Downloader v5")
        tprint("=" * 60)
        tprint()
        tprint("  Example inputs:")
        tprint("    1728               = YiRenZhiXia")
        tprint("    https://www.manwaqi.cc/comic/1728")
    tprint()

    while True:
        cid_input = input("  Comic ID or URL (q=quit): ").strip()
        if not cid_input:
            continue
        if cid_input.lower() in ("q", "quit"):
            break
        cid = parse_cid(cid_input)
        if not cid:
            tprint("  X Cannot parse ID, try again.\n")
            continue
        tprint()
        try:
            show_comic_info(cid)
        except SystemExit:
            tprint("  [i] 取不到漫画信息，换个 ID 或稍后再试。")
            continue
        except Exception:
            logger.error("show_comic_info crashed:\n%s", traceback.format_exc())
            tprint("  [i] 取不到漫画信息，换个 ID 或稍后再试。")
            continue

        while True:
            tprint()
            tprint("  Download options:")
            tprint("    N       = Single chapter (e.g. 1)")
            tprint("    N-M     = Range (e.g. 1-10)")
            tprint("    all     = All chapters")
            tprint("    back    = Choose another comic")
            tprint("    q       = Quit")
            tprint()
            rng = input("  Chapter number or range: ").strip()

            if not rng:
                tprint("\n  [TEST] First 5 chapters...")
                _run_with_args(cid, start=1, end=5)
                continue
            if rng.lower() == "q":
                tprint("  Bye!")
                return
            if rng.lower() == "back":
                break
            if rng.lower() == "all":
                tprint("\n  [FULL] All chapters...")
                _run_with_args(cid)
                continue
            if "-" in rng:
                parts = rng.split("-", 1)
                if parts[0].isdigit() and parts[1].isdigit():
                    a, b = int(parts[0]), int(parts[1])
                    tprint(f"\n  [RANGE] Chapters {a}-{b}...")
                    _run_with_args(cid, start=a, end=b)
                    continue
            if rng.isdigit():
                n = int(rng)
                tprint(f"\n  [SINGLE] Chapter {n}...")
                _run_with_args(cid, start=n, end=n)
                continue
            tprint("  Unknown format. Try again.")

    tprint("  Bye!")
    try:
        input("\n  Press Enter to exit...")
    except (EOFError, KeyboardInterrupt):
        pass


def _make_args(cid, start=1, end=0, **overrides):
    """Build argparse.Namespace for interactive mode (C3 fix).

    Reads the same config.json keys the CLI path uses. Interactive mode is what
    a double-clicked exe runs, and it used to hardcode 64/8/2 — so tuning
    workers in config.json had no effect at all unless you used the command line.
    """
    import types
    cfg = _cfg.load_config()
    base = dict(
        comic=cid, start=start, end=end,
        source=_clamp_source(cfg.get("default_source", 0), len(IMAGE_SOURCES)),
        workers=int(cfg.get("workers", DEFAULT_WORKERS)),
        ch_workers=int(cfg.get("ch_workers", DEFAULT_CH_WORKERS)),
        retry=int(cfg.get("retry", CHAPTER_RETRY)),
        no_resume=False, output=".", info=False,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _run_with_args(cid: str, start: int = 1, end: int = 0):
    """Run async_main from interactive mode without touching sys.argv (C3 fix)."""
    args = _make_args(cid, start=start, end=end)
    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        pass
    except SystemExit:
        # A single failed chapter-list fetch must not kick the user out of the
        # interactive session.
        tprint("\n  [i] 本次下载未完成，已返回菜单。")
    except Exception:
        logger.error("interactive run crashed:\n%s", traceback.format_exc())
        tprint("\n  [i] 出错了，已返回菜单。把 manwa_download.log 发给开发者可以定位。")


# ============================================================
# Main entry point
# ============================================================

def _console_owner_count() -> int:
    """Number of processes attached to this console.

    1 means we are the only one — i.e. the exe was double-clicked and the
    window will disappear the moment we exit.
    """
    if os.name != "nt":
        return 2
    try:
        import ctypes
        buf = (ctypes.c_uint32 * 8)()
        n = int(ctypes.windll.kernel32.GetConsoleProcessList(buf, 8))
        return n if n > 0 else 1
    except Exception:
        return 2


def _hold_console():
    """Keep the window open after a failure so the message can be read.

    Only acts when we own the console alone (double-click), so running from
    运行下载.bat or a normal terminal is completely unaffected.
    """
    if _console_owner_count() != 1:
        return
    try:
        tprint("\n  按回车键关闭窗口...")
        input()
    except Exception:
        try:
            time.sleep(10)
        except Exception:
            pass


def main():
    """Entry point — configure, parse, launch async loop."""
    code = 0
    try:
        setup_log_file()               # Error/warning log to file (first, so
                                       # config/domain failures are recorded)
        # --help needs no network — skip the (potentially slow) domain probe
        needs_net = not any(a in ("-h", "--help") for a in sys.argv[1:])
        cfg = apply_config(detect=needs_net)
        setup_signal_handlers()        # Graceful shutdown on Ctrl+C

        if len(sys.argv) <= 1:
            interactive_mode()         # No args -> interactive mode
        else:
            args = parse_args(sys.argv[1:], cfg)  # Parse only, no sys.argv hack
            try:
                asyncio.run(async_main(args))     # Single event loop, no nesting
            except KeyboardInterrupt:
                tprint("\n[*] Interrupted.")

    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    except KeyboardInterrupt:
        tprint("\n[*] Interrupted.")
    except Exception:
        code = 1
        tb = traceback.format_exc()
        logger.error("unhandled crash:\n%s", tb)
        last = tb.strip().splitlines()[-1] if tb.strip() else "unknown error"
        if HAS_RICH:
            console.print("\n[bold red]程序异常退出[/bold red]")
            console.print(f"[red]{last}[/red]")
        else:
            tprint("\n程序异常退出")
            tprint(last)
        tprint(f"  完整堆栈已记录到: {data_dir() / 'manwa_download.log'}")
    finally:
        for fn in (shutdown_workers, close_session):
            try:
                fn()
            except Exception:
                pass
        if code:
            _hold_console()

    sys.exit(code)
