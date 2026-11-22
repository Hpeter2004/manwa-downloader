"""Comic registry + live table — one comic name maps to exactly one Comic ID.

Why this exists: the downloader only ever takes a Comic ID, and the title only
survives inside a folder name truncated by ``sname(name, 30)`` — so "which
comics do I have, and what are their IDs" was unanswerable after the fact.

Two invariants, both enforced by ``ComicRegistry.upsert()``:

1. **name -> cid is unique.** The first Comic ID that claims a title owns it. A
   second ID reporting the same title is stored as an alias of the first one,
   never as a second row.
2. **cid -> name is unique.** When the site renames a comic the new title
   replaces the old one, instead of leaving two names pointing at one ID.

The file lives next to the other runtime caches (``.domain_cache.json``) and is
written atomically (.tmp + ``os.replace``), so a Ctrl+C mid-write cannot
truncate it.
"""

import json
import os
import re
import threading
import time
import unicodedata
from pathlib import Path

from .config import data_dir, logger, tprint

REGISTRY_FILE = ".comics.json"
REGISTRY_VERSION = 1

try:
    from rich import box
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    HAS_RICH_TABLE = True
    console = Console()
except ImportError:
    HAS_RICH_TABLE = False
    console = None

# Live view cadence: how often the file is re-checked for outside writes.
POLL_INTERVAL = 0.25
# A name can be long; the table clips it, the registry never truncates.
NAME_MAX_WIDTH = 40


# ============================================================
# Helpers
# ============================================================

def registry_path() -> Path:
    """Location of the comic table file (same dir as the other caches)."""
    return data_dir() / REGISTRY_FILE


def _key(name: str) -> str:
    """Matching key for a title — collapse whitespace, keep everything else.

    Site titles carry stray full-width spaces and trailing blanks; two runs that
    differ only in whitespace must land on the same row.
    """
    return re.sub(r"\s+", " ", (name or "").strip())


def _cid_sort(cid: str):
    """Numeric IDs first in numeric order, the rest alphabetically."""
    return (0, int(cid), "") if cid.isdigit() else (1, 0, cid)


def _fmt_ts(ts: float) -> str:
    if not ts:
        return "-"
    try:
        return time.strftime("%Y-%m-%d", time.localtime(ts))
    except (OSError, ValueError, OverflowError):
        return "-"


def _disp_width(s: str) -> int:
    """Terminal cell width — CJK glyphs occupy two columns."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
               for c in s)


def _pad(s: str, width: int, align: str = "left") -> str:
    fill = " " * max(0, width - _disp_width(s))
    return fill + s if align == "right" else s + fill


# ============================================================
# Registry
# ============================================================

class ComicRegistry:
    """Comic list keyed by Comic ID, persisted as JSON. Thread-safe.

    Every mutation rewrites the file immediately: a second process (``--watch``
    in another terminal, or an interactive session) only learns about changes
    through the file, so lazy flushing would make the table feel stale.
    """

    __slots__ = ("_path", "_lock", "_comics", "_version", "_mtime")

    def __init__(self, path: Path | None = None):
        self._path = Path(path) if path is not None else registry_path()
        self._lock = threading.RLock()
        self._comics: dict[str, dict] = {}     # cid -> entry
        self._version = 0
        self._mtime = 0.0
        self.load()

    # ---------- persistence ----------

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> bool:
        """Re-read the file if it changed on disk. True when content changed."""
        with self._lock:
            try:
                mtime = self._path.stat().st_mtime
            except OSError:
                return False
            if mtime == self._mtime:
                return False
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("comic registry unreadable: %s", exc)
                return False
            if not isinstance(raw, dict):
                return False

            comics: dict[str, dict] = {}
            by_name: dict[str, str] = {}
            for item in raw.get("comics") or []:
                if not isinstance(item, dict):
                    continue
                cid = str(item.get("cid", "")).strip()
                name = _key(str(item.get("name", "")))
                if not cid or not name:
                    continue
                # A hand-edited file may violate the invariant; repair it here
                # with the same rule upsert() uses instead of trusting the disk.
                owner = by_name.get(name)
                if owner is not None and owner != cid:
                    aliases = comics[owner].setdefault("aliases", [])
                    if cid not in aliases:
                        aliases.append(cid)
                    logger.warning(
                        "registry: name %r already bound to id %s, id %s -> alias",
                        name, owner, cid,
                    )
                    continue
                by_name[name] = cid
                comics[cid] = {
                    "cid": cid,
                    "name": name,
                    "chapters": max(0, int(item.get("chapters") or 0)),
                    "updated": float(item.get("updated") or 0),
                    "aliases": [str(a) for a in (item.get("aliases") or [])],
                }

            self._comics = comics
            self._mtime = mtime
            self._version += 1
            return True

    def save(self) -> bool:
        """Write to disk atomically. Never raises."""
        with self._lock:
            return self._save_locked()

    def _save_locked(self) -> bool:
        data = {
            "version": REGISTRY_VERSION,
            "comics": [self._comics[c] for c in sorted(self._comics, key=_cid_sort)],
        }
        tmp = self._path.with_name(self._path.name + ".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
            self._mtime = self._path.stat().st_mtime
            return True
        except OSError as exc:
            logger.warning("comic registry write failed: %s", exc)
            try:
                tmp.unlink()
            except OSError:
                pass
            return False

    def _commit(self):
        """Bump the revision and flush (caller holds the lock)."""
        self._version += 1
        self._save_locked()

    # ---------- reads ----------

    def __len__(self) -> int:
        return len(self._comics)

    @property
    def version(self) -> int:
        """In-memory revision — the live view redraws when this moves."""
        return self._version

    def entries(self) -> list[dict]:
        with self._lock:
            return [
                {**self._comics[c], "aliases": list(self._comics[c]["aliases"])}
                for c in sorted(self._comics, key=_cid_sort)
            ]

    def get_cid(self, name: str) -> str | None:
        """The Comic ID a title is bound to, if any."""
        name = _key(name)
        with self._lock:
            return self._owner_of(name)

    def get_name(self, cid: str) -> str | None:
        with self._lock:
            entry = self._comics.get(str(cid).strip())
            return entry["name"] if entry else None

    def changed_on_disk(self) -> bool:
        """True when another process rewrote the file."""
        try:
            return self._path.stat().st_mtime != self._mtime
        except OSError:
            return False

    def _owner_of(self, name: str) -> str | None:
        for cid, entry in self._comics.items():
            if entry["name"] == name:
                return cid
        return None

    # ---------- writes ----------

    def upsert(self, cid: str, name: str) -> str:
        """Bind ``cid`` to ``name`` while keeping name -> cid unique.

        Returns the Comic ID the title ends up bound to: the given one on
        success, or the already-registered ID when the title was taken.
        """
        cid = str(cid).strip()
        name = _key(name)
        if not cid or not name:
            return cid

        with self._lock:
            now = time.time()
            owner = self._owner_of(name)

            # 1) Title belongs to another ID -> keep the mapping, record alias.
            if owner is not None and owner != cid:
                target = self._comics[owner]
                if cid not in target["aliases"]:
                    target["aliases"].append(cid)
                    logger.warning(
                        "comic name %r already bound to id %s; id %s kept as alias",
                        name, owner, cid,
                    )
                target["updated"] = now
                self._commit()
                return owner

            entry = self._comics.get(cid)
            # 2) Known ID -> refresh timestamp, follow a rename.
            if entry is not None:
                entry["name"] = name
                entry["updated"] = now
                self._commit()
                return cid

            # 3) New ID -> new row.
            self._comics[cid] = {
                "cid": cid,
                "name": name,
                "chapters": 0,
                "updated": now,
                "aliases": [],
            }
            self._commit()
            return cid

    def set_chapters(self, cid: str, chapters: int) -> None:
        """Record how many chapters the comic currently has (display only)."""
        cid = str(cid).strip()
        chapters = max(0, int(chapters))
        with self._lock:
            entry = self._comics.get(cid)
            if entry is None or entry["chapters"] == chapters:
                return
            entry["chapters"] = chapters
            entry["updated"] = time.time()
            self._commit()

    def remove(self, cid: str) -> bool:
        """Drop one row. False when the ID was never registered."""
        cid = str(cid).strip()
        with self._lock:
            if self._comics.pop(cid, None) is None:
                return False
            self._commit()
            return True


# ============================================================
# Module-level singleton (same pattern as config.get_rate_limiter)
# ============================================================

_registry: ComicRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> ComicRegistry:
    """Shared registry — created lazily so data_dir() resolves at call time."""
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = ComicRegistry()
        return _registry


def remember(cid: str, name: str | None, chapters: int = 0) -> None:
    """Record one comic from the download path. Best effort — never raises.

    A broken/readonly registry file must not abort a download that is
    otherwise fine, so every failure is downgraded to a log line.
    """
    if not name or not str(name).strip():
        return              # no title yet: leave it for the next run
    try:
        registry = get_registry()
        bound = registry.upsert(cid, name)
        if chapters > 0 and bound == str(cid).strip():
            registry.set_chapters(cid, chapters)
    except Exception as exc:                    # noqa: BLE001
        logger.warning("comic registry update failed: %s", exc)


# ============================================================
# Table rendering
# ============================================================

HEADERS = ("#", "Comic ID", "漫画名称", "章节", "最后使用")


def _row(i: int, entry: dict) -> tuple:
    return (
        str(i),
        entry["cid"],
        entry["name"],
        str(entry["chapters"]) if entry["chapters"] else "-",
        _fmt_ts(entry["updated"]),
    )


def _plain_table(entries: list, title: str = "漫画表格") -> str:
    """Fixed-width fallback for when rich is unavailable."""
    rows = [_row(i, e) for i, e in enumerate(entries, 1)]
    widths = [
        max([_disp_width(HEADERS[c])] + [_disp_width(r[c]) for r in rows])
        for c in range(len(HEADERS))
    ]
    lines = [title, ""]
    lines.append("  " + "  ".join(
        _pad(HEADERS[c], widths[c]) for c in range(len(HEADERS))
    ))
    lines.append("  " + "  ".join("-" * widths[c] for c in range(len(HEADERS))))
    for r in rows:
        cells = [
            _pad(r[0], widths[0], "right"),
            _pad(r[1], widths[1], "right"),
            _pad(r[2], widths[2]),
            _pad(r[3], widths[3], "right"),
            _pad(r[4], widths[4]),
        ]
        lines.append("  " + "  ".join(cells))
    if not rows:
        lines.append("  （还没有记录，下载任意一本漫画后自动登记）")
    return "\n".join(lines)


def build_table(entries: list, title: str = "漫画表格"):
    """rich Table for the comic list."""
    table = Table(
        title=title,
        title_justify="left",
        box=box.SIMPLE_HEAD,
        header_style="bold cyan",
        pad_edge=False,
    )
    table.add_column("#", justify="right", style="dim", no_wrap=True)
    table.add_column("Comic ID", justify="right", style="bold")
    table.add_column("漫画名称", overflow="ellipsis", max_width=NAME_MAX_WIDTH)
    table.add_column("章节", justify="right")
    table.add_column("最后使用", style="dim", no_wrap=True)

    if not entries:
        table.add_row("-", "-", "（暂无记录，下载后自动登记）", "-", "-")
        return table
    for i, entry in enumerate(entries, 1):
        table.add_row(*_row(i, entry))
    return table


def _alias_note(entries: list) -> str:
    """Warn (once) when a title was claimed by more than one ID."""
    n = sum(len(e["aliases"]) for e in entries)
    if not n:
        return ""
    return (f"{n} 个重复标题已按「先登记优先」合并，其余 Comic ID 记为别名"
            f"（只保留一条名称映射）")


def show_comic_table(watch: bool = False) -> None:
    """Print the comic table once, or keep it live until Ctrl+C."""
    registry = get_registry()
    registry.load()             # pick up anything another process wrote
    if watch:
        ComicTableLive(registry).run()
        return

    entries = registry.entries()
    if HAS_RICH_TABLE:
        console.print(build_table(entries))
        console.print(f"[dim]  共 {len(entries)} 本 · {registry.path}[/dim]")
        note = _alias_note(entries)
        if note:
            console.print(f"[yellow]  {note}[/yellow]")
    else:
        tprint(_plain_table(entries))
        tprint(f"  共 {len(entries)} 本 · {registry.path}")
        note = _alias_note(entries)
        if note:
            tprint(f"  {note}")


class ComicTableLive:
    """Re-render the table whenever the comic data changes.

    Two change sources, one redraw path: mutations in this process bump
    ``version``; writes from *another* process are caught by polling the file
    mtime. That is what makes the table update while a download runs in a
    second terminal.
    """

    __slots__ = ("_registry", "_fps")

    def __init__(self, registry: ComicRegistry, refresh_per_second: int = 4):
        self._registry = registry
        self._fps = max(1, int(refresh_per_second))

    def run(self) -> None:
        # rich's Live only paints on a real terminal; on a pipe / redirected
        # output it silently draws nothing, so fall back to append mode there.
        if HAS_RICH_TABLE and console.is_terminal:
            self._run_rich()
        else:
            self._run_plain()

    def _snapshot(self):
        entries = self._registry.entries()
        return build_table(entries) if HAS_RICH_TABLE else _plain_table(entries)

    def _emit(self, renderable) -> None:
        if HAS_RICH_TABLE:
            console.print(renderable)
        else:
            tprint(renderable)

    def _run_rich(self) -> None:
        console.print("[dim]实时漫画表格 · 数据变化自动刷新 · Ctrl+C 退出[/dim]")
        shown = -1
        with Live(
            self._snapshot(),
            console=console,
            refresh_per_second=self._fps,
            vertical_overflow="visible",
        ) as live:
            try:
                while True:
                    if self._registry.load():    # someone else wrote the file
                        shown = -1
                    if self._registry.version != shown:
                        live.update(self._snapshot())
                        shown = self._registry.version
                    time.sleep(POLL_INTERVAL)
            except KeyboardInterrupt:
                pass

    def _run_plain(self) -> None:
        hint = "实时漫画表格 · 数据变化时追加打印 · Ctrl+C 退出"
        if HAS_RICH_TABLE:
            self._emit(f"[dim]{hint}[/dim]")
        else:
            self._emit(hint)
        shown = -1
        try:
            while True:
                if self._registry.load():
                    shown = -1
                if self._registry.version != shown:
                    self._emit(self._snapshot())
                    shown = self._registry.version
                time.sleep(POLL_INTERVAL * 2)
        except KeyboardInterrupt:
            pass
        self._emit("\n已退出")
