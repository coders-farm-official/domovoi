"""The AcoustID key in the dashboard (2026-10-17).

Song recognition's key (``ACOUSTID_API_KEY``) could only be set by hand in
``domovoi/.env`` — out of reach inside the Windows install's WSL distro —
while the Music page and Settings → Internet told people they needed one.
Now it is a write-only field in Settings → Configuration → Library:

* the config read never carries the key: an admin (the Bearer or the
  dashboard cookie) gets the mask and ``is_set``, anyone else (the
  pre-setup LAN grace) doesn't get the row; a write needs the admin
  Bearer; clearing it works;
* it is 'hot' (the enricher reads it at every lookup), and a save lists
  song recognition (``library_enricher_enabled``, restart-tier) as needing
  a restart exactly when its next boot changes — never under the answer
  No, never when somebody switched it off by hand;
* "test the key" (``POST /v1/admin/config/acoustid-test`` and the web's
  ``/api/config/acoustid-test``) makes one AcoustID lookup, only when the
  internet answer lets song recognition go out, and tells a refused key
  from a line that didn't answer;
* the key appears in no log line of either process, at DEBUG;
* the enricher reads a refused key the way the test does: pyacoustid 1.3.1
  hands AcoustID's 400 back as data, and parsed it lost the error code, so
  a refused key used to read as the line being down.

DB-free: the auth primitives are faked (``install_fake_db``), ``.env`` lives
in tmp, the intent-log write is stubbed, and ``acoustid.lookup`` is a fake —
no request leaves the test.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from domovoi import config as config_mod
from domovoi import config_env_writer, egress, log_privacy
from domovoi.config import PROFILE_DEFAULTS, Settings, settings
from domovoi.config_schema import (
    FIELD_BY_NAME,
    SECRET_SETTING_NAMES,
    WRITE_ONLY_SETTING_NAMES,
    coerce_and_validate,
    is_write_only_setting,
)
from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import COOKIE, bearer, install_fake_db
from domovoi.workers import library_enricher as le
from web.backend.main import app as web_app

TOKEN = "admin-token"
KEY = "Zq7RealLookingKey42"
NAMES = [pd.name for pd in PROFILE_DEFAULTS]
ENRICHER = "library_enricher_enabled"


def _core() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=core_app, raise_app_exceptions=False),
                       base_url="http://test")


def _web() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=web_app, raise_app_exceptions=False),
                       base_url="http://test", headers={"X-Requested-With": "domovoi-tests"})


@pytest.fixture(autouse=True)
def _keep_acoustid_address(monkeypatch):
    """The check and the sweep move pyacoustid to HTTPS (a module global):
    put back whatever was there for the next module."""
    import acoustid

    monkeypatch.setattr(acoustid, "API_BASE_URL", acoustid.API_BASE_URL)


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A claimed box with no AcoustID key and no Shazam add-on: .env in
    tmp, profile fields at their defaults, an admin session, no DB."""
    import domovoi.db.repositories as repos
    import domovoi.main as main_mod

    env = tmp_path / ".env"
    monkeypatch.setattr(config_env_writer, "_ENV_FILE", env)
    for name in [*NAMES, "internet_access", "acoustid_api_key", "HF_HUB_OFFLINE", "FPCALC"]:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setattr(settings, "internet_access", "")
    monkeypatch.setattr(settings, "acoustid_api_key", "")
    for name in NAMES:
        monkeypatch.setattr(settings, name, Settings.model_fields[name].default)
    monkeypatch.setattr(config_mod, "HF_HUB_OFFLINE_SET_BY_PROFILE", False)
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda n, *a, **k: None if n == "shazamio" else real(n, *a, **k))
    state = install_fake_db(monkeypatch, admin=True, sessions={TOKEN})
    core_app.state.config_apply_lock = asyncio.Lock()

    @asynccontextmanager
    async def fake_scope():
        yield object()

    logged: list[str] = []

    class FakeLog:
        def __init__(self, _s):
            pass

        async def log(self, **kw):
            logged.append(kw.get("transcript"))

    monkeypatch.setattr(main_mod, "session_scope", fake_scope)
    monkeypatch.setattr(repos, "IntentLogRepository", FakeLog)
    return {"env": env, "logged": logged, "state": state}


def _answer(box: dict, answer: str, monkeypatch) -> None:
    """The household's internet answer, where both the gate and the
    profile read it: the live settings and the .env file."""
    monkeypatch.setattr(settings, "internet_access", answer)
    config_env_writer.write_env_values({"internet_access": answer})


class Lookups:
    """A fake ``acoustid.lookup``: records what it was asked (the key
    only as "was it this one"), answers with ``reply`` (a dict, or an
    exception to raise)."""

    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.keys: list[str] = []
        self.args: list[tuple] = []

    def __call__(self, apikey, fingerprint, duration, meta=None, timeout=None):
        self.keys.append(apikey)
        self.args.append((fingerprint, duration, meta, timeout))
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


OK = {"status": "ok", "results": []}
REFUSED = {"status": "error", "error": {"code": 4, "message": "invalid API key"}}


def _rows(payload: dict) -> dict[str, dict]:
    return {f["name"]: f for f in payload["fields"]}


async def _save(changes: dict, headers: dict | None = None) -> Any:
    async with _core() as c:
        return await c.post("/v1/admin/config", json={"changes": changes},
                            headers=bearer(TOKEN) if headers is None else headers)


# ─── The registry ─────────────────────────────────────────────────────────


def test_the_key_is_a_common_library_field_written_never_read() -> None:
    spec = FIELD_BY_NAME["acoustid_api_key"]
    assert (spec.group, spec.section, spec.tier, spec.type) == ("Library", "common", "hot", "str")
    assert spec.secret is True and spec.write_only is True
    assert "acoustid.org/new-application" in spec.help
    assert "not the user API key" in spec.help
    # Write-only is a narrowing of "secret", never a second list to forget.
    assert WRITE_ONLY_SETTING_NAMES <= SECRET_SETTING_NAMES
    assert is_write_only_setting("acoustid_api_key")
    assert not is_write_only_setting("database_url")     # an admin edits it in place
    assert not FIELD_BY_NAME["library_enricher_acoustid_min_score"].write_only


def test_a_pasted_key_is_trimmed_and_anything_else_refused() -> None:
    spec = FIELD_BY_NAME["acoustid_api_key"]
    assert coerce_and_validate(spec, f"  {KEY}\n") == KEY
    assert coerce_and_validate(spec, "") == ""            # clear
    for junk in ("API key: abc", "abc def", "abc\ndef=1", "x" * 129):
        with pytest.raises(ValueError) as e:
            coerce_and_validate(spec, junk)
        assert "abc" not in str(e.value) and "x" * 20 not in str(e.value)


# ─── The read: nobody gets the key back ───────────────────────────────────


@pytest.mark.asyncio
async def test_an_admin_bearer_reads_only_whether_a_key_is_set(box, monkeypatch) -> None:
    async with _core() as c:
        empty = _rows((await c.get("/v1/admin/config", headers=bearer(TOKEN))).json())
    row = empty["acoustid_api_key"]
    assert row["write_only"] is True and row["masked"] is True
    assert row["is_set"] is False and row["value"] == "not set"
    assert row["group"] == "Library" and row["tier"] == "hot"

    monkeypatch.setattr(settings, "acoustid_api_key", KEY)
    async with _core() as c:
        r = await c.get("/v1/admin/config", headers=bearer(TOKEN))
    assert KEY not in r.text
    row = _rows(r.json())["acoustid_api_key"]
    assert row["is_set"] is True and row["value"] == "•••• set" and row["masked"] is True
    # The other secrets keep CORE-6: the Bearer reads the database URL whole.
    assert _rows(r.json())["database_url"]["masked"] is False
    # Every other row says it isn't write-only, but the Brave Search key
    # (Settings → Chat & AI → Search, W1), which is kept the same way.
    write_only = {"acoustid_api_key", "lookup_brave_key"}
    assert all(f["write_only"] is False for f in r.json()["fields"] if f["name"] not in write_only)
    assert _rows(r.json())["lookup_brave_key"]["write_only"] is True


@pytest.mark.asyncio
async def test_the_cookie_reads_the_mask_and_everyone_else_nothing(box, monkeypatch) -> None:
    monkeypatch.setattr(settings, "acoustid_api_key", KEY)
    async with _core() as c:
        c.cookies.set(COOKIE, TOKEN)
        cookie = await c.get("/v1/admin/config")
    assert cookie.status_code == 200 and KEY not in cookie.text
    assert _rows(cookie.json())["acoustid_api_key"]["is_set"] is True

    async with _core() as c:
        guest = await c.get("/v1/admin/config")
        wrong = await c.get("/v1/admin/config", headers=bearer("not-a-session"))
    assert guest.status_code == 401 and KEY not in guest.text
    assert wrong.status_code == 401 and KEY not in wrong.text

    # Before setup the read is open to the LAN: the row isn't in it.
    install_fake_db(monkeypatch, admin=False)
    async with _core() as c:
        pre = await c.get("/v1/admin/config")
    assert pre.status_code == 200 and KEY not in pre.text
    assert "acoustid_api_key" not in _rows(pre.json())
    assert "bot_name" in _rows(pre.json())


@pytest.mark.asyncio
async def test_the_web_read_passes_the_mask_through(box, monkeypatch) -> None:
    import web.backend.api.config as web_config

    monkeypatch.setattr(settings, "acoustid_api_key", KEY)

    async def to_core(path, timeout=10.0, headers=None):
        async with _core() as c:
            r = await c.get(path, headers=headers)
        return r.status_code, r.json()

    monkeypatch.setattr(web_config, "get_admin", to_core)
    async with _web() as c:
        r = await c.get("/api/config/editable", headers=bearer(TOKEN))
        guest = await c.get("/api/config/editable")
    assert r.status_code == 200 and KEY not in r.text
    assert _rows(r.json())["acoustid_api_key"]["is_set"] is True
    assert guest.status_code == 401


# ─── The write ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_admin_saves_it_live_and_clears_it(box) -> None:
    r = await _save({"acoustid_api_key": f" {KEY} "})
    assert r.status_code == 200 and KEY not in r.text
    out = r.json()
    assert out["applied"] == ["acoustid_api_key"] and out["rejected"] == {}
    assert settings.acoustid_api_key == KEY                          # live: no restart
    assert le.provider_available() is True
    assert f"ACOUSTID_API_KEY={KEY}" in box["env"].read_text(encoding="utf-8")
    assert box["logged"] == ["[ui] update config acoustid_api_key"]

    r = await _save({"acoustid_api_key": ""})
    assert r.json()["applied"] == ["acoustid_api_key"]
    assert settings.acoustid_api_key == ""
    assert le.provider_available() is False
    text = box["env"].read_text(encoding="utf-8")
    assert KEY not in text and 'ACOUSTID_API_KEY=""' in text
    async with _core() as c:
        row = _rows((await c.get("/v1/admin/config", headers=bearer(TOKEN))).json())["acoustid_api_key"]
    assert row["is_set"] is False


@pytest.mark.asyncio
async def test_a_bad_paste_is_refused_without_echoing_it(box) -> None:
    r = await _save({"acoustid_api_key": "my key is Zq7RealLookingKey42"})
    assert r.status_code == 200
    assert "acoustid_api_key" in r.json()["rejected"] and KEY not in r.text
    assert settings.acoustid_api_key == "" and not box["env"].exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("how", "status"),
    [("cookie", 403), ("nobody", 401), ("wrong_bearer", 401), ("pre_setup", 501)],
)
async def test_anyone_but_an_admin_bearer_is_refused(box, monkeypatch, how, status) -> None:
    import web.backend.api.config as web_config

    if how == "pre_setup":
        install_fake_db(monkeypatch, admin=False)
    sent: list[str] = []

    async def to_core(path, body=None, timeout=30.0, headers=None):
        sent.append(path)
        return 200, {}

    monkeypatch.setattr(web_config, "post_admin", to_core)
    headers = {"wrong_bearer": bearer("not-a-session")}.get(how, {})
    for client in (_core, _web):
        async with client() as c:
            if how == "cookie":
                c.cookies.set(COOKIE, TOKEN)
            path = "/v1/admin/config" if client is _core else "/api/config/editable"
            method = c.post if client is _core else c.patch
            r = await method(path, json={"changes": {"acoustid_api_key": KEY}}, headers=headers)
        assert r.status_code == status, (client.__name__, r.text)
    assert settings.acoustid_api_key == "" and not box["env"].exists()
    assert sent == []                                   # the web never forwarded it


# ─── When it takes effect: song recognition follows the key ───────────────


async def _internet_feature() -> dict:
    async with _core() as c:
        doc = (await c.get("/v1/admin/internet", headers=bearer(TOKEN))).json()
    return {f["name"]: f for f in doc["features"]}[ENRICHER]


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["always", "sometimes"])
async def test_a_key_turns_song_recognition_on_at_the_next_restart(box, monkeypatch, answer) -> None:
    _answer(box, answer, monkeypatch)
    monkeypatch.setattr(settings, ENRICHER, False)     # what this boot derived: no key
    before = await _internet_feature()
    assert before["value"] is False and before["next_boot_value"] is False
    assert before["condition"] == "needs an AcoustID key or the Shazam add-on"
    assert before["condition_met"] is False

    out = (await _save({"acoustid_api_key": KEY})).json()
    assert out["applied"] == ["acoustid_api_key"]
    assert out["restart_required"] == [ENRICHER]
    after = await _internet_feature()
    assert after["value"] is False and after["next_boot_value"] is True    # "→ on after a restart"
    assert after["condition_met"] is True and after["follows"] is True

    # Taking the key away again: off at the next restart, and said so.
    monkeypatch.setattr(settings, ENRICHER, True)       # as if that restart happened
    out = (await _save({"acoustid_api_key": ""})).json()
    assert out["restart_required"] == [ENRICHER]
    assert (await _internet_feature())["next_boot_value"] is False


@pytest.mark.asyncio
async def test_a_key_never_turns_it_on_for_a_household_that_said_no(box, monkeypatch) -> None:
    _answer(box, "never", monkeypatch)
    monkeypatch.setattr(settings, ENRICHER, False)
    out = (await _save({"acoustid_api_key": KEY})).json()
    assert out["applied"] == ["acoustid_api_key"] and out["restart_required"] == []
    f = await _internet_feature()
    assert f["value"] is False and f["next_boot_value"] is False


@pytest.mark.asyncio
async def test_a_key_never_turns_it_on_when_it_was_switched_off_by_hand(box, monkeypatch) -> None:
    _answer(box, "always", monkeypatch)
    config_env_writer.write_env_values({ENRICHER: False})
    monkeypatch.setattr(settings, ENRICHER, False)
    out = (await _save({"acoustid_api_key": KEY})).json()
    assert out["restart_required"] == []
    f = await _internet_feature()
    assert f["set_by"] == "env_file" and f["next_boot_value"] is False


@pytest.mark.asyncio
async def test_unanswered_it_stays_on_as_it_always_was(box, monkeypatch) -> None:
    assert settings.internet_access == "" and settings.library_enricher_enabled is True
    out = (await _save({"acoustid_api_key": KEY})).json()
    assert out["restart_required"] == []


# ─── "Test the key" ───────────────────────────────────────────────────────


async def _test_key(body: dict | None = None, headers: dict | None = None):
    async with _core() as c:
        return await c.post("/v1/admin/config/acoustid-test", json=body or {},
                            headers=bearer(TOKEN) if headers is None else headers)


@pytest.mark.asyncio
async def test_the_saved_key_works(box, monkeypatch) -> None:
    monkeypatch.setattr(settings, "acoustid_api_key", KEY)
    monkeypatch.setattr(le, "can_fingerprint", lambda _a: True)
    fake = Lookups(OK)
    with patch("acoustid.lookup", fake):
        r = await _test_key()
    assert r.status_code == 200, r.text
    assert r.json() == {"result": "works", "detail": "", "fingerprints": True}
    assert fake.keys == [KEY]
    fingerprint, duration, _meta, timeout = fake.args[0]
    assert fingerprint == le.KEY_CHECK_FINGERPRINT and duration == le.KEY_CHECK_DURATION
    assert timeout and timeout <= 30          # under the web hop's 30 s
    assert KEY not in r.text


@pytest.mark.asyncio
async def test_a_typed_key_is_tested_instead_and_nothing_is_saved(box, monkeypatch) -> None:
    monkeypatch.setattr(settings, "acoustid_api_key", "saved-key")
    fake = Lookups(REFUSED)
    with patch("acoustid.lookup", fake):
        r = await _test_key({"key": f"  {KEY} "})
    assert r.json()["result"] == "refused" and r.json()["detail"] == "invalid API key"
    assert fake.keys == [KEY]
    assert settings.acoustid_api_key == "saved-key" and not box["env"].exists()


@pytest.mark.asyncio
async def test_a_line_that_does_not_answer_is_not_a_refusal(box, monkeypatch) -> None:
    import acoustid

    monkeypatch.setattr(settings, "acoustid_api_key", KEY)
    down = Lookups(acoustid.WebServiceError(f"HTTP request failed: refused (client={KEY})"))
    with patch("acoustid.lookup", down):
        r = await _test_key()
    out = r.json()
    assert out["result"] == "unreachable" and KEY not in r.text
    assert "HTTP request failed" in out["detail"]

    busy = Lookups({"status": "error", "error": {"code": 14, "message": "too many requests"}})
    with patch("acoustid.lookup", busy):
        out = (await _test_key()).json()
    assert out == {"result": "unreachable", "detail": "AcoustID answered: too many requests",
                   "fingerprints": out["fingerprints"]}


@pytest.mark.asyncio
async def test_a_working_key_on_a_box_that_cannot_fingerprint_says_so(box, monkeypatch) -> None:
    import shutil

    import acoustid

    monkeypatch.setattr(settings, "acoustid_api_key", KEY)
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)     # no fpcalc anywhere
    monkeypatch.setattr(acoustid, "have_chromaprint", False, raising=False)
    with patch("acoustid.lookup", Lookups(OK)):
        out = (await _test_key()).json()
    assert out["result"] == "works" and out["fingerprints"] is False


@pytest.mark.asyncio
async def test_under_no_nothing_is_sent(box, monkeypatch) -> None:
    monkeypatch.setattr(settings, "acoustid_api_key", KEY)
    _answer(box, "never", monkeypatch)
    fake = Lookups(OK)
    # The suite pins the gate to "unset" (conftest); the answer No, here.
    with egress.override_policy("never"), patch("acoustid.lookup", fake):
        r = await _test_key()
        typed = await _test_key({"key": "another"})
        assert r.status_code == 409 and typed.status_code == 409
        assert r.headers.get(egress.REFUSAL_HEADER) == egress.REFUSAL_VALUE
        assert fake.keys == []
        # The gate itself, below the route: the check refuses before asking.
        with pytest.raises(egress.InternetTurnedOff):
            await le.check_acoustid_key(KEY)
    assert fake.keys == []


@pytest.mark.asyncio
async def test_no_key_to_test_is_a_422_and_no_request(box) -> None:
    fake = Lookups(OK)
    with patch("acoustid.lookup", fake):
        r = await _test_key()
        junk = await _test_key({"key": "two words"})
    assert r.status_code == 422 and "no AcoustID key" in r.text
    assert junk.status_code == 422
    assert fake.keys == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("how", "status"),
    [("cookie", 403), ("nobody", 401), ("pre_setup", 501)],
)
async def test_only_an_admin_bearer_may_test(box, monkeypatch, how, status) -> None:
    monkeypatch.setattr(settings, "acoustid_api_key", KEY)
    if how == "pre_setup":
        install_fake_db(monkeypatch, admin=False)
    fake = Lookups(OK)
    for client, path in ((_core, "/v1/admin/config/acoustid-test"), (_web, "/api/config/acoustid-test")):
        async with client() as c:
            if how == "cookie":
                c.cookies.set(COOKIE, TOKEN)
            with patch("acoustid.lookup", fake):
                r = await c.post(path, json={})
        assert r.status_code == status, (path, r.text)
    assert fake.keys == []


@pytest.mark.asyncio
async def test_the_web_proxies_the_test_to_the_core(box, monkeypatch) -> None:
    import web.backend.api.config as web_config

    monkeypatch.setattr(settings, "acoustid_api_key", KEY)
    forwarded: list[tuple[str, dict, dict]] = []

    async def to_core(path, body=None, timeout=30.0, headers=None):
        forwarded.append((path, body, dict(headers or {})))
        async with _core() as c:
            r = await c.post(path, json=body or {}, headers=headers)
        return r.status_code, r.json()

    monkeypatch.setattr(web_config, "post_admin", to_core)
    fake = Lookups(REFUSED)
    async with _web() as c, _patched_lookup(fake):
        saved = await c.post("/api/config/acoustid-test", json={}, headers=bearer(TOKEN))
        typed = await c.post("/api/config/acoustid-test", json={"key": "typed1"}, headers=bearer(TOKEN))
        too_long = await c.post("/api/config/acoustid-test", json={"key": "x" * 257}, headers=bearer(TOKEN))
    assert saved.json()["result"] == "refused" and typed.json()["result"] == "refused"
    assert [f[1] for f in forwarded] == [{}, {"key": "typed1"}]
    assert forwarded[0][0] == "/v1/admin/config/acoustid-test"
    assert forwarded[0][2].get("Authorization") == f"Bearer {TOKEN}"
    assert fake.keys == [KEY, "typed1"]
    assert too_long.status_code == 422


@asynccontextmanager
async def _patched_lookup(fake):
    with patch("acoustid.lookup", fake):
        yield


# ─── The enricher reads a refusal the way the test does ───────────────────


@pytest.mark.asyncio
async def test_the_sweep_reads_a_refused_key_from_the_real_answer(box, monkeypatch, tmp_path, caplog) -> None:
    """pyacoustid's ``match`` with ``parse=True`` turns AcoustID's 400 into
    ``WebServiceError("status: error")`` with the code gone, so a user key
    read as a line failure: no hint, and the sweep stopped after five
    "errors". The enricher now reads the answer itself."""
    monkeypatch.setattr(settings, "acoustid_api_key", "user-key")
    monkeypatch.setattr(le, "_key_hint_logged", False)
    asked: list[bool] = []

    def match(_key, _path, parse=True, **_kw):
        asked.append(parse)
        return REFUSED

    with patch("acoustid.match", match), caplog.at_level(logging.WARNING, logger=le.__name__):
        out = await le._enrich_via_acoustid(tmp_path / "x.mp3")
    assert asked == [False]
    assert out.verdict is le.Verdict.NOT_ASKED and out.unusable is True
    assert any("APPLICATION key" in r.getMessage() for r in caplog.records)

    def ok(_key, _path, parse=True, **_kw):
        return {"status": "ok", "results": []}

    with patch("acoustid.match", ok):
        assert (await le._enrich_via_acoustid(tmp_path / "y.mp3")).verdict is le.Verdict.NO_MATCH


@pytest.mark.asyncio
async def test_the_key_goes_to_acoustid_over_https(box, monkeypatch, tmp_path) -> None:
    """pyacoustid's default address is plain HTTP: the key and every
    fingerprint would cross the network readable. Both the test and the
    sweep move it to HTTPS; an address set on purpose is left alone."""
    import acoustid

    monkeypatch.setattr(acoustid, "API_BASE_URL", le.ACOUSTID_API_PLAIN)
    with patch("acoustid.lookup", Lookups(OK)):
        await le.check_acoustid_key(KEY)
    assert acoustid.API_BASE_URL == "https://api.acoustid.org/v2/"

    monkeypatch.setattr(acoustid, "API_BASE_URL", le.ACOUSTID_API_PLAIN)
    monkeypatch.setattr(settings, "acoustid_api_key", KEY)
    with patch("acoustid.match", lambda *_a, **_k: OK):
        await le._enrich_via_acoustid(tmp_path / "x.mp3")
    assert acoustid.API_BASE_URL == "https://api.acoustid.org/v2/"

    monkeypatch.setattr(acoustid, "API_BASE_URL", "http://127.0.0.1:6391/v2/")
    with patch("acoustid.lookup", Lookups(OK)):
        await le.check_acoustid_key(KEY)
    assert acoustid.API_BASE_URL == "http://127.0.0.1:6391/v2/"


def test_the_check_fingerprint_is_a_real_chromaprint_one() -> None:
    """URL-safe base64 of Chromaprint's compressed form: algorithm byte,
    then a 24-bit count of sub-fingerprints."""
    import base64

    fp = le.KEY_CHECK_FINGERPRINT
    raw = base64.urlsafe_b64decode(fp + "=" * (-len(fp) % 4))
    assert raw[0] == 1                                           # fpcalc's default algorithm
    count = int.from_bytes(raw[1:4], "big")
    assert 100 < count < 400                                     # ~8 per second of 30 s
    assert le.KEY_CHECK_DURATION == 30


# ─── No log line carries the key ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_key_is_in_no_log_line_of_either_process(box, monkeypatch, caplog) -> None:
    import acoustid

    import web.backend.api.config as web_config

    async def to_core_post(path, body=None, timeout=30.0, headers=None):
        async with _core() as c:
            r = await c.post(path, json=body or {}, headers=headers)
        return r.status_code, r.json()

    async def to_core_get(path, timeout=10.0, headers=None):
        async with _core() as c:
            r = await c.get(path, headers=headers)
        return r.status_code, r.json()

    monkeypatch.setattr(web_config, "post_admin", to_core_post)
    monkeypatch.setattr(web_config, "get_admin", to_core_get)
    monkeypatch.setattr(le, "_key_hint_logged", False)
    caplog.set_level(logging.DEBUG)
    _answer(box, "always", monkeypatch)
    monkeypatch.setattr(settings, ENRICHER, False)
    async with _web() as c:
        h = bearer(TOKEN)
        assert (await c.patch("/api/config/editable", json={"changes": {"acoustid_api_key": KEY}},
                              headers=h)).status_code == 200
        assert (await c.patch("/api/config/editable", json={"changes": {"acoustid_api_key": f"bad {KEY}"}},
                              headers=h)).status_code == 200
        await c.get("/api/config/editable", headers=h)
        await c.get("/api/config/internet", headers=h)
        for reply in (OK, REFUSED, acoustid.WebServiceError(f"HTTP request failed (client={KEY})"),
                      RuntimeError(f"boom {KEY}")):
            with patch("acoustid.lookup", Lookups(reply)):
                await c.post("/api/config/acoustid-test", json={}, headers=h)
                await c.post("/api/config/acoustid-test", json={"key": KEY}, headers=h)
    # A sweep with a refused key, through the real function.
    monkeypatch.setattr(settings, "acoustid_api_key", KEY)
    with patch("acoustid.match", lambda *_a, **_k: REFUSED):
        await le._enrich_via_acoustid(box["env"].parent / "x.mp3")

    lines = [r.getMessage() for r in caplog.records]
    assert any("AcoustID key test" in m for m in lines)          # the test did log, without the key
    leaked = [m for m in lines if KEY in m]
    assert leaked == []
    assert all(KEY not in t for t in box["logged"])


def test_the_redaction_also_blanks_the_key_by_its_setting_name() -> None:
    """S5's scrub covers a line that names the setting (a .env dump, a
    pydantic error): ``…api_key=<value>`` is blanked."""
    for line in (f"ACOUSTID_API_KEY={KEY}", f"loading acoustid_api_key={KEY} from .env"):
        assert KEY not in log_privacy.scrub(line)
