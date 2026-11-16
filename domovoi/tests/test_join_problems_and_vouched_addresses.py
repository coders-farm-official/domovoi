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
    assert ours("192.168.1.30:6371", can_bind=_nobody_binds, vouched=vouched) is True
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


# ─── why a room couldn't join ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_refused_address_is_said_where_an_admin_looks(healthy_core, monkeypatch):
    monkeypatch.setattr(server_identity, "VOUCHED_ADDRESS_SOURCES", [])
    await main.health(challenge=sat_identity.new_challenge(), addr="192.0.2.197:6370")
    [line] = join_problems.recent()
    assert line["kind"] == "addr_refused"
    assert "192.0.2.197:6370" in line["text"]
    assert "couldn't prove it's the one at that address" in line["text"]


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
