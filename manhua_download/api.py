"""Sync API functions for Manwa comic site.

These calls are blocking by design — the async layer runs them inside a
dedicated thread pool (see downloader.run_blocking) so they never stall the
event loop.
"""

import re
import sys
import time

import requests

from . import config as _cfg
from .config import (
    PAGE_SIZE,
    get_rate_limiter, make_session, robust_get, thread_session, tprint,
)
from .utils import fmt_t

# Retry budgets. A blocked network resets the TLS handshake in ~0.4s, so a big
# retry count only buys a longer wait before the user sees "cannot connect".
NAME_RETRIES = 3
CHAPTERS_RETRIES = 4
IMAGES_RETRIES = 3


def _api_timeout() -> tuple:
    """(connect, read) seconds for API calls, read from config at call time."""
    return tuple(_cfg.API_TIMEOUT)


def _base() -> str:
    """Current API base URL — read at call time so domain detection applies."""
    return _cfg.BASE_URL


def _sess(sess=None) -> requests.Session:
    """Return the caller's session, or a per-thread one."""
    return sess if sess is not None else thread_session()


def get_comic_name(sess: requests.Session | None, cid: str) -> str | None:
    """Extract comic name from detail page HTML. Multiple fallback patterns."""
    sess = _sess(sess)
    get_rate_limiter().wait()
    try:
        r = robust_get(sess, f"{_base()}/comic/{cid}", _api_timeout(),
                       retries=NAME_RETRIES, backoff=0.25)
        r.raise_for_status()
        html = r.text
        # Pattern 1: Chinese book title marks
        m = re.search(r'《(.+?)》', html)
        if m:
            return m.group(1).strip()
        # Pattern 2: <h1> tag content
        m = re.search(r'<h1[^>]*>([^<]{2,50})</h1>', html)
        if m:
            return m.group(1).strip()
        # Pattern 3: og:title meta tag
        m = re.search(
            r'<meta\s+property="og:title"\s+content="([^"]+)"',
            html, re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()
        # Pattern 4: title tag (clean up suffix)
        m = re.search(r'<title>([^<]+)</title>', html, re.IGNORECASE)
        if m:
            title = m.group(1).strip()
            # Strip common site suffixes
            for suffix in [" - 漫蛙", "_漫蛙", "-manwa", "漫画"]:
                if title.endswith(suffix):
                    return title[: -len(suffix)].strip()
            return title if len(title) > 2 else None
    except Exception as e:
        tprint(f"  [!] get_comic_name error: {e}")
    return None


def get_chapters(sess: requests.Session | None, cid: str) -> list:
    """Fetch and return sorted chapter list."""
    sess = _sess(sess)
    get_rate_limiter().wait()
    r = robust_get(
        sess, f"{_base()}/api/comic/{cid}/chapters", _api_timeout(),
        retries=CHAPTERS_RETRIES, backoff=0.25,
    )
    r.raise_for_status()
    d = r.json()
    if d.get("code") != 200:
        raise RuntimeError(d.get("msg", "unknown API error"))
    chs = d["data"]["list"]
    chs.sort(key=lambda x: x.get("sortId", 0))
    return chs


def get_images(sess: requests.Session | None, ch_id: int, src: str) -> list:
    """Fetch all image URLs for a chapter from given source."""
    sess = _sess(sess)
    urls, page = [], 1
    while True:
        get_rate_limiter().wait()
        r = robust_get(
            sess,
            f"{_base()}/api/comic/image/{ch_id}"
            f"?page={page}&page_size={PAGE_SIZE}"
            f"&image_source={requests.utils.quote(src)}",
            _api_timeout(), retries=IMAGES_RETRIES, backoff=0.25,
        )
        r.raise_for_status()
        d = r.json()
        if d.get("code") != 200:
            raise RuntimeError(d.get("msg", "?"))
        urls.extend(img["url"] for img in d["data"]["images"])
        tp = d["data"]["pagination"].get("total_pages", 1)
        if page >= tp:
            break
        page += 1
    return urls


def get_images_with_fallback(
    sess: requests.Session | None, ch_id: int,
    primary_src: str, fallback_srcs: list, max_retries: int,
) -> tuple[list | None, str | None]:
    """Try multiple image sources with retry. Returns (urls, used_source) or (None, None)."""
    sess = _sess(sess)
    all_sources = [primary_src] + [
        s for s in fallback_srcs if s != primary_src
    ]
    for attempt in range(max_retries + 1):
        for src in all_sources:
            try:
                urls = get_images(sess, ch_id, src)
                if urls:
                    return urls, src
            except Exception:
                continue
        if attempt < max_retries:
            time.sleep(0.6 + attempt * 1.0)
    return None, None


def _fetch_chapters_with_recovery(sess, cid: str) -> list:
    """get_chapters(), swapping to a re-discovered mirror if the API 404s.

    --info and interactive mode go through here. Without this they died on a
    dead mirror even though a perfectly good one existed a second later.
    """
    try:
        return get_chapters(sess, cid)
    except Exception as first:
        err = str(first)
        transient = ("404", "403", "Not Found", "SSL", "Connection",
                     "Max retries", "timed out")
        if not any(k in err for k in transient):
            raise
        try:
            new_base = _cfg.redetect_domain(force=True)
        except Exception:
            raise first
        if _cfg.last_detect_failed():
            raise first
        tprint(f"\n  [>] 镜像不可用，已切换到 {new_base} 重试...", end=" ")
        return get_chapters(sess, cid)


def show_comic_info(cid: str):
    """Print comic info to console (used by --info and interactive mode)."""
    sess = make_session(pool=5)
    tprint("=" * 60)
    tprint("  Manwa Downloader v5")
    tprint("=" * 60)
    tprint("[>] Getting comic info...", end=" ", flush=True)
    name = get_comic_name(sess, cid)
    tprint(f"\n  Name: {name}" if name else f"\n  Name: (unknown, ID={cid})")
    tprint("[>] Getting chapter list...", end=" ", flush=True)
    t0 = time.time()
    try:
        chs = _fetch_chapters_with_recovery(sess, cid)
    except Exception as e:
        tprint(f"\nX {e}")
        sys.exit(1)
    # The title lookup above ran before recovery may have swapped the mirror.
    if not name:
        name = get_comic_name(sess, cid)
        if name:
            tprint(f"\n  Name: {name}")
    total = len(chs)
    real = sum(1 for c in chs if c.get("picCount", 0) > 1)
    tprint(f"  Manga: {total} entries ({real} chapters) ({fmt_t(time.time()-t0)})")
    tprint("\n  Chapter preview:")
    count = 0
    for c in chs:
        ct, pics = c["title"], c.get("picCount", 0)
        if pics <= 1 and any(k in ct for k in ("公告", "贺图", "活动")):
            continue
        count += 1
        tprint(f"    [{count:4d}] {ct[:28]:28s} {pics:3d}P")
        if count >= 10:
            break
    tprint(f"\n  Tip: enter chapter number to download")
    tprint(f"  e.g. ManwaDownloader.exe {cid} --start 1 --end 1")
    tprint("=" * 60)
