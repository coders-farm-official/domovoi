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

import logging
import threading
import time
from pathlib import Path
from typing import Any

from satellite import boot_write, wifi_reasons

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
#
# Since 2026-10-22 a join error is tagged with its kind
# (satellite/wifi_reasons.py), and the card gets that kind's words plus
# nmcli's reason line, which the join has already stripped of the network's
# name and the password: the card is the one place a household with no
# keyboard can read WHY (pull the card, open domovoi/device-info.json).
JOIN_FAILED = wifi_reasons.CARD[wifi_reasons.WRONG_PASSWORD]
JOIN_TIMED_OUT = wifi_reasons.CARD[wifi_reasons.TIMEOUT]
JOIN_REFUSED = wifi_reasons.CARD[wifi_reasons.SETUP_FAILED]
JOIN_NO_TOOLS = "couldn't join your home Wi-Fi — the Wi-Fi tools on this card failed"


def card_error(status: str | None, error: str | None) -> str | None:
    """``error`` as the card may show it. A failed join's reason is
    replaced by its kind's words (and nmcli's scrubbed reason, when the
    error is tagged); other errors (the setup network's own, which name no
    house network) pass through."""
    if not error:
        return None
    if status != "wifi_failed":
        return error
    kind = wifi_reasons.kind_of(error)
    if kind is not None:
        words = wifi_reasons.CARD[kind]
        detail = wifi_reasons.detail_of(error)
        return f"{words} (NetworkManager: {detail})" if detail else words
    # An untagged error: an older card's, or one of the few that are not
    # from the join itself.
    low = error.lower()
    if "timed out" in low:
        return JOIN_TIMED_OUT
    if "refused" in low:
        return JOIN_REFUSED
    if "tooling unavailable" in low:
        return JOIN_NO_TOOLS
    return wifi_reasons.CARD[wifi_reasons.classify(error)]


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

    Through satellite/boot_write.py: a temporary file, synced, renamed over
    the old one, synced again, because the card is pulled out of a device
    that was simply switched off and the partition is FAT. And only when
    something a person would read has changed: see ``_worth_writing``."""
    path = report_path(boot_dirs)
    if path is None:
        return False
    # Two writers (the state machine and the portal's phone counter) share
    # one temporary name; one at a time.
    with _LOCK:
        return _update_locked(path, top, network)


# Fields that change on every report without saying anything new: when
# they are ALL that changed, the card is rewritten at most this often.
# ``updated`` is the write's own time, ``line`` repeats the rest in words
# (with the time in it), ``attempts`` counts a retry loop's turns.
_VOLATILE = ("updated", "line", "attempts")
VOLATILE_REWRITE_SEC = 30 * 60
_last_write: dict[str, float] = {}


def _stable(doc: dict[str, Any]) -> dict[str, Any]:
    out = dict(doc)
    block = out.get(BLOCK)
    if isinstance(block, dict):
        out[BLOCK] = {k: v for k, v in block.items() if k not in _VOLATILE}
    return out


def _worth_writing(path: Path, old: dict[str, Any] | None,
                   new: dict[str, Any]) -> bool:
    """FAT has no journal, so every write is a chance for a power cut to
    land mid-write: write when the report says something new, and when only
    its clock and counters moved, at most every ``VOLATILE_REWRITE_SEC``."""
    if old is None or _stable(old) != _stable(new):
        return True
    if old == new:
        return False
    last = _last_write.get(str(path))
    return last is None or time.monotonic() - last >= VOLATILE_REWRITE_SEC


def _skeleton() -> dict[str, Any]:
    # What stage 1 would have started from, so the card still reads as a
    # Domovoi setup card once a damaged report has been replaced.
    return {"domovoi_setup": 1, "profiles_supported": []}


def _update_locked(path: Path, top, network) -> bool:
    old = boot_write.read_json(path)
    if old is None and path.exists():
        # Missing a closing brace, empty, or unreadable altogether: a power
        # cut caught an older, in-place write. Leaving it means the card
        # never says anything again; start over from what stage 1 seeds.
        log.warning("%s is damaged; replacing it", path)
        doc: dict[str, Any] = _skeleton()
        doc["report_replaced"] = f"{_now()}: the previous report was unreadable"
    else:
        doc = dict(old or {})
    if top:
        doc.update(top)
    if network is not None:
        block = doc.get(BLOCK)
        block = dict(block) if isinstance(block, dict) else {}
        block.update(network)
        block["updated"] = _now()
        doc[BLOCK] = block
    if not _worth_writing(path, old, doc):
        return False
    try:
        boot_write.write_json(path, doc)
    except OSError as e:
        log.warning("could not update %s: %s", path, e)
        return False
    _last_write[str(path)] = time.monotonic()
    return True


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
