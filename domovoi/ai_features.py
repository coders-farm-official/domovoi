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
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import urlsplit

from domovoi import config_env_writer

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
OFF_LOOKUP_LINE = "I can't look that up with AI turned off."

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


def http_exception(what: str = "this"):
    """The one refusal shape for HTTP routes while AI is off: 409, detail
    :data:`TURNED_OFF_REASON`, header ``X-Domovoi-Refusal: ai-off``."""
    from fastapi import HTTPException

    return HTTPException(
        status_code=409,
        detail=TURNED_OFF_REASON,
        headers={REFUSAL_HEADER: REFUSAL_VALUE},
    )


# ─── Plugins' requests to Ollama ──────────────────────────────────────────

# Ollama endpoints that run (or fetch) a model. /api/tags, /api/ps and
# /api/version only describe what is there and stay open.
_MODEL_PATHS = (
    "/api/generate", "/api/chat", "/api/embed", "/api/embeddings",
    "/api/pull", "/api/create",
)
OLLAMA_DEFAULT_PORT = 11434


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


def is_model_request(url: str) -> bool:
    """Whether ``url`` asks Ollama to run or fetch a model: a model path on
    the default Ollama port (any host), or on the configured Ollama."""
    try:
        parts = urlsplit(str(url))
        host = (parts.hostname or "").lower()
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return False
    path = parts.path.rstrip("/") or "/"
    if not any(path == p or path.startswith(p + "/") for p in _MODEL_PATHS):
        return False
    if port == OLLAMA_DEFAULT_PORT:
        return True
    return (host, port) in _ollama_endpoints()


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
