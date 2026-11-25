"""The update notice's "behind" rule and its highlights from git
(domovoi/update_notice.py; design note UPDATE-NOTICES §3, §4).

git runs for real against throwaway repos: an "origin" with main, stable,
digits-only and rc tags, and the server's clone. The fetch is a local
clone's (no network); the signature check is faked (its own tests are
test_update_signed_release.py). No DB: the state lives in memory.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from domovoi import egress, git_version, self_restart, update_notice
from domovoi.config import settings

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}
HL = ["Friend Houses is open to everyone in the household, by voice too.",
      "Voice ID learns better from short samples.",
      "Music keeps playing after you ask a question."]
NO_HL = "# Domovoi {v}\n\nIntro.\n\n## At a glance\n\n- **Something**, in a line.\n"


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, **_GIT_ENV}
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True,
                          check=True).stdout.strip()


def _commit(repo: Path, name: str, files: dict[str, str] | None = None) -> str:
    for path, body in (files or {f"{name}.txt": name}).items():
        p = repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


def _doc(version: str, highlights: list[str] | None) -> dict[str, str]:
    text = NO_HL.format(v=version)
    if highlights:
        text = text.replace("## At a glance", "## Highlights\n\n" + "\n".join(f"- {h}" for h in highlights)
                            + "\n\n## At a glance")
    return {f"docs/release/v{version}.md": text}


def _release(work: Path, version: str, highlights: list[str] | None = None) -> str:
    sha = _commit(work, f"release {version}", _doc(version, highlights))
    _git(work, "tag", f"v{version}")
    _git(work, "branch", "-f", "stable", "HEAD")
    return sha


@pytest.fixture
def world(tmp_path, monkeypatch):
    (tmp_path / "gitconfig").touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("DOMOVOI_UPSTREAM_BRANCH", raising=False)
    monkeypatch.setenv("DOMOVOI_UPDATE_DEFAULTS_FILE", str(tmp_path / "no-update-defaults"))
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _commit(work, "start")
    r141 = _release(work, "1.4.1")          # the v1.4.1 tag's own file: no Highlights
    server = tmp_path / "server"
    _git(tmp_path, "clone", "-q", str(work), str(server))
    monkeypatch.setattr(settings, "repo_dir", str(server))
    monkeypatch.setattr(settings, "update_result_file", str(tmp_path / "no-result.json"))
    monkeypatch.setattr(settings, "update_auto_check", True)
    monkeypatch.setattr(self_restart, "underway", lambda *a, **k: False)
    git_version.invalidate_label_cache()
    update_notice.reset_for_tests()
    store: dict = {}

    async def load():
        return store.get("v")

    async def save(value):
        store["v"] = value

    monkeypatch.setattr(update_notice, "_db_load", load)
    monkeypatch.setattr(update_notice, "_db_save", save)
    signed: dict[str, bool] = {}

    async def verify(tag, commit):
        return signed.get(tag, True)

    monkeypatch.setattr(update_notice, "verify_release", verify)
    yield work, server, r141, signed
    update_notice.reset_for_tests()


def _fetch(server: Path) -> None:
    _git(server, "fetch", "-q", "origin")
    _git(server, "fetch", "-q", "origin", "refs/tags/*:refs/tags/*")
    update_notice.forget_caches()


def _follow(server: Path, branch: str) -> None:
    _git(server, "checkout", "-q", "-B", branch, f"origin/{branch}")
    _git(server, "branch", "-q", f"--set-upstream-to=origin/{branch}")
    update_notice.forget_caches()


def _rel(server: Path, bad: str | None = None) -> tuple[str, int | None]:
    found = update_notice.stable_release()
    assert found
    tag, commit = found
    head = _git(server, "rev-parse", "HEAD")
    return update_notice.relation(tag, commit, head, bad)


def test_stable_house_on_the_release_is_the_same_then_behind_the_next(world):
    work, server, r141, _ = world
    _follow(server, "stable")
    _fetch(server)
    assert update_notice.stable_release() == ("v1.4.1", r141)
    assert _rel(server) == ("same", None)
    _commit(work, "work for 1.5.0")
    _release(work, "1.5.0", HL)
    _fetch(server)
    assert update_notice.stable_release()[0] == "v1.5.0"
    assert _rel(server) == ("behind", None)


def test_main_house_ahead_counts_changes_by_first_parent(world):
    work, server, _, _ = world
    # Two merges into main, one of them carrying three commits.
    _git(work, "checkout", "-q", "-b", "feature")
    for i in range(3):
        _commit(work, f"f{i}")
    _git(work, "checkout", "-q", "main")
    _git(work, "merge", "-q", "--no-ff", "-m", "merge feature", "feature")
    _commit(work, "one more")
    _follow(server, "main")
    _git(server, "pull", "-q")
    _fetch(server)
    assert _rel(server) == ("ahead", 2)


def test_main_house_that_pulled_before_the_release_is_behind(world):
    work, server, _, _ = world
    _commit(work, "on main")
    _follow(server, "main")
    _git(server, "pull", "-q")
    _commit(work, "more on main")
    _release(work, "1.5.0", HL)
    _fetch(server)
    assert _rel(server) == ("behind", None)
    # Pulling main's tip brings the release: no longer behind, now the same.
    _git(server, "pull", "-q")
    update_notice.forget_caches()
    assert _rel(server)[0] == "same"


def test_a_branch_on_a_newer_base_that_lacks_r_is_not_behind(world):
    work, server, r141, _ = world
    # A dev line from 1.4.1 that has its own v1.6.0 (digits) but not 1.5.0's commit.
    _git(work, "checkout", "-q", "-b", "dev", r141)
    _commit(work, "dev work")
    _git(work, "tag", "v1.6.0")
    _git(work, "checkout", "-q", "main")
    _commit(work, "main work")
    _release(work, "1.5.0", HL)
    _fetch(server)
    _follow(server, "dev")
    assert _rel(server) == ("unknown", None)


def test_release_candidates_never_count(world):
    work, server, r141, _ = world
    # An rc on main moves no stable: R stays 1.4.1.
    _commit(work, "rc work")
    _git(work, "tag", "v1.5.0-rc1")
    _fetch(server)
    assert update_notice.stable_release() == ("v1.4.1", r141)
    # stable at a commit with only an rc tag: no release at all.
    _git(work, "branch", "-f", "stable", "HEAD")
    _fetch(server)
    assert update_notice.stable_release() is None
    # An rc is no Base: a branch at v1.6.0-rc1 lacking R=1.5.0 is behind it.
    _git(work, "checkout", "-q", "-b", "next", r141)
    _commit(work, "next work")
    _git(work, "tag", "v1.6.0-rc1")
    _git(work, "checkout", "-q", "main")
    _release(work, "1.5.0", HL)
    _fetch(server)
    _follow(server, "next")
    assert update_notice.base_version(_git(server, "rev-parse", "HEAD")) == (1, 4, 1)
    assert _rel(server) == ("behind", None)


def test_a_rolled_back_release_is_not_offered(world):
    work, server, _, _ = world
    _follow(server, "stable")
    r150 = _release(work, "1.5.0", HL)
    _fetch(server)
    assert _rel(server, bad=r150) == ("unknown", None)
    assert _rel(server, bad=None) == ("behind", None)


def test_the_highest_digits_tag_on_stable_names_r(world):
    work, server, _, _ = world
    _release(work, "1.5.0", HL)
    _git(work, "tag", "v1.5.0-rc3")
    _git(work, "tag", "v1.4.9")
    _fetch(server)
    assert update_notice.stable_release()[0] == "v1.5.0"


# ─── through the check: notices, signatures, highlights ──────────────────


def _check(found_by: str = "manual") -> dict:
    return asyncio.run(update_notice.run_check(found_by))


def test_an_unsigned_release_makes_no_notice_and_no_highlights(world):
    work, server, _, signed = world
    _follow(server, "stable")
    _release(work, "1.5.0", HL)
    signed["v1.5.0"] = False
    with egress.override_policy("always"):
        res = _check()
        assert res["upstream"] is True and res["behind"] == 1
        up = res["updates"]
        assert up["notice"] == {"available": False, "updating": False}
        assert up["relation"] == "unknown"
        assert up["latest_release"]["signed"] is False and up["latest_release"]["highlights"] == []
        assert asyncio.run(update_notice.notice()) == {"available": False, "updating": False}


def test_a_signed_release_makes_a_notice_with_its_highlights(world):
    work, server, _, _ = world
    _follow(server, "stable")
    _release(work, "1.5.0", HL)
    with egress.override_policy("always"):
        up = _check()["updates"]
        assert up["relation"] == "behind"
        assert up["latest_release"]["highlights"] == HL
        n = asyncio.run(update_notice.notice())
    assert n["available"] is True and n["version"] == "1.5.0" and n["highlights"] == HL
    assert n["found_by"] == "manual" and set(n) == {"available", "version", "highlights", "found_by",
                                                    "found_at", "updating"}


def test_the_v1_4_1_tag_shows_no_bullets_quietly(world, caplog):
    """Owner decision 4: the v1.4.1 tag's own file has no Highlights. A
    house checking it shows no bullets, and nothing else breaks."""
    work, server, r141, _ = world
    _git(server, "checkout", "-q", "-B", "old", f"{r141}~1")
    _git(server, "branch", "-q", "--set-upstream-to=origin/stable")
    with egress.override_policy("always"), caplog.at_level("INFO"):
        res = _check()
        up = res["updates"]
        assert up["latest_release"] == {"version": "1.4.1", "tag": "v1.4.1", "commit": r141,
                                        "signed": True, "highlights": []}
        assert up["relation"] == "behind"
        n = asyncio.run(update_notice.notice())
        assert n["available"] is True and n["version"] == "1.4.1" and n["highlights"] == []
        # The version read says the same, without a fetch.
        assert asyncio.run(update_notice.updates_state())["latest_release"]["highlights"] == []
    assert not [r for r in caplog.records if r.levelname in ("WARNING", "ERROR")]
    assert sum("no highlights for v1.4.1" in r.getMessage() for r in caplog.records) == 1


def test_main_reads_main_s_copy_when_the_tag_has_none_and_stable_never_does(world):
    work, server, r141, _ = world
    # 1.4.1's bullets backfilled on main after the tag (the tag never moves).
    _commit(work, "backfill", _doc("1.4.1", HL))
    with egress.override_policy("always"):
        _follow(server, "main")
        _git(server, "pull", "-q")
        _check()
        up = asyncio.run(update_notice.updates_state())
        assert up["relation"] == "ahead" and up["changes_since"] == 1
        assert up["latest_release"]["highlights"] == HL
        # A stable house: no fallback.
        _follow(server, "stable")
        up = asyncio.run(update_notice.updates_state())
        assert up["relation"] == "same" and up["latest_release"]["highlights"] == []


def test_main_commits_never_make_a_notice(world):
    work, server, _, _ = world
    _follow(server, "main")
    _commit(work, "main moves on")
    _commit(work, "and on")
    with egress.override_policy("always"):
        res = _check()
        assert res["behind"] == 2
        assert res["updates"]["notice"]["available"] is False
        assert res["updates"]["relation"] == "same"
