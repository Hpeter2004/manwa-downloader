"""Utility functions — pure, no side effects."""

import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

# Image magic bytes → extension. Must stay in sync with crypto_utils._IMAGE_MAGIC:
# a format detected there but missing here would be written with a wrong suffix
# and then never counted as "already downloaded" by count_valid_images().
_MAGIC_EXT = {
    b"\xff\xd8\xff": ".jpg",
    b"\x89PNG": ".png",
    b"GIF8": ".gif",
    b"RIFF": ".webp",
    b"BM": ".bmp",
    b"\x00\x00\x01\x00": ".ico",
}

# Every suffix the downloader may write; used by the resume check. Anything
# missing here makes that chapter look incomplete forever and re-downloaded on
# every run.
IMAGE_SUFFIXES = frozenset(_MAGIC_EXT.values()) | {".jpeg", ".avif"}

# Content-Type → extension
_CT_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/avif": ".avif",
}

# HTML/text signatures (error pages masquerading as images)
_HTML_SIGS = (b"<!DOCTYPE", b"<html", b"<?xml", b"<htm")


def sname(name: str, n: int = 50) -> str:
    """Safe filename with CJK-aware truncation."""
    x = re.sub(r'[<>:"/\\|?*\n\r\t]', "_", name)
    x = re.sub(r'\s+', " ", x)
    x = x.strip().strip(".")
    if len(x) <= n:
        return x or "u"
    truncated = x[:n]
    tail = truncated[-12:]
    m = re.search(
        r'[，。！？、；：）\)」』}\s,.;:!?)\]}]'
        r'(?=[^，。！？、；：）\)」』}\s,.;:!?)\]}}]*$)',
        tail,
    )
    if m:
        cut = len(truncated) - 12 + m.end()
        return x[:cut].rstrip() + "\u2026"  # …
    return truncated.rstrip() + "\u2026"


def parse_cid(s: str) -> str:
    """Extract comic ID from URL or raw number."""
    m = re.search(r'/comic/(\d+)', s)
    if m:
        return m.group(1)
    m = re.search(r'^(\d+)$', s.strip())
    if m:
        return m.group(1)
    return ""


def ext(url: str) -> str:
    """Guess file extension from URL. Defaults to .jpg.
    Note: the CDN may serve WebP with .jpg URLs, so prefer
    get_ext_from_magic() or get_ext_from_ct() after downloading."""
    p = urlparse(url).path.rsplit(".", 1)[-1].split("?")[0]
    return f".{p}" if len(p) <= 5 else ".jpg"


def get_ext_from_magic(data: bytes) -> str | None:
    """Determine file extension from magic bytes. Returns None if unknown."""
    if len(data) < 4:
        return None
    for magic, ext in _MAGIC_EXT.items():
        if data.startswith(magic):
            return ext
    return None


def get_ext_from_ct(content_type: str) -> str | None:
    """Determine file extension from HTTP Content-Type header."""
    if not content_type:
        return None
    ct = content_type.split(";")[0].strip().lower()
    return _CT_EXT.get(ct)


def is_html_response(data: bytes) -> bool:
    """Check if downloaded content is actually an HTML error page."""
    if len(data) < 8:
        return False
    head = data[:128].lstrip()
    for sig in _HTML_SIGS:
        if head.lower().startswith(sig.lower()):
            return True
    return False


def determine_ext(
    data: bytes,
    content_type: str = "",
    url_ext: str = ".jpg",
) -> str:
    """Determine the best file extension for downloaded image data.

    Priority: magic bytes > Content-Type header > URL extension > default .jpg
    """
    # 1. Magic bytes (most reliable)
    ext_magic = get_ext_from_magic(data)
    if ext_magic:
        return ext_magic

    # 2. Content-Type header
    ext_ct = get_ext_from_ct(content_type)
    if ext_ct:
        return ext_ct

    # 3. URL-based (least reliable, but better than nothing)
    return url_ext


def fmt_t(sec: float) -> str:
    """Format seconds as human-readable string."""
    if sec < 60:
        return f"{sec:.0f}s"
    m, s = divmod(int(sec), 60)
    return f"{m}m{s:02d}s"


def fmt_size(nbytes: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


def count_valid_images(folder: Path) -> int:
    """Fast count of valid images (>512 bytes) in folder."""
    if not folder.is_dir():
        return 0
    c = 0
    try:
        for f in folder.iterdir():
            if f.suffix.lower() in IMAGE_SUFFIXES and f.stat().st_size > 512:
                c += 1
    except OSError:
        pass
    return c


def total_dir_size_fast(path: Path) -> int:
    """Fast directory size using scandir instead of rglob."""
    total = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat().st_size
                    elif entry.is_dir(follow_symlinks=False):
                        total += total_dir_size_fast(Path(entry.path))
                except OSError:
                    pass
    except OSError:
        pass
    return total
