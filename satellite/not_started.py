"""Why a spare computer's satellite is not running, for its tray and CLI.

A spare computer's engine that refuses to start (no pinned server, an
account that belongs to a Domovoi server, settings that don't load) exits
78, and its unit leaves it stopped (``RestartPreventExitStatus=78``) rather
than restarting it every few seconds. It leaves the reason here first, so
the tray and ``python -m satellite.client --status`` can say why instead of
only "isn't running". A start that gets past those checks clears it.

``~/.domovoi/not-started.txt``, 0600, one line. Stdlib only.
"""

from __future__ import annotations

import os
from pathlib import Path

PATH = Path.home() / ".domovoi" / "not-started.txt"
MAX_CHARS = 500


def note(reason: str | None) -> None:
    """Record why the engine is not starting, or (None) that it started."""
    try:
        if reason:
            PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = PATH.with_name(PATH.name + ".tmp")
            tmp.write_text(" ".join(reason.split())[:MAX_CHARS] + "\n", encoding="utf-8")
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, PATH)
        else:
            PATH.unlink(missing_ok=True)
    except OSError:
        pass


def read() -> str | None:
    """The recorded reason, or None."""
    try:
        text = PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text[:MAX_CHARS] or None
