"""Friend Houses plugin mail over the wire (slice 4): ``LoopbackPair`` end to
end in both directions, the sender's answers and caps, the listener's
slice-4 rules (L7-L10), the decoder's readiness and host events (M3, L6),
the single listener-file writer (M2), the restore marker (M1) and the event
reader (L15)."""

from __future__ import annotations

import asyncio
import json

import pytest

from domovoi.db.session import session_scope
from domovoi.friend_houses import budget as fb
from domovoi.friend_houses import identity as fid
from domovoi.friend_houses import listener as fl
from domovoi.friend_houses import plugin_mail as pm
from domovoi.friend_houses import plugin_sender as ps
from domovoi.friend_houses import plugin_wire as pw
from domovoi.friend_houses import service as svc
from domovoi.friend_houses import session as fs
from domovoi.friend_houses.loopback import LoopbackPair
from domovoi.plugins_runtime import isolated as iso
from domovoi.sdk import friend_houses as sdk
from domovoi.tests.conftest import requires_db
from domovoi.tests.fh_plugin_mail_kit import (  # noqa: F401  (fixtures)
    Proc,
    echo,
    fake_docker,
    frame,
    house,
    make_pool,
    q,
    settle,
)
from domovoi.tests.friend_houses_testkit import no_tasks_left_behind  # noqa: F401  (autouse)


async def _inbox() -> list[tuple]:
    return await q("SELECT id, state, faults, seq FROM friend_houses_plugin_inbox ORDER BY id")


async def _pair(friend_id: str = "fa", slug: str = "games") -> dict:
    async with session_scope() as s:
        return await pm.pair_row(s, friend_id, slug)


async def _lane_idle() -> None:
    q_ = pm._LANE["queue"]
    if q_ is not None:
        await asyncio.wait_for(q_.join(), 10)


# ─── LoopbackPair: B receives (this process is B) ────────────────────────


@requires_db
async def test_loopback_send_store_ack_deliver(house, tmp_path) -> None:
    async with LoopbackPair(tmp_path, apps=("games",)) as pair:
        await pair.befriend()
        answer = await pair.mail("games", json.dumps({"move": "e4", "by": "Sam"}).encode())
        assert answer == pw.Answer(True)                    # ACK = committed
        assert [r[1] for r in await _inbox()] == ["stored"]
        await settle()
    (house_id, _mail_id, seq, value), = house.delivered
    assert house_id == pair.b.plugin_house_id("games", pair.a.house_key) and seq == 1
    assert isinstance(value["by"], sdk.FriendText) and value["by"].text == "Sam"


@requires_db
async def test_loopback_nacks_each_reason(house, tmp_path, monkeypatch) -> None:
    async with LoopbackPair(tmp_path, apps=("games",)) as pair:
        await pair.befriend()
        # not_allowed at the listener (unlisted) and at core (toggle off).
        assert (await pair.mail("chess", b"{}")).reason is fs.Nack.NOT_ALLOWED
        await q("UPDATE friend_houses_friends SET apps_on = FALSE")
        assert (await pair.mail("games", b"{}")).reason is fs.Nack.NOT_ALLOWED
        await q("UPDATE friend_houses_friends SET apps_on = TRUE")
        # full
        monkeypatch.setattr(pm, "PAIR_ROWS", 1)
        assert (await pair.mail("games", b'{"a":"1"}')).acked
        full = await pair.mail("games", b'{"a":"2"}')
        assert full.reason is fs.Nack.FULL and full.retry_after == pm.FULL_RETRY_SEC
        # unavailable: paused (the time left), or not loaded yet
        await q("UPDATE friend_houses_plugin_pairs SET paused_until = now() + interval '1 hour'")
        paused = await pair.mail("games", b'{"a":"3"}')
        assert paused.reason is fs.Nack.UNAVAILABLE and 3500 < paused.retry_after <= 3601
        # rate: the listener's buckets
        monkeypatch.setattr(fb, "PAIR_BURST", 0.5)
        pair.state.budgets = fb.PluginBudgets()
        assert (await pair.mail("games", b'{"a":"4"}')).reason is fs.Nack.RATE


@requires_db
async def test_loopback_replay_is_acked_once_and_an_old_epoch_is_refused(house, tmp_path) -> None:
    async with LoopbackPair(tmp_path, apps=("games",)) as pair:
        await pair.befriend()
        env = pair.envelope("games", b'{"move":"e4"}')
        assert (await pair.mail("games", b"", env=env)).acked
        await settle()
        assert (await pair.mail("games", b"", env=env)).acked       # a replay: ACKed, dropped
        await settle()
        assert len(house.delivered) == 1
        old = pair.epoch_a
        pair.epoch_a = b"\x0c" * 8                                   # A restored a backup
        assert (await pair.mail("games", b'{"move":"d4"}', seq=1)).acked
        pair.epoch_a = old                                           # a listener flipping back
        assert (await pair.mail("games", b'{"move":"x"}', seq=99)).reason is fs.Nack.NOT_ALLOWED


@requires_db
async def test_loopback_expiry_and_pause_and_resume(house, tmp_path) -> None:
    async with LoopbackPair(tmp_path, apps=("games",)) as pair:
        await pair.befriend()
        assert (await pair.mail("games", b'{"a":"1"}', ttl_s=30)).acked
        await q("UPDATE friend_houses_plugin_inbox SET expires_at = now() - interval '1 second'")
        await pm.sweep()
        assert await _inbox() == []
        for _ in range(3):
            await pm.charge_sink("games", "fa", None, iso.W_FAULT, "x", iso.KIND_DECODER)
        assert (await pair.mail("games", b'{"a":"2"}')).reason is fs.Nack.UNAVAILABLE
        await pm.resume("fa", "games")
        assert (await pair.mail("games", b'{"a":"3"}')).acked
        await settle()
        assert len(house.delivered) == 1


# ─── LoopbackPair: A sends (this process is A, its real sender) ──────────


def _scripted(lines: list[str]):
    """B's core: answers from ``lines`` in turn ("stored" or "refused <reason> <secs>")."""
    seen: list[dict] = []

    def core(event):
        seen.append(event)
        mid = bytes.fromhex(event["envelope"])[66:82].hex()
        word = lines.pop(0) if lines else "stored"
        return f"stored {mid}" if word == "stored" else f"refused {mid} {word}"

    core.seen = seen
    return core


async def _sender_pair(tmp_path, house, monkeypatch, lines):
    monkeypatch.setattr(ps, "gate_open", lambda: True)
    monkeypatch.setattr(ps, "wake", lambda friend_id: None)   # the test drives each round
    core = _scripted(lines)
    pair = LoopbackPair(tmp_path, apps=("games",), home="a", core=core)
    await pair.__aenter__()
    await pair.befriend()
    return pair, core


async def _round(friend_id: str = "fa"):
    sender = ps._Sender(friend_id)
    try:
        return sender, await sender.round()
    finally:
        sender.drop()
        await asyncio.gather(*list(pm._TASKS), return_exceptions=True)


@requires_db
async def test_sender_caps_first_then_mail_acked_and_caps_needed_cleared(house, tmp_path, monkeypatch) -> None:
    pair, core = await _sender_pair(tmp_path, house, monkeypatch, [])
    try:
        await q("UPDATE friend_houses_friends SET caps_needed = TRUE")
        await pm.send("games", "fa", b'{"move":"e4"}')
        await _round()
        assert await q("SELECT 1 FROM friend_houses_plugin_outbox") == []
        caps = [e for e in pair.events if e["event"] == "caps"]
        assert caps and caps[0]["apps"] == "games" and pair.events.index(caps[0]) < \
            pair.events.index(core.seen[0])
        f = await q("SELECT caps_needed, caps_sent_at IS NOT NULL, peer_proto FROM friend_houses_friends")
        assert f == [(False, True, 2)]
    finally:
        await pair.__aexit__(None, None, None)


@requires_db
async def test_l13b_the_remaining_ttl_goes_on_the_wire_and_too_little_expires_here(
        house, tmp_path, monkeypatch) -> None:
    pair, core = await _sender_pair(tmp_path, house, monkeypatch, [])
    try:
        await pm.send("games", "fa", b'{"a":"1"}', ttl_s=3600)
        await q("UPDATE friend_houses_plugin_outbox SET expires_at = now() + interval '100 seconds'")
        await _round()
        env = bytes.fromhex(core.seen[0]["envelope"])
        from domovoi.friend_houses import envelope as fe

        body = fe.parse(env, sender=pair.a.house_key, recipient=pair.b.house_key).plugin
        assert 95 <= body.ttl_s <= 100
        await pm.send("games", "fa", b'{"a":"2"}')
        await q("UPDATE friend_houses_plugin_outbox SET expires_at = now() + interval '10 seconds'")
        await _round()
        assert len(core.seen) == 1 and await q("SELECT 1 FROM friend_houses_plugin_outbox") == []
    finally:
        await pair.__aexit__(None, None, None)


@requires_db
async def test_full_and_unavailable_back_the_pair_off_doubling(house, tmp_path, monkeypatch) -> None:
    pair, core = await _sender_pair(tmp_path, house, monkeypatch, ["full 5", "unavailable 2"])
    try:
        await pm.send("games", "fa", b'{"a":"1"}')
        await _round()
        assert (await _pair())["pair_backoff_sec"] == 60          # full: from >= 60 s
        await q("UPDATE friend_houses_plugin_pairs SET pair_retry_at = NULL")
        await _round()
        assert (await _pair())["pair_backoff_sec"] == 120         # doubled on the next refusal
        assert len(await q("SELECT 1 FROM friend_houses_plugin_outbox WHERE state = 'queued'")) == 1
        assert (await q("SELECT backoff_sec FROM friend_houses_plugin_sender"))[0][0] == 0
        await q("UPDATE friend_houses_plugin_pairs SET pair_retry_at = NULL")
        await _round()                                            # ACK: both reset
        assert (await _pair())["pair_backoff_sec"] == 0
    finally:
        await pair.__aexit__(None, None, None)


def test_l1_pair_backoff_rules() -> None:
    assert ps.next_pair_backoff(0, fs.Nack.UNAVAILABLE, 2) == 10       # floor 10 for unavailable
    assert ps.next_pair_backoff(0, fs.Nack.FULL, 2) == 60
    assert ps.next_pair_backoff(10, fs.Nack.UNAVAILABLE, 2) == 20
    assert ps.next_pair_backoff(2400, fs.Nack.UNAVAILABLE, 2) == 3600
    assert ps.next_pair_backoff(20, fs.Nack.UNAVAILABLE, 7000) == 7000   # never below the retry-after


def test_friend_backoff_steps() -> None:
    steps, cur = [], 0
    for _ in range(10):
        cur = ps.next_friend_backoff(cur)
        steps.append(cur)
    assert steps == [2, 5, 15, 60, 300, 600, 1200, 2400, 3600, 3600]
    assert ps.next_friend_backoff(0, 900) == 900


@requires_db
async def test_rate_and_no_answer_back_the_friend_off_only_an_ack_resets(house, tmp_path, monkeypatch) -> None:
    pair, core = await _sender_pair(tmp_path, house, monkeypatch, ["rate 40"])
    try:
        await pm.send("games", "fa", b'{"a":"1"}')
        _, wait = await _round()
        assert wait == 40.0
        assert (await q("SELECT backoff_sec FROM friend_houses_plugin_sender"))[0][0] == 40
        await q("UPDATE friend_houses_plugin_sender SET retry_at = NULL")
        await _round()
        assert (await q("SELECT backoff_sec, retry_at FROM friend_houses_plugin_sender"))[0] == (0, None)
    finally:
        await pair.__aexit__(None, None, None)


@requires_db
async def test_m2_not_allowed_deletes_only_that_mail_and_holds_then_a_second_wipes(
        house, tmp_path, monkeypatch) -> None:
    pair, core = await _sender_pair(tmp_path, house, monkeypatch, ["not_allowed 0", "not_allowed 0"])
    try:
        for i in range(3):
            await pm.send("games", "fa", json.dumps({"i": str(i)}).encode())
        await _round()
        assert len(await q("SELECT 1 FROM friend_houses_plugin_outbox")) == 2
        p = await _pair()
        assert p["na_first_at"] is not None and p["na_hold_until"] is not None
        _, wait = await _round()                                   # held: nothing goes
        assert len(core.seen) == 1
        # The hold ends; a second not_allowed 5+ minutes after the first.
        await q("UPDATE friend_houses_plugin_pairs SET na_hold_until = NULL, "
                "na_first_at = now() - interval '6 minutes'")
        await _round()
        assert await q("SELECT 1 FROM friend_houses_plugin_outbox") == []
        assert (await q("SELECT peer_caps FROM friend_houses_friends"))[0][0] == []   # has_plugin cleared
    finally:
        await pair.__aexit__(None, None, None)


@requires_db
async def test_l4_the_lowest_row_id_goes_first_across_pairs(house, tmp_path, monkeypatch) -> None:
    house.states["chess"] = "ready"
    pair, core = await _sender_pair(tmp_path, house, monkeypatch, [])
    try:
        await q("INSERT INTO friend_houses_plugin_pairs (friend_id, slug, allowed) VALUES ('fa', 'chess', TRUE)")
        await q("UPDATE friend_houses_friends SET peer_caps = '{games,chess}'")
        pair.set_listener_apps(("games", "chess"))
        await pm.send("games", "fa", b'{"n":"1"}')
        await pm.send("chess", "fa", b'{"n":"2"}')
        await q("UPDATE friend_houses_plugin_pairs SET out_seq = 0 WHERE slug = 'chess'")
        await pm.send("chess", "fa", b'{"n":"3"}')                 # a lower seq than games' row
        await pm.send("games", "fa", b'{"n":"4"}')
        await _round()
        from domovoi.friend_houses import envelope as fe

        got = [json.loads(fe.parse(bytes.fromhex(e["envelope"]), sender=pair.a.house_key,
                                   recipient=pair.b.house_key).plugin.payload)["n"] for e in core.seen]
        assert got == ["1", "2", "3", "4"]
    finally:
        await pair.__aexit__(None, None, None)


@requires_db
async def test_l1_a_resend_spends_the_budgets_again(house, tmp_path, monkeypatch) -> None:
    pair, core = await _sender_pair(tmp_path, house, monkeypatch, ["unavailable 2"])
    try:
        spent: list[int] = []
        real = pm.spend_send_budget

        async def spy(s, friend_id, slug, size):
            spent.append(size)
            return await real(s, friend_id, slug, size)

        monkeypatch.setattr(pm, "spend_send_budget", spy)
        await pm.send("games", "fa", b'{"a":"1"}')
        await _round()
        await q("UPDATE friend_houses_plugin_pairs SET pair_retry_at = NULL")
        await _round()
        assert len(spent) == 2       # at enqueue, and again on the resend
    finally:
        await pair.__aexit__(None, None, None)


@requires_db
async def test_l2_thirty_dials_an_hour_survive_a_restart(house, tmp_path) -> None:
    from domovoi.tests.fh_plugin_mail_kit import add_friend

    await add_friend(tmp=tmp_path)
    for _ in range(ps.DIALS_PER_HOUR):
        assert await ps._Sender("fa")._note_dial() == 0
    # A new sender (a core restart) reads the same log.
    wait = await ps._Sender("fa")._note_dial()
    assert 3500 < wait <= 3600


async def test_at_the_held_cap_mail_waits(monkeypatch) -> None:
    monkeypatch.setitem(ps._HELD, "n", ps.HELD_MAX)
    assert await ps._Sender("fa")._open({}, ()) == 30.0
    ps._HELD["n"] = 0


@requires_db
async def test_l8_legacy_only_after_two_closes_five_minutes_apart(house, tmp_path) -> None:
    from domovoi.tests.fh_plugin_mail_kit import add_friend

    await add_friend(tmp=tmp_path)
    sender = ps._Sender("fa")
    async with session_scope() as s:
        f = await pm.friend_row(s, friend_id="fa")
    await sender._probed(f, pw.Offer.LEGACY, ())
    assert (await q("SELECT peer_proto, probe_closes FROM friend_houses_friends"))[0] == (None, 1)
    await sender._probed(f, pw.Offer.LEGACY, ())                  # a second close, too soon
    assert (await q("SELECT peer_proto FROM friend_houses_friends"))[0][0] is None
    await sender._probed(f, pw.Offer.NO_ANSWER, ())               # decides nothing
    await q("UPDATE friend_houses_friends SET first_close_at = now() - interval '6 minutes'")
    await sender._probed(f, pw.Offer.LEGACY, ())
    assert (await q("SELECT peer_proto FROM friend_houses_friends"))[0][0] == 1


async def test_l8_a_send_error_in_the_probe_is_no_answer(tmp_path) -> None:
    class _Broken:
        peer_house_key = b"\x01" * 32
        peer = type("P", (), {"statement": None, "epoch": b"\x02" * 8})()

        async def send(self, kind, body=b""):
            raise ConnectionError("gone")

    async def saw(house, statement):
        return None

    assert await pw.offer(_Broken(), pw.encode_caps(()), saw=saw, known=pw.Known()) is pw.Offer.NO_ANSWER


# ─── the listener: L7, L9 ────────────────────────────────────────────────


def test_l7_a_per_friend_byte_bucket_and_a_fixed_house_retry() -> None:
    t = [0.0]
    b = fb.PluginBudgets(clock=lambda: t[0])
    big = 16 * 1024
    let = sum(b.spend(b"\x01" * 32, f"s{i % 30:02d}x", big) == 0 for i in range(200))
    assert let == fb.FRIEND_BYTES_BURST // big                    # 1 MiB a minute for one friend
    # The house buckets: a refusal there says a fixed 60 s, not the refill.
    h = fb.PluginBudgets(clock=lambda: t[0])
    for i in range(fb.HOUSE_BURST):
        assert h.spend(bytes([i % 250, i // 250]) * 16, "games", 10) == 0
    assert h.spend(b"\xff" * 32, "games", 10) == fb.HOUSE_RETRY_SEC


@requires_db
async def test_l9_sixteen_unlisted_refusals_close_the_connection(house, tmp_path) -> None:
    async with LoopbackPair(tmp_path, apps=("games",)) as pair:
        sess = await pair.dial()
        await sess.send(fs.Kind.CAPS, pw.encode_caps(("chess",)))
        for i in range(fl.NOT_ALLOWED_PER_CONNECTION):
            env = pair.envelope("chess", b"{}")
            await sess.send(fs.Kind.MESSAGE, env.to_bytes())
            assert (await pw.await_answer(sess, env.msg_id, deadline=5)).reason is fs.Nack.NOT_ALLOWED
        with pytest.raises(fs.SessionRefused):
            await sess.recv(timeout=5)
        await sess.close()


# ─── the decoder: M3 readiness and host events, L6 ───────────────────────


async def test_m3_the_timer_starts_after_readiness(fake_docker) -> None:
    told: list[tuple] = []

    async def sink(*a):
        told.append(a)
        return None

    iso.CHARGE_SINK = sink
    try:
        pool, procs = await make_pool("slowstart", ready=False, timeout=0.2, ready_timeout=2.0)

        async def late_ready():
            await asyncio.sleep(0.4)            # a cold start longer than the decode deadline
            procs[0].stdout.feed_data(iso.READY_BYTE)

        task = asyncio.ensure_future(late_ready())
        r = await pool.decode("f", b'{"a":"b"}')
        await task
        assert r.ok and told == []
        await pool.stop()
    finally:
        iso.CHARGE_SINK = None
        iso.POOLS.pop("slowstart", None)


async def test_m3_an_idle_death_charges_nobody(fake_docker) -> None:
    told: list[tuple] = []

    async def sink(*a):
        told.append(a)
        return None

    iso.CHARGE_SINK = sink
    try:
        pool, procs = await make_pool("idle")
        assert (await pool.decode("f", b'{"a":"b"}')).ok
        procs[0].stdout.feed_eof()              # dies with nothing in flight
        for _ in range(100):
            if "f" not in pool.instances:
                break
            await asyncio.sleep(0.01)
        assert "f" not in pool.instances and told == []
        await pool.stop()
    finally:
        iso.CHARGE_SINK = None
        iso.POOLS.pop("idle", None)


async def test_m3_a_crash_in_flight_alone_is_charged(fake_docker) -> None:
    told: list[tuple] = []

    async def sink(slug, friend, item, w, why, kind):
        told.append((friend, item, w, kind))
        return None

    iso.CHARGE_SINK = sink
    try:
        pool, _ = await make_pool("crashy", lambda rid, k, m, p: "crash")
        r = await pool.decode("f", b"{}", item_key="7")
        assert r.outcome == "fault" and told == [("f", "7", iso.W_FAULT, iso.KIND_DECODER)]
        await pool.stop()
    finally:
        iso.CHARGE_SINK = None
        iso.POOLS.pop("crashy", None)


async def test_l6_two_decodes_per_friend_across_plugins_and_slow_items_weigh_ten(fake_docker) -> None:
    told: list[tuple] = []

    async def sink(slug, friend, item, w, why, kind):
        told.append((w, kind))
        return None

    iso.CHARGE_SINK = sink
    pools: list[iso.DecoderPool] = []
    try:
        for i in range(3):
            pools.append((await make_pool(f"p{i}x", lambda rid, k, m, p: None, timeout=1.0))[0])
        tasks = [asyncio.ensure_future(p.decode("f", b"{}")) for p in pools[:2]]
        for _ in range(100):
            if iso._DECODING.get("f") == 2 and all(p.instances.get("f") and p.instances["f"]._rid
                                                    for p in pools[:2]):
                break
            await asyncio.sleep(0.01)
        third = await pools[2].decode("f", b"{}")
        assert third.outcome == "unavailable" and "already has items decoding" in third.reason
        await asyncio.sleep(0.85)              # > 80% of the 1 s deadline
        for p in pools[:2]:
            inst = p.instances["f"]
            inst.proc.stdout.feed_data(frame(inst._rid, 0, b'{"a":"b"}'))
        results = await asyncio.gather(*tasks)
        assert all(r.ok for r in results)
        assert told.count((iso.W_SLOW, iso.KIND_SLOW)) == 2
    finally:
        for p in pools:
            await p.stop()
        iso.CHARGE_SINK = None
        for i in range(3):
            iso.POOLS.pop(f"p{i}x", None)


def test_m4_the_breaker_counts_integers_and_the_versions_pause_does_not_double() -> None:
    b = iso.Breaker()
    for _ in range(149):
        assert b.refusal(1000.0) is None
    assert b.refusal(1000.0) == 3600 and b.cause == "versions" and b.next_pause == 3600
    b = iso.Breaker()
    assert b.charge(0.0, 100, "d") is None and b.charge(0.0, 100, "h") is None
    assert b.charge(0.0, 100, "h") == 3600 and b.cause == "handler" and b.next_pause == 7200
    b = iso.Breaker()
    assert b.charge(0.0, 300, "r") == 3600 and b.cause == "restart"
    # 24 h without a pause: the length starts over.
    b.next_pause = 86_400
    assert b.charge(90_000.0, 300, "r") == 3600


# ─── M2: one writer, reading under its lock ──────────────────────────────


@requires_db
async def test_m2_a_slow_reconcile_write_cant_drop_a_newly_allowed_slug(house, tmp_path, monkeypatch) -> None:
    from domovoi.tests.fh_plugin_mail_kit import add_friend

    monkeypatch.undo()                     # the real writer, not the fixture's recorder
    monkeypatch.setattr(pm, "plugin_state", _ready)
    monkeypatch.setattr(pm, "permitted_slugs", _games)
    monkeypatch.setattr(ps, "gate_open", lambda: False)
    monkeypatch.setattr(fid, "load", lambda path=None: house.keys)
    folder = tmp_path / "listener"
    monkeypatch.setattr(svc, "_listener_folder", lambda: folder)
    await add_friend(tmp=tmp_path, slugs=())
    real_inputs = svc._inputs
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_inputs():
        out = await real_inputs()
        entered.set()
        await release.wait()          # a reconcile holding its read for minutes
        return out

    monkeypatch.setattr(svc, "_inputs", slow_inputs)
    first = asyncio.ensure_future(svc.write_listener_files())   # the reconcile's write
    await entered.wait()
    monkeypatch.setattr(svc, "_inputs", real_inputs)
    allow = asyncio.ensure_future(pm.set_pair("fa", "games", True))
    await asyncio.sleep(0.1)
    release.set()
    await first
    await allow
    doc = json.loads((folder / fl.FRIENDS_FILE).read_text())
    assert [e["apps"] for e in doc.values()] == [["games"]]
    # And the CAPS that adds the slug comes after the file: caps_needed now.
    assert (await q("SELECT caps_needed FROM friend_houses_friends"))[0][0] is True


async def _ready(slug):
    return "ready"


async def _games():
    return ["games"]


# ─── M1: the restore marker at every start ───────────────────────────────


@requires_db
async def test_m1_a_fresh_marker_at_start_makes_a_restored_dump_start_a_new_epoch(
        db_session, tmp_path, monkeypatch) -> None:
    keys = fid.create(tmp_path / "k.json")
    monkeypatch.setattr(fid, "load", lambda path=None: keys)
    monkeypatch.setattr(svc.sys, "platform", "linux")      # Friend Houses runs on Linux
    marker = tmp_path / "state-marker"
    monkeypatch.setattr(svc, "_marker_path", lambda: marker)
    first = await svc.current_epoch()
    dump_marker = (await q("SELECT marker FROM friend_houses_state"))[0][0]   # last night's backup
    await svc.refresh_marker()                                               # a core start
    now_marker = (await q("SELECT marker FROM friend_houses_state"))[0][0]
    assert now_marker != dump_marker and marker.read_text().strip() == now_marker
    assert await svc.current_epoch() == first                                # same epoch
    await q("INSERT INTO friend_houses_friends (id, house_key, session_key, onion_key, statement, "
            "statement_at, reach_public, local_name, state, apps_on) VALUES ('fa', :h, :h, :h, '\\x00', 1, :h, "
            "'Sam', 'active', TRUE)", h=b"\x05" * 32)
    await q("UPDATE friend_houses_state SET marker = :m", m=dump_marker)    # the dump is restored
    assert await svc.current_epoch() != first                               # a new epoch
    assert (await q("SELECT caps_needed FROM friend_houses_friends"))[0][0] is True


async def test_m1_current_epoch_runs_under_the_files_lock(monkeypatch) -> None:
    seen: list[bool] = []

    async def locked(**kw):
        seen.append(svc._FILES_LOCK.locked())
        return b"\x00" * 8

    monkeypatch.setattr(svc, "_current_epoch_locked", locked)
    await svc.current_epoch()
    assert seen == [True]


# ─── L15: the event reader ───────────────────────────────────────────────


async def test_l15_a_recursion_error_restarts_the_listener(monkeypatch) -> None:
    killed: list[int] = []

    class _Proc:
        returncode = None

        def __init__(self):
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_data(b'{"event": "listening"}\n')
            self.stdout.feed_eof()

        def kill(self):
            killed.append(1)

    real = json.loads
    monkeypatch.setattr(svc.json, "loads", lambda raw: (_ for _ in ()).throw(RecursionError()) if b"listening" in raw
                        else real(raw))
    await svc._read_events(_Proc())
    assert killed == [1]


def test_core_waits_on_a_fixed_ack_deadline() -> None:
    assert ps.ACK_SEC == 30.0 and ps.ROUND_SEC == 120.0 and ps.IDLE_SEC == 110.0
    assert ps.CONNECT_SEC == 180.0 and svc.PIPE_WRITE_SEC == 5.0



# ─── the plugin lifecycle calls (§6.9, L11, L12) ─────────────────────────


@pytest.mark.parametrize("old, new, call", [
    ({"friend_houses": True}, {}, "on_plugin_off"),          # L12: dropped: a closure
    ({}, {"friend_houses": True}, "on_plugin_new"),          # gained: re-confirm
    ({"friend_houses": True}, {"friend_houses": True}, "on_plugin_upgraded"),   # L11
    ({}, {}, None),
])
async def test_an_upgrade_tells_the_service_by_the_permission(old, new, call, monkeypatch) -> None:
    from domovoi.plugins_runtime import installer

    called: list[tuple] = []

    async def hook(name, slug):
        called.append((name, slug))

    monkeypatch.setattr(installer, "_fh_hook", hook)
    await installer._fh_hook_upgrade("games", {"permissions": old}, {"permissions": new})
    assert called == ([(call, "games")] if call else [])


async def test_disable_takes_the_lifecycle_lock_and_closes_the_apps(monkeypatch) -> None:
    from types import SimpleNamespace

    from domovoi.plugins_runtime import installer

    seen: list[tuple] = []

    async def get_plugin(slug):
        return SimpleNamespace(manifest={"permissions": {"friend_houses": True}})

    async def nothing(*a, **k):
        return None

    async def off(slug):
        seen.append((slug, installer.lifecycle_lock().locked()))

    monkeypatch.setattr(installer.reg, "get_plugin", get_plugin)
    monkeypatch.setattr(installer.reg, "set_enabled", nothing)
    monkeypatch.setattr(installer.LOADER, "unload_plugin", nothing)
    monkeypatch.setattr(installer, "_best_effort_resync", nothing)
    monkeypatch.setattr(pm, "on_plugin_off", off)
    assert (await installer.disable_plugin("games")) == {"enabled": False, "slug": "games"}
    assert seen == [("games", True)]


# ─── presence carries the CAPS (§6.8) ────────────────────────────────────


@requires_db
async def test_a_presence_dial_carries_the_caps_hourly_to_apps_on_proto_2_friends(house, tmp_path) -> None:
    from domovoi.friend_houses import presence
    from domovoi.tests.fh_plugin_mail_kit import add_friend

    await add_friend(tmp=tmp_path, caps=True)
    await add_friend("fb", tmp=tmp_path, apps_on=False, slugs=())
    await q("UPDATE friend_houses_friends SET peer_proto = 2, caps_needed = TRUE")
    presence._CAPS_CARRIED.clear()
    caps = await presence._caps_to_carry("fa")
    assert caps is not None and caps[0] == ("games",)
    assert await presence._caps_to_carry("fb") is None          # apps off: never told
    await presence._caps_carried("fa", caps[0])
    assert (await q("SELECT caps_needed FROM friend_houses_friends WHERE id = 'fa'"))[0][0] is False
    assert await presence._caps_to_carry("fa") is None          # at most hourly


@requires_db
async def test_l14_one_plugin_lane_connection_reused_across_rounds_and_replaced_on_new_caps(
        house, tmp_path, monkeypatch) -> None:
    pair, core = await _sender_pair(tmp_path, house, monkeypatch, [])
    dials: list[int] = []
    real = ps.DIAL

    async def counting(friend):
        dials.append(1)
        return await real(friend)

    monkeypatch.setattr(ps, "DIAL", counting)
    sender = ps._Sender("fa")
    try:
        for i in range(2):
            await pm.send("games", "fa", json.dumps({"i": str(i)}).encode())
            await sender.round()
        assert len(dials) == 1 and len(core.seen) == 2           # the held connection outlives a round
        house.states["chess"] = "ready"
        await q("INSERT INTO friend_houses_plugin_pairs (friend_id, slug, allowed) VALUES ('fa', 'chess', TRUE)")
        pair.set_listener_apps(("games", "chess"))
        await pm.send("games", "fa", b'{"i":"2"}')
        await sender.round()
        assert len(dials) == 2                                    # a new CAPS: a new connection
    finally:
        sender.drop()
        await asyncio.gather(*list(pm._TASKS), return_exceptions=True)
        await pair.__aexit__(None, None, None)


@requires_db
async def test_l13d_a_decoded_image_stays_in_the_row_across_a_restart(house, tmp_path) -> None:
    from domovoi.tests.fh_plugin_mail_kit import add_friend

    await add_friend(tmp=tmp_path)
    png = iso.encode_png(1, 1, 3, b"\x01\x02\x03")
    await q("INSERT INTO friend_houses_plugin_inbox (friend_id, slug, generation, mail_id, epoch, seq, canonical, "
            "image_png, size, expires_at, state) VALUES ('fa', 'games', 1, :m, :e, 1, '\x7b7d', :p, 2, "
            "now() + interval '1 hour', 'handling')", m=b"\x01" * 16, e=b"\x02" * 8, p=png)
    await pm.recover_after_start()
    assert (await q("SELECT image_png, state FROM friend_houses_plugin_inbox")) == [(png, "decoded")]
    assert "octet_length(image_png)" in pm._BYTES["inbox"]     # it counts toward the inbox's bytes
