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
                   settings.ollama_tool_model). Only the model is real:
                   a turn that needs database state the fixture doesn't
                   hold, the live rooms or a real plugin is reported
                   "needs db" / "needs live" / "needs plugin", as in stub
                   mode. A chat case gets the chat's system prompt (or the
                   help desk's, for a help question).

  --runs N         Run every case N times (a model's answers vary) and
                   report each case's pass rate (2/3) and each group's.

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
# "away": a phone's chat from away over the house's onion (1.5.0 away chat,
# web/backend/away_bridge.py): the home chat under that phone's identity.
# "chat_mic": the phone chat's mic button (domovoi/phone_voice.py _answer)
# without speech: route() as that phone, credited to its person, one
# conversation per chat thread. Typed chat's house actions (N19, 1.5.0
# wf/chat-actions) answer as this does.
WHERE = ("voice", "chat", "playbox", "phone", "away", "chat_mic")
# Who can chat from away: the enrolled phone's person; a valid admin_grant
# makes that one turn a proven admin. Shared screens and guests are refused
# at enrol (AWAY-CHAT-WIRE.md section 8), so no away case has those askers.
AWAY_ASKERS = ("member", "proven_admin")
#: Stand-ins for the household token and a grant's one-turn admin session.
AWAY_HOUSEHOLD_TOKEN = "fixture-household-token"
AWAY_ADMIN_SESSION = "fixture-away-admin-session"


def away_available() -> bool:
    """Whether this checkout has the away chat bridge (1.5.0 wf/away-chat)."""
    import importlib.util
    return importlib.util.find_spec("web.backend.away_bridge") is not None

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
# keeps ``needs_change`` only while its gap is open (N1 today). N17
# (device-state questions answered by the model) was found 2026-11-26 and
# fixed with house_facts' device-state answers (16.4-16.4g are stub cases).
# N18 (2026-11-26): a proven admin gets the Plugins-page pointer for a missing feature.
# N19 (2026-11-27): a typed house action (announce, timer, list, music, drop-in,
# broadcast) runs as the chat mic does (path B), never a pretend-done.
# N20 (2026-11-27): the intercom announces the pronoun ('announce that' plays
# "that") and keeps a leading 'that' in the message.
NEEDS_CHANGE_IDS = tuple(f"N{i}" for i in range(1, 22) if i not in (7, 10))
CASE_STATUS = ("verified", "unverified")

# What a house fixture may say (the schema the test checks).
HOUSE_KEYS = {
    "extends", "note", "internet", "ai", "memory", "memory_extractor", "memory_items",
    "voice_id", "speaker", "plugins", "rooms", "printers", "calendars", "home_location",
    "quiet_hours_active", "friend_houses", "friends", "asker", "admin_session",
    "command_recordings", "voice_history_days", "music_library", "listenbrainz",
    "offline_kit", "timers", "alarms", "list_writes", "secrets", "update_channel",
    "people", "devices",
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
    # Typed chat: the phone or browser it is typed on (a device id of the
    # house's ``devices``), and the thread (turns in one thread see each
    # other). None: the house's default device for its asker.
    device: str | None = None
    thread: str = "t1"
    # Turns said first, in order ({"say", "device"?, "thread"?}); only the
    # case's own utterance is scored.
    before: list[dict[str, Any]] = field(default_factory=list)
    # After the turn: memories that must be saved / must be gone, as
    # [person_id or null, regex on the memory's text].
    stored: list[list[Any]] = field(default_factory=list)
    not_stored: list[list[Any]] = field(default_factory=list)
    # The reply is one of a set of owner-approved lines (the names of
    # constants in domovoi/owner_easter_eggs.py, e.g. "CLAIM_LINES"), its
    # {name}/{other} filled with the house's People list; ``one_of_rest``:
    # what must follow the line (a regex; none: nothing follows).
    one_of: list[str] = field(default_factory=list)
    one_of_rest: str | None = None
    # The reply is none of these line sets (a fact, never a joke).
    not_one_of: list[str] = field(default_factory=list)
    # The reply differs from the last ``before`` turn's reply.
    differs_from_before: bool = False
    # What the turn did (Turn.actions, e.g. "announce:kitchen,garage:<text>",
    # "timer:600", "list_item:Shopping:milk", "music:kitchen:start"): each
    # regex must match one action; ``did_not`` regexes must match none.
    did: list[str] = field(default_factory=list)
    did_not: list[str] = field(default_factory=list)
    # A feature the case needs on this checkout ("chat_actions": typed chat's
    # house commands, 1.5.0 wf/chat-actions); skipped without it.
    requires: str | None = None


def case_group(case_id: str) -> int:
    return int(case_id.split(".")[0])


def _as_list(v: Any) -> list[str]:
    return [v] if isinstance(v, str) else list(v or [])


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
                device=c.get("device"), thread=c.get("thread", "t1"),
                before=list(c.get("before") or []),
                stored=list(c.get("stored") or []), not_stored=list(c.get("not_stored") or []),
                one_of=_as_list(c.get("one_of")), one_of_rest=c.get("one_of_rest"),
                not_one_of=_as_list(c.get("not_one_of")),
                differs_from_before=bool(c.get("differs_from_before")),
                did=_as_list(c.get("did")), did_not=_as_list(c.get("did_not")),
                requires=c.get("requires"),
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


# Only the form Domovoi writes its places in: "Settings → Group → Section",
# capitalised, with the arrow. Prose ("open your phone's settings, then
# Apps") is general advice, not a claim about Domovoi's menus.
_ARROW = r"\s*(?:→|->)\s*"
_ARROW_PATH_RE = re.compile(r"\bSettings((?:" + _ARROW + r"[^→\n.,;:!?\"()]+)+)")


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
    return out


def check_menu_paths(text: str, tree: SettingsTree) -> list[str]:
    return [
        f"no such menu path: {written!r}"
        for written, segs in find_menu_paths(text)
        if not _valid_segments(segs, tree)
    ]


# ─── Owner-approved line sets (domovoi/owner_easter_eggs.py) ─────────

LINE_SETS = ("CLAIM_LINES", "WHO_LINES", "ONE_PERSON_LINES", "NO_NAME_LINES")


def line_set(name: str) -> tuple[str, ...]:
    from domovoi import owner_easter_eggs

    if name not in LINE_SETS:
        raise KeyError(f"no line set {name!r}")
    return tuple(getattr(owner_easter_eggs, name))


def _template_re(template: str, names: list[str]) -> re.Pattern[str]:
    """A line as a regex: {name} and {other} are names from ``names``; a
    second {name} is the same person (a backreference)."""
    who = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) or r"(?!)"
    out, seen = "", set()
    for part in re.split(r"(\{name\}|\{other\})", template):
        if part in ("{name}", "{other}"):
            key = part[1:-1]
            out += f"(?P={key})" if key in seen else f"(?P<{key}>{who})"
            seen.add(key)
        else:
            out += re.escape(part)
    return re.compile(out + r"(?:\s+(?P<rest>.*))?\Z", re.S)


def match_line(text: str, sets: list[str], names: list[str]) -> tuple[str, str] | None:
    """``(template, rest)`` when ``text`` starts with a line of ``sets``
    (filled with ``names``, {name} and {other} two different people);
    else None."""
    t = (text or "").strip()
    for name in sets:
        for template in line_set(name):
            m = _template_re(template, names).match(t)
            if m is None:
                continue
            g = m.groupdict()
            if g.get("name") and g.get("other") and g["name"] == g["other"]:
                continue
            return template, (g.get("rest") or "").strip()
    return None


def check_lines(case: "Case", text: str, names: list[str], before: list[str]) -> list[str]:
    out = []
    if case.one_of:
        got = match_line(text, case.one_of, names)
        if got is None:
            out.append(f"not a line of {'/'.join(case.one_of)} (with this house's People names)")
        elif case.one_of_rest is None and got[1]:
            out.append(f"more after the line: {got[1]!r}")
        elif case.one_of_rest is not None and not re.fullmatch(case.one_of_rest, got[1], re.I | re.S):
            out.append(f"after the line, {got[1]!r} is not /{case.one_of_rest}/")
    if case.not_one_of:
        everyone = names or ["(nobody)"]
        if match_line(text, case.not_one_of, everyone) is not None:
            out.append("a joke line where a fact is due")
    if case.differs_from_before and before and before[-1].strip() == (text or "").strip():
        out.append("the same line as the turn before")
    return out


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

    def __init__(self, rows: list[dict[str, Any]], rowcount: int | None = None) -> None:
        self._rows = rows
        self.rowcount = len(rows) if rowcount is None else rowcount

    def scalar_one(self) -> Any:
        return self.scalar()

    def one(self) -> Any:
        if not self._rows:
            raise LookupError("no row")
        return tuple(self._rows[0].values())

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


# The house's state a case changes, kept in memory for one case: the
# memories (from the fixture's ``memory_items``) and typed-chat threads.
STATE: dict[str, Any] = {}


def reset_state(house: dict) -> None:
    speaker = int((house.get("speaker") or {}).get("person_id", 1))
    STATE.clear()
    STATE["memories"] = [
        {"id": i, "person_id": int(m.get("person_id", speaker)), "body": m["fact"]}
        for i, m in enumerate(house.get("memory_items") or [], start=1)
    ]
    STATE["next_id"] = len(STATE["memories"]) + 1
    STATE["threads"] = {}      # thread name -> [{"id", "role", "stats"}]
    STATE["thread_ids"] = {}   # thread name -> int
    STATE["next_row"] = 1
    STATE["chat_log"] = {}     # thread name -> [{"role", "content"}] (the model's history)
    STATE["actions"] = []      # what a chat_mic turn did (Turn.actions)
    STATE["sessions"] = {}     # conversation id -> its context (sessions.context)
    STATE["mic_sessions"] = {}  # chat thread name -> its conversation id
    STATE["list_items"] = []
    STATE["saying"] = {}
    try:
        from domovoi import phone_voice

        phone_voice._SESSIONS.clear()
    except Exception:  # noqa: BLE001 - a checkout without the phone mic
        pass


def _mem_insert(h: dict, p: dict) -> list[dict[str, Any]]:
    mid = STATE["next_id"]
    STATE["next_id"] += 1
    STATE["memories"].append({"id": mid, "person_id": int(p["person_id"]), "body": p["body"],
                              "status": p.get("status", "active")})
    return [{"id": mid}]


def _mem_active(h: dict, p: dict) -> list[dict[str, Any]]:
    return [{"id": m["id"], "body": m["body"], "topic": None, "source": "explicit"}
            for m in STATE["memories"]
            if m["person_id"] == int(p["pid"]) and m.get("status", "active") == "active"]


def _mem_fuzzy(h: dict, p: dict) -> list[dict[str, Any]]:
    frag = str(p["pat"]).strip("%").lower()
    hits = [m for m in STATE["memories"]
            if m["person_id"] == int(p["pid"]) and m.get("status", "active") == "active"
            and frag in m["body"].lower()]
    hits.sort(key=lambda m: (len(m["body"]), m["id"]))
    return [{"id": m["id"], "body": m["body"]} for m in hits[: int(p.get("limit", 5))]]


def _mem_delete(h: dict, p: dict) -> tuple[list[dict[str, Any]], int]:
    before = len(STATE["memories"])
    STATE["memories"] = [m for m in STATE["memories"]
                         if not (m["id"] == int(p["id"]) and m["person_id"] == int(p["pid"]))]
    return [], before - len(STATE["memories"])


def _device_row(h: dict, p: dict) -> list[dict[str, Any]]:
    for d in h.get("devices") or []:
        if d["id"] == p.get("d"):
            return [{"owner_person_id": d.get("person_id"), "shared_screen": bool(d.get("shared_screen"))}]
    return []


def _thread_rows(p: dict) -> list[dict[str, Any]]:
    for name, tid in STATE["thread_ids"].items():
        if tid == int(p["t"]):
            return STATE["threads"][name]
    return []


def _thread_name(p: dict) -> str | None:
    return next((n for n, tid in STATE["thread_ids"].items() if tid == int(p["t"])), None)


def _recent_requests(h: dict, p: dict) -> list[dict[str, Any]]:
    name = _thread_name(p)
    said = [m["content"] for m in STATE["chat_log"].get(name, []) if m["role"] == "user"]
    if name in STATE.get("saying", {}):
        said.append(STATE["saying"][name])
    now = time.time()
    return [{"content": c, "at": now} for c in reversed(said)][:10]


def _last_reply_stats(h: dict, p: dict) -> list[dict[str, Any]]:
    rows = [r for r in _thread_rows(p) if r["id"] < int(p["u"]) and r["role"] == "assistant"]
    return [{"stats": rows[-1].get("stats")}] if rows else []


def _previous_chat_row(h: dict, p: dict) -> list[dict[str, Any]]:
    rows = [r for r in _thread_rows(p) if r["id"] < int(p["u"])]
    if not rows:
        return []
    r = rows[-1]
    return [{"role": r["role"], "stats": r.get("stats")}]


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
    ("people", re.compile(r"SELECT id, name FROM people ORDER BY id", re.I),
     lambda h, p: [{"id": x["id"], "name": x["name"]} for x in h.get("people") or []]),
    ("people names", re.compile(r"SELECT name FROM people ORDER BY id", re.I),
     lambda h, p: [{"name": x["name"]} for x in h.get("people") or []]),
    ("memory saved", re.compile(r"INSERT INTO memories", re.I), _mem_insert),
    ("memories", re.compile(r"SELECT id, body, topic, source FROM memories WHERE person_id = :pid "
                            r"AND status = 'active'", re.I), _mem_active),
    ("memory search", re.compile(r"SELECT id, body FROM memories .*ILIKE :pat", re.I), _mem_fuzzy),
    ("memory deleted", re.compile(r"DELETE FROM memories WHERE id = :id AND person_id = :pid", re.I),
     _mem_delete),
    ("device's person", re.compile(r"SELECT owner_person_id, shared_screen FROM devices", re.I),
     _device_row),
    ("chat's previous row", re.compile(r"SELECT role, stats FROM chat_messages WHERE thread_id", re.I),
     _previous_chat_row),
    # The intercom's "announce that" in a chat reads the person's own recent
    # messages in the thread (phone_voice.recent_requests): user rows only,
    # never a reply, newest first.
    ("chat's recent requests", re.compile(r"SELECT content, EXTRACT\(EPOCH FROM created_at\) FROM chat_messages", re.I),
     _recent_requests),
    # The typed send's hop: did the house's last reply ask something back?
    ("chat's last reply", re.compile(r"SELECT stats FROM chat_messages WHERE thread_id = :t AND role = 'assistant' AND id < :u", re.I),
     _last_reply_stats),
    ("room board", re.compile(r"SELECT board FROM satellites", re.I),
     lambda h, p: [{"board": "android_room" if h.get("asker") == "room_mode_phone" else "pi"}]),
]


def _sess_insert(h: dict, p: dict) -> tuple[list, int]:
    STATE["sessions"].setdefault(str(p.get("id")), {})
    return [], 1


def _sess_id(h: dict, p: dict) -> list[dict[str, Any]]:
    return [{"id": p["id"]}] if str(p.get("id")) in STATE["sessions"] else []


def _sess_context(h: dict, p: dict) -> list[dict[str, Any]]:
    sid = str(p.get("id"))
    return [{"context": STATE["sessions"][sid]}] if sid in STATE["sessions"] else []


def _sess_update(h: dict, p: dict) -> tuple[list, int]:
    if "ctx" in p:
        STATE["sessions"][str(p["id"])] = json.loads(p["ctx"])
    return [], 1


def _did(kind: str):
    def write(h: dict, p: dict) -> tuple[list, int]:
        n = len(STATE["actions"]) + 1
        if kind == "timer":
            secs = None
            if p.get("expires_at") is not None:
                from datetime import datetime, timezone

                start = p.get("created_at") or datetime.now(tz=timezone.utc)
                secs = round((p["expires_at"] - start).total_seconds())
            STATE["actions"].append(f"timer:{secs}")
        else:
            STATE["actions"].append(kind)
        return [{"id": n}], 1
    return write


def _list_rows(h: dict, p: dict) -> list[dict[str, Any]]:
    return [{"id": i, "name": name, "position": i, "open": 0, "done": 0, "c": None, "u": None}
            for i, name in enumerate(h.get("lists") or ["Shopping"], start=1)]


def _list_item_insert(h: dict, p: dict) -> tuple[list, int]:
    if h.get("list_writes") == "error":
        raise RuntimeError("the fixture's list write fails (list_writes: error)")
    STATE["list_items"].append(dict(p))
    name = next((r["name"] for r in _list_rows(h, p) if r["id"] == p.get("l")), p.get("l"))
    STATE["actions"].append(f"list_item:{name}:{p.get('t')}")
    return [{"id": len(STATE["list_items"])}], 1


def _list_item_read(h: dict, p: dict) -> list[dict[str, Any]]:
    i = int(p["id"]) - 1
    if not 0 <= i < len(STATE["list_items"]):
        return []
    it = STATE["list_items"][i]
    return [{"id": p["id"], "list_id": it["l"], "text": it["t"], "done": False, "done_at": None,
             "position": i, "pid": it.get("p"), "name": None, "room": it.get("r"),
             "via": it.get("v"), "c": None, "u": None}]


def _room_rows(h: dict, p: dict) -> list[dict[str, Any]]:
    return [{"room_id": r["id"], "label": None, "sat_type": "pi", "seen": True}
            for r in h.get("rooms") or []]


ACTION_READS: list[tuple[str, re.Pattern[str], Any]] = [
    # The conversation and the turn's bookkeeping (router._persist_turn).
    ("conversation", re.compile(r"INSERT INTO sessions\b", re.I), _sess_insert),
    ("conversation", re.compile(r"SELECT id FROM sessions WHERE id", re.I), _sess_id),
    ("conversation", re.compile(r"SELECT context FROM sessions WHERE id", re.I), _sess_context),
    ("conversation", re.compile(r"UPDATE sessions\b", re.I), _sess_update),
    ("turn log", re.compile(r"(?:INSERT INTO|UPDATE) (?:intents_log|conversation_log|media_plays)\b", re.I),
     lambda h, p: ([{"id": 1}], 1)),
    ("turn log", re.compile(r"FROM information_schema\.columns", re.I), lambda h, p: [{"x": 1}]),
    ("notify", re.compile(r"SELECT pg_notify", re.I), lambda h, p: [{"x": None}]),
    # Timers, lists, rooms, music.
    ("timer", re.compile(r"INSERT INTO timers\b", re.I), _did("timer")),
    ("lists", re.compile(r"FROM lists l LEFT JOIN list_items", re.I), _list_rows),
    ("list item", re.compile(r"SELECT COUNT\(\*\) FROM list_items", re.I), lambda h, p: [{"n": 0}]),
    ("list item", re.compile(r"INSERT INTO list_items\b", re.I), _list_item_insert),
    ("list item", re.compile(r"FROM list_items i LEFT JOIN people p .*AND i\.id = :id", re.I), _list_item_read),
    ("list item", re.compile(r"FROM list_items i LEFT JOIN people p", re.I), lambda h, p: []),
    ("list item", re.compile(r"UPDATE lists SET updated_at", re.I), lambda h, p: ([], 1)),
    ("rooms", re.compile(r"FROM \(SELECT room_id FROM rooms UNION", re.I), _room_rows),
    # The library's fingerprint and the recent plays: none (the stub player
    # answers the search, "Stub Artist": music is checked by shape).
    ("music", re.compile(r"FROM library_tracks\)|started_at > NOW\(\) -", re.I), lambda h, p: []),
    # The media libraries' own searches (tracks, podcasts, audiobooks,
    # videos): nothing matches ("play chess with me").
    ("media search", re.compile(
        r"^\s*SELECT\b.*?\bFROM\s+(?:library_tracks|podcast_episodes|podcast_subscriptions|audiobook\w*|video\w*)\b",
        re.I | re.S), lambda h, p: []),
]


class FixtureSession:
    """Stands where route() passes its AsyncSession and where
    ``session_scope()`` opens one: the reads in FIXTURE_READS come from the
    house fixture; any other read or any write is "needs db" (stub), or an
    empty answer / a no-op (--ollama, noted in the result)."""

    async def execute(self, stmt: Any, params: Any = None, *a: Any, **k: Any) -> _Rows:
        sql = " ".join(str(stmt).split())
        house = CURRENT["house"]
        reads = FIXTURE_READS + (ACTION_READS if CURRENT.get("actions") else [])
        for _what, rx, rows in reads:
            if rx.search(sql):
                got = rows(house, params if isinstance(params, dict) else {})
                if isinstance(got, tuple):   # (rows, rowcount): a write
                    return _Rows(*got)
                return _Rows(got)
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

    @contextlib.asynccontextmanager
    async def begin_nested(self) -> Any:
        if not CURRENT.get("actions"):
            TOUCHES.db.append("session.begin_nested")
            raise NeedsDB("session.begin_nested")
        yield self

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
        # No fixture room is in a drop-in: every routed turn asks whether
        # its room is in a call (router, dropin_common.room_in_call).
        self.active_dropins: dict = {}

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
    # (original, replacement): a module first imported INSIDE the sandbox
    # binds the replacement by name and is in no undo list, so the exit
    # sweeps every module for these and points them back (else a later
    # test in the same process gets the fixture session: NeedsDB).
    swaps = [
        (ollama_mod.get_ollama_client, lambda: client),
        (db_session.session_scope, fixture_scope),
        (db_session.SessionLocal, lambda *a, **k: FixtureSession()),
    ]
    for original, replacement in swaps:
        undo += _swap_everywhere(original, replacement)
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
        for original, replacement in swaps:
            _swap_everywhere(replacement, original)
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
    from domovoi.help_desk import admin_tasks
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
    saved_installed_from_kit = admin_tasks.installed_from_kit

    async def fixture_printers(s: Any = None) -> list[dict[str, Any]]:
        return [
            {"id": i, "name": f"Printer {i}", "ipp_address": f"192.168.1.{50 + i}", "rooms": []}
            for i in range(1, int(house.get("printers") or 0) + 1)
        ]

    CURRENT.update(house=house, lenient=lenient, catalogue=catalogue)
    reset_state(house)
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
            # A house set up from an offline kit (1.5.0): the record
            # domovoi.kit_rooms writes at install, as the fixture says. Whether
            # it still updates from a kit is admin_tasks.updates_by_kit's own
            # call, from that and the internet answer overridden above.
            admin_tasks.installed_from_kit = lambda: bool(house.get("offline_kit"))
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
            admin_tasks.installed_from_kit = saved_installed_from_kit
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
    # The case's stored / not_stored checks that failed (check_state).
    state_problems: list[str] = field(default_factory=list)
    # What the turn did in the house (see Case.did).
    actions: list[str] = field(default_factory=list)


async def run_voice_turn(utterance: str, ctx: Any) -> Turn:
    """The real ``router.route()``, with the fixture's session and rooms.
    The model is whatever the sandbox gives (none in stub mode)."""
    from domovoi.models import Intent
    from domovoi.router import route

    resp = await route(Intent(transcript=utterance, room_id=ctx.room_id), ctx, FixtureSession())
    # The next turn is in the same conversation, as on a satellite.
    ctx.session_id = resp.session_id
    return Turn("reply", resp.text or "", resp.matched_handler, resp.matched_path)


def requirement_met(case: "Case") -> bool:
    if case.requires is None:
        return True
    if case.requires == "chat_actions":
        return chat_actions_available()
    raise ValueError(f"{case.id}: unknown requires {case.requires!r}")


def chat_actions_available() -> bool:
    """Whether this checkout has typed chat's house commands (1.5.0
    wf/chat-actions: chat._router_hop_kind, phone_voice._route_phone_turn)."""
    from domovoi import phone_voice
    from web.backend.api import chat as chat_api

    return hasattr(phone_voice, "_route_phone_turn") and hasattr(chat_api, "_router_hop_kind")


@contextlib.contextmanager
def action_turn(house: dict) -> Iterator[Any]:
    """A turn that may act on the house: the fixture's rooms have music
    players and are connected (the live app it yields), ACTION_READS answer,
    the room's music dispatch is recorded, and the turn's own stub players
    leave nothing playing for a later case (2.19 once heard 25.4m's jazz)."""
    from domovoi import room_music
    from domovoi.clients import mpd as mpd_mod

    app = LiveApp(house)
    live = app.state
    live.satellite_full_duplex = {r: True for r in live.active_sessions}
    live.satellite_volume = {}
    live.active_dropins = {}
    # The core's internet probe (phone_voice reads ``state.probe.online``).
    import types

    live.probe = types.SimpleNamespace(online=house.get("internet", "always") != "never")

    async def dispatch(app: Any, response: Any, room_id: str, *, start_paused: bool = False) -> None:
        STATE["actions"].append(f"music:{room_id}:{response.music_action}")

    saved_ports, saved_dispatch = dict(mpd_mod._room_ports), room_music.dispatch_music
    saved_clients = dict(mpd_mod._clients)
    mpd_mod._clients.clear()
    mpd_mod._room_ports.clear()
    mpd_mod._room_ports.update({r["id"]: (6600 + 2 * i, 6601 + 2 * i)
                                for i, r in enumerate(house.get("rooms") or [])})
    room_music.dispatch_music = dispatch
    CURRENT["actions"] = True
    try:
        yield app
    finally:
        CURRENT["actions"] = False
        room_music.dispatch_music = saved_dispatch
        mpd_mod._room_ports.clear()
        mpd_mod._room_ports.update(saved_ports)
        mpd_mod._clients.clear()
        mpd_mod._clients.update(saved_clients)


async def _settle() -> None:
    import asyncio

    for _ in range(10):   # the room's music task and the fan-out run after the reply
        await asyncio.sleep(0)


def _did_from(resp: Any, before: int) -> list[str]:
    did = STATE["actions"][before:]
    if resp is not None and resp.announce_to_rooms:
        did.insert(0, f"announce:{','.join(resp.announce_to_rooms)}:{resp.announce_text or ''}")
    return did


async def run_typed_command(
    utterance: str, house: dict, *, device: str | None, thread: str, tid: int, user_row: int,
) -> Turn | None:
    """chat.send_message's router hop (web/backend/api/chat.py _router_hop)
    and the core's typed turn (POST /v1/phone/text with fast_paths_only and
    plays_here false, phone_voice _answer_text without its caller checks
    and row writes): the reply the house writes, or None (the chat
    answers). The core is up and the thread has nothing attached (the
    fixture has neither)."""
    from domovoi import phone_voice, typed_chat
    from domovoi.router import plan_route
    from web.backend.api import chat as chat_api

    saved_snap, saved_attach = chat_api.get_cached_snapshot, chat_api._thread_reads_attachments

    async def no_attachments(thread_id: int) -> bool:
        return False

    chat_api.get_cached_snapshot = lambda: {"fixture": True}
    chat_api._thread_reads_attachments = no_attachments
    t0 = time.perf_counter()
    try:
        kind = await chat_api._router_hop_kind(tid, utterance, user_row, None)
    finally:
        chat_api.get_cached_snapshot, chat_api._thread_reads_attachments = saved_snap, saved_attach
    if kind is None:
        return None
    STATE["hop"] = {"kind": kind, "t0": t0}
    dev = next((d for d in house.get("devices") or [] if d["id"] == device), {})
    person = None if dev.get("shared_screen") else dev.get("person_id")
    room = phone_voice.phone_room(device or f"chat-{tid}")
    before = len(STATE["actions"])
    said = utterance.strip()
    if not typed_chat.eligible(said):
        STATE["hop"].update(ms=int((time.perf_counter() - t0) * 1000), acted=False)
        return None
    skip = typed_chat.skip_handlers(plan_route(said), said)
    with action_turn(house) as app:
        resp, err = await phone_voice._route_phone_turn(
            app, room=room, thread_id=tid, request=said, player={}, person_id=person,
            trigger="typed", stages={}, fast_paths_only=True, skip_handlers=skip, no_player=True)
        await _settle()
    shaped = phone_voice._typed_reply(resp, False) if resp is not None else ("", [], {})
    deferred = resp is not None and (resp.matched_path == phone_voice.DEFERRED_PATH or shaped is None)
    STATE["hop"]["ms"] = int((time.perf_counter() - t0) * 1000)
    STATE["hop"]["acted"] = not deferred
    if deferred:
        return None   # no fast tier took it (or it was the phone player's): the chat model answers
    if resp is None:
        text = (err or {}).get("detail", "")
        _chat_row(thread, "assistant", {"model_role": "action", "error": True})
        return Turn("reply", text, None, "chat_action", actions=_did_from(None, before))
    reply, _actions, _notes = shaped
    stats = {"model_role": "action", "matched_handler": resp.matched_handler}
    if resp.expect_followup:
        stats["expect_followup"] = True
    _chat_row(thread, "assistant", stats)
    return Turn("reply", reply, resp.matched_handler, "chat_action", actions=_did_from(resp, before))


async def run_chat_mic_turn(utterance: str, house: dict, *, device: str | None, thread: str) -> Turn:
    """The phone chat's mic (domovoi/phone_voice.py _answer) without speech:
    route() as the phone (its PHONE_ROOM_PREFIX room, device_kind phone,
    push to talk), credited to the device's person, one conversation per
    chat thread. The fixture's rooms have music players and are connected;
    what the turn does lands in Turn.actions (announcements from the reply,
    timers and list items from their writes, music from its dispatch)."""
    from domovoi import phone_voice
    from domovoi.models import Intent
    from domovoi.phone_voice import phone_room
    from domovoi.router import route

    dev = next((d for d in house.get("devices") or [] if d["id"] == device), {})
    room = phone_room(device or "phone")
    tid = STATE["thread_ids"].setdefault(thread, len(STATE["thread_ids"]) + 1)
    if hasattr(phone_voice, "_route_phone_turn"):
        before = len(STATE["actions"])
        with action_turn(house) as app:
            resp, err = await phone_voice._route_phone_turn(
                app, room=room, thread_id=tid, request=utterance, player={},
                person_id=dev.get("person_id"), trigger="push_to_talk", stages={})
            await _settle()
        if resp is None:
            return Turn("reply", (err or {}).get("detail", ""), None, "chat_mic", actions=_did_from(None, before))
        return Turn("reply", resp.text or "", resp.matched_handler, resp.matched_path,
                    actions=_did_from(resp, before))
    # Before wf/chat-actions: the mic's Context, built as phone_voice._answer did.
    with action_turn(house) as app:
        ctx = build_context(house).model_copy(update={
            "room_id": room, "device_kind": "phone", "trigger": "push_to_talk", "app": app,
            "person_id": dev.get("person_id"), "voice_outcome": None, "voice_candidates": [],
            "presence_tier": None, "chat_thread_id": tid, "session_id": STATE["mic_sessions"].get(thread),
        })
        before = len(STATE["actions"])
        resp = await route(Intent(transcript=utterance, room_id=room, session_id=ctx.session_id),
                           ctx, FixtureSession())
        await _settle()
    STATE["mic_sessions"][thread] = resp.session_id
    return Turn("reply", resp.text or "", resp.matched_handler, resp.matched_path, actions=_did_from(resp, before))


def _fixture_caller(house: dict, device: str | None) -> Any:
    """The fixture's ``device`` as ``devices.caller_device_row`` would find
    it on a request presenting its device key; None for no such device."""
    from web.backend.api.devices import CallerDevice

    for d in house.get("devices") or []:
        if device is not None and d["id"] == device:
            return CallerDevice(d["id"], d.get("person_id"), bool(d.get("shared_screen")), None, None, "device_key")
    return None


def default_device(house: dict) -> str | None:
    """The phone or browser a typed message comes from when the case names
    none: the kitchen tablet on a shared screen, an unlinked browser for a
    guest, else the asker's own linked phone."""
    asker = house.get("asker", "member")
    devices = house.get("devices") or []
    if asker == "shared_screen":
        return next((d["id"] for d in devices if d.get("shared_screen")), None)
    if asker == "guest":
        return next((d["id"] for d in devices if not d.get("person_id") and not d.get("shared_screen")), None)
    pid = int((house.get("speaker") or {}).get("person_id", 1))
    return next((d["id"] for d in devices if d.get("person_id") == pid), None)


def _chat_row(thread: str, role: str, stats: dict[str, Any] | None = None) -> int:
    STATE["thread_ids"].setdefault(thread, len(STATE["thread_ids"]) + 1)
    rid = STATE["next_row"]
    STATE["next_row"] += 1
    STATE["threads"].setdefault(thread, []).append({"id": rid, "role": role, "stats": stats})
    return rid


async def run_chat_turn(
    utterance: str, house: dict, *, device: str | None, thread: str,
    model_url: str | None, model: str | None, away: bool = False,
) -> Turn:
    """The home-server chat as web/backend/api/chat.py send_message runs a
    typed message: refused with AI off (before anything is stored); the
    memory step (chat_memory.reply, for the device's person); the house's
    own answer (house_facts.chat_reply); a help turn (an admin's job is
    answered by who is asking: a proven admin is this house's asker
    "proven_admin", a live admin sign-in); then the model."""
    from domovoi import ai_features, house_facts
    from domovoi.clients import ollama as ollama_mod
    from domovoi.config import settings
    from domovoi.help_desk import admin_tasks
    from domovoi.help_desk import answer as help_answer
    from web.backend.api import chat_memory

    if ai_features.turned_off():
        return Turn("reply", ai_features.TURNED_OFF_REASON, None, "chat_refused")
    user_row = _chat_row(thread, "user")
    tid = STATE["thread_ids"][thread]
    # The fixture's phones and browsers are 1.5.0 clients: each proves
    # itself with its device key (chat_memory.caller_asker).
    mem = await chat_memory.reply(FixtureSession(), thread_id=tid, content=utterance,
                                  before_id=user_row, request=None, caller=_fixture_caller(house, device))
    if mem is not None:
        _chat_row(thread, "assistant", mem.stats)
        return Turn("reply", mem.text, "chat_memory", "chat")

    async def previous_assistant() -> None:
        return None

    if away:
        # The bridge's own request (away_bridge.build_request): the household
        # token, plus the one-turn admin session when the phone sent a valid
        # admin_grant (this house's asker "proven_admin").
        from domovoi import admin_auth
        from web.backend import away_bridge

        bearer = AWAY_ADMIN_SESSION if house.get("asker") == "proven_admin" else None
        away_req = away_bridge.build_request(device or "away-phone", AWAY_HOUSEHOLD_TOKEN, bearer)

        async def admin_check() -> bool:
            # chat.caller_is_admin validates the Bearer against the database;
            # the grant exchange is Main's test (test_away_bridge). Here: the
            # session reached the request the chat code reads.
            return admin_auth.bearer_token(away_req) == AWAY_ADMIN_SESSION

        async def guest_check() -> bool:
            # The real rule on the bridge's real request: never a guest.
            return await admin_auth.request_is_guest(away_req)
    else:
        async def admin_check() -> bool:
            return house.get("asker") == "proven_admin"

        async def guest_check() -> bool:
            # admin_auth.request_is_guest: no device token, Bearer or cookie.
            return house.get("asker") == "guest"

    fact = await house_facts.chat_reply(
        utterance, get_setting=house_facts.core_setting, scope=fixture_scope,
        previous_assistant=previous_assistant, admin_check=admin_check, guest_check=guest_check,
    )
    if fact is not None:
        _chat_row(thread, "assistant", {"model_role": "house_facts"})
        return Turn("reply", fact, "house_facts", "chat")
    # chat._owner_egg_reply: "I'm the owner" (bare) / "who's in charge?".
    from domovoi import owner_easter_eggs

    egg = await owner_easter_eggs.chat_reply(
        utterance, scope=fixture_scope, key=f"chat:{tid}", guest_check=guest_check)
    if egg is not None:
        _chat_row(thread, "assistant", {"model_role": "owner_easter_egg"})
        return Turn("reply", egg, "owner_easter_egg", "chat")
    if chat_actions_available():
        STATE.setdefault("saying", {})[thread] = utterance
        try:
            routed = await run_typed_command(utterance, house, device=device, thread=thread, tid=tid, user_row=user_row)
        finally:
            STATE["saying"].pop(thread, None)
        hop = STATE.pop("hop", None)
        note = (f"router hop: {hop['kind']}, {hop.get('ms', 0)} ms, "
                f"{'acted' if hop.get('acted') else 'no action'}") if hop else ""
        if routed is not None:
            routed.detail = (routed.detail + "; " if routed.detail else "") + note
            return routed
        STATE["hop_note"] = note
    system = ollama_mod.chat_system_prompt(None)
    if help_answer.looks_like_help(utterance) or admin_tasks.match(utterance) is not None:
        from web.backend.api import help as help_api

        try:
            turn = await help_api.help_turn_for(
                utterance, help_thread=False, admin=await admin_check())
        except Exception:  # noqa: BLE001 - chat._help_turn: the chat answers as before
            turn = None
        if turn is not None:
            system = help_answer.system_prompt(turn, bot=settings.bot_name)
            if not turn.covered:
                return Turn("reply", help_answer.not_covered_answer(turn), "help", "chat_help")
            if turn.task_answer is not None:
                _chat_row(thread, "assistant", {"model_role": "help"})
                return Turn("reply", turn.task_answer, "help", "chat_help")
    if house_facts.wants_facts(utterance):
        # chat.send_message: a privacy-worded question about this house that
        # the closed set didn't take gets house_facts.privacy_summary, no model.
        try:
            facts = await house_facts.read(
                chat=True, get_setting=house_facts.core_setting, scope=fixture_scope)
            text = house_facts.privacy_summary(facts)
        except Exception:  # noqa: BLE001 - chat.py: still an honest answer
            text = f"{house_facts.CANT_CHECK} {house_facts.PRIVACY_POINTER}"
        _chat_row(thread, "assistant", {"model_role": "house_facts"})
        return Turn("reply", text, "house_facts", "chat")
    if model_url is None:
        return Turn("needs_model", detail="the chat model")
    import ollama

    resp = await ollama.AsyncClient(host=model_url).chat(
        model=model or settings.ollama_model,
        # chat._history_for_model: the thread's earlier turns ride along.
        messages=[{"role": "system", "content": system},
                  *STATE.get("chat_log", {}).get(thread, []),
                  {"role": "user", "content": utterance}],
    )
    _chat_row(thread, "assistant", {"model_role": "chat"})
    return Turn("reply", resp["message"]["content"], None, "chat", STATE.pop("hop_note", ""))


async def run_playbox_turn(utterance: str, house: dict) -> Turn:
    """The dashboard's play box (main._admin_route_intent): route() with
    no answer back, in the house's first room, and ``proven_admin`` only
    when the request carries a live admin sign-in (this house's asker is
    "proven_admin"); a device token, a guest or the cookie alone is a
    member."""
    from domovoi.models import Intent
    from domovoi.router import route

    ctx = build_context(house).model_copy(update={
        "answerable": False, "proven_admin": house.get("asker") == "proven_admin",
        "person_id": None, "voice_outcome": None, "voice_candidates": [], "trigger": None,
    })
    resp = await route(Intent(transcript=utterance, room_id=ctx.room_id), ctx, FixtureSession())
    return Turn("reply", resp.text or "", resp.matched_handler, resp.matched_path)


def check_state(case: Case) -> list[str]:
    """The case's ``stored`` / ``not_stored`` memories, after its turn."""
    out = []
    mems = STATE.get("memories") or []

    def found(pid: Any, pat: str) -> bool:
        return any((pid is None or m["person_id"] == pid) and re.search(pat, m["body"], re.I)
                   for m in mems)

    for pid, pat in case.stored:
        if not found(pid, pat):
            out.append(f"no memory /{pat}/ saved for person {pid}")
    for pid, pat in case.not_stored:
        if found(pid, pat):
            out.append(f"memory /{pat}/ of person {pid} is stored")
    return out


async def run_case(
    case: Case, house: dict, catalogue: dict[str, dict], *,
    model_url: str | None = None, model: str | None = None,
) -> Turn:
    if case.skip_reason:
        return Turn("skipped", detail=case.skip_reason)
    if case.where == "away" and not away_available():
        return Turn("skipped", detail="away chat isn't on this checkout (web/backend/away_bridge.py)")
    if not requirement_met(case):
        return Turn("skipped", detail=f"needs {case.requires}, which isn't on this checkout")
    TOUCHES.clear()
    # Only the model is real in an --ollama run: state the fixture doesn't
    # hold is still "needs db" (answering it empty made fake failures:
    # "Playing some jazz by Stub Artist", rooms that don't exist).
    lenient = False

    voice_ctx: list[Any] = []

    async def one(say: str, device: str | None, thread: str) -> Turn:
        if case.where == "voice":
            if not voice_ctx:
                voice_ctx.append(build_context(house))
            return await run_voice_turn(say, voice_ctx[0])
        if case.where == "playbox":
            return await run_playbox_turn(say, house)
        if case.where == "chat_mic":
            STATE["saying"][thread] = say
            try:
                t = await run_chat_mic_turn(say, house, device=device or default_device(house), thread=thread)
            finally:
                STATE["saying"].pop(thread, None)
            # The mic writes what was said in the thread too (its transcript row).
            STATE["chat_log"].setdefault(thread, []).extend(
                [{"role": "user", "content": say}, {"role": "assistant", "content": t.text or ""}])
            return t
        t = await run_chat_turn(say, house, device=device or default_device(house),
                                thread=thread, model_url=model_url, model=model,
                                away=case.where == "away")
        if t.outcome == "reply":
            STATE.setdefault("chat_log", {}).setdefault(thread, []).extend(
                [{"role": "user", "content": say}, {"role": "assistant", "content": t.text or ""}])
        return t

    with applied(house, catalogue, lenient=lenient):
        try:
            said_before, before_texts = [], []
            for b in case.before:
                if "reply" in b:
                    # A turn from before (an old reply in the thread): seeded, not run.
                    th = b.get("thread", case.thread)
                    _chat_row(th, "user")
                    _chat_row(th, "assistant", {"model_role": "chat"})
                    STATE["chat_log"].setdefault(th, []).extend(
                        [{"role": "user", "content": b["say"]}, {"role": "assistant", "content": b["reply"]}])
                    said_before.append(f"{b['say']!r} -> {b['reply']!r} (seeded)")
                    before_texts.append(b["reply"])
                    continue
                t = await one(b["say"], b.get("device", case.device), b.get("thread", case.thread))
                said_before.append(f"{b['say']!r} -> {t.text!r}")
                before_texts.append(t.text)
            turn = await one(case.utterance, case.device, case.thread)
            state_problems = check_state(case) + check_lines(
                case, turn.text, [p["name"] for p in house.get("people") or []], before_texts)
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
    if said_before:
        turn.detail = (turn.detail + "; " if turn.detail else "") + "before: " + " / ".join(said_before)
    turn.state_problems = state_problems
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
    actions: list[str] = field(default_factory=list)
    wall_ms: int = 0
    detail: str = ""
    # --runs N: how many scored runs passed, of how many; every run's own
    # status, reply and problems.
    passes: int = 0
    scored_runs: int = 0
    runs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def rate(self) -> str:
        return f"{self.passes}/{self.scored_runs}" if self.scored_runs else "-"


SCORED = ("pass", "fail", "known_gap", "fixed")
# The case's status over several runs: its worst scored run.
_WORST = ("fail", "known_gap", "fixed", "pass")


def combine(runs: list[Result]) -> Result:
    """One result per case from its runs: the worst scored run's (the
    reply shown is that run's), with the pass rate and every run kept."""
    scored = [r for r in runs if r.status in SCORED]
    if scored:
        worst = min(scored, key=lambda r: _WORST.index(r.status))
    else:
        worst = runs[0]
    out = Result(**{k: v for k, v in asdict(worst).items() if k not in ("passes", "scored_runs", "runs")})
    out.passes = sum(1 for r in scored if r.status in ("pass", "fixed"))
    out.scored_runs = len(scored)
    out.runs = [{"status": r.status, "text": r.text, "problems": r.problems, "detail": r.detail,
                 "path": r.path, "handler": r.handler, "wall_ms": r.wall_ms} for r in runs]
    return out


def score(case: Case, turn: Turn, house: dict, tree: SettingsTree, catalogue: dict) -> Result:
    r = Result(
        id=case.id, group=case.group, utterance=case.utterance, house=case.house,
        where=case.where, expect=case.expect, outcome=turn.outcome, status=turn.outcome,
        text=turn.text, handler=turn.handler, path=turn.path, needs_change=case.needs_change,
        detail=turn.detail, actions=list(turn.actions),
    )
    if turn.outcome not in ("reply", "raised"):
        return r
    problems = check_verdict(case, turn.text, turn.handler)
    if turn.outcome == "raised":
        problems.insert(0, f"handler raised ({turn.detail}); the turn is silent")
    problems += turn.state_problems
    for pat in case.did:
        if not any(re.search(pat, a, re.I) for a in turn.actions):
            problems.append(f"didn't do /{pat}/ (did: {turn.actions or 'nothing'})")
    for pat in case.did_not:
        hit = next((a for a in turn.actions if re.search(pat, a, re.I)), None)
        if hit is not None:
            problems.append(f"did {hit!r} (/{pat}/)")
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


def group_rates(results: list[Result]) -> dict[str, str]:
    """Each group's passed scored runs, of all its scored runs ("all": every group)."""
    out = {}
    for g in sorted({r.group for r in results}):
        rows = [r for r in results if r.group == g]
        out[str(g)] = f"{sum(r.passes for r in rows)}/{sum(r.scored_runs for r in rows)}"
    out["all"] = f"{sum(r.passes for r in results)}/{sum(r.scored_runs for r in results)}"
    return out


def report(results: list[Result], verbose: bool = False) -> int:
    groups = sorted({r.group for r in results})
    rates = group_rates(results)
    head = (f"{'group':>5} {'cases':>5} " + " ".join(f"{c.replace('needs_', 'n/'):>9}" for c in STATUS_COLUMNS)
            + f" {'run rate':>9}")
    print(head)
    for g in groups:
        rows = [r for r in results if r.group == g]
        print(f"{g:>5} {len(rows):>5} " + " ".join(
            f"{sum(1 for r in rows if r.status == c):>9}" for c in STATUS_COLUMNS) + f" {rates[str(g)]:>9}")
    print(f"{'all':>5} {len(results):>5} " + " ".join(
        f"{sum(1 for r in results if r.status == c):>9}" for c in STATUS_COLUMNS) + f" {rates['all']:>9}")
    print()

    def show(r: Result) -> None:
        rate = f" (passed {r.rate} runs)" if r.scored_runs > 1 else ""
        print(f"  {r.id:6} [{r.house}] {r.utterance!r}{rate}")
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
    judge_model: str | None = None, runs: int = 1,
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
    per_case: dict[str, list[Result]] = {c.id: [] for c in cases}
    with sandbox(client):
        for _ in range(max(1, runs)):
            for case in cases:
                house = houses[case.house]
                t0 = time.perf_counter()
                turn = await run_case(case, house, catalogue, model_url=model_url, model=model)
                r = score(case, turn, house, tree, catalogue)
                r.wall_ms = int((time.perf_counter() - t0) * 1000)
                per_case[case.id].append(r)
    if judge_model and model_url:
        for rs in per_case.values():
            for r in rs:
                if by_id[r.id].judge and r.status in SCORED and r.text:
                    await judge_result(r, by_id[r.id], model_url, judge_model)
    return [combine(per_case[c.id]) for c in cases]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ollama", metavar="URL", help="Ollama server for real-model runs")
    p.add_argument("--model", help="Q&A / chat model (default: settings.ollama_model)")
    p.add_argument("--tool-model", help="tool-routing model (default: settings.ollama_tool_model)")
    p.add_argument("--judge-model", help="score judge:true cases 0-3 with this model (needs --ollama)")
    p.add_argument("--runs", type=int, default=1, metavar="N",
                   help="run every case N times and report pass rates (for model runs)")
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
        tool_model=args.tool_model, judge_model=args.judge_model, runs=args.runs,
    ))
    code = report(results, verbose=args.verbose)
    print(f"({time.monotonic() - t0:.1f}s)")
    if args.json:
        args.json.write_text(json.dumps({
            "target": target, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "runs": args.runs,
            "group_rates": group_rates(results),
            "results": [{**asdict(r), "rate": r.rate} for r in results],
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
