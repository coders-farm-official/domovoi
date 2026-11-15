"""First-run setup puts the Android app on the server (owner decision
2026-11-15): "Yes during setup. If it can't, it should warn the user
they'll need to have internet later at some point to download it one time."

Setup is a core start while no admin has claimed the server
(domovoi/main.py hands ``setup=True`` to android_app.schedule_auto_refresh).
Then the first download happens by itself, in the background, through the
same fetch and check_apk as an admin's press, under any internet answer but
"never". When it can't (no internet, "never", a failed or partial download,
a file check_apk refuses), the app is simply not here, and the dashboard's
reminder stays until it is (test_web_spare_device_enrol.py,
test_web_settings_health.py). After setup the old rules hold
(test_android_app_after_update.py).

No DB, no network: the signed fixtures and the fake GitHub of
test_android_app.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import re

import pytest

from domovoi import android_app as aa
from domovoi import egress
from domovoi.tests.test_android_app import (  # noqa: F401 — fixtures
    GOOD, VERSION, cache, fixture, github, load, manifest_doc,
)


def _run(coro):
    return asyncio.run(coro)


async def _no_wait(_):
    return None


@pytest.fixture
def fresh_job():
    aa._job.update(running=False, ok=None, message=None, bytes_done=0, bytes_todo=0)
    aa._auto.update(state="idle", reason=None, tries=0, next_at=None, setup=False)
    yield
    aa._auto.update(state="idle", reason=None, tries=0, next_at=None, setup=False)


# ─── the decision ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("policy,go", [
    ("always", True),
    ("sometimes", True),   # setup is the one time a big download goes ahead by itself
    ("", True),            # not answered yet: setup still puts the app here
    ("never", False),      # the egress gate: never, setup or not
])
def test_setup_downloads_the_first_app_under_any_answer_but_never(tmp_path, cache, policy, go):
    m = load(tmp_path, manifest_doc(GOOD))
    ok, why = aa.auto_fetch_decision(m, policy, setup=True)
    assert ok is go, why
    if go:
        assert "setting up" in why and VERSION in why and "without internet" in why
    else:
        assert "turned off" in why and "upload" in why
    # Not setup: the same server waits for an admin.
    assert aa.auto_fetch_decision(m, policy)[0] is False


def test_setup_has_nothing_to_do_when_the_app_is_here_or_there_is_none(tmp_path, cache, github):
    none = load(tmp_path, {"manifest_version": 1, "release": None})
    assert aa.auto_fetch_decision(none, "always", setup=True) == (False, "there is no app release yet")
    m = load(tmp_path, manifest_doc(GOOD))
    with egress.override_policy("always"):
        assert aa.fetch(m)[0]
    ok, why = aa.auto_fetch_decision(m, "always", setup=True)
    assert not ok and "already on this server" in why


# ─── the download, in the background ──────────────────────────────────────

@pytest.mark.parametrize("policy", ["always", "sometimes", ""])
def test_setup_puts_the_app_on_the_server(tmp_path, cache, github, fresh_job, policy):
    m = load(tmp_path, manifest_doc(GOOD))
    waits: list[float] = []

    async def sleep(d):
        waits.append(d)

    with egress.override_policy(policy):
        why = _run(aa.auto_refresh(lambda: m, sleep=sleep, poll=0.01, setup=True))
    assert "is on this server" in why
    assert waits[0] == aa.SETUP_DELAYS[0], "setup's own, sooner first try"
    st = aa.status(m)
    assert st["ready"] is True and st["current_ready"] is True and st["missing"] == []
    assert st["auto"]["setup"] is True and st["auto"]["state"] == "done"
    assert all(re.search(r"github\.com|githubusercontent\.com", s) for s in github.seen)


def test_setup_starts_soon_but_not_in_the_middle_of_the_core_start():
    assert 5 <= aa.SETUP_DELAYS[0] < aa.AUTO_DELAYS[0]
    assert len(aa.SETUP_DELAYS) >= 3, "a house whose line comes up late still gets it"


def test_setup_with_internet_never_downloads_nothing_and_says_so(tmp_path, cache, github, fresh_job):
    m = load(tmp_path, manifest_doc(GOOD))
    with egress.override_policy("never"):
        why = _run(aa.auto_refresh(lambda: m, delays=(0.0, 0.0), sleep=_no_wait, setup=True))
        st = aa.status(m)
    assert github.seen == [], "never means no download, setup or not"
    assert "turned off" in why
    assert st["ready"] is False and st["internet_off"] is True
    assert st["auto"]["setup"] is True and st["auto"]["state"] == "skipped"


def test_setup_with_no_internet_retries_then_leaves_the_reminder(tmp_path, cache, github, fresh_job):
    """No line at install: every try fails, the app is not here, and the
    status the card reads says so (not ready, the failure, setup's)."""
    github.files.clear()
    m = load(tmp_path, manifest_doc(GOOD))
    with egress.override_policy("always"):
        _run(aa.auto_refresh(lambda: m, delays=(0.0, 0.0, 0.0), sleep=_no_wait, poll=0.01, setup=True))
    st = aa.status(m)
    assert st["ready"] is False and len(st["missing"]) == 2
    assert st["auto"] == {**st["auto"], "state": "failed", "tries": 3, "setup": True}
    assert st["fetch"]["ok"] is False and "HTTP 404" in st["fetch"]["message"]


def test_a_partial_download_during_setup_is_not_ready(tmp_path, cache, github, fresh_job):
    """One ABI arrives, the other doesn't: phones don't get a half release,
    the reminder stays, and the file that did arrive is kept for the next
    try."""
    del github.files[aa.file_name(VERSION, "armeabi-v7a")]
    m = load(tmp_path, manifest_doc(GOOD))
    with egress.override_policy("always"):
        _run(aa.auto_refresh(lambda: m, delays=(0.0,), sleep=_no_wait, poll=0.01, setup=True))
    st = aa.status(m)
    assert st["ready"] is False and st["missing"] == [aa.file_name(VERSION, "armeabi-v7a")]
    assert aa._auto["state"] == "failed"
    assert (cache / aa.file_name(VERSION, "arm64-v8a")).is_file()


def test_a_file_check_apk_refuses_is_not_kept(tmp_path, cache, github, fresh_job):
    """The release names a file signed by another key (sizes and digests
    pinned to it, so the download itself succeeds): check_apk refuses it,
    nothing is kept, the reminder stays."""
    other = fixture("other-arm64-v8a.apk")
    files = {"arm64-v8a": other, "armeabi-v7a": GOOD["armeabi-v7a"]}
    github.files[aa.file_name(VERSION, "arm64-v8a")] = other
    m = load(tmp_path, manifest_doc(files))
    assert m["release"]["apks"][0]["sha256"] == hashlib.sha256(other).hexdigest()
    with egress.override_policy("always"):
        _run(aa.auto_refresh(lambda: m, delays=(0.0,), sleep=_no_wait, poll=0.01, setup=True))
    st = aa.status(m)
    assert st["ready"] is False and aa.file_name(VERSION, "arm64-v8a") in st["missing"]
    assert not (cache / aa.file_name(VERSION, "arm64-v8a")).exists()
    assert "certificate" in st["fetch"]["message"]


def test_setup_waits_out_a_freeze(tmp_path, cache, github, fresh_job):
    m = load(tmp_path, manifest_doc(GOOD))

    async def frozen():
        return True

    with egress.override_policy("always"):
        why = _run(aa.auto_refresh(lambda: m, delays=(0.0,), sleep=_no_wait, frozen=frozen, setup=True))
    assert "freeze" in why and github.seen == []


def test_the_reminder_clears_once_an_admin_uploads_the_app(tmp_path, cache, github, fresh_job):
    """The no-internet route stays: setup couldn't, an admin uploads the
    APKs, and the server is ready (the card's reminder goes with it)."""
    github.files.clear()
    m = load(tmp_path, manifest_doc(GOOD))
    with egress.override_policy("always"):
        _run(aa.auto_refresh(lambda: m, delays=(0.0,), sleep=_no_wait, poll=0.01, setup=True))
    assert aa.status(m)["ready"] is False
    for abi, body in GOOD.items():
        src = tmp_path / f"upload-{abi}.part"
        src.write_bytes(body)
        aa.accept_upload(m, src)
    assert aa.status(m)["ready"] is True


# ─── where setup is decided ───────────────────────────────────────────────

def test_the_core_start_is_setup_while_nobody_has_claimed_the_server():
    """One hook for every install (Linux, the Windows installer's distro,
    the Mac): the core's start, unclaimed. Never under stubs, so the
    suite's cores never reach for the real release."""
    src = (aa.REPO / "domovoi" / "main.py").read_text(encoding="utf-8")
    assert "first_run_setup = await admin_auth_mod.ensure_setup_code_if_unclaimed() is not None" in src
    assert ("schedule_auto_refresh(frozen=container_freeze.is_frozen,\n"
            "                                       setup=first_run_setup and not settings.use_stubs)") in src
    # Setup is decided before the refresh is scheduled.
    assert src.index("first_run_setup = await") < src.index("schedule_auto_refresh(")


def test_schedule_passes_setup_through(monkeypatch):
    seen = {}

    async def fake(**kw):
        seen.update(kw)
        return "ok"

    monkeypatch.setattr(aa, "auto_refresh", fake)
    monkeypatch.setattr(aa, "_auto_task", None)

    async def go():
        aa.schedule_auto_refresh(setup=True)
        await aa._auto_task
        await aa.cancel_auto_refresh()

    _run(go())
    assert seen.get("setup") is True
