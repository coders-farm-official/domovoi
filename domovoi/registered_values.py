"""In-process open-enum registries (design §6.4).

V001 removed the CHECK constraints on extensible enum columns
(``intents_log.matched_path``, ``library_tracks.source``,
``media_plays.source``, ``voices.engine``, ``media_acquisitions.status``)
so plugins can add values without core DDL churn. Validation moved HERE:
app code validates against these in-process registries at write time,
raising ``ValueError`` exactly where the old CHECK would have aborted the
transaction.

The ``registered_values`` DB table is the *informational mirror* of this
registry (seeded for core by V001, written at plugin registration time by
the plugin runtime in C3) so DBAs/analytics can see the live vocabulary —
it is deliberately NOT consulted on the hot path.
"""

from __future__ import annotations

# domain → value → owner ("core" or a plugin slug). Core seeds mirror the
# V001 registered_values INSERTs exactly — keep the two in sync.
_REGISTRY: dict[str, dict[str, str]] = {
    "matched_path": {
        "fast": "core",
        "fast_offline": "core",
        "llm": "core",
        "llm_offline": "core",
        "tool": "core",
        "tool_offline": "core",
        "qa": "core",
        "qa_offline": "core",
        "error": "core",
        "confirmation": "core",
        "auto_search": "core",
        "volatile_offer": "core",
        "chat": "core",
    },
    "library_source": {
        "manual": "core",
        "indexed": "core",
        "upload": "core",
    },
    "media_play_source": {
        "library": "core",
        "playlist": "core",
        "spoken_audio": "core",
    },
    "tts_engine": {
        "edge": "core",
        "piper": "core",
        "system": "core",
    },
    "acquisition_status": {
        "pending": "core",
        "claimed": "core",
        "done": "core",
        "failed": "core",
        "unfulfillable": "core",
        "cancelled": "core",
    },
}


def register(domain: str, value: str, *, owner: str = "core") -> None:
    """Add a value to an open enum. The plugin runtime (C3) calls this at
    plugin registration and mirrors the row into the registered_values
    table; core additions belong in both this seed dict and V001."""
    _REGISTRY.setdefault(domain, {})[value] = owner


def unregister_owner(owner: str) -> None:
    """Teardown for plugin disable/uninstall (C3)."""
    for values in _REGISTRY.values():
        for value in [v for v, o in values.items() if o == owner]:
            del values[value]


def is_registered(domain: str, value: str) -> bool:
    return value in _REGISTRY.get(domain, {})


def values(domain: str) -> frozenset[str]:
    return frozenset(_REGISTRY.get(domain, {}))


def require(domain: str, value: str) -> str:
    """Validate-or-raise. Raises where the removed DB CHECK constraint
    would have aborted the transaction, keeping the failure mode loud
    and test-visible instead of silently widening the vocabulary."""
    if not is_registered(domain, value):
        raise ValueError(
            f"unregistered {domain} value {value!r} — registered: "
            f"{sorted(values(domain))}"
        )
    return value
