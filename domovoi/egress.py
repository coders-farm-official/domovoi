"""The ONE egress choke point: may this box use the internet at all?

The household answers one question, ``INTERNET_ACCESS`` (Settings →
Internet, the Windows installer, ``env_bootstrap --internet``):

* ``always``    — yes, it's on our home internet;
* ``sometimes`` — the connection comes and goes, or it's slow or metered;
* ``never``     — no; keep everything in the house;
* unset (``""``) — nobody has answered: exactly today's behaviour.

``always`` and ``sometimes`` only change DEFAULTS (``domovoi/config.py``
``PROFILE_DEFAULTS``). ``never`` is also a gate, and this module is it:

* :func:`check_destination` / :func:`require_destination` — a URL whose
  host is not on this network is refused under ``never``. "Not on this
  network" is decided from the host AS WRITTEN, with no DNS at all (a
  lookup is egress too): loopback, private, link-local, CGNAT and ULA
  literals, ``localhost``, single-label names and the LAN suffixes below
  stay reachable — satellites, the router, Ollama, Postgres, MPD, a LAN
  feed mirror. "never" means no internet, not no network.
* :func:`require_internet` — egress that has no URL (``edge_tts``,
  AcoustID, Shazam, ``git fetch``, ``pip``, ``docker pull``) and LOCAL
  services that proxy to the internet (SearXNG, an Ollama registry pull).
* :func:`async_client` / :func:`sync_client` and the request hooks — an
  ``httpx`` client that refuses non-local URLs, redirect hops included.
* :func:`http_exception` — the one refusal shape for HTTP routes: 409,
  ``detail`` = :data:`TURNED_OFF_REASON`, header
  ``X-Domovoi-Refusal: internet-off``.

:mod:`domovoi.net_safety` consults this module, so every caller of the
outbound-URL check (news and podcast feeds, ICY, the radio stream proxy,
the Ollama registry check, plugins through ``sdk.net_safety`` /
``webkit.net_safety``) refuses under ``never`` with no code of its own.
The SDK's ``HttpFactory`` and the web plugin host's ``http()`` install
:func:`async_request_hook`, so plugin clients refuse too.

Where the answer comes from — :func:`policy`, in BOTH processes: the
process environment first (``INTERNET_ACCESS``, any case, exactly as
pydantic-settings reads it), then ``domovoi/.env``. The web process has
no live copy of the core's settings, so a shared read of ``.env`` (cached
on its mtime) is the one source both agree on right after a dashboard
save. Nothing else parses ``INTERNET_ACCESS``.

Importable by the web process and by plugin web modules (re-exported as
``webkit.egress`` and ``sdk.egress``): stdlib + :mod:`domovoi.net_safety`
+ :mod:`domovoi.config_env_writer` at import time; ``httpx``, ``fastapi``
and ``dotenv`` only inside the functions that need them. It must never
import :mod:`domovoi.config` (config imports this module).

CLI::

    python -m domovoi.egress --print-policy    # the answer, or an empty line
    python -m domovoi.egress --check URL       # "allowed" or the reason
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Mapping
from urllib.parse import urlsplit

from domovoi import config_env_writer, net_safety

if TYPE_CHECKING:  # pragma: no cover
    import fastapi
    import httpx

log = logging.getLogger(__name__)

POLICIES: tuple[str, ...] = ("always", "sometimes", "never")

# The one sentence every refusal carries — HTTP detail, exception text,
# dashboard toast (web/static/components.jsx INTERNET_OFF_MESSAGE is the
# same string; a test keeps them equal).
TURNED_OFF_REASON: str = "internet access is turned off for this box (Settings → Internet)"

# Name suffixes that mean "a machine on this network". Keep in step with
# domovoi/transport_guard.LOCAL_SUFFIXES (a test asserts the overlap).
LOCAL_NAME_SUFFIXES: tuple[str, ...] = (
    ".local", ".localhost", ".lan", ".home.arpa", ".internal", ".localdomain",
)

ENV_KEY = "INTERNET_ACCESS"
REFUSAL_HEADER = "X-Domovoi-Refusal"
REFUSAL_VALUE = "internet-off"

# Spellings a person plausibly means. Matched case- and
# whitespace-insensitively; anything else reads as unanswered (D4).
_ALIASES: dict[str, str] = {
    **{k: "always" for k in ("always", "yes", "true", "on", "online", "enabled")},
    **{k: "sometimes" for k in ("sometimes", "limited", "metered", "intermittent")},
    **{k: "never" for k in (
        "never", "no", "false", "off", "offline", "none", "disabled", "dark",
    )},
    **{k: "" for k in ("", "unset", "ask")},
}

_warned_unknown: set[str] = set()
_refused_whats: set[str] = set()
# override_policy stack: innermost last; None = "no override at this level".
_overrides: list[str | None] = []
# policy() cache: (key, value). The key is what can change the answer.
_cache: tuple[tuple[Any, ...], str] | None = None


class InternetTurnedOff(net_safety.UnsafeOutboundURL):
    """Raised when the answer is ``never`` and something tries to reach
    the internet. Subclasses :class:`net_safety.UnsafeOutboundURL` (a
    ``ValueError``), so every existing ``except UnsafeOutboundURL /
    OutboundFetchError / ValueError`` still refuses rather than crashing.
    The price is the durable-failure rule: background work must never
    record this as "that URL is bad" — it is a setting, not a verdict."""

    def __init__(self, what: str) -> None:
        super().__init__(what, TURNED_OFF_REASON)
        self.what = what


# ─── The answer ───────────────────────────────────────────────────────────


def normalize_policy(value: object) -> str:
    """``""`` / ``"always"`` / ``"sometimes"`` / ``"never"`` for a raw
    value. ``None`` and blank are unanswered; a bool reads as its word.
    An unknown value is unanswered too, with ONE warning per distinct
    value (neither always nor never is a safe guess for a typo)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "always" if value else "never"
    text = str(value).strip().lower()
    if text in _ALIASES:
        return _ALIASES[text]
    if text not in _warned_unknown:
        _warned_unknown.add(text)
        log.warning(
            "INTERNET_ACCESS=%r is not one of always, sometimes, never — "
            "treating it as not answered (today's behaviour)", value,
        )
    return ""


def _environ_value(environ: Mapping[str, str]) -> str | None:
    """The raw ``INTERNET_ACCESS`` from ``environ``, matched
    case-insensitively (pydantic-settings' default), or None when absent.
    An empty value counts as present: it shadows ``.env``, as it does for
    pydantic-settings."""
    value = environ.get(ENV_KEY)
    if value is not None:
        return value
    for key, val in environ.items():
        if key.upper() == ENV_KEY:
            return val
    return None


def _env_file_value(path: Path) -> str | None:
    """The raw value of an uncommented ``INTERNET_ACCESS=`` line in the
    ``.env`` at ``path``, or None (no file, no line, unreadable)."""
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


def read_policy(
    env_path: Path | None = None, environ: Mapping[str, str] | None = None
) -> str:
    """The answer as the next boot will read it, uncached: ``environ``
    (default ``os.environ``) first, then the ``.env`` file (default
    ``domovoi/.env``; commented lines don't count), then ``""``."""
    env = os.environ if environ is None else environ
    raw = _environ_value(env)
    if raw is not None:
        return normalize_policy(raw)
    path = Path(env_path) if env_path is not None else config_env_writer._ENV_FILE
    return normalize_policy(_env_file_value(path))


def policy() -> str:
    """The live answer. An :func:`override_policy` wins (tests, harness);
    otherwise :func:`read_policy`, cached on the environment value and the
    ``.env`` file's path, mtime and size — one ``os.stat`` per call, so
    every egress check can afford it, and a dashboard save (which rewrites
    ``.env``) is seen by both processes on their next check."""
    global _cache
    for value in reversed(_overrides):
        if value is not None:
            return value
    raw = _environ_value(os.environ)
    path = config_env_writer._ENV_FILE
    stamp: tuple[int, int] | None
    try:
        st = os.stat(path)
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    key = (raw, str(path), stamp)
    cached = _cache
    if cached is not None and cached[0] == key:
        return cached[1]
    value = normalize_policy(raw) if raw is not None else normalize_policy(_env_file_value(Path(path)))
    _cache = (key, value)
    return value


def internet_allowed() -> bool:
    """False only under ``never``. Unanswered, always and sometimes all
    allow (sometimes only changes defaults)."""
    return policy() != "never"


def internet_turned_off() -> bool:
    return policy() == "never"


@contextlib.contextmanager
def override_policy(value: str | None) -> Iterator[None]:
    """Pin the answer for a block (tests and the egress harness).
    Nestable; the innermost wins. ``None`` adds no override at this
    level. The value goes through :func:`normalize_policy`."""
    _overrides.append(None if value is None else normalize_policy(value))
    try:
        yield
    finally:
        _overrides.pop()


# ─── Where a URL goes ─────────────────────────────────────────────────────


def is_local_host(host: str) -> bool:
    """Whether ``host`` (as written in a URL) names a machine on this
    network. NEVER resolves a name.

    * An IP literal, in any spelling :func:`net_safety.parse_ip_literal`
      accepts (``127.1``, ``0x7f000001``): local when it is not a public
      address — loopback, RFC 1918, link-local, CGNAT, ULA…
    * A name: ``localhost``, ``host.docker.internal``, anything ending in
      :data:`LOCAL_NAME_SUFFIXES`, and any single-label name (``nas``).
    * Anything else — a dotted public name — is not local."""
    if not isinstance(host, str):
        return False
    h = host.strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h.endswith("."):
        h = h[:-1]
    if not h:
        return False
    literal = net_safety.parse_ip_literal(h)
    if literal is not None:
        return not net_safety.is_public_address(literal)
    if h in ("localhost", "host.docker.internal"):
        return True
    if any(h.endswith(suffix) for suffix in LOCAL_NAME_SUFFIXES):
        return True
    return "." not in h


def _log_refusal(what: str) -> None:
    """INFO the first time each ``what`` is refused in this process, then
    DEBUG — a polling worker must not flood the journal."""
    label = str(what)[:200]
    if label not in _refused_whats:
        _refused_whats.add(label)
        log.info("internet off: refused %s", label)
    else:
        log.debug("internet off: refused %s", label)


def check_destination(url: str) -> str | None:
    """None when ``url`` may be fetched as far as the internet answer is
    concerned; :data:`TURNED_OFF_REASON` when the answer is ``never`` and
    the URL's host is not :func:`is_local_host`. A URL with no parseable
    host is not this module's business (None) — ``net_safety`` or httpx
    refuses it on its own terms. Never resolves a name."""
    if not internet_turned_off():
        return None
    try:
        host = urlsplit(str(url).strip()).hostname
    except ValueError:
        return None
    if not host:
        return None
    if is_local_host(host):
        return None
    return TURNED_OFF_REASON


def require_destination(url: str) -> None:
    """Raise :class:`InternetTurnedOff` when :func:`check_destination`
    refuses ``url``."""
    if check_destination(url) is not None:
        try:
            host = urlsplit(str(url).strip()).hostname or str(url)
        except ValueError:
            host = str(url)
        _log_refusal(host)
        raise InternetTurnedOff(str(url))


def require_internet(what: str) -> None:
    """Raise :class:`InternetTurnedOff` under ``never``. For egress with
    no URL and for local services that proxy to the internet. ``what`` is
    a short human label: "web search", "Microsoft Edge voice"."""
    if internet_turned_off():
        _log_refusal(what)
        raise InternetTurnedOff(what)


# ─── httpx ────────────────────────────────────────────────────────────────


async def async_request_hook(request: Any) -> None:
    """httpx request event hook (async client): runs for every request a
    client sends, redirect hops included."""
    require_destination(str(request.url))


def sync_request_hook(request: Any) -> None:
    """httpx request event hook (sync client)."""
    require_destination(str(request.url))


def _with_hook(kwargs: dict[str, Any], hook: Any) -> dict[str, Any]:
    hooks = dict(kwargs.pop("event_hooks", None) or {})
    request_hooks = [h for h in list(hooks.get("request") or []) if h is not hook]
    hooks["request"] = [hook, *request_hooks]
    kwargs["event_hooks"] = hooks
    return kwargs


def async_client(**kwargs: Any) -> "httpx.AsyncClient":
    """An ``httpx.AsyncClient`` that refuses non-local URLs under
    ``never``: :func:`async_request_hook` runs FIRST; the caller's own
    request hooks are kept after it."""
    import httpx

    return httpx.AsyncClient(**_with_hook(kwargs, async_request_hook))


def sync_client(**kwargs: Any) -> "httpx.Client":
    """Blocking twin of :func:`async_client`."""
    import httpx

    return httpx.Client(**_with_hook(kwargs, sync_request_hook))


# ─── How a refusal looks ──────────────────────────────────────────────────


def http_exception(what: str | None = None) -> "fastapi.HTTPException":
    """The refusal for an HTTP route that would reach the internet: 409,
    ``detail`` = :data:`TURNED_OFF_REASON`, ``X-Domovoi-Refusal:
    internet-off``. The caller raises it."""
    from fastapi import HTTPException

    if what:
        _log_refusal(what)
    return HTTPException(
        status_code=409,
        detail=TURNED_OFF_REASON,
        headers={REFUSAL_HEADER: REFUSAL_VALUE},
    )


def spoken_offline_phrase() -> str:
    """The start of a spoken "can't reach the internet" reply. The caller
    finishes the sentence ("…, so I can't check that.")."""
    if internet_turned_off():
        return "I'm set to stay off the internet"
    return "I don't have internet right now"


def apply_process_env() -> bool:
    """Under ``never``, set ``HF_HUB_OFFLINE=1`` for this process when it
    is absent or empty, so ``huggingface_hub`` (read at its import) never
    asks huggingface.co. A non-empty value set by hand wins, even "0".
    Returns True when it set the variable."""
    if not internet_turned_off():
        return False
    if os.environ.get("HF_HUB_OFFLINE"):
        return False
    os.environ["HF_HUB_OFFLINE"] = "1"
    log.info("internet off: HF_HUB_OFFLINE=1 for this process")
    return True


# ─── CLI ──────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m domovoi.egress",
        description="The internet-access answer (INTERNET_ACCESS) and its gate.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--print-policy", action="store_true",
        help="print the answer (always, sometimes, never; an empty line when unanswered)",
    )
    group.add_argument(
        "--check", metavar="URL",
        help="print 'allowed' or the reason the URL is refused; exit 1 when refused",
    )
    args = parser.parse_args(argv)
    if args.print_policy:
        print(policy())
        return 0
    reason = check_destination(args.check)
    if reason is None:
        print("allowed")
        return 0
    print(reason)
    return 1


__all__ = [
    "ENV_KEY",
    "InternetTurnedOff",
    "LOCAL_NAME_SUFFIXES",
    "POLICIES",
    "REFUSAL_HEADER",
    "REFUSAL_VALUE",
    "TURNED_OFF_REASON",
    "apply_process_env",
    "async_client",
    "async_request_hook",
    "check_destination",
    "http_exception",
    "internet_allowed",
    "internet_turned_off",
    "is_local_host",
    "normalize_policy",
    "override_policy",
    "policy",
    "read_policy",
    "require_destination",
    "require_internet",
    "spoken_offline_phrase",
    "sync_client",
    "sync_request_hook",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
