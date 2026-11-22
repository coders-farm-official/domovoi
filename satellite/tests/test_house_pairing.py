"""Ask every house; trust only the one that proves the code (1.4.0).

The device's side: discovery that collects every server instead of the
first, a request parked with each, the proof checked before anything is
believed, the budget, the withdrawal — against fake houses that run the
REAL server half of the exchange (satellite/pairing_proof.py). The core's
side, against the real core session and database, is
domovoi/tests/test_satellite_approval_proof.py.

No network, no database.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import urllib.parse
from contextlib import asynccontextmanager

import pytest

from satellite import _ed25519, discovery, house_pairing, pairing_proof, server_identity
from satellite.discovery import House
from satellite.tests._client_import import import_client
from satellite.tests.test_server_identity import _Resp, _health_opener, _server

client = import_client()

ROOM = "kitchen"
CODE = "481502"
MASTER = "c" * 64
FP1 = "SHA256:" + "1" * 43
FP2 = "SHA256:" + "2" * 43


# ─── fake houses ──────────────────────────────────────────────────────────


class FakeHouse:
    """A Domovoi server as far as the device can tell. ``mode``:

    * ``honest`` — parks; with a code typed, runs the exchange and gives its
      proof only after the device's half checks out;
    * ``ready`` — says ``ready`` without proving anything;
    * ``garbage`` — answers the device's half with a proof that is noise;
    * ``other_fp`` — knows the code, but computes the run for another key
      (what a proof carried from another server looks like);
    * ``replay`` — sends back ``self.replayed`` instead of its own proof;
    * ``chatty`` — answers the hello with ``working`` frames, for ever;
    * ``silent`` — answers nothing at all, and keeps the socket open;
    * ``stall`` — challenges, then answers the device's confirmation with
      ``working`` frames, for ever.

    Like the core it puts a typed code back when the device declines, or
    says nothing within ``confirm_deadline`` (a run with no guess in it);
    ``challenge_delay`` holds its challenge back a moment."""

    def __init__(self, url: str, fp: str, mode: str = "honest", *,
                 confirm_deadline: float = 30.0, challenge_delay: float = 0.0) -> None:
        self.house = House(url=url, fingerprint=fp, proof=True, name=url[-8:], via="name")
        self.mode = mode
        self.entered: str | None = None
        self.hellos: list[dict] = []
        self.withdrawn: list[str] = []
        self.paired: str | None = None
        self.last_proof: str | None = None
        self.replayed: str | None = None
        self.confirm_deadline = confirm_deadline
        self.challenge_delay = challenge_delay
        self.declines = 0
        self.timeouts = 0
        self.challenges = 0

    @staticmethod
    async def _babble(say, from_device: asyncio.Queue) -> None:
        while True:
            await say({"type": "working", "stage": "thinking"})
            try:
                if await asyncio.wait_for(from_device.get(), 0.02) is None:
                    return
            except asyncio.TimeoutError:
                continue

    async def serve(self, hello: dict, to_device: asyncio.Queue, from_device: asyncio.Queue) -> None:
        self.hellos.append(hello)
        say = lambda frame: to_device.put(json.dumps(frame))  # noqa: E731
        if hello.get("approval_withdraw"):
            self.withdrawn.append(hello["pairing_token"])
            await say({"type": "error", "reason": "approval_withdrawn", "withdrawn": True})
            return
        if self.mode == "ready":
            await say({"type": "ready"})
            return
        if self.mode == "chatty":
            await self._babble(say, from_device)
            return
        if self.mode == "silent":
            while await from_device.get() is not None:
                pass
            return
        if self.entered is None:
            await say({"type": "error", "reason": "awaiting_approval", "proof": True})
            return
        code, self.entered = self.entered, None
        fp = "SHA256:" + "Z" * 43 if self.mode == "other_fp" else self.house.fingerprint
        ex = pairing_proof.ServerExchange(code, hello["approval_pake"], room_id=hello["room_id"],
                                          fingerprint=fp)
        if self.challenge_delay:
            await asyncio.sleep(self.challenge_delay)
        self.challenges += 1
        await say({"type": "approval_challenge", "pake": ex.message})
        try:
            raw = await asyncio.wait_for(from_device.get(), self.confirm_deadline)
        except asyncio.TimeoutError:
            self.timeouts += 1
            self.entered = code
            return
        if raw is None:
            self.entered = code
            return
        answer = json.loads(raw)
        if answer.get("type") == "approval_decline":
            self.declines += 1
            self.entered = code
            return
        tag = answer.get("tag")
        if self.mode == "stall":
            await self._babble(say, from_device)
            return
        if self.mode == "garbage":
            await say({"type": "approval_proof", "tag": "ab" * 32})
            return
        if self.mode == "replay":
            await say({"type": "approval_proof", "tag": self.replayed})
            return
        if self.mode == "other_fp":
            ex._device_proved = True      # it skips its own check, to test the device's
            await say({"type": "approval_proof", "tag": ex.proof})
            return
        if not ex.device_proved(tag):
            await say({"type": "error", "reason": "approval_code_mismatch"})
            return
        self.paired = hello["pairing_token"]
        self.last_proof = ex.proof
        await say({"type": "approval_proof", "tag": ex.proof})
        await say({"type": "ready"})


class _DeviceWS:
    def __init__(self, to_core, from_core):
        self.to_core, self.from_core = to_core, from_core

    async def send(self, data):
        await self.to_core.put(data)

    async def recv(self):
        item = await self.from_core.get()
        if item is None:
            raise ConnectionError("closed")
        return item


def _connect(houses: list[FakeHouse], contacted: list[str] | None = None):
    by_url = {h.house.url: h for h in houses}

    @asynccontextmanager
    async def connect(url: str):
        base = url.split("/v1/stream/")[0]
        if contacted is not None:
            contacted.append(base)
        house = by_url.get(base)
        if house is None:
            raise ConnectionRefusedError(base)
        to_core: asyncio.Queue = asyncio.Queue()
        to_device: asyncio.Queue = asyncio.Queue()

        async def run():
            hello = json.loads(await to_core.get())
            await house.serve(hello, to_device, to_core)
            await to_device.put(None)

        task = asyncio.create_task(run())
        try:
            yield _DeviceWS(to_core, to_device)
        finally:
            await to_core.put(None)
            try:
                await asyncio.wait_for(task, 5)
            except Exception:  # noqa: BLE001
                task.cancel()

    return connect


def _coordinator(houses, tmp_path, *, legacy=(), contacted=None, budget=None, hello=None):
    found = [h.house for h in houses] + list(legacy)
    return house_pairing.Coordinator(
        room_id=ROOM, code=CODE, master_token=MASTER,
        hello=hello or (lambda: {"type": "hello", "room_id": ROOM, "approval_code": CODE,
                                 "prev_health": {"private": 1}, "pairing_token": MASTER}),
        discover=lambda: found,
        connect=_connect(houses, contacted),
        budget=budget or house_pairing.ProofBudget(tmp_path / "budget.json"),
    )


def _run(coro):
    return asyncio.run(coro)


# ─── discovery collects every house ────────────────────────────────────────


def test_locate_all_collects_every_house_once_each():
    by_host = {
        "192.168.1.20": House("ws://192.168.1.20:6370", FP1, True, "live"),
        "192.168.1.21": House("ws://192.168.1.21:6370", FP1, True, "live again"),   # same key
        "192.168.1.30": House("ws://192.168.1.30:6370", FP2, True, "test"),
        "192.168.1.31": House("ws://192.168.1.31:6370", None, False, "old"),
    }
    probe = lambda host, **k: by_host.get(host)  # noqa: E731
    swept = [by_host["192.168.1.30"], by_host["192.168.1.31"], by_host["192.168.1.20"]]
    out = discovery.locate_all(
        names=("domovoi.local",), last_known="ws://192.168.1.21:6370", probe_fn=probe,
        resolver=lambda *a: [(None, None, None, None, ("192.168.1.20", 6370))],
        sweeper=lambda **k: swept,
    )
    assert [h.url for h in out] == ["ws://192.168.1.20:6370", "ws://192.168.1.30:6370",
                                    "ws://192.168.1.31:6370"]
    assert [h.via for h in out] == ["name", "scan", "scan"]


def test_locate_all_is_capped():
    swept = [House(f"ws://192.168.1.{i}:6370", f"SHA256:{i:043d}", True) for i in range(40, 60)]
    out = discovery.locate_all(names=(), sweeper=lambda **k: swept, cap=3)
    assert len(out) == 3


def test_the_sweep_collects_instead_of_stopping_at_the_first():
    hits = {"192.168.1.20", "192.168.1.30", "192.168.1.40"}
    probe = lambda h, **k: House(f"ws://{h}:6370", "SHA256:" + h, True) if h in hits else None  # noqa: E731
    hosts = [f"192.168.1.{i}" for i in range(2, 60)]
    found = discovery.find_houses(hosts=hosts, probe_fn=probe)
    assert {h.url for h in found} == {f"ws://{h}:6370" for h in hits}
    assert len(discovery.find_houses(hosts=hosts, probe_fn=probe, cap=2)) == 2


def _opener_with(field, **kw):
    seed, public, fp = _server()
    inner = _health_opener(seed, public, **kw)

    def opener(url, timeout=None):
        doc = json.loads(inner(url, timeout).read())
        if field is not None:
            doc["pairing_proof"] = field
        return _Resp(json.dumps(doc).encode("utf-8"))
    return opener, fp


def _proof_opener(advert, *, strip=False, bind="echo", tamper=None, seed_byte=1):
    """A 1.4.0 core's /v1/health: for a pairing-proof challenge bound to an
    address it SIGNS ``pairing_proof=<advert>`` (``advert=None``: an older
    core, which signs no such line and has no such field). ``strip`` and
    ``tamper`` are a host in the middle: it drops the field, or replaces
    it, after the core signed."""
    seed, public, fp = _server(seed_byte)

    def opener(url, timeout=None):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        challenge = query["challenge"][0]
        dialed = query.get("addr", [None])[0]
        signed_addr = dialed if (dialed is not None and bind == "echo") else None
        line = None
        if (advert is not None and signed_addr is not None
                and challenge.startswith(server_identity.PAIRING_PROOF_CHALLENGE_PREFIX)):
            line = advert
        message = b"domovoi-health-v1\n" + challenge.encode()
        if signed_addr is not None:
            message += b"\n" + signed_addr.encode()
            if line is not None:
                message += b"\npairing_proof=" + str(line).encode()
        block = {
            "algorithm": "ed25519", "fingerprint": fp,
            "public_key": base64.b64encode(public).decode("ascii"),
            "challenge": challenge,
            "signature": base64.b64encode(_ed25519.sign(seed, message)).decode("ascii"),
        }
        if signed_addr is not None:
            block["addr"] = signed_addr
        doc = {"status": "ok", "bot_name": "Domovoi", "identity": block}
        if line is not None:
            doc["pairing_proof"] = line
        if strip:
            doc.pop("pairing_proof", None)
        if tamper is not None:
            doc["pairing_proof"] = tamper
        return _Resp(json.dumps(doc).encode("utf-8"))
    return opener, fp


def test_probe_house_needs_a_signed_advert_and_an_address_bound_key():
    opener, fp = _proof_opener(1)
    house = discovery.probe_house("192.168.1.20", opener=opener)
    assert house.proof and house.fingerprint == fp and house.url == "ws://192.168.1.20:6370"
    assert house.identity == "proved"
    old, fp_old = _proof_opener(None)             # an older core: proves its key, signs no proof
    h = discovery.probe_house("192.168.1.20", opener=old)
    assert h.proof is False and h.fingerprint == fp_old and h.identity == "proved"
    none, _ = _proof_opener(0)                    # a 1.4.0 core without the exchange
    assert discovery.probe_house("192.168.1.20", opener=none).proof is False
    unbound, _ = _proof_opener(1, bind=None)
    assert discovery.probe_house("192.168.1.20", opener=unbound).proof is False
    refused, _ = _opener_with(1, bind="refuse")
    h = discovery.probe_house("192.168.1.20", opener=refused)
    assert h.proof is False and h.fingerprint is None


def test_an_unsigned_advert_proves_nothing():
    """The field outside the signature (the pre-fix core's shape) is not
    believed: anyone in the middle could have added it."""
    unsigned, _ = _opener_with(1)
    h = discovery.probe_house("192.168.1.20", opener=unsigned)
    assert h.proof is False and h.identity == "failed"


@pytest.mark.parametrize("strip, tamper", [(True, None), (False, 0), (False, 2)])
def test_a_stripped_or_changed_advert_breaks_the_signature(strip, tamper):
    """SAT-16 PoC: a host in the middle that strips (or changes) a 1.4.0
    core's ``pairing_proof`` used to pass it off as an older core — and a
    device whose address was typed then asked it the old way. Now the
    advert is signed: the answer does not verify at all."""
    opener, _fp = _proof_opener(1, strip=strip, tamper=tamper)
    h = discovery.probe_house("192.168.1.20", opener=opener)
    assert h.proof is False and h.fingerprint is None and h.identity == "failed"


def test_an_unbound_answer_holds_no_fingerprint_and_does_not_hide_the_real_house():
    """An answer verified but not bound to the address dialled proves no
    identity there: it takes no fingerprint, so the server it names, found
    at its own address, is still the one asked."""
    unbound, fp = _proof_opener(None, bind=None)
    h = discovery.probe_house("192.168.1.31", opener=unbound)
    assert h.fingerprint is None and h.proof is False and h.identity == "unbound"
    real, _ = _proof_opener(1)
    by_host = {"192.168.1.31": h, "192.168.1.20": discovery.probe_house("192.168.1.20", opener=real)}
    out = discovery.locate_all(
        names=("domovoi.local",), probe_fn=lambda host, **k: by_host.get(host),
        resolver=lambda *a: [(None, None, None, None, ("192.168.1.31", 6370))],
        sweeper=lambda **k: [by_host["192.168.1.20"]],
    )
    assert [x.url for x in out if x.proof] == ["ws://192.168.1.20:6370"]
    assert out[0].fingerprint == fp


# ─── parking with every house ──────────────────────────────────────────────


def test_a_request_is_parked_with_every_house_that_can_prove(tmp_path):
    a = FakeHouse("ws://192.168.1.20:6370", FP1)
    b = FakeHouse("ws://192.168.1.30:6370", FP2)
    # An older server: it has an identity (since 1.2) but no pairing_proof.
    old = House("ws://192.168.1.31:6370", "SHA256:" + "0" * 43, False, "old")
    contacted: list[str] = []
    coord = _coordinator([a, b], tmp_path, legacy=[old], contacted=contacted)
    rnd = _run(coord.round())
    assert rnd.approved is None
    assert {h.url for h in rnd.parked} == {a.house.url, b.house.url}
    assert old.url not in contacted, "an old server must never be asked by an unpinned device"
    assert [h.url for h in rnd.legacy] == [old.url]


def test_the_code_and_the_device_token_never_leave_the_device(tmp_path):
    a = FakeHouse("ws://192.168.1.20:6370", FP1)
    b = FakeHouse("ws://192.168.1.30:6370", FP2)
    _run(_coordinator([a, b], tmp_path).round())
    for house in (a, b):
        hello = house.hellos[-1]
        assert "approval_code" not in hello and "prev_health" not in hello
        assert CODE not in json.dumps(hello)
        assert hello["pairing_token"] != MASTER
        assert pairing_proof.decode(hello["approval_pake"]) is not None
    # Each house gets its own token.
    assert a.hellos[-1]["pairing_token"] != b.hellos[-1]["pairing_token"]
    assert a.hellos[-1]["pairing_token"] == house_pairing.house_token(MASTER, FP1)


# ─── only a proven approval counts ─────────────────────────────────────────


def test_the_house_that_proves_the_code_is_chosen_and_the_rest_withdrawn(tmp_path):
    a = FakeHouse("ws://192.168.1.20:6370", FP1)
    b = FakeHouse("ws://192.168.1.30:6370", FP2)
    coord = _coordinator([a, b], tmp_path)
    _run(coord.round())
    b.entered = CODE                           # typed on b's dashboard
    rnd = _run(coord.round())
    assert rnd.approved is not None and rnd.approved.url == b.house.url
    assert b.paired == house_pairing.house_token(MASTER, FP2)
    assert _run(coord.withdraw_others(rnd.approved)) == 1
    assert a.withdrawn == [house_pairing.house_token(MASTER, FP1)]
    assert b.withdrawn == []


@pytest.mark.parametrize("mode", ["ready", "garbage", "other_fp"])
def test_an_approval_without_a_valid_proof_is_ignored(tmp_path, mode, caplog):
    rogue = FakeHouse("ws://192.168.1.66:6370", FP1, mode=mode)
    rogue.entered = CODE        # even one that somehow knows the code, for other_fp
    coord = _coordinator([rogue], tmp_path)
    with caplog.at_level("WARNING"):
        rnd = _run(coord.round())
    assert rnd.approved is None
    assert rnd.visits[rogue.house.url].kind in ("unproven", "bad_proof")
    assert "ignored" in caplog.text


def test_a_proof_replayed_from_another_houses_run_is_refused(tmp_path):
    real = FakeHouse("ws://192.168.1.20:6370", FP1)
    real.entered = CODE
    _run(_coordinator([real], tmp_path / "one").round())
    assert real.last_proof is not None
    thief = FakeHouse("ws://192.168.1.66:6370", FP1, mode="replay")
    thief.replayed = real.last_proof
    thief.entered = "000000"
    rnd = _run(_coordinator([thief], tmp_path / "two").round())
    assert rnd.approved is None and rnd.visits[thief.house.url].kind == "bad_proof"


def test_a_wrong_code_typed_is_a_mismatch_not_an_approval(tmp_path):
    a = FakeHouse("ws://192.168.1.20:6370", FP1)
    a.entered = "000000"
    rnd = _run(_coordinator([a], tmp_path).round())
    assert rnd.approved is None and rnd.visits[a.house.url].kind == "mismatch"
    assert a.paired is None


def test_two_houses_with_the_code_typed_approve_exactly_one(tmp_path):
    a = FakeHouse("ws://192.168.1.20:6370", FP1)
    b = FakeHouse("ws://192.168.1.30:6370", FP2)
    a.entered = b.entered = CODE
    rnd = _run(_coordinator([a, b], tmp_path).round())
    assert rnd.approved is not None
    assert [a.paired, b.paired].count(None) == 1
    kinds = sorted(v.kind for v in rnd.visits.values())
    assert kinds == ["approved", "declined"]
    # The other house was told, and keeps its typed code.
    other = b if a.paired else a
    assert other.declines == 1 and other.entered == CODE


# ─── the budget ────────────────────────────────────────────────────────────


def test_the_budget_counts_answered_challenges_and_survives_a_restart(tmp_path):
    now = [1000.0]
    path = tmp_path / "budget.json"
    budget = house_pairing.ProofBudget(path, clock=lambda: now[0], max_attempts=2, window_sec=60)
    assert budget.spend() and budget.spend()
    assert not budget.spend() and not budget.allow()
    again = house_pairing.ProofBudget(path, clock=lambda: now[0], max_attempts=2, window_sec=60)
    assert not again.allow()
    assert 0 < again.retry_after() <= 60
    now[0] += 61
    assert again.allow()


def test_a_spent_budget_still_visits_and_declines(tmp_path):
    """SAT-15 PoC: a spent budget used to ask NOBODY — every request it had
    parked expired after fifteen minutes, and a code typed meanwhile was
    lost. Now it keeps visiting, and declines the challenge: the house
    keeps the typed code for later and counts nothing."""
    a = FakeHouse("ws://192.168.1.20:6370", FP1)
    budget = house_pairing.ProofBudget(tmp_path / "b.json", max_attempts=1)
    budget.spend()
    a.entered = CODE
    contacted: list[str] = []
    rnd = _run(_coordinator([a], tmp_path, budget=budget, contacted=contacted).round())
    assert rnd.budget_spent and contacted == [a.house.url]
    assert rnd.visits[a.house.url].kind == "declined"
    assert a.declines == 1 and a.entered == CODE and a.paired is None
    assert [h.url for h in rnd.parked] == [a.house.url]


def test_each_wrong_code_costs_one_from_the_budget(tmp_path):
    a = FakeHouse("ws://192.168.1.20:6370", FP1)
    budget = house_pairing.ProofBudget(tmp_path / "b.json", max_attempts=2, hold_base_sec=0)
    coord = _coordinator([a], tmp_path, budget=budget)
    for _ in range(2):
        a.entered = "000000"
        assert _run(coord.round()).visits[a.house.url].kind == "mismatch"
    a.entered = CODE                     # right, but the budget is spent
    rnd = _run(coord.round())
    assert rnd.budget_spent and rnd.approved is None and a.entered == CODE
    assert rnd.visits[a.house.url].kind == "declined"


def test_tokens_are_per_house_and_stable():
    t1 = house_pairing.house_token(MASTER, FP1)
    assert t1 == house_pairing.house_token(MASTER, FP1)
    assert t1 != house_pairing.house_token(MASTER, FP2)
    assert t1 != house_pairing.house_token("d" * 64, FP1)
    assert len(t1) == 64


def test_the_coordinator_wants_a_six_digit_code(tmp_path):
    with pytest.raises(ValueError):
        house_pairing.Coordinator(room_id=ROOM, code="1234", master_token=MASTER, hello=dict,
                                  discover=list, connect=None,
                                  budget=house_pairing.ProofBudget(tmp_path / "b"))


# ─── the client's glue ─────────────────────────────────────────────────────


@pytest.fixture
def config_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[satellite]\ndomovoi_url = "auto"\nroom_id = "kitchen"\n', encoding="utf-8")
    return p


def test_adopting_the_approving_house_pins_it_and_keeps_its_token(tmp_path, config_file, monkeypatch):
    monkeypatch.setattr(server_identity, "PENDING_SERVER_SIDECAR", tmp_path / "pending.json")
    monkeypatch.setattr(client, "PAIRING_TOKEN_SIDECAR", tmp_path / "pairing_token")
    monkeypatch.setattr(client, "_PAIRING_TOKEN_OVERRIDE", None)
    (tmp_path / "pairing_token").write_text(MASTER + "\n", encoding="utf-8")
    import types

    monkeypatch.setattr(client, "APPROVED_MARKER", tmp_path / "approved")
    cfg = types.SimpleNamespace(domovoi_url="auto", server_fingerprint="", room_id=ROOM)
    house = House("ws://192.168.1.30:6370", FP2, True, "test")
    token = house_pairing.house_token(MASTER, FP2)
    client._adopt_approved_house(cfg, config_file, house, token)
    body = config_file.read_text(encoding="utf-8")
    assert 'domovoi_url = "ws://192.168.1.30:6370"' in body
    assert f'server_fingerprint = "{FP2}"' in body
    assert (tmp_path / "pairing_token").read_text(encoding="utf-8").strip() == token
    assert client._effective_pairing_token() == token
    assert cfg.domovoi_url == house.url and cfg.server_fingerprint == FP2


def test_a_typed_address_on_an_old_server_keeps_the_old_flow(monkeypatch):
    """Compatibility: the person typed this server's address, so an
    approval it cannot prove is still accepted the way it always was — the
    single-server flow, not the coordinator."""
    import types

    monkeypatch.setattr(client, "_approval_decision", lambda: (False, "test"))
    monkeypatch.setattr(client, "_probe_one_house", lambda cfg: ("legacy", None))  # an old server
    cfg = types.SimpleNamespace(domovoi_url="ws://192.168.1.20:6370", server_fingerprint="",
                                room_id=ROOM, sat_type="voice")
    sat = types.SimpleNamespace(cfg=cfg, _hello_frame=dict, _house_pairing=None,
                                _one_house_said=None)
    _run(client.Satellite._start_house_pairing(sat))
    assert sat._house_pairing is None and sat._legacy_allowed is True


def test_a_typed_address_on_a_new_server_uses_the_proof(monkeypatch, tmp_path):
    import types

    monkeypatch.setattr(client, "_approval_decision", lambda: (False, "test"))
    monkeypatch.setattr(client, "_probe_one_house",
                        lambda cfg: ("proof", House("ws://192.168.1.20:6370", FP1, True,
                                                    via="address")))
    monkeypatch.setattr(client, "_effective_pairing_token", lambda: MASTER)
    monkeypatch.setattr(client, "_proof_code", lambda: CODE)
    monkeypatch.setattr(client, "APPROVAL_BUDGET_PATH", tmp_path / "b.json")
    cfg = types.SimpleNamespace(domovoi_url="ws://192.168.1.20:6370", server_fingerprint="",
                                room_id=ROOM, sat_type="voice")
    sat = types.SimpleNamespace(cfg=cfg, _hello_frame=dict, _house_pairing=None,
                                _one_house_said=None)
    _run(client.Satellite._start_house_pairing(sat))
    assert isinstance(sat._house_pairing, house_pairing.Coordinator)


def test_an_older_four_digit_code_is_replaced_for_the_proof(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "APPROVAL_CODE_SIDECAR", tmp_path / "approval_code")
    (tmp_path / "approval_code").write_text("1234\n", encoding="utf-8")
    code = client._proof_code()
    assert pairing_proof.valid_code(code)
    assert (tmp_path / "approval_code").read_text(encoding="utf-8").strip() == code
    assert client._proof_code() == code     # and kept


# ─── SAT-14: a visit is bounded as a whole, and adoption is prompt ─────────
#
# Each test is a proof of concept against the code before the fix, where
# VISIT_TIMEOUT_SEC bounded each FRAME: a server that kept sending frames
# froze the round for ever. ``_within`` turns "for ever" into a failure.


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(house_pairing, "VISIT_TIMEOUT_SEC", 2.0, raising=False)
    monkeypatch.setattr(house_pairing, "PROOF_WAIT_SEC", 0.3, raising=False)
    monkeypatch.setattr(house_pairing, "ANSWER_LOCK_WAIT_SEC", 1.5, raising=False)
    monkeypatch.setattr(house_pairing, "ROUND_GRACE_SEC", 0.2, raising=False)


def _within(coro, limit: float):
    async def go():
        return await asyncio.wait_for(coro, limit)
    try:
        return asyncio.run(go())
    except asyncio.TimeoutError:
        pytest.fail(f"still running after {limit} s")


def _visit(house: FakeHouse, budget, code: str = CODE):
    return house_pairing.visit(
        house.house, room_id=ROOM, code=code, hello={}, token="t" * 64,
        connect=_connect([house]), budget=budget,
    )


def test_a_server_that_keeps_talking_cannot_hold_a_visit(tmp_path, quick):
    chatty = FakeHouse("ws://192.168.1.66:6370", FP1, mode="chatty")
    t0 = time.monotonic()
    result = _within(_visit(chatty, house_pairing.ProofBudget(tmp_path / "b.json")), 6)
    assert result.kind == "unreachable"
    assert time.monotonic() - t0 < 4


def test_after_answering_only_the_proof_is_accepted(tmp_path, quick):
    """A server that challenges and then stalls (``working`` frames instead
    of its proof) gets PROOF_WAIT_SEC, not for ever — and is held back."""
    stall = FakeHouse("ws://192.168.1.66:6370", FP1, mode="stall")
    stall.entered = "000000"
    budget = house_pairing.ProofBudget(tmp_path / "b.json")
    result = _within(_visit(stall, budget), 6)
    assert result.kind == "unproven"
    assert budget.held(stall.house) > 0


def test_a_stalled_challenger_does_not_make_the_real_house_time_out(tmp_path, quick):
    """The SAT-14 scenario: a hostile server's challenge took the
    one-exchange lock and kept the socket alive, so the real house's run
    timed out at its confirm deadline (its typed code spent, a failure
    counted) and the device never adopted it."""
    hostile = FakeHouse("ws://192.168.1.66:6370", FP1, mode="stall")
    hostile.entered = "000000"
    real = FakeHouse("ws://192.168.1.20:6370", FP2, confirm_deadline=2.0, challenge_delay=0.1)
    real.entered = CODE
    rnd = _within(_coordinator([hostile, real], tmp_path).round(), 8)
    assert rnd.approved is not None and rnd.approved.url == real.house.url
    assert real.timeouts == 0 and real.paired == house_pairing.house_token(MASTER, FP2)


def test_a_round_returns_as_soon_as_one_house_proves(tmp_path, quick):
    """Adoption waits for nobody: a house that never answers is cut once
    another has proved the code."""
    silent = FakeHouse("ws://192.168.1.66:6370", FP1, mode="silent")
    real = FakeHouse("ws://192.168.1.20:6370", FP2)
    real.entered = CODE
    t0 = time.monotonic()
    rnd = _within(_coordinator([silent, real], tmp_path).round(), 6)
    assert rnd.approved is not None and rnd.approved.url == real.house.url
    assert time.monotonic() - t0 < 1.5, "it waited for the silent house"


def test_adopting_records_the_approval_so_a_restart_is_not_half_paired(
        tmp_path, config_file, monkeypatch):
    """The proof is the approval. Recorded only at the first ``ready``, a
    restart in between came back asking that one server for a proof the
    core no longer runs (the room is bound), ignored its plain ``ready``,
    and stayed half-paired until Reset pairing."""
    import types

    monkeypatch.setattr(server_identity, "PENDING_SERVER_SIDECAR", tmp_path / "pending.json")
    monkeypatch.setattr(client, "PAIRING_TOKEN_SIDECAR", tmp_path / "pairing_token")
    monkeypatch.setattr(client, "_PAIRING_TOKEN_OVERRIDE", None)
    monkeypatch.setattr(client, "APPROVED_MARKER", tmp_path / "approved")
    cfg = types.SimpleNamespace(domovoi_url="auto", server_fingerprint="", room_id=ROOM)
    house = House("ws://192.168.1.30:6370", FP2, True, "test")
    client._adopt_approved_house(cfg, config_file, house, house_pairing.house_token(MASTER, FP2))
    assert client._read_approval_record() == client._APPROVAL_YES


# ─── SAT-15: the budget, and who gets the places ───────────────────────────


def test_the_budget_survives_a_wall_clock_step(tmp_path):
    """A Pi with no battery clock answers challenges while its clock is an
    hour behind, then the clock is set (NTP, or a server it copies the time
    from). The wall-clock stamps then looked an hour old and the budget
    was whole again. It counts running time now."""
    path = tmp_path / "budget.json"
    behind = house_pairing.ProofBudget(path, clock=lambda: time.time() - 3600, max_attempts=2)
    assert behind.spend() and behind.spend()
    after = house_pairing.ProofBudget(path, max_attempts=2)       # clock set; or restarted
    assert not after.allow()


def test_an_unreadable_budget_file_counts_as_spent(tmp_path):
    path = tmp_path / "budget.json"
    path.write_text("{not json", encoding="utf-8")
    budget = house_pairing.ProofBudget(path)
    assert not budget.allow() and not budget.spend()


def test_a_budget_that_cannot_be_saved_answers_nothing(tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    budget = house_pairing.ProofBudget(blocker / "budget.json")   # its folder is a file
    assert budget.spend() is False
    assert not budget.allow()


def test_a_daily_cap_bounds_a_patient_server(tmp_path):
    now = [0.0]
    budget = house_pairing.ProofBudget(tmp_path / "b.json", clock=lambda: now[0], max_attempts=5,
                                       window_sec=1800, daily_max=20, hold_base_sec=0)
    answered = 0
    while now[0] < 86400 - 1800:
        while budget.spend():
            answered += 1
        now[0] += 1800
    assert answered == 20


def test_a_server_whose_run_proved_nothing_is_held_for_a_growing_while(tmp_path):
    now = [0.0]
    budget = house_pairing.ProofBudget(tmp_path / "b.json", clock=lambda: now[0],
                                       max_attempts=50, daily_max=50)
    a = FakeHouse("ws://192.168.1.20:6370", FP1)
    coord = _coordinator([a], tmp_path, budget=budget)
    a.entered = "000000"
    assert _run(coord.round()).visits[a.house.url].kind == "mismatch"
    a.entered = CODE
    rnd = _run(coord.round())
    assert rnd.visits[a.house.url].kind == "declined" and a.entered == CODE
    now[0] += house_pairing.HOLD_BASE_SEC + 1
    rnd = _run(coord.round())
    assert rnd.approved is not None
    # The hold doubles, and follows the address as well as the key.
    budget.fail(a.house)
    budget.fail(a.house)
    assert budget.held(a.house) > house_pairing.HOLD_BASE_SEC * 1.5
    assert budget.held(House(a.house.url, "SHA256:" + "9" * 43, True)) > 0


def test_arrival_order_does_not_decide_who_is_asked(tmp_path):
    """Twenty hosts that answer the sweep first used to fill all eight
    places, for good. The places rotate now."""
    crowd = [House(f"ws://192.168.1.{i}:6370", f"SHA256:{i:043d}", True, via="scan")
             for i in range(100, 120)]
    real = House("ws://192.168.1.250:6370", FP1, True, via="scan")       # answered last
    coord = _coordinator([], tmp_path)
    coord._discover = lambda: crowd + [real]
    seen = False
    for _ in range(3):           # 21 servers, 8 places: everyone within three looks
        _run(coord.discover_now())
        assert len(coord.houses) == discovery.MAX_HOUSES
        seen = seen or real.url in [h.url for h in coord.houses]
    assert seen


def test_hosts_that_prove_nothing_take_no_place():
    legacy = [House(f"ws://192.168.1.{i}:6370", None, False, via="scan") for i in range(100, 110)]
    real = House("ws://192.168.1.20:6370", FP1, True, via="scan")
    out = discovery.locate_all(names=(), sweeper=lambda **k: legacy + [real], cap=8)
    assert real.url in [h.url for h in out if h.proof]


def test_a_name_or_last_known_server_always_has_a_place():
    crowd = [House(f"ws://192.168.1.{i}:6370", f"SHA256:{i:043d}", True, via="scan")
             for i in range(100, 130)]
    named = House("ws://192.168.1.20:6370", FP1, True, via="name")
    last = House("ws://192.168.1.21:6370", FP2, True, via="last-known")
    for rotation in range(6):
        chosen = discovery.choose_houses(crowd + [named, last], cap=8, rotation=rotation)
        urls = [h.url for h in chosen]
        assert named.url in urls and last.url in urls and len(chosen) == 8


def test_the_sweep_collects_every_answer():
    hits = {f"192.168.1.{i}" for i in range(100, 120)}
    probe = lambda h, **k: House(f"ws://{h}:6370", "SHA256:" + h, True) if h in hits else None  # noqa: E731
    found = discovery.find_houses(hosts=[f"192.168.1.{i}" for i in range(2, 200)], probe_fn=probe)
    assert len(found) == len(hits)


# ─── SAT-16: the advert is signed, and remembered ──────────────────────────


def _typed(monkeypatch, tmp_path, fp):
    import types

    monkeypatch.setattr(client, "_approval_decision", lambda: (False, "test"))
    monkeypatch.setattr(client, "_pinned_fingerprint", lambda cfg: (fp, "config"))
    monkeypatch.setattr(client, "APPROVAL_PROOF_SERVERS_PATH", tmp_path / "proof-servers.json")
    monkeypatch.setattr(client, "APPROVAL_CODE_SIDECAR", tmp_path / "approval_code")
    (tmp_path / "approval_code").write_text(CODE + "\n", encoding="utf-8")
    cfg = types.SimpleNamespace(domovoi_url="ws://192.168.1.20:6370", server_fingerprint=fp,
                                room_id=ROOM, sat_type="voice")
    return cfg, types.SimpleNamespace(cfg=cfg, _hello_frame=dict, _house_pairing=None,
                                      _one_house_said=None)


def test_a_stripped_advert_never_sends_the_code_the_old_way(monkeypatch, tmp_path):
    """SAT-16 PoC: a device whose server was chosen for it (a typed address,
    a pinned key) asked that server ONCE per boot whether it proves
    approvals; a host in the middle that stripped ``pairing_proof`` from
    that one answer made it ask the old way — its code in the hello, a
    bare ``ready`` believed. Now the stripped answer does not verify, and
    the device sends nothing until it gets one that does."""
    import urllib.request

    opener, fp = _proof_opener(1, strip=True)
    monkeypatch.setattr(urllib.request, "urlopen", opener)
    _cfg, sat = _typed(monkeypatch, tmp_path, fp)
    _run(client.Satellite._start_house_pairing(sat))
    assert sat._house_pairing is None
    assert getattr(sat, "_legacy_allowed", True) is False, "it would ask the old way"


def test_a_server_seen_proving_is_never_asked_the_old_way_again(monkeypatch, tmp_path):
    import urllib.request

    opener, fp = _proof_opener(1)
    monkeypatch.setattr(urllib.request, "urlopen", opener)
    cfg, _sat = _typed(monkeypatch, tmp_path, fp)
    status, house = client._probe_one_house(cfg)
    assert status == "proof" and house.fingerprint == fp
    # Later the same key answers WITHOUT the advert (signed that way, so it
    # verifies): a downgrade — never the old way for it again.
    older, _ = _proof_opener(None)
    monkeypatch.setattr(urllib.request, "urlopen", older)
    assert client._probe_one_house(cfg)[0] == "unknown"
    assert client._legacy_approval_code(cfg) is None


def test_an_older_server_that_proves_its_key_keeps_the_old_flow(monkeypatch, tmp_path):
    import urllib.request

    older, fp = _proof_opener(None)
    monkeypatch.setattr(urllib.request, "urlopen", older)
    cfg, _sat = _typed(monkeypatch, tmp_path, fp)
    assert client._probe_one_house(cfg) == ("legacy", None)
    assert client._legacy_approval_code(cfg) == CODE


def test_the_old_way_is_decided_again_before_every_session(monkeypatch, tmp_path):
    """Not once per boot: a server that cannot prove an approval at boot,
    and can a minute later, is asked with the proof from then on."""
    answers = iter([("legacy", None),
                    ("proof", House("ws://192.168.1.20:6370", FP1, True, via="address"))])
    monkeypatch.setattr(client, "_probe_one_house", lambda cfg: next(answers))
    _cfg, sat = _typed(monkeypatch, tmp_path, FP1)
    monkeypatch.setattr(client, "_effective_pairing_token", lambda: MASTER)
    monkeypatch.setattr(client, "APPROVAL_BUDGET_PATH", tmp_path / "b.json")
    sat._start_house_pairing = lambda: client.Satellite._start_house_pairing(sat)
    _run(client.Satellite._start_house_pairing(sat))                      # at boot
    assert _run(client.Satellite._before_legacy_session(sat)) == "legacy"  # the first session
    assert _run(client.Satellite._before_legacy_session(sat)) == "proof"   # asked again
    assert isinstance(sat._house_pairing, house_pairing.Coordinator)


def test_an_unverified_answer_still_connects_but_without_the_code(monkeypatch, tmp_path):
    """No answer, or one that does not verify, must not strand the device
    (a server that moved is found again by the failing session's own
    relocation) — but its hello carries no code: only a server that SIGNED
    that it cannot prove an approval is asked the old way."""
    monkeypatch.setattr(client, "_probe_one_house", lambda cfg: ("unknown", None))
    _cfg, sat = _typed(monkeypatch, tmp_path, FP1)
    sat._start_house_pairing = lambda: client.Satellite._start_house_pairing(sat)
    _run(client.Satellite._start_house_pairing(sat))
    assert _run(client.Satellite._before_legacy_session(sat)) == "legacy"
    assert _run(client.Satellite._before_legacy_session(sat)) == "legacy"
    assert sat._legacy_allowed is False
    monkeypatch.setattr(client, "_probe_one_house", lambda cfg: ("legacy", None))
    assert _run(client.Satellite._before_legacy_session(sat)) == "legacy"
    assert sat._legacy_allowed is True
