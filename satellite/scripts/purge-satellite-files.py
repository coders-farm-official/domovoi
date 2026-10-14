"""Remove a spare computer's satellite files from its ~/.domovoi, and nothing
else: ``install-pc.sh --uninstall --purge`` on a desktop.

The installer puts a root-owned copy of this file next to its own copy in
/usr/local/lib/domovoi-satellite, and the uninstaller runs that copy with
the system's ``python3 -I`` — never the satellite's own code or venv, which
the satellite's account (and the core's updates) can change: by the time
--purge runs, the terminal may hold a sudo ticket (security review of
wf/sat-pc, round 2). Standard library only; it imports nothing of the
satellite.

On a computer that also runs a Domovoi server, ~/.domovoi holds that
server's key, voices and wake models too: with one of its files there, the
folders both use (``sounds``, ``wake_models``) lose only the clips the
satellite's sound sync recorded as its own.

    python3 -I purge-satellite-files.py ~/.domovoi     # prints what went (JSON)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

#: What a Domovoi server keeps in ~/.domovoi (satellite/pc_engine.py
#: CORE_MARKERS and install-pc.sh CORE_MARKERS; a test holds them equal).
CORE_MARKERS = ("server-identity.json", "device-token.txt", "setup-code.txt")
#: Every file a satellite keeps in ~/.domovoi.
SATELLITE_FILES = (
    "config.toml", "approved", "approval_code", "pairing_token", "pending-server.json",
    "server-fingerprint.json", "server.url", "synced_sha", "synced_manifest.json",
    "upgrade_pending.json", "satellite_backup.tar", "log-spool.txt", "last-health.json",
    "self-restarts.json", "muted", "voice", "wake", "wake_threshold", "manifest-serials.json",
    "net-settings.logged", "plugin_payload_state.json", "plugin_payload_manifest.json",
    "pending_payload.json", "provisioning_state.json", "image_sat_type", "image_device_profile",
    "ap.json", "setup_gadget.img", "control.sock", "install-pc.json", "not-started.txt",
)
SATELLITE_DIRS = ("plugin_payloads",)
#: satellite/sound_sync.py RECORD_NAME (a test holds them equal).
SOUNDS_RECORD = ".satellite-synced.json"


def _inside(base: Path, rel: str) -> Path | None:
    """``base/rel`` when no part of it below ``base`` is a link, else None."""
    if not rel or rel.startswith("/") or "\\" in rel or any(part in ("", ".", "..") for part in rel.split("/")):
        return None
    p = base
    for part in rel.split("/"):
        p = p / part
        if p.is_symlink() and p != base / rel:
            return None
    return p


def _remove(p: Path) -> bool:
    try:
        if p.is_symlink() or p.is_file() or p.is_socket():
            p.unlink()
            return True
        if p.is_dir():
            shutil.rmtree(p)
            return True
    except OSError:
        pass
    return False


def purge(config_dir: Path) -> list[str]:
    """Remove the satellite's own files from ``config_dir`` and return what
    went. A link is removed, never followed."""
    config_dir = Path(config_dir)
    removed: list[str] = []
    if config_dir.is_symlink() or not config_dir.is_dir():
        return removed
    server = any((config_dir / m).exists() for m in CORE_MARKERS)
    names = list(SATELLITE_FILES) + list(SATELLITE_DIRS)
    names += sorted(p.name for p in config_dir.glob("config.toml.bak-*"))
    names += sorted(p.name for p in config_dir.glob("previous-enrolment-*"))
    for name in names:
        if _remove(config_dir / name):
            removed.append(name)
    sounds = config_dir / "sounds"
    if sounds.is_dir() and not sounds.is_symlink():
        try:
            recorded = json.loads((sounds / SOUNDS_RECORD).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            recorded = None
        if isinstance(recorded, list):
            for rel in recorded:
                p = _inside(sounds, rel) if isinstance(rel, str) else None
                if p is not None and (p.is_file() or p.is_symlink()) and _remove(p):
                    removed.append(f"sounds/{rel}")
            _remove(sounds / SOUNDS_RECORD)
            # Folders left empty, deepest first; a linked folder is not entered.
            for dirpath, dirnames, _files in os.walk(sounds, topdown=False):
                for name in dirnames:
                    d = os.path.join(dirpath, name)
                    if not os.path.islink(d):
                        try:
                            os.rmdir(d)
                        except OSError:
                            pass
            try:
                sounds.rmdir()
                removed.append("sounds")
            except OSError:
                pass
        elif not server and _remove(sounds):
            removed.append("sounds")
    wake = config_dir / "wake_models"
    if not server and not wake.is_symlink() and wake.is_dir() and _remove(wake):
        removed.append("wake_models")
    return removed


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python3 -I purge-satellite-files.py CONFIG_DIR", file=sys.stderr)
        return 64
    print(json.dumps(purge(Path(argv[0]))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
