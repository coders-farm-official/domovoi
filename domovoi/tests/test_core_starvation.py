"""Incident 2026-10-14, mechanism 2: a full default thread pool must not
stop the database (functional-testing incident-1014, ``starve.py``).

Every session opens a new connection (NullPool), and a ``localhost`` host
makes each connect resolve the name in the event loop's default pool,
the pool every ``asyncio.to_thread`` shares. With that pool full, the
connect waited forever; dialed at 127.0.0.1 it needs no lookup. The heavy
blocking work (speech, synthesis, plugin installs) also runs in pools of
its own now (domovoi/executors.py), and one Piper voice's first download
no longer holds up every other voice."""

from __future__ import annotations

import asyncio
import contextvars
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from domovoi import executors
from domovoi.db import session as db_session_mod
from domovoi.db.session import connect_url
from domovoi.tests.conftest import requires_db


# ─── connect_url ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "want"),
    [
        ("postgresql+asyncpg://domovoi:domovoi@localhost:6432/domovoi",
         "postgresql+asyncpg://domovoi:domovoi@127.0.0.1:6432/domovoi"),
        ("postgresql+asyncpg://domovoi:domovoi@LocalHost:6432/domovoi",
         "postgresql+asyncpg://domovoi:domovoi@127.0.0.1:6432/domovoi"),
        # A password with characters that need quoting survives the rewrite.
        ("postgresql+asyncpg://domovoi:p%40ss%2Fw@localhost:6432/domovoi?ssl=disable",
         "postgresql+asyncpg://domovoi:p%40ss%2Fw@127.0.0.1:6432/domovoi?ssl=disable"),
        # Any other name keeps resolving: it may move.
        ("postgresql+asyncpg://domovoi:domovoi@db.lan:6432/domovoi",
         "postgresql+asyncpg://domovoi:domovoi@db.lan:6432/domovoi"),
        ("postgresql+asyncpg://domovoi:domovoi@127.0.0.1:6432/domovoi",
         "postgresql+asyncpg://domovoi:domovoi@127.0.0.1:6432/domovoi"),
        ("postgresql+asyncpg://domovoi:domovoi@[::1]:6432/domovoi",
         "postgresql+asyncpg://domovoi:domovoi@[::1]:6432/domovoi"),
        # No host at all (a Unix socket): untouched.
        ("postgresql+asyncpg://domovoi@/domovoi", "postgresql+asyncpg://domovoi@/domovoi"),
        ("not a url", "not a url"),
    ],
)
def test_connect_url_dials_localhost_by_address(url: str, want: str) -> None:
    assert connect_url(url) == want


def test_the_core_engine_is_built_from_connect_url() -> None:
    assert str(db_session_mod.engine.url.host) == str(
        db_session_mod.make_url(connect_url(db_session_mod.settings.database_url)).host
    )


# ─── a full default pool and the database ────────────────────────────────


def _default_pool_size() -> int:
    return min(32, (os.cpu_count() or 1) + 4)


def _as_localhost(url: str) -> str:
    from sqlalchemy.engine import make_url

    return make_url(url).set(host="localhost").render_as_string(hide_password=False)


async def _select_one(url: str, timeout: float) -> float:
    eng = create_async_engine(url, poolclass=NullPool)
    try:
        t0 = time.perf_counter()
        async with eng.connect() as conn:
            await asyncio.wait_for(conn.execute(text("SELECT 1")), timeout)
        return time.perf_counter() - t0
    finally:
        await eng.dispose()


@requires_db
async def test_a_full_default_pool_does_not_block_a_database_connect() -> None:
    """starve.py as a test: occupy every thread of the default pool, then
    open a session the way the core does. Dialed by name the connect
    waits behind the blockers (the control, so the test is known to
    starve the pool); through connect_url it is answered at once."""
    url = _as_localhost(db_session_mod.settings.database_url)
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=_default_pool_size()))
    release = threading.Event()
    blockers = [loop.run_in_executor(None, release.wait, 30) for _ in range(_default_pool_size())]
    try:
        await asyncio.sleep(0.2)
        took = await asyncio.wait_for(_select_one(connect_url(url), 5.0), 5.0)
        assert took < 3.0
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_select_one(url, 2.0), 2.0)
    finally:
        release.set()
        await asyncio.gather(*blockers)


@requires_db
async def test_core_sessions_answer_with_the_default_pool_full() -> None:
    """The real session path (session_scope) with every default-pool
    thread taken: SELECT 1 still answers. The test DB URL is pinned to
    127.0.0.1, so the core's own engine is rebuilt here from the
    ``localhost`` spelling a stock install has (DATABASE_URL's default)."""
    url = connect_url(_as_localhost(db_session_mod.settings.database_url))
    eng = create_async_engine(url, poolclass=NullPool)
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=_default_pool_size()))
    release = threading.Event()
    blockers = [loop.run_in_executor(None, release.wait, 30) for _ in range(_default_pool_size())]
    async def select_one() -> int:
        async with eng.connect() as conn:
            return (await conn.execute(text("SELECT 1"))).scalar_one()

    try:
        await asyncio.sleep(0.2)
        # The connect included: that is the part that used to wait.
        assert await asyncio.wait_for(select_one(), 5.0) == 1
    finally:
        release.set()
        await asyncio.gather(*blockers)
        await eng.dispose()


# ─── the bounded pools ───────────────────────────────────────────────────


async def test_heavy_work_has_pools_of_its_own() -> None:
    """A full default pool doesn't hold up speech work, and a full speech
    pool doesn't hold up anything in the default pool."""
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=2))
    release = threading.Event()
    blockers = [loop.run_in_executor(None, release.wait, 30) for _ in range(2)]
    try:
        assert await asyncio.wait_for(executors.run("tts", lambda: "spoken"), 3.0) == "spoken"
    finally:
        release.set()
        await asyncio.gather(*blockers)

    hold = threading.Event()
    jammed = [asyncio.ensure_future(executors.run("speech", hold.wait, 30))
              for _ in range(executors.POOL_SIZES["speech"])]
    try:
        await asyncio.sleep(0.1)
        assert executors.busy()["speech"]["threads"] == executors.POOL_SIZES["speech"]
        assert await asyncio.wait_for(asyncio.to_thread(lambda: "default"), 3.0) == "default"
    finally:
        hold.set()
        await asyncio.gather(*jammed)


async def test_executors_run_is_to_thread_with_a_pool() -> None:
    var: contextvars.ContextVar[str] = contextvars.ContextVar("var", default="unset")
    var.set("copied")
    assert await executors.run("plugins", var.get) == "copied"
    assert await executors.run("speech", lambda a, b=0: a + b, 2, b=3) == 5

    def boom() -> None:
        raise ValueError("raised in the pool")

    with pytest.raises(ValueError, match="raised in the pool"):
        await executors.run("tts", boom)
    with pytest.raises(KeyError):
        await executors.run("nonesuch", lambda: None)


def test_the_core_uses_the_pools() -> None:
    """The heavy users named in the incident run in their own pools."""
    import inspect

    from domovoi import speech_warmup
    from domovoi.clients import tts, voice_embedder, whisper
    from domovoi.plugins_runtime import installer

    for mod, pool_name in ((whisper, "speech"), (voice_embedder, "speech"),
                           (tts, "tts"), (speech_warmup, "tts"), (installer, "plugins")):
        src = inspect.getsource(mod)
        assert "asyncio.to_thread(" not in src, mod.__name__
        assert f'executors.run("{pool_name}"' in src or f'"{pool_name}", ' in src, mod.__name__


# ─── one voice's download does not hold up another voice ─────────────────


def test_a_slow_voice_download_does_not_block_another_voice(monkeypatch) -> None:
    from domovoi.clients import tts as tts_mod

    downloading = threading.Event()
    finish = threading.Event()

    def path_for(voice: str):
        if voice == "slow":
            downloading.set()
            assert finish.wait(30)
        return f"/voices/{voice}.onnx"

    class FakeVoice:
        def __init__(self, path: str) -> None:
            self.path = path

    monkeypatch.setattr(tts_mod, "_piper_voice_cache", {})
    monkeypatch.setattr(tts_mod, "_piper_voice_locks", {})
    monkeypatch.setattr(tts_mod, "_piper_voice_path", path_for)
    monkeypatch.setattr(tts_mod, "PiperVoice", FakeVoice)

    slow = threading.Thread(target=tts_mod._piper_voice, args=("slow",), daemon=True)
    slow.start()
    assert downloading.wait(5)
    got: list[object] = []
    fast = threading.Thread(target=lambda: got.append(tts_mod._piper_voice("fast")), daemon=True)
    fast.start()
    fast.join(3)
    try:
        assert got and got[0].path == "/voices/fast.onnx", "another voice waited on a download"
    finally:
        finish.set()
        slow.join(5)
    # The slow one still loads once, and a second ask is the cached voice.
    assert tts_mod._piper_voice("slow") is tts_mod._piper_voice_cache["slow"]


async def test_replies_in_a_loaded_voice_never_wait_behind_a_download(monkeypatch, tmp_path) -> None:
    """Review 2026-10-15 (the reviewer's scratch test, through the real
    synthesis chain): four replies in a voice still downloading must not
    take the speech pool's four threads. The chain stops for a voice that
    isn't on disk, the voice is fetched once in the downloads pool, and a
    reply in a loaded voice goes straight through (it waited 3.70 s)."""
    from domovoi.clients import tts as tts_mod

    downloaded: list[str] = []
    on_disk = {"cached-voice"}

    def local_path(voice: str):
        return tmp_path / f"{voice}.onnx" if voice in on_disk else None

    def slow_fetch(voice: str):
        if voice in on_disk:                 # as the real one: no fetch once there
            return tmp_path / f"{voice}.onnx"
        time.sleep(3.0)                      # a first download on a slow line
        downloaded.append(voice)
        on_disk.add(voice)
        return tmp_path / f"{voice}.onnx"

    class FakeVoice:
        def __init__(self, path) -> None:
            self.path = path

    def fake_synth(text: str, voice: str, speed: float):
        tts_mod._piper_voice(voice)          # the real lookup, with its deferral
        return tts_mod._pcm_to_wav_bytes(b"\x01\x02" * 400, 16_000)

    monkeypatch.setattr(tts_mod, "_piper_voice_cache", {"cached-voice": FakeVoice("cached")})
    monkeypatch.setattr(tts_mod, "_piper_voice_locks", {})
    monkeypatch.setattr(tts_mod, "_piper_local_path", local_path)
    monkeypatch.setattr(tts_mod, "_piper_voice_path", slow_fetch)
    monkeypatch.setattr(tts_mod, "PiperVoice", FakeVoice)
    monkeypatch.setattr(tts_mod, "_synth_piper_sync", fake_synth)
    client = tts_mod.RealTTSClient(preferred_engine="piper", piper_voice="cached-voice", speed=1.0)

    waiting = [asyncio.create_task(client.synthesize_detailed("hello", voice="slow-voice"))
               for _ in range(4)]
    await asyncio.sleep(0.3)
    t0 = time.monotonic()
    quick = await client.synthesize_detailed("hello", voice="cached-voice")
    took = time.monotonic() - t0
    slow = await asyncio.gather(*waiting)
    assert quick.engine == "piper" and quick.voice == "cached-voice"
    assert took < 0.5, f"a reply in a loaded voice waited {took:.2f} s behind a download"
    assert [r.engine for r in slow] == ["piper"] * 4 and all(r.voice == "slow-voice" for r in slow)
    assert downloaded == ["slow-voice"]      # fetched once for all four
    assert executors.POOL_SIZES["downloads"] >= 1


async def test_a_voice_that_cannot_be_fetched_falls_back_as_before(monkeypatch, tmp_path) -> None:
    from domovoi.clients import tts as tts_mod

    def refused(voice: str):
        raise tts_mod.PiperDownloadError("refused")

    monkeypatch.setattr(tts_mod, "_piper_voice_cache", {})
    monkeypatch.setattr(tts_mod, "_piper_local_path", lambda voice: None)
    monkeypatch.setattr(tts_mod, "_piper_voice_path", refused)
    monkeypatch.setattr(tts_mod, "_synth_piper_sync",
                        lambda text, voice, speed: tts_mod._piper_voice(voice) and b"never")
    monkeypatch.setattr(tts_mod, "_synth_system_sync",
                        lambda text: tts_mod._pcm_to_wav_bytes(b"\x01\x02" * 400, 16_000))
    client = tts_mod.RealTTSClient(preferred_engine="piper", piper_voice="nope-voice", speed=1.0)
    res = await client.synthesize_detailed("hello")
    assert res.engine == "system" and res.missing_voice is None
    assert any("not on disk" in f for f in res.failures)
