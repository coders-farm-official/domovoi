"""Live facts about this Domovoi, exact and read-only.

The help desk answers "which version am I on?", "is the internet on?",
"what plugins do I have?" from the server itself, never from the manual
(which describes every Domovoi) or from a model's memory. The web layer
gathers the values (``web/backend/api/help.py``: the running version, the
rooms' list, the internet answer, the AI switch, the ``plugins`` rows) and
:func:`build` turns them into :class:`LiveFacts`; :func:`render` is the
block a help answer is given, one fact a line.

Nothing secret is here: room names and plugin names are what every paired
device already lists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

INTERNET_WORDS = {
    "always": "yes (Domovoi may use the internet)",
    "sometimes": "sometimes (each use is asked about)",
    "never": "no (Domovoi never uses the internet)",
    "": "not answered yet (it behaves as it always has: online when it can be)",
}


@dataclass(frozen=True)
class RoomFact:
    name: str
    status: str  # online | offline | waiting


@dataclass(frozen=True)
class PluginFact:
    slug: str
    name: str
    version: str
    enabled: bool
    status: str


@dataclass(frozen=True)
class LiveFacts:
    version: str | None
    internet: str
    ai_on: bool
    rooms: tuple[RoomFact, ...] = ()
    plugins: tuple[PluginFact, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "internet": self.internet,
            "internet_words": INTERNET_WORDS.get(self.internet, self.internet),
            "ai": self.ai_on,
            "rooms": [{"name": r.name, "status": r.status} for r in self.rooms],
            "plugins": [
                {"slug": p.slug, "name": p.name, "version": p.version, "enabled": p.enabled,
                 "status": p.status}
                for p in self.plugins
            ],
        }


def build(
    *,
    version: str | None,
    internet: str,
    ai_on: bool,
    rooms: Iterable[dict[str, Any]] = (),
    plugins: Iterable[dict[str, Any]] = (),
) -> LiveFacts:
    room_facts = tuple(
        RoomFact(str(r.get("room_id") or r.get("name") or "?"), str(r.get("status") or "unknown"))
        for r in rooms
    )
    plugin_facts = tuple(
        PluginFact(
            slug=str(p.get("slug")), name=str(p.get("name") or p.get("slug")),
            version=str(p.get("version") or "?"), enabled=bool(p.get("enabled")),
            status=str(p.get("status") or "ok"),
        )
        for p in plugins
        if p.get("status") != "uninstalled"
    )
    return LiveFacts(version=version, internet=internet or "", ai_on=bool(ai_on),
                     rooms=room_facts, plugins=plugin_facts)


def render(f: LiveFacts) -> str:
    lines = [
        f"Domovoi version: {f.version or 'unknown'}",
        f"Internet: {INTERNET_WORDS.get(f.internet, f.internet)}",
        f"AI chat and questions: {'on' if f.ai_on else 'off'}",
    ]
    if f.rooms:
        lines.append("Rooms: " + ", ".join(f"{r.name} ({r.status})" for r in f.rooms))
    else:
        lines.append("Rooms: none set up yet")
    if f.plugins:
        lines.append("Plugins: " + ", ".join(
            f"{p.name} {p.version}" + ("" if p.enabled else " (disabled)")
            + ("" if p.status in ("ok", "degraded") else f" ({p.status})")
            for p in f.plugins
        ))
    else:
        lines.append("Plugins: none installed")
    return "\n".join(lines)
