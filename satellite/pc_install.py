"""The Python half of ``satellite/scripts/install-pc.sh``.

Spare-device satellites (S1, 2026-10-13). The shell script does what only
a shell does well (apt, users, units, questions); everything with a rule
in it is here, where it is tested:

* ``config`` — write ``~/.domovoi/config.toml`` for an enrolled laptop: the
  ``generic_pc`` profile, the room, the pinned core (address and
  fingerprint) and the permission the person chose. An existing config is
  kept and edited (comments and the owner's own settings survive) after a
  dated backup; moving to another core or room clears the old approval so
  the new core's approval gate starts from "not approved".
* ``models`` — the openWakeWord files the default wake word needs, each
  checked against the same SHA-256 pins the core uses
  (``domovoi/satellite_media/fetchers.py`` OWW_MODEL_FILES): from the
  enrolling core's signed ``/v1/wake-base`` channel when it serves them,
  else from openWakeWord's own release.
* ``wait`` — watch the new engine until it is approved, printing the
  approval code only to a terminal (never to a log, never into a pipe).

What the installer changed on the system (linger, units, power settings)
is recorded by the shell script itself, in a root-owned file root alone
reads (``/var/lib/domovoi-satellite/install.json``); nothing here writes
or reads it (security review of wf/sat-pc, B3). Nothing here runs at
uninstall either: ``--purge`` uses root's copy of
``satellite/scripts/purge-satellite-files.py`` with the system's
``python3 -I``, never this code, which the satellite's account can change.

Stdlib only, plus the satellite package's own stdlib modules: it runs with
the venv's python, but nothing here needs a wheel.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

from satellite import config_writer, enrolment

#: What openwakeword 0.6.0 loads for the default wake word (``hey_jarvis``):
#: the two feature models and the word's own. SHA-256 and size, exactly as
#: domovoi/satellite_media/fetchers.py pins them (a test holds them equal).
OWW_MODELS: dict[str, tuple[str, int]] = {
    "melspectrogram.onnx": ("ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f", 1087958),
    "embedding_model.onnx": ("70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f", 1326578),
    "hey_jarvis_v0.1.onnx": ("94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb", 1271370),
}
OWW_RELEASE_URL = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"
#: The core's signed channel for the base models (room phones use it too).
WAKE_BASE_CHANNEL = "satellite-wake-base"
#: Approval state a new core or room must not inherit.
_APPROVAL_FILES = ("approved", "approval_code", "pending-server.json", "pairing_token",
                   "server-fingerprint.json")


# ─── config ───────────────────────────────────────────────────────────────

def config_changes(e: enrolment.Enrolment, permissions: str) -> dict[str, Any]:
    return {
        "device.profile": "generic_pc",
        "satellite.room_id": e.room_id,
        "satellite.room_name": e.room,
        "satellite.domovoi_url": e.ws_url,
        "satellite.server_fingerprint": e.fp,
        "pc.permissions": permissions,
        # The example config switches these on for a Pi in so many words;
        # a laptop has no LED ring, and its Wi-Fi is not ours to manage.
        "leds.enabled": False,
        "wifi.enabled": False,
    }


def write_config(config_path: Path, example: Path, e: enrolment.Enrolment, permissions: str,
                 *, now: Callable[[], float] = time.time) -> dict[str, Any]:
    """Write the enrolled config. Returns what happened, for the script."""
    if permissions not in ("automatic", "ask"):
        raise ValueError(f"permissions must be automatic or ask, not {permissions!r}")
    config_dir = config_path.parent
    config_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(config_dir, 0o700)
    except OSError:
        pass
    report: dict[str, Any] = {"config": str(config_path), "backup": None, "reset_approval": False}
    if config_path.exists():
        original = config_path.read_text(encoding="utf-8")
        import tomllib

        try:
            old = tomllib.loads(original)
        except tomllib.TOMLDecodeError:
            old = {}
        sat = old.get("satellite", {}) if isinstance(old.get("satellite"), dict) else {}
        moved = (sat.get("server_fingerprint") or None, sat.get("room_id") or None) != (e.fp, e.room_id)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now()))
        backup = config_path.with_name(f"{config_path.name}.bak-{stamp}")
        backup.write_text(original, encoding="utf-8")
        report["backup"] = str(backup)
        base = original
        if moved:
            report["reset_approval"] = _reset_approval(config_dir, stamp)
    else:
        base = example.read_text(encoding="utf-8")
        report["reset_approval"] = _reset_approval(config_dir, "new")
    merged = config_writer.apply_changes(base, config_changes(e, permissions))
    tmp = config_path.with_name(config_path.name + ".tmp")
    tmp.write_text(merged, encoding="utf-8", newline="\n")
    os.replace(tmp, config_path)
    try:
        os.chmod(config_path, 0o600)
    except OSError:
        pass
    return report


def _reset_approval(config_dir: Path, stamp: str) -> bool:
    """Move the old approval state aside: a device approved by another core,
    or as another room, starts this enrolment unapproved and with a new
    pairing token."""
    moved = False
    aside = config_dir / f"previous-enrolment-{stamp}"
    for name in _APPROVAL_FILES:
        p = config_dir / name
        if p.exists() and not p.is_symlink():
            aside.mkdir(exist_ok=True)
            os.replace(p, aside / name)
            moved = True
    return moved


# ─── models ───────────────────────────────────────────────────────────────

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _get(url: str, opener, timeout: float = 60.0, limit: int = 8 * 1024 * 1024) -> bytes:
    with opener(url, timeout=timeout) as r:
        if getattr(r, "status", 200) != 200:
            raise OSError(f"{url}: HTTP {getattr(r, 'status', '?')}")
        data = r.read(limit + 1)
    if len(data) > limit:
        raise OSError(f"{url}: larger than {limit} bytes")
    return data


def _place(dest: Path, name: str, data: bytes) -> None:
    tmp = dest / f".{name}.part"
    tmp.write_bytes(data)
    os.replace(tmp, dest / name)


def fetch_models(dest: Path, *, core: str | None, fp: str | None, opener=None,
                 log: Callable[[str], None] = print) -> dict[str, str]:
    """Put every model in :data:`OWW_MODELS` into ``dest``; returns
    ``{name: source}``. A file already there with the right digest is kept.
    Each body is checked against its pin whatever the source; the core's
    list must also carry its signature. Raises OSError naming what could
    not be had."""
    from satellite import server_identity

    opener = opener or urllib.request.urlopen
    dest.mkdir(parents=True, exist_ok=True)
    got: dict[str, str] = {}
    for name, (sha, size) in OWW_MODELS.items():
        p = dest / name
        if p.is_file() and p.stat().st_size == size and _sha256(p.read_bytes()) == sha:
            got[name] = "present"
    wanted = [n for n in OWW_MODELS if n not in got]
    if wanted and core and fp:
        try:
            envelope = json.loads(_get(f"{core}/v1/wake-base/manifest.sig", opener).decode("utf-8"))
            listed = server_identity.verify_manifest_envelope(
                envelope, channel=WAKE_BASE_CHANNEL, expected_fingerprint=fp)
            for name in list(wanted):
                sha, size = OWW_MODELS[name]
                if not isinstance(listed, dict) or listed.get(name) != sha:
                    continue
                data = _get(f"{core}/v1/wake-base/{name}", opener)
                if len(data) == size and _sha256(data) == sha:
                    _place(dest, name, data)
                    got[name] = "core"
                    wanted.remove(name)
        except (OSError, ValueError, server_identity.IdentityError) as e:
            log(f"  the Domovoi server does not serve the wake-word models ({e}); "
                "fetching them from openWakeWord's release instead")
    for name in list(wanted):
        sha, size = OWW_MODELS[name]
        try:
            data = _get(OWW_RELEASE_URL + name, opener)
        except OSError as e:
            raise OSError(f"could not download {name}: {e}") from None
        if len(data) != size or _sha256(data) != sha:
            raise OSError(f"{name} from {OWW_RELEASE_URL} is not the pinned file; not using it")
        _place(dest, name, data)
        got[name] = "openwakeword"
        wanted.remove(name)
    return got


# ─── command line ─────────────────────────────────────────────────────────

def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m satellite.pc_install")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("config")
    c.add_argument("--config", type=Path, required=True)
    c.add_argument("--payload", required=True)
    c.add_argument("--permissions", required=True, choices=("automatic", "ask"))
    m = sub.add_parser("models")
    m.add_argument("--dest", type=Path, required=True)
    m.add_argument("--payload")
    w = sub.add_parser("wait")
    w.add_argument("--socket", type=Path, required=True)
    w.add_argument("--timeout", type=float, default=900.0)
    w.add_argument("--show-code", action="store_true",
                   help="print the approval code (the script passes it only when its own output is a terminal)")
    args = parser.parse_args(argv)

    if args.cmd == "config":
        e = enrolment.parse(args.payload)
        example = Path(__file__).resolve().parent / "config.toml.example"
        print(json.dumps(write_config(args.config, example, e, args.permissions)))
        return 0
    if args.cmd == "models":
        e = enrolment.parse(args.payload) if args.payload else None
        try:
            got = fetch_models(args.dest, core=e.core if e else None, fp=e.fp if e else None,
                               log=lambda line: print(line, file=sys.stderr))
        except OSError as err:
            print(f"wake-word models: {err}", file=sys.stderr)
            return 1
        print(json.dumps(got))
        return 0
    if args.cmd == "wait":
        from satellite import pc_engine

        final = pc_engine.wait_for_approval(args.socket, timeout=args.timeout, show_code=args.show_code)
        return 0 if final == "connected" else 4
    return 64


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
