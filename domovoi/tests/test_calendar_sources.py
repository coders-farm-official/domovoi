"""Calendars by link and phones' shared calendars (V055, 1.3.0):
domovoi/calendar_feeds.py, the refresher worker and /api/calendar/sources,
/api/calendar/phones/{device_id}.

* DB-free: the address rules (webcal, schemes, house-local targets), the
  redaction, the backoff, the httpx log line, the route tiers.
* On the lane's database: a refresh stores events and keeps the validators;
  304 keeps them; a 404 backs off in plain words; under internet "never"
  nothing is fetched and the last copy stays; the API never returns the
  address; imported events are read-only (409); a phone's snapshot
  replaces, unpicks and deletes only its own calendars; the rate limit
  and the caps.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import admin_auth, calendar_feeds, egress, household_time, net_safety
from domovoi.tests.auth_testkit import HEADER, bearer, install_fake_db, web_app
from domovoi.tests.conftest import requires_db
from domovoi.tests.test_route_auth_matrix import GET_ROUTES, ROUTES, _dependency_calls

BROWSER = {"X-Requested-With": "XMLHttpRequest"}
ADMIN_TOKEN = "admin-session-token"
DEVICE_TOKEN = "d3v1ce-t0ken"
DEVICE = {HEADER: DEVICE_TOKEN}
ADMIN = bearer(ADMIN_TOKEN)
SECRET = "https://calendar.example.com/calendar/ical/alex/private-0123456789abcdef/basic.ics"
EST = timezone(timedelta(hours=-5))

ICS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nX-WR-CALNAME:Work\r\n"
    "BEGIN:VEVENT\r\nUID:1\r\nSUMMARY:Standup\r\nDTSTART:{d}T140000Z\r\nDURATION:PT15M\r\n"
    "RRULE:FREQ=DAILY;COUNT=3\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nUID:2\r\nSUMMARY:Holiday\r\nDTSTART;VALUE=DATE:{d}\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def _ics(days_ahead: int = 1) -> bytes:
    d = (datetime.now(timezone.utc) + timedelta(days=days_ahead)).strftime("%Y%m%d")
    return ICS.format(d=d).encode()


# ─── The address (DB-free) ─────────────────────────────────────────────────


def test_webcal_becomes_https_and_other_schemes_are_refused() -> None:
    assert calendar_feeds.normalize_feed_url(" webcal://p01-calendars.example.com/published/2/abc ") == (
        "https://p01-calendars.example.com/published/2/abc")
    for bad in ("ftp://example.com/a.ics", "file:///etc/passwd", "javascript:alert(1)", "", "not a url"):
        with pytest.raises(calendar_feeds.FeedUrlRefused):
            calendar_feeds.normalize_feed_url(bad)


def test_a_house_local_address_is_refused_like_every_outbound_fetch() -> None:
    for bad in ("http://192.168.1.20/cal.ics", "http://127.0.0.1:6369/x.ics", "http://[::1]/a"):
        with pytest.raises(calendar_feeds.FeedUrlRefused) as e:
            calendar_feeds.normalize_feed_url(bad)
        assert "inside the house" in e.value.words


def test_a_link_can_be_saved_while_internet_access_is_off() -> None:
    with egress.override_policy("never"):
        assert calendar_feeds.normalize_feed_url(SECRET) == SECRET


def test_the_address_is_shown_as_its_host_only() -> None:
    assert calendar_feeds.url_host(SECRET) == "calendar.example.com"
    assert calendar_feeds.redact("calendar.example.com") == "calendar.example.com/…"


def test_backoff_doubles_to_a_ceiling() -> None:
    assert calendar_feeds.backoff(1) == timedelta(minutes=15)
    assert calendar_feeds.backoff(3) == timedelta(hours=1)
    assert calendar_feeds.backoff(40) == calendar_feeds.MAX_BACKOFF


def test_refresh_cadence_follows_the_internet_answer() -> None:
    with egress.override_policy("always"):
        assert calendar_feeds.refresh_every() == timedelta(minutes=30)
    with egress.override_policy("sometimes"):
        assert calendar_feeds.refresh_every() == timedelta(hours=2)
    with egress.override_policy("never"):
        assert calendar_feeds.refresh_every() is None


def test_httpx_request_line_never_carries_the_secret_path(caplog) -> None:
    calendar_feeds._guard(SECRET)
    with caplog.at_level(logging.INFO, logger="httpx"):
        logging.getLogger("httpx").info('HTTP Request: %s %s "%s"', "GET",
                                        "https://203.0.113.9/calendar/ical/alex/private-0123456789abcdef/basic.ics",
                                        "HTTP/1.1 200 OK")
    assert "private-0123456789abcdef" not in caplog.text
    assert "203.0.113.9/…" in caplog.text


# ─── Route tiers (DB-free) ─────────────────────────────────────────────────

_WEB = {(m, p): d for a, m, p, d in ROUTES if a == "web"}


@pytest.mark.parametrize(("method", "path"), [
    ("POST", "/api/calendar/sources"), ("PATCH", "/api/calendar/sources/{source_id}"),
    ("DELETE", "/api/calendar/sources/{source_id}"), ("POST", "/api/calendar/sources/{source_id}/refresh"),
])
def test_managing_calendars_is_an_admins(method, path) -> None:
    assert admin_auth.require_admin_mutation in list(_dependency_calls(_WEB[(method, path)]))


@pytest.mark.parametrize(("method", "path"), [
    ("PUT", "/api/calendar/phones/{device_id}"), ("DELETE", "/api/calendar/phones/{device_id}"),
])
def test_a_phone_sends_on_the_device_tier(method, path) -> None:
    calls = list(_dependency_calls(_WEB[(method, path)]))
    assert admin_auth.require_device in calls
    assert admin_auth.require_admin_mutation not in calls


def test_the_list_is_a_household_read() -> None:
    calls = list(_dependency_calls(getattr(GET_ROUTES[("web", "/api/calendar/sources")], "dependant", None)))
    assert admin_auth.require_device_read in calls


def test_the_openapi_spec_lists_the_routes() -> None:
    spec = json.loads((Path(__file__).resolve().parents[2] / "web" / "openapi.json").read_text(encoding="utf-8"))
    paths = spec["paths"]
    assert {"get", "post"} <= set(paths["/api/calendar/sources"])
    assert {"patch", "delete"} <= set(paths["/api/calendar/sources/{source_id}"])
    assert "post" in paths["/api/calendar/sources/{source_id}/refresh"]
    assert {"put", "delete"} <= set(paths["/api/calendar/phones/{device_id}"])


# ─── On the lane's database ────────────────────────────────────────────────


class _Fetch:
    """Stands in for net_safety.fetch_bytes; records what it was asked."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.calls: list[dict] = []

    async def __call__(self, url, **kw):
        self.calls.append({"url": url, **kw})
        a = self.answers.pop(0)
        if isinstance(a, BaseException):
            raise a
        return a


def _ok(body: bytes, etag: str | None = '"v1"') -> net_safety.FetchResult:
    return net_safety.FetchResult(url=SECRET, status_code=200, content=body, etag=etag,
                                  last_modified="Wed, 11 Nov 2026 10:00:00 GMT")


def _status(code: int) -> net_safety.FetchResult:
    return net_safety.FetchResult(url=SECRET, status_code=code, content=b"")


@pytest_asyncio.fixture
async def house(db_session, monkeypatch):
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    from web.backend.api import calendar as cal_api

    cal_api._last_snapshot.clear()
    with household_time.use_zone(EST), egress.override_policy("always"):
        yield db_session


def _client(headers=None) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=web_app), base_url="http://test",
                       headers={**BROWSER, **(headers or {})})


async def _call(method, path, body=None, headers=ADMIN):
    async with _client(headers) as c:
        return await c.request(method, path, json=body)


@requires_db
@pytest.mark.asyncio
async def test_adding_a_link_reads_it_and_never_returns_the_address(house, monkeypatch, caplog) -> None:
    fetch = _Fetch(_ok(_ics()))
    monkeypatch.setattr(net_safety, "fetch_bytes", fetch)
    with caplog.at_level(logging.DEBUG):
        r = await _call("POST", "/api/calendar/sources", {"url": SECRET.replace("https", "webcal"),
                                                           "name": "Alex work", "color": "#3366CC"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ok"] is True and body["events"] == 4
    src = body["source"]
    assert src["url_display"] == "calendar.example.com/…" and src["color"] == "#3366cc"
    assert src["status"] == "ok" and src["event_count"] == 4
    assert "private-" not in r.text and "private-" not in caplog.text
    assert fetch.calls[0]["url"] == SECRET
    assert fetch.calls[0]["max_bytes"] == calendar_feeds.MAX_FEED_BYTES

    r = await _call("GET", "/api/calendar/sources", headers=DEVICE)
    assert r.status_code == 200 and "private-" not in r.text
    # The same link twice is one calendar.
    r = await _call("POST", "/api/calendar/sources", {"url": SECRET, "name": "Again"})
    assert r.status_code == 409

    # Its events read like any other, labelled and read-only.
    r = await _call("GET", "/api/calendar/events", headers=DEVICE)
    events = r.json()
    assert {e["calendar_name"] for e in events} == {"Alex work"}
    assert all(e["read_only"] and e["source"] == "link" for e in events)
    holiday = next(e for e in events if e["title"] == "Holiday")
    assert holiday["all_day"] and holiday["starts_at"].endswith("T00:00:00-05:00")
    eid = events[0]["id"]
    r = await _call("PATCH", f"/api/calendar/events/{eid}", {"title": "x"}, headers=DEVICE)
    assert r.status_code == 409 and "Alex work" in r.json()["detail"]
    r = await _call("DELETE", f"/api/calendar/events/{eid}", headers=DEVICE)
    assert r.status_code == 409


@requires_db
@pytest.mark.asyncio
async def test_a_refresh_is_conditional_and_a_failure_backs_off_in_plain_words(house, monkeypatch) -> None:
    fetch = _Fetch(_ok(_ics()), _status(304), _status(404))
    monkeypatch.setattr(net_safety, "fetch_bytes", fetch)
    r = await _call("POST", "/api/calendar/sources", {"url": SECRET, "name": "Work"})
    sid = r.json()["source"]["id"]

    r = await _call("POST", f"/api/calendar/sources/{sid}/refresh")
    assert r.status_code == 200 and r.json()["ok"] and r.json()["events"] is None
    assert fetch.calls[1]["headers"]["If-None-Match"] == '"v1"'
    assert "If-Modified-Since" in fetch.calls[1]["headers"]

    r = await _call("POST", f"/api/calendar/sources/{sid}/refresh")
    src = r.json()["source"]
    assert r.json()["ok"] is False and src["status"] == "error" and src["last_error"] == "not_found"
    assert "copy the new one" in src["last_error_words"]
    assert src["event_count"] == 4  # the last copy stays
    row = (await house.execute(text(
        "SELECT failures, next_refresh_at - now() FROM calendar_sources WHERE id = :id"), {"id": sid})).first()
    assert row[0] == 1 and timedelta(minutes=14) < row[1] <= timedelta(minutes=15)


@requires_db
@pytest.mark.asyncio
async def test_under_never_nothing_is_fetched_and_the_last_copy_stays(house, monkeypatch) -> None:
    fetch = _Fetch(_ok(_ics()))
    monkeypatch.setattr(net_safety, "fetch_bytes", fetch)
    r = await _call("POST", "/api/calendar/sources", {"url": SECRET, "name": "Work"})
    sid = r.json()["source"]["id"]
    with egress.override_policy("never"):
        from domovoi.workers.calendar_feed_refresher import CalendarFeedRefresher

        await house.execute(text("UPDATE calendar_sources SET next_refresh_at = now() - interval '1 minute'"))
        await house.commit()
        out = await CalendarFeedRefresher().tick()
        assert out["refreshed"] == 0
        r = await _call("GET", "/api/calendar/sources", headers=DEVICE)
        src = r.json()[0]
        assert src["status"] == "internet_off" and "Showing the last copy" in src["last_error_words"]
        assert src["event_count"] == 4 and src["last_refreshed_at"]
        r = await _call("POST", f"/api/calendar/sources/{sid}/refresh")
        assert r.json()["ok"] is False
    assert len(fetch.calls) == 1
    row = (await house.execute(text("SELECT failures FROM calendar_sources"))).first()
    assert row[0] == 0  # a setting, not a verdict on the link


@requires_db
@pytest.mark.asyncio
async def test_the_worker_reads_what_is_due(house, monkeypatch) -> None:
    from domovoi.workers.calendar_feed_refresher import CalendarFeedRefresher

    fetch = _Fetch(_ok(_ics()), _ok(_ics(2), etag='"v2"'))
    monkeypatch.setattr(net_safety, "fetch_bytes", fetch)
    await _call("POST", "/api/calendar/sources", {"url": SECRET, "name": "Work"})
    assert (await CalendarFeedRefresher().tick())["refreshed"] == 0  # not due yet
    await house.execute(text("UPDATE calendar_sources SET next_refresh_at = now() - interval '1 minute'"))
    await house.commit()
    assert (await CalendarFeedRefresher().tick())["refreshed"] == 1
    assert len(fetch.calls) == 2


@requires_db
@pytest.mark.asyncio
async def test_removing_a_link_takes_its_events(house, monkeypatch) -> None:
    monkeypatch.setattr(net_safety, "fetch_bytes", _Fetch(_ok(_ics())))
    sid = (await _call("POST", "/api/calendar/sources", {"url": SECRET, "name": "Work"})).json()["source"]["id"]
    r = await _call("DELETE", f"/api/calendar/sources/{sid}", headers=DEVICE)
    assert r.status_code in (401, 403)
    r = await _call("PATCH", f"/api/calendar/sources/{sid}", {"name": "Office", "color": "nope"})
    assert r.json()["name"] == "Office" and r.json()["color"] == calendar_feeds.DEFAULT_COLOR
    assert (await _call("DELETE", f"/api/calendar/sources/{sid}")).status_code == 204
    assert (await house.execute(text("SELECT count(*) FROM calendar_events"))).scalar() == 0


@requires_db
@pytest.mark.asyncio
async def test_a_local_event_stays_editable(house) -> None:
    r = await _call("POST", "/api/calendar/events",
                    {"title": "Dentist", "starts_at": "2026-11-20T15:00:00Z"}, headers=DEVICE)
    assert r.status_code == 201 and r.json()["read_only"] is False and r.json()["calendar_name"] is None
    r = await _call("PATCH", f"/api/calendar/events/{r.json()['id']}", {"title": "Dentist!"}, headers=DEVICE)
    assert r.status_code == 200 and r.json()["title"] == "Dentist!"


def _snapshot(*cals, name="Pixel 8"):
    return {"phone_name": name, "calendars": list(cals)}


def _pcal(cid, name, *events, color="#33b679"):
    return {"calendar_id": cid, "name": name, "account_name": "alex@example.com", "color": color,
            "events": list(events)}


def _pev(uid, title, start_ms, end_ms=None, all_day=False):
    return {"uid": uid, "title": title, "starts_at_ms": start_ms, "ends_at_ms": end_ms, "all_day": all_day}


@requires_db
@pytest.mark.asyncio
async def test_a_phone_snapshot_replaces_unpicks_and_never_touches_another_phone(house) -> None:
    t = 1_795_000_000_000
    # 2026-11-17T00:00:00Z: an all-day instance, as CalendarContract keeps it.
    day = int(datetime(2026, 11, 17, tzinfo=timezone.utc).timestamp() * 1000)
    r = await _call("PUT", "/api/calendar/phones/android-aaaa", _snapshot(
        _pcal("1", "Work", _pev("10:1", "Standup", t, t + 900_000)),
        _pcal("2", "Family", _pev("20:1", "", day, day + 86_400_000, all_day=True)),
    ), headers=DEVICE)
    assert r.status_code == 200
    key_a = r.json().pop("calendar_key")
    assert key_a and r.json() == {"calendars": 2, "events": 2, "calendar_key": key_a}
    r = await _call("PUT", "/api/calendar/phones/android-bbbb",
                    _snapshot(_pcal("1", "Work", _pev("9:1", "Other phone", t)), name="Galaxy"), headers=DEVICE)
    assert r.status_code == 200
    key_b = r.json()["calendar_key"]
    assert key_b and key_b != key_a
    phone_a = {**DEVICE, calendar_feeds.CALENDAR_KEY_HEADER: key_a}

    events = (await _call("GET", "/api/calendar/events", headers=DEVICE)).json()
    fam = next(e for e in events if e["calendar_name"] == "Family")
    assert fam["title"] == "(no title)" and fam["all_day"] and fam["starts_at"].startswith("2026-11-17T00:00:00")
    assert fam["owner_name"] == "Pixel 8" and fam["read_only"] and fam["calendar_kind"] == "phone"
    r = await _call("PATCH", f"/api/calendar/events/{fam['id']}", {"title": "x"}, headers=DEVICE)
    assert r.status_code == 409 and "on that phone" in r.json()["detail"]

    # Too soon for the same phone.
    r = await _call("PUT", "/api/calendar/phones/android-aaaa", _snapshot(), headers=phone_a)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0

    # Unpicking Family: only that calendar goes, and only this phone's.
    from web.backend.api import calendar as cal_api

    cal_api._last_snapshot.clear()
    r = await _call("PUT", "/api/calendar/phones/android-aaaa",
                    _snapshot(_pcal("1", "Work", _pev("10:2", "Standup moved", t + 3_600_000))), headers=phone_a)
    assert r.json() == {"calendars": 1, "events": 1, "calendar_key": None}
    titles = sorted(e["title"] for e in (await _call("GET", "/api/calendar/events", headers=DEVICE)).json())
    assert titles == ["Other phone", "Standup moved"]

    # Device ids are an admin's to see; the account (an e-mail) nobody's.
    srcs = (await _call("GET", "/api/calendar/sources", headers=DEVICE)).json()
    assert {(s["device_id"], s["name"]) for s in srcs} == {(None, "Work")}
    assert all(s["account_name"] is None for s in srcs) and "alex@example.com" not in json.dumps(srcs)
    srcs = (await _call("GET", "/api/calendar/sources")).json()
    assert {(s["device_id"], s["name"]) for s in srcs} == {("android-aaaa", "Work"), ("android-bbbb", "Work")}
    assert (await house.execute(text(
        "SELECT count(*) FROM calendar_sources WHERE account_name IS NOT NULL"))).scalar() == 0

    # Sharing turned off.
    assert (await _call("DELETE", "/api/calendar/phones/android-aaaa", headers=phone_a)).status_code == 204
    titles = [e["title"] for e in (await _call("GET", "/api/calendar/events", headers=DEVICE)).json()]
    assert titles == ["Other phone"]


@requires_db
@pytest.mark.asyncio
async def test_one_phone_cannot_write_or_delete_another_phones_calendars(house) -> None:
    t = 1_795_000_000_000
    from web.backend.api import calendar as cal_api

    r = await _call("PUT", "/api/calendar/phones/android-aaaa",
                    _snapshot(_pcal("1", "Work", _pev("1", "Mine", t))), headers=DEVICE)
    key_a = r.json()["calendar_key"]
    r = await _call("PUT", "/api/calendar/phones/android-bbbb",
                    _snapshot(_pcal("1", "Work", _pev("1", "B's", t))), headers=DEVICE)
    key_b = r.json()["calendar_key"]
    cal_api._last_snapshot.clear()

    # Device B (or anything with only the household token) on A's id: refused.
    for headers in (DEVICE, {**DEVICE, calendar_feeds.CALENDAR_KEY_HEADER: key_b},
                    {**DEVICE, calendar_feeds.CALENDAR_KEY_HEADER: "guess"}):
        r = await _call("PUT", "/api/calendar/phones/android-aaaa",
                        _snapshot(_pcal("1", "Work", _pev("1", "Hijacked", t))), headers=headers)
        assert r.status_code == 403, r.text
        assert (await _call("DELETE", "/api/calendar/phones/android-aaaa", headers=headers)).status_code == 403
    titles = sorted(e["title"] for e in (await _call("GET", "/api/calendar/events", headers=DEVICE)).json())
    assert titles == ["B's", "Mine"]

    # A phone's own key works; an admin needs none.
    r = await _call("PUT", "/api/calendar/phones/android-aaaa",
                    _snapshot(_pcal("1", "Work", _pev("1", "Mine 2", t))),
                    headers={**DEVICE, calendar_feeds.CALENDAR_KEY_HEADER: key_a})
    assert r.status_code == 200 and r.json()["calendar_key"] is None
    assert (await _call("DELETE", "/api/calendar/phones/android-bbbb")).status_code == 204
    titles = [e["title"] for e in (await _call("GET", "/api/calendar/events", headers=DEVICE)).json()]
    assert titles == ["Mine 2"]
    # The admin's removal forgot B's key: B, still sharing, starts over.
    cal_api._last_snapshot.clear()
    r = await _call("PUT", "/api/calendar/phones/android-bbbb",
                    _snapshot(_pcal("1", "Work", _pev("1", "B again", t))),
                    headers={**DEVICE, calendar_feeds.CALENDAR_KEY_HEADER: key_b})
    assert r.status_code == 200 and r.json()["calendar_key"] not in (None, key_b)


@requires_db
@pytest.mark.asyncio
async def test_an_admin_removes_a_phone_calendar_left_behind(house) -> None:
    t = 1_795_000_000_000
    await _call("PUT", "/api/calendar/phones/android-aaaa", _snapshot(
        _pcal("1", "Work", _pev("1", "Standup", t)), _pcal("2", "Family", _pev("2", "Dinner", t))),
        headers=DEVICE)
    srcs = (await _call("GET", "/api/calendar/sources")).json()
    work = next(s for s in srcs if s["name"] == "Work")
    assert (await _call("DELETE", f"/api/calendar/sources/{work['id']}", headers=DEVICE)).status_code in (401, 403)
    assert (await _call("DELETE", f"/api/calendar/sources/{work['id']}")).status_code == 204
    titles = [e["title"] for e in (await _call("GET", "/api/calendar/events", headers=DEVICE)).json()]
    assert titles == ["Dinner"]


@requires_db
@pytest.mark.asyncio
async def test_a_silent_phone_goes_stale_after_a_day_and_leaves_after_a_week(house, monkeypatch) -> None:
    from domovoi.workers.calendar_feed_refresher import CalendarFeedRefresher

    t = 1_795_000_000_000
    clock = {"now": datetime.now(timezone.utc)}
    monkeypatch.setattr(calendar_feeds, "_now", lambda: clock["now"])
    await _call("PUT", "/api/calendar/phones/android-aaaa",
                _snapshot(_pcal("1", "Work", _pev("1", "Standup", t))), headers=DEVICE)

    clock["now"] += timedelta(hours=23)
    src = (await _call("GET", "/api/calendar/sources", headers=DEVICE)).json()[0]
    assert src["status"] == "ok" and src["last_error_words"] is None

    clock["now"] += timedelta(hours=7)  # 30 h without a snapshot
    src = (await _call("GET", "/api/calendar/sources", headers=DEVICE)).json()[0]
    assert src["status"] == "stale" and src["last_error"] == "stale"
    assert src["last_error_words"].startswith("Last heard from Pixel 8 30 hours ago.")
    assert (await CalendarFeedRefresher().tick())["expired"] == 0

    clock["now"] += timedelta(days=6)  # 7 days and 6 hours
    assert (await CalendarFeedRefresher().tick())["expired"] == 1
    assert (await _call("GET", "/api/calendar/sources", headers=DEVICE)).json() == []
    assert (await house.execute(text("SELECT count(*) FROM calendar_events"))).scalar() == 0
    assert (await house.execute(text("SELECT count(*) FROM calendar_phones"))).scalar() == 0


@requires_db
@pytest.mark.asyncio
async def test_an_upload_made_before_sharing_was_turned_off_is_refused(house) -> None:
    from web.backend.api import calendar as cal_api

    t = 1_795_000_000_000
    body = {**_snapshot(_pcal("1", "Work", _pev("1", "Standup", t))), "generation": 100}
    r = await _call("PUT", "/api/calendar/phones/android-aaaa", body, headers=DEVICE)
    phone = {**DEVICE, calendar_feeds.CALENDAR_KEY_HEADER: r.json()["calendar_key"]}
    cal_api._last_snapshot.clear()
    # The phone read generation 105, went off mid-upload; sharing was then
    # turned off at 106; the 105 upload lands after the DELETE.
    async with _client(phone) as c:
        assert (await c.delete("/api/calendar/phones/android-aaaa", params={"generation": 106})).status_code == 204
    late = {**_snapshot(_pcal("1", "Work", _pev("1", "Ghost", t))), "generation": 105}
    r = await _call("PUT", "/api/calendar/phones/android-aaaa", late, headers=phone)
    assert r.status_code == 410
    assert (await _call("GET", "/api/calendar/sources", headers=DEVICE)).json() == []
    # Sharing turned on again later: a newer generation is taken.
    r = await _call("PUT", "/api/calendar/phones/android-aaaa", {**late, "generation": 107}, headers=phone)
    assert r.status_code == 200
    # An older one than stored is refused; the same one again (a retry) is fine.
    cal_api._last_snapshot.clear()
    r = await _call("PUT", "/api/calendar/phones/android-aaaa", {**late, "generation": 106}, headers=phone)
    assert r.status_code == 410
    r = await _call("PUT", "/api/calendar/phones/android-aaaa", {**late, "generation": 107}, headers=phone)
    assert r.status_code == 200
    cal_api._last_snapshot.clear()
    await _call("PUT", "/api/calendar/phones/android-aaaa", {**late, "generation": 200}, headers=phone)
    cal_api._last_snapshot.clear()
    r = await _call("PUT", "/api/calendar/phones/android-aaaa", {**late, "generation": 150}, headers=phone)
    assert r.status_code == 409


@requires_db
@pytest.mark.asyncio
async def test_replacing_the_device_token_removes_every_phones_calendars(house) -> None:
    t = 1_795_000_000_000
    await _call("PUT", "/api/calendar/phones/android-aaaa",
                _snapshot(_pcal("1", "Work", _pev("1", "Standup", t))), headers=DEVICE)
    from web.backend.db import session_scope

    await calendar_feeds.after_device_token_change(session_scope)
    assert (await house.execute(text("SELECT count(*) FROM calendar_sources"))).scalar() == 0
    assert (await house.execute(text("SELECT count(*) FROM calendar_phones"))).scalar() == 0


def test_the_token_routes_remove_phones_calendars() -> None:
    import inspect

    from domovoi import main as core_main
    from web.backend.api import auth as web_auth

    for fn in (web_auth.auth_rotate_device_token, web_auth.auth_set_device_token,
               core_main.admin_rotate_device_token, core_main.admin_set_device_token):
        assert "after_device_token_change" in inspect.getsource(fn), fn.__name__


@requires_db
@pytest.mark.asyncio
async def test_events_default_to_a_day_back_and_sixty_ahead(house) -> None:
    now = datetime.now(timezone.utc)
    for title, at in (("Long ago", now - timedelta(days=10)), ("Yesterday", now - timedelta(hours=20)),
                      ("Soon", now + timedelta(days=2)), ("Far", now + timedelta(days=90))):
        r = await _call("POST", "/api/calendar/events", {"title": title, "starts_at": at.isoformat()},
                        headers=DEVICE)
        assert r.status_code == 201
    titles = [e["title"] for e in (await _call("GET", "/api/calendar/events", headers=DEVICE)).json()]
    assert titles == ["Yesterday", "Soon"]
    # One bound alone stays open on the other side, as before.
    async with _client(DEVICE) as c:
        r = await c.get("/api/calendar/events", params={"start": (now - timedelta(days=30)).isoformat()})
    assert [e["title"] for e in r.json()] == ["Long ago", "Yesterday", "Soon", "Far"]


@requires_db
@pytest.mark.asyncio
async def test_one_event_from_two_phones_is_listed_once(house) -> None:
    t = 1_795_000_000_000
    ev = {**_pev("7:1", "Grandma's birthday", t), "ical_uid": "abc123@google.com"}
    other = {**_pev("99:1", "Grandma's birthday", t), "ical_uid": "abc123@google.com"}
    await _call("PUT", "/api/calendar/phones/android-aaaa", _snapshot(_pcal("1", "Family", ev)), headers=DEVICE)
    await _call("PUT", "/api/calendar/phones/android-bbbb",
                _snapshot(_pcal("5", "Family", other, _pev("98:1", "No uid", t)), name="Galaxy"), headers=DEVICE)
    events = (await _call("GET", "/api/calendar/events", headers=DEVICE)).json()
    assert sorted(e["title"] for e in events) == ["Grandma's birthday", "No uid"]
    bday = next(e for e in events if e["title"] == "Grandma's birthday")
    assert bday["owner_name"] == "Pixel 8" and bday["also_in"] == ["Family · Galaxy"]
    # A different start (another instance) is a different event.
    assert calendar_feeds.master_uid("abc123@google.com@2026-11-17T09:00:00") == "abc123@google.com"
    assert calendar_feeds.master_uid("abc123@google.com") == "abc123@google.com"


@requires_db
@pytest.mark.asyncio
async def test_a_private_event_is_busy_with_no_title_or_place(house) -> None:
    t = 1_795_000_000_000
    ev = {**_pev("1", "Therapy", t, t + 3_600_000), "location": "Dr. Smith", "busy": True}
    await _call("PUT", "/api/calendar/phones/android-aaaa", _snapshot(_pcal("1", "Work", ev)), headers=DEVICE)
    e = (await _call("GET", "/api/calendar/events", headers=DEVICE)).json()[0]
    assert e["busy"] is True and e["title"] == "Busy" and e["location"] is None
    stored = (await house.execute(text("SELECT title, location FROM calendar_events"))).first()
    assert tuple(stored) == ("Busy", None)


@requires_db
@pytest.mark.asyncio
async def test_a_phone_snapshot_is_capped(house) -> None:
    many = [_pcal(str(i), f"C{i}") for i in range(calendar_feeds.PHONE_MAX_CALENDARS + 1)]
    r = await _call("PUT", "/api/calendar/phones/android-cccc", _snapshot(*many), headers=DEVICE)
    assert r.status_code == 413
    r = await _call("PUT", "/api/calendar/phones/bad id!", _snapshot(), headers=DEVICE)
    assert r.status_code in (404, 422)
    r = await _call("PUT", "/api/calendar/phones/android-cccc", _snapshot(), headers={})
    assert r.status_code == 401


@requires_db
@pytest.mark.asyncio
async def test_a_phone_says_which_app_it_is_and_older_builds_are_android(house) -> None:
    """``platform`` (optional): the iPhone app's snapshot makes an 'ios'
    devices row, a body without it (an older Android build) an 'android'
    one, and anything else is refused before anything is stored."""
    from domovoi.db.session import engine

    ids = ("iphone-plat-1", "android-plat-2", "phone-plat-3")
    try:
        r = await _call("PUT", f"/api/calendar/phones/{ids[0]}",
                        {**_snapshot(name="Ana's iPhone"), "platform": "ios"}, headers=DEVICE)
        assert r.status_code == 200, r.text
        r = await _call("PUT", f"/api/calendar/phones/{ids[1]}", _snapshot(), headers=DEVICE)
        assert r.status_code == 200, r.text
        r = await _call("PUT", f"/api/calendar/phones/{ids[2]}",
                        {**_snapshot(), "platform": "windows"}, headers=DEVICE)
        assert r.status_code == 422
        async with engine.begin() as conn:
            rows = dict((await conn.execute(text(
                "SELECT device_id, platform FROM devices WHERE device_id = ANY(:ids)"
            ), {"ids": list(ids)})).all())
        assert rows == {ids[0]: "ios", ids[1]: "android"}
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM devices WHERE device_id = ANY(:ids)"), {"ids": list(ids)})
