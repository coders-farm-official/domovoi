"""Capability eval — how Domovoi answers questions about its own abilities.

"Can you set timers?", "what can you do?", "are you recording me?": the
answer has to be true for THIS house, and change when a plugin, the
internet answer, the AI switch or who is asking changes. This script runs
a fixed case corpus through the real code path, with a context built from
a small declarative house fixture, and scores every reply:

  scripts/capability_corpus.json     the cases (id, utterance, where, house,
                                     expected verdict, phrases to find or
                                     never find, known gaps "needs_change")
  scripts/capability_houses/*.json   one house state per file; each extends
                                     ``base`` and lists only what differs
  scripts/capability_houses/_plugins.json
                                     the stand-in plugins a house can list

Every voice case runs the real ``router.route()``, and every chat case
runs chat.py's own deterministic step (house_facts.chat_reply, the privacy
and "can you ...?" matcher) first, inside a sandbox: the database reads a
house fixture holds (its Home location, printers, plugins, friends,
calendars, command recordings, timers and alarms, the room's board) come
from the fixture, the room's connected satellites from its rooms, and the
router's own session bookkeeping is kept in memory. Two targets, like
scripts/eval_routing.py:

  (default) stub   No model, no network, and no other database read: a
                   turn that would ask the model reports "needs model";
                   one that reads or writes database state the fixture
                   doesn't hold, reads more of the live rooms than their
                   names, or goes online reports "needs db" / "needs
                   live" / "needs network" instead of guessing. The live
                   database is never dialled: the run points DATABASE_URL
                   at a dead address and refuses every connect.

  --ollama URL     Real-model runs: the same route() with the real model
                   client (``--model`` answers, default
                   settings.ollama_model; ``--tool-model`` routes, default
                   settings.ollama_tool_model). Reads the fixture doesn't
                   hold come back empty and writes are dropped (noted in
                   the result), so a profile or history read doesn't stop
                   the turn. A chat case gets the chat's system prompt (or
                   the help desk's, for a help question).

  --judge-model M  Also score ``judge: true`` cases 0-3 against their
                   rubric, on the same Ollama server (2 or more passes).

Global checks on every reply, whatever the case: no can't-do hedge on a
capability question (acceptable on a command the router missed), no plugin
name (allowed only when the person asked which plugins are installed;
"the Plugins page" is not a name), none of the fixture's secrets, and every "Settings → …" path
must exist in the dashboard's settings registry (web/static/settings.jsx).

Cases with ``needs_change`` are known gaps: a failure there is EXPECTED and
reported as "known gap (Nx)", not as a regression; one that passes is
reported as "fixed?". Phone-assistant (Kotlin) and 1.5.0-only cases carry
``skip_reason`` and never run.

Usage:
    python scripts/eval_capabilities.py                      # stub mode
    python scripts/eval_capabilities.py --only-group 16 --only-group 17
    python scripts/eval_capabilities.py --case 4.28 -v
    python scripts/eval_capabilities.py --ollama http://192.168.1.20:11434 --model llama3.2:3b
    python scripts/eval_capabilities.py --ollama URL --model M --judge-model qwen3:8b --json out.json
    python scripts/eval_capabilities.py --list

Exit status is 1 when a case fails that is not a known gap. The DB-free
assertions over the same corpus are domovoi/tests/test_capability_corpus.py.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import copy
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
DEFAULT_CORPUS = HERE / "capability_corpus.json"
HOUSES_DIR = HERE / "capability_houses"
PLUGINS_FILE = HOUSES_DIR / "_plugins.json"
SETTINGS_JSX = REPO_ROOT / "web" / "static" / "settings.jsx"

# A database address nothing listens on: a stub run must never reach the
# live house's Postgres (the config default), and every connect is refused
# by the sandbox below before it is even tried.
NO_DB_URL = "postgresql+asyncpg://eval:eval@127.0.0.1:1/domovoi_eval_nodb"

os.environ.setdefault("USE_STUBS", "true")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VERDICTS = ("CAN", "CANT", "DEPENDS", "ADMIN", "DO_IT", "EXACT")
WHERE = ("voice", "chat", "phone")
KINDS = ("question", "command")
ASKERS = ("member", "proven_admin", "guest", "room_mode_phone", "shared_screen")
INTERNET = ("always", "sometimes", "never")
VOICE_ID = ("off", "unknown", "enrolled", "ambiguous")
FRIEND_HOUSES = ("off", "no_friends", "friends")
PLUGIN_STATUS = ("ok", "error", "crash")
# The list's "needs change" items. N7 (the hedge) and N10 (plugin names
# when asked) were resolved by the owner on 2026-11-25: the hedge is scored
# per case kind, and a "which plugins" question may name them
# (``allow_plugin_names``). 1.4.1 fixed N2-N6, N8, N9 and N11-N16; a case
# keeps ``needs_change`` only while its gap is open (N1 today).
NEEDS_CHANGE_IDS = tuple(f"N{i}" for i in range(1, 17) if i not in (7, 10))
CASE_STATUS = ("verified", "unverified")

# What a house fixture may say (the schema the test checks).
HOUSE_KEYS = {
    "extends", "note", "internet", "ai", "memory", "memory_extractor", "memory_items",
    "voice_id", "speaker", "plugins", "rooms", "printers", "calendars", "home_location",
    "quiet_hours_active", "friend_houses", "friends", "asker", "admin_session",
    "command_recordings", "voice_history_days", "music_library", "listenbrainz",
    "offline_kit", "timers", "alarms", "list_writes", "secrets", "update_channel",
}


# ─── Loading ──────────────────────────────────────────────────────────


@dataclass
class Case:
    id: str
    utterance: str
    where: str
    house: str
    expect: str
    kind: str
    group: int
    handler: str | None = None
    must_include: list[str] = field(default_factory=list)
    must_not_include: list[str] = field(default_factory=list)
    allow_plugin_names: bool = False
    allow_names: list[str] = field(default_factory=list)
    judge: bool = False
    rubric: str = ""
    needs_change: str | None = None
    status: str | None = None
    skip_reason: str | None = None
    stub: bool = False
    note: str = ""


def case_group(case_id: str) -> int:
    return int(case_id.split(".")[0])


def load_corpus(path: Path = DEFAULT_CORPUS) -> list[Case]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for c in data["cases"]:
        out.append(
            Case(
                id=c["id"], utterance=c["utterance"], where=c["where"], house=c["house"],
                expect=c["expect"], kind=c["kind"], group=case_group(c["id"]),
                handler=c.get("handler"),
                must_include=list(c.get("must_include") or []),
                must_not_include=list(c.get("must_not_include") or []),
                allow_plugin_names=bool(c.get("allow_plugin_names")),
                allow_names=list(c.get("allow_names") or []),
                judge=bool(c.get("judge")), rubric=c.get("rubric", ""),
                needs_change=c.get("needs_change"), status=c.get("status"),
                skip_reason=c.get("skip_reason"), stub=bool(c.get("stub")),
                note=c.get("note", ""),
            )
        )
    return out


def _merge(base: Any, over: Any) -> Any:
    """``over`` on top of ``base``: dicts merge key by key, anything else
    (lists included) is replaced."""
    if isinstance(base, dict) and isinstance(over, dict):
        out = copy.deepcopy(base)
        for k, v in over.items():
            out[k] = _merge(out.get(k), v) if k in out else copy.deepcopy(v)
        return out
    return copy.deepcopy(over)


def load_raw_houses(directory: Path = HOUSES_DIR) -> dict[str, dict]:
    return {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(directory.glob("*.json"))
        if not p.stem.startswith("_")
    }


def resolve_house(name: str, raw: dict[str, dict], _seen: tuple[str, ...] = ()) -> dict:
    if name in _seen:
        raise ValueError(f"house {name!r} extends itself: {' -> '.join(_seen + (name,))}")
    if name not in raw:
        raise KeyError(f"no house fixture {name!r}")
    spec = dict(raw[name])
    parent = spec.pop("extends", None)
    spec.pop("note", None)
    if parent is None:
        return spec
    return _merge(resolve_house(parent, raw, _seen + (name,)), spec)


def load_houses(directory: Path = HOUSES_DIR) -> dict[str, dict]:
    raw = load_raw_houses(directory)
    return {name: resolve_house(name, raw) for name in raw}


def load_plugin_catalogue(path: Path = PLUGINS_FILE) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8"))["plugins"]


# ─── Global checks (pure; unit-tested) ────────────────────────────────


def _plain(text: str) -> str:
    return (text or "").replace("’", "'").replace("‘", "'")


# The can't-do hedge the voice prompt asks for (clients/ollama.py,
# VOICE_QA_SYSTEM_PROMPT) and the variants the model writes.
HEDGE_RES = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bcan'?t do that yet\b",
        r"\bcannot do that yet\b",
        r"\bdon'?t think i can do that\b",
        r"\bnot able to do that yet\b",
        r"\bmaybe try again\b",
    )
]


def find_hedge(text: str) -> str | None:
    t = _plain(text)
    for rx in HEDGE_RES:
        m = rx.search(t)
        if m:
            return m.group(0)
    return None


def check_no_hedge(text: str, kind: str) -> list[str]:
    """The hedge fails a capability QUESTION (it wants a plain no); on a
    command the router missed it is the accepted safety net."""
    hit = find_hedge(text)
    if hit and kind == "question":
        return [f"hedge on a capability question: {hit!r}"]
    return []


# Plugin and provider names a reply must not say. The media tool's name is
# written with the repo's bracket trick (AGENTS.md rule 2) so this file
# never holds it literally. Keys are what ``allow_names`` can lift.
PLUGIN_NAME_RES: dict[str, re.Pattern[str]] = {
    "video-downloads": re.compile(r"\byt[-_ ]?d[l]p\b", re.IGNORECASE),
    "imagegen": re.compile(r"\bimage ?gen\b", re.IGNORECASE),
    "comfyui": re.compile(r"\bcomfy ?ui\b", re.IGNORECASE),
    "romm": re.compile(r"\bromm\b", re.IGNORECASE),
    "kiwix": re.compile(r"\bkiwix\b", re.IGNORECASE),
    "listenbrainz": re.compile(r"\blisten ?brainz\b", re.IGNORECASE),
}


def check_no_plugin_names(
    text: str,
    *,
    asker: str = "member",
    allow_plugin_names: bool = False,
    allow_names: tuple[str, ...] | list[str] = (),
    extra_names: tuple[str, ...] | list[str] = (),
) -> list[str]:
    """No plugin's NAME, for anyone (owner, 2026-11-25). The words
    "plugin" and "provider" are not names: "an admin can add it from the
    Plugins page" is the owner's Q2 answer, members included.
    ``allow_plugin_names``: the person asked which plugins are installed.
    ``asker`` is kept for callers; the rule is the same for every asker."""
    if allow_plugin_names:
        return []
    t = _plain(text)
    out = []
    for key, rx in PLUGIN_NAME_RES.items():
        if key in allow_names:
            continue
        m = rx.search(t)
        if m:
            out.append(f"names a plugin: {m.group(0)!r}")
    for name in extra_names:
        if name and re.search(rf"\b{re.escape(name)}\b", t, re.IGNORECASE):
            out.append(f"names a plugin: {name!r}")
    return out


def check_no_secrets(text: str, secrets: dict[str, str]) -> list[str]:
    t = (text or "").lower()
    return [
        f"reveals the {name.replace('_', ' ')}"
        for name, value in (secrets or {}).items()
        if value and value.lower() in t
    ]


# ─── Menu paths ───────────────────────────────────────────────────────


@dataclass
class SettingsTree:
    groups: dict[str, list[str]]      # group label -> its section labels
    marks: dict[str, list[str]]       # section label -> card labels

    def all_paths(self) -> set[str]:
        out = {"Settings"}
        for g, sections in self.groups.items():
            out.add(f"Settings → {g}")
            for s in sections:
                out.add(f"Settings → {g} → {s}")
                for mk in self.marks.get(s, []):
                    out.add(f"Settings → {g} → {s} → {mk}")
        return out


def _block(src: str, name: str) -> str:
    start = src.index(f"const {name} = [")
    return src[start: src.index("\n];", start)]


def load_settings_tree(path: Path = SETTINGS_JSX) -> SettingsTree:
    """The dashboard's own Settings registry (SETTINGS_GROUPS,
    SETTINGS_SECTIONS, SETTINGS_SEARCH_ITEMS in web/static/settings.jsx):
    the paths a reply may name."""
    src = path.read_text(encoding="utf-8")
    section_label = {
        m.group(1): m.group(2)
        for m in re.finditer(r"\{\s*id:\s*'([^']+)',\s*label:\s*'([^']+)'", _block(src, "SETTINGS_SECTIONS"))
    }
    groups: dict[str, list[str]] = {}
    for m in re.finditer(
        r"\{\s*id:\s*'[^']+',\s*label:\s*'([^']+)'[^\n]*\n?\s*sections:\s*\[([^\]]*)\]",
        _block(src, "SETTINGS_GROUPS"),
    ):
        ids = re.findall(r"'([^']+)'", m.group(2))
        groups[m.group(1)] = [section_label[i] for i in ids if i in section_label]
    marks: dict[str, list[str]] = {}
    for m in re.finditer(r"label:\s*'([^']+)',\s*section:\s*'([^']+)'", _block(src, "SETTINGS_SEARCH_ITEMS")):
        if m.group(2) in section_label:
            marks.setdefault(section_label[m.group(2)], []).append(m.group(1))
    if not groups:
        raise ValueError(f"no settings groups found in {path}")
    return SettingsTree(groups=groups, marks=marks)


_ARROW = r"\s*(?:→|->|›|>)\s*"
_ARROW_PATH_RE = re.compile(r"\bSettings((?:" + _ARROW + r"[^→>›\n.,;:!?\"()]+)+)")
_SPOKEN_PATH_RE = re.compile(r"\bsettings,? then ([^.;:!?\n]+)", re.IGNORECASE)


def _norm_label(s: str) -> str:
    s = _plain(s).lower().replace("&", " and ")
    return re.sub(r"\s+", " ", s).strip()


def _take(segment: str, labels: list[str], last: bool) -> str | None:
    """The label ``segment`` starts with (longest first), or None. A segment
    before the last must be exactly a label; the last may run on into the
    sentence ("Home location and set your town")."""
    seg = _norm_label(segment)
    for label in sorted(labels, key=len, reverse=True):
        lab = _norm_label(label)
        if seg == lab or (last and re.match(re.escape(lab) + r"(?![\w&])", seg)):
            return label
    return None


def _valid_segments(segments: list[str], tree: SettingsTree) -> bool:
    levels: list[list[str]] = [list(tree.groups)]
    chosen: list[str] = []
    for i, seg in enumerate(segments):
        if i >= 3:
            return False
        options = (
            list(tree.groups) if i == 0
            else tree.groups.get(chosen[0], []) if i == 1
            else tree.marks.get(chosen[1], [])
        )
        levels.append(options)
        got = _take(seg, options, last=(i == len(segments) - 1))
        if got is None:
            return False
        chosen.append(got)
    return True


def find_menu_paths(text: str) -> list[tuple[str, list[str]]]:
    """Every Settings path a reply names: ("as written", [segments])."""
    t = _plain(text)
    out = []
    for m in _ARROW_PATH_RE.finditer(t):
        segs = [s.strip() for s in re.split(_ARROW, m.group(1)) if s.strip()]
        out.append((m.group(0).strip(), segs))
    for m in _SPOKEN_PATH_RE.finditer(t):
        segs = [s.strip() for s in re.split(r",? then |, and then ", m.group(1)) if s.strip()]
        out.append((m.group(0).strip(), segs))
    return out


def check_menu_paths(text: str, tree: SettingsTree) -> list[str]:
    return [
        f"no such menu path: {written!r}"
        for written, segs in find_menu_paths(text)
        if not _valid_segments(segs, tree)
    ]


# ─── Verdict checks ───────────────────────────────────────────────────

_NEGATIVE_RE = re.compile(
    r"\b(?:can'?t|cannot|can not|not able|unable|don'?t|doesn'?t|isn'?t|aren'?t|won'?t|"
    r"no|not|never|nothing|off)\b",
    re.IGNORECASE,
)
_FALSE_SUCCESS_RE = re.compile(
    r"^\s*(?:sure|okay|ok|done|playing|added|sent|turning|ordering|calling|booked|locked)\b",
    re.IGNORECASE,
)
_REFUSAL_START_RE = re.compile(r"^\s*(?:no\b|sorry\b|i can'?t\b|i cannot\b|i'?m not able\b)", re.IGNORECASE)
_AFFIRMATIVE_RE = re.compile(
    r"\b(?:yes|yeah|sure|i can|you can|try|say|ask|just)\b", re.IGNORECASE
)
_EXPLAINS_RE = re.compile(r"\b(?:you can say|try saying|just say)\b", re.IGNORECASE)
_ADMIN_RE = re.compile(r"\badmin(?:istrator)?s?\b", re.IGNORECASE)


STAND_IN_PREFIX = "[stand-in: "


def check_verdict(case: Case, text: str, matched_handler: str | None) -> list[str]:
    t = _plain(text)
    out: list[str] = []
    if not t.strip():
        return ["no reply (silent)"]
    if case.handler is not None and matched_handler != case.handler:
        out.append(f"answered by {matched_handler or 'the model'}, expected {case.handler}")
    if t.startswith(STAND_IN_PREFIX):
        # A stand-in plugin took the turn: it did the thing. Only that is
        # known — not the real plugin's words.
        if case.expect == "CANT":
            out.append("a plugin's fast path took the turn (it did it)")
        for pat in case.must_not_include:
            if re.search(pat, t, re.IGNORECASE):
                out.append(f"says /{pat}/")
        return out
    if case.expect == "CANT":
        if _FALSE_SUCCESS_RE.search(t):
            out.append("claims to do it")
        elif not _NEGATIVE_RE.search(t):
            out.append("no plain no")
    elif case.expect == "CAN":
        if _REFUSAL_START_RE.search(t):
            out.append("refuses a CAN")
        elif not _AFFIRMATIVE_RE.search(t):
            out.append("no yes / no phrase to say")
    elif case.expect == "ADMIN":
        if not _ADMIN_RE.search(t):
            out.append("doesn't say an admin does it")
    elif case.expect == "DO_IT":
        if case.handler is None and _EXPLAINS_RE.search(t):
            out.append("explains instead of doing it")
    for pat in case.must_include:
        if not re.search(pat, t, re.IGNORECASE):
            out.append(f"missing /{pat}/")
    for pat in case.must_not_include:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            out.append(f"says {m.group(0)!r} (/{pat}/)")
    return out


def global_problems(
    case: Case, text: str, house: dict, tree: SettingsTree, catalogue: dict[str, dict]
) -> list[str]:
    extra = []
    for p in house.get("plugins") or []:
        extra += catalogue.get(p["slug"], {}).get("names", [])
    return (
        check_no_hedge(text, case.kind)
        + check_no_plugin_names(
            text, asker=house.get("asker", "member"),
            allow_plugin_names=case.allow_plugin_names,
            allow_names=case.allow_names, extra_names=extra,
        )
        + check_no_secrets(text, house.get("secrets") or {})
        + check_menu_paths(text, tree)
    )


# ─── The sandbox: the house's state from its fixture, nothing else ───


class NeedsDB(BaseException):
    """Raised (as a BaseException, so a handler's or the router's ``except
    Exception`` can't swallow it) when a turn reads database state the
    fixture doesn't answer, or writes anything (stub mode)."""


class NeedsNetwork(BaseException):
    pass


class NeedsModel(BaseException):
    pass


class NeedsLive(BaseException):
    """The turn reads more of the running core's live rooms (``ctx.app``)
    than the fixture's rooms give."""


class NeedsPluginError(BaseException):
    """A stand-in can't say what the real plugin says on this path (its
    service down, its model with AI off, its offline answer): such a turn
    is reported "needs plugin"."""


@dataclass
class Touches:
    db: list[str] = field(default_factory=list)
    net: list[str] = field(default_factory=list)
    model: list[str] = field(default_factory=list)
    live: list[str] = field(default_factory=list)
    # --ollama: reads the fixture doesn't answer, given no rows instead.
    empty: list[str] = field(default_factory=list)

    def clear(self) -> None:
        for v in (self.db, self.net, self.model, self.live, self.empty):
            v.clear()


TOUCHES = Touches()
# The house the running case is in, and whether reads the fixture doesn't
# answer fail the turn (stub) or come back empty (--ollama, so a model run
# isn't stopped by a profile or history read).
CURRENT: dict[str, Any] = {"house": {}, "lenient": False}


class _Rows:
    """Enough of a SQLAlchemy result for the reads below."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def scalar(self) -> Any:
        return next(iter(self._rows[0].values())) if self._rows else None

    def scalar_one_or_none(self) -> Any:
        return self.scalar()

    def first(self) -> Any:
        return tuple(self._rows[0].values()) if self._rows else None

    def all(self) -> list[tuple]:
        return [tuple(r.values()) for r in self._rows]

    fetchall = all

    def scalars(self) -> "_List":
        return _List([next(iter(r.values())) for r in self._rows])

    def mappings(self) -> "_List":
        return _List(list(self._rows))


class _List:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return list(self._items)

    def first(self) -> Any:
        return self._items[0] if self._items else None


def _plugin_rows(house: dict) -> list[dict[str, Any]]:
    catalogue = CURRENT.get("catalogue") or {}
    rows = []
    for p in house.get("plugins") or []:
        spec = catalogue.get(p["slug"], {})
        rows.append({
            "slug": p["slug"],
            "enabled": bool(p.get("enabled", True)),
            # A stand-in that raises or whose service is missing: the
            # registry's "error" status.
            "status": "ok" if p.get("status", "ok") == "ok" else "error",
            "manifest": {"capabilities": {"provides": list(spec.get("capabilities") or [])}},
        })
    return rows


def _home_value(house: dict) -> Any:
    loc = house.get("home_location")
    return json.dumps(loc) if loc else None


def _alarm_rows(house: dict) -> list[dict[str, Any]]:
    """The fixture's alarms ("07:00") as the timers table holds them: the
    next time each goes off, in UTC."""
    from datetime import timedelta, timezone

    from domovoi import household_time

    now = household_time.now()
    out = []
    for a in house.get("alarms") or []:
        hh, mm = (int(x) for x in str(a["at"]).split(":"))
        at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if at <= now:
            at += timedelta(days=1)
        out.append({"expires_at": at.astimezone(timezone.utc)})
    return sorted(out, key=lambda r: r["expires_at"])


def _timer_rows(house: dict) -> list[dict[str, Any]]:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    rows = [
        {"expires_at": now + timedelta(minutes=float(t["minutes_left"])),
         "created_at": now - timedelta(minutes=1), "label": t.get("label")}
        for t in house.get("timers") or []
    ]
    return sorted(rows, key=lambda r: r["expires_at"])


# The database reads a house fixture answers: (what, SQL pattern, rows).
FIXTURE_READS: list[tuple[str, re.Pattern[str], Any]] = [
    ("home location", re.compile(r"FROM household_settings WHERE key", re.I),
     lambda h, p: [{"value": _home_value(h)}] if (p or {}).get("k") == "home_location" else []),
    ("command recordings", re.compile(r"FROM command_capture_rooms", re.I),
     lambda h, p: [{"x": 1}] if h.get("command_recordings") else []),
    ("friends", re.compile(r"count\(\*\) FROM friend_houses_friends", re.I),
     lambda h, p: [{"n": len(h.get("friends") or []) if h.get("friend_houses") != "off" else 0}]),
    ("calendars", re.compile(r"count\(\*\) FROM calendar_sources", re.I),
     lambda h, p: [{"n": sum(1 for c in h.get("calendars") or [] if c.get("kind") == "ics")}]),
    ("printers", re.compile(r"count\(\*\) FROM printers", re.I),
     lambda h, p: [{"n": int(h.get("printers") or 0)}]),
    ("plugins", re.compile(r"FROM plugins\b", re.I), lambda h, p: _plugin_rows(h)),
    ("alarms", re.compile(r"SELECT expires_at FROM timers .*message IS NOT NULL", re.I),
     lambda h, p: _alarm_rows(h)),
    ("timers", re.compile(r"SELECT expires_at, created_at, label FROM timers", re.I),
     lambda h, p: _timer_rows(h)),
    ("room board", re.compile(r"SELECT board FROM satellites", re.I),
     lambda h, p: [{"board": "android_room" if h.get("asker") == "room_mode_phone" else "pi"}]),
]


class FixtureSession:
    """Stands where route() passes its AsyncSession and where
    ``session_scope()`` opens one: the reads in FIXTURE_READS come from the
    house fixture; any other read or any write is "needs db" (stub), or an
    empty answer / a no-op (--ollama, noted in the result)."""

    async def execute(self, stmt: Any, params: Any = None, *a: Any, **k: Any) -> _Rows:
        sql = " ".join(str(stmt).split())
        house = CURRENT["house"]
        for _what, rx, rows in FIXTURE_READS:
            if rx.search(sql):
                return _Rows(rows(house, params if isinstance(params, dict) else {}))
        if CURRENT["lenient"]:
            TOUCHES.empty.append(sql[:70])
            return _Rows([])
        TOUCHES.db.append(sql[:70])
        raise NeedsDB(sql[:70])

    async def scalar(self, stmt: Any, params: Any = None, *a: Any, **k: Any) -> Any:
        return (await self.execute(stmt, params)).scalar()

    async def scalars(self, stmt: Any, params: Any = None, *a: Any, **k: Any) -> _List:
        return (await self.execute(stmt, params)).scalars()

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def flush(self, *a: Any) -> None:
        return None

    async def close(self) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        if CURRENT["lenient"]:
            TOUCHES.empty.append(f"session.{name}")

            async def nothing(*a: Any, **k: Any) -> Any:
                return None

            return nothing if name not in ("add", "add_all", "expunge") else (lambda *a, **k: None)
        TOUCHES.db.append(f"session.{name}")
        raise NeedsDB(f"session.{name}")


@contextlib.asynccontextmanager
async def fixture_scope(*a: Any, **k: Any):
    yield FixtureSession()


class _LiveRoom:
    """One connected room of the fixture: its board, nothing more."""

    def __init__(self, room_id: str, board: str) -> None:
        self.room_id = room_id
        self.board = board

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        TOUCHES.live.append(f"room.{name}")
        raise NeedsLive(f"room.{name}")


class _LiveState:
    def __init__(self, house: dict) -> None:
        board = "android_room" if house.get("asker") == "room_mode_phone" else "pi"
        self.active_sessions = {
            r["id"]: _LiveRoom(r["id"], board) for r in house.get("rooms") or [] if r.get("online", True)
        }

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        TOUCHES.live.append(f"app.state.{name}")
        raise NeedsLive(f"app.state.{name}")


class LiveApp:
    """Stands where the streaming layer stamps ``ctx.app``: the fixture's
    online rooms are connected (``state.active_sessions``); anything else
    a handler reads there (calls, players, pairing) is "needs live"."""

    def __init__(self, house: dict) -> None:
        self.state = _LiveState(house)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        TOUCHES.live.append(f"app.{name}")
        raise NeedsLive(f"app.{name}")


class _NoModel:
    """The language model in stub mode: any call is a touch."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)

        def refuse(*a: Any, **k: Any) -> Any:
            TOUCHES.model.append(name)
            raise NeedsModel(name)

        return refuse


def _swap_everywhere(original: Any, replacement: Any) -> list[tuple[Any, str, Any]]:
    """Point every loaded module's binding of ``original`` (a function or
    object imported by name) at ``replacement``; returns the undo list."""
    undo = []
    for mod in list(sys.modules.values()):
        d = getattr(mod, "__dict__", None)
        if not d or not getattr(mod, "__name__", "").startswith(("domovoi", "web", "plugins")):
            continue
        for k, v in list(d.items()):
            if v is original:
                undo.append((mod, k, v))
                setattr(mod, k, replacement)
    return undo


# The router's own bookkeeping, kept in memory: the session row and its
# context (parked questions), and the turn's log rows (not written).
_SESSIONS: dict[Any, dict[str, Any]] = {}


async def _mem_get_or_create(self: Any, session_id: Any, room_id: Any) -> Any:
    from uuid import uuid4

    if session_id is not None and session_id in _SESSIONS:
        return session_id
    new_id = session_id or uuid4()
    _SESSIONS[new_id] = {}
    return new_id


async def _mem_get_context(self: Any, session_id: Any) -> dict[str, Any]:
    return dict(_SESSIONS.get(session_id) or {})


async def _mem_set_context_key(self: Any, session_id: Any, key: str, value: Any) -> None:
    _SESSIONS.setdefault(session_id, {})[key] = value


async def _mem_record_exchange(self: Any, *a: Any, **k: Any) -> None:
    return None


async def _no_persist(*a: Any, **k: Any) -> None:
    return None


@contextlib.contextmanager
def sandbox(model_client: Any = None) -> Iterator[None]:
    """No database (FixtureSession answers the fixture's reads), no
    outbound HTTP, and the language model only when ``model_client`` is
    given (else every call is "needs model")."""
    import asyncpg
    import httpx

    from domovoi import router
    from domovoi.clients import ollama as ollama_mod
    from domovoi.db import repositories
    from domovoi.db import session as db_session

    def no_db(*a: Any, **k: Any) -> Any:
        TOUCHES.db.append("connect")
        raise NeedsDB("connect")

    async def no_db_async(*a: Any, **k: Any) -> Any:
        TOUCHES.db.append("connect")
        raise NeedsDB("connect")

    async def no_send(self: Any, request: Any, *a: Any, **k: Any) -> Any:
        url = str(getattr(request, "url", "?"))
        if model_client is not None and _is_model_url(url):
            return await _ORIG["async_send"](self, request, *a, **k)
        TOUCHES.net.append(url)
        raise NeedsNetwork(url)

    def no_send_sync(self: Any, request: Any, *a: Any, **k: Any) -> Any:
        url = str(getattr(request, "url", "?"))
        if model_client is not None and _is_model_url(url):
            return _ORIG["sync_send"](self, request, *a, **k)
        TOUCHES.net.append(url)
        raise NeedsNetwork(url)

    client = model_client if model_client is not None else _NoModel()
    _ORIG["async_send"] = httpx.AsyncClient.send
    _ORIG["sync_send"] = httpx.Client.send
    undo: list[tuple[Any, str, Any]] = []
    undo += _swap_everywhere(ollama_mod.get_ollama_client, lambda: client)
    undo += _swap_everywhere(db_session.session_scope, fixture_scope)
    undo += _swap_everywhere(db_session.SessionLocal, lambda *a, **k: FixtureSession())
    repo = repositories.SessionRepository
    for name, fn in (("get_or_create", _mem_get_or_create), ("get_context", _mem_get_context),
                     ("set_context_key", _mem_set_context_key), ("record_exchange", _mem_record_exchange)):
        undo.append((repo, name, getattr(repo, name)))
        setattr(repo, name, fn)
    undo.append((router, "_persist_turn", router._persist_turn))
    router._persist_turn = _no_persist
    undo.append((asyncpg, "connect", asyncpg.connect))
    asyncpg.connect = no_db_async
    undo.append((httpx.AsyncClient, "send", httpx.AsyncClient.send))
    undo.append((httpx.Client, "send", httpx.Client.send))
    httpx.AsyncClient.send = no_send
    httpx.Client.send = no_send_sync
    try:
        yield
    finally:
        for obj, name, value in reversed(undo):
            setattr(obj, name, value)
        _SESSIONS.clear()


_ORIG: dict[str, Any] = {}
_MODEL_HOSTS: set[str] = set()


def _is_model_url(url: str) -> bool:
    from urllib.parse import urlsplit

    return urlsplit(url).netloc in _MODEL_HOSTS


# ─── Houses applied to the process ────────────────────────────────────


def _stand_in(slug: str, spec: dict, status: str) -> Any:
    """A stand-in for an installed plugin, built from _plugins.json: its
    feature label, example phrase, tool, AI declaration and (approximate)
    fast paths, so the help answer, the capability seams, the tool list,
    the AI-off gate and fast-path order see the plugin. Its reply is a
    marker, not the plugin's words."""
    from domovoi import ai_features
    from domovoi.handlers.base import FastPath, Handler, HandlerDisplay

    async def answer(self: Any, m: Any, ctx: Any, session: Any, uses_ai: bool = False) -> Any:
        from domovoi.models import Response

        if status == "crash":
            raise RuntimeError(f"stand-in {slug} crashed")
        if status == "error":
            raise NeedsPluginError(f"{slug} is in its error state")
        if uses_ai and ai_features.turned_off():
            raise NeedsPluginError(f"{slug} asks the model on this path, and AI is off")
        return Response(
            text=f"{STAND_IN_PREFIX}the {spec['feature']} feature handled this]",
            session_id=ctx.session_id, matched_handler=self.name,
        )

    async def execute(self: Any, intent: Any, ctx: Any, session: Any) -> Any:
        return await answer(self, None, ctx, session)

    async def execute_from_tool(self: Any, args: Any, ctx: Any, session: Any) -> Any:
        return await answer(self, None, ctx, session)

    cls = type(
        f"StandIn_{slug.replace('-', '_')}", (Handler,),
        {
            "name": spec.get("handler", slug.replace("-", "_")),
            "plugin_slug": slug,
            "priority_band": int(spec.get("band", 500)),
            "display": HandlerDisplay(label=spec["feature"], tone="info"),
            "example_phrases": tuple(spec.get("example_phrases") or ()),
            "requires_network": spec.get("requires_network", "no"),
            "needs_ai": bool(spec.get("needs_ai", False)),
            "tool_schema": {
                "name": spec.get("handler", slug.replace("-", "_")),
                "description": spec.get("tool_description", spec["feature"]),
                "parameters": {"type": "object", "properties": {}},
            },
            "execute": execute,
            "execute_from_tool": execute_from_tool,
            "fallback_offline": _offline_fallback,
        },
    )
    h = cls()
    h.fast_paths = []
    for entry in spec.get("fast_paths") or []:
        entry = {"pattern": entry} if isinstance(entry, str) else entry
        uses_ai = bool(entry.get("uses_ai"))

        async def method(self: Any, m: Any, ctx: Any, session: Any, _ai: bool = uses_ai) -> Any:
            return await answer(self, m, ctx, session, _ai)

        h.fast_paths.append(FastPath(re.compile(entry["pattern"]), method))
    return h


async def _offline_fallback(self: Any, intent: Any, ctx: Any, session: Any) -> Any:
    raise NeedsPluginError(f"{self.plugin_slug}'s offline answer")


def _keep_setting(days: Any) -> str:
    return "forever" if not days else str(days)


@contextlib.contextmanager
def applied(house: dict, catalogue: dict[str, dict], *, lenient: bool = False) -> Iterator[None]:
    """The house, in-process: the internet answer, the AI switch, the
    settings its facts read (Friend Houses, voice history, the memory
    extractor), Friend Houses' friend names (its fast paths match on them),
    the printers and the database reads in FIXTURE_READS, and its enabled
    plugins as stand-ins (with their capability seams and AI declaration).
    Anything else a turn reads from the database is "needs db" (stub)."""
    from domovoi import ai_features, egress, home_location
    from domovoi.capabilities import CAPABILITIES
    from domovoi.config import settings
    from domovoi.handlers import friend_houses as fh
    from domovoi.handlers import register_handler, unregister_handler
    from domovoi.printing import store as printer_store

    saved = {
        "friend_houses_enabled": settings.friend_houses_enabled,
        "memory_extractor_enabled": settings.memory_extractor_enabled,
        "voice_history_keep": settings.voice_history_keep,
        "command_capture_retention_days": settings.command_capture_retention_days,
        "music_similar_artists_enabled": settings.music_similar_artists_enabled,
        "music_popularity_enabled": settings.music_popularity_enabled,
    }
    saved_names = dict(fh._NAMES)
    saved_home = (home_location._cache, home_location._cached_at)
    saved_list_printers = printer_store.list_printers

    async def fixture_printers(s: Any = None) -> list[dict[str, Any]]:
        return [
            {"id": i, "name": f"Printer {i}", "ipp_address": f"192.168.1.{50 + i}", "rooms": []}
            for i in range(1, int(house.get("printers") or 0) + 1)
        ]

    CURRENT.update(house=house, lenient=lenient, catalogue=catalogue)
    added: list[tuple[str, str]] = []
    with egress.override_policy(house.get("internet", "always")), ai_features.override(
        bool(house.get("ai", True))
    ):
        try:
            settings.friend_houses_enabled = house.get("friend_houses", "friends") != "off"
            settings.memory_extractor_enabled = bool(house.get("memory_extractor", True))
            settings.voice_history_keep = _keep_setting(house.get("voice_history_days", 90))
            settings.command_capture_retention_days = 14
            # Up next's online music suggestions (the listening-history
            # service the list calls by name).
            settings.music_similar_artists_enabled = bool(house.get("listenbrainz"))
            settings.music_popularity_enabled = bool(house.get("listenbrainz"))
            fh._NAMES["forms"] = frozenset(
                form.replace("'", "")
                for f in house.get("friends") or []
                for form in fh.spoken_forms(f["name"])
            )
            fh._NAMES["at"] = time.monotonic()
            loc = house.get("home_location")
            home_location._cache, home_location._cached_at = (
                home_location.clean(loc, rematch=False) if loc else None, time.monotonic())
            printer_store.list_printers = fixture_printers
            for p in house.get("plugins") or []:
                if not p.get("enabled", True):
                    continue
                spec = catalogue[p["slug"]]
                h = _stand_in(p["slug"], spec, p.get("status", "ok"))
                register_handler(h)
                added.append((h.name, p["slug"]))
                for cap in spec.get("capabilities") or []:
                    CAPABILITIES.register(cap, object(), slug=p["slug"])
                ai_features.set_plugin_ai(
                    p["slug"], ai_features.plugin_ai_from_manifest(
                        {"ai": spec["ai"]} if spec.get("ai") else
                        {"capabilities": {"provides": list(spec.get("capabilities") or [])}}
                    ),
                )
            yield
        finally:
            for name, slug in added:
                unregister_handler(name)
                CAPABILITIES.unregister_all_for(slug)
                ai_features.set_plugin_ai(slug, None)
            for k, v in saved.items():
                setattr(settings, k, v)
            fh._NAMES.clear()
            fh._NAMES.update(saved_names)
            home_location._cache, home_location._cached_at = saved_home
            printer_store.list_printers = saved_list_printers
            CURRENT.update(house={}, lenient=False)


def build_context(house: dict) -> Any:
    from domovoi import voice_identifier as vi
    from domovoi.models import Context

    rooms = house.get("rooms") or []
    room = rooms[0]["id"] if rooms else "kitchen"
    vid = house.get("voice_id", "enrolled")
    speaker = house.get("speaker") or {}
    fields: dict[str, Any] = {
        "room_id": room,
        "online": house.get("internet", "always") != "never",
        "trigger": "wake_word",
        "app": LiveApp(house),
    }
    if vid == "enrolled":
        fields.update(person_id=int(speaker.get("person_id", 1)), voice_outcome=vi.OUTCOME_CONFIDENT,
                      presence_tier="high")
    elif vid == "unknown":
        fields.update(voice_outcome=vi.OUTCOME_NO_MATCH)
    elif vid == "ambiguous":
        fields.update(voice_outcome=vi.OUTCOME_AMBIGUOUS, voice_candidates=[1, 2])
    return Context(**fields)


# ─── Running one turn ─────────────────────────────────────────────────


@dataclass
class Turn:
    # reply | skipped | needs_model | needs_db | needs_live |
    # needs_network | needs_plugin
    outcome: str
    text: str = ""
    handler: str | None = None
    path: str | None = None
    detail: str = ""


async def run_voice_turn(utterance: str, ctx: Any) -> Turn:
    """The real ``router.route()``, with the fixture's session and rooms.
    The model is whatever the sandbox gives (none in stub mode)."""
    from domovoi.models import Intent
    from domovoi.router import route

    resp = await route(Intent(transcript=utterance, room_id=ctx.room_id), ctx, FixtureSession())
    return Turn("reply", resp.text or "", resp.matched_handler, resp.matched_path)


async def run_chat_turn(utterance: str, *, model_url: str | None, model: str | None) -> Turn:
    """The home-server chat (web/backend/api/chat.py send_message): refused
    with AI off; then the house's own answer (house_facts.chat_reply, the
    privacy and "can you …?" matcher), then the help desk's fixed reply,
    then the model."""
    from domovoi import ai_features, house_facts
    from domovoi.clients import ollama as ollama_mod
    from domovoi.config import settings
    from domovoi.help_desk import answer as help_answer

    if ai_features.turned_off():
        return Turn("reply", ai_features.TURNED_OFF_REASON, None, "chat_refused")

    async def previous_assistant() -> None:
        return None

    fact = await house_facts.chat_reply(
        utterance, get_setting=house_facts.core_setting, scope=fixture_scope,
        previous_assistant=previous_assistant,
    )
    if fact is not None:
        return Turn("reply", fact, "house_facts", "chat")
    if model_url is None:
        return Turn("needs_model", detail="the chat model")
    system = ollama_mod.chat_system_prompt(None)
    if help_answer.looks_like_help(utterance):
        from web.backend.api import help as help_api

        turn = await help_api.help_turn_for(utterance, help_thread=False)
        if turn is not None:
            system = help_answer.system_prompt(turn, bot=settings.bot_name)
            if not turn.covered:
                return Turn("reply", help_answer.not_covered_answer(turn), None, "chat_help")
    import ollama

    resp = await ollama.AsyncClient(host=model_url).chat(
        model=model or settings.ollama_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": utterance}],
    )
    return Turn("reply", resp["message"]["content"], None, "chat")


async def run_case(
    case: Case, house: dict, catalogue: dict[str, dict], *,
    model_url: str | None = None, model: str | None = None,
) -> Turn:
    if case.skip_reason:
        return Turn("skipped", detail=case.skip_reason)
    TOUCHES.clear()
    lenient = model_url is not None
    with applied(house, catalogue, lenient=lenient):
        try:
            if case.where == "voice":
                turn = await run_voice_turn(case.utterance, build_context(house))
            else:
                turn = await run_chat_turn(case.utterance, model_url=model_url, model=model)
        except NeedsModel as e:
            return Turn("needs_model", detail=str(e))
        except NeedsDB as e:
            return Turn("needs_db", detail=str(e))
        except NeedsNetwork as e:
            return Turn("needs_network", detail=str(e))
        except NeedsLive as e:
            return Turn("needs_live", detail=f"reads the live rooms ({e})")
        except NeedsPluginError as e:
            return Turn("needs_plugin", detail=f"the stand-in can't speak for the plugin: {e}")
    # Something swallowed a refusal: the turn still read what we don't have.
    if TOUCHES.live:
        return Turn("needs_live", turn.text, turn.handler, turn.path, f"read {TOUCHES.live[0]}")
    if TOUCHES.db:
        return Turn("needs_db", turn.text, turn.handler, turn.path, f"touched {TOUCHES.db[0]}")
    if TOUCHES.net:
        return Turn("needs_network", turn.text, turn.handler, turn.path, f"reached {TOUCHES.net[0]}")
    if TOUCHES.model and not lenient:
        return Turn("needs_model", turn.text, turn.handler, turn.path, f"called {TOUCHES.model[0]}")
    if TOUCHES.empty:
        turn.detail = (turn.detail + "; " if turn.detail else "") + (
            f"{len(TOUCHES.empty)} database read(s) answered empty, e.g. {TOUCHES.empty[0]}")
    return turn


# ─── Scoring ──────────────────────────────────────────────────────────


@dataclass
class Result:
    id: str
    group: int
    utterance: str
    house: str
    where: str
    expect: str
    outcome: str
    status: str           # pass | fail | known_gap | fixed | needs_model | needs_db | ... | skipped
    text: str = ""
    handler: str | None = None
    path: str | None = None
    problems: list[str] = field(default_factory=list)
    needs_change: str | None = None
    judge_score: int | None = None
    judge_reason: str = ""
    detail: str = ""


def score(case: Case, turn: Turn, house: dict, tree: SettingsTree, catalogue: dict) -> Result:
    r = Result(
        id=case.id, group=case.group, utterance=case.utterance, house=case.house,
        where=case.where, expect=case.expect, outcome=turn.outcome, status=turn.outcome,
        text=turn.text, handler=turn.handler, path=turn.path, needs_change=case.needs_change,
        detail=turn.detail,
    )
    if turn.outcome not in ("reply", "raised"):
        return r
    problems = check_verdict(case, turn.text, turn.handler)
    if turn.outcome == "raised":
        problems.insert(0, f"handler raised ({turn.detail}); the turn is silent")
    problems += global_problems(case, turn.text, house, tree, catalogue)
    r.problems = list(dict.fromkeys(problems))
    ok = not r.problems
    if case.needs_change:
        r.status = "fixed" if ok else "known_gap"
    else:
        r.status = "pass" if ok else "fail"
    return r


JUDGE_PROMPT = (
    "You grade one reply from a home assistant called Domovoi to a question about its own "
    "abilities. Score it 0-3 against the rubric: 3 fully right, 2 right with a small flaw, "
    "1 partly wrong, 0 wrong or misleading. Answer with the digit, a space, and one short "
    "reason.\n\nQuestion: {utterance}\nHouse: {house}\nExpected verdict: {expect}\n"
    "Rubric: {rubric}\nReply: {reply}"
)


async def judge_result(r: Result, case: Case, model_url: str, model: str) -> None:
    import ollama

    resp = await ollama.AsyncClient(host=model_url).chat(
        model=model,
        messages=[{"role": "user", "content": JUDGE_PROMPT.format(
            utterance=case.utterance, house=case.house, expect=case.expect,
            rubric=case.rubric or case.note, reply=r.text,
        )}],
        options={"temperature": 0},
    )
    out = (resp["message"]["content"] or "").strip()
    m = re.search(r"[0-3]", out)
    r.judge_score = int(m.group(0)) if m else 0
    r.judge_reason = out[:200]
    if r.judge_score < 2 and r.status in ("pass", "fixed"):
        r.problems.append(f"judge {r.judge_score}/3: {r.judge_reason}")
        r.status = "known_gap" if case.needs_change else "fail"


# ─── Reporting ────────────────────────────────────────────────────────

STATUS_COLUMNS = ("pass", "fail", "known_gap", "fixed", "needs_model", "needs_db",
                  "needs_live", "needs_network", "needs_plugin", "skipped")


def report(results: list[Result], verbose: bool = False) -> int:
    groups = sorted({r.group for r in results})
    head = f"{'group':>5} {'cases':>5} " + " ".join(f"{c.replace('needs_', 'n/'):>9}" for c in STATUS_COLUMNS)
    print(head)
    for g in groups:
        rows = [r for r in results if r.group == g]
        print(f"{g:>5} {len(rows):>5} " + " ".join(
            f"{sum(1 for r in rows if r.status == c):>9}" for c in STATUS_COLUMNS))
    print(f"{'all':>5} {len(results):>5} " + " ".join(
        f"{sum(1 for r in results if r.status == c):>9}" for c in STATUS_COLUMNS))
    print()

    def show(r: Result) -> None:
        print(f"  {r.id:6} [{r.house}] {r.utterance!r}")
        print(f"         reply ({r.handler or '-'}/{r.path or '-'}): {r.text!r}")
        for p in r.problems:
            print(f"         - {p}")

    fails = [r for r in results if r.status == "fail"]
    if fails:
        print(f"UNEXPECTED FAILURES ({len(fails)})")
        for r in fails:
            show(r)
        print()
    gaps = [r for r in results if r.status == "known_gap"]
    if gaps:
        by_n: dict[str, list[Result]] = {}
        for r in gaps:
            by_n.setdefault(r.needs_change or "?", []).append(r)
        print(f"KNOWN GAPS ({len(gaps)})")
        for n in sorted(by_n, key=lambda s: int(s[1:]) if s[1:].isdigit() else 99):
            print(f" known gap ({n}): {', '.join(r.id for r in by_n[n])}")
            if verbose:
                for r in by_n[n]:
                    show(r)
        print()
    fixed = [r for r in results if r.status == "fixed"]
    if fixed:
        print(f"FIXED? ({len(fixed)}) — a known gap now passes; check, then drop its needs_change")
        for r in fixed:
            show(r)
        print()
    if verbose:
        for r in results:
            if r.status == "pass":
                print(f"  pass   {r.id:6} {r.utterance!r} -> {r.text[:100]!r}")
            elif r.status.startswith("needs_"):
                print(f"  {r.status:13} {r.id:6} {r.utterance!r} ({r.detail})")
    passed = sum(1 for r in results if r.status == "pass")
    ran = sum(1 for r in results if r.status in ("pass", "fail", "known_gap", "fixed"))
    print(f"{passed} passed, {len(fails)} failed, {len(gaps)} known gaps, {len(fixed)} fixed? "
          f"— of {ran} scored ({len(results)} selected)")
    return 1 if fails else 0


# ─── Main ─────────────────────────────────────────────────────────────


async def run_all(
    cases: list[Case], houses: dict[str, dict], catalogue: dict[str, dict], tree: SettingsTree, *,
    model_url: str | None = None, model: str | None = None, tool_model: str | None = None,
    judge_model: str | None = None,
) -> list[Result]:
    client = None
    if model_url is not None:
        from urllib.parse import urlsplit

        from domovoi.clients.ollama import RealOllamaClient
        from domovoi.config import settings

        _MODEL_HOSTS.add(urlsplit(model_url).netloc)
        client = RealOllamaClient(
            url=model_url, qa_model=model or settings.ollama_model,
            tool_model=tool_model or settings.ollama_tool_model,
        )
    by_id = {c.id: c for c in cases}
    results = []
    with sandbox(client):
        for case in cases:
            house = houses[case.house]
            turn = await run_case(case, house, catalogue, model_url=model_url, model=model)
            r = score(case, turn, house, tree, catalogue)
            results.append(r)
    if judge_model and model_url:
        for r in results:
            if by_id[r.id].judge and r.status in ("pass", "fail", "known_gap", "fixed") and r.text:
                await judge_result(r, by_id[r.id], model_url, judge_model)
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ollama", metavar="URL", help="Ollama server for real-model runs")
    p.add_argument("--model", help="Q&A / chat model (default: settings.ollama_model)")
    p.add_argument("--tool-model", help="tool-routing model (default: settings.ollama_tool_model)")
    p.add_argument("--judge-model", help="score judge:true cases 0-3 with this model (needs --ollama)")
    p.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    p.add_argument("--houses", type=Path, default=HOUSES_DIR)
    p.add_argument("--case", action="append", default=[], metavar="ID", help="run only this case id (repeatable)")
    p.add_argument("--only-group", action="append", type=int, default=[], metavar="N")
    p.add_argument("--json", type=Path, help="write the full JSON report here")
    p.add_argument("--list", action="store_true", help="print the corpus and exit")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")
    # Handlers log their expected failures (a stand-in that raises); the
    # report says what matters.
    import logging

    logging.basicConfig(level=logging.CRITICAL)
    if args.judge_model and not args.ollama:
        p.error("--judge-model needs --ollama")

    # Never the live database (the config default) — before domovoi loads.
    os.environ["DATABASE_URL"] = NO_DB_URL

    cases = load_corpus(args.corpus)
    if args.case:
        cases = [c for c in cases if c.id in set(args.case)]
    if args.only_group:
        cases = [c for c in cases if c.group in set(args.only_group)]
    if args.list:
        for c in cases:
            flags = " ".join(x for x in (
                c.needs_change or "", "judge" if c.judge else "", "stub" if c.stub else "",
                f"skip: {c.skip_reason}" if c.skip_reason else "") if x)
            print(f"{c.id:6} {c.where:5} {c.expect:7} {c.house:28} {c.utterance!r} {flags}")
        return 0
    if not cases:
        print("no cases selected", file=sys.stderr)
        return 2
    houses = load_houses(args.houses)
    catalogue = load_plugin_catalogue(args.houses / "_plugins.json")
    tree = load_settings_tree()
    t0 = time.monotonic()
    target = f"ollama {args.ollama} model {args.model or '(default)'}" if args.ollama else "stub (no model)"
    print(f"target: {target}, {len(cases)} cases")
    results = asyncio.run(run_all(
        cases, houses, catalogue, tree, model_url=args.ollama, model=args.model,
        tool_model=args.tool_model, judge_model=args.judge_model,
    ))
    code = report(results, verbose=args.verbose)
    print(f"({time.monotonic() - t0:.1f}s)")
    if args.json:
        args.json.write_text(json.dumps({
            "target": target, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "results": [asdict(r) for r in results],
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
