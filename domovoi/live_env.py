"""A setting's live value, read the way the next boot would read it.

The web process keeps no live copy of the core's settings: a dashboard
save goes to the core, which rewrites ``domovoi/.env``. A setting the web
process must follow at once (the chat's "Always allow internet") is read
here: the process environment first (any case, as pydantic-settings reads
it), then ``domovoi/.env``, cached on the file's mtime and size, so a
check costs one ``os.stat``. The same rule :mod:`domovoi.egress` and
:mod:`domovoi.ai_features` follow for their own switches.

Stdlib + :mod:`domovoi.config_env_writer` at import time; ``dotenv`` only
when the file has changed.
"""

from __future__ import annotations

import os
from pathlib import Path

from domovoi import config_env_writer

_cache: dict[str, tuple[tuple[object, ...], str | None]] = {}
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})


def _environ(name: str) -> str | None:
    key = name.upper()
    for k, v in os.environ.items():
        if k.upper() == key:
            return v
    return None


def raw(name: str) -> str | None:
    """The raw value of setting ``name`` (``lookup_always_allow``), or None
    when neither the environment nor ``.env`` has it."""
    env = _environ(name)
    path = config_env_writer._ENV_FILE
    try:
        st = os.stat(path)
        stamp: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    key = (env, str(path), stamp)
    hit = _cache.get(name)
    if hit is not None and hit[0] == key:
        return hit[1]
    value = env
    if value is None and stamp is not None:
        try:
            from dotenv import dotenv_values

            for k, v in dotenv_values(Path(path)).items():
                if k.upper() == name.upper() and v is not None:
                    value = v
        except (OSError, ValueError, UnicodeDecodeError):
            value = None
    _cache[name] = (key, value)
    return value


def flag(name: str, default: bool) -> bool:
    """A boolean setting's live value; ``default`` when unset or unreadable."""
    v = raw(name)
    if v is None:
        return default
    t = v.strip().lower()
    if t in _TRUE:
        return True
    if t in _FALSE:
        return False
    return default


__all__ = ["flag", "raw"]
