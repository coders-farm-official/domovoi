"""The household's "AI features" switch: may Domovoi use its chat models?

One question, ``AI_FEATURES`` (Settings → Models, both installers'
"Include AI chat and questions?", ``provision.sh --no-ai-models``):

* on (the default, and every install from before the switch existed) —
  today's behaviour: the language models answer questions, route the
  phrasings no fixed command knows, summarise the news briefing, read
  pictures in chat, and the dashboard and app show Chat;
* off — nothing in Domovoi asks a language model. Voice commands still
  run on their fixed phrases (timers, music, the clock, intercom, …);
  speech recognition (Whisper), the voices (Piper), the wake word and
  speaker recognition stay, because voice control needs them. A question
  gets :data:`OFF_LINE`. Chat is hidden. A plugin's request to Ollama
  through the SDK's HTTP clients is refused with :class:`AITurnedOff`.

"AI chat is optional" is the honest claim: speech recognition is local
machine learning too, and it never leaves the box.

Where the answer comes from — :func:`enabled`, in BOTH processes, the way
:mod:`domovoi.egress` reads the internet answer: an :func:`override` (tests)
first, then the process environment (``AI_FEATURES``), then
``domovoi/.env``, then on. The web process has no live copy of the core's
settings, so ``.env`` (cached on its mtime and size) is what both agree on
right after a dashboard save. ``settings.ai_features`` is the same value as
the core read it at boot, kept in step by the Settings save.

Stdlib + :mod:`domovoi.config_env_writer` at import time (``dotenv`` only
inside a function), so the web process, plugins and ``domovoi.config``
can all import it.
"""

from __future__ import annotations

import contextlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import urlsplit

from domovoi import config_env_writer, off_answers

ENV_KEY = "AI_FEATURES"

# What the voice says, with AI off, to anything no command matched: a
# question, a joke request, a paraphrase of a command. Not an outage line:
# nothing is broken, the household chose this.
OFF_LINE = (
    "I can't answer that one — I'm set up for home commands. "
    "Say 'what can you do' to hear them."
)
# The weather, scores, prices, the news of the day, "are you sure?": things
# only a web answer summarised by the model could give.
# Says how to turn AI back on (owner, Q2: domovoi/off_answers.py).
_OFF_LOOKUP_LEAD = "I can't look that up with AI turned off."


def off_lookup_line(*, admin: bool = False) -> str:
    """:data:`OFF_LOOKUP_LINE`, with the steps for a proven admin
    (``Context.proven_admin``; off_answers.turn_on_line)."""
    return off_answers.with_turn_on(_OFF_LOOKUP_LEAD, off_answers.AI, admin=admin)


#: The member wording, which every turn but a proven admin's hears.
OFF_LOOKUP_LINE = off_lookup_line()

# The one sentence an API refusal carries (HTTP detail, the SDK's
# exception text, the dashboard's toast).
TURNED_OFF_REASON = "AI features are turned off for this home (Settings → Models)"
REFUSAL_HEADER = "X-Domovoi-Refusal"
REFUSAL_VALUE = "ai-off"

# What turning the switch off locks out of a dashboard save: a value pinned
# in the process environment. The web process would keep reading the
# environment's value while the core read the saved one.
ANSWER_LOCKED = "set in the server's environment (AI_FEATURES); change it there"

# Download sizes of the chat models the installers pull, in GB, as the
# Ollama library lists them (the repo pins these by digest in
# scripts/linux/provision-manifest.conf; a test keeps the two in step).
# Settings → Models shows them before turning AI on; provision.sh and the
# Windows Setup page say the tier totals below.
MODEL_DOWNLOAD_GB: dict[str, float] = {
    "llama3.2:3b-instruct-q4_K_M": 2.0,
    "qwen3:4b-instruct-2507-q4_K_M": 2.5,
    "qwen3:8b-q4_K_M": 5.2,
    "qwen3-vl:2b-instruct-q4_K_M": 1.9,
}
# The two totals the installers' question shows: a smaller box (the
# "floor" tier) and one with 24 GB of memory or more ("standard").
TIER_DOWNLOAD_GB: dict[str, float] = {"floor": 6.4, "standard": 9.1}

_TRUE = frozenset({"1", "true", "yes", "on", "enabled", "y", "t"})
_FALSE = frozenset({"0", "false", "no", "off", "disabled", "n", "f", "never"})

_overrides: list[bool | None] = []
_cache: tuple[tuple[Any, ...], bool] | None = None


class AITurnedOff(RuntimeError):
    """Raised when AI features are off and something asks a language
    model anyway (a plugin's request to Ollama through ``sdk.http``)."""

    def __init__(self, what: str = "a language model") -> None:
        super().__init__(f"{what}: {TURNED_OFF_REASON}")
        self.what = what


def parse(value: object) -> bool:
    """A raw value as on/off. Blank, missing or unknown reads as ON: the
    switch only turns AI off when someone clearly said so."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _FALSE:
        return False
    return True


def _environ_value(environ: Mapping[str, str]) -> str | None:
    value = environ.get(ENV_KEY)
    if value is not None:
        return value
    for key, val in environ.items():
        if key.upper() == ENV_KEY:
            return val
    return None


def _env_file_value(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        from dotenv import dotenv_values

        for key, val in dotenv_values(path).items():
            if key.upper() == ENV_KEY and val is not None:
                return val
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return None


def read(env_path: Path | None = None, environ: Mapping[str, str] | None = None) -> bool:
    """The answer as the next boot will read it, uncached."""
    env = os.environ if environ is None else environ
    raw = _environ_value(env)
    if raw is not None:
        return parse(raw)
    path = Path(env_path) if env_path is not None else config_env_writer._ENV_FILE
    return parse(_env_file_value(path))


def enabled() -> bool:
    """Whether AI features are on, live. One ``os.stat`` per call."""
    global _cache
    for value in reversed(_overrides):
        if value is not None:
            return value
    raw = _environ_value(os.environ)
    path = config_env_writer._ENV_FILE
    try:
        st = os.stat(path)
        stamp: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    key = (raw, str(path), stamp)
    cached = _cache
    if cached is not None and cached[0] == key:
        return cached[1]
    value = parse(raw) if raw is not None else parse(_env_file_value(Path(path)))
    _cache = (key, value)
    return value


def turned_off() -> bool:
    return not enabled()


def locked() -> bool:
    """Pinned in the process environment, so not changeable from Settings."""
    return _environ_value(os.environ) is not None


@contextlib.contextmanager
def override(value: bool | None) -> Iterator[None]:
    """Pin the answer for a block (tests). Nestable; innermost wins."""
    _overrides.append(None if value is None else bool(value))
    try:
        yield
    finally:
        _overrides.pop()


def http_exception(what: str = "this", *, status_code: int = 409, detail: str | None = None):
    """The one refusal shape for HTTP routes while AI is off: 409, detail
    :data:`TURNED_OFF_REASON`, header ``X-Domovoi-Refusal: ai-off``.
    ``status_code`` and ``detail`` are for the plugin route gate
    (:data:`PLUGIN_ROUTE_REFUSAL_STATUS`)."""
    from fastapi import HTTPException

    return HTTPException(
        status_code=status_code,
        detail=TURNED_OFF_REASON if detail is None else detail,
        headers={REFUSAL_HEADER: REFUSAL_VALUE},
    )


# --- Plugins that make things with a model (owner, Q6) ---------------------
#
# A plugin whose feature IS a model (image generation runs a diffusion
# model on its own engine, never this box's Ollama) slips past the Ollama
# refusal below. It says so in its manifest, and core does the rest:
#
#   [ai]
#   does = "make pictures"        # finishes "AI is off on this house, so I can't ..."
#   routes = ["/generate"]        # web/core routes that make something
#
#   [[handlers]]
#   name = "..."
#   needs_ai = true               # every voice turn this handler takes
#
# While AI is off the router answers a ``needs_ai`` handler's turns with
# :func:`off_feature_line` (the handler's code never runs), and both
# plugin route gates (web ``/api/plugins/<slug>``, core
# ``/v1/plugins/<slug>``) refuse a non-GET request to a listed route with
# :data:`PLUGIN_ROUTE_REFUSAL_STATUS`, the same line and
# ``X-Domovoi-Refusal: ai-off``. Reading (GET) always works: what was
# made before stays.
#
# A plugin released before the ``[ai]`` table existed says nothing, so a
# capability it provides stands in for it (:data:`KNOWN_AI_CAPABILITIES`):
# what core knows that capability's routes and requests to be. A manifest
# with its own ``[ai]`` table is read instead.

AI_OFF_LEAD = "AI is off on this house"

#: A refused plugin route answers 423, not the core routes' 409: pages
#: released before this gate read their own 409 and 503 as something else
#: ("model not installed", "engine offline"), which would be untrue here.
#: The ``X-Domovoi-Refusal: ai-off`` header is what a page should read;
#: the dashboard shell shows the refusal's line itself (data.js).
PLUGIN_ROUTE_REFUSAL_STATUS = 423

#: A manifest's ``[ai] does`` is spoken, so it stays a short phrase.
AI_DOES_MAX = 60


@dataclass(frozen=True)
class PluginAI:
    """What a plugin's ``[ai]`` table says (or what core knows for it)."""

    #: Finishes "AI is off on this house, so I can't ..."; "" says "do that".
    does: str = ""
    #: Route paths under the plugin's prefix ("/generate",
    #: "/jobs/{job_id}/redo") whose non-GET requests make something.
    routes: tuple[str, ...] = ()
    #: Core-only, for a capability with no voice handler of its own: the
    #: requests that would be it ("make a picture of ..."), answered with
    #: the off line instead of the general AI-off answer.
    voice: re.Pattern[str] | None = None


KNOWN_AI_CAPABILITIES: dict[str, PluginAI] = {
    # The Image Generation plugin (capability "imagegen", 1.0.x): its page
    # starts a picture with POST /generate. It has no voice handler, so
    # core recognises the request itself.
    "imagegen": PluginAI(
        does="make pictures",
        routes=("/generate",),
        voice=re.compile(
            r"^(?:(?:can you |could you |please )?"
            r"(?:make|draw|create|generate|paint|render|design) "
            r"(?:me |us )?(?:a |an |some |the )?(?:\w+ )?"
            r"(?:picture|pictures|image|images|drawing|painting|illustration|photo|artwork)"
            r"|(?:can you |could you |please )?draw (?:me |us )?(?:a|an|some) "
            r"|(?:image|picture) generation)\b"
        ),
    ),
}


def off_feature_line(does: str | None = None, *, admin: bool = False) -> str:
    """What a turn hears when AI is off and it asked for something only a
    model makes: "AI is off on this house, so I can't make pictures." and
    how to turn it on (the steps when ``admin``: the turn's
    ``Context.proven_admin``). Never the plugin's name."""
    what = " ".join(str(does or "").split()) or "do that"
    return off_answers.with_turn_on(f"{AI_OFF_LEAD}, so I can't {what}.", off_answers.AI, admin=admin)


def plugin_ai_from_manifest(raw: Mapping[str, Any] | None) -> PluginAI | None:
    """A manifest's ``[ai]`` table (the raw TOML or the registry's JSONB),
    else the known capability it provides, else None. Raises
    :class:`ValueError` on a malformed table (the installer refuses it)."""
    raw = raw or {}
    table = raw.get("ai")
    if table is not None:
        if not isinstance(table, Mapping):
            raise ValueError("[ai] must be a table")
        does = table.get("does", "")
        if not isinstance(does, str) or len(does) > AI_DOES_MAX:
            raise ValueError(f"ai.does must be a phrase of at most {AI_DOES_MAX} characters")
        routes = table.get("routes", [])
        if not isinstance(routes, list) or not all(
            isinstance(r, str) and r.startswith("/") for r in routes
        ):
            raise ValueError('ai.routes must be a list of paths starting with "/"')
        return PluginAI(does=does.strip(), routes=tuple(r.rstrip("/") or "/" for r in routes))
    caps = raw.get("capabilities") or {}
    provides = caps.get("provides") if isinstance(caps, Mapping) else None
    for cap in provides or ():
        known = KNOWN_AI_CAPABILITIES.get(cap) if isinstance(cap, str) else None
        if known is not None:
            return known
    return None


def route_needs_ai(decl: PluginAI | None, method: str, route_path: str) -> bool:
    """Whether a request to ``route_path`` (the route's path under the
    plugin's prefix, as declared: "/generate", "/jobs/{job_id}/redo") is
    one AI-off refuses. Reads (GET, HEAD, OPTIONS) never are."""
    if decl is None or not decl.routes:
        return False
    if method.upper() in ("GET", "HEAD", "OPTIONS"):
        return False
    path = route_path.rstrip("/") or "/"
    return path in decl.routes


def plugin_route_refusal(decl: PluginAI | None, method: str, route_path: str):
    """The HTTPException a plugin route gate raises while AI is off for a
    route the plugin listed, or None when the request may run."""
    if not route_needs_ai(decl, method, route_path) or enabled():
        return None
    assert decl is not None
    return http_exception(
        status_code=PLUGIN_ROUTE_REFUSAL_STATUS, detail=off_feature_line(decl.does)
    )


def route_path_under(route_template: str | None, actual_path: str, prefix: str) -> str:
    """The path a plugin declares a route by: the matched route's template
    ("/api/plugins/imagegen/jobs/{job_id}") less the mount ``prefix``, else
    the request's own path less it."""
    path = route_template or actual_path
    if path.startswith(prefix):
        path = path[len(prefix):]
    return path or "/"


# The core process's plugin declarations, by slug: the plugin loader sets
# one as it loads a plugin and drops it on disable. The router reads
# ``does`` for a needs_ai handler's line; the core route gate ``routes``.
_PLUGIN_AI: dict[str, PluginAI] = {}


def set_plugin_ai(slug: str, decl: PluginAI | None) -> None:
    if decl is None:
        _PLUGIN_AI.pop(slug, None)
    else:
        _PLUGIN_AI[slug] = decl


def plugin_ai(slug: str | None) -> PluginAI | None:
    return _PLUGIN_AI.get(slug) if slug else None


# Requests only the model answers, asked while AI is off: a conversation,
# a story, a joke, a poem. They hear that AI is off and how to turn it on
# (owner Q2) instead of the general "set up for home commands" line, which
# stays for everything else nothing matched (case 6.2, 6.6).
_AI_CHAT_REQUEST_RE = re.compile(
    r"^(?:(?:can|could|may) (?:i|we) |(?:i|we) (?:want|would like|wanna) to |let'?s )"
    r"(?:chat|talk|have a (?:chat|conversation)|just talk)(?: with you| to you)?\b"
)
_AI_MAKE_REQUEST_RE = re.compile(
    r"^(?:(?:can|could|would|will) you |please )?"
    r"(?:tell|read|make up|write|give)(?: me| us)? (?:a |an |another |one )?"
    r"(?:(?:bedtime|short|funny|scary) )?(?P<what>story|stories|joke|jokes|poem|poems|riddle|riddles)\b"
)
_AI_MADE = {"story": "tell stories", "joke": "tell jokes", "poem": "write poems", "riddle": "tell riddles"}


def off_request_line(transcript: str, *, admin: bool = False) -> str | None:
    """The AI-off answer for a request only the model answers (a chat, a
    story, a joke, a poem), or None for anything else. Says AI is off, that
    home commands still work, and how to turn it on."""
    text = " ".join(str(transcript or "").lower().replace("?", " ").split())
    if _AI_CHAT_REQUEST_RE.search(text):
        return off_answers.with_turn_on(
            "AI chat is off on this house, but home commands like timers and music still work.",
            off_answers.AI, admin=admin,
        )
    m = _AI_MAKE_REQUEST_RE.search(text)
    if m:
        what = _AI_MADE[m.group("what").rstrip("s")]
        return off_answers.with_turn_on(f"{AI_OFF_LEAD}, so I can't {what}.", off_answers.AI, admin=admin)
    return None


def known_capability_request(transcript: str, provided: Any) -> PluginAI | None:
    """The known capability (:data:`KNOWN_AI_CAPABILITIES`) a turn asks
    for, among those ``provided`` (names with a live provider), or None.
    For the router's AI-off answer: "make a picture of a cat" with an
    image plugin installed hears the honest line, not the general one."""
    text = " ".join(str(transcript or "").lower().split())
    for cap, decl in KNOWN_AI_CAPABILITIES.items():
        if decl.voice is not None and cap in provided and decl.voice.search(text):
            return decl
    return None


# ─── Plugins' requests to Ollama ──────────────────────────────────────────

# Deny by host and port, not by path: while AI is off, every request to
# this box's Ollama is refused except the few that only describe what is
# there. A path list of what runs a model would miss Ollama's other ways in
# (its OpenAI-compatible /v1/chat/completions, /v1/completions,
# /v1/embeddings, whatever a later release adds) and spellings like
# //api/generate; an allow-list of read-only paths can't.
_OPEN_PATHS = frozenset({"/", "/api/tags", "/api/ps", "/api/version"})
OLLAMA_DEFAULT_PORT = 11434
_LOOPBACK_NAMES = frozenset({"localhost", "127.0.0.1", "::1", "ip6-localhost"})


def _is_loopback(host: str) -> bool:
    return host in _LOOPBACK_NAMES or host.startswith("127.") or host.endswith(".localhost")


def _ollama_endpoints() -> set[tuple[str, int]]:
    """(host, port) pairs that are this box's Ollama: the core's configured
    ``OLLAMA_URL`` (from the environment or ``.env``), and the default."""
    out: set[tuple[str, int]] = set()
    raw = _environ_value_named(os.environ, "OLLAMA_URL")
    if raw is None:
        raw = _env_file_named(config_env_writer._ENV_FILE, "OLLAMA_URL")
    for url in (raw, f"http://localhost:{OLLAMA_DEFAULT_PORT}"):
        if not url:
            continue
        try:
            parts = urlsplit(str(url))
            if parts.hostname:
                out.add((parts.hostname.lower(), parts.port or OLLAMA_DEFAULT_PORT))
        except ValueError:
            continue
    return out


def _environ_value_named(environ: Mapping[str, str], name: str) -> str | None:
    for key, val in environ.items():
        if key.upper() == name:
            return val
    return None


def _env_file_named(path: Path, name: str) -> str | None:
    try:
        if not path.is_file():
            return None
        from dotenv import dotenv_values

        for key, val in dotenv_values(path).items():
            if key.upper() == name and val is not None:
                return val
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return None


def is_ollama(url: str) -> bool:
    """Whether ``url`` is this box's Ollama: any host on Ollama's port
    (11434), or the core's configured ``OLLAMA_URL`` (host and port; a
    loopback name there matches any loopback name)."""
    try:
        parts = urlsplit(str(url))
        host = (parts.hostname or "").lower()
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return False
    if port == OLLAMA_DEFAULT_PORT:
        return True
    for ohost, oport in _ollama_endpoints():
        if oport == port and (ohost == host or (_is_loopback(ohost) and _is_loopback(host))):
            return True
    return False


def is_model_request(url: str) -> bool:
    """Whether ``url`` is a request to this box's Ollama that AI-off refuses:
    everything on it but the read-only listings (``/api/tags``, ``/api/ps``,
    ``/api/version``, ``/``). Denied by host and port, so the native API, the
    OpenAI-compatible ``/v1/...`` paths and any odd spelling of either are
    all covered."""
    if not is_ollama(url):
        return False
    try:
        path = urlsplit(str(url)).path or "/"
    except ValueError:
        return True
    while "//" in path:
        path = path.replace("//", "/")
    path = path.rstrip("/") or "/"
    return path not in _OPEN_PATHS


def require_for_url(url: str) -> None:
    """Raise :class:`AITurnedOff` for a model request while AI is off."""
    if turned_off() and is_model_request(url):
        raise AITurnedOff("a request to the language model")


_CONNECT_ERROR_CLASS: Any = None


def _connect_error_class() -> Any:
    """``AITurnedOffConnectError``, built on first use so this module never
    imports httpx at import time (as ``egress`` does for its own)."""
    global _CONNECT_ERROR_CLASS
    if _CONNECT_ERROR_CLASS is None:
        import httpx

        class AITurnedOffConnectError(AITurnedOff, httpx.ConnectError):
            """What an ``sdk.http`` client raises for a model request while AI
            features are off: an :class:`AITurnedOff` and an
            ``httpx.ConnectError``, so a plugin that only handles
            ``httpx.HTTPError`` treats it as "the model isn't reachable",
            which it is, instead of a 500."""

            def __init__(self, what: str, request: Any = None) -> None:
                AITurnedOff.__init__(self, what)
                self._request = request

        AITurnedOffConnectError.__module__ = __name__
        _CONNECT_ERROR_CLASS = AITurnedOffConnectError
    return _CONNECT_ERROR_CLASS


def __getattr__(name: str) -> Any:
    if name == "AITurnedOffConnectError":
        return _connect_error_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


async def async_request_hook(request: Any) -> None:
    """httpx request hook (``sdk.http`` clients, the web plugin host's
    ``http()``): refuses a model request to Ollama while AI is off, with
    ``AITurnedOffConnectError``."""
    url = str(request.url)
    if turned_off() and is_model_request(url):
        raise _connect_error_class()("a request to the language model", request=request)


def with_request_hook(kwargs: dict[str, Any]) -> dict[str, Any]:
    """``kwargs`` for an ``httpx.AsyncClient`` with
    :func:`async_request_hook` first among the caller's request hooks
    (``egress.async_client`` then puts the internet answer's before it)."""
    hooks = dict(kwargs.pop("event_hooks", None) or {})
    request_hooks = [h for h in list(hooks.get("request") or []) if h is not async_request_hook]
    hooks["request"] = [async_request_hook, *request_hooks]
    kwargs["event_hooks"] = hooks
    return kwargs
