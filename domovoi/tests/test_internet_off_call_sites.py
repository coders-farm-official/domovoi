"""Call sites outside ``net_safety`` honour ``INTERNET_ACCESS=never`` (B5).

Each site is checked two ways: under never it refuses BEFORE any
subprocess, download, job row or feed write (the would-be side effect is
monkeypatched to fail the test), and with the question unanswered it does
what it always did.

* git fetch / pull / commits_behind — the error shape, no git at all;
* the plugin installer — GitHub → ``internet_off`` (the 422 envelope),
  pip ``--no-index``, pip's "no matching distribution" → ``internet_off``;
* the satellite media fetchers — ``(False, reason)``, no subprocess; the
  web cache refresh → 409;
* the web model pull → 409, no ``model_jobs`` row, Ollama never asked;
* ``mpd_provisioner.ensure_image`` — no docker build;
* web news add-feed / validate / poll → 409, ``valid`` untouched.

DB-free except the news-feed test (``requires_db``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException

from domovoi import egress, git_version
from domovoi.tests.conftest import requires_db

REASON = egress.TURNED_OFF_REASON


def _boom(*a, **k):
    raise AssertionError(f"must not run under never: {a!r} {k!r}")


def _assert_409(exc: HTTPException) -> None:
    assert exc.status_code == 409
    assert exc.detail == REASON
    assert (exc.headers or {}).get("X-Domovoi-Refusal") == "internet-off"


# ─── git ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_git_fetch_and_pull_spawn_nothing_under_never(monkeypatch) -> None:
    monkeypatch.setattr(git_version, "_run", _boom)
    monkeypatch.setattr(git_version, "_rollback_baseline", _boom)
    with egress.override_policy("never"):
        fetched = await git_version.fetch()
        behind = await git_version.commits_behind()
        pulled = await git_version.pull()
    assert fetched == {"ok": False, "error": REASON}
    assert behind == {"behind": 0, "ahead": 0, "upstream": False,
                      "upstream_sha": None, "error": REASON}
    assert pulled["pulled"] is False and pulled["error"] == REASON
    assert pulled["new_sha"] is None


@pytest.mark.asyncio
async def test_git_fetch_still_runs_when_unanswered(monkeypatch) -> None:
    calls: list[tuple] = []

    def fake_run(*args):
        calls.append(args)
        return subprocess.CompletedProcess(["git", *args], 0, "", "")

    monkeypatch.setattr(git_version, "_run", fake_run)
    assert (await git_version.fetch()) == {"ok": True, "error": None}
    assert calls == [("fetch",)]


# ─── plugin installer ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_github_install_is_internet_off_before_any_request(monkeypatch) -> None:
    from domovoi.plugins_runtime import installer

    monkeypatch.setattr(installer.egress, "async_client", _boom)
    with egress.override_policy("never"):
        with pytest.raises(installer.InstallError) as ei:
            await installer.download_github_zip(
                "https://github.com/coders-farm-official/domovoi-plugin-sleep"
            )
    assert ei.value.code == "internet_off"
    assert str(ei.value) == REASON
    # The admin route turns it into the installer's usual 422 envelope.
    http = installer._install_error_response(ei.value)
    assert http.status_code == 422
    assert http.detail["error"]["code"] == "internet_off"
    assert http.detail["error"]["message"] == REASON


@pytest.mark.asyncio
async def test_a_bad_github_url_is_still_its_own_error_under_never() -> None:
    from domovoi.plugins_runtime import installer

    with egress.override_policy("never"):
        with pytest.raises(installer.InstallError) as ei:
            await installer.download_github_zip("https://example.com/not/github")
    assert ei.value.code == "github_url_invalid"


def test_pip_uses_no_index_under_never(monkeypatch, tmp_path) -> None:
    from domovoi.plugins_runtime import installer

    monkeypatch.setattr(installer, "validate_lockfile", lambda p: [])
    lock = tmp_path / "requirements.lock"
    with egress.override_policy("never"):
        offline = installer._pip_base_args(lock)
    online = installer._pip_base_args(lock)
    assert "--no-index" in offline and "--index-url" not in offline
    assert "--require-hashes" in offline and "--only-binary=:all:" in offline
    assert "--index-url" in online and "--no-index" not in online


_NOT_FOUND = (
    "ERROR: Could not find a version that satisfies the requirement "
    "shazamio==0.7.0 (from versions: none)\n"
    "ERROR: No matching distribution found for shazamio==0.7.0\n"
)


def test_missing_packages_read_as_needs_internet(monkeypatch, tmp_path) -> None:
    from domovoi.plugins_runtime import installer

    monkeypatch.setattr(installer, "validate_lockfile", lambda p: [])
    monkeypatch.setattr(
        installer.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", _NOT_FOUND),
    )
    lock = tmp_path / "requirements.lock"
    with egress.override_policy("never"):
        with pytest.raises(installer.InstallError) as dry:
            installer.pip_dry_run(lock)
        with pytest.raises(installer.InstallError) as real:
            installer.pip_install(lock)
    for e in (dry.value, real.value):
        assert e.code == "internet_off"
        assert "needs the internet to download its Python packages" in str(e)
        assert "shazamio==0.7.0" in str(e)
        assert e.details["missing"] == ["shazamio==0.7.0"]
    # Unanswered: pip's own failure, as before.
    with pytest.raises(installer.InstallError) as before:
        installer.pip_dry_run(lock)
    assert before.value.code == "requirements_unresolvable"


# ─── satellite media ──────────────────────────────────────────────────────


def test_satellite_fetchers_refuse_without_a_subprocess(monkeypatch, tmp_path) -> None:
    from domovoi.satellite_media import cache, fetchers

    monkeypatch.setattr(cache, "bucket", _boom)
    monkeypatch.setattr(fetchers.shutil, "which", _boom)
    want = (False, f"{REASON}; using what is already cached")
    with egress.override_policy("never"):
        assert fetchers.fetch_wheels(tmp_path, "3.11", ("manylinux_2_28_aarch64",), run=_boom) == want
        assert fetchers.fetch_debs("bookworm", run=_boom) == want
        assert fetchers.fetch_xvf_host(run=_boom) == want
        assert fetchers.fetch_oww_models() == want


@pytest.mark.asyncio
async def test_web_cache_refresh_is_409_and_prepare_stays(monkeypatch) -> None:
    from web.backend.api import satellite_media as sm

    for name in ("fetch_wheels", "fetch_debs", "fetch_oww_models", "fetch_xvf_host"):
        monkeypatch.setattr(sm.fetchers, name, _boom)
    with egress.override_policy("never"):
        with pytest.raises(HTTPException) as ei:
            await sm.media_cache_refresh()
    _assert_409(ei.value)


# ─── Ollama model pull (web) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_model_pull_is_409_before_any_job_row(monkeypatch) -> None:
    from web.backend.api import models

    monkeypatch.setattr(models, "session_scope", _boom)
    monkeypatch.setattr(models, "_check_registry_host", _boom)
    with egress.override_policy("never"):
        with pytest.raises(HTTPException) as ei:
            await models.start_pull(models.PullBody(model="tinyllama"))
    _assert_409(ei.value)


def test_a_registry_inside_the_house_is_the_one_pull_allowed() -> None:
    from web.backend.api import models

    assert models._local_registry("192.168.1.20:5000/library/llama3") is True
    assert models._local_registry("registry.lan/models/qwen:7b") is True
    assert models._local_registry("tinyllama") is False
    assert models._local_registry("registry.ollama.ai/library/llama3.2") is False
    assert models._local_registry("ghcr.io/org/model:1") is False


# ─── the MPD image ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_mpd_image_is_not_built_under_never(monkeypatch) -> None:
    from domovoi import mpd_provisioner as mp

    async def missing(tag):
        return False

    monkeypatch.setattr(mp, "_image_exists", missing)
    monkeypatch.setattr(mp, "_build_image", _boom)
    with egress.override_policy("never"):
        with pytest.raises(RuntimeError) as ei:
            await mp.ensure_image()
    assert "build it while online" in str(ei.value)
    assert "internet access is turned off" in str(ei.value)


@pytest.mark.asyncio
async def test_mpd_image_is_built_when_unanswered_and_left_when_present(monkeypatch) -> None:
    from domovoi import mpd_provisioner as mp

    built: list[str] = []
    present = {"v": False}

    async def exists(tag):
        return present["v"]

    async def build(tag):
        built.append(tag)

    monkeypatch.setattr(mp, "_image_exists", exists)
    monkeypatch.setattr(mp, "_build_image", build)
    await mp.ensure_image()
    assert built == [mp.settings.mpd_image_tag]
    present["v"] = True
    with egress.override_policy("never"):
        await mp.ensure_image()          # present: nothing to build, no refusal
    assert len(built) == 1


# ─── news feeds (web) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_news_poll_is_409(monkeypatch) -> None:
    from web.backend.api import news

    monkeypatch.setattr(news, "session_scope", _boom)
    with egress.override_policy("never"):
        with pytest.raises(HTTPException) as ei:
            await news.poll_now(person_id=None)
    _assert_409(ei.value)


@requires_db
@pytest.mark.asyncio
async def test_news_add_and_validate_are_409_and_leave_the_feed_alone(db_session, monkeypatch) -> None:
    """A refusal is never recorded as "this feed is dead" (5.12)."""
    from sqlalchemy import text

    from domovoi import news_service
    from web.backend.api import news

    url = "https://feeds.example.org/rss.xml"
    fid = await news_service._upsert_feed(db_session, url, discovered_via="manual")
    await db_session.execute(
        text("UPDATE news_feeds SET valid = TRUE, last_error = NULL WHERE id = :id"),
        {"id": fid},
    )
    await db_session.commit()
    monkeypatch.setattr(news.news_feeds_client, "feed_is_valid", _boom)

    with egress.override_policy("never"):
        with pytest.raises(HTTPException) as validate:
            await news.validate_feed(fid)
        with pytest.raises(HTTPException) as add:
            await news.add_topic_feed(1, news.NewsFeedCreate(url=url))
    _assert_409(validate.value)
    _assert_409(add.value)

    row = (await db_session.execute(
        text("SELECT valid, last_error, last_polled_at FROM news_feeds WHERE id = :id"),
        {"id": fid},
    )).one()
    assert row[0] is True and row[1] is None and row[2] is None


def test_every_site_imports_egress_at_module_level() -> None:
    """The web import guard refuses a lazy ``domovoi.*`` import inside a
    request handler, so the web modules import egress at the top."""
    root = Path(__file__).resolve().parents[2]
    for rel in (
        "web/backend/api/news.py",
        "web/backend/api/models.py",
        "web/backend/api/satellite_media.py",
    ):
        src = (root / rel).read_text(encoding="utf-8")
        assert "\nfrom domovoi import egress" in src, rel
