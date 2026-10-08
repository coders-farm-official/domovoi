"""Signed updates, the core's half (docs/LINUX_HOST.md, "Signed updates").

`git_version.pull()` fetches, verifies the fetched tip against the
root-owned allowed-signers file, and only then fast-forwards to that exact
SHA. While the file exists a tip that does not verify is refused and the
tree does not move; without it the pull goes ahead under a loud warning, so
an install that has not set signing up keeps updating. `version_state()`
reports the checkout's state as `signature` for the dashboard's badge.

git is a fake here: every call `_run` would make is answered by a script of
what a real git says for a signed, unsigned or wrongly-signed commit (the
real answers were taken on a scratch repo: %G? is N, G or U; verify-commit
exits 0 only for G; a commit's `gpgsig` header is the only trace of a
signature when no signers file is configured). The bash harness
(scripts/linux/tests/test-apply-update.sh) exercises real signatures.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from domovoi import git_version, self_restart
from domovoi.config import settings

UPSTREAM = "b" * 40
HEAD = "a" * 40


class FakeGit:
    """Answers `_run(*args)`; `calls` keeps every argv (pins included)."""

    def __init__(self, *, verdict_rc=1, mark="N", signer="", key="",
                 verify_stderr="", signed_header=False, merge_rc=0,
                 upstream=UPSTREAM):
        self.verdict_rc = verdict_rc
        self.mark = mark
        self.signer = signer
        self.key = key
        self.verify_stderr = verify_stderr
        self.signed_header = signed_header
        self.merge_rc = merge_rc
        self.upstream = upstream
        self.calls: list[tuple[str, ...]] = []

    @staticmethod
    def _strip_pins(args):
        out, skip = [], False
        for a in args:
            if skip:
                skip = False
                continue
            if a == "-c":
                skip = True
                continue
            out.append(a)
        return out

    def pins(self, call):
        return [call[i + 1] for i, a in enumerate(call) if a == "-c"]

    def commands(self):
        return [self._strip_pins(c)[0] for c in self.calls]

    def __call__(self, *args):
        self.calls.append(args)
        cmd = self._strip_pins(args)
        done = lambda rc=0, out="", err="": subprocess.CompletedProcess(  # noqa: E731
            ["git", *args], rc, stdout=out, stderr=err)
        match cmd[0]:
            case "fetch":
                return done()
            case "rev-parse":
                if "--short" in cmd:
                    return done(out=HEAD[:7] + "\n")
                if cmd[-1].startswith("@{u}"):
                    return done(out=self.upstream + "\n") if self.upstream else done(rc=128, err="fatal: no upstream")
                return done(out=HEAD + "\n")
            case "status":
                return done()
            case "verify-commit":
                return done(rc=self.verdict_rc, err=self.verify_stderr)
            case "log":
                return done(out=f"{self.mark}\n{self.signer}\n{self.key}\n")
            case "cat-file":
                body = "tree 1234\nparent 5678\n"
                if self.signed_header:
                    body += "gpgsig -----BEGIN SSH SIGNATURE-----\n U1NIU0lH...\n -----END SSH SIGNATURE-----\n"
                body += "author t <t@example.invalid> 1 +0000\n\nmsg\n"
                return done(out=body)
            case "merge":
                return done(rc=self.merge_rc, err="" if self.merge_rc == 0 else "fatal: Not possible to fast-forward, aborting.")
        return done(rc=1, err=f"unexpected git call: {cmd}")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """No boot state, no cache, no host env file, and the pre-pull record
    goes under tmp."""
    monkeypatch.setattr(git_version, "_BOOT_SHA", None, raising=False)
    monkeypatch.setattr(git_version, "_SIG_CACHE", None, raising=False)
    monkeypatch.setattr(git_version, "_SHA_CACHE", None, raising=False)
    monkeypatch.setenv("DOMOVOI_UPDATE_DEFAULTS_FILE", str(tmp_path / "no-defaults-file"))
    monkeypatch.delenv("DOMOVOI_ALLOWED_SIGNERS", raising=False)
    monkeypatch.setattr(settings, "update_state_dir", str(tmp_path / "update"))
    monkeypatch.setattr(git_version.egress, "internet_turned_off", lambda: False)


@pytest.fixture
def signers(monkeypatch, tmp_path):
    """An allowed-signers file in place: enforcement on."""
    path = tmp_path / "allowed_signers"
    path.write_text('owner@example.invalid namespaces="git" ssh-ed25519 AAAA\n')
    monkeypatch.setenv("DOMOVOI_ALLOWED_SIGNERS", str(path))
    return path


@pytest.fixture
def no_signers(monkeypatch, tmp_path):
    """No allowed-signers file: enforcement off."""
    path = tmp_path / "allowed_signers"
    monkeypatch.setenv("DOMOVOI_ALLOWED_SIGNERS", str(path))
    return path


def _install(monkeypatch, fake: FakeGit) -> FakeGit:
    monkeypatch.setattr(git_version, "_run", fake)
    return fake


GOOD = dict(verdict_rc=0, mark="G", signer="owner@example.invalid", key="SHA256:abc")
WRONG_SIGNER = dict(verdict_rc=1, mark="U", key="SHA256:zzz",
                    verify_stderr='Good "git" signature with ED25519 key SHA256:zzz\nNo principal matched.\n')
UNSIGNED = dict(verdict_rc=1, mark="N")


# ─── pull(): the gate ─────────────────────────────────────────────────────


async def test_a_verified_tip_is_fast_forwarded_to_by_its_sha(monkeypatch, signers):
    git = _install(monkeypatch, FakeGit(**GOOD))

    res = await git_version.pull()

    assert res["pulled"] is True
    assert res["signature"]["status"] == "verified"
    assert res["signature"]["signer"] == "owner@example.invalid"
    assert res["signature"]["enforced"] is True
    assert git.commands().index("fetch") < git.commands().index("verify-commit") < git.commands().index("merge")
    merge = [c for c in git.calls if "merge" in c][0]
    assert merge[-3:] == ("merge", "--ff-only", UPSTREAM), "the verified SHA, not @{u} again"


async def test_an_unsigned_tip_is_refused_before_the_tree_moves(monkeypatch, signers):
    git = _install(monkeypatch, FakeGit(**UNSIGNED))

    res = await git_version.pull()

    assert res["pulled"] is False
    assert "merge" not in git.commands(), "the tree must not move"
    assert "pull" not in git.commands()
    assert res["signature"]["status"] == "unsigned"
    assert res["signature"]["enforced"] is True
    assert "not signed" in res["error"] and str(signers) in res["error"]
    assert not (git_version.Path(settings.update_state_dir) / git_version.PREV_SHA_FILE).exists()


async def test_a_tip_signed_by_a_stranger_is_refused(monkeypatch, signers):
    git = _install(monkeypatch, FakeGit(**WRONG_SIGNER))

    res = await git_version.pull()

    assert res["pulled"] is False
    assert "merge" not in git.commands()
    assert res["signature"]["status"] == "unverified"
    assert "No principal matched" in res["signature"]["detail"]
    assert "refused" in res["error"]


async def test_a_signers_file_that_appears_later_is_honoured(monkeypatch, no_signers):
    """Enforcement is the file's presence, read on every pull."""
    git = _install(monkeypatch, FakeGit(**UNSIGNED))
    assert (await git_version.pull())["pulled"] is True
    no_signers.write_text("owner namespaces=\"git\" ssh-ed25519 AAAA\n")

    res = await git_version.pull()

    assert res["pulled"] is False
    assert git.commands().count("merge") == 1


# ─── pull(): without the file, warn and go on ─────────────────────────────


async def test_without_a_signers_file_an_unsigned_tip_pulls_with_a_loud_warning(monkeypatch, no_signers, caplog):
    git = _install(monkeypatch, FakeGit(**UNSIGNED))

    with caplog.at_level("WARNING", logger="domovoi.git_version"):
        res = await git_version.pull()

    assert res["pulled"] is True
    assert "merge" in git.commands()
    assert "verify-commit" not in git.commands(), "nothing to verify against"
    assert res["signature"] == {
        "status": "unsigned", "signer": None, "key": None, "enforced": False,
        "allowed_signers": str(no_signers),
        "detail": f"not signed, and there is no {no_signers}",
    }
    warning = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warning and "UNSIGNED UPDATE" in warning[0].getMessage()
    assert str(no_signers) in warning[0].getMessage()


async def test_without_a_signers_file_a_signed_tip_is_unverified(monkeypatch, no_signers):
    _install(monkeypatch, FakeGit(**UNSIGNED, signed_header=True))

    res = await git_version.pull()

    assert res["pulled"] is True
    assert res["signature"]["status"] == "unverified"
    assert "no " + str(no_signers) in res["signature"]["detail"]


async def test_a_failed_fast_forward_still_reports_the_verdict(monkeypatch, signers):
    _install(monkeypatch, FakeGit(**GOOD, merge_rc=1))

    res = await git_version.pull()

    assert res["pulled"] is False
    assert "fast-forward" in res["error"]
    assert res["signature"]["status"] == "verified"


async def test_no_upstream_is_an_error_not_a_crash(monkeypatch, signers):
    git = _install(monkeypatch, FakeGit(**GOOD, upstream=None))

    res = await git_version.pull()

    assert res["pulled"] is False
    assert "upstream" in res["error"]
    assert "merge" not in git.commands() and "verify-commit" not in git.commands()


# ─── the verdict is bound to the root-owned file ──────────────────────────


def test_every_verifying_call_pins_the_signers_file_and_the_programs(monkeypatch, signers):
    git = _install(monkeypatch, FakeGit(**GOOD))

    git_version.verify_commit(UPSTREAM)

    verifying = [c for c in git.calls if "verify-commit" in c or "log" in c]
    assert len(verifying) == 2
    for call in verifying:
        pins = git.pins(call)
        assert f"gpg.ssh.allowedSignersFile={signers}" in pins
        assert "gpg.ssh.program=ssh-keygen" in pins
        assert "gpg.program=gpg" in pins


def test_the_signers_path_comes_from_the_root_env_file(monkeypatch, tmp_path):
    defaults = tmp_path / "domovoi-update"
    defaults.write_text(
        "# DOMOVOI_ALLOWED_SIGNERS=/commented/out\n"
        "DOMOVOI_UPDATE_KEEP_BACKUPS=5\n"
        'DOMOVOI_ALLOWED_SIGNERS="/etc/domovoi/keys/allowed_signers"\n'
    )
    monkeypatch.setenv("DOMOVOI_UPDATE_DEFAULTS_FILE", str(defaults))
    monkeypatch.delenv("DOMOVOI_ALLOWED_SIGNERS", raising=False)

    assert git_version.allowed_signers_path() == "/etc/domovoi/keys/allowed_signers"

    monkeypatch.setenv("DOMOVOI_ALLOWED_SIGNERS", "/elsewhere/allowed")
    assert git_version.allowed_signers_path() == "/elsewhere/allowed", "the environment wins"


def test_the_default_path_is_the_documented_one(monkeypatch, tmp_path):
    monkeypatch.setenv("DOMOVOI_UPDATE_DEFAULTS_FILE", str(tmp_path / "missing"))
    assert git_version.allowed_signers_path() == "/etc/domovoi/allowed_signers"
    assert git_version.signing_enforced() is False


# ─── version_state(): the badge ───────────────────────────────────────────


@pytest.fixture
def stub_probes(monkeypatch):
    async def sha():
        return HEAD[:7] + "-dirty"

    async def cap():
        return (False, "test")

    monkeypatch.setattr(git_version, "current_sha", sha)
    monkeypatch.setattr(self_restart, "capable_async", cap)
    monkeypatch.setattr(self_restart, "restart_mode", lambda: "restart")


async def test_version_state_reports_the_checkouts_signature(monkeypatch, signers, stub_probes):
    git = _install(monkeypatch, FakeGit(**GOOD))

    state = await git_version.version_state()

    sig = state["signature"]
    assert sig["status"] == "verified" and sig["enforced"] is True
    assert sig["signer"] == "owner@example.invalid" and sig["key"] == "SHA256:abc"
    assert sig["allowed_signers"] == str(signers)
    verified = [c for c in git.calls if "verify-commit" in c][0]
    assert verified[-1] == HEAD[:7], "-dirty stripped before git sees it"


async def test_the_verdict_is_cached_per_head_and_dropped_by_a_pull(monkeypatch, signers, stub_probes):
    git = _install(monkeypatch, FakeGit(**GOOD))

    await git_version.version_state()
    await git_version.version_state()
    assert git.commands().count("verify-commit") == 1, "the panel polls; ssh-keygen per poll is waste"

    git_version.invalidate_signature_cache()
    await git_version.version_state()
    assert git.commands().count("verify-commit") == 2


async def test_an_unknown_checkout_reports_unknown_without_git(monkeypatch, signers):
    git = _install(monkeypatch, FakeGit(**GOOD))

    async def sha():
        return "unknown"

    async def cap():
        return (False, "test")

    monkeypatch.setattr(git_version, "current_sha", sha)
    monkeypatch.setattr(self_restart, "capable_async", cap)
    monkeypatch.setattr(self_restart, "restart_mode", lambda: "restart")

    state = await git_version.version_state()

    assert state["signature"]["status"] == "unknown"
    assert state["signature"]["enforced"] is True
    assert git.calls == []


def test_a_git_failure_is_unknown_never_an_exception(monkeypatch, signers):
    def boom(*args):
        raise FileNotFoundError("git")

    monkeypatch.setattr(git_version, "_run", boom)

    out = git_version.verify_commit(UPSTREAM)

    assert out["status"] == "unknown"
    assert "FileNotFoundError" in out["detail"]


# ─── the update unit's own verdict comes through last_update ──────────────


def test_last_update_passes_the_scripts_signature_through(monkeypatch, tmp_path):
    result = tmp_path / "last-result.json"
    monkeypatch.setattr(settings, "update_result_file", str(result))
    result.write_text(json.dumps({
        "status": "refused",
        "error": "HEAD is not signed; signed updates are enforced",
        "signature": {"status": "unsigned", "signer": None, "enforced": True,
                      "allowed_signers": "/etc/domovoi/allowed_signers", "private": "x"},
        "steps": [{"name": "signature", "status": "refused", "duration_sec": 0.1, "detail": "secret"}],
    }))

    last = git_version.read_last_update()

    assert last["signature"] == {"status": "unsigned", "signer": None, "enforced": True}
    assert last["steps"] == [{"name": "signature", "status": "refused", "duration_sec": 0.1}]


@pytest.mark.parametrize("junk", ["verified", 7, {"status": "nope"}, {"signer": "x"}, None])
def test_a_garbage_signature_in_the_result_is_null(monkeypatch, tmp_path, junk):
    result = tmp_path / "last-result.json"
    monkeypatch.setattr(settings, "update_result_file", str(result))
    result.write_text(json.dumps({"status": "ok", "signature": junk, "steps": []}))

    assert git_version.read_last_update()["signature"] is None
