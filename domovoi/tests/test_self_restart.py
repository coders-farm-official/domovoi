"""Dashboard-driven service restart.

The action shells out to sudo, so the interesting behaviour is all in what
it refuses to do: never prompt, never half-restart, and never claim a host
can bounce itself when the sudoers grant isn't there. Every probe is faked —
no sudo, no systemd, no DB, so these run everywhere rather than skipping.
"""

from __future__ import annotations

import asyncio
import subprocess
import time

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


# ─── what the panel shows in place of the button ─────────────────────────


def test_the_manual_command_is_the_restart_this_host_does(monkeypatch):
    """Shown, never run: the same restart the button would have asked
    systemd for, in the shape of the sudoers grant's mode."""
    monkeypatch.setattr(self_restart, "_WINDOWS", False)
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))
    calls: list[list[str]] = []
    monkeypatch.setattr(self_restart.subprocess, "run", _fake_run(0, calls))
    assert self_restart.manual_command("restart") == "sudo systemctl restart domovoi-core domovoi-web"
    assert self_restart.manual_command("update") == "sudo systemctl start domovoi-update.service"
    monkeypatch.setattr(self_restart, "restart_mode", lambda: "update")
    assert self_restart.manual_command() == "sudo systemctl start domovoi-update.service"
    assert calls == []


def test_no_manual_command_on_a_host_without_systemd(monkeypatch):
    """A Windows or development box: there is no systemctl to name, so the
    panel must not print one (it used to show the Linux command there)."""
    monkeypatch.setattr(self_restart, "_WINDOWS", False)
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which({"sudo": "/usr/bin/sudo"}))
    assert self_restart.manual_command("restart") is None
    monkeypatch.setattr(self_restart, "_WINDOWS", True)
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))
    assert self_restart.manual_command("restart") is None
    assert self_restart.manual_command("update") is None


def test_version_state_carries_the_manual_command(monkeypatch):
    monkeypatch.setattr(self_restart, "_WINDOWS", False)
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))
    monkeypatch.setattr(self_restart.subprocess, "run", _fake_run(returncode=1))
    monkeypatch.setattr(self_restart, "restart_mode", lambda: "update")

    state = asyncio.run(git_version.version_state())
    assert state["restart_capable"] is False
    assert state["restart_mode"] == "update"
    assert state["restart_command"] == "sudo systemctl start domovoi-update.service"

    monkeypatch.setattr(self_restart, "_WINDOWS", True)
    self_restart._cap_cache = None
    state = asyncio.run(git_version.version_state())
    assert state["restart_command"] is None


# ─── the two ways a restart silently doesn't happen ───────────────────────


def test_the_scheduled_task_is_strongly_referenced(monkeypatch):
    """asyncio keeps only a weak reference, so a task nobody holds can be
    collected mid-sleep — the restart then never fires while the endpoint
    has already answered ok."""
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))
    monkeypatch.setattr(self_restart.subprocess, "run", _fake_run(0))
    monkeypatch.setattr(self_restart, "_RESTART_DELAY_SEC", 0.05)

    async def _go():
        self_restart._PENDING.clear()
        await self_restart.restart()
        assert len(self_restart._PENDING) == 1     # held while in flight
        await asyncio.sleep(0.2)
        return len(self_restart._PENDING)

    assert asyncio.run(_go()) == 0                 # and released after


def test_a_refused_restart_is_logged_not_swallowed(monkeypatch, caplog):
    """The endpoint answered 'ok' a second ago — it only knew the bounce was
    scheduled. If sudo refuses here, the journal is the only evidence."""
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))

    def refuse(cmd, **kw):
        if "-l" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="sudo: a password is required")

    monkeypatch.setattr(self_restart.subprocess, "run", refuse)
    with caplog.at_level("ERROR"):
        self_restart._spawn_restart()
    assert "REFUSED" in caplog.text
    assert "password is required" in caplog.text
    assert "sudoers" in caplog.text          # says where to look


def test_an_accepted_restart_says_so(monkeypatch, caplog):
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))
    monkeypatch.setattr(self_restart.subprocess, "run", _fake_run(0))
    with caplog.at_level("WARNING"):
        self_restart._spawn_restart()
    assert "accepted" in caplog.text


def test_the_refusal_log_never_leaks_a_password(monkeypatch, caplog):
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))

    def refuse(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="denied")

    monkeypatch.setattr(self_restart.subprocess, "run", refuse)
    with caplog.at_level("ERROR"):
        self_restart._spawn_restart()
    # The logged command is the systemctl invocation only — no credentials
    # exist in this path, and none should ever appear if that changes.
    assert "password=" not in caplog.text


# ─── one restart at a time ────────────────────────────────────────────────
#
# OWNER REPORT (2026-10-09): pressed "Restart to apply changes", reloaded
# the page, and the button offered the restart again while the update unit
# was still running. The page's memory of the press is gone on a reload, so
# the server says whether one is under way, and refuses a second.

SHOW_STATE = ["/usr/bin/systemctl", "show", "--property=ActiveState", "--value",
              "domovoi-update.service"]


def _unit_host(monkeypatch, *, state=None, show_rc=0, record=None, grant=True):
    """A Linux host with the update unit installed: `systemctl show` answers
    `state` (rc `show_rc`), sudo grants the unit's start when `grant`."""
    monkeypatch.setattr(self_restart, "_WINDOWS", False)
    monkeypatch.setattr(self_restart, "restart_mode", lambda: "update")
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))

    def run(cmd, **kw):
        if record is not None:
            record.append(cmd)
        if cmd == SHOW_STATE:
            return subprocess.CompletedProcess(cmd, show_rc, stdout=f"{state or ''}\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0 if grant else 1, stdout="", stderr="")

    monkeypatch.setattr(self_restart.subprocess, "run", run)


def _result(status, started_at):
    return {"status": status, "started_at": started_at}


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def test_nothing_is_under_way_by_default(monkeypatch):
    _unit_host(monkeypatch, state="inactive")
    assert self_restart.underway(None) is False


@pytest.mark.parametrize("state", ["activating", "active", "deactivating", "reloading"])
def test_the_update_unit_running_is_a_restart_under_way(monkeypatch, state):
    """However it was started: the dashboard, another tab, or by hand."""
    _unit_host(monkeypatch, state=state)
    assert self_restart.underway(None) is True


@pytest.mark.parametrize("state", ["inactive", "failed"])
def test_a_finished_unit_is_not(monkeypatch, state):
    _unit_host(monkeypatch, state=state)
    # A "running" record the unit never got to finish (killed hard) is
    # overruled by systemd's word.
    assert self_restart.underway(_result("running", _iso(time.time() - 30))) is False


def test_without_systemd_the_running_record_answers_while_recent(monkeypatch):
    _unit_host(monkeypatch, show_rc=1)
    assert self_restart.underway(_result("running", _iso(time.time() - 60))) is True
    assert self_restart.underway(_result("ok", _iso(time.time() - 60))) is False
    # Older than the unit's TimeoutStartSec: a run that can't still be going.
    stale = time.time() - self_restart._RUNNING_RECORD_MAX_SEC - 60
    assert self_restart.underway(_result("running", _iso(stale))) is False
    assert self_restart.underway(_result("running", "not a time")) is False


def test_without_the_unit_only_the_accepted_restart_counts(monkeypatch):
    """No unit to ask: the bounce ends this process, so the restart it
    accepted is the whole story, and systemctl is never asked."""
    calls: list[list[str]] = []
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))
    monkeypatch.setattr(self_restart.subprocess, "run", _fake_run(0, calls))
    assert self_restart.underway(None, "restart") is False
    self_restart._note_request("restart")
    assert self_restart.underway(None, "restart") is True
    assert calls == []


def test_a_second_press_is_refused_and_schedules_nothing(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))
    monkeypatch.setattr(self_restart.subprocess, "run", _fake_run(0, calls))
    monkeypatch.setattr(self_restart, "_RESTART_DELAY_SEC", 0.01)

    async def _go():
        first = await self_restart.restart()
        second = await self_restart.restart()
        await asyncio.sleep(0.15)
        return first, second

    first, second = asyncio.run(_go())
    assert first["ok"] is True and first["in_progress"] is False
    assert second["ok"] is False and second["in_progress"] is True
    assert "under way" in second["error"]
    fired = [c for c in calls if "-l" not in c]
    assert len(fired) == 1, "only the first press may bounce the services"


def test_two_presses_at_once_schedule_one_restart(monkeypatch):
    """Both get past the first look before either is recorded: the record
    is checked again with no await in between."""
    calls: list[list[str]] = []
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))
    monkeypatch.setattr(self_restart.subprocess, "run", _fake_run(0, calls))
    monkeypatch.setattr(self_restart, "_RESTART_DELAY_SEC", 0.01)

    async def _go():
        res = await asyncio.gather(self_restart.restart(), self_restart.restart())
        await asyncio.sleep(0.15)
        return res

    results = asyncio.run(_go())
    assert sorted(r["ok"] for r in results) == [False, True]
    assert len([c for c in calls if "-l" not in c]) == 1


def test_a_restart_that_never_fired_is_not_under_way(monkeypatch):
    """sudo refused at the fire: nothing restarts, so the next press must
    not be refused for it."""
    monkeypatch.setattr(self_restart.shutil, "which", _fake_which(BOTH_PRESENT))

    def refuse_fire(cmd, **kw):
        rc = 0 if "-l" in cmd else 1
        return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="no")

    monkeypatch.setattr(self_restart.subprocess, "run", refuse_fire)
    monkeypatch.setattr(self_restart, "_RESTART_DELAY_SEC", 0.01)

    async def _go():
        res = await self_restart.restart()
        during = self_restart.underway(None, "restart")
        await asyncio.sleep(0.15)
        return res, during, self_restart.underway(None, "restart")

    res, during, after = asyncio.run(_go())
    assert res["ok"] is True
    assert during is True
    assert after is False


def test_the_accepted_restart_lapses_after_its_grace():
    self_restart._note_request("restart")
    at_mono, at_wall, mode = self_restart._REQUEST
    self_restart._REQUEST = (at_mono - self_restart._REQUEST_GRACE_SEC - 1, at_wall, mode)
    assert self_restart.underway(None, "restart") is False


def test_a_run_the_unit_finished_since_the_press_ends_it(monkeypatch):
    """A run the unit refuses at once (a signature check, say) never ends
    this process; its result, newer than the press, ends the wait. A result
    from before the press is the run before, and doesn't."""
    _unit_host(monkeypatch, state="inactive")
    self_restart._note_request("update")
    now = time.time()
    assert self_restart.underway(_result("ok", _iso(now - 3600))) is True
    assert self_restart.underway(_result("running", _iso(now + 1))) is True
    assert self_restart.underway(_result("refused", _iso(now + 1))) is False


def test_an_update_already_running_refuses_the_press(monkeypatch):
    calls: list[list[str]] = []
    _unit_host(monkeypatch, state="activating", record=calls)
    res = asyncio.run(self_restart.restart())
    assert res == {"ok": False, "mode": "update", "units": ["domovoi-update.service"],
                   "delay_sec": None, "in_progress": True,
                   "error": "a restart or update is already under way; wait for it to finish"}
    assert calls == [SHOW_STATE], "no sudo at all, not even the probe"


def test_version_state_says_a_restart_is_under_way(monkeypatch):
    _unit_host(monkeypatch, state="activating")
    assert asyncio.run(git_version.version_state())["restart_in_progress"] is True
    _unit_host(monkeypatch, state="inactive")
    assert asyncio.run(git_version.version_state())["restart_in_progress"] is False


def test_no_pull_while_a_restart_is_under_way(monkeypatch):
    """The unit applies the HEAD it read when it started; moving the
    checkout under it would load code it never checked."""
    _unit_host(monkeypatch, state="activating")
    ran: list = []
    monkeypatch.setattr(git_version, "_run", lambda *a: ran.append(a))
    monkeypatch.setattr(git_version.egress, "internet_turned_off", lambda: False)
    res = asyncio.run(git_version.pull())
    assert res["pulled"] is False
    assert "under way" in res["error"]
    assert ran == [], "no git at all"
