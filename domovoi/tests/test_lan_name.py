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

    def __init__(self, name="domovoi", *, sticky=None, on=True, ip=IP, prefix=24):
        self.t = 100.0
        self.r = Responder(rng=lambda: 0.0)
        self.sent: list[rmod.Out] = []
        self.sent += self.r.configure(name, on, ip, self.t, sticky=sticky, prefix=prefix)

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


def test_every_name_taken_gives_up_says_taken_and_tries_again_later() -> None:
    n = Net("kitchen")
    n.run(0.1)
    n.feed(taken_by("kitchen"))
    n.run(1)
    assert n.r.state == "taken" and n.r.claimed is None
    # L1: never for good: the list starts again after the back-off.
    n.run(rmod.RETRY_AFTER_GIVING_UP, step=5)
    n.run(3)
    assert n.r.state == "announced" and n.r.claimed == "kitchen"


def test_forged_answers_for_all_99_names_do_not_silence_it_forever() -> None:
    n = Net()
    for _ in range(5000):
        n.run(0.05)
        if n.r.phase == "gave_up":
            break
        if n.r.candidate:
            n.feed(taken_by(n.r.candidate))
    assert n.r.phase == "gave_up" and n.r.next_at is not None
    n.run(rmod.RETRY_AFTER_GIVING_UP + 5, step=5)
    n.run(3)
    assert n.r.state == "announced"


def test_a_conflict_before_the_first_probe_counts_for_nothing() -> None:
    # L1: during the pause before a candidate's first probe nothing was
    # asked, so an answer then can't mark the name taken.
    r = Responder(rng=lambda: 1.0)   # the first probe waits 250 ms
    r.configure("domovoi", True, IP, 0.0, prefix=24)
    assert r._probes == 0
    r.on_packet(taken_by("domovoi"), PEER, 0.1)
    assert r.candidate == "domovoi" and r.taken is None


def test_held_only_for_our_lan_address_and_reported_as_held() -> None:
    n = Net()
    n.run(0.1)
    # This machine's own responder (Avahi, or Windows for a PC called
    # DOMOVOI) answers with our LAN address.
    n.feed(packet.response(packet.name_of("domovoi"), IPB, 120), src=(IP, 5353))
    n.run(3)
    assert n.r.phase == "held" and n.r.state == "held" and n.r.claimed == "domovoi"
    assert n.r.snapshot()["state"] == "held"
    assert n.kinds().count("answer") == 0
    # Held: questions are left to the other responder.
    assert n.feed(packet.probe(packet.name_of("domovoi"), OTHER)) == []
    # Looked at again within a minute; the recheck still says held.
    assert n.r.next_at - n.t <= rmod.HELD_RECHECK
    n.run(n.r.next_at - n.t + 0.1)
    assert n.r.state == "held" and n.r.phase == "probing"
    n.run(3)
    assert n.r.state == "announced"   # nobody else answered: ours now


def test_another_address_of_this_machine_is_not_ours() -> None:
    # L4: a docker or WSL address of this machine, or any other address,
    # is someone else: the name moves on, it is never "held" by it.
    n = Net()
    n.run(0.1)
    n.feed(packet.response(packet.name_of("domovoi"), socket.inet_aton("192.168.1.99"), 120))
    n.run(3)
    assert n.r.claimed == "domovoi2" and n.r.phase == "announced"


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


def test_legacy_unicast_gets_a_unicast_answer_to_lan_sources_only() -> None:
    n = Net()
    n.run(3)
    out = answered(n, packet.T_A, src=("192.168.1.41", 50000), qid=0x1234)
    assert out[0].dest == ("192.168.1.41", 50000)
    assert out[0].data.hex() == VECTORS["legacy_a_domovoi_id_4660"]
    assert answered(n, packet.T_A, src=("8.8.8.8", 50000)) == []
    assert answered(n, packet.T_A, src=("192.168.1.42", 0)) == []
    # L3: on-link only: another private network is not the LAN.
    assert answered(n, packet.T_A, src=("10.0.0.7", 50000)) == []
    assert answered(n, packet.T_A, src=("172.17.0.2", 50000)) == []


def test_legacy_answers_have_their_own_small_budget() -> None:
    # L2: one a second per source, LEGACY_PER_SEC in all, and the
    # multicast answers keep their budget whatever legacy queries do.
    n = Net()
    n.run(3)
    n.t += 2
    n.sent.clear()
    assert len(answered(n, packet.T_A, src=("192.168.1.41", 50000))) == 1
    assert answered(n, packet.T_A, src=("192.168.1.41", 50001)) == []   # same source, same second
    for i in range(200):
        answered(n, packet.T_A, src=(f"192.168.1.{50 + i % 150}", 40000 + i), qid=i)
    assert len(n.sent) <= rmod.LEGACY_PER_SEC
    n.t += 1.1   # (the inbound limit took the rest of that second)
    out = answered(n, packet.T_A)   # a multicast question still answered
    assert len(out) == 1 and out[0].dest is None


def test_sends_are_capped_whatever_comes_in() -> None:
    n = Net()
    n.run(3)
    n.sent.clear()
    for i in range(500):
        answered(n, packet.T_A, src=("192.168.1.41", 40000 + i), qid=i)
        answered(n, packet.T_ANY, src=("192.168.1.42", 5353), qid=i)
        n.t += 0.001
    assert len(n.sent) <= rmod.MAX_SENDS_PER_SEC + rmod.LEGACY_PER_SEC


def test_responses_not_from_5353_and_packets_from_off_the_lan_are_dropped() -> None:
    # L3
    n = Net()
    n.run(0.1)
    n.feed(taken_by("domovoi"), src=("192.168.1.30", 40000))   # a response from another port
    n.feed(taken_by("domovoi"), src=("172.17.0.2", 5353))       # a container's bridge
    n.feed(taken_by("domovoi"), src=("10.0.0.9", 5353))         # another private network
    n.run(3)
    assert n.r.claimed == "domovoi" and n.r.state == "announced"


def test_inbound_packets_are_rate_limited_before_parsing(monkeypatch) -> None:
    # M1: past INBOUND_PER_SEC a second, packets are dropped unread.
    n = Net()
    n.run(3)
    parsed = []
    real = packet.parse
    monkeypatch.setattr(packet, "parse", lambda d: parsed.append(1) or real(d))
    for i in range(1000):
        n.feed(b"\x00" * 12, src=("192.168.1.30", 5353))
    assert len(parsed) <= rmod.INBOUND_PER_SEC + 1


def test_the_worst_packet_costs_a_bounded_parse() -> None:
    # M1: names that all point back at one 126-label name stop at the
    # per-packet label budget; the worst case parses fast.
    import time

    for key in ("label_bomb_query", "label_bomb_response"):
        data = bytes.fromhex(VECTORS["bad_packets"][key])
        with pytest.raises(packet.ParseError):
            packet.parse(data)
    data = bytes.fromhex(VECTORS["bad_packets"]["label_bomb_query"])
    t0 = time.perf_counter()
    for _ in range(1000):
        try:
            packet.parse(data)
        except packet.ParseError:
            pass
    per_packet = (time.perf_counter() - t0) / 1000
    assert per_packet < 0.0005, per_packet   # 1000 worst packets < 0.5 s (was ~2 ms each)
    assert VECTORS["label_budget"] == packet.MAX_LABELS_PER_PACKET
    # A legitimate packet with many short names still parses.
    q = b"".join(packet.encode_name(packet.name_of(f"n{i}")) + struct.pack("!HH", 1, 1) for i in range(32))
    assert len(packet.parse(struct.pack("!HHHHHH", 0, 0, 32, 0, 0, 0) + q).questions) == 32


def test_questions_in_a_response_are_not_kept() -> None:
    q = packet.encode_name(packet.name_of("domovoi")) + struct.pack("!HH", 1, 1)
    m = packet.parse(struct.pack("!HHHHHH", 0, 0x8400, 1, 0, 0, 0) + q)
    assert m.is_response and m.questions == ()


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


class FakeWire:
    def __init__(self, ip, iface, on_packet):
        self.ip, self.iface, self.on_packet = ip, iface, on_packet
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
    wires: list[FakeWire] = []

    def wire(ip, iface, cb):
        wires.append(FakeWire(ip, iface, cb))
        return wires[-1]

    s = svc.LanNameService(lan_ip=lambda: IP, iface=lambda ip: svc.Iface("eth0", 2, 24), wire=wire)
    s.start()
    for _ in range(80):
        await asyncio.sleep(0.05)
        if s.responder.phase == "announced":
            break
    assert s.snapshot()["state"] == "announced" and s.snapshot()["addresses"] == [IP]
    assert svc.read_state() == {"requested": "domovoi", "claimed": "domovoi"}
    assert all(dest == ("224.0.0.251", 5353) for _, dest in wires[0].sent)
    assert s.responder.subnet is not None and str(s.responder.subnet) == "192.168.1.0/24"
    await s.stop()
    last = packet.parse(wires[0].sent[-1][0])
    assert last.answers[0].ttl == 0 and wires[0].closed


@pytest.mark.asyncio
async def test_a_virtual_interface_gets_no_name(tmp_path, monkeypatch) -> None:
    # L5: the default route through a tunnel or a bridge: no name.
    import asyncio

    from domovoi.config import settings

    monkeypatch.setenv("DOMOVOI_LAN_NAME_DIR", str(tmp_path / "ln"))
    monkeypatch.setattr(settings, "lan_name_enabled", True)
    s = svc.LanNameService(lan_ip=lambda: IP, iface=lambda ip: svc.Iface("wg0", 5, 24, "wg0"),
                           wire=lambda *a: pytest.fail("no socket for a tunnel"))
    s.start()
    await asyncio.sleep(0.2)
    assert s.snapshot()["state"] == "no_lan" and "wg0" in s.snapshot()["error"]
    await s.stop()


@pytest.mark.parametrize("name, virtual", [
    ("eth0", False), ("enp3s0", False), ("wlan0", False), ("tun0", True), ("wg0", True),
    ("ppp0", True), ("docker0", True), ("veth12ab", True), ("br-1f2e", True), ("virbr0", True),
    ("tailscale0", True),
])
def test_virtual_interfaces_by_name(name, virtual, tmp_path) -> None:
    assert bool(svc.is_virtual(name, sys_class_net=tmp_path)) is virtual


def test_virtual_interfaces_by_sysfs(tmp_path) -> None:
    real = tmp_path / "devices" / "pci0000_00" / "net" / "enp1s0"
    virt = tmp_path / "devices" / "virtual" / "net" / "lanbridge"
    real.mkdir(parents=True)
    virt.mkdir(parents=True)
    cls = tmp_path / "class"
    cls.mkdir()
    try:
        (cls / "enp1s0").symlink_to(real)
        (cls / "lanbridge").symlink_to(virt)
    except OSError:
        pytest.skip("no symlinks here")
    assert svc.is_virtual("enp1s0", sys_class_net=cls) is None
    assert svc.is_virtual("lanbridge", sys_class_net=cls) == "lanbridge"


def test_the_arriving_interface_is_read_from_pktinfo() -> None:
    data = struct.pack("i4s4s", 3, socket.inet_aton("192.168.1.20"), socket.inet_aton("224.0.0.251"))
    assert svc.arrived_on([(socket.IPPROTO_IP, svc._IP_PKTINFO, data)]) == 3
    assert svc.arrived_on([]) is None


@pytest.mark.asyncio
async def test_a_packet_from_another_interface_is_dropped(monkeypatch) -> None:
    # L3: a container on docker0 unicasting to 172.17.0.1:5353 arrives on
    # docker0's index, not the LAN's: dropped before the responder sees it.
    import asyncio

    got = []

    class FakeSock:
        def __init__(self):
            self.queue = [
                (b"a", [(socket.IPPROTO_IP, svc._IP_PKTINFO, struct.pack("i8x", 7))], 0, ("172.17.0.2", 5353)),
                (b"b", [(socket.IPPROTO_IP, svc._IP_PKTINFO, struct.pack("i8x", 2))], 0, ("192.168.1.30", 5353)),
                (b"c", [], 0, ("192.168.1.31", 5353)),
            ]

        def fileno(self):
            return 99

        def recvmsg(self, n, anc):
            if not self.queue:
                raise BlockingIOError
            return self.queue.pop(0)

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_reader", lambda *a: None)
    w = svc.SocketWire(FakeSock(), 2, lambda d, src: got.append((d, src)))
    w._readable()
    assert got == [(b"b", ("192.168.1.30", 5353))]


@pytest.mark.asyncio
async def test_a_failed_bind_says_failed(tmp_path, monkeypatch) -> None:
    import asyncio

    monkeypatch.setenv("DOMOVOI_LAN_NAME_DIR", str(tmp_path / "ln"))

    def boom(ip, iface, cb):
        raise PermissionError(13, "Permission denied")

    s = svc.LanNameService(lan_ip=lambda: IP, iface=lambda ip: None, wire=boom)
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

    from domovoi.tests.route_walk import iter_route_contexts

    def deps(app, path, method):
        for r in iter_route_contexts(app.routes):
            if getattr(r, "path", None) == path and method in (getattr(r, "methods", None) or set()):
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
