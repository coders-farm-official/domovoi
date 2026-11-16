"""Ask every house; trust only the one that proves the code (1.3.1).

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
import json
from contextlib import asynccontextmanager

import pytest

from satellite import discovery, house_pairing, pairing_proof, server_identity
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
    * ``replay`` — sends back ``self.replayed`` instead of its own proof."""

    def __init__(self, url: str, fp: str, mode: str = "honest") -> None:
        self.house = House(url=url, fingerprint=fp, proof=True, name=url[-8:], via="name")
        self.mode = mode
        self.entered: str | None = None
        self.hellos: list[dict] = []
        self.withdrawn: list[str] = []
        self.paired: str | None = None
        self.last_proof: str | None = None
        self.replayed: str | None = None

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
        if self.entered is None:
            await say({"type": "error", "reason": "awaiting_approval", "proof": True})
            return
        code, self.entered = self.entered, None
        fp = "SHA256:" + "Z" * 43 if self.mode == "other_fp" else self.house.fingerprint
        ex = pairing_proof.ServerExchange(code, hello["approval_pake"], room_id=hello["room_id"],
                                          fingerprint=fp)
        await say({"type": "approval_challenge", "pake": ex.message})
        raw = await from_device.get()
        if raw is None:
            return
        tag = json.loads(raw).get("tag")
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


def test_probe_house_needs_the_advert_and_an_address_bound_key():
    opener, fp = _opener_with(1)
    house = discovery.probe_house("192.168.1.20", opener=opener)
    assert house.proof and house.fingerprint == fp and house.url == "ws://192.168.1.20:6370"
    for advert in (None, True, "1", 0):
        old, _ = _opener_with(advert)
        assert discovery.probe_house("192.168.1.20", opener=old).proof is False
    unbound, _ = _opener_with(1, bind=None)
    assert discovery.probe_house("192.168.1.20", opener=unbound).proof is False
    refused, _ = _opener_with(1, bind="refuse")
    h = discovery.probe_house("192.168.1.20", opener=refused)
    assert h.proof is False and h.fingerprint is None


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
    assert kinds == ["aborted", "approved"]


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


def test_a_spent_budget_asks_nobody(tmp_path):
    a = FakeHouse("ws://192.168.1.20:6370", FP1)
    budget = house_pairing.ProofBudget(tmp_path / "b.json", max_attempts=1)
    budget.spend()
    contacted: list[str] = []
    rnd = _run(_coordinator([a], tmp_path, budget=budget, contacted=contacted).round())
    assert rnd.budget_spent and contacted == []


def test_each_wrong_code_costs_one_from_the_budget(tmp_path):
    a = FakeHouse("ws://192.168.1.20:6370", FP1)
    budget = house_pairing.ProofBudget(tmp_path / "b.json", max_attempts=2)
    coord = _coordinator([a], tmp_path, budget=budget)
    for _ in range(2):
        a.entered = "000000"
        assert _run(coord.round()).visits[a.house.url].kind == "mismatch"
    a.entered = CODE                     # right, but the budget is spent
    rnd = _run(coord.round())
    assert rnd.budget_spent and rnd.approved is None and a.entered == CODE


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
    monkeypatch.setattr(client, "_one_house", lambda cfg: None)     # an old server there
    cfg = types.SimpleNamespace(domovoi_url="ws://192.168.1.20:6370", server_fingerprint="",
                                room_id=ROOM, sat_type="voice")
    sat = types.SimpleNamespace(cfg=cfg, _hello_frame=dict, _house_pairing=None)
    _run(client.Satellite._start_house_pairing(sat))
    assert sat._house_pairing is None


def test_a_typed_address_on_a_new_server_uses_the_proof(monkeypatch, tmp_path):
    import types

    monkeypatch.setattr(client, "_approval_decision", lambda: (False, "test"))
    monkeypatch.setattr(client, "_one_house",
                        lambda cfg: House("ws://192.168.1.20:6370", FP1, True, via="address"))
    monkeypatch.setattr(client, "_effective_pairing_token", lambda: MASTER)
    monkeypatch.setattr(client, "_proof_code", lambda: CODE)
    monkeypatch.setattr(client, "APPROVAL_BUDGET_PATH", tmp_path / "b.json")
    cfg = types.SimpleNamespace(domovoi_url="ws://192.168.1.20:6370", server_fingerprint="",
                                room_id=ROOM, sat_type="voice")
    sat = types.SimpleNamespace(cfg=cfg, _hello_frame=dict, _house_pairing=None)
    _run(client.Satellite._start_house_pairing(sat))
    assert isinstance(sat._house_pairing, house_pairing.Coordinator)


def test_an_older_four_digit_code_is_replaced_for_the_proof(monkeypatch, tmp_path):
    monkeypatch.setattr(client, "APPROVAL_CODE_SIDECAR", tmp_path / "approval_code")
    (tmp_path / "approval_code").write_text("1234\n", encoding="utf-8")
    code = client._proof_code()
    assert pairing_proof.valid_code(code)
    assert (tmp_path / "approval_code").read_text(encoding="utf-8").strip() == code
    assert client._proof_code() == code     # and kept
