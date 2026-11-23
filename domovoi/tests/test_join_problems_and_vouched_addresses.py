"""Two things a room's join needs the server to say (1.3.0):

* **addresses the host vouches for** — a core in a VM or a NAT'd distro is
  reached at the HOST's LAN address, which no interface of the core owns.
  The host can vouch for it (``server_identity.VOUCHED_ADDRESS_SOURCES``:
  today the root-owned network-name report), and the core then signs its
  identity for that address — and for no other;
* **why a room couldn't join** — said in Settings → Rooms
  (``domovoi/join_problems.py``) instead of only in the journal.

Plus the health answer's ``pairing_proof`` advert and the approve route's
answer for a device that checks the code itself. No database.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from domovoi import admin_auth, join_problems, main, server_identity
from satellite import pairing_proof
from satellite import server_identity as sat_identity


@pytest.fixture(autouse=True)
def identity_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_auth, "CONFIG_DIR", tmp_path)
    server_identity.reset_cache()
    join_problems.clear()
    yield server_identity.load_or_create()
    server_identity.reset_cache()
    join_problems.clear()


@pytest.fixture
def healthy_core(monkeypatch):
    @asynccontextmanager
    async def fake_scope():
        class _S:
            async def execute(self, *_a, **_k):
                return None
        yield _S()

    monkeypatch.setattr(main, "session_scope", fake_scope)
    monkeypatch.setattr(main, "HANDLERS", [object()])


def _nobody_binds(_ip):
    return False


# ─── addresses the host vouches for ────────────────────────────────────────


def test_a_vouched_address_is_ours_and_an_arbitrary_one_is_not():
    ours = server_identity.dialed_address_is_ours
    vouched = {"192.168.1.30"}
    assert ours("192.168.1.30:6370", can_bind=_nobody_binds, vouched=vouched) is True
    # A bare vouched IP is the host's address at the CORE's port (SAT-13).
    assert ours("192.168.1.30:6371", can_bind=_nobody_binds, vouched=vouched) is False
    assert ours("192.168.1.31:6370", can_bind=_nobody_binds, vouched=vouched) is False
    assert ours("192.168.1.30:6370", can_bind=_nobody_binds, vouched=set()) is False


def test_a_vouched_name_is_never_honoured(monkeypatch):
    """Names are never resolved to decide, and a source cannot smuggle one."""
    monkeypatch.setattr(server_identity, "VOUCHED_ADDRESS_SOURCES",
                        [lambda: ["evil.local", "192.168.1.30", "0.0.0.0", "not an ip"]])
    assert server_identity.host_vouched_addresses() == {"192.168.1.30"}
    assert server_identity.dialed_address_is_ours("evil.local:6370", can_bind=_nobody_binds) is False


def test_a_broken_source_vouches_for_nothing(monkeypatch):
    def broken():
        raise OSError("unreadable")

    monkeypatch.setattr(server_identity, "VOUCHED_ADDRESS_SOURCES",
                        [broken, lambda: ["192.168.1.30"]])
    assert server_identity.host_vouched_addresses() == {"192.168.1.30"}


def test_the_hosts_network_name_report_is_a_source(monkeypatch):
    from domovoi.lan_name import service

    monkeypatch.setattr(service, "read_report",
                        lambda: ("windows", {"addresses": ["192.168.1.30"]}))
    assert "192.168.1.30" in server_identity.host_vouched_addresses()
    monkeypatch.setattr(service, "read_report", lambda: None)
    assert server_identity.host_vouched_addresses() == set()


@pytest.mark.asyncio
async def test_health_signs_for_a_vouched_address_and_refuses_any_other(
    healthy_core, identity_in_tmp, monkeypatch,
):
    monkeypatch.setattr(server_identity, "VOUCHED_ADDRESS_SOURCES", [lambda: ["192.0.2.30"]])
    challenge = sat_identity.new_challenge()
    doc = await main.health(challenge=challenge, addr="192.0.2.30:6370")
    assert sat_identity.verify_health_document(
        doc, challenge=challenge, expected_fingerprint=identity_in_tmp.fingerprint,
        addr="192.0.2.30:6370",
    ) == identity_in_tmp.fingerprint
    doc = await main.health(challenge=challenge, addr="192.0.2.31:6370")
    assert doc["identity"]["addr_refused"] == "192.0.2.31:6370"
    assert "signature" not in doc["identity"]


@pytest.mark.asyncio
async def test_health_signs_for_the_macs_address_from_its_report(
    healthy_core, identity_in_tmp, tmp_path, monkeypatch,
):
    """S2-10: a room dialling the Mac's own LAN address reaches the core
    in the VM through the pf redirect, which keeps the port. The Mac
    helper's /var/lib/domovoi-mac/lan_name.json vouches for that address,
    so the core signs for it at its own port, 6370, and for nothing else."""
    import json

    from domovoi.lan_name import service

    report = tmp_path / "var" / "lib" / "domovoi-mac" / "lan_name.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({
        "schema": 1, "request_id": None, "requested": "domovoi", "claimed": "domovoi", "state": "announced",
        "taken": None, "addresses": ["192.168.1.20"], "error": None, "written_at": "2026-11-16T10:00:00Z",
    }), encoding="utf-8")
    monkeypatch.setenv("DOMOVOI_LAN_NAME_REPORT_WINDOWS", str(tmp_path / "none" / "lan_name.json"))
    monkeypatch.setenv("DOMOVOI_LAN_NAME_REPORT_MAC", str(report))
    monkeypatch.setattr(service, "_root_only", lambda st: True)
    monkeypatch.setattr(server_identity, "_can_bind", _nobody_binds)
    assert server_identity.host_vouched_addresses() == {"192.168.1.20"}
    challenge = sat_identity.new_challenge()
    doc = await main.health(challenge=challenge, addr="192.168.1.20:6370")
    assert sat_identity.verify_health_document(
        doc, challenge=challenge, expected_fingerprint=identity_in_tmp.fingerprint,
        addr="192.168.1.20:6370",
    ) == identity_in_tmp.fingerprint
    for other in ("192.168.1.20:6369", "192.168.1.21:6370"):
        doc = await main.health(challenge=challenge, addr=other)
        assert doc["identity"]["addr_refused"] == other and "signature" not in doc["identity"]
    # Not root's alone: the report vouches for nothing.
    monkeypatch.setattr(service, "_root_only", lambda st: False)
    assert server_identity.host_vouched_addresses() == set()


# ─── why a room couldn't join ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_refused_address_is_said_where_an_admin_looks(healthy_core, monkeypatch):
    monkeypatch.setattr(server_identity, "VOUCHED_ADDRESS_SOURCES", [])
    await main.health(challenge=sat_identity.new_challenge(), addr="192.0.2.197:6370")
    [line] = join_problems.recent()
    assert line["kind"] == "addr_refused"
    assert "192.0.2.197:6370" in line["text"]
    assert "couldn't prove it's the one at that address" in line["text"]
    # The address came from the request, so the advice never just says to
    # trust it: listing it is only for an address someone set up.
    assert "If nobody set a room up with it, don't trust it" in line["text"]


def test_join_problems_keep_the_newest_few_and_count_repeats(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(join_problems, "_clock", lambda: now[0])
    for i in range(join_problems.MAX_KEPT + 3):
        now[0] += 1
        join_problems.addr_refused(f"192.0.2.{i}:6370")
    lines = join_problems.recent()
    assert len(lines) == join_problems.MAX_KEPT
    assert lines[0]["subject"].startswith(f"192.0.2.{join_problems.MAX_KEPT + 2}:")
    # A repeat inside the window bumps the count, adds no line.
    join_problems.addr_refused(lines[0]["subject"])
    assert len(join_problems.recent()) == join_problems.MAX_KEPT
    assert join_problems.recent()[0]["count"] == 2
    # And a day later they are gone.
    now[0] += join_problems.MAX_AGE_SEC + 1
    assert join_problems.recent() == []


def test_a_note_never_raises():
    join_problems.note(object(), object(), object())   # type: ignore[arg-type]


def test_the_join_problems_route_is_admin_read():
    route = next(r for r in main.app.routes
                 if getattr(r, "path", "") == "/v1/admin/satellites/join-problems")
    deps = [d.dependency for d in route.dependencies]
    assert main.require_admin_read in deps


# ─── the advert and the approve answer ─────────────────────────────────────


@pytest.mark.asyncio
async def test_health_advertises_the_approval_proof(healthy_core):
    doc = await main.health()
    assert doc["pairing_proof"] == pairing_proof.VERSION


@pytest.mark.asyncio
async def test_approving_a_device_that_checks_the_code_says_so(monkeypatch):
    @asynccontextmanager
    async def fake_scope():
        yield object()

    class _Repo:
        def __init__(self, _s):
            pass

        async def approve(self, room_id, code):
            return "entered"

    monkeypatch.setattr(main, "session_scope", fake_scope)
    monkeypatch.setattr(main, "SatelliteApprovalRepository", _Repo)
    main.APPROVAL_CODE_LIMITER.reset()
    out = await main.admin_satellite_approve("kitchen", main._ApproveSatelliteBody(code="481502"))
    assert out == {"approved": False, "waiting_for_device": True, "room_id": "kitchen"}


# ─── SAT-13: the port is part of the address ──────────────────────────────


def _the_box(ip):
    return str(ip) == "192.168.1.20"


def test_a_relay_on_the_servers_own_machine_at_another_port_is_refused():
    """SAT-13 PoC: the check ignored the port, so anything else listening on
    the server's own machine — at 7000, forwarding to the core — got the
    core's signature for ``192.168.1.20:7000`` and passed a pinned device's
    key check as if it were the server."""
    ours = server_identity.dialed_address_is_ours
    assert ours("192.168.1.20:6370", can_bind=_the_box, vouched=set()) is True
    assert ours("192.168.1.20:7000", can_bind=_the_box, vouched=set()) is False
    assert ours("127.0.0.1:7000", can_bind=_the_box, vouched=set()) is False
    assert ours("[::1]:7000", can_bind=_the_box, vouched=set()) is False
    assert ours("localhost:7000", can_bind=_the_box, vouched=set()) is False
    assert ours("192.168.1.20", can_bind=_the_box, vouched=set()) is False   # no port at all


def test_the_port_a_question_arrived_at_is_the_cores():
    ours = server_identity.dialed_address_is_ours
    assert ours("192.168.1.20:6394", can_bind=_the_box, vouched=set(), listen_ports={6394}) is True
    assert ours("192.168.1.20:6370", can_bind=_the_box, vouched=set(), listen_ports={6394}) is False


def test_a_host_vouches_for_a_changed_port_explicitly(monkeypatch):
    ours = server_identity.dialed_address_is_ours
    vouched = {"192.168.1.30:16370"}
    assert ours("192.168.1.30:16370", can_bind=_nobody_binds, vouched=vouched) is True
    assert ours("192.168.1.30:6370", can_bind=_nobody_binds, vouched=vouched) is False
    assert ours("192.168.1.30:16371", can_bind=_nobody_binds, vouched=vouched) is False
    monkeypatch.setattr(server_identity, "VOUCHED_ADDRESS_SOURCES",
                        [lambda: ["192.168.1.30:16370", "[fd00::30]:6371", "fd00::31",
                                  "evil.local:6370", "192.168.1.30:99999"]])
    assert server_identity.host_vouched_addresses() == {
        "192.168.1.30:16370", "[fd00::30]:6371", "fd00::31"}


class _Req:
    def __init__(self, port):
        self.scope = {"server": ("172.17.0.2", port)}


@pytest.mark.asyncio
async def test_health_signs_only_at_the_port_the_question_arrived_at(healthy_core, monkeypatch):
    monkeypatch.setattr(server_identity, "VOUCHED_ADDRESS_SOURCES", [])
    challenge = sat_identity.new_challenge()
    doc = await main.health(challenge=challenge, addr="127.0.0.1:7000", request=_Req(6370))
    assert doc["identity"].get("addr_refused") == "127.0.0.1:7000"
    doc = await main.health(challenge=challenge, addr="127.0.0.1:6394", request=_Req(6394))
    assert doc["identity"]["addr"] == "127.0.0.1:6394" and "signature" in doc["identity"]


# ─── SAT-16: the advert is signed when the device asks ─────────────────────


@pytest.mark.asyncio
async def test_health_signs_the_pairing_proof_advert(healthy_core, identity_in_tmp):
    """SAT-16 PoC: ``pairing_proof`` sat outside the signed block, so a host
    in the middle could strip it and pass this server off as an older one.
    A pairing-proof challenge now gets it signed, as the last line."""
    import base64

    challenge = "pairing-proof." + "ab" * 16
    doc = await main.health(challenge=challenge, addr="127.0.0.1:6370")
    public = base64.b64decode(doc["identity"]["public_key"])
    signature = base64.b64decode(doc["identity"]["signature"])
    signed = (b"domovoi-health-v1\n" + challenge.encode() + b"\n127.0.0.1:6370"
              + b"\npairing_proof=" + str(pairing_proof.VERSION).encode())
    assert sat_identity.verify(public, signed, signature)
    assert doc["pairing_proof"] == pairing_proof.VERSION
    assert sat_identity.verify_health_proof(
        doc, challenge=challenge, expected_fingerprint=identity_in_tmp.fingerprint,
        addr="127.0.0.1:6370",
    ) == (identity_in_tmp.fingerprint, pairing_proof.VERSION)
    stripped = dict(doc)
    stripped.pop("pairing_proof")
    with pytest.raises(sat_identity.IdentityError):
        sat_identity.verify_health_proof(
            stripped, challenge=challenge, expected_fingerprint=identity_in_tmp.fingerprint,
            addr="127.0.0.1:6370",
        )


@pytest.mark.asyncio
async def test_a_pairing_proof_challenge_is_signed_only_bound(healthy_core, identity_in_tmp):
    """Every device that asks the pairing-proof form names the address it
    dialed, so the form without one gets no signature."""
    challenge = "pairing-proof." + "cd" * 16
    doc = await main.health(challenge=challenge)
    assert "signature" not in doc["identity"] and "addr" not in doc["identity"]
    with pytest.raises(sat_identity.IdentityError):
        sat_identity.verify_health_document(
            doc, challenge=challenge, expected_fingerprint=None, addr=None,
        )


@pytest.mark.asyncio
async def test_an_ordinary_challenge_is_signed_as_it_always_was(healthy_core, identity_in_tmp):
    """Older devices (and the root clock helper) never ask: their message
    is byte for byte what it was."""
    challenge = sat_identity.new_challenge()
    doc = await main.health(challenge=challenge, addr="127.0.0.1:6370")
    assert sat_identity.verify_health_document(
        doc, challenge=challenge, expected_fingerprint=identity_in_tmp.fingerprint,
        addr="127.0.0.1:6370",
    ) == identity_in_tmp.fingerprint


# ─── S3-4: a room set up for a different server ───────────────────────────


class _Peer:
    """A /v1/health request from a room device at ``host``, arriving at
    the core's own port."""

    def __init__(self, host, port=6370):
        self.scope = {"server": ("192.168.1.20", port)}
        self.client = type("C", (), {"host": host})()
        self.headers = {}


@pytest.mark.asyncio
async def test_a_room_pinned_to_another_server_is_said_where_an_admin_looks(
    healthy_core, monkeypatch,
):
    """S3-4: a room set up for this house before it was reinstalled checks
    the new key, refuses it and never opens a stream. The server says so in
    Settings → Rooms once the address has kept asking for a while."""
    monkeypatch.setattr(server_identity, "VOUCHED_ADDRESS_SOURCES", [lambda: ["192.168.1.20"]])
    now = [1000.0]
    monkeypatch.setattr(join_problems, "_clock", lambda: now[0])
    stranded = _Peer("192.168.1.41")

    async def check(peer):
        await main.health(challenge=sat_identity.new_challenge(),
                          addr="192.168.1.20:6370", request=peer)

    await check(stranded)
    assert join_problems.recent() == []          # one check is just a check
    now[0] += 60
    await check(stranded)
    assert join_problems.recent() == []
    now[0] += join_problems.OTHER_SERVER_AFTER_SEC
    await check(stranded)
    [line] = join_problems.recent()
    assert line["kind"] == "other_server" and line["subject"] == "192.168.1.41"
    assert "set it up again for this server" in line["text"]

    # A device that does open a stream is this server's: no line, and the
    # one above goes once that address joins.
    joined = _Peer("192.168.1.42")
    await check(joined)
    join_problems.stream_opened("192.168.1.42")
    now[0] += join_problems.OTHER_SERVER_AFTER_SEC + 60
    await check(joined)                          # e.g. its clock helper
    assert [p["subject"] for p in join_problems.recent()] == ["192.168.1.41"]
    join_problems.stream_opened("192.168.1.41")
    assert join_problems.recent() == []


@pytest.mark.asyncio
async def test_approval_proof_checks_and_plain_health_are_not_counted(
    healthy_core, monkeypatch,
):
    """An unpaired device asking every house (``pairing-proof.`` challenge)
    parks through a stream anyway, and a check without an address is a
    phone's or the dashboard's: neither is a room refusing this server."""
    monkeypatch.setattr(server_identity, "VOUCHED_ADDRESS_SOURCES", [lambda: ["192.168.1.20"]])
    now = [1000.0]
    monkeypatch.setattr(join_problems, "_clock", lambda: now[0])
    peer = _Peer("192.168.1.43")
    for _ in range(5):
        await main.health(challenge=sat_identity.new_challenge(pairing_proof=True),
                          addr="192.168.1.20:6370", request=peer)
        await main.health(challenge=sat_identity.new_challenge(), request=peer)
        now[0] += 120
    assert join_problems.recent() == []


def test_a_probe_not_repeated_starts_over(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(join_problems, "_clock", lambda: now[0])
    join_problems.identity_checked("192.168.1.44")
    now[0] += join_problems._PROBE_FORGET_SEC + 1
    join_problems.identity_checked("192.168.1.44")
    assert join_problems.recent() == []
    join_problems.stream_closed("192.168.1.99")  # never opened: no error


@pytest.mark.asyncio
async def test_a_room_socket_counts_as_joined_while_it_is_open(monkeypatch):
    """The stream route marks its address as talking to this server for as
    long as the socket lives, even when the session ends in an error."""
    from domovoi import streaming

    seen = []

    class _S:
        _hello_source = lambda self: "192.168.1.45"  # noqa: E731

        async def _run(self):
            seen.append(dict(join_problems._streams))
            raise RuntimeError("socket dropped")

    with pytest.raises(RuntimeError):
        await streaming.StreamSession.run(_S())
    assert seen == [{"192.168.1.45": 1}]
    assert join_problems._streams == {}
