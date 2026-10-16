"""Release labels and the two update channels (design-notes
V1-RELEASE-PLAN 1.2-1.3; docs/LINUX_HOST.md, "Release channels").

* the Version card names the code by its nearest release tag: "1.0.0" on
  a release, "1.0.0 + 14 (abc1234)" past one (``git describe --tags``);
* ``stable`` only ever moves to a release's commit, ``main`` carries every
  change; the channel is the branch of origin the checkout tracks;
* switching channel changes what the check and the pull follow and never
  moves HEAD; it is refused, changing nothing, where it can't work;
* tags are fetched explicitly, and a published tag that moved upstream is
  an error the card shows, never a silent rewrite.

No DB: git runs for real against throwaway repos.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi import egress, git_version, self_restart
from domovoi.config import settings

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, **_GIT_ENV}
    out = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _commit(repo: Path, name: str) -> str:
    (repo / f"{name}.txt").write_text(name)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


def _release(work: Path, version: str) -> str:
    """Tag HEAD v<version> (lightweight, as `gh release create` does) and
    move stable there, the way finish-release does."""
    _git(work, "tag", f"v{version}")
    _git(work, "branch", "-f", "stable", "HEAD")
    return _git(work, "rev-parse", "HEAD")


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """The project's repo (``work``, branch main, v1.0.0 released at A, so
    stable exists) and the server's clone of it on main. settings.repo_dir
    is the server; no update unit, no pin, internet on."""
    (tmp_path / "gitconfig").touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("DOMOVOI_UPSTREAM_BRANCH", raising=False)
    monkeypatch.setenv("DOMOVOI_UPDATE_DEFAULTS_FILE", str(tmp_path / "no-update-defaults"))
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    (work / "pyproject.toml").write_text('[project]\nname = "domovoi"\nversion = "1.0.0"\n')
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "A")
    sha_a = _release(work, "1.0.0")
    server = tmp_path / "server"
    _git(tmp_path, "clone", "-q", str(work), str(server))
    monkeypatch.setattr(settings, "repo_dir", str(server))
    monkeypatch.setattr(settings, "update_result_file", str(tmp_path / "no-result.json"))
    monkeypatch.setattr(git_version, "_BOOT_SHA", None, raising=False)
    monkeypatch.setattr(git_version, "_BOOT_LABEL", None, raising=False)
    git_version.invalidate_label_cache()
    monkeypatch.setattr(self_restart, "underway", lambda *a, **k: False)
    return work, server, sha_a


# ─── the label ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("described", "label"), [
    ("v1.0.0", "1.0.0"),
    ("v1.0.0-rc1", "1.0.0-rc1"),
    ("v1.0.0-14-gabc1234f", "1.0.0 + 14 (abc1234)"),
    ("v1.0.0-rc1-3-g0123456789ab", "1.0.0-rc1 + 3 (0123456)"),
    ("v1.0.0-14-gabc1234f-dirty", "1.0.0 + 14 (abc1234)"),
    ("v1.0.0-dirty", "1.0.0"),
    ("v1.0.0\n", "1.0.0"),
    ("abc1234", None),
    ("", None),
    ("v", None),
])
def test_a_describe_answer_reads_as_a_release(described, label):
    assert git_version.format_label(described) == label


def test_the_label_on_a_release_past_it_and_on_an_rc(repos):
    work, server, _ = repos
    assert git_version.version_label() == "1.0.0"
    _commit(work, "b")
    _commit(work, "c")
    _git(server, "pull", "-q")
    short = _git(server, "rev-parse", "--short=7", "HEAD")
    assert git_version.version_label() == f"1.0.0 + 2 ({short})"
    _git(work, "tag", "v1.0.1-rc1")
    _git(server, "fetch", "-q", "--tags")
    assert git_version.version_label() == "1.0.1-rc1"


def test_without_a_release_tag_the_label_is_the_project_version_and_the_sha(repos):
    _, server, _ = repos
    _git(server, "tag", "-d", "v1.0.0")
    short = _git(server, "rev-parse", "--short=7", "HEAD")
    assert git_version.version_label() == f"1.0.0 ({short})"
    # Another tag that isn't a release doesn't count.
    _git(server, "tag", "nightly")
    assert git_version.version_label() == f"1.0.0 ({short})"


def test_the_version_state_says_what_runs_and_what_it_follows(repos):
    work, server, _ = repos
    _run(git_version.capture_boot_state())
    _commit(work, "b")
    _git(server, "pull", "-q")
    state = _run(git_version.version_state())
    short = _git(server, "rev-parse", "--short=7", "HEAD")
    assert state["version"] == "1.0.0"                     # what was loaded at boot
    assert state["checkout_version"] == f"1.0.0 + 1 ({short})"
    assert state["channel"] == "main"
    assert state["channels"] == ["stable", "main"]
    assert state["channel_pin"] is None


# ─── the check, on each channel ───────────────────────────────────────────


def test_on_stable_the_check_offers_the_next_release_only(repos):
    work, server, _ = repos
    assert _run(git_version.switch_channel("stable"))["ok"] is True
    _commit(work, "b")                                     # main moves; stable doesn't
    res = _run(git_version.commits_behind())
    assert (res["channel"], res["behind"], res["ahead"]) == ("stable", 0, 0)
    assert res["upstream_version"] == "1.0.0" and res["latest_release"] == "1.0.0"
    sha_c = _commit(work, "c")
    _release(work, "1.0.1")
    res = _run(git_version.commits_behind())
    assert (res["behind"], res["upstream_sha"]) == (2, sha_c)
    assert res["upstream_version"] == "1.0.1" and res["latest_release"] == "1.0.1"
    assert _run(git_version.pull())["pulled"] is True
    assert git_version.version_label() == "1.0.1"


def test_on_main_the_check_counts_commits_and_names_the_latest_release(repos):
    work, _, _ = repos
    _commit(work, "b")
    sha_c = _commit(work, "c")
    res = _run(git_version.commits_behind())
    assert (res["channel"], res["behind"], res["upstream_sha"]) == ("main", 2, sha_c)
    assert res["upstream_version"].startswith("1.0.0 + 2 (")
    assert res["latest_release"] == "1.0.0"


def test_before_the_first_release_there_is_no_latest_release(repos):
    work, server, _ = repos
    _git(work, "branch", "-D", "stable")
    _git(server, "branch", "-r", "-d", "origin/stable")
    res = _run(git_version.commits_behind())
    assert res["latest_release"] is None and res["channel"] == "main"


def test_a_tag_that_moved_upstream_warns_and_never_blocks_an_update(repos):
    """rc2 review C1: a published tag never moves, but if upstream's did,
    this server keeps its copy (git refuses to clobber it) and says so as a
    warning about the labels; the check, the pull and the switch go on.
    One moved tag used to stop every box from updating."""
    work, server, sha_a = repos
    sha_b = _commit(work, "b")
    _git(work, "tag", "-f", "v1.0.0")                       # moved to B
    _git(work, "tag", "v1.0.1-rc1")                          # a new one, also at B
    res = _run(git_version.commits_behind())
    assert res["error"] is None and res["upstream"] is True
    assert (res["behind"], res["upstream_sha"]) == (1, sha_b)
    assert "a published release tag moved upstream (v1.0.0)" in res["tag_warning"]
    assert "Updates are not affected" in res["tag_warning"]
    assert _git(server, "rev-parse", "v1.0.0^{commit}") == sha_a   # its copy stays
    assert _git(server, "rev-parse", "v1.0.1-rc1^{commit}") == sha_b  # new tags still arrive
    pulled = _run(git_version.pull())
    assert pulled["pulled"] is True and pulled["error"] is None
    assert "v1.0.0" in pulled["tag_warning"]
    assert _git(server, "rev-parse", "HEAD") == sha_b
    assert git_version.version_label() == "1.0.1-rc1"
    assert _run(git_version.switch_channel("stable"))["ok"] is True


def test_the_fetch_takes_branches_then_tags(monkeypatch):
    calls = []

    def fake_run(*args):
        calls.append(args)
        return subprocess.CompletedProcess(["git", *args], 0, "", "")

    monkeypatch.setattr(git_version, "_run", fake_run)
    with egress.override_policy("always"):
        assert _run(git_version.fetch()) == {"ok": True, "error": None, "tag_warning": None}
    assert calls == [("fetch", "--no-tags"), ("fetch", "--no-tags", "origin", "refs/tags/*:refs/tags/*")]


def test_no_tags_are_fetched_when_the_branches_fail(monkeypatch):
    calls = []

    def fake_run(*args):
        calls.append(args)
        return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: unable to access")

    monkeypatch.setattr(git_version, "_run", fake_run)
    with egress.override_policy("always"):
        res = _run(git_version.fetch())
    assert res["ok"] is False and "unable to access" in res["error"]
    assert calls == [("fetch", "--no-tags")]


def test_git_reads_no_replace_refs(repos):
    """rc2 review C3: a commit can't be swapped for another under its SHA
    (refs/replace) when the core reads it, a signature check included."""
    work, server, sha_a = repos
    _git(server, "commit", "-q", "--allow-empty", "-m", "other")
    other = _git(server, "rev-parse", "HEAD")
    _git(server, "replace", sha_a, other)
    assert _git(server, "log", "-1", "--format=%s", sha_a) == "other"      # plain git follows it
    assert git_version._run("log", "-1", "--format=%s", sha_a).stdout.strip() == "A"


# ─── switching ────────────────────────────────────────────────────────────


def test_main_to_stable_keeps_head_and_waits_for_the_next_release(repos):
    """A box on main past the latest release: the switch moves nothing,
    the check says it runs newer code, and the next release is offered."""
    work, server, _ = repos
    _commit(work, "b")
    _git(server, "pull", "-q")
    head = _git(server, "rev-parse", "HEAD")
    (server / "a-local-edit.txt").write_text("kept")          # untracked
    (server / "pyproject.toml").write_text('[project]\nname = "domovoi"\nversion = "1.0.0"\n# local\n')

    res = _run(git_version.switch_channel("stable"))

    assert res == {"ok": True, "changed": True, "channel": "stable", "error": None}
    assert _git(server, "rev-parse", "HEAD") == head
    assert _git(server, "rev-parse", "--abbrev-ref", "HEAD") == "stable"
    assert _git(server, "rev-parse", "--abbrev-ref", "@{u}") == "origin/stable"
    assert (server / "a-local-edit.txt").read_text() == "kept"
    assert "# local" in (server / "pyproject.toml").read_text()
    check = _run(git_version.commits_behind())
    assert (check["channel"], check["behind"], check["ahead"]) == ("stable", 0, 1)
    # Release 1.0.1 includes B: the box moves on from there.
    _commit(work, "c")
    _release(work, "1.0.1")
    check = _run(git_version.commits_behind())
    assert (check["behind"], check["ahead"], check["upstream_version"]) == (1, 0, "1.0.1")


def test_stable_to_main_is_a_fast_forward_away(repos):
    work, server, _ = repos
    assert _run(git_version.switch_channel("stable"))["changed"] is True
    _commit(work, "b")
    res = _run(git_version.switch_channel("main"))
    assert res["ok"] is True and res["channel"] == "main"
    assert _git(server, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert _run(git_version.commits_behind())["behind"] == 1


def test_switching_to_the_channel_it_follows_changes_nothing(repos):
    _, server, _ = repos
    head = _git(server, "rev-parse", "HEAD")
    assert _run(git_version.switch_channel("main")) == {
        "ok": True, "changed": False, "channel": "main", "error": None}
    assert _git(server, "rev-parse", "HEAD") == head


def test_a_local_branch_with_nothing_of_its_own_is_remade_at_head(repos):
    """main → stable → (main moves) → main → stable again: the old local
    stable is behind HEAD, so making it again loses nothing."""
    work, server, _ = repos
    _run(git_version.switch_channel("stable"))
    _run(git_version.switch_channel("main"))
    _commit(work, "b")
    _git(server, "pull", "-q")
    head = _git(server, "rev-parse", "HEAD")
    assert _run(git_version.switch_channel("stable"))["ok"] is True
    assert _git(server, "rev-parse", "stable") == head


@pytest.mark.parametrize("channel", ["", "release", "stable; rm -rf /", "origin/main"])
def test_only_the_two_channels(repos, channel):
    _, server, _ = repos
    res = _run(git_version.switch_channel(channel))
    assert res["ok"] is False and "unknown channel" in res["error"]
    assert _git(server, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_refused_before_the_first_release(repos):
    work, server, _ = repos
    _git(work, "branch", "-D", "stable")
    _git(server, "branch", "-r", "-d", "origin/stable")
    res = _run(git_version.switch_channel("stable"))
    assert res["ok"] is False and "no release has been published" in res["error"]
    assert _git(server, "rev-parse", "--abbrev-ref", "@{u}") == "origin/main"


def test_refused_off_the_channels_line(repos):
    _, server, _ = repos
    _git(server, "checkout", "-q", "--orphan", "elsewhere")
    _commit(server, "unrelated")
    res = _run(git_version.switch_channel("stable"))
    assert res["ok"] is False and "could never fast-forward" in res["error"]
    assert _git(server, "rev-parse", "--abbrev-ref", "HEAD") == "elsewhere"


def test_refused_when_the_local_branch_has_commits_of_its_own(repos):
    _, server, _ = repos
    _git(server, "branch", "stable", "origin/stable")
    _git(server, "checkout", "-q", "stable")
    own = _commit(server, "mine")
    _git(server, "checkout", "-q", "main")
    res = _run(git_version.switch_channel("stable"))
    assert res["ok"] is False and "commits of its own" in res["error"]
    assert _git(server, "rev-parse", "stable") == own


def test_refused_where_root_pins_another_branch(repos, monkeypatch, tmp_path):
    _, server, _ = repos
    defaults = tmp_path / "domovoi-update"
    defaults.write_text('DOMOVOI_UPSTREAM_BRANCH="main"\n')
    monkeypatch.setenv("DOMOVOI_UPDATE_DEFAULTS_FILE", str(defaults))
    res = _run(git_version.switch_channel("stable"))
    assert res["ok"] is False and "only follows main" in res["error"]
    assert "DOMOVOI_UPSTREAM_BRANCH" in res["error"]
    # The pin provision.sh writes: both channels.
    defaults.write_text('DOMOVOI_UPSTREAM_BRANCH="stable main"\n')
    assert git_version.channel_pin() == ["stable", "main"]
    assert _run(git_version.switch_channel("stable"))["ok"] is True
    assert _run(git_version.version_state())["channel_pin"] == ["stable", "main"]


def _pin(monkeypatch, tmp_path, value: str) -> None:
    defaults = tmp_path / "domovoi-update"
    defaults.write_text(f'DOMOVOI_UPSTREAM_BRANCH="{value}"\n')
    monkeypatch.setenv("DOMOVOI_UPDATE_DEFAULTS_FILE", str(defaults))


def test_with_a_pin_the_check_and_the_pull_name_the_branch_not_at_u(repos, monkeypatch, tmp_path):
    """Delta security review 2026-10-16: @{u} is the service user's config.
    With root's pin, the check counts against and the pull merges
    refs/remotes/origin/<branch> named outright, and a checkout whose
    upstream is another remote is refused, though @{u} would resolve."""
    work, server, _ = repos
    _pin(monkeypatch, tmp_path, "stable main")
    sha_b = _commit(work, "b")
    res = _run(git_version.commits_behind())
    assert (res["behind"], res["upstream_sha"]) == (1, sha_b)
    # Another remote, with its own "main", set as the upstream by hand.
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", "--bare", str(work), str(other))
    elsewhere = tmp_path / "elsewhere"
    _git(tmp_path, "clone", "-q", str(other), str(elsewhere))
    _commit(elsewhere, "theirs")
    _git(elsewhere, "push", "-q", "origin", "main")
    _git(server, "remote", "add", "evil", str(other))
    _git(server, "fetch", "-q", "evil")
    _git(server, "branch", "-q", "--set-upstream-to", "evil/main", "main")
    assert _git(server, "rev-parse", "--abbrev-ref", "@{u}") == "evil/main"
    res = _run(git_version.commits_behind())
    assert res["upstream"] is False
    assert "follows evil/main, but the update unit only follows origin/stable, origin/main" in res["error"]
    head = _git(server, "rev-parse", "HEAD")
    pulled = _run(git_version.pull())
    assert pulled["pulled"] is False and "follows evil/main" in pulled["error"]
    assert _git(server, "rev-parse", "HEAD") == head
    # Unpinned, the same checkout follows its @{u}, as before the channels.
    monkeypatch.setenv("DOMOVOI_UPDATE_DEFAULTS_FILE", str(tmp_path / "none"))
    assert _run(git_version.commits_behind())["upstream"] is True


def test_with_a_pin_the_named_branch_wins_over_a_remapped_at_u(repos, monkeypatch, tmp_path):
    """The service user's config can map the followed branch onto another
    remote-tracking ref (here origin/sneaky, which @{u} then names). With
    root's pin the check still counts against refs/remotes/origin/main."""
    work, server, sha_a = repos
    _pin(monkeypatch, tmp_path, "stable main")
    _git(server, "config", "remote.origin.fetch", "+refs/heads/main:refs/remotes/origin/sneaky")
    sha_b = _commit(work, "b")
    res = _run(git_version.commits_behind())
    assert _git(server, "rev-parse", "@{u}") == sha_b                 # @{u} is origin/sneaky now
    assert _git(server, "rev-parse", "refs/remotes/origin/main") == sha_a
    assert (res["upstream_sha"], res["behind"]) == (sha_a, 0)


def test_with_a_pin_a_followed_branch_outside_it_is_refused(repos, monkeypatch, tmp_path):
    """Pinned to main alone, a checkout someone pointed at origin/stable by
    hand is refused by the check and the pull."""
    _, server, _ = repos
    _pin(monkeypatch, tmp_path, "main")
    _git(server, "checkout", "-q", "-B", "stable")
    _git(server, "branch", "-q", "--set-upstream-to", "origin/stable", "stable")
    res = _run(git_version.commits_behind())
    assert res["upstream"] is False and "only follows origin/main" in res["error"]
    pulled = _run(git_version.pull())
    assert pulled["pulled"] is False and "only follows origin/main" in pulled["error"]


def test_a_box_behind_both_channels_may_still_switch(repos, monkeypatch, tmp_path):
    """Code behind every pinned tip (an ancestor of them, equal to none) is
    on the pinned branches: the switch goes ahead."""
    work, server, sha_a = repos
    _commit(work, "b")
    _release(work, "1.0.1")                                   # stable: B
    _commit(work, "c")                                        # main: C
    _pin(monkeypatch, tmp_path, "stable main")
    assert _git(server, "rev-parse", "HEAD") == sha_a
    res = _run(git_version.switch_channel("stable"))
    assert res["ok"] is True, res
    assert _git(server, "rev-parse", "HEAD") == sha_a


def test_a_switch_root_would_then_refuse_is_refused(repos, monkeypatch, tmp_path):
    """Pinned to stable alone, a box on main past the latest release can't
    move to stable: its code is on no pinned branch, so the update unit
    would refuse every run after the switch."""
    work, server, _ = repos
    _commit(work, "b")
    _git(server, "pull", "-q")
    _pin(monkeypatch, tmp_path, "stable")
    res = _run(git_version.switch_channel("stable"))
    assert res["ok"] is False and "on none of the branches the update unit allows (stable)" in res["error"]
    assert _git(server, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    # Both channels pinned: it's on main, so the switch goes ahead.
    _pin(monkeypatch, tmp_path, "stable main")
    assert _run(git_version.switch_channel("stable"))["ok"] is True


def test_an_unreadable_settings_file_refuses_the_switch(repos, monkeypatch):
    """REV-20: the core can't see root's pin in a file it can't read, so it
    doesn't guess that there is none."""
    _, server, _ = repos
    monkeypatch.setattr(git_version, "update_defaults_problem", lambda: "unreadable")
    res = _run(git_version.switch_channel("stable"))
    assert res["ok"] is False and "can't read" in res["error"] and "chmod 0644" in res["error"]
    assert _git(server, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert _run(git_version.version_state())["channel_pin_problem"] == "unreadable"


def test_a_pull_on_stable_past_the_release_never_goes_back(repos):
    """main → stable for a box ahead of the release moves nothing (no
    reset, so the update unit's applied_sha and bad_sha stay right), and a
    pull there is a no-op until a release includes the code."""
    work, server, _ = repos
    _commit(work, "b")
    _git(server, "pull", "-q")
    head = _git(server, "rev-parse", "HEAD")
    assert _run(git_version.switch_channel("stable"))["ok"] is True
    pulled = _run(git_version.pull())
    assert _git(server, "rev-parse", "HEAD") == head
    assert pulled["pulled"] is True and head.startswith(pulled["new_sha"])


def test_the_channel_asked_for_is_remembered_and_the_switch_updates_it(repos):
    """provision.sh records stable when origin had none yet (it then follows
    main); the state says so beside the latest release, and a switch makes
    what the household chose the one remembered."""
    _, server, _ = repos
    _git(server, "config", "domovoi.intendedChannel", "stable")
    state = _run(git_version.version_state())
    assert (state["channel"], state["channel_intended"], state["latest_release"]) == ("main", "stable", "1.0.0")
    assert _run(git_version.switch_channel("stable"))["ok"] is True
    assert _git(server, "config", "domovoi.intendedChannel") == "stable"
    assert _run(git_version.switch_channel("main"))["ok"] is True
    assert _git(server, "config", "domovoi.intendedChannel") == "main"
    _git(server, "config", "domovoi.intendedChannel", "nonsense")
    assert _run(git_version.version_state())["channel_intended"] is None


def test_refused_while_an_update_runs(repos, monkeypatch):
    monkeypatch.setattr(self_restart, "underway", lambda *a, **k: True)
    res = _run(git_version.switch_channel("stable"))
    assert res["ok"] is False and "under way" in res["error"]


def test_no_git_at_all_under_never(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("git ran under never")

    monkeypatch.setattr(git_version, "_run", boom)
    with egress.override_policy("never"):
        res = _run(git_version.switch_channel("stable"))
    assert res["ok"] is False and res["error"] == egress.TURNED_OFF_REASON
