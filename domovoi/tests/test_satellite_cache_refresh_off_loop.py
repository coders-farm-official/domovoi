"""The SD card box's "Refresh caches" never runs on the web process's event
loop (mac-installer Train 1 finding 22: minutes of pip, docker and downloads
ran inside the request, and every other dashboard request waited: the home
page timed out at 10 s, /api/capabilities took 20 s). The fetches run in a
worker thread, one refresh at a time; the status card's Docker probe too."""

from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi import HTTPException

from domovoi import egress


@pytest.fixture
def sm(monkeypatch):
    from web.backend.api import satellite_media as sm

    async def unfrozen(*a, **kw):
        return None

    monkeypatch.setattr(sm.container_freeze, "require_unfrozen", unfrozen)
    return sm


@pytest.mark.asyncio
async def test_the_fetches_run_off_the_event_loop(sm, monkeypatch) -> None:
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}
    release = threading.Event()

    def fetch(name):
        def f(*a, **kw):
            seen[name] = threading.get_ident()
            if name == "wheels":
                release.wait(5)   # "minutes of pip": the loop must stay free meanwhile
            return True, name
        return f

    for name, attr in (("wheels", "fetch_wheels"), ("debs", "fetch_debs"),
                       ("oww_models", "fetch_oww_models"), ("xvf_host", "fetch_xvf_host")):
        monkeypatch.setattr(sm.fetchers, attr, fetch(name))
    with egress.override_policy("always"):
        task = asyncio.create_task(sm.media_cache_refresh())
        # The loop answers other work while the wheels fetch is "running".
        for _ in range(50):
            await asyncio.sleep(0.01)
            if "wheels" in seen:
                break
        assert "wheels" in seen
        assert not task.done()
        # A second press while the first runs: refused, not queued behind it.
        with pytest.raises(HTTPException) as ei:
            await sm.media_cache_refresh()
        assert ei.value.status_code == 409
        release.set()
        out = await task
    assert {k: v["ok"] for k, v in out.items()} == {"wheels": True, "debs": True, "oww_models": True, "xvf_host": True}
    assert all(t != loop_thread for t in seen.values())
    # Free again once it finished.
    assert not sm._CACHE_REFRESH_LOCK.locked()


@pytest.mark.asyncio
async def test_the_status_cards_docker_probe_is_off_the_loop(sm, monkeypatch) -> None:
    loop_thread = threading.get_ident()
    seen: list[int] = []

    def docker_available(*a, **kw):
        seen.append(threading.get_ident())
        return False

    monkeypatch.setattr(sm.fetchers, "docker_available", docker_available)
    out = await sm.media_status()
    assert out["docker_available"] is False
    assert seen and seen[0] != loop_thread
