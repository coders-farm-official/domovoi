"""Local artifact cache for satellite media builds.

Everything downloadable is fetched ONCE into ``~/.domovoi/satellite_media/
cache/`` and reused across builds — the payload itself is then assembled
fully offline. Layout:

    cache/wheels/<python_version>/   aarch64 wheels (pip download)
    cache/debs/<os_release>/         arm64 .debs (unprivileged docker fetch)
    cache/dtbo/                      prebuilt mic-board overlays (optional)
    cache/oww_models/                openWakeWord base models
    cache/refreshed.json             per-bucket refresh timestamps
"""

from __future__ import annotations

import json
import time
from pathlib import Path

CACHE_ROOT = Path("~/.domovoi/satellite_media/cache").expanduser()


def bucket(name: str, sub: str | None = None) -> Path:
    p = CACHE_ROOT / name
    if sub:
        p = p / sub
    p.mkdir(parents=True, exist_ok=True)
    return p


def _stamp_file() -> Path:
    return CACHE_ROOT / "refreshed.json"


def read_stamps() -> dict[str, float]:
    try:
        return json.loads(_stamp_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def stamp(name: str) -> None:
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    stamps = read_stamps()
    stamps[name] = time.time()
    _stamp_file().write_text(json.dumps(stamps, indent=2), encoding="utf-8")


def status(python_version: str, os_release: str) -> dict[str, object]:
    """What the dashboard's cache panel shows: per-bucket presence + last
    refresh."""
    stamps = read_stamps()

    def _bucket_info(name: str, path: Path, glob: str) -> dict[str, object]:
        files = list(path.glob(glob)) if path.is_dir() else []
        return {
            "ok": len(files) > 0,
            "files": len(files),
            "refreshed_at": stamps.get(name),
        }

    return {
        "wheels": _bucket_info(
            "wheels", CACHE_ROOT / "wheels" / python_version, "*.whl"
        ),
        "debs": _bucket_info("debs", CACHE_ROOT / "debs" / os_release, "*.deb"),
        "dtbo": _bucket_info("dtbo", CACHE_ROOT / "dtbo", "*.dtbo"),
        "oww_models": _bucket_info(
            "oww_models", CACHE_ROOT / "oww_models", "*.onnx"
        ),
    }
