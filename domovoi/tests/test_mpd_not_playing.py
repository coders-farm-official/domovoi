"""A stopped room is not an unreachable music player (F-V021).

MPD answers ``next``/``previous`` with ACK 55
(``ACK_ERROR_PLAYER_SYNC``, "Not playing") when the player is stopped.
``RealMPDClient`` let that ``CommandError`` propagate and
``MusicHandler._simple_ack`` caught every exception alike, so saying
"previous" to an idle room answered **"I couldn't reach the music
player."** — a connection complaint about a daemon that had just
answered. It sent a verification run hunting a dead daemon, and it is
wrong in the ordinary case too: a track that simply ended leaves the
room stopped.

Observed live (voice run 20260922-224651, room vt-kitchen, container
domovoi-vt-mpd-vt-kitchen up with RestartCount 0 and its control port
answering on 127.0.0.1:6750 throughout)::

    WARNING domovoi.handlers.music: MPD previous failed: [55@0] {previous} Not playing
    WARNING domovoi.handlers.music: MPD next failed: [55@0] {next} Not playing

Pure client/handler checks against the stub — no Docker, no DB, no
``requires_db`` — so they can never skip.
"""

from __future__ import annotations

import pytest

from domovoi.clients.mpd import MPDNotPlaying, MPDStubClient, _is_not_playing
from domovoi.handlers.music import MusicHandler
from domovoi.models import Context

UNREACHABLE = "I couldn't reach the music player."
IDLE = "Nothing is playing right now."


# ─── recognising MPD's refusal ────────────────────────────────────────────

@pytest.mark.parametrize(
    "text",
    [
        "[55@0] {previous} Not playing",
        "[55@0] {next} Not playing",
        "[55@1] {play} Not playing",
    ],
)
def test_ack_55_is_recognised_as_a_refusal(text: str) -> None:
    assert _is_not_playing(Exception(text))


@pytest.mark.parametrize(
    "text",
    [
        "Connection refused",
        "[50@0] {play} No such song",
        "Connection lost while reading MPD hello",
        "timed out",
    ],
)
def test_a_real_fault_is_not_mistaken_for_a_refusal(text: str) -> None:
    """The narrow half of the contract: a genuinely unreachable or broken
    daemon must still be reported as one."""
    assert not _is_not_playing(Exception(text))


# ─── the stub tells the same truth the daemon does ────────────────────────

async def test_the_stub_refuses_transport_while_stopped() -> None:
    """A stub that succeeded where the real daemon refuses is how this
    stayed green in the unit suite while failing in every real room."""
    client = MPDStubClient()
    assert await client.state() == "stop"
    with pytest.raises(MPDNotPlaying):
        await client.previous()
    with pytest.raises(MPDNotPlaying):
        await client.next()


async def test_the_stub_allows_transport_while_playing() -> None:
    client = MPDStubClient()
    await client.play_filename("anything")
    assert await client.state() == "play"
    await client.previous()
    await client.next()


# ─── what the room hears ──────────────────────────────────────────────────

async def _say(action: str, client: MPDStubClient) -> str:
    from domovoi.clients import mpd as mpd_module

    mpd_module._clients = {"kitchen": client}
    ctx = Context(session_id=None, room_id="kitchen", online=True)
    return (await MusicHandler()._simple_ack(action, ctx)).text


@pytest.mark.parametrize("action", ["previous", "next"])
async def test_transport_on_a_stopped_room_says_nothing_is_playing(action: str) -> None:
    """The finding itself: the answer names the real state and does not
    accuse the music player of being unreachable."""
    text = await _say(action, MPDStubClient())
    assert text == IDLE
    assert text != UNREACHABLE


@pytest.mark.parametrize(("action", "expected"),
                         [("previous", "Previous track."), ("next", "Next track.")])
async def test_transport_on_a_playing_room_is_unchanged(action: str, expected: str) -> None:
    client = MPDStubClient()
    await client.play_filename("anything")
    assert await _say(action, client) == expected


@pytest.mark.parametrize("action", ["previous", "next"])
async def test_a_daemon_that_is_really_unreachable_still_says_so(action: str) -> None:
    """Guard the other direction: this fix must not swallow a real
    outage into a soothing "nothing is playing"."""
    class Dead(MPDStubClient):
        async def previous(self) -> None:
            raise OSError("connection refused")

        async def next(self) -> None:
            raise OSError("connection refused")

    assert await _say(action, Dead()) == UNREACHABLE
