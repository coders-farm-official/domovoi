"""The update notice's lifecycle and its routes (domovoi/update_notice.py;
design note UPDATE-NOTICES §6, §8).

Lifecycle: git is faked (the latest release, HEAD and the relation are
pinned per test), as are the fetch and the signature check. Routes: the
auth primitives are faked (``install_fake_db``), as in
test_version_read_tier.py. One ``requires_db`` test keeps the state in the
real ``household_settings`` table. No network.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import admin_auth, egress, git_version, update_notice
from domovoi.config import settings
from domovoi.tests.auth_testkit import COOKIE, HEADER, bearer, install_fake_db, web_client
from domovoi.tests.conftest import requires_db

R150 = ("v1.5.0", "5" * 40)
R160 = ("v1.6.0", "6" * 40)
HEAD = "h" * 40
HL = ["Chat looks things up on the web when it has to.",
      "Music keeps playing after you ask a question.",
      "Friend Houses is open to everyone in the house."]


@pytest.fixture
def house(monkeypatch):
    """A house on stable at 1.4.1 with a fetch that works; the latest
    release, the relation and the signature are the test's to set."""
    update_notice.reset_for_tests()
    h = SimpleNamespace(release=R150, relation="behind", signed=True, fetch_ok=True, head=HEAD, bad=None,
                        channel="stable", fetches=0, verified=[], store={}, updating=False)

    async def load():
        return h.store.get("v")

    async def save(value):
        h.store["v"] = json.loads(json.dumps(value))

    async def commits_behind():
        h.fetches += 1
        git_version._LAST_FETCH = {"ok": h.fetch_ok, "error": None if h.fetch_ok else "can't reach", "at": 0}
        return {"behind": 3, "ahead": 0, "upstream": True, "upstream_sha": h.release[1],
                "error": None if h.fetch_ok else "can't reach the update server", "channel": h.channel,
                "upstream_version": h.release[0][1:], "latest_release": h.release[0][1:]}

    async def verify(tag, commit):
        h.verified.append(tag)
        return h.signed

    monkeypatch.setattr(update_notice, "_db_load", load)
    monkeypatch.setattr(update_notice, "_db_save", save)
    monkeypatch.setattr(git_version, "commits_behind", commits_behind)
    monkeypatch.setattr(update_notice, "verify_release", verify)
    monkeypatch.setattr(update_notice, "stable_release", lambda: h.release)
    monkeypatch.setattr(update_notice, "head_sha", lambda: h.head)
    monkeypatch.setattr(update_notice, "_bad_sha", lambda: h.bad)
    monkeypatch.setattr(update_notice, "relation",
                        lambda tag, commit, head, bad: ("unknown", None) if commit == bad else
                        (h.relation, 12 if h.relation == "ahead" else None))
    monkeypatch.setattr(update_notice, "read_highlights", lambda rev, tag: list(HL))
    monkeypatch.setattr(update_notice, "_main_highlights", lambda tag: [])
    monkeypatch.setattr(update_notice, "_updating", lambda: h.updating)
    monkeypatch.setattr(git_version, "tracking", lambda: {"channel": h.channel})
    monkeypatch.setattr(settings, "update_auto_check", True)
    yield h
    update_notice.reset_for_tests()


def _run(coro):
    return asyncio.run(coro)


def _check(by):
    with egress.override_policy("always"):
        return _run(update_notice.run_check(by))


def _notice(policy="always"):
    with egress.override_policy(policy):
        return _run(update_notice.notice())


def test_an_automatic_check_finds_the_house_behind(house):
    res = _check("auto")
    assert res["behind"] == 3 and res["updates"]["relation"] == "behind"
    n = _notice()
    assert n == {"available": True, "version": "1.5.0", "highlights": HL, "found_by": "auto",
                 "found_at": n["found_at"], "updating": False}
    assert house.store["v"]["last_auto"]["ok"] is True
    assert house.store["v"]["notice"]["commit"] == R150[1]


def test_unticking_drops_an_automatic_notice_and_keeps_a_manual_one(house, monkeypatch):
    _check("auto")
    monkeypatch.setattr(settings, "update_auto_check", False)
    assert _notice()["available"] is False
    assert house.store["v"]["notice"] is None, "dropped, not hidden"
    monkeypatch.setattr(settings, "update_auto_check", True)
    assert _notice()["available"] is False, "ticking again doesn't bring it back before a check"
    _check("manual")
    monkeypatch.setattr(settings, "update_auto_check", False)
    n = _notice()
    assert n["available"] is True and n["found_by"] == "manual"


def test_a_manual_check_takes_over_an_automatic_notice(house):
    _check("auto")
    _check("manual")
    assert _notice()["found_by"] == "manual"
    _check("auto")
    assert _notice()["found_by"] == "manual", "an automatic check keeps the source"


def test_an_automatic_check_while_unticked_makes_no_notice(house, monkeypatch):
    monkeypatch.setattr(settings, "update_auto_check", False)
    _check("auto")
    assert _notice()["available"] is False


def test_updating_clears_the_notice_on_the_next_read(house):
    _check("auto")
    house.relation = "same"              # pulled and restarted elsewhere
    update_notice.forget_caches()
    assert _notice()["available"] is False
    assert house.store["v"]["notice"] is None


def test_a_newer_signed_release_replaces_the_notice_with_the_same_source(house):
    _check("manual")
    house.release = R160
    _check("auto")
    n = _notice()
    assert n["version"] == "1.6.0" and n["found_by"] == "manual"


def test_internet_no_hides_the_notice_and_it_comes_back(house):
    _check("auto")
    assert _notice("never") == {"available": False, "updating": False}
    assert house.store["v"]["notice"] is not None, "hidden, not dropped"
    assert _notice("sometimes")["available"] is True


def test_a_failed_check_keeps_the_notice(house):
    _check("auto")
    house.fetch_ok = False
    house.release = R160
    res = _check("auto")
    assert res["updates"]["auto_check"]["last_ok"] is False
    assert _notice()["version"] == "1.5.0"
    house.fetch_ok, house.signed = True, None        # GitHub down: the manifest isn't read
    _check("auto")
    assert _notice()["version"] == "1.5.0"
    assert house.store["v"]["last_auto"]["ok"] is False


def test_an_unsigned_release_makes_no_notice(house):
    house.signed = False
    _check("auto")
    assert _notice()["available"] is False
    house.signed = True
    _check("auto")
    assert _notice()["available"] is True


def test_a_rolled_back_release_drops_the_notice(house):
    _check("auto")
    house.bad = R150[1]
    update_notice.forget_caches()
    assert _notice()["available"] is False


def test_the_updating_flag_rides_the_notice(house):
    _check("manual")
    house.updating = True
    assert _notice()["updating"] is True


def test_updating_is_reported_without_a_notice_too(house):
    """`updating` is the real update state, notice or none (a house on
    main, an update started by hand): Home still reads "Domovoi is
    restarting…"."""
    house.updating = True
    assert _notice() == {"available": False, "updating": True}
    assert _notice("never") == {"available": False, "updating": True}, "internet No hides the notice only"
    with egress.override_policy("always"):
        assert _run(update_notice.updates_state())["notice"] == {"available": False, "updating": True}
        house.head = None
        assert _run(update_notice.updates_state())["notice"] == {"available": False, "updating": True}
    house.head = HEAD
    _check("manual")
    with egress.override_policy("always"):
        up = _run(update_notice.updates_state())
    assert up["notice"]["available"] is True and up["notice"]["updating"] is True
    house.updating = False
    assert _notice()["updating"] is False


def test_updating_is_the_version_read_s_restart_in_progress(monkeypatch):
    """The same test as the version read's restart_in_progress:
    self_restart.underway on the update unit's last result."""
    from domovoi import self_restart

    last = {"status": "running"}
    seen = []
    monkeypatch.setattr(git_version, "read_last_update", lambda: last)
    monkeypatch.setattr(self_restart, "underway", lambda lu=None, mode=None: seen.append(lu) or True)
    assert update_notice._updating() is True and seen == [last]
    monkeypatch.setattr(self_restart, "underway", lambda lu=None, mode=None: False)
    assert update_notice._updating() is False

    def boom(*a, **k):
        raise OSError("can't tell")

    monkeypatch.setattr(self_restart, "underway", boom)
    assert update_notice._updating() is False, "can't tell reads as not updating"


def test_a_press_during_an_automatic_check_waits_and_answers_its_result(house, monkeypatch):
    gate = asyncio.Event()
    real = git_version.commits_behind

    async def slow():
        await gate.wait()
        return await real()

    monkeypatch.setattr(git_version, "commits_behind", slow)

    async def both():
        auto = asyncio.create_task(update_notice.run_check("auto"))
        await asyncio.sleep(0)
        manual = asyncio.create_task(update_notice.run_check("manual"))
        await asyncio.sleep(0)
        gate.set()
        return await auto, await manual

    with egress.override_policy("always"):
        a, m = _run(both())
    assert house.fetches == 1, "one fetch for both"
    assert m["behind"] == a["behind"] and m["updates"]["notice"]["found_by"] == "manual"


def test_why_off_states(house, monkeypatch):
    for policy, why in (("always", None), ("sometimes", "internet_sometimes"), ("never", "internet_never"),
                        ("", "internet_unanswered")):
        with egress.override_policy(policy):
            up = _run(update_notice.updates_state())
        assert up["auto_check"]["why_off"] == why and up["auto_check"]["active"] is (why is None)
    monkeypatch.setattr(settings, "update_auto_check", False)
    with egress.override_policy("always"):
        up = _run(update_notice.updates_state())
        assert up["auto_check"] == {"enabled": False, "active": False, "why_off": "setting_off",
                                    "last_at": None, "last_ok": None}
        house.head = None
        up = _run(update_notice.updates_state())
    assert up["auto_check"]["why_off"] == "no_git" and up["latest_release"] is None


def test_the_updates_shape_ahead_on_main(house):
    house.channel, house.relation = "main", "ahead"
    up = _check("manual")["updates"]
    assert set(up) == {"auto_check", "latest_release", "relation", "changes_since", "notice"}
    assert up["relation"] == "ahead" and up["changes_since"] == 12
    assert up["latest_release"] == {"version": "1.5.0", "tag": "v1.5.0", "commit": R150[1], "signed": True,
                                    "highlights": HL}
    assert up["notice"] == {"available": False, "updating": False}


def test_latest_release_is_null_before_any_fetch(house):
    house.release = None
    with egress.override_policy("always"):
        up = _run(update_notice.updates_state())
    assert up["latest_release"] is None and up["relation"] == "unknown" and up["changes_since"] is None


def test_the_state_survives_the_database_being_away(house, monkeypatch):
    async def down(*a):
        raise OSError("db down")

    monkeypatch.setattr(update_notice, "_db_load", down)
    monkeypatch.setattr(update_notice, "_db_save", down)
    _check("manual")
    assert _notice()["available"] is True


# ─── the routes ──────────────────────────────────────────────────────────

ADMIN_TOKEN = "admin-token"
DEVICE_TOKEN = "device-token"
NOTICE = "/v1/updates/notice"


def _core() -> AsyncClient:
    from domovoi.main import app as core_app

    return AsyncClient(transport=ASGITransport(app=core_app, raise_app_exceptions=False),
                       base_url="http://test")


def test_the_notice_route_is_a_device_read():
    from domovoi.main import app as core_app
    from domovoi.tests.route_walk import iter_route_contexts

    def calls(dependant):
        for dep in getattr(dependant, "dependencies", ()) if dependant else ():
            if dep.call is not None:
                yield dep.call
            yield from calls(dep)

    gates = [g for rc in iter_route_contexts(core_app.routes)
             if getattr(rc, "path", None) == NOTICE for g in calls(getattr(rc, "dependant", None))]
    assert admin_auth.require_device_read in gates


@pytest.mark.asyncio
async def test_the_notice_route_answers_the_household_and_never_an_unpaired_guest(house, monkeypatch):
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    with egress.override_policy("always"):
        await update_notice.run_check("auto")
        async with _core() as c:
            r = await c.get(NOTICE)
            assert r.status_code == 401, r.text
            assert "1.5.0" not in r.text
            r = await c.get(NOTICE, headers={HEADER: DEVICE_TOKEN})
            assert r.status_code == 200, r.text
            body = r.json()
            assert set(body) == {"available", "version", "highlights", "found_by", "found_at", "updating"}
            assert body["version"] == "1.5.0" and body["highlights"] == HL
            assert "5555555" not in r.text and "stable" not in r.text, "no SHA, no channel"
            assert (await c.get(NOTICE, headers=bearer(ADMIN_TOKEN))).status_code == 200
        async with _core() as c:
            c.cookies.set(COOKIE, ADMIN_TOKEN)                 # an admin after a reload
            assert (await c.get(NOTICE)).status_code == 200


@pytest.mark.asyncio
async def test_the_version_read_and_the_check_carry_updates(house, monkeypatch):
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)

    async def fake_state() -> dict[str, Any]:
        return {"sha": "abc1234", "restart_required": False, "channel": "stable"}

    monkeypatch.setattr(git_version, "version_state", fake_state)
    with egress.override_policy("always"):
        async with _core() as c:
            r = await c.post("/v1/admin/version/check", headers={HEADER: DEVICE_TOKEN})
            assert r.status_code == 200, r.text
            assert r.json()["behind"] == 3
            assert r.json()["updates"]["relation"] == "behind"
            assert r.json()["updates"]["notice"]["found_by"] == "manual"
            r = await c.get("/v1/admin/version", headers={HEADER: DEVICE_TOKEN})
            assert r.status_code == 200, r.text
            assert r.json()["updates"]["latest_release"]["version"] == "1.5.0"


@pytest.mark.asyncio
async def test_the_web_mirror_forwards_the_credential(monkeypatch):
    from web.backend.api import config as config_api

    seen: list[tuple[str, dict]] = []

    async def fake_core(path, timeout=10.0, headers=None):
        seen.append((path, dict(headers or {})))
        return 200, {"available": False, "updating": False}

    monkeypatch.setattr(config_api, "get_admin", fake_core)
    install_fake_db(monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN)
    async with web_client() as client:
        refused = await client.get("/api/updates/notice")
        allowed = await client.get("/api/updates/notice", headers={HEADER: DEVICE_TOKEN})
    assert refused.status_code == 401, refused.text
    assert allowed.status_code == 200 and allowed.json() == {"available": False, "updating": False}
    assert seen == [("/v1/updates/notice", seen[0][1])] and seen[0][1][HEADER] == DEVICE_TOKEN


# ─── the real table ──────────────────────────────────────────────────────


@requires_db
def test_the_state_lives_in_household_settings(house, monkeypatch):
    from sqlalchemy import text

    from domovoi.db.session import session_scope

    monkeypatch.undo()
    update_notice.reset_for_tests()

    async def go():
        state = await update_notice.load_state()
        state["notice"] = {"tag": "v1.5.0", "version": "1.5.0", "commit": "5" * 40, "found_by": "auto",
                           "found_at": "2026-11-25T08:02:11Z"}
        await update_notice.save_state(state)
        update_notice.reset_for_tests()
        again = await update_notice.load_state()
        async with session_scope() as s:
            raw = (await s.execute(text("SELECT value FROM household_settings WHERE key = 'update_check'"))).scalar()
            await s.execute(text("DELETE FROM household_settings WHERE key = 'update_check'"))
        return again, raw

    again, raw = _run(go())
    assert again["notice"]["tag"] == "v1.5.0"
    assert (json.loads(raw) if isinstance(raw, str) else raw)["notice"]["commit"] == "5" * 40
    update_notice.reset_for_tests()
