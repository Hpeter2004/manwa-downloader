#!/usr/bin/env python3
"""Repair tool for existing downloaded manga images.

Fixes three issues from v4.0:
  1. WebP images saved with .jpg extension → rename to .webp
  2. AES-encrypted images → decrypt in-place
  3. Orphaned .tmp files → clean up

Usage:
    python repair_existing.py [directory]

If no directory is given, repairs all comics under the current directory.
Dry-run mode (--dry-run) shows what would be done without making changes.
"""

import argparse
import os
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from manhua_download.crypto_utils import try_decrypt_if_needed, is_encrypted
from manhua_download.utils import get_ext_from_magic, determine_ext


def repair_file(filepath: Path, dry_run: bool = False) -> str:
    """Repair a single image file. Returns action taken or 'skip'/'error'."""
    try:
        size = filepath.stat().st_size
        if size < 512:
            return "too_small"

        with open(filepath, "rb") as f:
            data = f.read()

        action = None

        # Check 1: Is it encrypted?
        if is_encrypted(data):
            data, decrypted = try_decrypt_if_needed(data)
            if decrypted:
                if not dry_run:
                    with open(filepath, "wb") as f:
                        f.write(data)
                action = "decrypted"
                # Fall through to check extension (decrypted data may be WebP)

        # Check 2: Wrong extension?
        correct_ext = get_ext_from_magic(data)
        if correct_ext and filepath.suffix.lower() != correct_ext:
            new_path = filepath.with_suffix(correct_ext)
            if not dry_run:
                # Don't overwrite existing correct file
                if new_path.exists():
                    if new_path.stat().st_size > 512:
                        filepath.unlink()  # Remove duplicate with wrong ext
                        return "dedup_removed"
                os.replace(str(filepath), str(new_path))
            return f"renamed_to_{correct_ext}" if not action else f"{action}+renamed_to_{correct_ext}"

        return action or "skip"
    except Exception as e:
        return f"error:{e}"


def repair_directory(root: Path, dry_run: bool = False) -> dict:
    """Repair all images in a directory tree. Returns stats dict."""
    stats = {
        "decrypted": 0,
        "renamed": 0,
        "dedup_removed": 0,
        "tmp_cleaned": 0,
        "skipped": 0,
        "errors": 0,
        "too_small": 0,
    }

    # Fix .jpg/.jpeg/.png/.webp/.gif files
    image_files = []
    for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp", "*.gif", "*.bmp"):
        image_files.extend(root.rglob(ext))

    total = len(image_files)
    for i, fp in enumerate(image_files):
        if i % 50 == 0:
            print(f"  Processing... {i}/{total}", end="\r")

        action = repair_file(fp, dry_run=dry_run)

        if "decrypted" in action:
            stats["decrypted"] += 1
            print(f"  [DECRYPT] {fp}")
        if "renamed_to" in action:
            stats["renamed"] += 1
            ext = action.split("renamed_to_")[-1] if "renamed_to_" in action else "?"
            print(f"  [RENAME]  {fp} -> {ext}")
        if action == "dedup_removed":
            stats["dedup_removed"] += 1
            print(f"  [DEDUP]   {fp} (removed duplicate)")
        if action == "skip":
            stats["skipped"] += 1
        if action == "too_small":
            stats["too_small"] += 1
        if action.startswith("error"):
            stats["errors"] += 1
            print(f"  [ERROR]   {fp}: {action}")

    # Clean up .tmp files
    tmp_files = list(root.rglob("*.tmp"))
    for fp in tmp_files:
        try:
            if not dry_run:
                fp.unlink()
            stats["tmp_cleaned"] += 1
        except OSError:
            stats["errors"] += 1
    if tmp_files:
        print(f"  [CLEAN]   {len(tmp_files)} .tmp files removed")

    return stats


def main():
    ap = argparse.ArgumentParser(
        description="Repair manga downloader images (decrypt, fix extensions, clean .tmp)"
    )
    ap.add_argument(
        "directory", nargs="?", default=".",
        help="Directory to repair (default: current directory)"
    )
    ap.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without making changes"
    )
    args = ap.parse_args()

    root = Path(args.directory).resolve()
    if not root.is_dir():
        print(f"Error: '{root}' is not a directory")
        sys.exit(1)

    mode = "DRY RUN" if args.dry_run else "REPAIR"
    print(f"=== Manwa Image Repair Tool ({mode}) ===")
    print(f"Directory: {root}")
    print()

    stats = repair_directory(root, dry_run=args.dry_run)

    print()
    print("=== Summary ===")
    print(f"  Decrypted:      {stats['decrypted']}")
    print(f"  Renamed:        {stats['renamed']}")
    print(f"  Dups removed:   {stats['dedup_removed']}")
    print(f"  .tmp cleaned:   {stats['tmp_cleaned']}")
    print(f"  Skipped (OK):   {stats['skipped']}")
    print(f"  Too small:      {stats['too_small']}")
    print(f"  Errors:         {stats['errors']}")

    if args.dry_run:
        print("\n  (Dry run — no files were modified)")


if __name__ == "__main__":
    main()
