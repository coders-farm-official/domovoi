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

Whose they are and how long they last (V057 ``calendar_phones``, review
2026-11-14):

* **The calendar key.** A device id is self-asserted, so the first
  snapshot of a phone mints a key (:func:`admit_phone`), handed back once;
  only its SHA-256 is kept. Every later PUT or DELETE for that phone must
  present it (``X-Domovoi-Calendar-Key``): another household device can't
  write or delete this phone's calendars. An admin may delete any.
* **Late uploads.** A snapshot may carry the phone's ``generation`` (a
  number that only grows). Turning sharing off records the generation it
  happened at; a snapshot at or below it arrived after the DELETE (it was
  sent first, the phone went off mid-upload) and is refused (410), and
  one older than a snapshot already stored is refused too (409).
* **Left behind.** A phone sends every 6 hours. Without a snapshot for
  :data:`PHONE_STALE_AFTER` (24 h) its calendars show as ``stale`` ("last
  heard from …"); after :data:`PHONE_EXPIRE_AFTER` (7 days) the
  refresher worker deletes them (:func:`expire_phones`). An admin can
  remove one calendar or all of a phone's at any time, and replacing the
  household device token removes every phone's (:func:`forget_all_phones`).

Importable by both processes (the core's worker refreshes on schedule; the
web's "refresh now" and "add" run :func:`refresh_link` in the web process).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
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

PHONE_STALE_AFTER = timedelta(hours=24)
PHONE_EXPIRE_AFTER = timedelta(days=7)
CALENDAR_KEY_HEADER = "X-Domovoi-Calendar-Key"

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
    # The iCalendar UID (one event in two calendars: listed once) and a
    # private event sent as "busy" (no title or place).
    ical_uid: str | None = None
    busy: bool = False


BUSY_TITLE = "Busy"

# calendar_ics names a recurring instance "<UID>@<local start>".
_INSTANCE_SUFFIX_RE = re.compile(r"@\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$")


def master_uid(uid: str | None) -> str | None:
    """A link event's iCalendar UID without the instance suffix."""
    u = (uid or "").strip()
    if not u or u.startswith("anon-"):
        return None
    return _INSTANCE_SUFFIX_RE.sub("", u)[:300] or None


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
            "title": BUSY_TITLE if e.busy else (e.title or "(no title)")[:200],
            "s": e.starts_at, "e": e.ends_at, "ad": bool(e.all_day),
            "loc": None if e.busy else ((e.location or None) and e.location[:200]), "now": now,
            "uid": (e.ical_uid or None) and e.ical_uid[:300], "busy": bool(e.busy),
        }
        for e in events
    ]
    if rows:
        await session.execute(
            text(
                """
                INSERT INTO calendar_events
                    (calendar_source_id, source, external_id, title, starts_at, ends_at,
                     all_day, location, last_synced_at, created_via, ical_uid, busy)
                VALUES (:sid, :kind, :ext, :title, :s, :e, :ad, :loc, :now, 'sync', :uid, :busy)
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
                    EventRow(e.uid, e.title, e.start, e.end, e.all_day, e.location,
                             ical_uid=master_uid(e.uid))
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
    color: str | None
    events: list[EventRow]


class PhoneRefused(Exception):
    """A phone's PUT or DELETE the house won't take: ``status`` and plain
    ``detail`` for the HTTP answer."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


WRONG_KEY_DETAIL = (
    "this phone's calendar key is missing or wrong: only the phone that shares "
    "these calendars, or an admin, can change them"
)
DELETED_DETAIL = (
    "sharing was turned off on this phone after this update was made: it is not stored"
)
SUPERSEDED_DETAIL = "a newer update from this phone is already stored"


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def key_matches(stored: str | None, presented: str | None) -> bool:
    if not stored or not presented:
        return False
    return hmac.compare_digest(stored, key_hash(presented))


async def _phone_row(session: AsyncSession, device_id: str, *, create: bool) -> dict[str, Any] | None:
    if create:
        await session.execute(
            text("INSERT INTO calendar_phones (device_id) VALUES (:d) ON CONFLICT (device_id) DO NOTHING"),
            {"d": device_id},
        )
    row = (
        await session.execute(
            text(
                "SELECT device_id, key_hash, last_generation, deleted_generation "
                "FROM calendar_phones WHERE device_id = :d FOR UPDATE"
            ),
            {"d": device_id},
        )
    ).mappings().first()
    return dict(row) if row is not None else None


async def admit_phone(
    session: AsyncSession, device_id: str, *, key: str | None, generation: int | None,
) -> str | None:
    """May this snapshot be stored? Raises :class:`PhoneRefused` (403 wrong
    key, 410 sharing turned off after it was made, 409 older than one
    stored); otherwise returns a NEW calendar key when the phone has none
    yet (hand it to the phone once), else None. Locks the phone's row, so a
    PUT and a DELETE of one phone take turns."""
    row = await _phone_row(session, device_id, create=True)
    assert row is not None
    if row["key_hash"] and not key_matches(row["key_hash"], key):
        raise PhoneRefused(403, WRONG_KEY_DETAIL)
    if generation is not None:
        if row["deleted_generation"] is not None and generation <= int(row["deleted_generation"]):
            raise PhoneRefused(410, DELETED_DETAIL)
        if row["last_generation"] is not None and generation < int(row["last_generation"]):
            raise PhoneRefused(409, SUPERSEDED_DETAIL)
    if row["key_hash"]:
        return None
    minted = secrets.token_urlsafe(32)
    await session.execute(
        text("UPDATE calendar_phones SET key_hash = :h, updated_at = now() WHERE device_id = :d"),
        {"h": key_hash(minted), "d": device_id},
    )
    return minted


async def store_phone_snapshot(
    session: AsyncSession, device_id: str, calendars: list[PhoneCalendar],
    *, generation: int | None = None,
) -> tuple[int, int]:
    """Make the house's copy of ``device_id``'s shared calendars exactly
    ``calendars``. Returns (calendars, events) stored. The caller has
    admitted it (:func:`admit_phone`)."""
    now = _now()
    keep: list[int] = []
    total = 0
    for cal in calendars:
        sid = (
            await session.execute(
                text(
                    """
                    INSERT INTO calendar_sources
                        (kind, name, color, device_id, phone_calendar_id,
                         last_refreshed_at, last_attempt_at)
                    VALUES ('phone', :name, :color, :dev, :cid, :now, :now)
                    ON CONFLICT (device_id, phone_calendar_id) WHERE kind = 'phone'
                    DO UPDATE SET name = EXCLUDED.name, color = EXCLUDED.color,
                                  account_name = NULL,
                                  last_refreshed_at = EXCLUDED.last_refreshed_at,
                                  last_attempt_at = EXCLUDED.last_attempt_at,
                                  last_error = NULL, updated_at = now()
                    RETURNING id
                    """
                ),
                {"name": cal.name[:MAX_NAME_LEN] or "Phone calendar", "color": valid_color(cal.color),
                 "dev": device_id, "cid": cal.calendar_id[:200], "now": now},
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
    await session.execute(
        text(
            """
            INSERT INTO calendar_phones (device_id, last_generation, last_snapshot_at)
            VALUES (:d, :g, :now)
            ON CONFLICT (device_id) DO UPDATE SET
                last_generation = CASE
                    WHEN EXCLUDED.last_generation IS NULL THEN calendar_phones.last_generation
                    ELSE GREATEST(COALESCE(calendar_phones.last_generation, EXCLUDED.last_generation),
                                  EXCLUDED.last_generation) END,
                last_snapshot_at = EXCLUDED.last_snapshot_at, updated_at = now()
            """
        ),
        {"d": device_id, "g": generation, "now": now},
    )
    await notify_changed(session, "phone")
    return len(keep), total


async def delete_phone(
    session: AsyncSession, device_id: str, *, key: str | None = None,
    generation: int | None = None, admin: bool = False,
) -> int:
    """Every calendar ``device_id`` shared leaves the house. From the phone
    (sharing turned off) it needs the phone's key and leaves a tombstone at
    ``generation`` (or the last one stored), so an upload made before it
    and arriving after it is refused. An admin needs no key, and the
    phone's key is forgotten (its next snapshot, if it still shares, mints
    a new one). Returns the calendars removed."""
    row = await _phone_row(session, device_id, create=False)
    if row is not None and not admin and row["key_hash"] and not key_matches(row["key_hash"], key):
        raise PhoneRefused(403, WRONG_KEY_DETAIL)
    if row is not None:
        marks = [g for g in (row["deleted_generation"], row["last_generation"], generation) if g is not None]
        await session.execute(
            text(
                "UPDATE calendar_phones SET deleted_generation = :g, deleted_at = :now, "
                + ("key_hash = NULL, " if admin else "")
                + "updated_at = now() WHERE device_id = :d"
            ),
            {"g": max(int(g) for g in marks) if marks else None, "now": _now(), "d": device_id},
        )
    elif generation is not None and not admin:
        await session.execute(
            text(
                "INSERT INTO calendar_phones (device_id, deleted_generation, deleted_at) "
                "VALUES (:d, :g, :now) ON CONFLICT (device_id) DO NOTHING"
            ),
            {"d": device_id, "g": generation, "now": _now()},
        )
    res = await session.execute(
        text("DELETE FROM calendar_sources WHERE kind = 'phone' AND device_id = :dev"),
        {"dev": device_id},
    )
    n = int(res.rowcount or 0)
    if n:
        await notify_changed(session, "phone")
    return n


async def expire_phones(session: AsyncSession, now: datetime | None = None) -> int:
    """Delete the calendars of phones not heard from for
    :data:`PHONE_EXPIRE_AFTER` (an uninstalled app, a lost phone, a server
    the phone forgot), and those phones' rows. Returns calendars removed."""
    cutoff = (now or _now()) - PHONE_EXPIRE_AFTER
    res = await session.execute(
        text(
            "DELETE FROM calendar_sources WHERE kind = 'phone' "
            "AND COALESCE(last_refreshed_at, created_at) < :cut"
        ),
        {"cut": cutoff},
    )
    n = int(res.rowcount or 0)
    await session.execute(
        text(
            """
            DELETE FROM calendar_phones p
            WHERE COALESCE(p.last_snapshot_at, p.created_at) < :cut
              AND (p.deleted_at IS NULL OR p.deleted_at < :cut)
              AND NOT EXISTS (SELECT 1 FROM calendar_sources s
                              WHERE s.kind = 'phone' AND s.device_id = p.device_id)
            """
        ),
        {"cut": cutoff},
    )
    if n:
        log.info("phone calendars: %s expired (no update for %s days)", n, PHONE_EXPIRE_AFTER.days)
        await notify_changed(session, "phone")
    return n


async def forget_all_phones(session: AsyncSession) -> int:
    """The household device token was replaced: every phone's calendars
    leave, with the phones' keys (a phone given the new token shares again
    and is handed a new key)."""
    present = (
        await session.execute(text("SELECT to_regclass('calendar_phones') IS NOT NULL"))
    ).scalar()
    if not present:
        return 0
    res = await session.execute(text("DELETE FROM calendar_sources WHERE kind = 'phone'"))
    await session.execute(text("DELETE FROM calendar_phones"))
    n = int(res.rowcount or 0)
    if n:
        await notify_changed(session, "phone")
    return n


async def after_device_token_change(scope: Any) -> None:
    """Called by the rotate / set device-token routes (web and core) with
    their ``session_scope``: :func:`forget_all_phones` in its own
    transaction, never failing the token change."""
    try:
        async with scope() as session:
            n = await forget_all_phones(session)
        if n:
            log.info("device token replaced: %s phone calendars removed", n)
    except Exception:  # noqa: BLE001 — the token change already happened
        log.warning("device token replaced, but phones' calendars were not removed", exc_info=True)


# ─── Reading the list ────────────────────────────────────────────────────


def _ago(delta: timedelta) -> str:
    hours = int(delta.total_seconds() // 3600)
    if hours < 48:
        return f"{hours} hours ago"
    return f"{hours // 24} days ago"


def phone_is_stale(last: datetime | None, now: datetime | None = None) -> bool:
    return last is None or (now or _now()) - last >= PHONE_STALE_AFTER


def status_of(row: dict[str, Any]) -> str:
    """``ok`` / ``error`` / ``waiting`` (never read yet) / ``internet_off``,
    and for a phone ``ok`` / ``stale`` (no snapshot for a day)."""
    if row.get("kind") == "phone":
        return "stale" if phone_is_stale(row.get("last_refreshed_at")) else "ok"
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


def stale_words(owner: str | None, last: datetime | None, now: datetime | None = None) -> str:
    who = owner or "the phone"
    days = PHONE_EXPIRE_AFTER.days
    if last is None:
        return f"Nothing heard from {who} yet."
    return (
        f"Last heard from {who} {_ago((now or _now()) - last)}. Phones send an update every "
        f"6 hours; after {days} days without one, its calendars leave the house."
    )


async def list_sources(session: AsyncSession, *, admin: bool = False) -> list[dict[str, Any]]:
    """Every calendar from elsewhere. A phone's ``device_id`` is an admin's
    to see (it is what removing all of a phone's calendars takes); the
    account a phone calendar lives in is never returned."""
    rows = (
        await session.execute(
            text(
                """
                SELECT s.id, s.kind, s.name, s.color, s.person_id, p.name AS person_name,
                       s.url_host, s.device_id, d.name AS device_name,
                       dp.name AS device_person_name,
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
    now = _now()
    out = []
    for r in rows:
        d = dict(r)
        phone = d["kind"] == "phone"
        if phone:
            owner = d.get("device_person_name") or d.get("device_name")
        else:
            owner = d.get("person_name")
        status = status_of(d)
        item = {
            "id": d["id"], "kind": d["kind"], "name": d["name"], "color": d["color"],
            "person_id": d["person_id"], "owner_name": owner,
            "url_display": redact(d["url_host"]) if not phone else None,
            "device_id": d["device_id"] if (phone and admin) else None,
            "device_name": d.get("device_name"),
            "account_name": None, "event_count": int(d["event_count"] or 0),
            "last_refreshed_at": d["last_refreshed_at"], "last_attempt_at": d["last_attempt_at"],
            "next_refresh_at": d["next_refresh_at"],
            "status": status,
            "expires_at": ((d["last_refreshed_at"] or d["created_at"]) + PHONE_EXPIRE_AFTER)
                          if phone else None,
        }
        if phone:
            item["last_error"] = "stale" if status == "stale" else None
            item["last_error_words"] = (
                stale_words(d.get("device_name") or owner, d["last_refreshed_at"], now)
                if status == "stale" else None
            )
        else:
            err = "internet_off" if status == "internet_off" else d["last_error"]
            item["last_error"] = err
            item["last_error_words"] = ERROR_WORDS.get(err) if err else None
        out.append(item)
    return out
