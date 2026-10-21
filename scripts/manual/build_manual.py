"""Build the user manual's content file from its source.

    python scripts/manual/build_manual.py           # write web/static/manual-content.json
    python scripts/manual/build_manual.py --check   # exit 1 when the file is out of date

The words are ``scripts/manual/manual.md``; this script adds the map (the
topics around "Domovoi", each topic's pieces, their subtitles and search
words), the link routes for the dashboard and the app, the "Getting set
up" checklist's ticks, and which handler each "try saying" phrase reaches
(PHRASES). The dashboard (web/static/manual.jsx) and the Android app
(ui/screens/manual/) both render the one file; the app fetches it from
the server it is connected to, so its manual matches that server.

``domovoi/tests/test_manual_content.py`` holds the file and the source in
sync, checks every link and route, and routes every fast-path phrase.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "scripts" / "manual" / "manual.md"
OUT = REPO / "web" / "static" / "manual-content.json"
SCHEMA = "domovoi-manual/2"

# ── routes: one id per destination, for both clients ─────────────────────
# dashboard: the hash route (None: the app's own screen, no dashboard page).
# app: a screen (the Route enum, android/.../ui/shell/Routes.kt) and an
# optional Settings tab key (SettingsTab, SettingsScreen.kt), or
# {"dashboard": True}: the app opens the dashboard in the browser at the
# same route (the app has no admin session).
ROUTES: dict[str, dict] = {
    "home": {"dashboard": "#home", "app": {"screen": "Home"}},
    "chat": {"dashboard": "#chat", "app": {"screen": "Chat"}},
    "music": {"dashboard": "#music", "app": {"screen": "Music"}},
    "music/queue": {"dashboard": "#music/queue", "app": {"screen": "Music"}},
    "music/playlists": {"dashboard": "#music/playlists", "app": {"screen": "Music"}},
    "podcasts": {"dashboard": "#podcasts", "app": {"screen": "Podcasts"}},
    "audiobooks": {"dashboard": "#audiobooks", "app": {"screen": "Audiobooks"}},
    "videos": {"dashboard": "#videos", "app": {"screen": "Videos"}},
    "news": {"dashboard": "#news", "app": {"screen": "News"}},
    "people": {"dashboard": "#people", "app": {"screen": "People"}},
    "calendar": {"dashboard": "#calendar", "app": {"screen": "Calendar"}},
    "files": {"dashboard": "#files", "app": {"screen": "Files"}},
    "plugins": {"dashboard": "#plugins", "app": {"dashboard": True}},
    # Settings' sections (web settings.jsx SETTINGS_SECTIONS, the app's
    # SettingsSection in SettingsScreen.kt): the app shows each one; the ones
    # only the dashboard can change say so there and open it.
    "settings/about": {"dashboard": "#settings/about", "app": {"screen": "Settings", "tab": "about"}},
    "settings/app-layout": {"dashboard": "#settings/app-layout", "app": {"screen": "Settings", "tab": "app-layout"}},
    "settings/rooms": {"dashboard": "#settings/rooms", "app": {"screen": "Settings", "tab": "rooms"}},
    "settings/greetings": {"dashboard": "#settings/greetings", "app": {"screen": "Settings", "tab": "greetings"}},
    "settings/voices": {"dashboard": "#settings/voices", "app": {"dashboard": True}},
    "settings/wake-words": {"dashboard": "#settings/wake-words", "app": {"screen": "Settings", "tab": "wake-words"}},
    "settings/phones-and-browsers": {"dashboard": "#settings/phones-and-browsers", "app": {"dashboard": True}},
    "settings/models": {"dashboard": "#settings/models", "app": {"screen": "Settings", "tab": "models"}},
    "settings/internet": {"dashboard": "#settings/internet", "app": {"screen": "Settings", "tab": "internet"}},
    "settings/voice-history": {"dashboard": "#settings/voice-history", "app": {"screen": "Settings", "tab": "voice-history"}},
    "settings/health": {"dashboard": "#settings/health", "app": {"screen": "Settings", "tab": "health"}},
    "settings/advanced": {"dashboard": "#settings/advanced", "app": {"screen": "Settings", "tab": "advanced"}},
    # the app's own: plain text on the dashboard
    "app/connection": {"dashboard": None, "app": {"screen": "Settings", "tab": "phones-and-browsers"}},
    "app/voice": {"dashboard": None, "app": {"screen": "Settings", "tab": "voices"}},
}

# ── the map ───────────────────────────────────────────────────────────────
# Level 1: the topics around "Domovoi". The centre opens "How it works".
# Level 2: a topic's pieces (at most 7 each). Level 3: a piece's page.
TOPICS: list[dict] = [
    {"id": "how-it-works", "title": "How it works", "subtitle": "your home's own assistant", "centre": True,
     "keywords": ["how does it work", "technical", "architecture", "server", "models", "privacy", "offline"]},
    {"id": "things-to-say", "title": "Things to say", "subtitle": "talk to any room",
     "keywords": ["voice", "commands", "what can I say", "phrases", "talk", "ask"]},
    {"id": "rooms-and-devices", "title": "Rooms and devices", "subtitle": "the speakers that listen",
     "keywords": ["satellite", "room", "speaker", "raspberry pi", "microphone", "device"]},
    {"id": "your-phone", "title": "Your phone", "subtitle": "the Domovoi app",
     "keywords": ["android", "app", "phone", "tablet", "mobile"]},
    {"id": "music-and-media", "title": "Music and media", "subtitle": "music, podcasts, books, videos, news",
     "keywords": ["listen", "watch", "media", "library"]},
    {"id": "chat-files-calendar", "title": "Chat, files and calendar", "subtitle": "your household's pages",
     "keywords": ["chat", "files", "documents", "calendar", "photos"]},
    {"id": "people-and-privacy", "title": "People and privacy", "subtitle": "who's who, and what's kept",
     "keywords": ["privacy", "people", "admin", "guest", "data", "internet", "security"]},
    {"id": "keeping-it-running", "title": "Keeping it running", "subtitle": "updates, restarts, backups, plugins",
     "keywords": ["maintenance", "update", "restart", "backup", "plugin", "problem", "help"]},
]

# "### title" → (id, subtitle, search words, extras). Extras: live (a live
# slot on the page), plugin (shown only when that plugin is installed and
# enabled), alias_of (the piece opens that piece's page).
PIECES: dict[str, tuple] = {
    # How it works
    "Hears": ("hears", "the wake word, then your words", ["whisper", "speech to text", "stt", "wake word", "transcription", "microphone"], {}),
    "Thinks": ("thinks", "quick commands, then the models", ["ollama", "llm", "ai", "model", "qwen", "llama", "routing", "gpu", "cpu"], {}),
    "Speaks": ("speaks", "three voices, all on the server", ["tts", "text to speech", "piper", "voice", "kristin", "norman", "linda"], {}),
    "Remembers": ("remembers", "one database, on your server", ["database", "postgres", "history", "storage"], {}),
    "Plays": ("plays", "a music player for every room", ["mpd", "music player", "streaming", "rooms"], {}),
    "The server": ("server", "Linux, or a Windows PC; GPU optional", ["linux", "windows", "wsl", "nvidia", "gpu", "cpu", "memory", "ram", "hardware", "installer"], {}),
    "Dashboard and app": ("dashboard-and-app", "where you manage it", ["dashboard", "web", "browser", "app", "6369"], {}),
    # Things to say
    "Timers and reminders": ("timers", "countdowns and nudges", ["timer", "reminder", "remind me", "countdown", "alarm", "kitchen timer", "at 6 pm"], {}),
    "Quick answers": ("quick-answers", "time, maths, questions", ["time", "date", "calculator", "math", "maths", "convert", "units", "weather", "question", "tip", "are you sure", "fact check"], {}),
    "Music by voice": ("music-voice", "play, pause, any room", ["play", "pause", "skip", "volume", "louder", "another room", "shuffle", "favorites"], {}),
    "Talk to other rooms": ("talk", "announce, drop in", ["intercom", "announce", "broadcast", "drop in", "call", "tell everyone"], {}),
    "Notes and memory": ("notes", "jot it down, remember it", ["note", "jot", "remember", "memory", "favorite"], {}),
    "Everything it can do": ("everything", "every installed feature", ["what can you do", "features", "capabilities", "list", "help", "plugins"], {"live": "things-to-say"}),
    "When it mishears": ("mishears", "repeat, never mind, try again", ["repeat", "never mind", "misheard", "wake word not working", "doesn't hear", "sensitivity"], {}),
    # Rooms and devices
    "Your rooms": ("rooms", "online, playing, last heard", ["satellites", "rooms", "offline", "wifi", "restart satellite", "upgrade", "reset pairing"], {"live": "rooms"}),
    "A Raspberry Pi room": ("pi-room", "the usual room", ["raspberry pi", "pi", "sd card", "add a room", "setup wifi", "approve", "respeaker", "xvf3800"], {}),
    "A spare laptop or PC": ("laptop-room", "Linux or Windows", ["laptop", "pc", "linux", "windows", "spare device", "old laptop"], {}),
    "An old phone or tablet as a room": ("phone-room", "room mode", ["room mode", "old phone", "tablet", "domovoi room", "qr code"], {}),
    "An old computer, from a USB stick": ("usb-room", "the stick sets it up", ["usb stick", "old computer", "old pc", "mac", "debian", "erase"], {}),
    "Wake word and greeting": ("wake-word", "the word a room wakes to", ["wake word", "hey jarvis", "train", "training", "custom wake word", "greeting", "chime"], {"live": "wake-word"}),
    "Domovoi's voice": ("voice", "pick it, fix a word", ["voice", "say it like", "pronunciation", "kristin", "norman", "linda", "piper"], {}),
    # Your phone
    "Get the app and connect": ("connect", "from your own server", ["install", "apk", "connect", "pair", "household token", "server", "qr"], {}),
    "Music on the phone": ("phone-music", "here or in a room, saved offline", ["offline", "download", "save to device", "player", "your device", "add folder"], {}),
    "Chat and the chat mic": ("phone-chat", "type, talk, send photos", ["chat mic", "talk", "push to talk", "photo", "heic", "picture"], {}),
    "The assistant": ("assistant", "no server needed", ["assistant", "offline", "no server", "airplane"], {}),
    "This phone as a room": ("phone-as-room", "room mode, on this phone", ["room mode", "this phone"], {"alias_of": "phone-room"}),
    "App settings and updates": ("app-settings", "bar, voice, new versions", ["settings", "phone bar", "update", "new version", "privacy", "about"], {}),
    # Music and media
    "Music and room queues": ("music", "the library, every room", ["music", "queue", "playlist", "upload", "library", "favorites"], {}),
    "Lyrics": ("lyrics", "see the words, find a song", ["lyrics", "words", "song that goes", "lrclib"], {}),
    "Podcasts": ("podcasts", "shows and episodes", ["podcast", "episode", "subscribe", "rss"], {}),
    "Audiobooks": ("audiobooks", "your books, with chapters", ["audiobook", "book", "chapter"], {}),
    "Videos": ("videos", "resume where you left off", ["video", "movie", "watch"], {}),
    "News": ("news", "briefings and topics", ["news", "headlines", "briefing", "topics", "feeds"], {}),
    "Radio": ("radio", "stations you star", ["radio", "station", "fm", "stream"], {"plugin": "radio"}),
    # Chat, files and calendar
    "Chat": ("chat", "type to Domovoi, pictures too", ["chat", "text", "pictures", "vision", "images"], {}),
    "Files and photos": ("files", "every library, one place", ["files", "upload", "photos", "pictures", "move", "delete", "usb drive"], {}),
    "Documents, sheets and drawings": ("documents", "write and edit on the server", ["document", "spreadsheet", "sheet", "drawing", "text file", "markdown", "xlsx"], {}),
    "Calendar": ("calendar", "the household's events", ["calendar", "event", "appointment", "what's on"], {}),
    # People and privacy
    "People and voices": ("people", "Domovoi learns who's talking", ["people", "voice id", "who am i", "my name is", "forget me", "profile"], {}),
    "Guests, household and admin": ("guests", "who can change what", ["admin", "password", "guest", "sign in", "setup code", "household token", "paired"], {}),
    "Devices and house rules": ("devices", "names, queue and file access", ["devices", "ban", "queue access", "files access", "shared screen", "kitchen tablet"], {}),
    "What's kept, and forgetting": ("kept", "how long, and \"forget me\"", ["privacy", "data", "history", "delete", "forget", "retention", "recordings", "transcripts", "voice history"], {}),
    "Internet: yes, sometimes, no": ("internet", "your choice, any time", ["internet", "offline", "no internet", "online", "go dark"], {}),
    # Keeping it running
    "Updates": ("updates", "releases, channels, the app", ["update", "upgrade", "version", "release", "stable", "main", "channel"], {}),
    "Restarting": ("restart", "when the server isn't answering", ["restart", "frozen", "not answering", "offline", "core"], {}),
    "Backups and moving": ("backups", "what to keep safe", ["backup", "restore", "move", "new computer", "reinstall"], {}),
    "Plugins": ("plugins", "add features", ["plugin", "install", "add feature", "github", "zip"], {"live": "plugins"}),
    "Models": ("models", "speech, answers, pictures", ["models", "vision", "whisper", "ollama", "speech-to-text"], {}),
    "Troubleshooting": ("troubleshooting", "the common fixes", ["problem", "not working", "help", "fix", "error", "broken"], {}),
}

# "Getting set up": what ticks each line, and where it shows. The clients
# read done_when (see the schema notes below).
CHECKLIST: list[dict] = [
    {"id": "claim-admin", "done_when": "admin_claimed", "platforms": ["dashboard"]},
    {"id": "internet", "done_when": "internet_answered", "platforms": ["dashboard"]},
    {"id": "first-room", "done_when": "rooms", "platforms": ["dashboard", "app"]},
    {"id": "wake-word", "done_when": "room_heard", "platforms": ["dashboard", "app"]},
    {"id": "introduce", "done_when": "people", "platforms": ["dashboard", "app"]},
    {"id": "music", "done_when": "music", "platforms": ["dashboard", "app"]},
    {"id": "app", "done_when": "phone_app", "platforms": ["dashboard"],
     "app_text": "**Pair this phone**: [Settings → Phones & browsers](#app/connection) → **Household token**.",
     "app_done_when": "this_phone_paired"},
]

# Every "try saying" phrase → (handler, path, extras): the handler the
# router gives it and how. path: fast (a fast path: the test routes it
# with no model), llm (the tool model picks it: marked, not routed by the
# test), qa (no handler: the answer model, or its offer to check online).
# extras: try (the phrase the test routes, for one written with "…"),
# plugin (a plugin's handler), pending (behaviour another branch builds:
# the test expects it NOT to route yet, and fails once it does, so the
# mark comes off). Checked through POST /v1/intent on v1.0.0-rc2.
PHRASES: dict[str, tuple] = {
    "set a timer for 10 minutes": ("timer", "fast", {}),
    "what's the weather tomorrow": (None, "qa", {}),
    "play some jazz in the kitchen": ("music", "fast", {}),
    "tell everyone dinner's ready": ("intercom", "fast", {}),
    "set a timer for 8 minutes for the pasta": ("timer", "fast", {}),
    "how much time is left on the timer": ("timer", "llm", {}),
    "cancel the timer": ("timer", "fast", {}),
    "remind me to call Mom in 20 minutes": ("reminder", "fast", {}),
    "remind me in 2 hours to take the bins out": ("reminder", "fast", {}),
    "remind me at 6 pm to take the bins out": ("reminder", "fast", {}),
    "wake me up at 7": ("reminder", "fast", {}),
    "set an alarm for 6:30": ("reminder", "fast", {}),
    "cancel the 7 am alarm": ("reminder", "fast", {}),
    "what reminders do I have": ("reminder", "fast", {}),
    "what time is it": ("clock", "fast", {}),
    "what day is it": ("clock", "fast", {}),
    "what's 15 percent of 80": ("calculator", "fast", {}),
    "convert 5 miles to kilometers": ("calculator", "fast", {}),
    "how many days until Christmas": ("calculator", "fast", {}),
    "split $120 4 ways with a 20 percent tip": ("calculator", "llm", {}),
    "why is the sky blue": (None, "qa", {}),
    "are you sure?": ("double_check", "fast", {}),
    "play some jazz": ("music", "fast", {}),
    "play Norah Jones": ("music", "fast", {}),
    "shuffle my music": ("music", "fast", {}),
    "pause the music": ("music", "fast", {}),
    "skip this song": ("music", "fast", {}),
    "turn it up": ("music", "fast", {}),
    "what's playing": ("music", "fast", {}),
    "play my favorites": ("playlist", "fast", {}),
    "play some music in the garage": ("music", "fast", {}),
    "in the garage, stop the music": ("music", "fast", {}),
    "what's playing in the office": ("music", "fast", {}),
    "play the song that goes …": ("music", "fast", {"try": "play the song that goes warm stones by the fire"}),
    "when I say Gramps I mean Hearth Ensemble": ("music_alias", "fast", {}),
    "tell the kitchen dinner's ready": ("intercom", "llm", {}),
    "drop in on the kitchen": ("dropin", "fast", {}),
    "hang up": ("dropin", "fast", {}),
    "jot down buy milk and eggs": ("voice_notes", "fast", {}),
    "read my notes": ("voice_notes", "fast", {}),
    "what was my last note": ("voice_notes", "fast", {}),
    "remember that I parked on level 3": ("memory", "fast", {}),
    "my favorite band is Weezer": ("memory", "fast", {}),
    "what do you remember about me": ("memory", "fast", {}),
    "forget that I parked on level 3": ("memory", "fast", {}),
    "what can you do": ("help", "fast", {}),
    "what can I say": ("help", "fast", {}),
    "say that again": ("repeat", "fast", {}),
    "never mind": ("dismiss", "fast", {}),
    "what voices are there": ("voice", "fast", {}),
    "let me hear Norman's voice": ("voice", "fast", {}),
    "switch to Norman's voice": ("voice", "fast", {}),
    "play the latest episode of The Daily": ("spoken_audio", "fast", {}),
    "subscribe to The Daily": ("spoken_audio", "fast", {}),
    "skip forward 30 seconds": ("spoken_audio", "fast", {}),
    "set speed to 1.5": ("spoken_audio", "fast", {}),
    "resume the podcast in the kitchen": ("spoken_audio", "fast", {}),
    "resume my audiobook": ("spoken_audio", "fast", {}),
    "next chapter": ("spoken_audio", "fast", {}),
    "play my audiobook in the office": ("spoken_audio", "fast", {}),
    "what's the news": ("news", "fast", {}),
    "read me the headlines": ("news", "fast", {}),
    "any news about the city council": ("news", "fast", {}),
    "stream the jazz station": ("radio", "fast", {"plugin": "radio"}),
    "stop the radio": ("radio", "fast", {"plugin": "radio"}),
    "what's on my calendar today": ("calendar", "fast", {}),
    "what's next on my calendar": ("calendar", "fast", {}),
    "do I have anything on Friday": ("calendar", "fast", {}),
    "my name is Sarah": ("voice_profile", "fast", {}),
    "who am I": ("voice_profile", "fast", {}),
    "forget me": ("voice_profile", "fast", {}),
    "never save my voice": ("voice_profile", "fast", {}),
}

# The wake word: what {wake} and [live slot: wake-word] say. One place, so
# the words can change without touching either client. A v1.0.0 room
# answers to hey_jarvis (owner, 2026-10-17); the live slot shows what the
# household's rooms really answer to.
WAKE_WORD = {
    "default": "hey_jarvis",
    # openWakeWord's own words; a trained word's phrase comes from the
    # server (the satellite row's wake_phrase), else its slug in words.
    "names": {"hey_jarvis": "Hey Jarvis", "alexa": "Alexa", "hey_mycroft": "Hey Mycroft",
              "hey_rhasspy": "Hey Rhasspy"},
    # {wake}: the word most rooms answer to (the default while any room
    # does, or nothing is known), in quotes.
    "quoted": "“{phrase}”",
    # The live slot, shown only when some rooms answer to another word.
    # {rooms}: the rooms, joined with ", ".
    "note": "Some rooms answer to another word: {list}.",
    "note_item": "“{phrase}” in {rooms}",
    "note_join": "; ",
}

# "Everything it can do": a feature listed only while the server has it
# switched on (GET /api/capabilities features). Chat mode answers only
# while chat mode is enabled, which it isn't by default; Double-Check only
# while the household's AI features are on (domovoi/ai_features.py: a
# fact-check is the model reading the search results).
HANDLER_NEEDS_FEATURE = {"chat_mode": "chat", "double_check": "ai"}

# A "try saying" phrase the language models answer: one the router hands to
# the tool model (llm) or to the Q&A model (qa), or one whose handler needs
# a feature above. It carries "needs": the feature, and both clients leave it
# out while GET /api/capabilities says that feature is off (features.ai
# false: AI chat and questions are off). A "then" reply goes with its phrase.
AI_PATHS = frozenset({"llm", "qa"})


def phrase_needs(handler: str | None, path: str | None) -> str | None:
    if handler in HANDLER_NEEDS_FEATURE:
        return HANDLER_NEEDS_FEATURE[handler]
    if path in AI_PATHS:
        return "ai"
    return None

LABELS = {
    "start_here": "start here",
    "map": "map",
    "centre_hint": "how it works",
    "map_hint": "the middle: how it works · a topic: its pages",
    "show_map": "show me the map",
    "eyebrow": "user manual",
    "next": "next",
    "search_placeholder": "search the manual",
    "search_empty": "nothing matches",
    "zoom_out": "zoom out",
    "back": "back",
    "more_detail": "More detail",
    "try_saying": "Try saying",
    "on_the_dashboard": "opens the dashboard",
    "live_unavailable": "can't reach the server for this right now",
    "checklist_unknown": "can't tell from here",
    "preview": {"text": "preview", "means": "It works, but it has been tried on fewer devices than the rest of Domovoi, and it may still change."},
    "admin": {"text": "admin", "means": "Needs the admin password. Ask whoever set up Domovoi."},
    "plugin": {"text": "plugin", "means": "Shown when this plugin is installed."},
    "pending": {"text": "coming", "means": "Being built now; this server may not answer to it yet."},
    "net": {"no": "works offline", "degraded": "less offline", "yes": "needs internet"},
    "say_none": "no phrases listed",
    "rooms_none": "no rooms yet",
    "plugins_none": "no plugins installed",
}

# The source's paragraph labels → their role. "**Name**" alone (optionally
# with *(admin)*), or "**Name:** text".
LABEL_ROLES = {
    "Try saying": "say", "How to": "how", "If something's wrong": "wrong", "Also": "also",
    "Three ways to use it": "ways", "Getting set up": "checklist",
    "Train your own wake word": "how", "Greetings": "how",
}
ONLY_PREFIXES = [("**App:**", "app"), ("**Dashboard:**", "dashboard")]
DOC_PREFIXES = ("docs/", "README.md", "scripts/", "android/", "plugins/")
LIVE_SLOTS = ("things-to-say", "rooms", "plugins", "wake-word")


class SourceError(ValueError):
    pass


# ── source → sections ─────────────────────────────────────────────────────


def strip_comments(md: str) -> str:
    return re.sub(r"<!--.*?-->", "", md, flags=re.S)


def sections(md: str) -> list[tuple[int, str, list[str]]]:
    """(level, heading, body lines) for every '## ' and '### ' heading."""
    out: list[tuple[int, str, list[str]]] = []
    for ln in md.splitlines():
        m = re.match(r"^(#{2,3}) (.+)$", ln)
        if m:
            out.append((len(m.group(1)), m.group(2).strip(), []))
        elif out and ln.strip() != "---":
            out[-1][2].append(ln)
    return out


def apply_snippets(secs: list[tuple[int, str, list[str]]]) -> list[tuple[int, str, list[str]]]:
    """Replace each '{{name}}' line with the body of '### name' under '## Snippets'."""
    try:
        at = next(i for i, s in enumerate(secs) if s[0] == 2 and s[1] == "Snippets")
    except StopIteration:
        return secs
    snippets = {h: body for lvl, h, body in secs[at + 1:] if lvl == 3}
    for _lvl, h, body in secs[at + 1:]:
        if _lvl == 2:
            raise SourceError(f"nothing may follow ## Snippets: found ## {h}")

    def trim(lines: list[str]) -> list[str]:
        while lines and not lines[0].strip():
            lines = lines[1:]
        while lines and not lines[-1].strip():
            lines = lines[:-1]
        return lines

    used: set[str] = set()
    out = []
    for lvl, h, body in secs[:at]:
        new: list[str] = []
        for ln in body:
            m = re.match(r"^\{\{([a-z0-9-]+)\}\}$", ln.strip())
            if not m:
                new.append(ln)
                continue
            name = m.group(1)
            if name not in snippets:
                raise SourceError(f"{{{{{name}}}}} in '{h}' has no '### {name}' under ## Snippets")
            used.add(name)
            new.extend(trim(list(snippets[name])))
        out.append((lvl, h, new))
    unused = sorted(set(snippets) - used)
    if unused:
        raise SourceError(f"snippets used nowhere: {unused}")
    return out


# ── inline text ───────────────────────────────────────────────────────────


def convert_inline(t: str) -> str:
    t = t.replace("*(admin, once)*", "{admin} once:")
    t = t.replace("*(admin)*", "{admin}").replace("*(Preview)*", "{preview}").replace("*(plugin)*", "{plugin}")
    t = re.sub(r"\]\(#node:([a-z0-9-]+)\)", r"](node:\1)", t)
    t = re.sub(r"\]\(#([a-z0-9/-]+)\)", r"](route:\1)", t)
    t = re.sub(r"\]\(((?:" + "|".join(re.escape(p) for p in DOC_PREFIXES) + r")[^)]*)\)", r"](doc:\1)", t)
    return re.sub(r"\s+", " ", t).strip()


def blocks_of(lines: list[str]) -> list[list[str]]:
    out, cur = [], []
    for ln in lines:
        if ln.strip() == "":
            if cur:
                out.append(cur)
                cur = []
        else:
            cur.append(ln)
    if cur:
        out.append(cur)
    return out


def list_items(block: list[str]) -> tuple[bool, list[str]]:
    items: list[str] = []
    ordered = bool(re.match(r"^\d+\.\s", block[0]))
    for ln in block:
        m = re.match(r"^(?:-|\d+\.)\s+(.*)$", ln)
        if m:
            items.append(m.group(1))
        elif items:
            items[-1] += " " + ln.strip()
        else:
            raise SourceError(f"a list that doesn't start with an item: {ln!r}")
    return ordered, items


def split_only(t: str) -> tuple[str | None, str]:
    """A leading **App:** / **Dashboard:** → (where it shows, the rest,
    capitalised: the prefix is not shown)."""
    for prefix, only in ONLY_PREFIXES:
        if t.startswith(prefix):
            rest = t[len(prefix):].strip()
            return only, rest[:1].upper() + rest[1:]
    return None, t


def phrase_claim(text: str) -> dict:
    if text not in PHRASES:
        raise SourceError(f'"try saying" phrase without a PHRASES entry: {text!r}')
    handler, path, extras = PHRASES[text]
    claim: dict = {"handler": handler, "path": path}
    needs = phrase_needs(handler, path)
    if needs:
        claim["needs"] = needs
    for k in ("try", "plugin", "pending"):
        if extras.get(k):
            claim[k] = extras[k]
    return claim


def parse_say(item: str) -> list[dict]:
    """One "try saying" item: "a" · "b", or "a", then "yes", or "a" (note)."""
    phrases: list[dict] = []
    for part in re.split(r"\s+·\s+", item):
        quoted = re.findall(r'"([^"]+)"', part)
        if not quoted:
            raise SourceError(f"a try-saying item with no quoted phrase: {part!r}")
        rest = re.sub(r'"[^"]+"', "", part).strip(" ,")
        if rest == ".":
            rest = ""
        if len(quoted) == 2 and rest == "then":
            first = {"text": quoted[0], **phrase_claim(quoted[0])}
            phrases.append(first)
            reply = {"text": quoted[1], "then": True}
            if first.get("needs"):
                reply["needs"] = first["needs"]
            phrases.append(reply)
            continue
        if len(quoted) != 1:
            raise SourceError(f"a try-saying item must hold one phrase: {part!r}")
        p = {"text": quoted[0], **phrase_claim(quoted[0])}
        if rest:
            if not (rest.startswith("(") and rest.endswith(")")):
                raise SourceError(f"a phrase's note goes in brackets: {part!r}")
            p["note"] = convert_inline(rest[1:-1])
        phrases.append(p)
    return phrases


MORE_RE = re.compile(r"\s*\[(More detail|All troubleshooting)\]\(([^)]+)\)\.?\s*$")


def parse_body(lines: list[str], where: str) -> dict:
    blocks: list[dict] = []
    more = None
    label = None
    for b in blocks_of(lines):
        first = b[0]
        if first.startswith("|"):
            rows = [[c.strip() for c in ln.strip().strip("|").split("|")]
                    for ln in b if not re.match(r"^\|[-| ]+\|$", ln.strip())]
            blocks.append({"type": "table", "header": [convert_inline(c) for c in rows[0]],
                           "rows": [[convert_inline(c) for c in r] for r in rows[1:]]})
            label = None
            continue
        if re.match(r"^(?:-|\d+\.)\s", first):
            ordered, items = list_items(b)
            if label == "say":
                say: list[dict] = []
                for it in items:
                    say += parse_say(it)
                blocks.append({"type": "say", "phrases": say})
            elif label == "checklist":
                blocks.append({"type": "checklist", "items": [convert_inline(i) for i in items]})
            else:
                out_items = []
                for it in items:
                    only, txt = split_only(it.strip())
                    out_items.append({"text": convert_inline(txt), **({"only": only} if only else {})})
                blocks.append({"type": "list", "ordered": ordered, "items": out_items})
            label = None
            continue
        text = " ".join(ln.strip() for ln in b)
        m = MORE_RE.search(text)
        if m:
            if more:
                raise SourceError(f"two More detail links in '{where}'")
            more = {"text": m.group(1), "target": convert_inline(f"[x]({m.group(2)})")[4:-1]}
            text = text[: m.start()].strip()
            if not text:
                continue
        lm = re.match(r"^\[live slot: ([a-z-]+)\]$", text)
        if lm:
            if lm.group(1) not in LIVE_SLOTS:
                raise SourceError(f"unknown live slot {lm.group(1)!r} in '{where}'")
            blocks.append({"type": "live", "slot": lm.group(1)})
            label = None
            continue
        hm = re.match(r"^\*\*([^*]+?)(:)?\*\*(:)?\s*(\*\(admin\)\*)?\s*(.*)$", text)
        if hm and hm.group(1) in LABEL_ROLES:
            name = hm.group(1)
            role = LABEL_ROLES[name]
            rest = hm.group(5).strip()
            lab: dict = {"type": "label", "role": role, "text": name}
            if hm.group(4):
                lab["admin"] = True
            if rest.startswith("(") and rest.endswith(")"):
                lab["note"] = rest[1:-1]
                rest = ""
            blocks.append(lab)
            label = role
            if rest:
                if role == "say":
                    blocks.append({"type": "say", "phrases": parse_say(rest)})
                else:
                    blocks.append({"type": "p", "text": convert_inline(rest[0].upper() + rest[1:])})
            continue
        only, txt = split_only(text)
        if txt.startswith("**Intro:**"):
            raise SourceError(f"**Intro:** belongs right under a ## topic, not in '{where}'")
        blocks.append({"type": "p", "text": convert_inline(txt), **({"only": only} if only else {})})
        label = None
    page: dict = {"blocks": blocks}
    if more:
        page["more"] = more
    return page


def strip_flags(h: str) -> tuple[str, bool, bool]:
    preview = "*(Preview)*" in h
    plugin = "*(plugin)*" in h
    return h.replace("*(Preview)*", "").replace("*(plugin)*", "").strip(), preview, plugin


# ── build ─────────────────────────────────────────────────────────────────


def build(source: Path = SOURCE) -> dict:
    md = strip_comments(source.read_text(encoding="utf-8"))
    secs = apply_snippets(sections(md))
    if not secs or secs[0][:2] != (2, "Start here"):
        raise SourceError("the source must open with ## Start here")
    topics_by_title = {t["title"]: t for t in TOPICS}
    start = None
    topics: list[dict] = []
    pieces: list[dict] = []
    seen_titles: set[str] = set()
    current: dict | None = None
    for lvl, heading, body in secs:
        if lvl == 2 and heading == "Start here":
            start = parse_body(body, heading)
            current = None
            continue
        if lvl == 2:
            meta = topics_by_title.get(heading)
            if meta is None:
                raise SourceError(f"## {heading} isn't a topic in TOPICS")
            intro = None
            rest = list(body)
            paras = blocks_of(rest)
            if paras and paras[0][0].startswith("**Intro:**"):
                intro = convert_inline(" ".join(ln.strip() for ln in paras[0]).replace("**Intro:**", ""))
                rest = rest[rest.index(paras[0][-1]) + 1:]
            if any(ln.strip() for ln in rest):
                raise SourceError(f"## {heading} holds text outside its pieces")
            current = {"id": meta["id"], "title": heading, "subtitle": meta["subtitle"],
                       "keywords": meta["keywords"], "pieces": []}
            if meta.get("centre"):
                current["centre"] = True
            if intro:
                current["intro"] = intro
            topics.append(current)
            continue
        if current is None:
            raise SourceError(f"### {heading} sits under no topic")
        title, preview, plugin = strip_flags(heading)
        if title not in PIECES:
            raise SourceError(f"### {title} isn't a piece in PIECES")
        seen_titles.add(title)
        pid, sub, kw, extra = PIECES[title]
        piece: dict = {"id": pid, "topic": current["id"], "title": title, "subtitle": sub, "keywords": kw}
        if preview:
            piece["preview"] = True
        if plugin != bool(extra.get("plugin")):
            raise SourceError(f"### {title}: *(plugin)* in the heading and PIECES' plugin must agree")
        for k in ("plugin", "live", "alias_of"):
            if extra.get(k):
                piece[k] = extra[k]
        piece.update(parse_body(body, title))
        current["pieces"].append(pid)
        pieces.append(piece)
    if start is None:
        raise SourceError("no ## Start here")
    missing = sorted(set(PIECES) - seen_titles)
    if missing:
        raise SourceError(f"PIECES with no ### in the source: {missing}")
    if [t["id"] for t in topics] != [t["id"] for t in TOPICS]:
        raise SourceError("the source's topics must follow TOPICS, in order")

    # The checklist: the source's numbered list under "Getting set up".
    cl = [b for b in start["blocks"] if b["type"] == "checklist"]
    if len(cl) != 1 or len(cl[0]["items"]) != len(CHECKLIST):
        raise SourceError(f"Start here needs one checklist of {len(CHECKLIST)} lines")
    checklist = []
    for meta, text in zip(CHECKLIST, cl[0]["items"]):
        entry = {**meta, "text": text}
        if "app_text" in meta:
            entry["app_text"] = convert_inline(meta["app_text"])
        checklist.append(entry)
    cl[0].pop("items")

    doc = {
        "schema": SCHEMA,
        "about": ("Domovoi's user manual, for the dashboard (#manual) and the Android app. "
                  "Generated by scripts/manual/build_manual.py from scripts/manual/manual.md: "
                  "edit those, not this file."),
        "notes": {
            "inline": ("Text is a small Markdown subset: **bold** (on-screen labels), *italic*, `code`, "
                       "and [text](target) links. A target is route:<id> (routes below), node:<id> "
                       "(another piece of the manual) or doc:<repo path>#<anchor> (a GitHub doc, "
                       "doc_base with the ref for the server's version). {admin}, {preview} and "
                       "{plugin} are pills; {wake} is the wake word (wake_word below)."),
            "blocks": ("A page's blocks, in order. p {text, only?}; list {ordered, items:[{text, only?}]}; "
                       "label {role: say|how|wrong|also|ways|checklist, text, admin?, note?}; "
                       "say {phrases:[{text, handler, path, needs?, note?, then?, try?, plugin?, pending?}]} "
                       "(needs: a GET /api/capabilities feature; the phrase is left out while that "
                       "feature is false, e.g. ai for what the language models answer); "
                       "table {header, rows}; live {slot}; checklist (Start here only). "
                       "only: app = shown only in the app, dashboard = only on the dashboard."),
            "map": ("The centre (Domovoi) opens the centre topic (How it works); the other topics "
                    "ring it. A topic's pieces ring the topic; a piece opens its page. alias_of: "
                    "the piece opens that piece's page. plugin: shown only while that plugin is "
                    "installed and enabled."),
            "live": ("Filled from the server when the page shows: things-to-say = GET "
                     "/api/capabilities/manual (each handler's display.label, example_phrases, "
                     "requires_network, origin; where it lists no phrases, the manual's own phrases "
                     "for that handler; a handler in handler_needs_feature shows only while GET "
                     "/api/capabilities features names that feature on); rooms = GET /api/satellites; plugins = GET /api/plugins; "
                     "wake-word = the satellites' wake_word / wake_phrase. Nothing extra shows when "
                     "it can't be read."),
            "checklist": ("done_when: admin_claimed (GET /api/auth/status setup_complete), "
                          "internet_answered (GET /api/config internet_access not ''), rooms "
                          "(GET /api/satellites not empty), room_heard (a room has a session: GET "
                          "/api/satellites/<room>/sessions), people (GET /api/people not empty), "
                          "music (GET /api/music/library/stats total_tracks > 0), phone_app (an "
                          "android device in GET /api/devices, admin only), this_phone_paired (the "
                          "app, paired). A read that fails leaves the line unticked, never ticked."),
            "doc_ref": ("More-detail links open the docs of the version the server runs: the tag "
                        "v<label> when the version label is a release exactly (1.0.0, 1.0.0-rc2), "
                        "else main. The label is GET /api/config/version's version."),
            "phrases": ("handler/path: what the router does with the phrase (fast = a fast path; "
                        "llm = the tool model; qa = no handler). pending: being built elsewhere. "
                        "domovoi/tests/test_manual_content.py routes the fast ones."),
        },
        "doc_base": "https://github.com/coders-farm-official/domovoi/blob/{ref}/",
        "labels": LABELS,
        "wake_word": WAKE_WORD,
        "handler_needs_feature": HANDLER_NEEDS_FEATURE,
        "routes": ROUTES,
        "centre": {"id": "domovoi", "title": "Domovoi", "subtitle": "your home", "opens": "how-it-works"},
        "start": {"title": "Start here", **start, "checklist": checklist},
        "topics": topics,
        "pieces": pieces,
    }
    lint(doc)
    return doc


def lint(doc: dict) -> None:
    """What the source can get wrong that the renderers can't recover from."""
    blob = json.dumps(doc, ensure_ascii=False)
    ids = {p["id"] for p in doc["pieces"]}
    bad_routes = sorted({r for r in re.findall(r"\]\(route:([a-z0-9/-]+)\)", blob) if r not in ROUTES})
    if bad_routes:
        raise SourceError(f"links to routes not in ROUTES: {bad_routes}")
    bad_nodes = sorted({r for r in re.findall(r"\]\(node:([a-z0-9-]+)\)", blob) if r not in ids})
    if bad_nodes:
        raise SourceError(f"links to pieces that don't exist: {bad_nodes}")
    for p in doc["pieces"]:
        if p.get("alias_of") and p["alias_of"] not in ids:
            raise SourceError(f"{p['id']} is an alias of a missing piece")
    for t in doc["topics"]:
        if len(t["pieces"]) > 7:
            raise SourceError(f"topic {t['id']} has {len(t['pieces'])} pieces; 7 at most")
    # Every "Settings → X" is a link (the owner's rule, 2026-10-17).
    unlinked = re.sub(r"\[[^\]]*\]\([^)]*\)", "", blob)
    if "Settings →" in unlinked:
        i = unlinked.index("Settings →")
        raise SourceError(f"'Settings →' outside a link: …{unlinked[max(0, i - 60):i + 40]}…")
    # Nothing the inline renderers would show as raw markup.
    for text in _texts(doc):
        rest = re.sub(r"\[[^\]]+\]\((?:route|node|doc):[^)]+\)", "", text)
        rest = re.sub(r"\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\{(?:admin|preview|plugin|wake)\}", "", rest)
        if re.search(r"[*`\[\]{}]|\]\(", rest):
            raise SourceError(f"markup the renderers don't know: {text!r}")
    used = set(re.findall(r"\]\(route:([a-z0-9/-]+)\)", blob))
    unused = sorted(set(ROUTES) - used)
    if unused:
        raise SourceError(f"ROUTES nothing links to: {unused}")
    used_phrases = {p["text"] for p in _phrases(doc)}
    stale = sorted(set(PHRASES) - used_phrases)
    if stale:
        raise SourceError(f"PHRASES the source no longer says: {stale}")


def _texts(doc: dict):
    pages = [doc["start"], *doc["pieces"]]
    for c in doc["start"]["checklist"]:
        yield c["text"]
        if "app_text" in c:
            yield c["app_text"]
    for t in doc["topics"]:
        if "intro" in t:
            yield t["intro"]
    for pg in pages:
        for b in pg["blocks"]:
            if b["type"] == "p":
                yield b["text"]
            elif b["type"] == "list":
                for it in b["items"]:
                    yield it["text"]
            elif b["type"] == "table":
                yield from b["header"]
                for r in b["rows"]:
                    yield from r
            elif b["type"] == "say":
                for p in b["phrases"]:
                    if "note" in p:
                        yield p["note"]


def _phrases(doc: dict):
    for pg in [doc["start"], *doc["pieces"]]:
        for b in pg["blocks"]:
            if b["type"] == "say":
                yield from b["phrases"]


def render(doc: dict) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 when the content file is out of date")
    args = ap.parse_args(argv)
    try:
        data = render(build())
    except SourceError as e:
        print(f"manual source: {e}", file=sys.stderr)
        return 2
    if args.check:
        if not OUT.is_file() or OUT.read_bytes() != data:
            print(f"{OUT.relative_to(REPO)} is out of date: run python scripts/manual/build_manual.py",
                  file=sys.stderr)
            return 1
        print("up to date")
        return 0
    OUT.write_bytes(data)
    print(f"wrote {OUT.relative_to(REPO)} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
