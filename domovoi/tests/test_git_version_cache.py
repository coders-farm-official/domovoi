"""cached_current_sha(): the admin snapshot is polled every 1.5 s by the
dashboard, so the SHA it embeds must not shell out to git on every call.

Also pins the Windows console-window guard: every git launch passes
CREATE_NO_WINDOW (0 on other platforms) so a console-less backend does not
pop a conhost window per poll.
"""

from __future__ import annotations

import subprocess

import pytest

from domovoi import git_version


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(git_version, "_SHA_CACHE", None, raising=False)


def _count_calls(monkeypatch, values):
    calls = {"n": 0}

    async def fake():
        calls["n"] += 1
        return values[min(calls["n"] - 1, len(values) - 1)]

    monkeypatch.setattr(git_version, "current_sha", fake)
    return calls


async def test_cached_sha_asks_git_once_within_ttl(monkeypatch):
    calls = _count_calls(monkeypatch, ["aaaaaaa"])
    assert await git_version.cached_current_sha() == "aaaaaaa"
    assert await git_version.cached_current_sha() == "aaaaaaa"
    assert await git_version.cached_current_sha() == "aaaaaaa"
    assert calls["n"] == 1


async def test_cached_sha_refreshes_after_ttl(monkeypatch):
    calls = _count_calls(monkeypatch, ["aaaaaaa", "bbbbbbb"])
    clock = {"t": 1000.0}
    monkeypatch.setattr(git_version.time, "monotonic", lambda: clock["t"])
    assert await git_version.cached_current_sha(ttl_sec=60.0) == "aaaaaaa"
    clock["t"] += 30.0
    assert await git_version.cached_current_sha(ttl_sec=60.0) == "aaaaaaa"
    clock["t"] += 31.0
    assert await git_version.cached_current_sha(ttl_sec=60.0) == "bbbbbbb"
    assert calls["n"] == 2


async def test_invalidate_forces_a_fresh_read(monkeypatch):
    calls = _count_calls(monkeypatch, ["aaaaaaa", "bbbbbbb"])
    assert await git_version.cached_current_sha() == "aaaaaaa"
    git_version.invalidate_sha_cache()
    assert await git_version.cached_current_sha() == "bbbbbbb"
    assert calls["n"] == 2


async def test_pull_invalidates_the_cache(monkeypatch, tmp_path):
    calls = _count_calls(monkeypatch, ["aaaaaaa", "bbbbbbb", "bbbbbbb"])
    assert await git_version.cached_current_sha() == "aaaaaaa"
    # No allowed-signers file: the pull is not enforced (test_git_version_signing
    # covers the gate), and the host's own file never leaks in.
    monkeypatch.setenv("DOMOVOI_ALLOWED_SIGNERS", str(tmp_path / "no-such-file"))

    def fake_run(cmd, **kwargs):
        # fetch, then the upstream's SHA, then the fast-forward to it.
        out = "b" * 40 + "\n" if "rev-parse" in cmd else "Already up to date.\n"
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(git_version.subprocess, "run", fake_run)
    result = await git_version.pull()
    assert result["pulled"] is True
    assert await git_version.cached_current_sha() == "bbbbbbb"


async def test_every_git_launch_hides_its_console_window(monkeypatch):
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(kwargs.get("creationflags"))
        return subprocess.CompletedProcess(cmd, 0, stdout="abc1234\n", stderr="")

    monkeypatch.setattr(git_version.subprocess, "run", fake_run)
    await git_version.current_sha()
    assert seen, "current_sha() must shell out"
    expected = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert all(flag == expected for flag in seen), seen
