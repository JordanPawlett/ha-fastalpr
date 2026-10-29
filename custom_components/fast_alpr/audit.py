"""Bounded, local audit bundles containing the exact inference input bytes."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

SCAN_NAME = re.compile(r"^[0-9a-f]{32}$")


def save_scan(root: Path, report: dict, images: list[tuple[int, str, bytes]], keep: int):
    """Run in an executor, after recognition. Never retrieve another image.

    Only our UUID-named scan directories are pruned. Existing sample datasets
    and symlinks are never followed or removed by retention.
    """
    if not SCAN_NAME.fullmatch(report["capture_id"]):
        raise ValueError("Invalid capture ID")
    root.mkdir(parents=True, exist_ok=True)
    folder = root / report["capture_id"]
    folder.mkdir(exist_ok=False)
    try:
        for index, mime, content in images:
            suffix = "png" if mime == "image/png" else "jpg"
            (folder / f"frame_{index + 1:02d}.{suffix}").write_bytes(content)
        (folder / "scan.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    except Exception:
        # Remove partial private images before reporting the failed write.
        # Do not suppress cleanup failures: callers must know if files remain.
        shutil.rmtree(folder)
        raise
    scans = sorted(
        (p for p in root.iterdir() if SCAN_NAME.fullmatch(p.name)
         and not p.is_symlink() and p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for expired in scans[max(1, keep):]:
        shutil.rmtree(expired)
    return str(folder)
