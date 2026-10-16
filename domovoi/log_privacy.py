"""What the core's and the web's log lines may hold (S5, delta review
2026-10-16): no credential ever, and what people said only at DEBUG.

Spoken and typed words
----------------------

A log line that names what somebody said — a transcript, the search it
became, the reply it got — passes the words through :func:`said`. At the
default level the line shows only how many words there were
(``<5 words>``); with the process's log level at DEBUG
(``LOG_LEVEL=DEBUG``, what an operator sets to chase a misrecognition) it
shows them, as before. So the journal, kept on disk, holds the household's
words only while someone has chosen to log at DEBUG.

Credentials
-----------

Both processes log to stdout, which on a Linux server (and the Windows
install's distro) is the systemd journal, kept on disk
(``scripts/linux/units/journald-domovoi.conf``). Before this module the
web's uvicorn access log wrote every ``?device_token=<household token>``
URL the dashboard builds (``web/static/data.js`` puts the token in the URLs
of Files, Videos, chat images and the editors), and the core's uvicorn
wrote a WebSocket's ``?token=`` the same way ("WebSocket /ws/state?token=…"
[accepted]). Anyone who can read the journal (root, ``adm`` and
``systemd-journal`` members) then held the household token.

:func:`install` wraps the logging module's record factory, so EVERY record
either process creates passes through :func:`scrub` before any handler,
filter or formatter sees it, whatever logger it came from and however
uvicorn or a ``dictConfig`` set the handlers up. What it blanks, keeping
the name so a line still says what was sent:

* the value of a parameter whose name says it is a credential (``…token``,
  ``…secret``, ``…password``, ``…api_key``, ``sig``, ``signature``,
  ``setup_code``), written ``name=value`` in a query string or a log line:
  ``device_token=hh-…`` → ``device_token=***``;
* an ``Authorization: Bearer …`` value.

It looks at the message, the string arguments, URL objects (httpx's,
Starlette's) and exceptions among the arguments, and a traceback's text.
The shape of ``record.args`` is kept (uvicorn's access formatter unpacks it
into five fields); only strings are replaced by strings.

Pure string work, no I/O; a failure inside it leaves the record as it was
rather than losing the line.
"""

from __future__ import annotations

import logging
import re
from typing import Any

#: What a credential's parameter name looks like. Matched only where a
#: name starts (after ``?``, ``&``, ``;``, a space, a comma, a quote or a
#: bracket, or at the start), so ``returncode=`` or ``tokens_used=`` are
#: left alone.
_NAME = r"[A-Za-z0-9_\-]*(?:token|secret|password|passwd|api[_\-]?key)|sig|signature|setup_code"

_PARAM_RE = re.compile(
    r"(?P<lead>(?:^|[?&;\s,(\"'\[{]))(?P<name>" + _NAME + r")=(?P<value>(?!%)[^&\s\"'#,;)\]}]+)",
    re.IGNORECASE,
)
#: A format string that puts an argument right after a credential's name
#: (``"pairing_token=%s"``): such a record is formatted first, then scrubbed.
_PLACEHOLDER_RE = re.compile(
    r"(?:^|[?&;\s,(\"'\[{])(?:" + _NAME + r")=%", re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(?P<lead>\bbearer\s+)(?P<value>[A-Za-z0-9._~+/=\-]{6,})", re.IGNORECASE)

#: What a blanked value becomes.
MASK = "***"

#: The marker :func:`install` leaves on the factory it installs.
_MARK = "_domovoi_log_scrub"


def _may_hold(text: str) -> bool:
    lowered = text.lower()
    return "=" in text or "bearer" in lowered


def scrub(text: str) -> str:
    """``text`` with every credential value blanked (see the module
    docstring). Unchanged text is returned as the same object."""
    if not text or not _may_hold(text):
        return text
    out = _PARAM_RE.sub(lambda m: f"{m.group('lead')}{m.group('name')}={MASK}", text)
    if "earer" in out:
        out = _BEARER_RE.sub(lambda m: f"{m.group('lead')}{MASK}", out)
    return out


def _scrub_arg(arg: Any) -> Any:
    if isinstance(arg, str):
        return scrub(arg)
    if isinstance(arg, (int, float, bytes, bool)) or arg is None:
        return arg
    # httpx.URL, starlette.datastructures.URL, yarl.URL: formatted with
    # %s into the line, so their text is what has to be clean.
    if type(arg).__name__ == "URL" or isinstance(arg, BaseException):
        try:
            text = str(arg)
        except Exception:
            return arg
        clean = scrub(text)
        return arg if clean == text else clean
    return arg


def scrub_record(record: logging.LogRecord) -> None:
    """Blank credentials in ``record`` in place (message, arguments and
    traceback text). Never raises."""
    try:
        if isinstance(record.msg, str) and record.args and _PLACEHOLDER_RE.search(record.msg):
            # The credential is an argument: blank it in the formatted line.
            record.msg, record.args = scrub(record.getMessage()), ()
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        args = record.args
        if isinstance(args, tuple) and args:
            clean = tuple(_scrub_arg(a) for a in args)
            if any(c is not a for c, a in zip(clean, args)):
                record.args = clean
        elif isinstance(args, dict) and args:
            clean_map = {k: _scrub_arg(v) for k, v in args.items()}
            if any(clean_map[k] is not v for k, v in args.items()):
                record.args = clean_map
        if record.exc_info and record.exc_info[0] is not None and not record.exc_text:
            text = logging.Formatter().formatException(record.exc_info)
            clean_text = scrub(text)
            if clean_text != text:
                # Formatters use exc_text when it is set, so the traceback
                # they print is the scrubbed one.
                record.exc_text = clean_text
    except Exception:  # noqa: BLE001 — never lose the line over this
        pass


def install() -> None:
    """Wrap the process's log record factory with :func:`scrub_record`.
    Idempotent: importing both the core's and the web's main module (the
    web imports parts of the core) installs it once."""
    current = logging.getLogRecordFactory()
    if getattr(current, _MARK, False):
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = current(*args, **kwargs)
        scrub_record(record)
        return record

    setattr(factory, _MARK, True)
    logging.setLogRecordFactory(factory)


# ─── Spoken and typed words ───────────────────────────────────────────────


class _Said:
    """A person's words in a log line's arguments: their ``repr`` at DEBUG,
    only their word count otherwise. Judged when the line is formatted
    (lazily, as %-style arguments are), against the root logger, whose
    level is the process's ``LOG_LEVEL``."""

    __slots__ = ("_text",)

    def __init__(self, text: Any) -> None:
        self._text = "" if text is None else str(text)

    def _shown(self) -> str:
        if logging.getLogger().isEnabledFor(logging.DEBUG):
            return repr(self._text)
        words = len(self._text.split())
        return f"<{words} word{'' if words == 1 else 's'}>"

    def __repr__(self) -> str:
        return self._shown()

    __str__ = __repr__


def said(text: Any) -> _Said:
    """Wrap what somebody said (or typed, or was answered) for a log line:
    ``log.info("declined %r", said(transcript))``. See the module
    docstring."""
    return _Said(text)
