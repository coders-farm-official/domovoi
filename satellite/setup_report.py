"""What setup is doing, written where a household can read it: the card.

An unprovisioned satellite has no network but its own setup hotspot, and
when that hotspot is the thing that's broken there is nowhere to read a
log. The one surface every household can reach is the boot partition: pull
the card, put it in any PC, open ``domovoi/device-info.json``. Stage 1
already reports its progress there (``status`` / ``step`` / ``error``);
before this, provisioning mode never touched it again, so a card whose
hotspot never worked read ``awaiting_provision`` / ``stage1-done`` /
``error: null`` forever (2026-10-17, a phone that "Couldn't connect to
network" for fifteen minutes).

This module keeps that file current from provisioning mode:

* the top-level ``status`` / ``step`` / ``error`` stage 1 started, so the
  last error is where people already look;
* a ``setup_network`` block: the hotspot's state, its name and radio
  settings, how many phones have joined it, the last error, and ``line``,
  one sentence a person can read without knowing what any of the rest
  means.

Never the setup network's password: this file is on a FAT partition anyone
holding the card can read, and its key lives in ``ap.json`` beside it.

Best-effort and stdlib-only, like everything provisioning mode imports: a
card that can't be written must never stop a device from being set up.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Where Pi OS mounts the boot partition: /boot/firmware since bookworm,
# /boot before it (and on other boards). The same order as
# satellite/provisioning_mode.BOOT_DIRS; repeated, not imported, so this
# module stays importable on its own.
BOOT_DIRS = (Path("/boot/firmware"), Path("/boot"))
NAME = "device-info.json"
BLOCK = "setup_network"
_LOCK = threading.Lock()

# What the card says about a failed home-Wi-Fi join. The journal and the
# portal page get the named version (nmcli's words and the network's name);
# the card does not, because the house network's name has no business on a
# card anyone can pull and read.
JOIN_FAILED = "couldn't join your home Wi-Fi — wrong password?"
JOIN_TIMED_OUT = "couldn't join your home Wi-Fi in time — is it in range?"
JOIN_REFUSED = "the home Wi-Fi details were refused — check the name and password"
JOIN_NO_TOOLS = "couldn't join your home Wi-Fi — the Wi-Fi tools on this card failed"


def card_error(status: str | None, error: str | None) -> str | None:
    """``error`` as the card may show it. A failed join's reason is
    replaced by generic words; other errors (the setup network's own, which
    name no house network) pass through."""
    if not error:
        return None
    if status != "wifi_failed":
        return error
    low = error.lower()
    if "timed out" in low:
        return JOIN_TIMED_OUT
    if "refused" in low:
        return JOIN_REFUSED
    if "tooling unavailable" in low:
        return JOIN_NO_TOOLS
    return JOIN_FAILED


def report_path(boot_dirs=None) -> Path | None:
    """The card's ``domovoi/device-info.json``, or None when this isn't a
    prepared card. Only ever a file that media prep put there: the folder
    must exist already, so a hand-built unit (or a developer's machine)
    never grows one."""
    for boot in (boot_dirs if boot_dirs is not None else BOOT_DIRS):
        folder = Path(boot) / "domovoi"
        if folder.is_dir():
            return folder / NAME
    return None


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def update(*, top: dict[str, Any] | None = None,
           network: dict[str, Any] | None = None,
           boot_dirs=None) -> bool:
    """Merge ``top`` into the document and ``network`` into its
    ``setup_network`` block, then write it back. True when written.

    Rewritten in place, through a temporary file and a rename, and flushed:
    the card is pulled out of a device that was simply switched off, and a
    half-written report is worse than a stale one."""
    path = report_path(boot_dirs)
    if path is None:
        return False
    # Two writers (the state machine and the portal's phone counter) share
    # one temporary name; one at a time.
    with _LOCK:
        return _update_locked(path, top, network)


def _update_locked(path: Path, top, network) -> bool:
    try:
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = None
        if text is None:
            doc: dict[str, Any] = {}
        else:
            try:
                doc = json.loads(text)
            except ValueError:
                doc = None
            if not isinstance(doc, dict):
                # Stage 1's fields are in there, maybe half-written by a
                # power cut. Ours are not worth more than theirs: leave it.
                log.warning("%s does not parse; not rewriting it", path)
                return False
        if top:
            doc.update(top)
        if network is not None:
            block = doc.get(BLOCK)
            block = dict(block) if isinstance(block, dict) else {}
            block.update(network)
            block["updated"] = _now()
            doc[BLOCK] = block
        tmp = path.with_name(NAME + ".tmp")
        # Readable as written: non-ASCII stays as typed, not escaped.
        raw = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        with open(tmp, "wb") as fh:          # binary: no CRLF, whatever the OS
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True
    except OSError as e:
        log.warning("could not update %s: %s", path, e)
        return False


def line(*, state: str, ssid: str | None = None, channel: int | None = None,
         phones: int = 0, error: str | None = None) -> str:
    """The one sentence a person reads. Plain words, no jargon beyond the
    network's own name."""
    name = ssid or "the setup network"
    at = time.strftime("%H:%M")
    if state == "up":
        joined = (
            "no phone has joined it yet" if phones == 0
            else "1 phone has joined it" if phones == 1
            else f"{phones} phones have joined it"
        )
        where = f" on channel {channel}" if channel else ""
        return f"{name} is on the air{where} (since {at}); {joined}."
    if state == "unavailable":
        return f"{name} cannot start on this card: {error}"
    if state == "failed":
        return f"{name} failed to start at {at} and will be retried: {error}"
    if state == "starting":
        return f"{name} is starting (since {at})."
    if state == "closed":
        return f"{name} closed at {at}: setup details received, joining the home Wi-Fi."
    return f"{name}: {state}."
