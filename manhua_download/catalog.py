"""Crawl the site's own catalogue into the comic table.

The downloader only ever knows the comics you already downloaded; this fills the
table with what the site actually publishes. ``/api/rank`` is the site's
browsable listing — 20 entries per page, each carrying a ``/comic/{id}`` link,
so the Comic ID comes from the same field the user would click.

Two modes:

* ``crawl(pages)``  — the first N pages, sequential, for a quick look.
* ``crawl_all()``   — the whole catalogue (~170k rows / ~8.5k pages). Fetches
  through a thread pool, records in batches, and keeps a page-level checkpoint
  so an interrupted run resumes instead of restarting from page 1.
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import config as _cfg
from .comics import get_registry
from .config import (
    data_dir, get_rate_limiter, logger, make_session, robust_get,
    thread_session, tprint,
)
from .utils import parse_cid

RANK_PATH = "/api/rank?page={page}"
API_RETRIES = 2
PAGE_SIZE = 20
MAX_PAGES = 50              # 顺序模式的页数上限（1000 条）

CHECKPOINT_FILE = ".crawl-checkpoint.json"
BATCH_ROWS = 2000           # 每写这么多行落一次盘
BATCH_PAGES_SAVE = 100      # 每完成这么多页存一次断点
MAX_WORKERS = 24


def _headers() -> dict:
    """Same Referer rule as the rest of the API layer."""
    return {"Referer": f"{_cfg.BASE_URL}/"}


def fetch_page(sess, page: int) -> tuple[list, int]:
    """One page of the ranking API -> (items, total). Raises on API errors."""
    if sess is None:
        sess = thread_session()          # 线程池里每个线程一个 session
    get_rate_limiter().wait()
    url = f"{_cfg.BASE_URL}{RANK_PATH.format(page=page)}"
    r = robust_get(sess, url, tuple(_cfg.API_TIMEOUT), retries=API_RETRIES,
                   backoff=0.3, headers=_headers())
    r.raise_for_status()
    d = r.json()
    if d.get("code") != 200:
        raise RuntimeError(d.get("msg") or f"rank api error (code={d.get('code')})")
    data = d.get("data") or {}
    return data.get("list") or [], int(data.get("total") or 0)


# ============================================================
# Checkpoint — 让 8 千多页的任务可以中断续跑
# ============================================================

def checkpoint_path() -> Path:
    return data_dir() / CHECKPOINT_FILE


def load_checkpoint() -> set:
    try:
        raw = json.loads(checkpoint_path().read_text(encoding="utf-8"))
        return {int(p) for p in (raw.get("done_pages") or [])}
    except (OSError, ValueError):
        return set()


def save_checkpoint(done: set) -> None:
    path = checkpoint_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(
            json.dumps({"done_pages": sorted(done), "ts": time.time()}),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("checkpoint write failed: %s", exc)


def clear_checkpoint() -> None:
    try:
        checkpoint_path().unlink()
    except OSError:
        pass


# ============================================================
# 顺序模式（前 N 页）
# ============================================================

def crawl(pages: int = 5, sess=None, on_page=None) -> dict:
    """Fetch ``pages`` pages of the ranking and record every comic.

    Returns a summary dict: fetched / added / merged / pages / total.
    """
    pages = max(1, min(int(pages), MAX_PAGES))
    sess = sess if sess is not None else make_session(pool=4)
    registry = get_registry()
    before = len(registry)

    fetched = 0
    done = 0
    total = 0
    for page in range(1, pages + 1):
        try:
            items, total = fetch_page(sess, page)
        except Exception as exc:                     # noqa: BLE001
            logger.warning("crawl page %s failed: %s", page, exc)
            tprint(f"  [!] 第 {page} 页抓取失败：{exc}")
            break
        if not items:
            break
        pairs = []
        for item in items:
            cid = parse_cid(item.get("url") or "")
            name = (item.get("title") or "").strip()
            if cid and name:
                pairs.append((cid, name))
        registry.upsert_many(pairs)
        fetched += len(pairs)
        done = page
        if on_page is not None:
            on_page(page, len(items), fetched)

    added = len(registry) - before
    return {
        "fetched": fetched,
        "added": added,
        "merged": fetched - added,
        "pages": done,
        "total": total,
    }


def run_crawl(pages: int = 5) -> dict:
    """CLI entry for the sequential mode."""
    tprint(f"[>] 抓取站点目录：{pages} 页 × {PAGE_SIZE} 条 ...")
    t0 = time.time()

    def _progress(page, count, fetched):
        tprint(f"  第 {page:>2} 页  +{count:<3} 累计 {fetched} 条", end="\r")

    try:
        summary = crawl(pages, on_page=_progress)
    except Exception as exc:                         # noqa: BLE001
        tprint(f"\nX 抓取失败：{exc}")
        tprint("  检查网络 / 代理设置（config.json 的 proxy），或换时间段重试。")
        return {"error": str(exc), "fetched": 0, "added": 0, "merged": 0,
                "pages": 0, "total": 0}

    tprint(" " * 60, end="\r")
    ela = time.time() - t0
    tprint(f"  完成：{summary['pages']} 页，新增 {summary['added']} 条，"
           f"合并重复 {summary['merged']} 条（{ela:.1f}s）")
    if summary["total"]:
        tprint(f"  站点当前共 {summary['total']} 条，本次只取了前 "
               f"{summary['pages']} 页；全量请用 --all。")
    return summary


# ============================================================
# 全量模式（并发 + 断点）
# ============================================================

def crawl_all(workers: int = 12, resume: bool = True, on_progress=None) -> dict:
    """Fetch the entire catalogue. Returns a summary dict.

    ``on_progress(done_pages, total_pages, fetched, failed)`` fires after every
    completed page.
    """
    workers = max(1, min(int(workers), MAX_WORKERS))
    _, total = fetch_page(thread_session(), 1)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE if total else 0
    if total_pages <= 0:
        raise RuntimeError("站点没有返回目录总数，无法全量抓取")

    done = load_checkpoint() if resume else set()
    done &= set(range(1, total_pages + 1))       # 站点缩水时丢弃过期页码
    todo = [p for p in range(1, total_pages + 1) if p not in done]

    registry = get_registry()
    before = len(registry)
    fetched = failed = 0
    buffer: list = []
    since_save = 0

    def flush():
        nonlocal buffer
        if buffer:
            registry.upsert_many(buffer, batch=BATCH_ROWS)
            buffer = []

    try:
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="crawl") as pool:
            futures = {pool.submit(fetch_page, None, p): p for p in todo}
            for fut in as_completed(futures):
                page = futures[fut]
                try:
                    items, _ = fut.result()
                except Exception as exc:             # noqa: BLE001
                    failed += 1
                    logger.warning("crawl page %s failed: %s", page, exc)
                    continue

                for item in items:
                    cid = parse_cid(item.get("url") or "")
                    name = (item.get("title") or "").strip()
                    if cid and name:
                        buffer.append((cid, name))
                        fetched += 1

                done.add(page)
                if len(buffer) >= BATCH_ROWS:
                    flush()
                since_save += 1
                if since_save >= BATCH_PAGES_SAVE:
                    save_checkpoint(done)
                    since_save = 0
                if on_progress is not None:
                    on_progress(len(done), total_pages, fetched, failed)
    finally:
        try:
            flush()
            save_checkpoint(done)
        except Exception:                            # noqa: BLE001
            pass

    added = len(registry) - before
    return {
        "fetched": fetched,
        "added": added,
        "merged": fetched - added,
        "pages": len(done),
        "total_pages": total_pages,
        "failed": failed,
        "total": total,
    }


def run_crawl_all(workers: int = 12) -> dict:
    """CLI entry for the full-catalogue mode."""
    workers = max(1, min(int(workers), MAX_WORKERS))
    tprint(f"[>] 全量抓取：并发 {workers}，每页 {PAGE_SIZE} 条")

    resumed = load_checkpoint()
    if resumed:
        tprint(f"  [i] 发现断点：已完成 {len(resumed)} 页，跳过继续")

    t0 = time.time()
    last = [0.0]

    def _progress(done_pages, total_pages, fetched, failed):
        now = time.time()
        if now - last[0] < 2.0:
            return
        last[0] = now
        ela = now - t0
        rate = done_pages / ela if ela > 0 else 0
        eta = (total_pages - done_pages) / rate if rate > 0 else 0
        tprint(f"  {done_pages}/{total_pages} 页 "
               f"({done_pages * 100.0 / total_pages:4.1f}%)  "
               f"收录 {fetched} 条  失败 {failed}  剩约 {eta / 60:.0f} 分", end="\r")

    try:
        summary = crawl_all(workers, on_progress=_progress)
    except KeyboardInterrupt:
        tprint("\n\n[*] 已中断 — 进度已存，重跑同一条命令会从断点继续。")
        raise
    except Exception as exc:                         # noqa: BLE001
        tprint(f"\nX 全量抓取失败：{exc}")
        return {"error": str(exc), "fetched": 0, "added": 0, "merged": 0,
                "pages": 0, "total_pages": 0, "failed": 0, "total": 0}

    tprint(" " * 78, end="\r")
    ela = time.time() - t0
    tprint(f"  完成：{summary['pages']}/{summary['total_pages']} 页  "
           f"收录 {summary['fetched']} 条  新增 {summary['added']}  "
           f"合并 {summary['merged']}  失败页 {summary['failed']}  "
           f"({ela / 60:.1f} 分)")
    if summary["failed"]:
        tprint("  [i] 有失败页：再跑一次会只补这些页（已完成页已进断点）。")
    else:
        clear_checkpoint()               # 全部成功，断点没用了
    return summary
