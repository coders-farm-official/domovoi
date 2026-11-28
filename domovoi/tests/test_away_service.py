"""Away chat's onion in the Tor plan (tor_config) and the core's reconcile.

DB-free. The plan rules (the S1 rules from spike S1, applied to
``hs-away``): absent until the first phone has enrolled, the first one is a
restart (never a reload), later phones ride the batched reloads; Tor runs
for it with no friends and Friend Houses off; "never" or "Chat from away"
off removes it. The reconcile reuses test_friend_houses_service's fake
house (Docker and the database faked), run as on Linux.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from domovoi.away import keys as away_keys
from domovoi.away import listener as al
from domovoi.away.store import Phone, tor_auth_raw
from domovoi.friend_houses import service as svc
from domovoi.friend_houses import tor_config as tc
from domovoi.tests.friend_houses_testkit import no_tasks_left_behind  # noqa: F401  (autouse)
from domovoi.tests.test_friend_houses_service import _friend, house  # noqa: F401  (fixture)

TOR_KEY = "A" * 52


def _phone(n: int) -> Phone:
    return Phone(f"android-{n}", TOR_KEY[:-1] + "ABCDEFGH"[n % 8], bytes([n]) * 32)


# ─── the plan (pure) ─────────────────────────────────────────────────────


def test_no_phone_no_away_onion() -> None:
    assert tc.wanted(enabled=False, internet_policy="always", friends=0, open_invites=0,
                     away_on=True, away_phones=0) == tc.Wanted(False, False, False)


def test_one_phone_runs_tor_for_away_alone() -> None:
    w = tc.wanted(enabled=False, internet_policy="always", friends=0, open_invites=0,
                  away_on=True, away_phones=1)
    assert (w.run, w.main_onion, w.pairing_onion, w.away_onion) == (True, False, False, True)
    torrc = tc.render_torrc(w)
    assert f"HiddenServiceDir {tc.DATA_MOUNT}/hs-away" in torrc
    assert f"HiddenServicePort 80 unix:{tc.RUN_MOUNT}/listen/away.sock" in torrc
    assert "hs-main" not in torrc and "hs-pairing" not in torrc


@pytest.mark.parametrize("policy,on", [("never", True), ("always", False)])
def test_never_or_away_off_removes_it(policy: str, on: bool) -> None:
    w = tc.wanted(enabled=True, internet_policy=policy, friends=1, open_invites=0,
                  away_on=on, away_phones=3)
    assert not w.away_onion
    if policy == "never":
        assert not w.run


def test_friends_unchanged_by_away() -> None:
    w = tc.wanted(enabled=True, internet_policy="sometimes", friends=2, open_invites=1)
    assert (w.run, w.main_onion, w.pairing_onion, w.away_onion) == (True, True, True, False)


def test_the_first_phone_is_a_restart_later_ones_a_reload() -> None:
    friends = tc.wanted(enabled=True, internet_policy="always", friends=1, open_invites=0)
    with_away = tc.wanted(enabled=True, internet_policy="always", friends=1, open_invites=0,
                          away_on=True, away_phones=1)
    two = tc.wanted(enabled=True, internet_policy="always", friends=1, open_invites=0,
                    away_on=True, away_phones=2)
    assert tc.plan_change(friends, with_away) == "restart"      # never a reload (S1)
    assert tc.plan_change(with_away, two) == "reload"           # batched
    assert tc.plan_change(two, friends) == "restart"            # away off / last phone gone
    assert tc.plan_change(None, with_away) == "start"
    alone = tc.wanted(enabled=False, internet_policy="always", friends=0, open_invites=0,
                      away_on=True, away_phones=1)
    assert tc.plan_change(alone, tc.wanted(enabled=False, internet_policy="never", friends=0,
                                           open_invites=0, away_on=True, away_phones=1)) == "stop"


def test_phone_keys_have_a_folder_of_their_own(tmp_path) -> None:
    folder = tc.TorFolder(tmp_path / "tor")
    folder.ensure()
    folder.authorize_phone("p" + "a" * 31, TOR_KEY)
    auth = folder.data / "hs-away" / "authorized_clients" / ("p" + "a" * 31 + ".auth")
    assert auth.read_text() == f"descriptor:x25519:{TOR_KEY}\n"
    assert folder.phones_authorized() == {"p" + "a" * 31}
    assert folder.authorized() == set()                        # never hs-main's
    folder.deauthorize_phone("p" + "a" * 31)
    assert folder.phones_authorized() == set()
    with pytest.raises(ValueError):
        folder.authorize_phone("../x", TOR_KEY)
    assert folder.away_socket.parent == folder.listen


def test_a_phone_file_id_follows_tors_rule() -> None:
    for device in ("android-abc", "browser:Ünï", "x" * 64):
        fid = Phone(device, TOR_KEY, bytes(32)).file_id
        assert len(fid) == 32 and fid.isalnum() and fid == fid.lower()


def test_tor_auth_key_shape() -> None:
    assert tor_auth_raw(TOR_KEY) == bytes(32)
    for bad in ("A" * 51, "A" * 53, "a" * 52, "A" * 51 + "1", "A" * 51 + "=", 5, None):
        assert tor_auth_raw(bad) is None


def test_the_onion_key_tor_gets_is_the_address_the_phone_got(tmp_path) -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from domovoi.friend_houses import identity as fid

    seed = bytes(range(32))
    away_keys.write_tor_keys(tmp_path, seed)
    secret = (tmp_path / "hs_ed25519_secret_key").read_bytes()
    assert secret[:32] == b"== ed25519v1-secret: type0 ==\x00\x00\x00" and len(secret) == 96
    public = (tmp_path / "hs_ed25519_public_key").read_bytes()
    assert public[32:] == Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
    address = (tmp_path / "hostname").read_text().strip()
    assert address == away_keys.onion_address(seed) and fid.parse_onion(address) == public[32:]
    # The expanded secret's scalar half is clamped as ed25519 does.
    assert secret[32] & 7 == 0 and secret[63] & 0x80 == 0 and secret[63] & 0x40


# ─── the reconcile ───────────────────────────────────────────────────────


@pytest.fixture
def away(house, tmp_path, monkeypatch):  # noqa: F811 — the imported fixture
    from domovoi import admin_auth

    monkeypatch.setattr(svc, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(svc, "_start_away_watch", lambda: None)
    monkeypatch.setitem(svc._AWAY, "state", None)
    monkeypatch.setitem(svc._AWAY, "authorized", frozenset())
    monkeypatch.setitem(svc._AWAY, "keys", frozenset())
    monkeypatch.setitem(svc._AWAY, "publishing", {})
    monkeypatch.setitem(svc._LISTENER, "mode", (True, False))
    return house


def _tor(house):
    return house.root / "friend-houses" / "tor" / "data"


async def test_the_first_phone_starts_tor_with_its_key_already_there(away) -> None:
    away.phones.append(_phone(1))
    await svc.reconcile()
    assert away.docker.calls == ["start"]
    tor = _tor(away)
    assert "hs-away" in (tor / "torrc").read_text() and "hs-main" not in (tor / "torrc").read_text()
    # The client key, and the onion key the phone was told, are on disk
    # BEFORE Tor first starts: never an open onion.
    assert (tor / "hs-away" / "authorized_clients" / f"{_phone(1).file_id}.auth").exists()
    assert (tor / "hs-away" / "hostname").read_text().strip() == away_keys.onion_address(away_keys.onion_seed())
    phones = json.loads((away.root / "friend-houses" / "listener" / al.PHONES_FILE).read_text())
    assert phones == {"on": True, "phones": {(bytes([1]) * 32).hex(): "android-1"}}
    # Tor has its key; the descriptor carrying it is still on its way.
    assert svc.away_status("android-1") == "publishing" and svc.away_status("android-2") == "starting"
    # Friend Houses stays off: no house keys, its status says off.
    assert not (away.root / "friend-houses" / "house-keys.json").exists()
    assert svc.status()["status"] == "off"


async def test_a_second_phone_is_a_batched_reload_and_removal_too(away) -> None:
    away.phones.append(_phone(1))
    await svc.reconcile()
    away.phones.append(_phone(2))
    await svc.reconcile()
    away.phones.pop(0)
    await svc.reconcile()
    assert away.docker.calls == ["start", "reload", "reload"]
    assert _tor(away).joinpath("hs-away", "authorized_clients").exists()
    assert {p.stem for p in (_tor(away) / "hs-away" / "authorized_clients").glob("*.auth")} == {_phone(2).file_id}


async def test_away_off_or_never_takes_it_away(away) -> None:
    away.phones.append(_phone(1))
    await svc.reconcile()
    away.away["on"] = False
    await svc.reconcile()
    assert away.docker.calls == ["start", "stop"]          # nothing else to do: stopped
    assert svc.away_status("android-1") is None
    away.away["on"] = True
    await svc.reconcile()
    away.policy["value"] = "never"
    await svc.reconcile()
    assert away.docker.calls == ["start", "stop", "start", "stop"]


async def test_with_friends_too_the_phones_change_restarts_once(away) -> None:
    away.enable()
    away.friends.append(_friend(1))
    await svc.reconcile()
    away.phones.append(_phone(1))
    await svc.reconcile()
    assert away.docker.calls == ["start", "restart"]
    torrc = (_tor(away) / "torrc").read_text()
    assert "hs-main" in torrc and "hs-away" in torrc
    away.away["on"] = False
    await svc.reconcile()
    assert away.docker.calls == ["start", "restart", "restart"]
    assert "hs-away" not in (_tor(away) / "torrc").read_text()


async def test_the_listener_serves_away_alone_without_the_house_keys(away, monkeypatch) -> None:
    modes = []

    async def ensure(_folder, mode=(True, False)):
        modes.append(mode)

    monkeypatch.setattr(svc, "_ensure_listener", ensure)
    away.phones.append(_phone(1))
    await svc.reconcile()
    assert modes == [(False, True)]
    monkeypatch.setitem(svc._LISTENER, "mode", (False, True))
    monkeypatch.setitem(svc._LISTENER, "image", "sha256:img")
    command = svc._listener_command(tc.TorFolder(away.root / "tor"))
    assert "--away-socket" in command and "--keys" not in command
    assert any(a.endswith(":/etc/fh/away-noise.key:ro") for a in command)
    assert not any("house-keys.json" in a for a in command)


async def test_the_listener_files_say_nobody_when_the_database_fails(away, monkeypatch) -> None:
    async def down():
        raise OSError("database unavailable")

    folder = away.root / "friend-houses" / "listener"
    folder.mkdir(parents=True)
    monkeypatch.setattr(svc, "_away_inputs", down)
    await svc._write_phones(folder)
    assert json.loads((folder / al.PHONES_FILE).read_text()) == {"on": False, "phones": {}}


# ─── the core's two routes for the web ───────────────────────────────────


async def test_the_web_asking_twice_queues_one_reconcile(monkeypatch) -> None:
    import asyncio

    from domovoi.away import routes

    gate, calls = asyncio.Event(), []

    async def reconcile():
        calls.append(1)
        await gate.wait()

    monkeypatch.setattr(svc, "reconcile", reconcile)
    monkeypatch.setitem(routes._KICK, "task", None)
    assert await routes.away_reconcile() == {"ok": True}
    await routes.away_reconcile()
    await asyncio.sleep(0)
    assert calls == [1]
    gate.set()
    await routes._KICK["task"]
    await routes.away_reconcile()
    await routes._KICK["task"]
    assert calls == [1, 1]


async def test_the_state_route_says_what_the_core_knows(monkeypatch) -> None:
    from domovoi.away import routes

    monkeypatch.setitem(svc._AWAY, "state", "ready")
    monkeypatch.setitem(svc._AWAY, "authorized", frozenset({_phone(1).file_id, _phone(2).file_id}))
    monkeypatch.setitem(svc._AWAY, "publishing", {_phone(2).file_id: (svc.time.monotonic() + 60, "")})
    assert await routes.away_state("android-1") == {"state": "ready"}
    assert await routes.away_state("android-2") == {"state": "publishing"}
    assert await routes.away_state("android-9") == {"state": "starting"}  # its key waits for a reload
    monkeypatch.setitem(svc._AWAY, "state", None)
    assert await routes.away_state("android-1") == {"state": None}



async def test_no_torrc_names_the_away_onion_without_a_client_key_on_disk(away, monkeypatch) -> None:
    """L2: the client keys are written before the torrc, and an away onion
    whose keys aren't on disk is never configured (it would be open)."""
    monkeypatch.setattr(svc, "_retry_reconcile_later", lambda: None)
    monkeypatch.setattr(tc.TorFolder, "authorize_phone", lambda self, *a: None)   # a write that "failed"
    away.phones.append(_phone(1))
    await svc.reconcile()
    torrc = _tor(away) / "torrc"
    assert not torrc.exists() or "hs-away" not in torrc.read_text()
    assert away.docker.calls == []
    assert svc._STATE["status"] == "error"


# ─── "publishing": a new key's descriptor on its way ─────────────────────


@pytest.fixture
def clock(monkeypatch):
    """The service's monotonic clock, moved by hand."""
    import time as real_time

    now = [1000.0]
    monkeypatch.setattr(svc, "time", SimpleNamespace(monotonic=lambda: now[0], time=real_time.time))
    return now


async def test_a_new_key_is_publishing_for_its_window_then_ready(away, clock) -> None:
    away.phones.append(_phone(1))
    await svc.reconcile()                                            # a start: the longer window
    assert svc.away_status("android-1") == "publishing"
    clock[0] += svc.AWAY_PUBLISH_AFTER_START_SEC - 1
    assert svc.away_status("android-1") == "publishing"
    clock[0] += 2
    assert svc.away_status("android-1") == "ready"
    away.phones.append(_phone(2))
    await svc.reconcile()                                            # a batched reload
    assert away.docker.calls == ["start", "reload"]
    assert (svc.away_status("android-1"), svc.away_status("android-2")) == ("ready", "publishing")
    clock[0] += svc.AWAY_PUBLISH_AFTER_RELOAD_SEC + 1
    assert svc.away_status("android-2") == "ready"
    # Enrolling again with a new Tor key: that phone waits out a window again.
    away.phones[1] = Phone("android-2", "B" * 52, bytes([9]) * 32)
    await svc.reconcile()
    assert (svc.away_status("android-1"), svc.away_status("android-2")) == ("ready", "publishing")
    # A reload with nothing new starts no window.
    clock[0] += svc.AWAY_PUBLISH_AFTER_RELOAD_SEC + 1
    await svc.reconcile()
    assert (svc.away_status("android-1"), svc.away_status("android-2")) == ("ready", "ready")
    # The house without a device asked about: the onion's own state.
    assert svc.away_status(None) == "ready"


async def test_a_restart_republishes_every_phone(away, clock) -> None:
    away.phones.append(_phone(1))
    await svc.reconcile()
    clock[0] += svc.AWAY_PUBLISH_AFTER_START_SEC + 1
    assert svc.away_status("android-1") == "ready"
    away.enable()
    away.friends.append(_friend(1))                                  # the house onion appears
    await svc.reconcile()
    assert away.docker.calls == ["start", "restart"]
    assert svc.away_status("android-1") == "publishing"


async def test_a_request_from_the_phone_ends_publishing_early(away, clock) -> None:
    away.phones.append(_phone(1))
    await svc.reconcile()
    svc.away_reached("android-1", (bytes([7]) * 32).hex())           # not its key: no proof
    svc.away_reached("android-2", (bytes([1]) * 32).hex())
    assert svc.away_status("android-1") == "publishing"
    svc.away_reached("android-1", (bytes([1]) * 32).hex())
    assert svc.away_status("android-1") == "ready"


async def test_the_away_onion_going_forgets_the_windows(away, clock) -> None:
    away.phones.append(_phone(1))
    await svc.reconcile()
    away.away["on"] = False
    await svc.reconcile()
    assert svc._AWAY["publishing"] == {} and svc._AWAY["keys"] == frozenset()
    assert svc.away_status("android-1") is None


# ─── the last phone leaving (S1: an onion with no clients is open) ────────


async def test_the_last_phone_leaving_stops_tor_when_nothing_else_runs(away) -> None:
    away.phones.append(_phone(1))
    await svc.reconcile()
    away.phones.clear()                                              # what away.leave leaves behind
    await svc.reconcile()
    assert away.docker.calls == ["start", "stop"]                    # never a reload with no clients
    assert svc.away_status("android-1") is None and svc._AWAY["authorized"] == frozenset()


async def test_the_last_phone_leaving_restarts_tor_without_hs_away(away) -> None:
    away.enable()
    away.friends.append(_friend(1))
    away.phones.append(_phone(1))
    await svc.reconcile()
    assert "hs-away" in (_tor(away) / "torrc").read_text()
    away.phones.clear()
    await svc.reconcile()
    assert away.docker.calls == ["start", "restart"]                 # never a reload
    assert "hs-away" not in (_tor(away) / "torrc").read_text()
    assert tc.TorFolder(_tor(away).parent).phones_authorized() == set()
    phones = json.loads((away.root / "friend-houses" / "listener" / al.PHONES_FILE).read_text())
    assert phones == {"on": True, "phones": {}}
