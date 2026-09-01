"""Audio requests before any satellite has connected must degrade, not 500.

Per-room MPD daemons are provisioned lazily on a satellite's first connect,
so a fresh install legitimately has none. Exercising the server over
/v1/intent before building a Pi is the documented first-run check
(docs/SETUP_RUNBOOK.md Step 4), and it used to raise a bare RuntimeError
straight through FastAPI as a 500.
"""

from __future__ import annotations

import pytest

from domovoi.clients.mpd import MPDNotProvisioned
from domovoi.models import Context, Intent, Response
from domovoi.router import _no_speakers_yet, route
from domovoi.tests.conftest import requires_db


def test_exception_is_typed_not_bare_runtime_error():
    """A narrow type keeps the router's catch from swallowing real MPD faults."""
    assert issubclass(MPDNotProvisioned, RuntimeError)
    assert MPDNotProvisioned is not RuntimeError


def test_degraded_response_is_spoken_and_explains_why():
    r = _no_speakers_yet(None, Context(online=True))
    assert isinstance(r, Response)
    assert r.text and "satellite" in r.text.lower()
    # It must not masquerade as a successful handler dispatch.
    assert r.matched_handler is None


@requires_db
@pytest.mark.asyncio
async def test_fast_path_mpd_failure_degrades(monkeypatch, db_session):
    """A fast-path handler raising MPDNotProvisioned yields a spoken answer."""
    import domovoi.router as router_mod

    async def boom(*a, **kw):
        raise MPDNotProvisioned("no rooms")

    # "turn it up" is a music fast path; make its method raise.
    for h in router_mod.HANDLERS:
        if h.name == "music":
            for entry in h.fast_paths:
                fp = router_mod.as_fast_path(entry)
                monkeypatch.setattr(fp, "method", boom, raising=False)

    resp = await route(
        Intent(transcript="turn it up", room_id="kitchen"),
        Context(room_id="kitchen", online=True),
        db_session,
    )
    assert "satellite" in resp.text.lower()


@requires_db
@pytest.mark.asyncio
async def test_unrelated_runtime_error_still_propagates(monkeypatch, db_session):
    """The catch is narrow: a genuine fault must not be reported as
    'no speakers yet', which would hide real MPD breakage."""
    import domovoi.router as router_mod

    async def boom(*a, **kw):
        raise RuntimeError("mpd socket exploded")

    for h in router_mod.HANDLERS:
        if h.name == "music":
            for entry in h.fast_paths:
                fp = router_mod.as_fast_path(entry)
                monkeypatch.setattr(fp, "method", boom, raising=False)

    with pytest.raises(RuntimeError, match="exploded"):
        await route(
            Intent(transcript="turn it up", room_id="kitchen"),
            Context(room_id="kitchen", online=True),
            db_session,
        )
