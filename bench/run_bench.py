"""Benchmark harness — runs the downloader against the local fake server.

Usage:  python bench/run_bench.py [port] [--workers N] [--ch-workers N]
Prints total elapsed + effective throughput.
"""

import argparse
import asyncio
import os
import shutil
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from manhua_download import config as cfg  # noqa: E402
from manhua_download import api as api_mod  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", type=int, default=8899)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--ch-workers", type=int, default=None)
    ap.add_argument("--out", default=str(ROOT / "bench" / "_out"))
    a = ap.parse_args()

    base = f"http://127.0.0.1:{a.port}"
    cfg.BASE_URL = base
    cfg.IMAGE_SOURCES[:] = [base]
    cfg.HEADERS["Referer"] = base + "/"

    out_dir = Path(a.out)
    if not os.environ.get("BENCH_KEEP"):
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    from manhua_download.cli import async_main
    from manhua_download.config import load_config

    jcfg = load_config()
    args = types.SimpleNamespace(
        comic="http://127.0.0.1/comic/1",
        start=1, end=0, source=0,
        workers=a.workers if a.workers is not None else jcfg.get("workers", 15),
        ch_workers=a.ch_workers if a.ch_workers is not None else jcfg.get("ch_workers", 2),
        retry=2, no_resume=False, output=str(out_dir), info=False,
    )

    t0 = time.time()
    asyncio.run(async_main(args))
    ela = time.time() - t0

    files = list(out_dir.rglob("*.webp")) + list(out_dir.rglob("*.jpg"))
    total_bytes = sum(f.stat().st_size for f in files)
    print("\n" + "=" * 58)
    print(f"BENCH RESULT: {len(files)} images in {ela:.2f}s")
    if ela > 0:
        print(f"  throughput: {len(files) / ela:.1f} img/s | "
              f"{total_bytes / ela / 1e6:.2f} MB/s")
    print("=" * 58)


if __name__ == "__main__":
    main()
