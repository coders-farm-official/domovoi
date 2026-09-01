"""The version panel must distinguish RUNNING code from CHECKED-OUT code.

`current_sha()` shells out to git on every call, so it reports the working
tree. After `git pull` without a restart the tree has moved but the process
is still executing the modules it imported at boot — and the dashboard's
"what's running / is there an update?" panel would show the new SHA while
the old code served every request.

That is not hypothetical: on 2026-09-01 a calculator fix read as deployed
(`/v1/admin/version` → the new SHA) while the fixed code had never been
loaded, which sent the debugging in the wrong direction entirely.
"""

from __future__ import annotations

import pytest

from domovoi import git_version


@pytest.fixture(autouse=True)
def _reset_boot_state(monkeypatch):
    """Each test owns the module-level boot capture."""
    monkeypatch.setattr(git_version, "_BOOT_SHA", None, raising=False)
    monkeypatch.setattr(git_version, "_BOOT_MONOTONIC", None, raising=False)
    monkeypatch.setattr(git_version, "_BOOT_WALL", None, raising=False)


def _fix_current_sha(monkeypatch, value):
    async def fake():
        return value
    monkeypatch.setattr(git_version, "current_sha", fake)


@pytest.mark.asyncio
async def test_boot_sha_is_pinned_and_does_not_follow_the_tree(monkeypatch):
    """The whole point: the tree moves, the reported running SHA does not."""
    _fix_current_sha(monkeypatch, "aaaaaaa")
    await git_version.capture_boot_state()
    assert git_version.boot_sha() == "aaaaaaa"

    # Someone runs `git pull`.
    _fix_current_sha(monkeypatch, "bbbbbbb")

    assert git_version.boot_sha() == "aaaaaaa", "running SHA must not track the tree"
    state = await git_version.version_state()
    assert state["running_sha"] == "aaaaaaa"
    assert state["checkout_sha"] == "bbbbbbb"
    assert state["restart_required"] is True
    # `sha` keeps its old name but now means the running code.
    assert state["sha"] == "aaaaaaa"


@pytest.mark.asyncio
async def test_no_restart_required_when_they_match(monkeypatch):
    _fix_current_sha(monkeypatch, "aaaaaaa")
    await git_version.capture_boot_state()
    state = await git_version.version_state()
    assert state["restart_required"] is False


@pytest.mark.asyncio
async def test_dirty_suffix_alone_does_not_demand_a_restart(monkeypatch):
    """A dev box is dirty constantly; the -dirty marker moves with any
    uncommitted edit and must not scream 'restart' forever."""
    _fix_current_sha(monkeypatch, "aaaaaaa")
    await git_version.capture_boot_state()
    _fix_current_sha(monkeypatch, "aaaaaaa-dirty")

    state = await git_version.version_state()
    assert state["restart_required"] is False


@pytest.mark.asyncio
async def test_unknown_sha_never_claims_a_restart_is_needed(monkeypatch):
    """No git / not a repo must not produce a permanent false alarm."""
    _fix_current_sha(monkeypatch, "unknown")
    await git_version.capture_boot_state()
    _fix_current_sha(monkeypatch, "ccccccc")

    state = await git_version.version_state()
    assert state["restart_required"] is False


@pytest.mark.asyncio
async def test_capture_is_idempotent(monkeypatch):
    """A second call must not re-pin to a tree that has since moved."""
    _fix_current_sha(monkeypatch, "aaaaaaa")
    await git_version.capture_boot_state()
    _fix_current_sha(monkeypatch, "bbbbbbb")
    await git_version.capture_boot_state()

    assert git_version.boot_sha() == "aaaaaaa"


@pytest.mark.asyncio
async def test_uptime_and_started_at_are_reported(monkeypatch):
    _fix_current_sha(monkeypatch, "aaaaaaa")
    await git_version.capture_boot_state()
    state = await git_version.version_state()

    assert state["started_at"] is not None
    assert state["uptime_sec"] is not None
    assert state["uptime_sec"] >= 0


@pytest.mark.asyncio
async def test_state_is_safe_before_capture(monkeypatch):
    """Stub/test contexts skip the boot hook — report unknown, don't crash."""
    _fix_current_sha(monkeypatch, "aaaaaaa")
    state = await git_version.version_state()

    assert state["running_sha"] == "unknown"
    assert state["restart_required"] is False
    assert state["started_at"] is None
    assert state["uptime_sec"] is None
