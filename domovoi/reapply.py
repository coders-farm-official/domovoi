"""Core reapply-hook registry (design §4.6).

Replaces hardcoded if/elif dispatch in the config-write endpoint:
``tier="reapply"`` fields now run registered callbacks after the live
settings singleton has been mutated + persisted, instead of the endpoint
knowing which subsystem each field pokes. Core registers its own hooks
(TTS client reset, Ollama client reset, log level) at lifespan start;
plugin fields go through the parallel per-slug registry in
``domovoi.plugins_runtime.config_bridge`` (the ``ctx.on_reapply`` path)
— this module is the CORE-field half only, importable by both processes
(no plugin-runtime dependency).

Callbacks take no arguments and read the already-mutated ``settings``
singleton. Registration is keyed (field, key) so re-running the lifespan
(tests enter it repeatedly) replaces rather than accumulates; a callback
shared by several fields (the TTS reset serves ``tts_engine`` AND
``tts_speed``) runs at most once per write batch — ``run_for`` dedupes
by callback identity.
"""

from __future__ import annotations

import logging
from typing import Callable, Iterable

log = logging.getLogger(__name__)

# field name → {key: callback}
_HOOKS: dict[str, dict[str, Callable[[], None]]] = {}


def on_reapply(
    field: str, cb: Callable[[], None], *, key: str | None = None
) -> None:
    """Register ``cb`` to run after a ``tier="reapply"`` write to
    ``field``. ``key`` defaults to the callback's ``__name__``;
    re-registering the same (field, key) replaces the callback."""
    _HOOKS.setdefault(field, {})[key or getattr(cb, "__name__", repr(cb))] = cb


def run_for(fields: Iterable[str]) -> list[str]:
    """Run every hook registered for ``fields``, deduping shared
    callbacks so a batch touching ``tts_engine`` + ``tts_speed`` resets
    the TTS client once. Hook failures log and never abort the batch
    (the config write already persisted). Returns ``"field:key"`` labels
    of the hooks that ran, for the endpoint's response/logging."""
    ran: list[str] = []
    seen: set[int] = set()
    for field in fields:
        for key, cb in (_HOOKS.get(field) or {}).items():
            if id(cb) in seen:
                continue
            seen.add(id(cb))
            try:
                cb()
                ran.append(f"{field}:{key}")
            except Exception as e:  # noqa: BLE001 — a hook must not fail the write
                log.warning("reapply hook %s for %r failed: %s", key, field, e)
    return ran


def registered_fields() -> list[str]:
    return sorted(_HOOKS)
