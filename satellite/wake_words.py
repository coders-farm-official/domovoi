"""Several wake words in one room (1.4.0): which words, and how many.

A room used to wake on one word. It may now wake on up to
:data:`MAX_WORDS`, in order: the first is the room's main word (the one
its ``[wake] wake_word`` / the ``~/.domovoi/wake`` sidecar names, the one
an older core knows about), the rest are optional extras an admin added on
the dashboard. openWakeWord runs every word on the same audio front end
(the melspectrogram and the speech embedding, the expensive part), and each
word adds only its own small classifier, so an extra word costs little CPU.
What it does cost is false wakes: each word can fire on its own, at its own
threshold, so every extra word makes an accidental wake-up a little more
likely.

**How many a device runs** (:func:`device_limit`). Measured 2026-11-16 on
the dev PC with openWakeWord 0.6.0 (onnx, one thread): the shared front end
took 1.79 ms per 80 ms chunk, each word's classifier 0.012-0.026 ms (stock
models; a trained word is the same shape), about 1.5 % of the front end per
word, and about 1 MB of memory per word. A Pi Zero 2 W's cores are far
slower (its office satellite idled at 73-93 % of a core before the
2026-10-17 streaming fix, satellite/client.py ``_lighten_wake_streaming``),
but the ratio holds: an extra word is roughly 1 % of one core there. So the
CPU would allow five on every device. The Zero still gets
:data:`ZERO_MAX_WORDS` (3): it is the weakest device, already the one whose
wake loop falls behind under thermal throttling or swap
(MIGRATION_HAT_TO_XVF3800.md), it has 512 MB, and its drop-in wake watcher
already drops frames. Three leaves that headroom. Everything else (Pi 3/4/5,
a PC) runs :data:`MAX_WORDS`.

The device says its limit in its hello (``wake_words_max``) and the core
never pushes more; the device also loads no more than its limit whatever it
is sent. A core that knows nothing of this sends ``set_wake_word`` with one
``slug``: the device then runs that one word, as before.
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: The most wake words a room may have.
MAX_WORDS = 5
#: The most a Pi Zero 2 W runs (see the module docstring).
ZERO_MAX_WORDS = 3

#: Where a Pi says what board it is.
DEVICE_MODEL = Path("/proc/device-tree/model")

#: A wake word's slug: an openWakeWord model stem (the core's rule,
#: domovoi/streaming.py ``_WAKE_SLUG_RE``).
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def clean_slug(raw: object) -> str | None:
    if isinstance(raw, str) and _SLUG_RE.match(raw.strip()):
        return raw.strip()
    return None


def clean_threshold(raw: object) -> float | None:
    """A word's own threshold, 0-1, else None (the room's [wake] threshold)."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        return None
    return round(value, 4)


def board_model(path: Path = DEVICE_MODEL) -> str | None:
    """The board's model line ("Raspberry Pi Zero 2 W Rev 1.0"), or None
    off a Pi (a PC has no device tree)."""
    try:
        return path.read_bytes().decode("utf-8", "replace").strip("\x00\n ") or None
    except OSError:
        return None


def device_limit(*, is_pc: bool, model: str | None) -> int:
    """How many wake words this device runs at once."""
    if is_pc:
        return MAX_WORDS
    if model and "zero" in model.lower():
        return ZERO_MAX_WORDS
    return MAX_WORDS


def parse_words(raw: object) -> list[dict[str, Any]]:
    """A pushed ``words`` list (``[{"slug", "threshold"?}, ...]`` or bare
    slugs) as ``[{"slug", "threshold"}]``: slug-shaped, no repeats, at most
    :data:`MAX_WORDS`, in order. Anything else is dropped."""
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, dict):
            slug, threshold = clean_slug(item.get("slug")), clean_threshold(item.get("threshold"))
        else:
            slug, threshold = clean_slug(item), None
        if slug is None or any(w["slug"] == slug for w in out):
            continue
        out.append({"slug": slug, "threshold": threshold})
        if len(out) >= MAX_WORDS:
            break
    return out


def read_list(path: Path, first: str) -> list[dict[str, Any]] | None:
    """The room's stored word list, when it is there, readable, and starts
    with ``first`` (the main word the sidecar or config names). A list that
    doesn't (a later single-word push replaced the main word without it, a
    hand edit) is not used: the room runs its main word alone."""
    try:
        if not path.is_file():
            return None
        words = parse_words(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as e:
        log.warning("wake words: %s can't be read (%s); using the main word only", path, e)
        return None
    if not words or words[0]["slug"] != first:
        if words:
            log.info("wake words: %s starts with %r, not the main word %r; using the main "
                     "word only", path, words[0]["slug"], first)
        return None
    return words


def write_list(path: Path, words: list[dict[str, Any]]) -> None:
    """Keep the extra words (the whole list, main word first) for the next
    start; one word removes the file (the main word alone needs none)."""
    if len(words) <= 1:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(words) + "\n", encoding="utf-8")
    tmp.replace(path)
