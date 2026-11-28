"""Away chat's setup and every way it is revoked (web/backend/api/away.py,
domovoi/away/store.py), against the test database.

Pinned: the enrolment answer (wire §1), LAN-only, the key shapes, the
device key as the identity (never an id the caller names), guests and
shared screens refused, the house's "Chat from away" and internet gates,
re-enrolling replacing both keys, and revocation by each path (the phone's
own DELETE, an admin's Remove, the setting off, a shared screen marked,
the device row gone, the device holding another key), each seen by the
listener's phone list the core writes (``service._write_phones``).
"""

from __future__ import annotations

import json
import secrets
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from domovoi import egress
from domovoi.away import keys as away_keys
from domovoi.away import listener as al
from domovoi.away import store as away_store
from domovoi.db.session import engine, session_scope
from domovoi.friend_houses import service as svc
from domovoi.friend_houses.noise import public_bytes
from domovoi.tests.auth_testkit import _db, web_client  # noqa: F401 — fixture
from domovoi.tests.away_kit import away_db, claimed, enroll, phone  # noqa: F401 — fixture
from domovoi.tests.conftest import requires_db
from web.backend.api import away as away_api
from web.backend.main import app as web_app

pytestmark = [requires_db, pytest.mark.asyncio]


@pytest.fixture
def told(monkeypatch):
    """Every time the web tells the core the phones changed."""
    calls: list[str] = []

    async def tell(_request):
        calls.append("reconcile")
        return True

    async def state(_device):
        return "starting"

    monkeypatch.setattr(away_api, "tell_core", tell)
    monkeypatch.setattr(away_api, "core_state", state)
    # The core writes the listener's phone list on its Linux side.
    monkeypatch.setattr(svc, "sys", SimpleNamespace(platform="linux"))
    return calls


async def _listed(tmp_path) -> dict:
    """What the listener would be told: phones.json as the core writes it."""
    folder = tmp_path / "listener"
    folder.mkdir(exist_ok=True)
    await svc._write_phones(folder)
    return json.loads((folder / al.PHONES_FILE).read_text())


# ─── enrolling ───────────────────────────────────────────────────────────


async def test_enrolling_answers_the_onion_and_the_house_key(away_db, told, tmp_path) -> None:
    async with web_client() as c:
        base, _, _ = await claimed(c)
        p = await phone(c, base)
        out = await enroll(c, p)
        assert set(out) == {"onion", "port", "house_noise_pub", "proto", "state"}
        assert out["port"] == 80 and out["proto"] == 1 and out["state"] == "starting"
        assert out["onion"].endswith(".onion") and len(out["onion"]) == 62
        assert out["onion"] == away_keys.onion_address(away_keys.onion_seed())
        house = away_keys.noise_key()
        assert out["house_noise_pub"] == away_keys.b64url(away_keys.noise_public(house))
        # Its own key, never the Friend Houses session key.
        from domovoi.friend_houses import identity as fid

        assert fid.load() is None or fid.load().session_key != away_keys.noise_public(house)
        assert told == ["reconcile"]
        r = await c.get("/api/away/status", headers=p["headers"])
        assert r.json() == {"house": "on", "enrolled": True, "state": "starting"}
        listed = await _listed(tmp_path)
        assert listed == {"on": True, "phones": {public_bytes(p["noise"]).hex(): p["id"]}}
        # The same answer again for a second enrolment: one house key, one onion.
        again = await enroll(c, await phone(c, base))
        assert (again["onion"], again["house_noise_pub"]) == (out["onion"], out["house_noise_pub"])


async def test_enrolling_again_replaces_both_keys(away_db, told, tmp_path) -> None:
    from domovoi.tests.away_kit import b64url, tor_auth_pub
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    async with web_client() as c:
        base, _, _ = await claimed(c)
        p = await phone(c, base)
        await enroll(c, p)
        new = X25519PrivateKey.generate()
        p["body"] = {"tor_auth_pub": tor_auth_pub(), "noise_pub": b64url(public_bytes(new))}
        await enroll(c, p)
        listed = await _listed(tmp_path)
        assert listed["phones"] == {public_bytes(new).hex(): p["id"]}  # the old key is refused at once
        async with session_scope() as s:
            (row,) = await away_store.current_phones(s)
        assert row.tor_auth_pub == p["body"]["tor_auth_pub"]


async def test_publishing_passes_through_enroll_and_status(away_db, told, monkeypatch) -> None:
    """Tor has the phone's key, its descriptor is on its way: said as is,
    and the status route doesn't nudge the core (it has applied it all)."""
    async def publishing(_device):
        return "publishing"

    monkeypatch.setattr(away_api, "core_state", publishing)
    async with web_client() as c:
        base, _, _ = await claimed(c)
        p = await phone(c, base)
        assert (await enroll(c, p))["state"] == "publishing"
        assert told == ["reconcile"]
        r = await c.get("/api/away/status", headers=p["headers"])
        assert r.json() == {"house": "on", "enrolled": True, "state": "publishing"}
        assert told == ["reconcile"]                                  # no nudge


async def test_a_state_the_web_doesnt_know_is_starting(away_db, told, monkeypatch) -> None:
    """The core's word is checked against the known states: anything else
    reads as None from core_state, and enroll and status say starting."""
    async def unknown(_device):
        return None

    monkeypatch.setattr(away_api, "core_state", unknown)
    async with web_client() as c:
        base, _, _ = await claimed(c)
        p = await phone(c, base)
        assert (await enroll(c, p))["state"] == "starting"
        r = await c.get("/api/away/status", headers=p["headers"])
        assert r.json() == {"house": "on", "enrolled": True, "state": "starting"}
        assert told == ["reconcile", "reconcile"]                     # enroll's, then the nudge


@pytest.mark.parametrize("said,heard", [
    ("ready", "ready"), ("publishing", "publishing"), ("starting", "starting"),
    ("uploading", None), (None, None), (7, None),
])
async def test_core_state_keeps_only_the_known_states(monkeypatch, said, heard) -> None:
    import httpx

    class Answer:
        status_code = 200

        def json(self):
            return {"state": said}

    class Client:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None, headers=None):
            assert url.endswith("/v1/away/state") and params == {"device": "android-1"}
            return Answer()

    async def headers():
        return {}

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(away_api, "service_auth_headers", headers)
    assert await away_api.core_state("android-1") == heard
    assert away_api.STATES == ("ready", "publishing", "starting")


async def test_lan_only(away_db, told) -> None:
    async with web_client() as c:
        base, _, _ = await claimed(c)
        p = await phone(c, base)
    async with AsyncClient(transport=ASGITransport(app=web_app, client=("8.8.4.4", 4000)),
                           base_url="http://test") as far:
        r = await far.post("/api/away/enroll", json=p["body"], headers=p["headers"])
        assert (r.status_code, r.json().get("code")) == (403, "lan_only"), r.text
        r = await far.request("DELETE", "/api/away/enroll", headers=p["headers"])
        assert (r.status_code, r.json()["code"]) == (403, "lan_only")
        # Status works off the LAN (only at home is it useful).
        assert (await far.get("/api/away/status", headers=p["headers"])).status_code == 200
    for addr in ("192.168.1.40", "10.0.0.5", "fe80::1", "::1"):
        assert away_api.lan_caller(_req(addr)), addr
    for addr in ("8.8.8.8", "2001:4860::1", "not an address"):
        assert not away_api.lan_caller(_req(addr)), addr


def _req(addr: str):
    from starlette.requests import Request

    return Request({"type": "http", "headers": [], "client": (addr, 1)})


@pytest.mark.parametrize("change", [
    {"tor_auth_pub": "A" * 51},                               # short
    {"tor_auth_pub": "a" * 52},                               # lower case
    {"tor_auth_pub": "A" * 51 + "1"},                         # not base32
    {"noise_pub": "A" * 42},                                  # short
    {"noise_pub": "A" * 42 + "="},                            # padded
    {"noise_pub": "A" * 43},                                  # all zero: a low-order key
    {"noise_pub": None},
    {"tor_auth_pub": 5},
])
async def test_bad_keys(away_db, told, change) -> None:
    async with web_client() as c:
        base, _, _ = await claimed(c)
        p = await phone(c, base)
        r = await c.post("/api/away/enroll", json=p["body"] | change, headers=p["headers"])
        assert (r.status_code, r.json()["code"]) == (400, "bad_key")
        assert str(change.get("tor_auth_pub", "")) not in r.text or not change.get("tor_auth_pub")


async def test_the_two_keys_the_same_or_a_taken_key_is_bad(away_db, told) -> None:
    import base64

    async with web_client() as c:
        base, _, _ = await claimed(c)
        p, q = await phone(c, base), await phone(c, base)
        raw = base64.urlsafe_b64decode(p["body"]["noise_pub"] + "=")
        same = {"tor_auth_pub": base64.b32encode(raw).decode().rstrip("="), "noise_pub": p["body"]["noise_pub"]}
        r = await c.post("/api/away/enroll", json=same, headers=p["headers"])
        assert r.json()["code"] == "bad_key"
        await enroll(c, p)
        r = await c.post("/api/away/enroll", json=q["body"] | {"noise_pub": p["body"]["noise_pub"]},
                         headers=q["headers"])
        assert r.json()["code"] == "bad_key"


async def test_the_device_key_is_the_identity(away_db, told) -> None:
    async with web_client() as c:
        base, _, _ = await claimed(c)
        p = await phone(c, base)
        no_key = {k: v for k, v in p["headers"].items() if k != "X-Domovoi-Device-Key"}
        r = await c.post("/api/away/enroll", json=p["body"], headers=no_key)
        assert (r.status_code, r.json()["code"]) == (401, "device_key_required")
        wrong = {**p["headers"], "X-Domovoi-Device-Key": "not-the-key"}
        assert (await c.post("/api/away/enroll", json=p["body"], headers=wrong)).status_code == 401
        # The device tier first, as at home: no household token, no answer.
        keyed_only = {"X-Domovoi-Device-Key": p["headers"]["X-Domovoi-Device-Key"], "X-Requested-With": "x"}
        assert (await c.post("/api/away/enroll", json=p["body"], headers=keyed_only)).status_code == 401


async def test_before_setup_nothing_enrols(away_db, told) -> None:
    async with web_client() as c:
        r = await c.post("/api/devices/register", json={"device_id": "android-presetup1", "want_device_key": True})
        key = r.json()["device_key"]
        c.cookies.clear()
        from domovoi.tests.away_kit import b64url, tor_auth_pub

        r = await c.post("/api/away/enroll", headers={"X-Domovoi-Device-Key": key},
                         json={"tor_auth_pub": tor_auth_pub(), "noise_pub": b64url(bytes(range(1, 33)))})
        assert r.status_code == 501
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM devices WHERE device_id = 'android-presetup1'"))


async def test_a_shared_screen_is_refused_as_a_guest(away_db, told) -> None:
    async with web_client() as c:
        base, admin, _ = await claimed(c)
        p = await phone(c, base, "tablet")
        assert (await c.patch(f"/api/devices/{p['id']}/shared-screen", json={"shared_screen": True},
                              headers=admin)).status_code == 200
        r = await c.post("/api/away/enroll", json=p["body"], headers=p["headers"])
        assert (r.status_code, r.json()["code"]) == (409, "guest")


async def test_away_off_and_internet_off(away_db, told, monkeypatch) -> None:
    async with web_client() as c:
        base, admin, _ = await claimed(c)
        p = await phone(c, base)
        r = await c.put("/api/away/setting", json={"on": False}, headers=admin)
        assert r.status_code == 200 and r.json() == {"on": False, "house": "away_off"}
        r = await c.post("/api/away/enroll", json=p["body"], headers=p["headers"])
        assert (r.status_code, r.json()["code"]) == (409, "away_off")
        assert (await c.get("/api/away/status", headers=p["headers"])).json() == {
            "house": "away_off", "enrolled": False, "state": None}
        await c.put("/api/away/setting", json={"on": True}, headers=admin)
        monkeypatch.setattr(egress, "policy", lambda: "never")
        r = await c.post("/api/away/enroll", json=p["body"], headers=p["headers"])
        assert (r.status_code, r.json()["code"]) == (409, "internet_off")


# ─── revocation, every path ──────────────────────────────────────────────


async def test_the_phone_turns_it_off_for_itself(away_db, told, tmp_path) -> None:
    async with web_client() as c:
        base, _, _ = await claimed(c)
        p = await phone(c, base)
        await enroll(c, p)
        r = await c.request("DELETE", "/api/away/enroll", headers=p["headers"])
        assert r.json() == {"ok": True}
        assert (await _listed(tmp_path))["phones"] == {}
        assert told == ["reconcile", "reconcile"]
        # Again, not enrolled: the same answer.
        assert (await c.request("DELETE", "/api/away/enroll", headers=p["headers"])).json() == {"ok": True}


async def test_an_admin_removes_a_phone(away_db, told, tmp_path) -> None:
    async with web_client() as c:
        base, admin, _ = await claimed(c)
        p = await phone(c, base)
        await enroll(c, p)
        listing = (await c.get("/api/away/phones", headers=admin)).json()
        assert listing["on"] is True and [x["device_id"] for x in listing["phones"]] == [p["id"]]
        assert listing["phones"][0]["current"] is True
        # Household members don't manage it.
        assert (await c.get("/api/away/phones", headers=base)).status_code in (401, 403)
        assert (await c.request("DELETE", f"/api/away/phones/{p['id']}", headers=base)).status_code in (401, 403)
        r = await c.request("DELETE", f"/api/away/phones/{p['id']}", headers=admin)
        assert r.json() == {"ok": True}
        assert (await _listed(tmp_path))["phones"] == {}
        assert (await c.request("DELETE", f"/api/away/phones/{p['id']}", headers=admin)).status_code == 404


async def test_away_off_for_the_house_tells_the_listener(away_db, told, tmp_path) -> None:
    async with web_client() as c:
        base, admin, _ = await claimed(c)
        p = await phone(c, base)
        await enroll(c, p)
        await c.put("/api/away/setting", json={"on": False}, headers=admin)
        assert await _listed(tmp_path) == {"on": False, "phones": {}}
        await c.put("/api/away/setting", json={"on": True}, headers=admin)
        assert (await _listed(tmp_path))["phones"]  # kept: back when it's on again


async def test_the_internet_answer_never_tells_the_listener(away_db, told, tmp_path, monkeypatch) -> None:
    async with web_client() as c:
        base, _, _ = await claimed(c)
        await enroll(c, await phone(c, base))
    monkeypatch.setattr(egress, "policy", lambda: "never")
    assert await _listed(tmp_path) == {"on": False, "phones": {}}


async def test_marking_a_shared_screen_removes_its_keys(away_db, told, tmp_path, monkeypatch) -> None:
    async with web_client() as c:
        base, admin, _ = await claimed(c)
        p = await phone(c, base)
        await enroll(c, p)
        assert (await c.patch(f"/api/devices/{p['id']}/shared-screen", json={"shared_screen": True},
                              headers=admin)).status_code == 200
        assert (await _listed(tmp_path))["phones"] == {}
        async with session_scope() as s:
            assert not await away_store.revoke(s, p["id"])  # already gone
        assert told[-1] == "reconcile"


async def test_a_device_removed_takes_its_keys(away_db, told, tmp_path) -> None:
    async with web_client() as c:
        base, _, _ = await claimed(c)
        p = await phone(c, base)
        await enroll(c, p)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM devices WHERE device_id = :d"), {"d": p["id"]})
    assert (await _listed(tmp_path))["phones"] == {}


async def test_a_device_holding_another_key_no_longer_counts(away_db, told, tmp_path) -> None:
    async with web_client() as c:
        base, admin, _ = await claimed(c)
        p = await phone(c, base)
        await enroll(c, p)
        async with engine.begin() as conn:
            await conn.execute(text("UPDATE devices SET device_key_hash = :h WHERE device_id = :d"),
                               {"h": secrets.token_hex(32), "d": p["id"]})
        assert (await _listed(tmp_path))["phones"] == {}
        listing = (await c.get("/api/away/phones", headers=admin)).json()
        assert listing["phones"][0]["current"] is False


async def test_the_setting_needs_the_security_tier(away_db, told) -> None:
    async with web_client() as c:
        base, admin, _ = await claimed(c)
        assert (await c.put("/api/away/setting", json={"on": False}, headers=base)).status_code in (401, 403)
        no_rw = {k: v for k, v in admin.items() if k != "X-Requested-With"}
        async with web_client(headers={}) as bare:
            assert (await bare.put("/api/away/setting", json={"on": False}, headers=no_rw)).status_code == 403
        async with session_scope() as s:
            assert await away_store.setting_on(s)


async def test_a_new_household_token_cuts_off_every_phone(away_db, told, tmp_path) -> None:
    """H2: changing the household token (the lost-phone step) ends every
    enrolment; a phone sets away chat up again at home with the new token."""
    async with web_client() as c:
        base, admin, _ = await claimed(c)
        p = await phone(c, base)
        await enroll(c, p)
        assert (await _listed(tmp_path))["phones"]
        told.clear()
        r = await c.post("/api/auth/device-token/rotate", headers=admin)
        assert r.status_code == 200, r.text
        assert told == ["reconcile"]                                  # the core was told
        assert (await _listed(tmp_path))["phones"] == {}              # the listener refuses it
        new_base = {**base, "X-Device-Token": r.json()["token"]}
        headers = {**p["headers"], "X-Device-Token": r.json()["token"]}
        assert (await c.get("/api/away/status", headers=headers)).json()["enrolled"] is False
        listing = (await c.get("/api/away/phones", headers={**admin, "X-Device-Token": r.json()["token"]})).json()
        assert listing["phones"][0]["current"] is False
        # Set up again at home with the new token: it counts again.
        r = await c.post("/api/away/enroll", json=p["body"], headers=headers)
        assert r.status_code == 200, r.text
        assert (await _listed(tmp_path))["phones"] == {public_bytes(p["noise"]).hex(): p["id"]}
        # Setting a token of one's own is a change too.
        r = await c.post("/api/auth/device-token", json={"token": "a-household-token-of-ours"},
                         headers={**admin, "X-Device-Token": new_base["X-Device-Token"]})
        assert r.status_code == 200, r.text
        assert (await _listed(tmp_path))["phones"] == {}
