"""A device-tier upload indexes the tracks it just wrote (F-A017).

The regression: ``POST /api/music/library/upload`` stayed on the DEVICE
tier, but the post-write reindex it leaned on — the core's
``/v1/admin/library/reindex`` — became ADMIN tier in CORE-4 (fe39c02).
The web hop forwards the CALLER's credential, so a phone holding only the
household device token got a 401 from that hop: the file landed on disk,
``library_tracks`` never heard about it, and the upload still answered
200. "Uploaded" but invisible.

The fix does not touch the gate. The library-wide sweep is still an admin
job; what the upload runs instead is a BOUNDED index of the handful of
paths this one request wrote (``library_indexer.index_paths``), in
process, plus a device-tier hop for the MPD half that only the core can
fan out.

DB-FREE on purpose — the whole file runs without Postgres, so it can
never quietly skip:

* the upload handler is called directly (no app lifespan), with the
  indexer's ``session_scope`` swapped for a recorder, so the INSERT the
  caller earns is asserted by its parameters;
* the tier half drives the real core app with the auth primitives faked
  (``install_fake_db``), because a gate answers long before Postgres.
"""

from __future__ import annotations

import io
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from starlette.datastructures import UploadFile
from starlette.requests import Request

from domovoi import admin_auth
from domovoi.db.session import engine
from domovoi.main import app as core_app
from domovoi.tests.auth_testkit import (
    HEADER,
    _db,  # noqa: F401 — fixture
    bearer,
    claim_admin,
    db_device_token,
    install_fake_db,
    web_client,
)
from domovoi.tests.conftest import requires_db
import domovoi.workers.library_indexer as indexer
import web.backend.api.music as music_api

ADMIN_TOKEN = "admin-token"
DEVICE_TOKEN = "device-token"

# The core route the upload MUST NOT depend on any more: a library-wide
# sweep, admin-gated since CORE-4.
ADMIN_SWEEP = "/v1/admin/library/reindex"


# ─── plumbing ─────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _RecordingSession:
    """Just enough AsyncSession for the indexer: records the INSERTs and
    the NOTIFY, and reports one row affected per insert."""

    def __init__(self) -> None:
        self.inserts: list[dict[str, Any]] = []
        self.notifies: list[dict[str, Any]] = []
        self.commits = 0

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(stmt)
        if "INSERT INTO library_tracks" in sql:
            self.inserts.append(params or {})
            return _Result(1)
        if "pg_notify" in sql:
            self.notifies.append(params or {})
            return _Result(0)
        return _Result(0)

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture
def library_db(monkeypatch) -> _RecordingSession:
    """Swap the indexer's session for a recorder. Nothing here reaches
    Postgres, so this file never skips."""
    session = _RecordingSession()

    @asynccontextmanager
    async def fake_scope():
        yield session

    monkeypatch.setattr(indexer, "session_scope", fake_scope)
    return session


@pytest.fixture
def music_dir(tmp_path, monkeypatch):
    from domovoi.config import settings

    monkeypatch.setattr(settings, "music_dir", str(tmp_path), raising=False)
    return tmp_path


@pytest.fixture
def core_hops(monkeypatch) -> list[str]:
    """Spy on the web→core hops, answering the way the real core would to
    a caller holding only the household token: the admin sweep refuses,
    a device-tier route does not."""
    calls: list[str] = []

    async def _post(path, body=None, headers=None):
        calls.append(path)
        if path.startswith("/v1/admin/library/"):
            return 401, {"detail": "admin session required"}
        return 200, {"rooms": 0, "updated": 0}

    monkeypatch.setattr(music_api, "post_admin", _post)
    return calls


def _device_request() -> Request:
    """A minimal ASGI-scope Request carrying the household token — what a
    paired phone or the dashboard sends, and nothing more."""
    return Request(
        {
            "type": "http",
            "headers": [(HEADER.lower().encode(), DEVICE_TOKEN.encode())],
            "client": ("192.168.1.50", 12345),
        }
    )


def _upload(name: str, payload: bytes) -> UploadFile:
    return UploadFile(io.BytesIO(payload), filename=name, size=len(payload))


def _core_client() -> AsyncClient:
    # raise_app_exceptions=False: passing the gate lands in a handler that
    # wants MPD, and that outcome is the proof, not an error to chase.
    return AsyncClient(
        transport=ASGITransport(app=core_app, raise_app_exceptions=False),
        base_url="http://test",
    )


# ─── the regression ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_device_tier_upload_indexes_the_file_it_wrote(
    music_dir, library_db, core_hops
) -> None:
    """F-A017: the track the caller uploaded is in library_tracks when the
    response goes out — with no admin credential anywhere in the path.

    Before the fix this asserted nothing was inserted at all: the handler
    posted to the admin sweep, got 401, and returned
    ``reindex_triggered=False`` over a library that never changed.
    """
    result = await music_api.upload_to_library(
        _device_request(), files=[_upload("Nina Simone - Sinnerman.mp3", b"ID3fake")]
    )

    assert result.saved == 1
    written = music_dir / "uploads" / "Nina Simone - Sinnerman.mp3"
    assert written.read_bytes() == b"ID3fake"

    # The row the caller earned.
    assert [row["fp"] for row in library_db.inserts] == [str(written)]
    assert library_db.commits == 1
    # …and the dashboard is told, so the Library view refetches.
    assert library_db.notifies == [{"payload": "inserted=1"}]

    # The flag the phone's toast reads is now honest.
    assert result.reindex_triggered is True


@pytest.mark.asyncio
async def test_upload_does_not_call_the_admin_gated_sweep(
    music_dir, library_db, core_hops
) -> None:
    """The fix is not "retry the sweep with a better token": the upload
    must not touch the admin route at all, so no hop can 401 it again."""
    await music_api.upload_to_library(_device_request(), files=[_upload("a.mp3", b"x")])

    assert ADMIN_SWEEP not in core_hops
    assert core_hops == ["/v1/admin/music/mpd-rescan"]


@pytest.mark.asyncio
async def test_every_file_of_a_zip_upload_is_indexed(
    music_dir, library_db, core_hops
) -> None:
    """The bounded index covers the whole batch, not just the first — and
    still only the batch."""
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("one.mp3", b"aaa")
        zf.writestr("two.flac", b"bbb")
        zf.writestr("cover.jpg", b"img")
    (music_dir / "not-part-of-this-upload.mp3").write_bytes(b"old")

    result = await music_api.upload_to_library(
        _device_request(), files=[_upload("album.zip", buf.getvalue())]
    )

    assert result.saved == 2
    uploads = music_dir / "uploads"
    assert [row["fp"] for row in library_db.inserts] == [
        str(uploads / "one.mp3"),
        str(uploads / "two.flac"),
    ]


@pytest.mark.asyncio
async def test_indexing_failure_still_saves_the_files_and_says_so(
    music_dir, core_hops, monkeypatch
) -> None:
    """A dead database costs the index, never the upload — and the answer
    admits it instead of claiming the track is there."""

    @asynccontextmanager
    async def boom():
        raise RuntimeError("no database")
        yield  # pragma: no cover

    monkeypatch.setattr(indexer, "session_scope", boom)

    result = await music_api.upload_to_library(
        _device_request(), files=[_upload("b.mp3", b"x")]
    )

    assert result.saved == 1
    assert (music_dir / "uploads" / "b.mp3").exists()
    assert result.reindex_triggered is False


# ─── the indexer's own containment ────────────────────────────────────────


@pytest.mark.asyncio
async def test_index_paths_refuses_anything_outside_music_dir(
    music_dir, library_db, tmp_path
) -> None:
    """``index_paths`` is reached from an upload handler, so its argument
    is caller-derived: a path that resolves outside MUSIC_DIR, a
    non-audio file and a name that is not there at all all index nothing.
    """
    outside = tmp_path.parent / "outside.mp3"
    outside.write_bytes(b"x")
    not_audio = music_dir / "notes.txt"
    not_audio.write_bytes(b"x")

    counts = await indexer.index_paths(
        [outside, not_audio, music_dir / "ghost.mp3", music_dir / ".." / "outside.mp3"]
    )

    assert library_db.inserts == []
    assert counts == {"scanned": 0, "inserted": 0, "skipped": 0, "errors": 4}


@pytest.mark.asyncio
async def test_index_paths_is_idempotent_for_a_file_already_known(
    music_dir, monkeypatch
) -> None:
    """A second upload of the same path is a skip, not a duplicate row —
    the same ``WHERE NOT EXISTS`` contract the full sweep has."""
    track = music_dir / "known.mp3"
    track.write_bytes(b"x")

    class _Known(_RecordingSession):
        async def execute(self, stmt, params=None):
            await super().execute(stmt, params)
            return _Result(0)  # the row was already there

    session = _Known()

    @asynccontextmanager
    async def fake_scope():
        yield session

    monkeypatch.setattr(indexer, "session_scope", fake_scope)

    counts = await indexer.index_paths([track])

    assert counts["inserted"] == 0 and counts["skipped"] == 1
    assert session.notifies == [], "nothing changed, so nothing to notify"


# ─── the gate the fix must NOT have weakened ──────────────────────────────


@pytest.mark.asyncio
async def test_the_library_sweep_is_still_admin_only(monkeypatch) -> None:
    """CORE-4 stands: the household token does not buy a library-wide
    sweep. If this ever goes green for a device token, the fix above was
    done the wrong way."""
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    async with _core_client() as c:
        device = await c.post(ADMIN_SWEEP, headers={HEADER: DEVICE_TOKEN})
        nothing = await c.post(ADMIN_SWEEP)
    assert device.status_code == 401, device.text
    assert nothing.status_code == 401, nothing.text


@pytest.mark.asyncio
async def test_the_mpd_rescan_hop_is_on_the_device_tier(monkeypatch) -> None:
    """The MPD half the upload still hops for: the household token gets
    in (that is the point), a caller with nothing does not."""
    install_fake_db(
        monkeypatch, admin=True, sessions={ADMIN_TOKEN}, device_token=DEVICE_TOKEN
    )
    async with _core_client() as c:
        device = await c.post(
            "/v1/admin/music/mpd-rescan", headers={HEADER: DEVICE_TOKEN}
        )
        admin = await c.post("/v1/admin/music/mpd-rescan", headers=bearer(ADMIN_TOKEN))
        nothing = await c.post("/v1/admin/music/mpd-rescan")
    assert device.status_code not in (401, 403), device.text
    assert admin.status_code not in (401, 403), admin.text
    assert nothing.status_code == 401, nothing.text


def test_the_upload_route_itself_is_still_device_tier() -> None:
    """Both halves of the mismatch, named in one place: the route the
    phone calls is device tier, and it no longer depends on a route that
    is not."""
    from domovoi.tests.route_walk import iter_route_contexts
    from web.backend.main import app as web_app

    # FastAPI 0.139 keeps included routers as ``_IncludedRouter`` entries on
    # ``app.routes`` instead of flattening them, so walking for ``APIRoute``
    # finds nothing at all. ``iter_route_contexts`` resolves the effective
    # path and dependencies; older releases flatten on include and are read
    # directly. Same shape as test_route_auth_matrix's walker.
    contexts = list(iter_route_contexts(web_app.routes))

    gates: list[Any] = []
    for route in contexts:
        if getattr(route, "path", None) == "/api/music/library/upload":
            dependant = getattr(route, "dependant", None)
            if dependant is not None:
                gates = [d.call for d in dependant.dependencies if d.call]
    assert gates, "the upload route is not mounted"
    assert admin_auth.require_device in gates
    assert admin_auth.require_admin_mutation not in gates


# ─── the same ground, against real rows ───────────────────────────────────


@requires_db
@pytest.mark.asyncio
async def test_a_phone_uploads_and_the_track_is_in_the_library(
    _db, music_dir, monkeypatch
) -> None:
    """The card, end to end: a caller holding ONLY the household token
    uploads a file and then sees it in ``GET /api/music/library``.

    Setup is claimed first, so the pre-setup LAN grace is off and the
    device gate is the real one; ``_db`` points DOMOVOI_URL at a dead
    port, so the core hop fails and the index stands on its own. No
    Authorization header is sent anywhere in this test.

    The ``sys.meta_path`` import guard is installed the way
    ``web.backend.main``'s lifespan installs it, because without it this
    test is a lie: the first cut of the fix passed every test in this
    file and still indexed nothing in a real ft run, since the guard
    refuses a ``domovoi.*`` import the web process has not preloaded
    (§5.1). Pytest does not install it; the web process does.
    """
    import sys

    from web.backend.plugin_host import install_import_guard, remove_import_guard

    name = "ft-a017-proof.mp3"
    written = music_dir / "uploads" / name
    # Evict the indexer first: this module imported it at the top, and a
    # module already in sys.modules is never offered to the guard — which
    # would make the guard a no-op here and hide exactly the failure this
    # test exists for. The web process starts without it, so this test
    # does too; ``install_import_guard`` then preloads its allowlist,
    # which is what has to put it back.
    #
    # Both the sys.modules entry AND the parent package's attribute are
    # put back afterwards: the preload binds a SECOND module object, and
    # leaving either name pointing at it makes a later test's
    # ``monkeypatch.setattr`` land on the copy the handler doesn't use.
    modname = "domovoi.workers.library_indexer"
    original = sys.modules.get(modname)
    sys.modules.pop(modname, None)
    install_import_guard()
    try:
        async with web_client() as c:
            await claim_admin(c)  # rotates the device token
            token = await db_device_token()
            assert token

            up = await c.post(
                "/api/music/library/upload",
                files=[("files", (name, b"ID3fakeaudio", "audio/mpeg"))],
                headers={HEADER: token},
            )
            assert up.status_code == 200, up.text
            assert up.json()["saved"] == 1
            assert up.json()["reindex_triggered"] is True

            listing = await c.get(
                "/api/music/library", params={"q": name}, headers={HEADER: token}
            )
        assert listing.status_code == 200, listing.text
        paths = [t["file_path"] for t in listing.json()["items"]]
        assert paths == [str(written)], listing.text
    finally:
        remove_import_guard()
        if original is not None:
            sys.modules[modname] = original
            setattr(sys.modules["domovoi.workers"], "library_indexer", original)
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM library_tracks WHERE file_path = :fp"),
                {"fp": str(written)},
            )
