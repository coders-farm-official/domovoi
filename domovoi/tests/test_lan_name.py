"""The network name (domovoi/lan_name/, design note MDNS-NAME-PLAN).

Covers the name rules (the golden list the C# copy runs too), the packet
parser's bounds against hostile packets (pointer loops, oversize, random
bytes and mutations), the responder's state machine with a fake clock
(free, taken, a typed name kept, already ours, a simultaneous probe, a
conflict after announcing and the rename cap, answers and their limits,
goodbyes), the request/report files of the Windows install, and the
order of ``ready.server_names``.

No DB, no socket (the asyncio runner is tested with a fake transport).
"""

from __future__ import annotations

import json
import random
import socket
import struct
from pathlib import Path

import pytest

from domovoi.lan_name import names, packet
from domovoi.lan_name import responder as rmod
from domovoi.lan_name import service as svc
from domovoi.lan_name.responder import Responder

VECTORS = json.loads((Path(svc.__file__).parent / "vectors.json").read_text(encoding="utf-8"))
IP = "192.168.1.20"
IPB = socket.inet_aton(IP)
OTHER = socket.inet_aton("192.168.1.30")
PEER = ("192.168.1.30", 5353)


# ─── names ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("raw, want", VECTORS["names_ok"])
def test_names_the_household_may_type(raw, want) -> None:
    assert names.normalize(raw) == want


@pytest.mark.parametrize("raw", VECTORS["names_bad"])
def test_names_refused_with_a_reason(raw) -> None:
    with pytest.raises(names.BadName) as e:
        names.normalize(raw)
    assert str(e.value)


def test_only_the_default_has_a_family() -> None:
    fam = names.family("domovoi")
    assert fam[:3] == VECTORS["family_first"] and fam[-1] == VECTORS["family_last"] and len(fam) == 99
    assert names.family("kitchen") == ["kitchen"]
    assert not names.is_valid(None) and not names.is_valid(5)


def test_the_label_rule_matches_streaming() -> None:
    from domovoi import streaming

    for raw, want in VECTORS["names_ok"]:
        assert streaming._HOST_LABEL.match(want)


# ─── packets ─────────────────────────────────────────────────────────────


def test_packets_match_the_golden_bytes() -> None:
    n = packet.name_of("domovoi")
    assert packet.probe(n, IPB).hex() == VECTORS["probe_domovoi"]
    assert packet.response(n, IPB, 120).hex() == VECTORS["announce_domovoi"]
    assert packet.response(n, IPB, 0).hex() == VECTORS["goodbye_domovoi"]
    assert packet.response(n, IPB, 120, qtype=packet.T_AAAA).hex() == VECTORS["answer_aaaa_domovoi"]
    assert packet.response(n, IPB, 120, qtype=packet.T_ANY).hex() == VECTORS["answer_any_domovoi"]
    assert packet.legacy_response(0x1234, n, IPB, packet.T_A).hex() == VECTORS["legacy_a_domovoi_id_4660"]
    assert packet.legacy_response(0x1234, n, IPB, packet.T_AAAA).hex() == VECTORS["legacy_aaaa_domovoi_id_4660"]


def test_our_packets_parse_back() -> None:
    n = packet.name_of("domovoi")
    m = packet.parse(packet.probe(n, IPB))
    assert not m.is_response and m.questions[0].name == n and m.questions[0].unicast
    assert m.authority[0].rdata == IPB and m.authority[0].ttl == 120
    m = packet.parse(packet.response(n, IPB, 120))
    assert m.is_response and m.answers[0].flush and m.answers[0].rdata == IPB
    nsec = m.additional[0]
    assert nsec.rtype == packet.T_NSEC and nsec.rdata.endswith(b"\x00\x01\x40")


@pytest.mark.parametrize("key", sorted(VECTORS["bad_packets"]))
def test_bad_packets_are_dropped_whole(key) -> None:
    with pytest.raises(packet.ParseError):
        packet.parse(bytes.fromhex(VECTORS["bad_packets"][key]))


def test_compressed_names_are_read() -> None:
    # Question "domovoi.local" at 12; the answer's name is a pointer to it,
    # the way Avahi and Apple write answers.
    q = packet.encode_name(packet.name_of("domovoi")) + struct.pack("!HH", 1, 1)
    ans = b"\xc0\x0c" + struct.pack("!HHIH", 1, 0x8001, 120, 4) + OTHER
    m = packet.parse(struct.pack("!HHHHHH", 0, 0x8400, 1, 1, 0, 0) + q + ans)
    assert m.answers[0].name == (b"domovoi", b"local") and m.answers[0].rdata == OTHER
    # A name that ends in a pointer to a suffix: "x" + -> "local".
    tail = b"\x01X\xc0\x14"
    m = packet.parse(struct.pack("!HHHHHH", 0, 0x8400, 1, 1, 0, 0) + q
                     + tail + struct.pack("!HHIH", 1, 1, 120, 4) + OTHER)
    assert m.answers[0].name == (b"x", b"local")   # lowercased


def test_a_chain_of_backward_pointers_is_bounded() -> None:
    # Each name points one step back: 20 jumps is over the cap.
    buf = bytearray(struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0)) + b"\x01a\x00"
    prev = 12
    for _ in range(20):
        here = len(buf)
        buf += bytes([0xC0 | (prev >> 8), prev & 0xFF])
        prev = here
    buf[4:6] = struct.pack("!H", 1)
    # The question is the last pointer.
    pkt = bytes(struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0)) + bytes(buf[12:prev]) + bytes(buf[prev:]) + b"\x00\x01\x00\x01"
    with pytest.raises(packet.ParseError):
        packet.read_name(pkt, prev)


def test_oversize_is_dropped() -> None:
    with pytest.raises(packet.ParseError):
        packet.parse(b"\x00" * (packet.MAX_PACKET + 1))


def test_random_and_mutated_packets_never_raise_anything_else() -> None:
    rng = random.Random(6762)
    n = packet.name_of("domovoi")
    seeds = [packet.probe(n, IPB), packet.response(n, IPB, 120, qtype=packet.T_ANY),
             packet.legacy_response(7, n, IPB, packet.T_AAAA)]
    r = Responder(rng=lambda: 0.0)
    r.configure("domovoi", True, IP, 0.0)
    for _ in range(4):
        r.on_timer(10.0)
    for i in range(6000):
        if i % 3 == 0:
            data = bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 80)))
        else:
            data = bytearray(rng.choice(seeds))
            for _ in range(rng.randint(1, 6)):
                data[rng.randrange(len(data))] = rng.getrandbits(8)
            data = bytes(data)
        try:
            packet.parse(data)
        except packet.ParseError:
            pass
        # The responder never raises on any packet, whatever it holds.
        r.on_packet(data, PEER, 20.0 + i * 0.001)


# ─── the state machine ───────────────────────────────────────────────────


class Net:
    """A fake clock and wire around one :class:`Responder`."""

    def __init__(self, name="domovoi", *, own=(), sticky=None, on=True, ip=IP):
        self.t = 100.0
        self.r = Responder(own_addresses=lambda: set(own), rng=lambda: 0.0)
        self.sent: list[rmod.Out] = []
        self.sent += self.r.configure(name, on, ip, self.t, sticky=sticky)

    def run(self, seconds: float, step: float = 0.05) -> None:
        end = self.t + seconds
        while self.t < end:
            self.t = round(self.t + step, 4)
            self.sent += self.r.on_timer(self.t)

    def feed(self, data: bytes, src=PEER) -> list[rmod.Out]:
        out = self.r.on_packet(data, src, self.t)
        self.sent += out
        return out

    def kinds(self) -> list[str]:
        out = []
        for o in self.sent:
            m = packet.parse(o.data)
            if not m.is_response:
                out.append("probe")
            elif m.answers and m.answers[0].ttl == 0:
                out.append("goodbye")
            else:
                out.append("answer")
        return out


def taken_by(name: str, addr: bytes = OTHER) -> bytes:
    return packet.response(packet.name_of(name), addr, 120)


def test_a_free_name_is_probed_three_times_then_announced_twice() -> None:
    n = Net()
    assert n.r.state == "checking"
    n.run(3)
    assert n.kinds() == ["probe", "probe", "probe", "answer", "answer"]
    assert n.r.state == "announced" and n.r.claimed == "domovoi"
    assert all(o.dest is None for o in n.sent)
    snap = n.r.snapshot()
    assert snap == {"requested": "domovoi", "claimed": "domovoi", "state": "announced", "taken": None, "error": None}


def test_the_default_moves_to_domovoi2_when_taken() -> None:
    n = Net()
    n.run(0.1)
    n.feed(taken_by("domovoi"))
    n.run(3)
    assert n.r.claimed == "domovoi2" and n.r.state == "announced" and n.r.taken == "domovoi"


def test_the_sticky_name_is_tried_first() -> None:
    n = Net(sticky="domovoi3")
    n.run(3)
    assert n.r.claimed == "domovoi3"
    first = packet.parse(n.sent[0].data)
    assert first.questions[0].name == packet.name_of("domovoi3")


def test_a_sticky_name_from_another_family_is_only_the_fallback() -> None:
    n = Net("kitchen", sticky="domovoi2")
    n.run(3)
    assert n.r.claimed == "kitchen" and n.r.state == "announced"


def test_a_typed_name_that_is_taken_keeps_the_old_one_and_says_taken() -> None:
    n = Net(sticky="domovoi2")
    n.run(3)
    assert n.r.claimed == "domovoi2"
    n.sent.clear()
    n.sent += n.r.configure("kitchen", True, IP, n.t)
    assert n.kinds()[0] == "goodbye"   # the old name is dropped while kitchen is checked
    n.run(0.1)
    n.feed(taken_by("kitchen"))
    n.run(3)
    assert n.r.claimed == "domovoi2" and n.r.state == "taken" and n.r.taken == "kitchen"
    assert n.r.snapshot()["claimed"] == "domovoi2"


def test_every_name_taken_gives_up_and_says_taken() -> None:
    n = Net("kitchen")
    n.run(0.1)
    n.feed(taken_by("kitchen"))
    n.run(1)
    assert n.r.state == "taken" and n.r.claimed is None


def test_already_ours_is_held_without_a_second_copy() -> None:
    own = {socket.inet_aton("192.168.1.20"), socket.inet_aton("10.0.0.5")}
    n = Net(own=own)
    n.run(0.1)
    # This machine's own responder (Avahi, or Windows for a PC called DOMOVOI).
    n.feed(packet.response(packet.name_of("domovoi"), socket.inet_aton("10.0.0.5"), 120), src=(IP, 5353))
    n.run(3)
    assert n.r.phase == "held" and n.r.state == "announced" and n.r.claimed == "domovoi"
    assert n.kinds().count("answer") == 0
    # Held: questions are left to the other responder.
    assert n.feed(packet.probe(packet.name_of("domovoi"), OTHER)) == []


def test_our_own_echo_is_not_a_conflict() -> None:
    n = Net()
    n.run(0.3)
    for o in list(n.sent):
        n.feed(o.data, src=(IP, 5353))
    n.run(3)
    assert n.r.claimed == "domovoi" and n.r.state == "announced"


def test_a_simultaneous_probe_with_a_higher_address_wins() -> None:
    n = Net()
    n.run(0.3)
    their = packet.probe(packet.name_of("domovoi"), socket.inet_aton("192.168.1.99"))
    n.feed(their)
    assert n.r.phase == "probing" and n.r._probes == 0   # waits a second, starts again
    lower = packet.probe(packet.name_of("domovoi"), socket.inet_aton("192.168.1.2"))
    n.run(1.1)
    n.feed(lower)  # we win this one: nothing changes
    n.run(3)
    assert n.r.claimed == "domovoi"


def answered(n: Net, qtype: int, src=PEER, qid=0, known: bool = False) -> list[rmod.Out]:
    name = packet.encode_name(packet.name_of("domovoi"))
    an = 1 if known else 0
    pkt = struct.pack("!HHHHHH", qid, 0, 1, an, 0, 0) + name + struct.pack("!HH", qtype, 1)
    if known:
        pkt += packet.a_record(packet.name_of("domovoi"), IPB, 120)
    return n.feed(pkt, src)


def test_questions_are_answered_by_multicast_and_rate_limited() -> None:
    n = Net()
    n.run(3)
    n.t += 2
    out = answered(n, packet.T_A)
    assert len(out) == 1 and out[0].dest is None
    m = packet.parse(out[0].data)
    assert m.answers[0].rdata == IPB and m.additional[0].rtype == packet.T_NSEC
    assert answered(n, packet.T_A) == []   # within a second
    out = answered(n, packet.T_AAAA)
    assert packet.parse(out[0].data).answers[0].rtype == packet.T_NSEC
    n.t += 1.1
    assert answered(n, packet.T_A, known=True) == []   # the asker already has it
    assert len(answered(n, packet.T_A)) == 1


def test_other_names_and_types_get_nothing() -> None:
    n = Net()
    n.run(3)
    n.t += 2
    q = struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0) + packet.encode_name(packet.name_of("printer")) + struct.pack("!HH", 1, 1)
    assert n.feed(q) == []
    q = struct.pack("!HHHHHH", 0, 0, 1, 0, 0, 0) + packet.encode_name(packet.name_of("domovoi")) + struct.pack("!HH", 16, 1)
    assert n.feed(q) == []


def test_legacy_unicast_gets_a_unicast_answer_to_private_addresses_only() -> None:
    n = Net()
    n.run(3)
    out = answered(n, packet.T_A, src=("192.168.1.41", 50000), qid=0x1234)
    assert out[0].dest == ("192.168.1.41", 50000)
    assert out[0].data.hex() == VECTORS["legacy_a_domovoi_id_4660"]
    assert answered(n, packet.T_A, src=("8.8.8.8", 50000)) == []
    assert answered(n, packet.T_A, src=("192.168.1.41", 0)) == []


def test_sends_are_capped_whatever_comes_in() -> None:
    n = Net()
    n.run(3)
    n.sent.clear()
    for i in range(500):
        answered(n, packet.T_A, src=("192.168.1.41", 40000 + i), qid=i)
    assert len(n.sent) <= rmod.MAX_SENDS_PER_SEC


def test_a_conflict_after_announcing_reprobes_and_a_win_keeps_the_name() -> None:
    n = Net()
    n.run(3)
    n.feed(taken_by("domovoi"))
    assert n.r.phase == "probing" and n.r.state == "announced"   # still ours while we check
    n.run(3)
    assert n.r.claimed == "domovoi" and n.r.phase == "announced"


def test_losing_after_announcing_renames_with_a_goodbye_and_is_capped() -> None:
    n = Net()
    n.run(3)
    held = []
    for _ in range(rmod.MAX_FORCED_RENAMES):
        n.t += rmod.REPROBE_GAP + 1
        n.feed(taken_by(n.r.claimed))
        n.run(0.1)
        n.sent.clear()
        n.feed(taken_by(n.r.candidate))   # the re-probe is answered: lost
        assert "goodbye" in n.kinds()
        n.run(3)
        held.append(n.r.claimed)
    assert held[:2] == ["domovoi2", "domovoi3"]
    assert n.r.state == "taken" and n.r.claimed is None and n.r.error
    # An hour later it tries again.
    n.run(rmod.RENAME_WINDOW, step=5)
    n.run(3)
    assert n.r.claimed and n.r.state == "announced"


def test_reprobes_are_spaced() -> None:
    n = Net()
    n.run(3)
    n.feed(taken_by("domovoi"))
    n.run(3)
    n.feed(taken_by("domovoi"))
    assert n.r.phase == "announced" and n.r.next_at is not None   # waits for REPROBE_GAP
    n.feed(taken_by("domovoi"))
    n.run(rmod.REPROBE_GAP + 3)
    assert n.r.claimed == "domovoi"


def test_goodbye_on_stop_off_and_a_new_address() -> None:
    n = Net()
    n.run(3)
    n.sent.clear()
    n.sent += n.r.configure("domovoi", True, "192.168.1.21", n.t)
    assert n.kinds()[0] == "goodbye" and packet.parse(n.sent[0].data).answers[0].rdata == IPB
    n.run(3)
    n.sent.clear()
    n.sent += n.r.configure("domovoi", False, "192.168.1.21", n.t)
    assert n.kinds() == ["goodbye"] and n.r.state == "off"
    n.sent += n.r.configure("domovoi", True, "192.168.1.21", n.t)
    n.run(3)
    n.sent.clear()
    n.sent += n.r.stop(n.t)
    assert n.kinds() == ["goodbye"]
    assert n.r.stop(n.t) == []   # once


@pytest.mark.parametrize("ip", [None, "127.0.0.1", "169.254.3.4", "8.8.8.8", "100.64.0.1", "nonsense"])
def test_no_private_address_means_no_lan(ip) -> None:
    n = Net(ip=ip)
    n.run(3)
    assert n.r.state == "no_lan" and n.sent == []


def test_packets_before_a_name_is_set_are_ignored() -> None:
    r = Responder()
    assert r.on_packet(taken_by("domovoi"), PEER, 0.0) == []
    assert r.state == "off"


# ─── the request and report (the Windows install) ───────────────────────

REPORT = {
    "schema": 1, "request_id": "a" * 32, "requested": "domovoi", "claimed": "domovoi2",
    "state": "announced", "taken": "domovoi", "addresses": ["192.168.1.30"], "error": None,
    "written_at": "2026-11-12T05:00:00Z",
}


@pytest.fixture
def host(tmp_path, monkeypatch):
    report = tmp_path / "var" / "lib" / "domovoi-windows" / "lan_name.json"
    report.parent.mkdir(parents=True)
    monkeypatch.setenv("DOMOVOI_LAN_NAME_REPORT_WINDOWS", str(report))
    monkeypatch.setenv("DOMOVOI_LAN_NAME_DIR", str(tmp_path / "home" / ".domovoi" / "lan_name"))
    monkeypatch.setattr(svc, "_root_only", lambda st: True)
    monkeypatch.setattr(svc, "mode", lambda: "host")
    from domovoi.config import settings

    monkeypatch.setattr(settings, "lan_name", "domovoi")
    monkeypatch.setattr(settings, "lan_name_enabled", True)
    monkeypatch.setattr("domovoi.config_env_writer._ENV_FILE", tmp_path / ".env")

    def write(doc=REPORT) -> None:
        report.write_text(json.dumps(doc) if isinstance(doc, dict) else doc, encoding="utf-8")

    return {"report": report, "write": write, "dir": tmp_path / "home" / ".domovoi" / "lan_name",
            "env": tmp_path / ".env"}


def test_no_report_is_no_host(host) -> None:
    st = svc.status()
    assert st["state"] == "no_host" and st["claimed"] is None and st["fqdn"] is None


def test_the_report_is_read_and_checking_until_it_names_our_request(host) -> None:
    req = svc.sync_request()
    host["write"]()
    st = svc.status()
    assert st["state"] == "checking" and st["claimed"] == "domovoi2"
    host["write"]({**REPORT, "request_id": req["id"]})
    st = svc.status()
    assert st["state"] == "announced" and st["fqdn"] == "domovoi2.local" and st["taken"] == "domovoi"
    assert st["addresses"] == ["192.168.1.30"] and st["host_kind"] == "windows"


def test_a_report_not_roots_alone_is_not_believed(host, monkeypatch) -> None:
    host["write"]()
    monkeypatch.setattr(svc, "_root_only", lambda st: False)
    assert svc.read_report() is None
    assert svc.status()["state"] == "no_host"


@pytest.mark.parametrize("bad", [
    {**REPORT, "schema": 2},
    {**REPORT, "state": "pwned"},
    {**REPORT, "requested": "-bad"},
    {**REPORT, "requested": "Domovoi"},          # not as the host writes it
    {**REPORT, "addresses": ["8.8.8.8"]},
    {**REPORT, "addresses": "192.168.1.30"},
    {**REPORT, "addresses": ["192.168.1.30"] * 5},
    "not json", "[]",
])
def test_a_report_in_another_shape_is_ignored(host, bad) -> None:
    host["write"](bad)
    assert svc.read_report() is None


def test_odd_fields_in_a_report_are_dropped(host) -> None:
    host["write"]({**REPORT, "claimed": "evil.example", "taken": 5, "error": "x\x1b[31m", "request_id": "zz"})
    _, doc = svc.read_report()
    assert doc["claimed"] is None and doc["taken"] is None and doc["error"] is None and doc["request_id"] is None


def test_an_oversize_report_is_ignored(host) -> None:
    host["write"]("{" + " " * 5000 + "}")
    assert svc.read_report() is None


def test_the_request_changes_only_when_the_choice_does(host) -> None:
    a = svc.sync_request()
    assert svc.sync_request()["id"] == a["id"]
    assert svc.sync_request(force=True)["id"] != a["id"]
    doc = json.loads((host["dir"] / "request.json").read_text())
    assert set(doc) == {"v", "id", "name", "enabled", "at"} and doc["name"] == "domovoi" and doc["enabled"] is True


def test_save_checks_writes_env_and_asks_the_host(host) -> None:
    st = svc.save("Kitchen.local", None)
    assert st["requested"] == "kitchen"
    assert "LAN_NAME=kitchen" in host["env"].read_text()
    assert json.loads((host["dir"] / "request.json").read_text())["name"] == "kitchen"
    with pytest.raises(names.BadName):
        svc.save("kit chen", None)
    svc.save(None, False)
    assert json.loads((host["dir"] / "request.json").read_text())["enabled"] is False
    assert "lan_name_enabled=false" in host["env"].read_text().lower()


def test_a_broken_setting_means_the_default(monkeypatch) -> None:
    from domovoi.config import settings

    monkeypatch.setattr(settings, "lan_name", "not a name!")
    assert svc.requested_name() == "domovoi"


def test_nothing_announces_on_a_dev_box(monkeypatch) -> None:
    monkeypatch.setattr(svc, "host_side", lambda: None)
    monkeypatch.setattr("domovoi.host_kind.host_kind", lambda: "windows")
    assert svc.mode() == "none"
    st = svc.status()
    assert st["state"] == "no_host" and st["host_kind"] == "windows"
    monkeypatch.setattr("domovoi.host_kind.host_kind", lambda: "linux")
    assert svc.mode() == "core"
    monkeypatch.setattr(svc, "host_side", lambda: "windows")
    assert svc.mode() == "host"


# ─── the asyncio runner (core mode), with a fake transport ──────────────


class FakeTransport:
    def __init__(self):
        self.sent: list[tuple[bytes, tuple]] = []
        self.closed = False

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_the_runner_announces_saves_the_name_and_says_goodbye(tmp_path, monkeypatch) -> None:
    import asyncio

    from domovoi.config import settings

    monkeypatch.setenv("DOMOVOI_LAN_NAME_DIR", str(tmp_path / "ln"))
    monkeypatch.setattr(settings, "lan_name", "domovoi")
    monkeypatch.setattr(settings, "lan_name_enabled", True)
    transport = FakeTransport()

    async def fake_endpoint(self, factory, sock=None):
        return transport, factory()

    monkeypatch.setattr(asyncio.get_running_loop().__class__, "create_datagram_endpoint", fake_endpoint)
    s = svc.LanNameService(lan_ip=lambda: IP, open_sock=lambda ip: object(), own=lambda: set())
    s.start()
    for _ in range(80):
        await asyncio.sleep(0.05)
        if s.responder.phase == "announced":
            break
    assert s.snapshot()["state"] == "announced" and s.snapshot()["addresses"] == [IP]
    assert svc.read_state() == {"requested": "domovoi", "claimed": "domovoi"}
    assert all(dest == ("224.0.0.251", 5353) for _, dest in transport.sent)
    await s.stop()
    last = packet.parse(transport.sent[-1][0])
    assert last.answers[0].ttl == 0 and transport.closed


@pytest.mark.asyncio
async def test_a_failed_bind_says_failed(tmp_path, monkeypatch) -> None:
    import asyncio

    monkeypatch.setenv("DOMOVOI_LAN_NAME_DIR", str(tmp_path / "ln"))

    def boom(ip):
        raise PermissionError(13, "Permission denied")

    s = svc.LanNameService(lan_ip=lambda: IP, open_sock=boom, own=lambda: set())
    s.start()
    await asyncio.sleep(0.2)
    assert s.snapshot()["state"] == "failed" and "5353" in s.snapshot()["error"]
    await s.stop()


# ─── ready.server_names ─────────────────────────────────────────────────


def test_the_claimed_name_is_advertised_first(monkeypatch) -> None:
    from domovoi import streaming

    monkeypatch.setattr(streaming.socket, "gethostname", lambda: "beelink")
    monkeypatch.setattr(svc, "claimed_name", lambda: "domovoi2.local")
    assert streaming.advertised_server_names() == ["domovoi2.local", "beelink.local"]
    monkeypatch.setattr(streaming.socket, "gethostname", lambda: "domovoi2")
    assert streaming.advertised_server_names() == ["domovoi2.local"]
    monkeypatch.setattr(svc, "claimed_name", lambda: None)
    assert streaming.advertised_server_names() == ["domovoi2.local"]


def test_claimed_name_only_for_a_held_name(monkeypatch) -> None:
    monkeypatch.setattr(svc, "status", lambda: {"state": "announced", "claimed": "domovoi2"})
    assert svc.claimed_name() == "domovoi2.local"
    monkeypatch.setattr(svc, "status", lambda: {"state": "off", "claimed": None})
    assert svc.claimed_name() is None


# ─── the HTTP hops ───────────────────────────────────────────────────────


def test_read_by_devices_changed_by_admins_on_both_hops() -> None:
    from domovoi.main import app as core_app
    from web.backend.main import app as web_app

    def deps(app, path, method):
        for r in app.routes:
            if getattr(r, "path", None) == path and method in getattr(r, "methods", set()):
                return {d.call.__name__ for d in r.dependant.dependencies}
        raise AssertionError(f"no {method} {path}")

    assert "require_device_read" in deps(core_app, "/v1/lan-name", "GET")
    assert "require_admin_mutation" in deps(core_app, "/v1/lan-name", "PUT")
    assert "require_device_read" in deps(web_app, "/api/lan-name", "GET")
    assert "require_admin_mutation" in deps(web_app, "/api/lan-name", "PUT")


@pytest.mark.asyncio
async def test_the_core_endpoint_202_400_and_refuses_strangers(host, monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from domovoi.main import app as core_app
    from domovoi.tests.auth_testkit import bearer, install_fake_db

    install_fake_db(monkeypatch, admin=True, sessions={"admin-token"})
    if not hasattr(core_app.state, "config_apply_lock"):
        import asyncio

        monkeypatch.setattr(core_app.state, "config_apply_lock", asyncio.Lock(), raising=False)
    async with AsyncClient(transport=ASGITransport(app=core_app, raise_app_exceptions=False),
                           base_url="http://test") as c:
        r = await c.put("/v1/lan-name", json={"name": "Kitchen"}, headers=bearer("admin-token"))
        assert r.status_code == 202 and r.json()["requested"] == "kitchen"
        r = await c.put("/v1/lan-name", json={"name": "-x-"}, headers=bearer("admin-token"))
        assert r.status_code == 400 and "hyphen" in r.json()["detail"]
        r = await c.put("/v1/lan-name", json={"name": "kitchen"})
        assert r.status_code in (401, 403)
        r = await c.get("/v1/lan-name", headers=bearer("admin-token"))
        assert r.status_code == 200 and r.json()["state"] in ("no_host", "checking")


def test_the_windows_side_reads_the_same_paths() -> None:
    cs = (Path(__file__).resolve().parents[2] / "windows" / "common" / "MdnsResponder.cs").read_text(encoding="utf-8")
    assert '"/.domovoi/lan_name/request.json"' in cs and svc.REQUEST_NAME == "request.json"
    assert 'ReportDir + "/lan_name.json"' in cs and svc.REPORT_FILES[0][1] == "/var/lib/domovoi-windows/lan_name.json"
    assert "Every = TimeSpan.FromSeconds(" + str(svc.HOST_POLL_SEC) + ")" in cs
    for state in rmod.STATES:
        assert '"' + state + '"' in cs
