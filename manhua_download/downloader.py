"""Async image downloader — v5.0 high-throughput pipeline.

Why v5 is faster than v4.2 (measured 112 img/s -> 500+ img/s on local bench):

1. **No blocking work on the event loop.** In v4 the sync ``requests`` call that
   fetches a chapter's image list (plus its ``time.sleep`` rate limiter) ran
   *inside* the coroutine, freezing every in-flight image download for the whole
   HTTP round-trip. All blocking work (API calls, disk writes, AES decryption)
   now runs in dedicated thread pools.
2. **One global image semaphore** instead of per-chapter semaphores. A chapter
   with 5 images no longer wastes 35 slots while another chapter queues up.
3. **Resume check via a single ``scandir`` snapshot** instead of 6 ``stat()``
   calls per image (300+ syscalls saved per chapter).
4. **Chapter prefetch** — image lists for the next chapters are fetched while the
   current ones download, so API latency is fully hidden.
"""

import asyncio
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import aiohttp

from .api import get_images_with_fallback
from .config import (
    API_THREADS, IO_THREADS,
    logger, tprint,
)
from . import config as _cfg
from .crypto_utils import decrypt_image, is_encrypted
from .utils import (
    count_valid_images, determine_ext, ext, fmt_t, is_html_response, sname,
)


# ------------------------------------------------------------------
# Timeouts are read from config *at call time*.
#
# They used to be imported by value (``from .config import IMG_TIMEOUT``),
# which snapshots the tuple at import — so apply_config() rebinding
# config.IMG_TIMEOUT had no effect and the connect_timeout / read_timeout
# keys in config.json were silently ignored. Go through the module instead.
# ------------------------------------------------------------------
def img_timeout() -> tuple:
    """(connect, read) seconds for image downloads."""
    return tuple(_cfg.IMG_TIMEOUT)


def img_timeout_fb() -> tuple:
    """(connect, read) seconds for the fallback CDN pass."""
    return tuple(_cfg.IMG_TIMEOUT_FB)

# ------------------------------------------------------------------
# Thread pools (created once, reused)
# ------------------------------------------------------------------
_api_pool: ThreadPoolExecutor | None = None
_io_pool: ThreadPoolExecutor | None = None


def init_workers(api_threads: int = API_THREADS, io_threads: int = IO_THREADS):
    """Create the dedicated pools (idempotent)."""
    global _api_pool, _io_pool
    if _api_pool is None:
        _api_pool = ThreadPoolExecutor(
            max_workers=max(2, api_threads), thread_name_prefix="api",
        )
    if _io_pool is None:
        _io_pool = ThreadPoolExecutor(
            max_workers=max(4, io_threads), thread_name_prefix="io",
        )


def shutdown_workers(wait: bool = False):
    global _api_pool, _io_pool
    for pool in (_api_pool, _io_pool):
        if pool is not None:
            pool.shutdown(wait=wait, cancel_futures=True)
    _api_pool = None
    _io_pool = None


def _pool(name: str) -> ThreadPoolExecutor:
    if _api_pool is None or _io_pool is None:
        init_workers()
    return _api_pool if name == "api" else _io_pool


async def run_blocking(func, /, *args):
    """Run a blocking callable in the API thread pool."""
    return await asyncio.get_running_loop().run_in_executor(_pool("api"), func, *args)


async def _read_exec(func, /, *args):
    """Run a blocking callable in the I/O thread pool (disk / crypto)."""
    return await asyncio.get_running_loop().run_in_executor(_pool("io"), func, *args)


# ------------------------------------------------------------------
# Progress counters (single event loop -> plain ints are safe)
# ------------------------------------------------------------------
class Stats:
    """Shared progress counters, updated by every completed image."""

    __slots__ = ("done", "ok", "fail", "nbytes", "t0", "on_update")

    def __init__(self):
        self.done = 0
        self.ok = 0
        self.fail = 0
        self.nbytes = 0
        self.t0 = time.time()
        self.on_update = None

    def bump(self, result: str, nbytes: int = 0):
        self.done += 1
        if result == "ok":
            self.ok += 1
            self.nbytes += nbytes
        elif result != "skip":
            self.fail += 1
        if self.on_update is not None:
            self.on_update()

    @property
    def elapsed(self) -> float:
        return time.time() - self.t0


# ------------------------------------------------------------------
# Chapter preparation (runs in the API thread pool)
# ------------------------------------------------------------------
def prepare_chapter(
    ch: dict,
    folder: Path,
    primary_src: str,
    fallback_srcs: list,
    max_retries: int,
) -> tuple:
    """Resume check + fetch image URL list for one chapter.

    Returns ("skip", need) | ("fail", None) | ("ok", (urls, used_src)).
    Runs in a worker thread so the event loop never blocks.
    """
    need = ch.get("picCount", 0)
    if need > 0 and count_valid_images(folder) >= need:
        return ("skip", need)

    urls, used = get_images_with_fallback(
        None, ch["id"], primary_src, fallback_srcs, max_retries,
    )
    if not urls:
        return ("fail", None)
    return ("ok", (urls, used))


# ------------------------------------------------------------------
# Single image
# ------------------------------------------------------------------
def _snapshot_existing(folder: Path) -> set:
    """Stems of already-downloaded images (one scandir pass, no per-file stat)."""
    found = set()
    try:
        with os.scandir(folder) as it:
            for entry in it:
                try:
                    if not entry.is_file() or entry.stat().st_size <= 512:
                        continue
                except OSError:
                    continue
                name = entry.name
                dot = name.rfind(".")
                if dot > 0:
                    found.add(name[:dot])
    except OSError:
        pass
    return found


def _write_atomic(fp: Path, data: bytes):
    """Write bytes -> fp atomically (.tmp + replace). Blocking; call in thread."""
    tmp = fp.parent / (fp.name + ".tmp")
    try:
        with open(tmp, "wb", buffering=0) as f:
            f.write(data)
        os.replace(tmp, fp)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


async def _dl_one(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    url: str,
    folder: Path,
    stem: str,
    url_suffix: str,
    timeout: aiohttp.ClientTimeout,
    existing: set,
) -> tuple:
    """Download one image. Returns (status, nbytes)."""
    if stem in existing:
        return ("skip", 0)

    async with sem:
        try:
            async with session.get(url, timeout=timeout) as r:
                if r.status != 200:
                    return (f"fail:http_{r.status}", 0)
                content_type = r.headers.get("Content-Type", "")
                data = await r.read()
        except asyncio.TimeoutError:
            return ("fail:timeout", 0)
        except aiohttp.ClientConnectionError:
            return ("fail:conn", 0)
        except asyncio.CancelledError:
            raise
        except Exception as e:                      # noqa: BLE001
            return (f"fail:{type(e).__name__}", 0)

        if len(data) < 512:
            return ("fail:too_small", 0)

        if is_html_response(data):
            logger.warning("HTML response for %s (len=%d)", url, len(data))
            return ("fail:html_response", 0)

        # AES-256-CBC payloads: decrypt off the loop (only when actually encrypted)
        if is_encrypted(data):
            decrypted = await _read_exec(decrypt_image, data)
            if decrypted is None:
                logger.error("Decryption failed for %s", url)
                return ("fail:decrypt", 0)
            data = decrypted
            if len(data) < 512:
                return ("fail:decrypted_too_small", 0)
            if is_html_response(data):
                return ("fail:decrypted_to_html", 0)

        correct_ext = determine_ext(data, content_type, url_suffix or ".jpg")
        fp = folder / f"{stem}{correct_ext}"

        try:
            await _read_exec(_write_atomic, fp, data)
        except OSError as e:
            return (f"fail:os_{e.errno}", 0)

        return ("ok", len(data))


async def _dl_tracked(stats: Stats, *args) -> tuple:
    """Wrap _dl_one so progress counters update the moment each image lands."""
    res = await _dl_one(*args)
    stats.bump(res[0], res[1])
    return res


# ------------------------------------------------------------------
# One chapter
# ------------------------------------------------------------------
async def _dl_images(
    session: aiohttp.ClientSession,
    urls: list,
    folder: Path,
    sem: asyncio.Semaphore,
    timeout: aiohttp.ClientTimeout,
    stats: Stats,
    existing: set | None = None,
) -> tuple:
    """Download every image of a chapter concurrently (shared global semaphore)."""
    if existing is None:
        existing = await _read_exec(_snapshot_existing, folder)

    tasks = [
        _dl_tracked(stats, session, sem, url, folder, f"{i + 1:04d}", ext(url),
                    timeout, existing)
        for i, url in enumerate(urls)
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return _tally(results, urls)


def _tally(results, urls) -> tuple:
    ok = skip = fail = 0
    fail_urls = []
    for i, r in enumerate(results):
        if isinstance(r, BaseException):
            r = f"fail:{type(r).__name__}"
        status = r[0] if isinstance(r, tuple) else r
        if status == "ok":
            ok += 1
        elif status == "skip":
            skip += 1
        else:
            fail += 1
            fail_urls.append((urls[i], i, status))
    return ok, skip, fail, fail_urls


async def dl_chapter(
    session: aiohttp.ClientSession,
    ch: dict,
    base: Path,
    urls: list,
    used_src: str,
    fallback_srcs: list,
    idx: int,
    total: int,
    img_sem: asyncio.Semaphore,
    stats: Stats,
) -> dict:
    """Download one chapter's images. Image list must be pre-fetched."""
    title = ch["title"]
    folder = base / f"{idx:04d}_{sname(title)}"
    folder.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    connect_to, read_to = img_timeout()
    timeout = aiohttp.ClientTimeout(
        total=read_to,
        connect=connect_to,
        sock_connect=connect_to,
        sock_read=read_to,
    )

    ok, skip, fail, fail_urls = await _dl_images(
        session, urls, folder, img_sem, timeout, stats,
    )

    # Failed images: retry in parallel across every other CDN mirror
    if fail_urls:
        fb_connect, fb_read = img_timeout_fb()
        fb_timeout = aiohttp.ClientTimeout(
            total=fb_read,
            connect=fb_connect,
            sock_connect=fb_connect,
            sock_read=fb_read,
        )
        existing = await _read_exec(_snapshot_existing, folder)
        for alt in fallback_srcs:
            if alt == used_src:
                continue
            still = []
            fb_tasks = []
            for u, i, reason in fail_urls:
                au = u.replace(used_src, alt, 1)
                if au == u:
                    still.append((u, i, reason))
                    continue
                fb_tasks.append((u, i, reason, au, i))
            if not fb_tasks:
                fail_urls = still
                break

            coros = [
                _dl_one(session, img_sem, t[3], folder, f"{t[4] + 1:04d}",
                        ext(t[3]), fb_timeout, existing)
                for t in fb_tasks
            ]
            fb_results = await asyncio.gather(*coros, return_exceptions=True)
            for (u, i, old_reason, _au, _ii), res in zip(fb_tasks, fb_results):
                if isinstance(res, BaseException):
                    res = (f"fail:{type(res).__name__}", 0)
                status = res[0] if isinstance(res, tuple) else res
                if status in ("ok", "skip"):
                    if status == "ok":
                        ok += 1
                        stats.ok += 1
                        stats.fail -= 1     # this image no longer counts as failed
                    else:
                        skip += 1
                    fail -= 1
                else:
                    still.append((u, i, f"{old_reason}->{status}"))
            fail_urls = still
            if not fail_urls:
                break

    ela = time.time() - t0
    line = (
        f"  [{idx:04d}/{total:04d}] {title[:30]:30s}"
        f" OK {ok + skip}/{len(urls)}"
    )
    if fail:
        line += f" X{fail}"
    line += f"  {fmt_t(ela)}"
    tprint(line)
    return {"ok": ok, "skip": skip, "fail": fail, "total": len(urls)}
