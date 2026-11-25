"""House facts: privacy and "can you …?" answers read from this house's
real state, never guessed (1.4.1, N2 with N7's capability answers).

Before this, "are you recording me?", "how long do you keep what I say?",
"does anything leave the house?" and "can you order pizza?" went to the
general answer model, whose prompt holds none of those facts: it guessed,
and a guess about privacy is the worst kind of wrong answer.

Four pieces, one module, so the facts and their wording live in ONE place:

1. :func:`read` — the facts for one turn, each read from the state the
   feature itself uses (the command-recordings opt-in with the same SQL as
   ``command_captures``, the settings the workers read, the internet answer
   through :mod:`domovoi.egress`, the AI switch through
   :mod:`domovoi.ai_features`, the Friend Houses and calendar rows). A
   fact that can't be read is ``None`` — "unknown" — and every answer that
   needs it says "I can't check that right now" instead of guessing.
   Reading never fails a turn.
2. The closed set of privacy questions (:func:`question_id`) and their
   answers (:func:`answer`): exact templates filled with the facts, no
   model. ``domovoi/handlers/house_facts.py`` speaks them on the fast path;
   ``web/backend/api/chat.py`` asks :func:`chat_reply` first.
3. The capability table (:data:`CAPABILITIES`): "can you X?" / "are you
   able to X?" / "do you X?" for a closed list of X — yes with a real
   phrase (the handler's own ``example_phrases``), a plain no for a known
   gap, "not on this house" plus how to get it for a feature a plugin would
   add. An X not in the table gets nothing here: the question goes on to
   the Q&A model exactly as before, so its measured hedge stays the safety
   net and an unknown X never hears "No". The router keeps a "can you"
   question whole only for an X in this table (:func:`kept_question`).
4. The fallback: a privacy-worded question outside the closed set gets a
   fixed answer built from the same facts (:func:`privacy_summary`), never
   a model's: measured 2026-11-25, the model given the facts still made up
   wrong privacy claims.

Replies never name a plugin or an outside service: they describe the
feature. Every "how to get it" clause goes through
:func:`_how_to_turn_on`, the one place its wording lives.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import text

log = logging.getLogger(__name__)

# ─── Places in the dashboard (exact names, web/static/settings.jsx) ───────

PLACE_INTERNET = "Settings → Privacy → Internet"
PLACE_ROOMS = "Settings → Household → Rooms"
# "Keep voice history" is behind the Voice history button on the People
# page (people.jsx), not a settings section of its own: Settings → Privacy
# → Voice history is the command recordings. Said as the real path, then
# the button, so every path a reply names is one the registry knows.
PLACE_VOICE_HISTORY = "Settings → Household → People, then Voice history"
PLACE_MUSIC_MIX = "Settings → Household → Music mix"
PLACE_FRIEND_HOUSES = "Settings → Household → Friend Houses"
PLACE_PRINTERS = "Settings → Household → Printers & scanners"
PLACE_MODELS = "Settings → Chat & AI → Models"
PLACE_ADVANCED = "Settings → System → Advanced"
PLACE_PLUGINS = "the Plugins page"
# The offline library's own page (its plugin's nav label), where an admin
# downloads a library.
PLACE_LIBRARY = "the Library page"

# The last sentence of the privacy fallback (privacy_summary).
PRIVACY_POINTER = "Settings → Privacy shows exactly what's kept and what goes online."
CANT_CHECK = "I can't check that right now."

# "Did you use the internet for that?" (D3, owner 2026-11-25).
D3_VOICE = "I can't tell from a voice turn yet. Ask in chat and I can say exactly."
D3_CHAT_YES = "Yes — that answer used a web lookup."
D3_CHAT_NO = "No — that answer came from this house, nothing went online."
D3_CHAT_NONE = "There's no answer of mine in this chat yet, so nothing went online."
D3_CHAT_SPOKEN = "That answer was spoken by a room, and I can't tell from here whether it went online."

# A phone or tablet in room mode names one of these boards in its hello.
ROOM_PHONE_BOARDS = frozenset({"android_room", "ios_room"})

# Devices a turn can come from.
SATELLITE = "satellite"
ROOM_PHONE = "room_phone"
PHONE_CHAT = "phone_chat"   # the app's chat mic
WEB_CHAT = "web_chat"       # a typed chat on the home server (dashboard or app)
OTHER = "other"             # /v1/intent with no room, the play box

ROOM_DEVICES = frozenset({SATELLITE, ROOM_PHONE})
CHAT_DEVICES = frozenset({PHONE_CHAT, WEB_CHAT})

# The settings the facts read. The core reads its own live ``settings``;
# the web process (whose copy goes stale on a save) reads these from the
# core's snapshot (``settings_snapshot``), and "unknown" when it has none.
SETTING_NAMES: tuple[str, ...] = (
    "voice_history_keep",
    "command_capture_retention_days",
    "lookup_always_allow",
    "news_enabled",
    "podcast_feed_poller_enabled",
    "music_alias_fetch_enabled",
    "library_enricher_enabled",
    "lyrics_lrclib_enabled",
    "music_similar_artists_enabled",
    "music_popularity_enabled",
    "library_cover_art_enabled",
    "friend_houses_enabled",
    "ollama_vision_model",
)

# What goes online on its own, by feature (never a service's name), in the
# order it is said. Each is the setting its worker reads.
BACKGROUND_FEATURES: tuple[tuple[str, str], ...] = (
    ("news_enabled", "the news headlines"),
    ("podcast_feed_poller_enabled", "new podcast episodes"),
    ("music_alias_fetch_enabled", "other names of your music's artists"),
    ("library_enricher_enabled", "recognising songs in your library"),
    ("lyrics_lrclib_enabled", "lyrics for songs that have none"),
    ("music_similar_artists_enabled", "music suggestions for Up next"),
    ("music_popularity_enabled", "music suggestions for Up next"),
    ("library_cover_art_enabled", "album covers"),
)

_KEEP_WORDS = {"30": "30 days", "90": "90 days", "365": "a year"}

SettingGetter = Callable[[str], Any]


def core_setting(name: str) -> Any:
    """A setting as the core reads it now (its ``settings`` is live)."""
    from domovoi.config import settings

    return getattr(settings, name)


def settings_snapshot() -> dict[str, Any]:
    """The values :func:`read` needs, for the core's admin snapshot: the
    web process reads them from there (``snapshot_setting``)."""
    out: dict[str, Any] = {}
    for name in SETTING_NAMES:
        try:
            value = core_setting(name)
        except Exception:  # noqa: BLE001 — left out: the web says "unknown"
            continue
        out[name] = value if isinstance(value, (bool, int, str)) else str(value)
    return out


def snapshot_setting(snapshot: dict[str, Any] | None) -> SettingGetter:
    """A getter over the core's snapshot (``snapshot["house_facts"]``); a
    value it doesn't hold raises, so the fact reads as unknown."""
    values = (snapshot or {}).get("house_facts")

    def get(name: str) -> Any:
        if not isinstance(values, dict) or name not in values:
            raise KeyError(name)
        return values[name]

    return get


# ─── The facts ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class HouseFacts:
    """One turn's facts. ``None`` anywhere means it couldn't be read."""

    device: str = OTHER
    room_id: str | None = None
    # This room keeps command recordings (opted in AND an admin exists).
    recordings: bool | None = None
    recording_days: int | None = None
    # "30" | "90" | "365" | "forever"
    voice_keep: str | None = None
    # "always" | "sometimes" | "never" | "" (not answered)
    internet: str | None = None
    ai_on: bool | None = None
    # Chat lookups ask before they leave the house (not "Always allow").
    lookups_ask_first: bool | None = None
    friend_houses: bool | None = None
    friends: int | None = None
    # Online music suggestions (similar artists or song popularity) on.
    music_suggestions: bool | None = None
    calendar_links: int | None = None
    # What goes online on its own (spoken features); None when one of the
    # switches couldn't be read.
    background: tuple[str, ...] | None = None
    vision_model: bool | None = None
    news_on: bool | None = None


def _bool(get: SettingGetter, name: str) -> bool | None:
    try:
        return bool(get(name))
    except Exception:  # noqa: BLE001
        return None


def _keep(get: SettingGetter) -> str | None:
    try:
        from domovoi.config import normalize_voice_history_keep

        return normalize_voice_history_keep(get("voice_history_keep"))
    except Exception:  # noqa: BLE001
        return None


def _recording_days(get: SettingGetter) -> int | None:
    from domovoi.command_captures import MAX_RETENTION_DAYS

    try:
        days = int(get("command_capture_retention_days"))
    except (TypeError, ValueError):
        days = MAX_RETENTION_DAYS
    except Exception:  # noqa: BLE001
        return None
    return max(1, min(MAX_RETENTION_DAYS, days))


def _internet() -> str | None:
    try:
        from domovoi import egress

        return egress.policy()
    except Exception:  # noqa: BLE001
        return None


def _ai_on() -> bool | None:
    try:
        from domovoi import ai_features

        return bool(ai_features.enabled())
    except Exception:  # noqa: BLE001
        return None


def _default_scope() -> Any:
    from domovoi.db.session import session_scope

    return session_scope


async def _scalar(scope: Any, sql: str, params: dict[str, Any] | None = None,
                  *, missing_table: Any = ...) -> Any:
    """One value, in its own session (a failed read never spoils the
    turn's). Raises on failure, except a missing table when
    ``missing_table`` is given: the feature's migration isn't applied, so
    there is nothing (the same rule the feature's own code uses)."""
    try:
        async with scope() as s:
            return (await s.execute(text(sql), params or {})).scalar()
    except Exception as e:
        if missing_table is not ... and "does not exist" in str(e).lower():
            return missing_table
        raise


async def _device(scope: Any, *, room_id: str | None, device_kind: str | None,
                  chat: bool, app: Any) -> str:
    from domovoi.models import is_phone_room

    if chat:
        return WEB_CHAT
    if device_kind == "phone" or is_phone_room(room_id):
        return PHONE_CHAT
    if not room_id:
        return OTHER
    board = None
    try:
        sess = (getattr(getattr(app, "state", None), "active_sessions", None) or {}).get(room_id)
        board = getattr(sess, "board", None)
    except Exception:  # noqa: BLE001
        board = None
    if board is None:
        try:
            board = await _scalar(scope, "SELECT board FROM satellites WHERE room_id = :r",
                                  {"r": room_id}, missing_table=None)
        except Exception as e:  # noqa: BLE001 — a room it is; only the phone's clause is lost
            log.debug("house facts: board unreadable for %s: %s", room_id, e)
    return ROOM_PHONE if board in ROOM_PHONE_BOARDS else SATELLITE


async def read(
    *,
    room_id: str | None = None,
    device_kind: str | None = None,
    chat: bool = False,
    app: Any = None,
    get_setting: SettingGetter | None = None,
    scope: Any = None,
) -> HouseFacts:
    """This turn's facts. ``get_setting`` reads a setting (the core's live
    ``settings`` by default; the web passes :func:`snapshot_setting`),
    ``scope`` opens a database session (the core's ``session_scope`` by
    default). Never raises: what can't be read is None."""
    get = get_setting or core_setting
    scope = scope or _default_scope()
    device = await _device(scope, room_id=room_id, device_kind=device_kind, chat=chat, app=app)

    recordings: bool | None = False
    if device in ROOM_DEVICES:
        # The same two conditions command_captures.room_opted_in checks
        # (opted in AND an admin credential exists); unlike it, a failed
        # read is "unknown" here, not "no".
        try:
            recordings = bool(await _scalar(
                scope,
                "SELECT 1 FROM command_capture_rooms WHERE room_id = :r "
                "AND EXISTS (SELECT 1 FROM admin_auth WHERE id = 1)",
                {"r": room_id}, missing_table=None,
            ))
        except Exception as e:  # noqa: BLE001
            log.debug("house facts: recordings unreadable for %s: %s", room_id, e)
            recordings = None

    internet = _internet()
    friend_houses = _bool(get, "friend_houses_enabled")
    friends: int | None = None
    if friend_houses:
        try:
            friends = int(await _scalar(
                scope, "SELECT count(*) FROM friend_houses_friends WHERE state = 'active'",
                missing_table=0) or 0)
        except Exception as e:  # noqa: BLE001
            log.debug("house facts: friends unreadable: %s", e)
    elif friend_houses is False:
        friends = 0
    try:
        calendar_links: int | None = int(await _scalar(
            scope, "SELECT count(*) FROM calendar_sources WHERE kind = 'link'",
            missing_table=0) or 0)
    except Exception as e:  # noqa: BLE001
        log.debug("house facts: calendars unreadable: %s", e)
        calendar_links = None

    similar = _bool(get, "music_similar_artists_enabled")
    popularity = _bool(get, "music_popularity_enabled")
    music = None if similar is None or popularity is None else (similar or popularity)

    background: tuple[str, ...] | None
    if internet == "never":
        background = ()
    else:
        said: list[str] = []
        known = True
        for name, words in BACKGROUND_FEATURES:
            on = _bool(get, name)
            if on is None:
                known = False
            elif on and words not in said:
                said.append(words)
        if calendar_links is None:
            known = False
        elif calendar_links:
            said.append("the calendars added by link")
        if friend_houses is None or (friend_houses and friends is None):
            known = False
        elif friend_houses and friends:
            said.append("messages to and from your friends' houses")
        background = tuple(said) if known else None

    try:
        vision: bool | None = bool(str(get("ollama_vision_model") or "").strip())
    except Exception:  # noqa: BLE001
        vision = None
    always_allow = _bool(get, "lookup_always_allow")
    return HouseFacts(
        device=device,
        room_id=room_id,
        recordings=recordings,
        recording_days=_recording_days(get),
        voice_keep=_keep(get),
        internet=internet,
        ai_on=_ai_on(),
        lookups_ask_first=None if always_allow is None else not always_allow,
        friend_houses=friend_houses,
        friends=friends,
        music_suggestions=(False if internet == "never" else music),
        calendar_links=calendar_links,
        background=background,
        vision_model=vision,
        news_on=_bool(get, "news_enabled"),
    )


# ─── The one "how to get it" clause ───────────────────────────────────────


def _how_to_turn_on(feature: str, *, place: str = PLACE_PLUGINS, add: bool = False) -> str:
    """The sentence that says how ``feature`` is turned on or added: the
    member wording (owner Q2). The ONE place this wording lives; wf/cap-off's
    shared off-line helper replaces its body when the two are folded.
    ``feature`` names the feature, never a plugin."""
    verb = "add" if add else "turn on"
    where = f"from {place}" if place == PLACE_PLUGINS else f"in {place}"
    return f"An admin can {verb} {feature} {where}."


def _admin_can_change(what: str, place: str) -> str:
    return f"An admin can change {what} in {place}."


# ─── The closed set of privacy questions ──────────────────────────────────

_RIGHT_NOW = r"(?: right now| now| all the time| at the moment)?"
_ME = r"(?: me| us| everything| what i say| what we say)?"
_HOUSE = r"(?:the|this|my|our) house"
_FRIEND = r"(?:my |a |our )?friends?(?:'s|s'|s)? houses?"

QUESTIONS: tuple[tuple[str, str], ...] = (
    ("internet_for_that",
     r"did you (?:use|go on|go to|check|need) the (?:internet|web)(?: for (?:that|this|it))?"
     r"|did (?:that|this)(?: answer)? (?:use|come from|need) the (?:internet|web)"
     r"|was (?:that|this)(?: answer)? from the (?:internet|web)"
     r"|did you (?:go online|look (?:that|this|it) up online) for (?:that|this|it)"
     r"|did you go online(?: for (?:that|this|it))?"),
    ("listening",
     rf"are you (?:always |still |constantly )?listening(?: to{_ME})?{_RIGHT_NOW}"
     rf"|do you (?:always |constantly )?listen(?: to{_ME})?{_RIGHT_NOW}"
     rf"|is (?:domovoi|this(?: thing| device| speaker)?) (?:always )?listening(?: to{_ME})?{_RIGHT_NOW}"),
    ("recording",
     rf"are you recording{_ME}{_RIGHT_NOW}"
     rf"|do you record(?: me| us| everything| what i say| my voice| conversations| this){_RIGHT_NOW}"
     rf"|is (?:domovoi|this(?: room| device| speaker)?) recording{_ME}{_RIGHT_NOW}"),
    ("voice_goes",
     r"where does (?:my voice|what i say) go"
     r"|where do (?:my words|my recordings|my voice recordings) go"
     r"|do you send (?:my voice|what i say|my recordings) (?:anywhere|online|to the cloud|to the internet|outside(?: the house)?)"
     r"|(?:does|is) my voice (?:sent |go |going |leave |leaving )?"
     rf"(?:anywhere|online|to the cloud|to the internet|outside {_HOUSE}|{_HOUSE})"),
    ("leaves_house",
     rf"does (?:anything|any data|my data|anything i say) (?:ever )?(?:leave|go outside) {_HOUSE}"
     rf"|what (?:leaves|goes outside) {_HOUSE}"
     r"|do you send (?:anything|my data|data) (?:anywhere|online|to the cloud|to the internet|outside(?: the house)?)"
     r"|what do you send (?:online|to the internet|to the cloud)"),
    ("keep",
     r"do you (?:keep|save|store) (?:what i say|what i said|my voice|recordings|everything(?: i say)?"
     r"|what we say|my conversations|what i tell you)"
     r"|how long do you keep (?:it|that|what i say|what i said|my voice|recordings|things|my data"
     r"|voice history|my voice history|everything)"
     r"|how long is (?:my voice history|voice history|what i say) kept"),
    ("internet",
     r"do you (?:ever )?use the internet|do you (?:ever )?go online|do you need (?:the )?internet"
     r"|does (?:this house|domovoi) use the internet"),
    ("chats",
     r"who (?:can )?(?:see|sees|read|reads) my (?:messages|chats?|chat history|conversations)"
     r"|can (?:other people|anyone else|anyone|others|everyone|people) (?:see|read|hear) my "
     r"(?:messages|chats?|chat history|conversations)"
     r"|are my (?:chats|messages|conversations) private"),
    ("friend_houses",
     rf"can {_FRIEND} (?:hear|see|listen to|read) (?:me|us|my stuff|my things|what i say|anything|my messages)"
     rf"|what can {_FRIEND} see"
     rf"|what does {_FRIEND} see"),
    ("private",
     r"is (?:this|our) (?:conversation|chat|talk) private|is this private|are we (?:talking )?in private"),
    ("listening_history",
     r"does anyone (?:else )?see my listening history"
     r"|who (?:can )?sees? my listening history"
     r"|do you share my listening(?: history)?"
     r"|who knows what i listen to"
     r"|is my listening history (?:shared|private)"),
)

QUESTION_RE = re.compile(
    r"^(?:" + "|".join(f"(?P<{qid}>{rx})" for qid, rx in QUESTIONS) + r")$"
)


_FILLER_RE = re.compile(
    r"^(?:(?:hey |ok |okay )?(?:domovoi|domo)[,\s]+)?"
    r"(?:(?:hey|um|uh|so|well|alright|ok|okay|please)[,\s]+)*"
)


def normalize(raw: str) -> str:
    """A typed message as the router's fast paths see a spoken one: lower
    case, one space, curly quotes straight, no closing punctuation, no
    leading filler (but "can you" stays: it is the question)."""
    t = " ".join((raw or "").replace("’", "'").replace("‘", "'").lower().split())
    t = t.strip().rstrip(".,!?").strip()
    return _FILLER_RE.sub("", t).strip()


def question_id(transcript: str) -> str | None:
    """The closed-set question ``transcript`` (normalized) asks, or None."""
    m = QUESTION_RE.match(transcript or "")
    return m.lastgroup if m else None


def _keep_text(keep: str | None) -> str | None:
    if keep is None:
        return None
    if keep == "forever":
        return "until someone deletes it"
    return f"for {_KEEP_WORDS.get(keep, keep + ' days')}"


def _recording_clause(f: HouseFacts) -> str:
    if f.recordings is None:
        return "I can't check this room's recordings right now."
    if f.recordings:
        return f"This room also keeps recordings of the commands said here for {f.recording_days or 14} days."
    return "No recordings of your voice are kept."


def _lookup_clause(f: HouseFacts) -> str:
    if f.internet in ("always", "sometimes", ""):
        return " When you ask me to look something up, only the search words go out."
    return ""


def _chat_shared() -> str:
    return ("Everyone in this house: chats on this house's server can be read on every paired "
            "phone and browser.")


def answer(qid: str, f: HouseFacts) -> str:
    """The exact answer to closed-set question ``qid`` for facts ``f``."""
    fn = _ANSWERS.get(qid)
    if fn is None:
        return CANT_CHECK
    return fn(f)


def _listening(f: HouseFacts) -> str:
    room = ("I only listen for my wake word, and that's checked right here on this device. "
            "Nothing is sent until I hear it; then what you say goes to this house's own server.")
    if f.device == SATELLITE:
        return room
    if f.device == ROOM_PHONE:
        return room + " While this phone listens, it shows a notification and the microphone dot."
    if f.device == PHONE_CHAT:
        return ("Only while you use the app's mic button. Then what you say goes to this "
                "house's own server.")
    if f.device == WEB_CHAT:
        return ("Not here: this is a typed chat, with no microphone. The rooms listen only for "
                "the wake word, on each device, and send nothing until they hear it.")
    return ("The rooms listen only for the wake word, on each device, and send nothing until "
            "they hear it; then what's said goes to this house's own server.")


def _recording(f: HouseFacts) -> str:
    keep = _keep_text(f.voice_keep)
    if f.device in ROOM_DEVICES:
        if f.recordings is None:
            return CANT_CHECK
        if f.recordings:
            return (f"Yes. This room keeps recordings of the commands said here for "
                    f"{f.recording_days or 14} days, to tune when I stop listening. "
                    + _admin_can_change("that", PLACE_ROOMS).replace("change that", "turn that off"))
        out = "No. This room keeps no recordings of your voice."
        if keep:
            out += f" I do keep the text of what's said {keep}."
        return out
    if f.device == PHONE_CHAT:
        return ("No. The app's mic keeps no recordings of your voice. What you say is kept as "
                "text in this chat until someone deletes it.")
    if f.device == WEB_CHAT:
        return ("No. This is a typed chat, so there's nothing to record. The chat itself is kept "
                "until someone deletes it.")
    return CANT_CHECK


def _voice_goes(f: HouseFacts) -> str:
    if f.device == WEB_CHAT:
        return ("There's no voice in a typed chat. In the rooms, your voice goes only to this "
                "house's own server and never leaves the house." + _lookup_clause(f))
    return ("Only to this house's own server. Your voice never leaves the house."
            + _lookup_clause(f))


def _leaves_house(f: HouseFacts) -> str:
    if f.internet is None:
        return CANT_CHECK
    if f.internet == "never":
        return "Nothing. This house is set to never use the internet, so everything stays home."
    out = ("Your voice never leaves the house. When you ask for something that needs the "
           "internet, like a web lookup or the weather, only what that needs goes out.")
    if f.background is None:
        return out + f" I can't check everything else right now; {PLACE_INTERNET} lists it."
    if f.background:
        items = list(f.background)
        listed = items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
        when = ", when the connection is up" if f.internet == "sometimes" else ""
        out += f" On its own, this house also goes online for {listed}{when}."
    else:
        out += " Nothing else goes online on its own."
    return out


def _keep_answer(f: HouseFacts) -> str:
    if f.device == WEB_CHAT:
        return ("Yes. This chat is kept until someone deletes it, and everyone in the house can "
                "read it.")
    keep = _keep_text(f.voice_keep)
    if keep is None:
        return CANT_CHECK
    out = f"Yes. I keep the text of what you say, and my answers, {keep}"
    out += ", then delete it." if f.voice_keep != "forever" else "."
    if f.device in ROOM_DEVICES:
        out += " " + _recording_clause(f)
    elif f.device == PHONE_CHAT:
        out += " It's also kept in this chat until someone deletes it."
    return out + " " + _admin_can_change("how long", PLACE_VOICE_HISTORY)


def _internet_answer(f: HouseFacts) -> str:
    change = " " + _admin_can_change("that", PLACE_INTERNET)
    if f.internet is None:
        return CANT_CHECK
    if f.internet == "always":
        return "Yes. This house is set to use the internet when something needs it." + change
    if f.internet == "sometimes":
        return ("Sometimes. This house uses the internet for small things when the connection "
                "is up, and skips the big downloads." + change)
    if f.internet == "never":
        return "No. This house is set to never use the internet, so everything stays home." + change
    return ("Yes, when it can. Nobody has answered the internet question yet, so I use the "
            "internet when it's there. " + _how_to_answer_internet())


def _how_to_answer_internet() -> str:
    return f"An admin can answer it in {PLACE_INTERNET}."


def _friend_clause(f: HouseFacts) -> str:
    if f.friend_houses is None or (f.friend_houses and f.friends is None):
        return " I can't check the friends' houses right now."
    if f.friend_houses and f.friends:
        return (" A message you send to a friend's house is seen there too, and only that "
                "message.")
    return ""


def _chats(f: HouseFacts) -> str:
    return _chat_shared() + _friend_clause(f)


def _friend_houses(f: HouseFacts) -> str:
    if f.friend_houses is None:
        return CANT_CHECK
    if not f.friend_houses:
        return "No. Friend Houses is off, so no other house can reach this one."
    return ("No. A friend's house only sees that this house is running and reachable, never "
            "who's home, and only the messages and lists you send or share with it.")


def _private(f: HouseFacts) -> str:
    if f.device in CHAT_DEVICES:
        return ("This chat is shared with everyone in the house: every paired phone and browser "
                "can read it.")
    keep = _keep_text(f.voice_keep)
    out = "Not entirely: anyone in the room can hear us."
    if keep:
        out += f" I keep the text of what's said {keep}."
    if f.device in ROOM_DEVICES:
        out += " " + _recording_clause(f)
    return out


def _listening_history(f: HouseFacts) -> str:
    if f.music_suggestions is None:
        return CANT_CHECK
    out = "No. It stays in this house; only people here can see it, on the dashboard."
    if f.music_suggestions:
        out += (" For music suggestions I ask an online music catalogue about the artists and "
                "songs in your library, by catalogue number only, never what anyone played. "
                + _admin_can_change("that", PLACE_MUSIC_MIX).replace("change that", "turn that off"))
    return out


def _internet_for_that(f: HouseFacts) -> str:
    # A spoken turn: the previous turn's flag isn't kept yet (D3; chat
    # answers it exactly, web/backend/api/chat.py).
    return D3_VOICE


_ANSWERS: dict[str, Callable[[HouseFacts], str]] = {
    "internet_for_that": _internet_for_that,
    "listening": _listening,
    "recording": _recording,
    "voice_goes": _voice_goes,
    "leaves_house": _leaves_house,
    "keep": _keep_answer,
    "internet": _internet_answer,
    "chats": _chats,
    "friend_houses": _friend_houses,
    "private": _private,
    "listening_history": _listening_history,
}


def chat_internet_answer(previous: dict[str, Any] | None) -> str:
    """D3 in chat: from the previous assistant message (its ``stats``) —
    ``stats.lookup.internet_used`` is what the lookup recorded; a spoken
    turn's row can't say; any other reply was written in this house."""
    if previous is None:
        return D3_CHAT_NONE
    stats = previous.get("stats") or {}
    if not isinstance(stats, dict):
        stats = {}
    lookup = stats.get("lookup")
    if isinstance(lookup, dict):
        return D3_CHAT_YES if lookup.get("internet_used") else D3_CHAT_NO
    if stats.get("model_role") == "voice":
        return D3_CHAT_SPOKEN
    return D3_CHAT_NO


# ─── "Can you X?" — the capability table ──────────────────────────────────

CAP_PREFIX = (
    r"(?:can you|are you able to|do you know how to|is it possible (?:for you )?to|can domovoi|do you)"
)


@dataclass(frozen=True)
class Capability:
    """One row: ``pattern`` is X (the words after "can you"), whole."""

    key: str
    pattern: str
    kind: str                      # core | chat | no | plugin | internet | news | print
    handler: str | None = None     # core: the handler whose phrase is said
    phrase: str | None = None      # core: which of its example phrases
    text: str | None = None        # no: the reply; chat / plugin: the feature
    slugs: tuple[str, ...] = ()    # plugin: the plugin slugs that bring it
    provides: str | None = None    # plugin: or the capability one declares
    needs_internet: bool = False
    needs_ai: bool = False


CAPABILITIES: tuple[Capability, ...] = (
    # Things Domovoi does: "Yes. Say '<the handler's own phrase>'."
    Capability("timers", r"(?:set|do|use|make) timers|time things", "core", "timer",
               "set a timer for 10 minutes"),
    Capability("reminders", r"(?:set|do|give|make) reminders|remind (?:me|us|people) (?:of|about) things",
               "core", "reminder", "remind me to call Mom in 20 minutes"),
    Capability("lists", r"(?:make|keep|do) (?:shopping |to-?do )?lists|keep a (?:shopping|to-?do) list",
               "core", "lists", "add milk to the shopping list"),
    Capability("rooms_music",
               r"play music in (?:other|different|all the|every|another) rooms?"
               r"|play music (?:all over|around) the house",
               "core", "music", "play some music in the garage"),
    Capability("drop_in", r"drop in on (?:another|other|a different|the other) rooms?", "core",
               "dropin", "drop in on the kitchen"),
    Capability("announce",
               r"make (?:an )?announcements?(?: to (?:the whole house|everyone|every room))?"
               r"|announce things|broadcast (?:messages|to the (?:whole )?house)",
               "core", "intercom", "tell everyone dinner's ready"),
    Capability("print", r"print(?: things| stuff| something| documents| pages)?", "print",
               "printing", "print my cover letter"),
    Capability("documents", r"write (?:documents|letters|a letter)|make documents|take letters",
               "core", "documents", "take a letter to the landlord"),
    Capability("audiobooks", r"(?:read|play) (?:me )?(?:an |my )?audiobooks?", "core",
               "spoken_audio", "resume my audiobook"),
    Capability("podcasts", r"play (?:a |my )?podcasts?", "core", "spoken_audio",
               "play the latest episode of The Daily"),
    Capability("maths", r"do (?:math|maths|sums|calculations|arithmetic)", "core", "calculator",
               "what's 15 percent of 80"),
    Capability("notes", r"take (?:voice )?notes|take a (?:voice )?note|make (?:voice )?notes",
               "core", "voice_notes", "jot down buy milk and eggs"),
    Capability("read_calendar", r"(?:read|check) (?:my |the )?calendars?", "core", "calendar",
               "what's on my calendar today"),
    Capability("news", r"(?:check|read|get) the news|tell me the news|do (?:the )?news", "news",
               "news", "what's the news"),
    Capability("web", r"search the (?:web|internet)|look (?:things|stuff) up(?: online| on the (?:web|internet))?"
               r"|browse the (?:web|internet)", "internet"),
    # Chat-only abilities (a picture or video attached in chat).
    Capability("pictures", r"look at (?:a |my |this )?(?:pictures?|photos?|images?)|read (?:pictures|photos)",
               "chat", text="picture", needs_ai=True),
    Capability("videos", r"watch (?:a |my |this )?videos?", "chat", text="video", needs_ai=True),
    # Known gaps: a plain no.
    Capability("calendar_add",
               r"add (?:things|stuff|events?|appointments?|something) to (?:my |the )?calendars?"
               r"|(?:edit|change) (?:my |the )?calendars?",
               "no", text="No, calendars here are read-only. To hear them, say \"what's on my calendar today\"."),
    Capability("food",
               r"order (?:a |some |me (?:a |some )?)?(?:pizza|food|takeout|takeaway|groceries|dinner|lunch|delivery)",
               "no", text="No, I can't order food."),
    Capability("buy", r"buy (?:.+)|make (?:a )?purchases?|shop online", "no",
               text="No, I can't buy things."),
    Capability("email", r"send (?:an |a )?e-?mails?(?: .+)?|e-?mail (?:.+)|(?:read|check) (?:my )?e-?mails?",
               "no", text="No, I can't send or read email."),
    Capability("call", r"call (?!the |every|all )(?:.+)|make (?:a )?(?:phone )?calls?|phone (?!the )(?:.+)", "no",
               text="No, I can't make phone calls."),
    Capability("text", r"text (?:.+)|send (?:a |an )?(?:texts?|sms|text messages?)(?: .+)?", "no",
               text="No, I can't send text messages."),
    Capability("tv",
               r"(?:control|turn on|turn off|switch on|switch off) (?:my |the )?(?:tv|television)"
               r"|turn (?:my |the )?(?:tv|television) (?:on|off)|change (?:the )?(?:tv )?channels?",
               "no", text="No, I can't control the TV."),
    Capability("appointments",
               r"(?:book|make|schedule) (?:me )?(?:a |an )?(?:[\w']+ )?(?:appointment|reservation)s?(?: .+)?",
               "no", text="No, I can't book appointments."),
    Capability("cameras",
               r"see (?:through )?(?:the |my )?(?:security )?cameras?|(?:watch|check) (?:the |my )?(?:security )?cameras?",
               "no", text="No, I can't see through cameras."),
    Capability("see_me", r"see (?:me|us)", "no", text="No, I can't see. I have no camera."),
    Capability("lights",
               r"(?:turn|switch) (?:on|off) (?:the |my )?lights?|(?:turn|switch) (?:the |my )?lights? (?:on|off)"
               r"|control (?:the |my )?lights?|dim (?:the |my )?lights?",
               "no", text="No, I can't control the lights."),
    Capability("locks",
               r"(?:lock|unlock) (?:the |my )?(?:front |back |side )?doors?|lock (?:up )?the house",
               "no", text="No, I can't lock or unlock doors."),
    Capability("heating",
               r"(?:control|change|set|turn (?:up|down)) (?:the )?(?:thermostat|heating|air conditioning)",
               "no", text="No, I can't control the heating."),
    # Features a plugin adds, by feature (never by name).
    Capability("video_downloads", r"download (?:a |the |that |this |some )?(?:videos?|movies?|shows?)",
               "plugin", text="video downloads", provides="media-acquisition-fulfiller",
               needs_internet=True),
    Capability("make_pictures",
               r"(?:make|draw|create|generate) (?:a |some )?(?:pictures?|images?|drawings?)(?: of .+)?",
               "plugin", text="picture making", slugs=("imagegen",), needs_ai=True),
    Capability("retro_games", r"play (?:retro|old|video) games", "plugin", text="retro games",
               slugs=("romm",)),
    Capability("offline_library",
               r"look (?:things|stuff) up offline|search offline|use an offline (?:library|encyclopedia)",
               "plugin", text="an offline library", slugs=("kiwix",)),
)

CAP_RE = re.compile(
    rf"^{CAP_PREFIX}\s+(?P<x>"
    + "|".join(f"(?P<cap_{c.key}>{c.pattern})" for c in CAPABILITIES)
    + r")(?: for (?:me|us))?$"
)
_CAP_BY_KEY = {c.key: c for c in CAPABILITIES}
_CAN_YOU_TAIL_RE = re.compile(r"(?:^|[,\s])can you(?:[,\s]+please)?[,\s]+$")


def capability_of(transcript: str) -> tuple[Capability, str] | None:
    """The table row a capability question asks about, and its X."""
    m = CAP_RE.match(transcript or "")
    if not m:
        return None
    for key, value in m.groupdict().items():
        if key.startswith("cap_") and value is not None:
            return _CAP_BY_KEY[key[4:]], m.group("x")
    return None


def _command_takes(x: str) -> bool:
    """Whether X said on its own is another handler's command ("can you
    call the kitchen" is a drop-in): then the request is carried out, and
    the question is never answered here."""
    from domovoi.router import first_fast_path

    hit = first_fast_path(x)
    return hit is not None and hit[0].name not in ("house_facts", "help", "help_desk")


def kept_question(transcript: str, stripped: str) -> str | None:
    """For the router's filler stripping: ``transcript`` (normalized) lost
    a leading "can you" on its way to ``stripped``. When what is left is a
    capability-table X that no command takes, the question is kept whole
    ("can you <X>"), so this module's fast path answers it; else None (the
    stripping stands: "can you set a timer for 10 minutes" sets it)."""
    prefix = transcript[: len(transcript) - len(stripped)] if transcript.endswith(stripped) else ""
    if not prefix or not _CAN_YOU_TAIL_RE.search(prefix):
        return None
    candidate = f"can you {stripped}"
    found = capability_of(candidate)
    if found is None or _command_takes(found[1]):
        return None
    return candidate


async def _plugin_state(cap: Capability, scope: Any) -> str | None:
    """"on" | "off" (installed, switched off) | "broken" | "absent", or None."""
    try:
        async with scope() as s:
            rows = (await s.execute(text(
                "SELECT slug, enabled, status, manifest FROM plugins"
            ))).mappings().all()
    except Exception as e:  # noqa: BLE001
        if "does not exist" in str(e).lower():
            return "absent"
        log.debug("house facts: plugins unreadable: %s", e)
        return None
    best = "absent"
    rank = {"absent": 0, "off": 1, "broken": 2, "on": 3}
    for r in rows:
        manifest = r.get("manifest") or {}
        provides = ((manifest.get("capabilities") or {}).get("provides") or []) if isinstance(manifest, dict) else []
        if str(r.get("slug")) not in cap.slugs and (cap.provides is None or cap.provides not in provides):
            continue
        status = str(r.get("status") or "ok")
        if status == "uninstalled":
            continue
        state = ("on" if status in ("ok", "degraded") else "broken") if r.get("enabled") else "off"
        if rank[state] > rank[best]:
            best = state
    return best


def _phrase(handler_name: str | None, preferred: str | None) -> str | None:
    from domovoi.handlers import HANDLER_BY_NAME
    from domovoi.handlers.base import example_phrases_of

    h = HANDLER_BY_NAME.get(handler_name or "")
    if h is None or getattr(h, "plugin_slug", None) is not None:
        return None
    phrases = example_phrases_of(h)
    if preferred and preferred in phrases:
        return preferred
    return phrases[0] if phrases else None


def _internet_off_line(f: HouseFacts) -> str:
    return ("No. This house is set to never use the internet. "
            + _admin_can_change("that", PLACE_INTERNET))


def _ai_off_line(feature: str) -> str:
    return (f"Not right now: {feature} needs AI features, and they're off on this house. "
            + _how_to_turn_on("AI features", place=PLACE_MODELS))


def _instead(cap: Capability, f: HouseFacts) -> str:
    """What a "no" for a plugin's feature offers instead, the same whether
    the plugin is missing, switched off or not ready: an offline library's
    lookups go to the web while the internet answer and AI allow it."""
    if cap.key == "offline_library" and f.internet in ("always", "sometimes", "") and f.ai_on:
        return " I can look things up on the web instead."
    return ""


async def capability_answer(cap: Capability, f: HouseFacts, *, scope: Any = None) -> str | None:
    """The answer to "can you <X>?" for row ``cap``, or None (no answer:
    the turn goes on as before)."""
    scope = scope or _default_scope()
    chat = f.device in CHAT_DEVICES
    if cap.kind == "no":
        return cap.text
    if cap.kind == "core":
        phrase = _phrase(cap.handler, cap.phrase)
        return f'Yes. Say "{phrase}".' if phrase else None
    if cap.kind == "print":
        phrase = _phrase(cap.handler, cap.phrase)
        if phrase is None:
            return None
        try:
            printers = int(await _scalar(scope, "SELECT count(*) FROM printers", missing_table=0) or 0)
        except Exception as e:  # noqa: BLE001
            log.debug("house facts: printers unreadable: %s", e)
            return CANT_CHECK
        if not printers:
            return ("Not yet: no printer is set up. "
                    + _how_to_turn_on("one", place=PLACE_PRINTERS, add=True))
        return f'Yes. Say "{phrase}".'
    if cap.kind == "news":
        phrase = _phrase(cap.handler, cap.phrase)
        if phrase is None or f.internet is None:
            return CANT_CHECK if phrase else None
        if f.internet == "never":
            return _internet_off_line(f)
        if f.news_on is None:
            return CANT_CHECK
        if not f.news_on:
            return "No, the news briefing is off on this house. " + _how_to_turn_on(
                "the news briefing", place=PLACE_ADVANCED)
        # "Sometimes" changes no ask at the moment of asking (it only sets
        # the online extras' defaults, config.PROFILE_DEFAULTS): the
        # headlines are the ones the news worker fetched, and what does ask
        # first, here as under "always", is reading a whole article online
        # (unless "Always allow internet" is on, handlers/news.py).
        out = f'Yes. Say "{phrase}".'
        if f.internet == "sometimes":
            out = (f'Yes. Say "{phrase}": I read the headlines from the last time the '
                   "connection was up.")
        if f.lookups_ask_first:
            out += " I ask before reading a whole article online."
        return out
    if cap.kind == "internet":
        if f.internet is None:
            return CANT_CHECK
        if f.internet == "never":
            return _internet_off_line(f)
        if f.ai_on is False:
            return _ai_off_line("looking things up")
        if chat:
            ask = " I'll ask before each lookup leaves the house." if f.lookups_ask_first else ""
            return f'Yes. Type "look up" and what you want to find.{ask}'
        # A spoken lookup goes online only when asked ("are you sure?", or
        # yes to "want me to check that online?"), under "sometimes" exactly
        # as under "always": that answer only sets defaults.
        return 'Yes. Ask me a question, then say "are you sure?" to have me check it online.'
    if cap.kind == "chat":
        if f.ai_on is None:
            return CANT_CHECK
        what = "pictures" if cap.text == "picture" else "videos"
        if not f.ai_on:
            return _ai_off_line(f"reading {what}")
        if f.vision_model is False:
            return ("Not right now: no model for reading pictures is set. "
                    + _how_to_turn_on("one", place=PLACE_MODELS, add=True))
        limit = " up to 3 minutes long" if cap.text == "video" else ""
        where = "here" if chat else "in chat"
        return f"Yes, {where}: attach a {cap.text}{limit} and ask about it."
    if cap.kind == "plugin":
        state = await _plugin_state(cap, scope)
        if state is None:
            return CANT_CHECK
        feature = cap.text or "it"
        if state == "absent":
            return f"No, not on this house. {_how_to_turn_on(feature, add=True)}" + _instead(cap, f)
        if state == "off":
            return (f"No, {feature} is switched off on this house. {_how_to_turn_on(feature)}"
                    + _instead(cap, f))
        if state == "broken" and cap.key == "offline_library":
            # Installed and on, but not answering: on a new install that is
            # most often a library with nothing downloaded yet, which the
            # core can't tell from a fault, so both are said.
            return ("Not right now: no offline library is set up yet, or it isn't working. "
                    f"An admin can download one on {PLACE_LIBRARY}, or check it on {PLACE_PLUGINS}."
                    + _instead(cap, f))
        if state == "broken":
            return f"Not right now: {feature} isn't working. An admin can check it on {PLACE_PLUGINS}."
        if cap.needs_internet and f.internet == "never":
            return ("Not right now: this house is set to never use the internet. "
                    + _admin_can_change("that", PLACE_INTERNET))
        if cap.needs_ai and f.ai_on is False:
            return _ai_off_line(feature)
        return f"Yes, {feature} {'is' if not feature.endswith('s') else 'are'} on in this house."
    return None


# ─── The fallback: a fixed privacy answer, never a model's ────────────────

_PRIVACY_WORDS_RE = re.compile(
    # The privacy topic: "is it creepy that you listen", "do you spy on us",
    # "does your server send my stuff to the cloud", "do you sell my data".
    r"\b(?:record(?:ing|ed|s)? (?:me|us|everything|what|my|our|this|conversations)"
    r"|listen(?:ing)? (?:to (?:me|us|everything|what|our|my conversations)|all the time|in on)"
    r"|always listening|spy(?:ing)?|eavesdrop\w*|track(?:ing)? (?:me|us)|privacy|private"
    r"|(?:keep|store|save|send|share|sell)s? (?:what i|what we|my|our|everything)"
    r"|the cloud|leaves? the house|my data|my voice|voice history|my recordings|creepy)\b"
)
# ... and about THIS house: you, Domovoi, this house or server, here. A
# privacy question about anything else ("is my data safe with Google?",
# "how do I protect my privacy online?") is an ordinary question for the
# model; the fixed answer only ever speaks for this house.
_ABOUT_THIS_HOUSE_RE = re.compile(
    r"\b(?:you|you're|youre|your|yours|domovoi|domo|here"
    r"|this (?:house|home|thing|device|speaker|server|assistant))\b"
)
# Someone else named as the subject ("with Google", "on my phone", "by
# apps"): not this house, even with "you" in the sentence.
_SOMEONE_ELSE_RE = re.compile(
    r"\b(?:with|on|by|from|at|to) (?!(?:you|your|yours|this|here|domovoi|domo|me|us|the cloud|the house)\b)"
    r"(?:my |the |a |an )?(?:phone|apps?|websites?|sites?|browser|computer|laptop|tv|company|companies"
    r"|[A-Z][a-z]+)\b"
    # ... or another device is the subject: "is my phone spying on me".
    r"|\b(?:my|the|an?) (?:phone|apps?|websites?|browser|computer|laptop|tv|car|watch|router)\b"
)


def wants_facts(transcript: str) -> bool:
    """A privacy-worded question outside the closed set ("is it creepy
    that you listen?"): it gets :func:`privacy_summary`, not the model."""
    raw = transcript or ""
    text = raw.lower()
    if not (_PRIVACY_WORDS_RE.search(text) and _ABOUT_THIS_HOUSE_RE.search(text)):
        return False
    # A named other subject (case kept: "with Google") makes it not ours.
    return not _SOMEONE_ELSE_RE.search(raw) and not _SOMEONE_ELSE_RE.search(text)


def _yes_no(v: bool | None, yes: str = "on", no: str = "off") -> str:
    return "unknown" if v is None else (yes if v else no)


def privacy_summary(f: HouseFacts) -> str:
    """The fixed answer to a privacy-worded question the closed set didn't
    take ("is it creepy that you listen?", "do you sell my data?"): what
    leaves the house, what is kept, and where the rest is shown. Built
    from the facts, never by a model: measured 2026-11-25 (eval_qa_guard,
    30 seeds), llama3.2:3b given these facts as data still made up wrong
    privacy claims ("your recordings are stored for 90 days" in a room
    that keeps none), so no model answers a privacy question."""
    parts = [_leaves_house(f), _keep_answer(f)]
    if any(p == CANT_CHECK for p in parts):
        return f"{CANT_CHECK} {PRIVACY_POINTER}"
    return " ".join(parts + [PRIVACY_POINTER])


async def privacy_fallback(transcript: str, ctx: Any) -> str | None:
    """:func:`privacy_summary` for a privacy-worded ``transcript``, else
    None. Never fails: a facts read that breaks gives the can't-check line."""
    if not wants_facts(transcript):
        return None
    try:
        f = await read(room_id=getattr(ctx, "room_id", None),
                       device_kind=getattr(ctx, "device_kind", None), app=getattr(ctx, "app", None))
    except Exception as e:  # noqa: BLE001 — still an honest answer
        log.warning("house facts: the privacy fallback failed: %s", e)
        return f"{CANT_CHECK} {PRIVACY_POINTER}"
    return privacy_summary(f)


# ─── Chat ─────────────────────────────────────────────────────────────────


async def chat_reply(
    message: str,
    *,
    get_setting: SettingGetter,
    scope: Any,
    previous_assistant: Callable[[], Any] | None = None,
) -> str | None:
    """The deterministic reply to a typed chat message, or None (the chat
    answers as before). ``previous_assistant`` is an async callable giving
    the thread's last assistant row (``{"stats": ...}``) or None."""
    t = normalize(message)
    qid = question_id(t)
    if qid == "internet_for_that":
        prev = await previous_assistant() if previous_assistant is not None else None
        return chat_internet_answer(prev)
    cap = capability_of(t) if qid is None else None
    if qid is None and cap is None:
        return None
    f = await read(chat=True, get_setting=get_setting, scope=scope)
    if qid is not None:
        return answer(qid, f)
    return await capability_answer(cap[0], f, scope=scope)

