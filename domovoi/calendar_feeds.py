"""Calendars the house reads from elsewhere: private ICS links and the
calendars household phones share (V055 ``calendar_sources``).

Read-only, both kinds (owner decisions 2026-11-14): Domovoi never writes
back to Google, Outlook, iCloud or a phone, and never signs in to any of
them. A link calendar is read from its address; a phone calendar arrives
as a snapshot the Android app sends over the LAN.

Links
-----

* :func:`normalize_feed_url` turns what a person pastes into the address
  fetched: ``webcal://`` / ``webcals://`` become ``https://``; anything that
  is not then ``http(s)`` is refused, and so is an address the outbound
  gate refuses on its face (a house-local address, unless the owner listed
  it in ``OUTBOUND_ALLOW_HOSTS``; :func:`domovoi.net_safety.check_outbound_url`
  with ``require_resolution=False``, the store-only form).
* :func:`refresh_link` reads one calendar through
  :func:`domovoi.net_safety.fetch_bytes` (every hop vetted and pinned,
  at most :data:`MAX_FEED_BYTES`), with a conditional GET (``If-None-Match``
  / ``If-Modified-Since`` from the last answer), parses it
  (:mod:`domovoi.calendar_ics`, a window of :data:`WINDOW_BACK` back and
  :data:`WINDOW_AHEAD` ahead) and replaces the calendar's events.
* When: every :data:`REFRESH_EVERY` (``always`` or unanswered), every
  :data:`REFRESH_EVERY_SOMETIMES` under ``sometimes`` (a metered or flaky
  line: the existing meaning of that answer is "fewer automatic fetches"),
  never under ``never`` — then the calendar is marked ``internet_off``,
  its last copy stays, and the page says so with its age. A failure backs
  off (:func:`backoff`), doubling up to :data:`MAX_BACKOFF`.

**The address is a secret.** Anyone holding a Google "secret address" can
read that calendar, so the URL is stored in ``calendar_sources.url`` and
nowhere else: the API answers with :func:`redact` (``host/…``), nothing
here logs it (log lines name the calendar's id and host), and
:class:`_SecretPathFilter` blanks its path out of httpx's own
``HTTP Request: GET …`` INFO line, which would otherwise write the whole
address to the journal.

Phones
------

:func:`store_phone_snapshot` keeps one row per (phone, calendar) and
replaces that calendar's events; a calendar the phone no longer lists is
deleted with its events (the person unpicked it), and
:func:`delete_phone` removes everything a phone shared (sharing turned
off). No internet is involved, so phones work under ``never``.

Importable by both processes (the core's worker refreshes on schedule; the
web's "refresh now" and "add" run :func:`refresh_link` in the web process).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from domovoi import calendar_ics, egress, net_safety

log = logging.getLogger(__name__)

MAX_FEED_BYTES = 10 * 1024 * 1024
MAX_URL_LEN = 2048
MAX_NAME_LEN = 80
FETCH_TIMEOUT_SEC = 20.0

WINDOW_BACK = timedelta(days=30)
WINDOW_AHEAD = timedelta(days=365)

REFRESH_EVERY = timedelta(minutes=30)
REFRESH_EVERY_SOMETIMES = timedelta(hours=2)
FIRST_BACKOFF = timedelta(minutes=15)
MAX_BACKOFF = timedelta(hours=12)

# Phones (PUT /api/calendar/phones/{device_id}).
PHONE_MAX_CALENDARS = 25
PHONE_MAX_EVENTS_PER_CALENDAR = 5000
PHONE_MAX_EVENTS = 20000

DEFAULT_COLOR = "#5b7a99"
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

USER_AGENT = "Domovoi-calendar/1.0"

# What a refresh failure is called (calendar_sources.last_error), and how
# the dashboard says it. Plain words, never exception text (which can hold
# the address).
ERROR_WORDS: dict[str, str] = {
    "internet_off": "Internet access is turned off for this server (Settings → Internet), "
                    "so this calendar can't refresh. Showing the last copy.",
    "unreachable": "Couldn't reach the calendar's server. Will try again.",
    "timeout": "The calendar's server took too long to answer. Will try again.",
    "denied": "The calendar's server refused. The link may have been reset or made private: "
              "copy it again and add it again.",
    "not_found": "The link doesn't work any more. It may have been reset: copy the new one "
                 "and add it again.",
    "server_error": "The calendar's server had a problem. Will try again.",
    "too_large": "This calendar is too big to read (over 10 MB).",
    "not_ics": "That address didn't give back a calendar. Check that it's the iCal / ICS link.",
    "blocked": "That address isn't allowed: it isn't a web address, or it points inside "
               "the house.",
    "truncated": "Only the first 5,000 events of this calendar are shown.",
}


class FeedUrlRefused(ValueError):
    """A pasted address that can't be a calendar link; ``words`` says why."""

    def __init__(self, words: str) -> None:
        super().__init__(words)
        self.words = words


# ─── The address ─────────────────────────────────────────────────────────


def normalize_feed_url(raw: str) -> str:
    """What a person pasted -> the address to fetch, or :class:`FeedUrlRefused`."""
    url = (raw or "").strip()
    if not url:
        raise FeedUrlRefused("Paste the calendar's link.")
    if len(url) > MAX_URL_LEN:
        raise FeedUrlRefused("That link is too long.")
    try:
        parts = urlsplit(url)
    except ValueError:
        raise FeedUrlRefused("That isn't a web address.") from None
    scheme = parts.scheme.lower()
    if scheme in ("webcal", "webcals"):
        parts = parts._replace(scheme="https")
    elif scheme not in ("http", "https"):
        raise FeedUrlRefused(
            "That isn't a calendar link. Use an address starting with https:// or webcal://."
        )
    if not parts.hostname:
        raise FeedUrlRefused("That isn't a web address.")
    url = urlunsplit(parts)
    reason = net_safety.check_outbound_url(url, require_resolution=False)
    if reason is not None and reason != egress.TURNED_OFF_REASON:
        raise FeedUrlRefused(ERROR_WORDS["blocked"])
    return url


def url_host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def redact(host: str | None) -> str:
    """How a link is shown: its host and an ellipsis, never the path."""
    return f"{host or 'link'}/…"


def valid_color(value: str | None) -> str:
    v = (value or "").strip()
    return v.lower() if _COLOR_RE.match(v) else DEFAULT_COLOR


# ─── Keeping the address out of the journal ──────────────────────────────


class _SecretPathFilter(logging.Filter):
    """httpx logs ``HTTP Request: GET <url> "HTTP/1.1 200 OK"`` at INFO.
    For a calendar link the path IS the secret, so a record that mentions
    a registered path is re-written with the path replaced by ``/…``."""

    def __init__(self) -> None:
        super().__init__()
        self.paths: set[str] = set()

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.paths:
            return True
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001 — leave a broken record alone
            return True
        hit = False
        for p in list(self.paths):
            if p and p in msg:
                msg = msg.replace(p, "/…")
                hit = True
        if hit:
            record.msg, record.args = msg, ()
        return True


_SECRET_FILTER = _SecretPathFilter()
for _name in ("httpx", "httpcore", "domovoi.net_safety"):
    logging.getLogger(_name).addFilter(_SECRET_FILTER)


def _secret_part(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    tail = parts.path + (("?" + parts.query) if parts.query else "")
    return tail if len(tail) > 1 else ""


def _guard(url: str) -> None:
    tail = _secret_part(url)
    if tail:
        _SECRET_FILTER.paths.add(tail)


# ─── Scheduling ──────────────────────────────────────────────────────────


def refresh_every() -> timedelta | None:
    """How often a link is read under the current internet answer; None
    under ``never``."""
    answer = egress.policy()
    if answer == "never":
        return None
    if answer == "sometimes":
        return REFRESH_EVERY_SOMETIMES
    return REFRESH_EVERY


def backoff(failures: int) -> timedelta:
    """Wait after ``failures`` failures in a row: 15 min, 30, 1 h, … 12 h."""
    n = max(1, int(failures))
    return min(FIRST_BACKOFF * (2 ** min(n - 1, 10)), MAX_BACKOFF)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── Events ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EventRow:
    external_id: str
    title: str
    starts_at: datetime
    ends_at: datetime | None
    all_day: bool
    location: str | None


async def _replace_events(
    session: AsyncSession, source_id: int, kind: str, events: Iterable[EventRow]
) -> int:
    await session.execute(
        text("DELETE FROM calendar_events WHERE calendar_source_id = :id"), {"id": source_id}
    )
    now = _now()
    rows = [
        {
            "sid": source_id, "kind": kind, "ext": e.external_id[:300],
            "title": (e.title or "(no title)")[:200], "s": e.starts_at, "e": e.ends_at,
            "ad": bool(e.all_day), "loc": (e.location or None) and e.location[:200], "now": now,
        }
        for e in events
    ]
    if rows:
        await session.execute(
            text(
                """
                INSERT INTO calendar_events
                    (calendar_source_id, source, external_id, title, starts_at, ends_at,
                     all_day, location, last_synced_at, created_via)
                VALUES (:sid, :kind, :ext, :title, :s, :e, :ad, :loc, :now, 'sync')
                """
            ),
            rows,
        )
    return len(rows)


async def notify_changed(session: AsyncSession, what: str) -> None:
    await session.execute(text("SELECT pg_notify('calendar_changed', :p)"), {"p": what})


# ─── Refreshing a link ───────────────────────────────────────────────────


def _failure_code(e: BaseException) -> str:
    import httpx

    if isinstance(e, egress.InternetTurnedOff):
        return "internet_off"
    if isinstance(e, net_safety.ResponseTooLarge):
        return "too_large"
    if isinstance(e, net_safety.UnsafeOutboundURL):
        return "unreachable" if str(getattr(e, "reason", "")).endswith("does not resolve") else "blocked"
    if isinstance(e, net_safety.TooManyRedirects):
        return "unreachable"
    if isinstance(e, calendar_ics.IcsError):
        return "not_ics"
    if isinstance(e, httpx.TimeoutException):
        return "timeout"
    if isinstance(e, (httpx.HTTPError, OSError, ConnectionError)):
        return "unreachable"
    return "server_error"


def _status_code(status: int) -> str | None:
    if status in (401, 403):
        return "denied"
    if status in (404, 410):
        return "not_found"
    if status >= 400:
        return "server_error"
    return None


def _decode(body: bytes) -> str:
    if body.startswith(b"\xef\xbb\xbf"):
        body = body[3:]
    return body.decode("utf-8", errors="replace")


async def _mark(session: AsyncSession, source_id: int, **fields: Any) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    await session.execute(
        text(f"UPDATE calendar_sources SET {sets}, updated_at = now() WHERE id = :id"),
        {"id": source_id, **fields},
    )


@dataclass
class RefreshResult:
    ok: bool
    error: str | None = None        # an ERROR_WORDS key
    events: int | None = None       # events stored (None: unchanged / not read)
    busy: bool = False              # another refresh of it is running


async def refresh_link(session: AsyncSession, source_id: int, *, client: Any = None) -> RefreshResult:
    """Read one link calendar now and store what it says. The caller's
    session commits. A row another refresh holds is skipped (``busy``)."""
    row = (
        await session.execute(
            text(
                """
                SELECT id, url, url_host, etag, last_modified, failures, last_refreshed_at
                FROM calendar_sources WHERE id = :id AND kind = 'link'
                FOR UPDATE SKIP LOCKED
                """
            ),
            {"id": source_id},
        )
    ).mappings().first()
    if row is None:
        return RefreshResult(ok=False, busy=True)
    url = str(row["url"])
    host = row["url_host"]
    now = _now()
    every = refresh_every()
    if every is None or egress.check_destination(url) is not None:
        # A setting, not a verdict on the link: no failure counted.
        egress.log_refusal(f"calendar {source_id} ({host})")
        await _mark(session, source_id, last_attempt_at=now, last_error="internet_off",
                    next_refresh_at=now + REFRESH_EVERY)
        return RefreshResult(ok=False, error="internet_off")

    _guard(url)
    headers = {"User-Agent": USER_AGENT, "Accept": "text/calendar, text/plain;q=0.8, */*;q=0.5"}
    if row["last_refreshed_at"] is not None:
        if row["etag"]:
            headers["If-None-Match"] = str(row["etag"])
        if row["last_modified"]:
            headers["If-Modified-Since"] = str(row["last_modified"])
    code: str | None = None
    stored: int | None = None
    truncated = False
    etag, modified = row["etag"], row["last_modified"]
    try:
        result = await net_safety.fetch_bytes(
            url, max_bytes=MAX_FEED_BYTES, timeout=FETCH_TIMEOUT_SEC, headers=headers, client=client,
        )
        if result.status_code == 304:
            pass
        else:
            code = _status_code(result.status_code)
            if code is None:
                window_start, window_end = now - WINDOW_BACK, now + WINDOW_AHEAD
                parsed = await asyncio.to_thread(
                    calendar_ics.parse_ics, _decode(result.content),
                    window_start=window_start, window_end=window_end,
                )
                truncated = parsed.truncated
                stored = await _replace_events(session, source_id, "link", (
                    EventRow(e.uid, e.title, e.start, e.end, e.all_day, e.location)
                    for e in parsed.events
                ))
                etag, modified = result.etag, result.last_modified
    except Exception as e:  # noqa: BLE001 — every failure becomes a code
        code = _failure_code(e)
        # The exception's text can carry the address: log its type only.
        log.info("calendar %s (%s): refresh failed: %s (%s)", source_id, host, code, type(e).__name__)

    if code is not None:
        if code == "internet_off":
            await _mark(session, source_id, last_attempt_at=now, last_error=code,
                        next_refresh_at=now + REFRESH_EVERY)
            return RefreshResult(ok=False, error=code)
        failures = int(row["failures"] or 0) + 1
        await _mark(session, source_id, last_attempt_at=now, last_error=code, failures=failures,
                    next_refresh_at=now + backoff(failures))
        return RefreshResult(ok=False, error=code)

    await _mark(
        session, source_id, last_attempt_at=now, last_refreshed_at=now,
        last_error="truncated" if truncated else None, failures=0,
        next_refresh_at=now + every, etag=etag, last_modified=modified,
    )
    if stored is not None:
        await notify_changed(session, "refreshed")
    log.info("calendar %s (%s): refreshed, %s", source_id, host,
             "unchanged" if stored is None else f"{stored} events")
    return RefreshResult(ok=True, events=stored)


async def due_links(session: AsyncSession, limit: int = 20) -> list[int]:
    rows = await session.execute(
        text(
            """
            SELECT id FROM calendar_sources
            WHERE kind = 'link' AND (next_refresh_at IS NULL OR next_refresh_at <= now())
            ORDER BY next_refresh_at NULLS FIRST, id
            LIMIT :limit
            """
        ),
        {"limit": limit},
    )
    return [int(r[0]) for r in rows.all()]


# ─── Phones ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PhoneCalendar:
    calendar_id: str
    name: str
    account_name: str | None
    color: str | None
    events: list[EventRow]


async def store_phone_snapshot(
    session: AsyncSession, device_id: str, calendars: list[PhoneCalendar]
) -> tuple[int, int]:
    """Make the house's copy of ``device_id``'s shared calendars exactly
    ``calendars``. Returns (calendars, events) stored."""
    now = _now()
    keep: list[int] = []
    total = 0
    for cal in calendars:
        sid = (
            await session.execute(
                text(
                    """
                    INSERT INTO calendar_sources
                        (kind, name, color, device_id, phone_calendar_id, account_name,
                         last_refreshed_at, last_attempt_at)
                    VALUES ('phone', :name, :color, :dev, :cid, :acct, :now, :now)
                    ON CONFLICT (device_id, phone_calendar_id) WHERE kind = 'phone'
                    DO UPDATE SET name = EXCLUDED.name, color = EXCLUDED.color,
                                  account_name = EXCLUDED.account_name,
                                  last_refreshed_at = EXCLUDED.last_refreshed_at,
                                  last_attempt_at = EXCLUDED.last_attempt_at,
                                  last_error = NULL, updated_at = now()
                    RETURNING id
                    """
                ),
                {"name": cal.name[:MAX_NAME_LEN] or "Phone calendar", "color": valid_color(cal.color),
                 "dev": device_id, "cid": cal.calendar_id[:200], "acct": cal.account_name, "now": now},
            )
        ).scalar_one()
        keep.append(int(sid))
        total += await _replace_events(session, int(sid), "phone", cal.events)
    await session.execute(
        text(
            "DELETE FROM calendar_sources WHERE kind = 'phone' AND device_id = :dev "
            "AND NOT (id = ANY(:keep))"
        ),
        {"dev": device_id, "keep": keep},
    )
    await notify_changed(session, "phone")
    return len(keep), total


async def delete_phone(session: AsyncSession, device_id: str) -> int:
    res = await session.execute(
        text("DELETE FROM calendar_sources WHERE kind = 'phone' AND device_id = :dev"),
        {"dev": device_id},
    )
    n = int(res.rowcount or 0)
    if n:
        await notify_changed(session, "phone")
    return n


# ─── Reading the list ────────────────────────────────────────────────────


def status_of(row: dict[str, Any]) -> str:
    """``ok`` / ``error`` / ``waiting`` (never read yet) / ``internet_off``."""
    if row.get("kind") == "phone":
        return "ok"
    if egress.internet_turned_off():
        return "internet_off"
    err = row.get("last_error")
    if err == "internet_off":
        return "internet_off"
    if err and err != "truncated":
        return "error"
    if row.get("last_refreshed_at") is None:
        return "waiting"
    return "ok"


async def list_sources(session: AsyncSession) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                """
                SELECT s.id, s.kind, s.name, s.color, s.person_id, p.name AS person_name,
                       s.url_host, s.device_id, d.name AS device_name,
                       dp.name AS device_person_name, s.account_name,
                       s.last_refreshed_at, s.last_attempt_at, s.last_error,
                       s.next_refresh_at, s.created_at,
                       (SELECT count(*) FROM calendar_events e WHERE e.calendar_source_id = s.id)
                           AS event_count
                FROM calendar_sources s
                LEFT JOIN people p ON p.id = s.person_id
                LEFT JOIN devices d ON d.device_id = s.device_id
                LEFT JOIN people dp ON dp.id = d.owner_person_id
                ORDER BY s.kind, lower(s.name), s.id
                """
            )
        )
    ).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        if d["kind"] == "phone":
            owner = d.get("device_person_name") or d.get("device_name")
        else:
            owner = d.get("person_name")
        out.append({
            "id": d["id"], "kind": d["kind"], "name": d["name"], "color": d["color"],
            "person_id": d["person_id"], "owner_name": owner,
            "url_display": redact(d["url_host"]) if d["kind"] == "link" else None,
            "device_id": d["device_id"], "device_name": d.get("device_name"),
            "account_name": d["account_name"], "event_count": int(d["event_count"] or 0),
            "last_refreshed_at": d["last_refreshed_at"], "last_attempt_at": d["last_attempt_at"],
            "next_refresh_at": d["next_refresh_at"],
            "status": status_of(d),
        })
        err = "internet_off" if out[-1]["status"] == "internet_off" else d["last_error"]
        out[-1].update({
            "last_error": err,
            "last_error_words": ERROR_WORDS.get(err) if err else None,
        })
    return out
