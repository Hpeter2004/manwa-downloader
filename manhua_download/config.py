"""Configuration, domain detection, session management, logging."""

import base64
import json
import logging
import os
import re
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import (
    ConnectionError as ReqConnectionError,
    SSLError as ReqSSLError,
    Timeout as ReqTimeout,
)
from urllib3.util.retry import Retry


# === Logging (thread-safe) ===
_log_handler = logging.StreamHandler(sys.stdout)
_log_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
logger = logging.getLogger("manwa")
logger.addHandler(_log_handler)
logger.setLevel(logging.INFO)


def setup_log_file(log_dir: Path | None = None):
    """Add file handler for error/warning logs. Creates manwa_download.log."""
    if log_dir is None:
        log_dir = data_dir()
    try:
        log_path = log_dir / "manwa_download.log"
        fh = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        fh.setLevel(logging.WARNING)  # Only warnings+ to file
        logger.addHandler(fh)
        return log_path
    except OSError:
        return None


# === Thread-safe print fallback (for non-logging output) ===
_print_lock = threading.Lock()


def tprint(*args, **kwargs):
    """Thread-safe print — use only when logger is too heavy."""
    with _print_lock:
        print(*args, **kwargs)


# === Known domains (probed at startup) ===
# The site rotates its domain constantly (it's a piracy mirror that gets taken
# down), so a hardcoded list is always stale sooner or later. This list is only
# a hint: auto_detect_domain() re-discovers live mirrors from the publisher's
# "走失页" (lost page) and from the intermediate route pages.
#
# Verified alive 2026-10-28. The previous entries (manwaqi.cc, mwuu.cc,
# manwa01.cc) are all dead now — every /api/ call 404s or the host no longer
# resolves, which is exactly why the downloader "sometimes can't download".
KNOWN_DOMAINS = [
    "https://manwadj.cc",
    "https://manwadh.cc",
    "https://manwabb.cc",
    "https://manwaxu.cc",
    "https://manwakk.cc",
    "https://manwann.cc",
    "https://manwaqq.cc",
    "https://manwarr.cc",
    "https://manwayy.cc",
    "https://manwazz.cc",
    "https://mwmwbu.cc",
    "https://mwmwff.cc",
]

# --- Dynamic discovery ---------------------------------------------------
# 走失页: a static page that lists where the site moved to. It never dies
# because it's plain HTML on its own host.
LOST_PAGES = [
    "https://manwami.cc",
    "https://manwala.cc",
    "https://manwalu.cc",
]
# Route pages ("线路检测中...") embed the real mirror list as base64 in JS, e.g.
#   const e=["aHR0cHM6Ly9tYW53YWJiLmNj", ...].map((e=>atob(e)))
# and redirect the browser to whichever mirror answers first.
DISCOVER_TIMEOUT = (4, 8)
MAX_SEED_PAGES = 6              # route pages to open per detection round
# Hosts that show up in these pages but never serve the API.
DISCOVER_HOST_DENY = (
    "manwami.cc", "manwala.cc", "manwalu.cc",   # the lost pages themselves
    "mwapp.cc", "su7.la",                       # app download / shortener
    "aizhantj.com", "googletagmanager.com", "google-analytics.com",
    "cloudflare.com", "gstatic.com", "jquery.com",
)

# A domain is only "usable" if this endpoint answers with real JSON. The
# homepage is useless as a health check — parked / soft-404 mirrors return 200
# with an empty shell while every /api/ call 404s, which used to make the
# downloader pick a dead mirror and die right after startup.
PROBE_COMIC_ID = "1728"
PROBE_API_PATH = "/api/comic/{cid}/chapters"
PROBE_TIMEOUT = 2.5              # per-domain budget while scanning mirrors
PROBE_RETRIES = 1
DOMAIN_CACHE_TTL = 6 * 3600      # remember a working domain for 6 hours
DISCOVERY_CACHE_TTL = 3 * 3600   # re-scan the lost pages this often

# True when the last detection round found nothing alive — means the network
# is blocked rather than the mirror being wrong, so re-probing is pointless.
_detect_failed = False


def last_detect_failed() -> bool:
    return _detect_failed

# === Runtime config (populated by apply_config) ===
BASE_URL = KNOWN_DOMAINS[0]
# Optional proxy from config.json ("proxy": "http://127.0.0.1:7890").
# The mirror is Cloudflare-fronted and TLS handshakes from some networks are
# reset constantly; a proxy is the only real fix there.
PROXY: str | None = None
IMAGE_SOURCES = [
    "https://tu.mwzu.cc",
    "https://svip.mwtt.cc",
    "https://tu.mwla.cc",
    "https://fm.mwtt.cc",
]
PAGE_SIZE = 60
API_TIMEOUT = (10, 30)
IMG_TIMEOUT = (5, 25)       # (connect, sock_read)
IMG_TIMEOUT_FB = (4, 20)    # fallback: slightly shorter
CHAPTER_RETRY = 2
RATE_LIMIT_DELAY = 0.05     # seconds between API calls (burst-friendly)
DNS_CACHE_TTL = 300         # DNS cache: 5 minutes
KEEPALIVE_TIMEOUT = 90      # idle connection keep-alive in seconds
CONNECTOR_LIMIT_PER_HOST = 0  # 0 = no per-host limit (use total limit only)

# === Throughput tuning (v5) ===
DEFAULT_WORKERS = 64        # 全局同时下载的图片数（不再是「每章」）
DEFAULT_CH_WORKERS = 8      # 同时进行的章节数
MAX_WORKERS = 128
API_THREADS = 6             # 线程池：拉取章节图源列表
IO_THREADS = 12             # 线程池：落盘 + AES 解密
PIPELINE_AHEAD_MULT = 3     # 预取窗口 = ch_workers * 该值（章）

# Output root used when --output is not given. Set by apply_config() so the
# SIGINT cleanup path knows which tree to sweep for leftover .tmp files.
OUTPUT_DIR = Path(".")


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": UA,
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive",
}

# Data dir: next to the exe when frozen, project root when running from source.
# For a PyInstaller onefile build __file__ lives in the temp _MEIPASS dir, so
# resolving this at import time from __file__ would make an external
# config.json unreadable.
def data_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


def _find_config() -> Path:
    # 1) working directory (that's where 运行下载.bat cds to)
    cwd_cfg = Path.cwd() / "config.json"
    if cwd_cfg.is_file():
        return cwd_cfg
    # 2) next to the exe / project root
    local = data_dir() / "config.json"
    if local.is_file():
        return local
    # 3) bundled default (inside the exe)
    return Path(__file__).parent.parent / "config.json"


CONFIG_PATH = _find_config()


# === Session singleton ===
_session: requests.Session | None = None
_session_lock = threading.Lock()
_all_sessions: list = []
_tls_sessions = threading.local()


def _new_session(pool: int = 20) -> requests.Session:
    s = requests.Session()
    # Transport retries are owned by robust_get() alone. urllib3's own retry
    # used to sit underneath it, so every logical attempt was silently
    # multiplied (3 x 3) — a dead network took ~9s per call instead of ~2s.
    # Keep the adapter for connection pooling and status-code retries only.
    retry = Retry(
        total=2,
        connect=0,          # never retry a refused/reset connection here
        read=0,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(
        max_retries=retry,
        pool_connections=15,
        pool_maxsize=pool,
        pool_block=False,
    )
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update(HEADERS)
    if PROXY:
        s.proxies.update({"http": PROXY, "https": PROXY})
    _all_sessions.append(s)
    return s


def make_session(pool: int = 20) -> requests.Session:
    """Create or return cached session (main-thread singleton)."""
    global _session
    with _session_lock:
        if _session is not None and not _closed:
            return _session
        _session = _new_session(pool)
        return _session


def thread_session() -> requests.Session:
    """Per-thread requests.Session — safe for concurrent API fetching."""
    s = getattr(_tls_sessions, "sess", None)
    if s is None:
        s = _new_session(pool=4)
        _tls_sessions.sess = s
    return s


_closed = False


# === Resilient HTTP GET ===
_TRANSIENT_HTTP = (ReqSSLError, ReqConnectionError, ReqTimeout)


def robust_get(
    sess: requests.Session, url: str, timeout, retries: int = 4,
    backoff: float = 0.5, **kwargs,
):
    """GET that retries transport failures (TLS reset, refused, timeout).

    The mirror is Cloudflare-fronted and a big share of TLS handshakes from
    some networks abort with SSLEOFError. Each retry gets a fresh connection
    and often succeeds — without this, one unlucky handshake decided whether
    the whole run worked.
    """
    total = max(1, int(retries))
    last: Exception | None = None
    for attempt in range(total):
        try:
            return sess.get(url, timeout=timeout, **kwargs)
        except _TRANSIENT_HTTP as exc:
            last = exc
            if attempt + 1 >= total:
                break
            time.sleep(backoff * (attempt + 1))
    assert last is not None
    raise last


def close_session():
    """Close every session created so far."""
    global _session, _closed
    with _session_lock:
        for s in _all_sessions:
            try:
                s.close()
            except Exception:
                pass
        _all_sessions.clear()
        _session = None
        _tls_sessions.sess = None
        _closed = True


# === Config loading ===
def load_config() -> dict:
    """Read config.json, tolerating a UTF-8 BOM and a missing/unparsable file.

    A BOM is what Notepad writes by default. json.load() then raises
    JSONDecodeError, which used to be swallowed into an empty dict — so the
    user's workers/proxy silently stopped applying and the whole file looked
    ignored. Read with utf-8-sig instead.
    """
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        pass
    return {}


# === Dynamic discovery from the publisher's own pages =====================
# The site moves to a new domain every few weeks. Rather than chasing it by
# hand, read the two pages the site itself publishes for exactly this purpose:
#
#   lost page  (走失页, e.g. manwami.cc)  -> plain <a href> links to mirrors
#   route page (线路检测中..., mwmw.cc)   -> base64 array of mirror URLs in JS
#
# Both are ordinary static HTML on hosts that don't rotate, so discovery keeps
# working long after the hardcoded KNOWN_DOMAINS list has gone stale.

_HOST_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_HREF_RE = re.compile(r"""href=["'](https?://[A-Za-z0-9.\-]+)""", re.I)
# const e=["aHR0cHM6...","..."]  — base64 domain list
_B64_ARRAY_RE = re.compile(r'\[\s*((?:"[A-Za-z0-9+/=]{8,}"\s*,?\s*)+)\]')
_B64_TOKEN_RE = re.compile(r'"([A-Za-z0-9+/=]{8,})"')


def _b64_urls(html: str) -> list:
    """Pull base64-encoded URLs out of a route page's JS array."""
    out = []
    for array in _B64_ARRAY_RE.finditer(html or ""):
        for token in _B64_TOKEN_RE.findall(array.group(1)):
            try:
                raw = base64.b64decode(token + "=" * (-len(token) % 4))
                url = raw.decode("utf-8", "ignore").strip().rstrip("/")
            except Exception:
                continue
            if _HOST_RE.match(url):
                out.append(url)
    return out


def _plain_links(html: str) -> list:
    """Mirror-looking <a href> targets on a lost page."""
    return [
        m.group(1).rstrip("/")
        for m in _HREF_RE.finditer(html or "")
        if _HOST_RE.match(m.group(1))
    ]


def _host_of(url: str) -> str:
    return url.split("//", 1)[-1].split("/", 1)[0].lower()


def _is_denied(url: str) -> bool:
    host = _host_of(url)
    return any(host == d or host.endswith("." + d) for d in DISCOVER_HOST_DENY)


def _fetch_page(sess, url: str) -> str:
    """GET a static page and decode it. Returns '' on any failure."""
    try:
        r = robust_get(sess, url, DISCOVER_TIMEOUT, retries=1, backoff=0.2)
        if r.status_code != 200:
            return ""
        if "json" in (r.headers.get("Content-Type") or "").lower():
            return ""
        # The pages are UTF-8, but be forgiving: some mirrors serve GBK.
        r.encoding = r.apparent_encoding or "utf-8"
        return r.text or ""
    except Exception:
        return ""


def _scan_route_pages(sess, seeds: list) -> list:
    """Open seed pages in parallel and collect every mirror URL they mention."""
    found: list = []
    seeds = seeds[:MAX_SEED_PAGES]
    if not seeds:
        return found
    with ThreadPoolExecutor(max_workers=len(seeds), thread_name_prefix="discover") as pool:
        pages = list(pool.map(lambda u: (u, _fetch_page(sess, u)), seeds))
    for url, html in pages:
        if not html:
            continue
        for cand in _plain_links(html) + _b64_urls(html):
            if _is_denied(cand) or cand in found:
                continue
            found.append(cand)
    return found


def discover_domains() -> list:
    """Ask the site where it lives now. Returns candidate base URLs.

    Never raises: a network failure just yields an empty list, and detection
    falls back to the built-in KNOWN_DOMAINS.
    """
    sess = thread_session()
    candidates: list = []

    # 1) Lost pages are static and rarely change host; read them directly.
    for lost in LOST_PAGES:
        html = _fetch_page(sess, lost + "/")
        if not html:
            continue
        for link in _plain_links(html):
            if not _is_denied(link) and link not in candidates:
                candidates.append(link)

    # 2) Route pages (mwmw.cc and friends) carry the base64 mirror list.
    route_seeds = [u for u in candidates if not _is_denied(u)]
    for extra in ("https://mwmw.cc", "https://mwmw1.cc"):
        if extra not in route_seeds:
            route_seeds.append(extra)
    for cand in _scan_route_pages(sess, route_seeds):
        if cand not in candidates:
            candidates.append(cand)

    # 3) One level deeper: mirrors found above may themselves be route pages.
    deeper = [u for u in candidates if u not in LOST_PAGES][:MAX_SEED_PAGES]
    for cand in _scan_route_pages(sess, [u + "/" for u in deeper]):
        if cand not in candidates:
            candidates.append(cand)

    return [c for c in candidates if not _is_denied(c)]


def _discovery_cache_path() -> Path:
    return data_dir() / ".discovery_cache.json"


def _load_discovery_cache() -> list:
    try:
        d = json.loads(_discovery_cache_path().read_text(encoding="utf-8"))
        if time.time() - float(d.get("ts", 0)) < DISCOVERY_CACHE_TTL:
            doms = d.get("domains")
            if isinstance(doms, list):
                return [x for x in doms if isinstance(x, str) and x]
    except Exception:
        pass
    return []


def _save_discovery_cache(domains: list):
    try:
        _discovery_cache_path().write_text(
            json.dumps({"domains": domains, "ts": time.time()}), encoding="utf-8",
        )
    except OSError:
        pass


# === Domain auto-detection (API health check, parallel) ===
def _probe_domain(domain: str, timeout: float) -> tuple:
    """Probe a domain by calling the real chapter-list API.

    Returns (domain, ok). Only a 200 response with a non-empty JSON chapter
    list counts as alive — anything else (404, HTML error page, SSL hiccup,
    parked domain) is treated as unavailable.
    """
    base = domain.rstrip("/")
    url = f"{base}{PROBE_API_PATH.format(cid=PROBE_COMIC_ID)}"
    try:
        r = robust_get(
            thread_session(), url, timeout, retries=PROBE_RETRIES, backoff=0.25,
        )
        if r.status_code != 200:
            return domain, False
        d = r.json()
        return domain, d.get("code") == 200 and bool((d.get("data") or {}).get("list"))
    except Exception:
        return domain, False


def _domain_cache_path() -> Path:
    return data_dir() / ".domain_cache.json"


def _load_domain_cache() -> str | None:
    try:
        d = json.loads(_domain_cache_path().read_text(encoding="utf-8"))
        if time.time() - float(d.get("ts", 0)) < DOMAIN_CACHE_TTL:
            url = d.get("base_url")
            return url if isinstance(url, str) and url else None
    except Exception:
        pass
    return None


def _save_domain_cache(url: str):
    try:
        _domain_cache_path().write_text(
            json.dumps({"base_url": url, "ts": time.time()}), encoding="utf-8",
        )
    except OSError:
        pass


def _probe_many(domains: list, timeout: float) -> dict:
    """Probe every domain in parallel -> {domain: alive}."""
    alive: dict[str, bool] = {}
    if not domains:
        return alive
    with ThreadPoolExecutor(
        max_workers=min(len(domains), 16), thread_name_prefix="probe",
    ) as pool:
        futures = {pool.submit(_probe_domain, d, timeout): d for d in domains}
        for future in as_completed(futures):
            domain, ok = future.result()
            alive[domain] = ok
    return alive


def auto_detect_domain(timeout: float = PROBE_TIMEOUT, force: bool = False) -> str:
    """Find a domain whose API actually works.

    Order of preference:
      1. the cached domain, if it still answers;
      2. the built-in KNOWN_DOMAINS list;
      3. domains discovered live from the site's own lost / route pages.

    Step 3 is what keeps this working after the site moves: the hardcoded list
    is a hint, not the truth.
    """
    global _detect_failed
    tprint("[>] Detecting available domain...", end=" ", flush=True)

    # Fast path: a recently working domain — one API call instead of dozens.
    if not force:
        cached = _load_domain_cache()
        if cached:
            _, ok = _probe_domain(cached, timeout)
            if ok:
                _detect_failed = False
                tprint(f"OK -> {cached} (cached)")
                return cached
            tprint("cached domain stale, re-probing...", end=" ", flush=True)

    # 1) Built-in list first: it is curated and ordered, and usually hits.
    # PROBE_RETRIES=1 (not robust_get's default 4): a dozen dead mirrors
    # retried four times each is a long stall before the user learns anything.
    alive = _probe_many(KNOWN_DOMAINS, timeout)
    for d in KNOWN_DOMAINS:
        if alive.get(d):
            url = d.rstrip("/")
            _detect_failed = False
            tprint(f"OK -> {url}")
            _save_domain_cache(url)
            return url

    # 2) Whole list is dead — the site has moved. Ask the site itself.
    tprint("all known mirrors dead, discovering...", end=" ", flush=True)
    discovered = _load_discovery_cache()
    if not discovered:
        try:
            discovered = discover_domains()
        except Exception:
            discovered = []
        if discovered:
            _save_discovery_cache(discovered)
    fresh = [d for d in discovered if d not in KNOWN_DOMAINS]
    if fresh:
        alive2 = _probe_many(fresh, timeout)
        for d in fresh:
            if alive2.get(d):
                url = d.rstrip("/")
                _detect_failed = False
                tprint(f"OK -> {url} (discovered)")
                _save_domain_cache(url)
                return url

    # Nothing answered. A broken network is far more likely than a dead site,
    # so fall back to the primary domain and let robust_get keep retrying.
    _detect_failed = True
    fallback = KNOWN_DOMAINS[0].rstrip("/")
    tprint(f"all failed, fallback -> {fallback}")
    tprint("[!] 所有镜像都连不上：TLS 握手被中断，通常是网络受限或站点被干扰。")
    tprint("    若你有代理，在 config.json 里填：\"proxy\": \"http://127.0.0.1:7890\"")
    return fallback


def apply_config(detect: bool = True) -> dict:
    """Load config, detect/set domain, apply overrides.

    detect=False skips domain probing (used by --help, which needs no network).
    """
    global BASE_URL, PROXY, RATE_LIMIT_DELAY, IMG_TIMEOUT, IMG_TIMEOUT_FB, KEEPALIVE_TIMEOUT
    cfg = load_config()

    proxy = cfg.get("proxy")
    if isinstance(proxy, str) and proxy.strip():
        PROXY = proxy.strip()
        os.environ.setdefault("HTTP_PROXY", PROXY)
        os.environ.setdefault("HTTPS_PROXY", PROXY)
        tprint(f"[>] Using proxy: {PROXY}")

    if cfg.get("base_url"):
        BASE_URL = cfg["base_url"].rstrip("/")
        tprint(f"[>] Using configured domain: {BASE_URL}")
    elif detect:
        BASE_URL = auto_detect_domain()
    HEADERS["Referer"] = f"{BASE_URL}/"
    if "image_sources" in cfg and isinstance(cfg["image_sources"], list) and cfg["image_sources"]:
        IMAGE_SOURCES[:] = cfg["image_sources"]
    # Optional overrides
    if "rate_limit" in cfg:
        set_rate_limit(cfg["rate_limit"])
    if "connect_timeout" in cfg or "read_timeout" in cfg:
        connect = float(cfg.get("connect_timeout", IMG_TIMEOUT[0]))
        read = float(cfg.get("read_timeout", IMG_TIMEOUT[1]))
        IMG_TIMEOUT = (connect, read)
        IMG_TIMEOUT_FB = (max(2.0, connect - 1), max(5.0, read - 5))
    if "keepalive_timeout" in cfg:
        KEEPALIVE_TIMEOUT = int(cfg["keepalive_timeout"])
    return cfg


def set_output_dir(path) -> None:
    """Remember the output root so the SIGINT handler can sweep it for .tmp."""
    global OUTPUT_DIR
    try:
        OUTPUT_DIR = Path(path)
    except (TypeError, ValueError):
        OUTPUT_DIR = Path(".")


def redetect_domain(force: bool = True) -> str:
    """Re-run detection at runtime (used when the current mirror 404s).

    The discovery cache is dropped first: we only get here because the mirror
    we were told about has just failed, so a cached "working" list is worthless.
    """
    global BASE_URL
    if force:
        try:
            _discovery_cache_path().unlink()
        except OSError:
            pass
    BASE_URL = auto_detect_domain(force=force)
    HEADERS["Referer"] = f"{BASE_URL}/"
    return BASE_URL


# === Rate limiter ===
class RateLimiter:
    """Token bucket — allows a short burst, then enforces min_interval.

    A plain "sleep between calls" limiter serialises every API call behind a
    lock, which made the chapter prefetcher lag behind the downloader.
    """

    def __init__(self, min_interval: float = RATE_LIMIT_DELAY, burst: int = 8):
        self._interval = max(float(min_interval), 0.0)
        self._cap = max(1, int(burst))
        self._tokens = float(self._cap)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def set_interval(self, min_interval: float):
        with self._lock:
            self._interval = max(float(min_interval), 0.0)
            self._tokens = float(self._cap)

    def wait(self):
        """Block until a token is available, then consume one."""
        if self._interval <= 0:
            return
        delay = 0.0
        with self._lock:
            now = time.monotonic()
            self._tokens = min(
                self._cap, self._tokens + (now - self._last) / self._interval
            )
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
            else:
                # Reserve a token in the future; debit it now.
                delay = (1.0 - self._tokens) * self._interval
                self._tokens = 0.0
                self._last = now + delay
        if delay > 0:
            time.sleep(delay)


_limiter_singleton: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Shared limiter — created lazily so config overrides always apply."""
    global _limiter_singleton
    if _limiter_singleton is None:
        _limiter_singleton = RateLimiter()
    return _limiter_singleton


def set_rate_limit(min_interval: float):
    """Change the global API spacing (also affects the live limiter)."""
    global RATE_LIMIT_DELAY
    RATE_LIMIT_DELAY = float(min_interval)
    if _limiter_singleton is not None:
        _limiter_singleton.set_interval(min_interval)


# === Signal handling & cleanup ===
_cleanup_callbacks: list = []
_shutting_down = False


def register_cleanup(callback):
    """Register a function to call on shutdown (SIGINT / exit)."""
    _cleanup_callbacks.append(callback)


def cleanup_tmp_files(base_path: Path | None = None):
    """Remove all .tmp files under base_path (defaults to the output dir).

    The signal handler calls this with no argument; it used to return
    immediately, so a Ctrl+C left .tmp files behind and the "clean exit"
    promise in the docs was false. Fall back to the configured output dir,
    then to the working directory.
    """
    target = base_path if base_path is not None else OUTPUT_DIR
    target = Path(target)
    if not target.is_dir():
        return
    for f in target.rglob("*.tmp"):
        try:
            f.unlink()
        except OSError:
            pass


def setup_signal_handlers(loop=None):
    """Register SIGINT handler for graceful shutdown."""
    def _handler(signum, frame):
        global _shutting_down
        if _shutting_down:
            sys.exit(1)
        _shutting_down = True
        tprint("\n[*] Interrupted, cleaning up...")
        for cb in _cleanup_callbacks:
            try:
                cb()
            except Exception:
                pass
        close_session()
        cleanup_tmp_files()
        sys.exit(130)

    # Windows doesn't fully support SIGINT via signal.signal in all contexts
    try:
        signal.signal(signal.SIGINT, _handler)
    except (OSError, ValueError):
        pass  # Not supported on this platform / already set


def get_proxy() -> str | None:
    """Proxy for aiohttp (None = direct connection)."""
    return PROXY


def get_aiohttp_headers() -> dict:
    """Return headers dict for aiohttp, with dynamic Referer from current BASE_URL."""
    return {
        "User-Agent": UA,
        "Referer": f"{BASE_URL}/",
        "Accept": "*/*",
        "Accept-Encoding": "identity",   # images are already compressed; skip gzip
    }
