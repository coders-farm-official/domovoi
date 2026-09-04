"""Dashboard-driven service restart.

The action shells out to sudo, so the interesting behaviour is all in what
it refuses to do: never prompt, never half-restart, and never claim a host
can bounce itself when the sudoers grant isn't there. Every probe is faked —
no sudo, no systemd, no DB, so these run everywhere rather than skipping.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from domovoi import git_version, self_restart


@pytest.fixture(autouse=True)
def _cold_capability_cache():
    """capable() memoizes for a minute — start every test cold."""
    self_restart._cap_cache = None
    yield
    self_restart._cap_cache = None


def _fake_which(mapping):
    return lambda name: mapping.get(name)


BOTH_PRESENT = {"systemctl": "/usr/bin/systemctl", "sudo": "/usr/bin/sudo"}


def _fake_run(returncode=0, record=None):
    def _run(cmd, **kw):
        if record is not None:
            record.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr="")

    return _run


# ─── capability probe ─────────────────────────────────────────────────────


def test_not_capable_without_systemd(monkeypatch):
    monkeypatch.setattr(self_restart, "shutil", type("S", (), {"which": staticmethod(_fake_which({}))}))
    ok, why = self_restart.capable()
    assert ok is False
    assert "systemd" in why


def test_not_capable_without_sudoers_grant(monkeypatch):
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))
    monkeypatch.setattr(self_restart.subprocess, "run", _fake_run(returncode=1))
    ok, why = self_restart.capable()
    assert ok is False
    assert "sudoers" in why


def test_capable_probe_never_actually_restarts(monkeypatch):
    """`sudo -n -l <cmd>` asks permission; it must not BE the restart."""
    calls: list[list[str]] = []
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))
    monkeypatch.setattr(self_restart.subprocess, "run", _fake_run(0, calls))
    ok, why = self_restart.capable()
    assert (ok, why) == (True, None)
    assert len(calls) == 1
    assert calls[0][:3] == ["/usr/bin/sudo", "-n", "-l"]   # list, not execute


def test_capability_is_cached(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))
    monkeypatch.setattr(self_restart.subprocess, "run", _fake_run(0, calls))
    asyncio.run(self_restart.capable_async())
    asyncio.run(self_restart.capable_async())
    assert len(calls) == 1, "second call should hit the memo, not fork sudo again"


# ─── the restart action ───────────────────────────────────────────────────


def test_restart_refuses_when_incapable(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))
    monkeypatch.setattr(self_restart.subprocess, "run", _fake_run(1, calls))

    res = asyncio.run(self_restart.restart())
    assert res["ok"] is False
    assert res["error"] and "sudoers" in res["error"]
    # Exactly the probe — no restart was attempted.
    assert all("-l" in c for c in calls)


def test_restart_bounces_both_units(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))
    monkeypatch.setattr(self_restart.subprocess, "run", _fake_run(0, calls))
    monkeypatch.setattr(self_restart, "_RESTART_DELAY_SEC", 0.01)

    async def _go():
        res = await self_restart.restart()
        # The response must be ready BEFORE the bounce fires, or the client
        # can't tell "restarting" from "the server broke".
        assert res["ok"] is True
        assert res["units"] == ["domovoi-core.service", "domovoi-web.service"]
        assert len(calls) == 1, "restart must not have fired yet"
        await asyncio.sleep(0.15)
        return calls

    fired = asyncio.run(_go())
    assert len(fired) == 2
    cmd = fired[-1]
    assert "--no-block" in cmd and "restart" in cmd
    assert "domovoi-core.service" in cmd and "domovoi-web.service" in cmd
    assert "-l" not in cmd


def test_restart_never_raises_when_spawn_fails(monkeypatch):
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))

    def _boom(cmd, **kw):
        if "-l" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise OSError("fork failed")

    monkeypatch.setattr(self_restart.subprocess, "run", _boom)
    monkeypatch.setattr(self_restart, "_RESTART_DELAY_SEC", 0.01)

    async def _go():
        res = await self_restart.restart()
        await asyncio.sleep(0.15)
        return res

    assert asyncio.run(_go())["ok"] is True   # scheduling succeeded


# ─── what the panel reads ─────────────────────────────────────────────────


def test_version_state_reports_restart_capability(monkeypatch):
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))
    monkeypatch.setattr(self_restart.subprocess, "run", _fake_run(returncode=1))

    state = asyncio.run(git_version.version_state())
    assert state["restart_capable"] is False
    assert "sudoers" in state["restart_hint"]
