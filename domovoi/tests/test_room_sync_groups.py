"""Rooms that share a queue (or a group) play in sync with each other
(owner decision 2026-11-22): one decoder per host, its timeline read by
every room following the host, so every speaker of every grouped room plays
the same sample at the same server time. A room's own answers, ducks and
speakers stay its own.

The den hosts; the kitchen follows it. Fake speakers, a fake clock and the
MPD stream from an endless tone (``LiveSource``), as in test_room_sound.
"""

from __future__ import annotations

import numpy as np
import pytest

from domovoi import music_groups, room_sound, streaming
from domovoi.clients import mpd as mpd_client
from domovoi.room_playout import protocol as P
from domovoi.tests.test_room_sound import OLD, SYNCED, URL, House, _quiet_mpd  # noqa: F401


@pytest.fixture
def den_hosts(monkeypatch):
    """The kitchen is in the den's shared queue."""
    hosts = {"kitchen": "den"}
    monkeypatch.setattr(music_groups, "host_of", lambda r: hosts.get(r, r))
    return hosts


def two_rooms(kitchen_abilities=SYNCED, kitchen_attached: bool = True) -> House:
    return House([("d_sat", "den", SYNCED, True), ("d_phone", "den", SYNCED, True),
                  ("k_sat", "kitchen", kitchen_abilities, kitchen_attached)])


async def start(h: House, sid: str, room: str) -> bool:
    return await streaming.send_music_start(h.app, h.s[sid], room, URL)


def by_index(frames: list[P.AudioFrame]) -> dict[int, tuple[int, bytes]]:
    return {f.first_sample: (f.play_at_us, f.payload) for f in frames}


async def test_two_rooms_in_a_group_play_the_same_samples_at_the_same_times(den_hosts) -> None:
    h = two_rooms()
    assert await start(h, "d_sat", "den") and await start(h, "k_sat", "kitchen")
    h.run(900)
    assert len(h.sources) == 1                                   # one decoder for the host
    den, kit = h.sinks["d_sat"].audio, h.sinks["k_sat"].audio
    assert len(den) > 20 and den[0].first_sample == kit[0].first_sample
    assert by_index(den) == by_index(kit)                        # first_sample, play_at, samples
    # Two timelines (one per room), one epoch: index n is one moment in both.
    t_den, t_kit = h.sinks["d_sat"].of("timeline")[-1], h.sinks["k_sat"].of("timeline")[-1]
    assert t_den["timeline_id"] != t_kit["timeline_id"] and t_den["t0_us"] == t_kit["t0_us"]
    assert h.pl.following("den") == ["den", "kitchen"]


async def test_pause_resume_and_skip_land_together_in_every_room(den_hosts) -> None:
    h = two_rooms()
    await start(h, "d_sat", "den")
    await start(h, "k_sat", "kitchen")
    h.run(600)
    player = mpd_client.get_mpd_client_for("den")
    await player.pause()
    flushes = {sid: s.of("flush")[-1] for sid, s in h.sinks.items()}
    assert len({f["at_us"] for f in flushes.values()}) == 1      # one moment, every speaker
    sent = {sid: len(s.audio) for sid, s in h.sinks.items()}
    h.run(600)
    assert {sid: len(s.audio) for sid, s in h.sinks.items()} == sent   # silent everywhere
    await player.resume()
    h.run(700)
    den, kit = by_index(h.sinks["d_sat"].audio[sent["d_sat"]:]), by_index(h.sinks["k_sat"].audio[sent["k_sat"]:])
    assert den and den == kit                                     # resumed at one index
    # A skip: one flush time in both rooms, both carry on from the same index.
    mpd_client._tell_playout("den", "skip")
    skip = {sid: s.of("flush")[-1]["at_us"] for sid, s in h.sinks.items()}
    assert len(set(skip.values())) == 1
    d_st, k_st = h.pl.streamers["den"], h.pl.streamers["kitchen"]
    assert d_st.next_index == k_st.next_index and d_st.play_at(d_st.next_index) >= skip["d_sat"]
    n_den, n_kit = len(h.sinks["d_sat"].audio), len(h.sinks["k_sat"].audio)
    h.run(400)
    after_den = by_index(h.sinks["d_sat"].audio[n_den:])
    after_kit = by_index(h.sinks["k_sat"].audio[n_kit:])
    assert after_den and after_den == after_kit


async def test_a_room_joining_mid_song_comes_in_where_the_song_is(den_hosts) -> None:
    h = two_rooms()
    await start(h, "d_sat", "den")
    h.run(2000)
    now = h.clock.t
    assert await start(h, "k_sat", "kitchen")
    h.run(10)
    kit = h.sinks["k_sat"].audio
    den = by_index(h.sinks["d_sat"].audio)
    assert len(h.sources) == 1
    first = kit[0]
    # It plays within moments (not a fresh playout buffer later), and from
    # the den's place in the song: the same samples at the same time.
    assert first.play_at_us - now < 100_000
    assert den[first.first_sample] == (first.play_at_us, first.payload)
    h.run(600)
    k = by_index(h.sinks["k_sat"].audio)
    assert all(den_v == k[i] for i, den_v in by_index(h.sinks["d_sat"].audio).items() if i in k)


async def test_a_room_that_leaves_goes_back_to_its_own_stream(den_hosts) -> None:
    h = two_rooms()
    await start(h, "d_sat", "den")
    await start(h, "k_sat", "kitchen")
    h.run(600)
    # Leaving: the room's music stops (music_groups._silence); the host plays on.
    assert room_sound.stop_music(h.app, "kitchen")
    assert h.pl.following("den") == ["den"] and not h.sources[0].closed
    n = len(h.sinks["d_sat"].audio)
    h.run(600)
    assert len(h.sinks["d_sat"].audio) > n
    # Its own music next: its own decoder, its own host.
    del den_hosts["kitchen"]
    assert await start(h, "k_sat", "kitchen")
    assert len(h.sources) == 2 and h.pl.music_host["kitchen"] == "kitchen"
    assert h.pl.following("kitchen") == ["kitchen"]
    assert h.pl.streamers["kitchen"].feed is not h.pl.streamers["den"].feed
    # A pause on the den's player no longer reaches the kitchen.
    assert h.pl.music_transport("den", "pause") == ["den"]


async def test_a_room_switching_into_a_group_cuts_over_at_one_moment(den_hosts) -> None:
    h = two_rooms()
    del den_hosts["kitchen"]
    await start(h, "d_sat", "den")
    await start(h, "k_sat", "kitchen")       # its own music first
    h.run(1000)
    assert len(h.sources) == 2
    den_hosts["kitchen"] = "den"
    flushes = len(h.sinks["k_sat"].of("flush"))
    assert await start(h, "k_sat", "kitchen")
    assert len(h.sinks["k_sat"].of("flush")) == flushes + 1   # its own song cut, timed
    assert h.sources[1].closed                                  # its own decoder gone
    cut = h.sinks["k_sat"].of("flush")[-1]["at_us"]
    n = len(h.sinks["k_sat"].audio)
    h.run(600)
    den = by_index(h.sinks["d_sat"].audio)
    new = h.sinks["k_sat"].audio[n:]
    assert new and new[0].play_at_us >= cut
    assert all(den[f.first_sample] == (f.play_at_us, f.payload) for f in new)


async def test_a_duck_or_an_answer_in_one_room_leaves_the_other_alone(den_hosts) -> None:
    h = two_rooms()
    await start(h, "d_sat", "den")
    await start(h, "k_sat", "kitchen")
    h.run(600)
    # A wake word in the kitchen: its music falls silent, the den's doesn't.
    assert room_sound.turn_started(h.app, "kitchen")
    assert h.sinks["k_sat"].of("gain") and not h.sinks["d_sat"].of("gain")
    # The kitchen's answer is mixed into the kitchen only.
    n_den, n_kit = len(h.sinks["d_sat"].audio), len(h.sinks["k_sat"].audio)
    tone = (np.sin(np.arange(24_000) / 7) * 9000).astype("<i2").tobytes()
    start_us, end_us = h.pl.say("kitchen", tone, 48_000, 1)
    assert not h.sinks["d_sat"].of("speech") and h.sinks["k_sat"].of("speech")
    h.run(1200)
    den = by_index(h.sinks["d_sat"].audio[n_den:])
    kit = by_index(h.sinks["k_sat"].audio[n_kit:])
    spoken = [i for i, (at, _) in kit.items() if start_us <= at < end_us]
    assert spoken and all(kit[i][0] == den[i][0] for i in spoken)       # same moments
    assert all(kit[i][1] != den[i][1] for i in spoken)                  # kitchen mixed
    # Outside the answer both rooms still play the same samples.
    assert all(kit[i] == den[i] for i in kit if i in den and kit[i][0] >= end_us + 40_000)


async def test_a_room_without_room_audio_keeps_its_own_stream_player(den_hosts) -> None:
    h = two_rooms(kitchen_abilities=OLD, kitchen_attached=False)
    await start(h, "d_sat", "den")
    await start(h, "k_sat", "kitchen")
    assert h.s["k_sat"].ws.of("music_start")                 # today's plain stream
    assert h.pl.following("den") == ["den"]
    assert not h.pl.playing_music("kitchen")


def test_every_room_timeline_shares_the_epoch() -> None:
    from domovoi.room_playout.service import RoomPlayout

    pl = RoomPlayout(clock=lambda: 5_000_000, run_tasks=False)
    assert pl.streamer("a").t0_us == pl.streamer("b").t0_us == pl.epoch_us
