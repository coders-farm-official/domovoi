"""Audio requests before any satellite has connected must degrade, not 500.

Per-room MPD daemons are provisioned lazily on a satellite's first connect,
so a fresh install legitimately has none. Exercising the server over
``/v1/intent`` before building a Pi is the documented first-run check
(docs/SETUP_RUNBOOK.md Step 4), and "play some jazz in the kitchen" used to
raise a bare RuntimeError straight through FastAPI as a 500.

These drive the REAL path — a genuine music fast path into ``_play``, with
``get_mpd_client_for`` raising — rather than substituting the handler
method. ``FastPath`` is a frozen dataclass, so its ``method`` cannot be
monkeypatched anyway, and patching the client is closer to what actually
happens on a fresh box.
"""

from __future__ import annotations

import pytest

from domovoi.clients.mpd import MPDNotProvisioned
from domovoi.models import Context, Intent, Response
from domovoi.router import _no_speakers_yet, route
from domovoi.tests.conftest import requires_db

# The utterance that produced the original 500: matches a music fast path,
# which calls get_mpd_client_for() to reach the room's daemon.
PLAY = "play some jazz in the kitchen"


def test_exception_is_typed_not_bare_runtime_error():
    """A narrow type keeps the router's catch from swallowing real MPD faults."""
    assert issubclass(MPDNotProvisioned, RuntimeError)
    assert MPDNotProvisioned is not RuntimeError


def test_degraded_response_is_spoken_and_explains_why():
    r = _no_speakers_yet(None, Context(online=True))
    assert isinstance(r, Response)
    assert r.text and "satellite" in r.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_music_request_degrades_when_no_room_is_provisioned(
    monkeypatch, db_session
):
    def no_rooms(_room_id=None):
        raise MPDNotProvisioned("no rooms provisioned yet")

    monkeypatch.setattr(
        "domovoi.handlers.music.get_mpd_client_for", no_rooms
    )

    resp = await route(
        Intent(transcript=PLAY, room_id="kitchen"),
        Context(room_id="kitchen", online=True),
        db_session,
    )

    # A spoken answer, not an exception — and one that says what to do.
    assert "satellite" in resp.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_unrelated_runtime_error_still_propagates(monkeypatch, db_session):
    """The catch is narrow on purpose: a genuine MPD fault must NOT be
    reported as 'no speakers yet', which would hide real breakage behind a
    reassuring message."""
    def exploded(_room_id=None):
        raise RuntimeError("mpd socket exploded")

    monkeypatch.setattr(
        "domovoi.handlers.music.get_mpd_client_for", exploded
    )

    with pytest.raises(RuntimeError, match="exploded"):
        await route(
            Intent(transcript=PLAY, room_id="kitchen"),
            Context(room_id="kitchen", online=True),
            db_session,
        )
