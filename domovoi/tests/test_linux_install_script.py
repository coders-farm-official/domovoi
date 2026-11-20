"""scripts/linux/install.sh, the one-command Linux install
(curl ... | sudo bash).

Each case runs the real script under bash (Git for Windows' bash on
Windows, as test_provision_script.py does) in its test harness mode: a
throwaway origin repository whose scripts/linux/provision.sh is a stub
that records how it was called, a folder of release files signed with a
throwaway key, a fake /etc (os-release, timezone, zoneinfo) and an answers
file standing in for /dev/tty. Nothing is installed and nothing leaves the
machine.

What they hold the script to: the questions and the no-terminal error,
the pass-through of provision.sh's options, the channel it picks (stable,
main before the first release, --channel main), and that provision.sh
never runs unless the download is the commit GitHub names and, on stable,
a release signed with the release key.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from domovoi.tests.test_apply_update_script import BASH, requires_bash

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "linux" / "install.sh"
PROVISION = REPO_ROOT / "scripts" / "linux" / "provision.sh"
RELEASE_PUB = REPO_ROOT / "windows" / "installer" / "release-signing.pub"
LINUX_HOST = REPO_ROOT / "docs" / "LINUX_HOST.md"
README = REPO_ROOT / "README.md"

ONE_LINER = (
    "curl -fsSL https://raw.githubusercontent.com/coders-farm-official/domovoi/stable/"
    "scripts/linux/install.sh | sudo bash"
)

STUB = """#!/usr/bin/env bash
out=${STUB_OUT:?}
printf '%s\\n' "$@" >"$out/args"
cat >"$out/stdin"
echo '@@DOMOVOI {"v":1,"step":"finish"}'
echo "provision: stub ran"
exit "${STUB_RC:-0}"
"""


# ─── Static checks ───────────────────────────────────────────────────────


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_script_is_lf_only():
    assert b"\r\n" not in SCRIPT.read_bytes()


@requires_bash
def test_script_parses():
    proc = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_release_key_is_the_pinned_one():
    """The key the script checks releases with is the one the Windows
    programs pin and the release workflow signs with."""
    m = re.search(r"^RELEASE_KEY='([^']+)'$", _text(), re.M)
    assert m, "RELEASE_KEY not found"
    pinned = " ".join(RELEASE_PUB.read_text(encoding="utf-8").split()[:2])
    assert m.group(1) == pinned


def test_release_names_match_finish_release():
    text = (REPO_ROOT / "scripts" / "release" / "finish_release.py").read_text(encoding="utf-8")
    for var, py in (
        ("MANIFEST_NAMESPACE", "MANIFEST_NAMESPACE"),
        ("MANIFEST_KIND", "MANIFEST_KIND"),
        ("RELEASE_SIGNER", "SIGNER"),
    ):
        ours = re.search(rf"^{var}=(\S+)$", _text(), re.M).group(1)
        theirs = re.search(rf'^{py} = "([^"]+)"$', text, re.M).group(1)
        assert ours == theirs, var


def _provision_opts() -> tuple[set[str], set[str]]:
    text = PROVISION.read_text(encoding="utf-8")
    value = re.search(r"^    (--repo-url\|--ref\|[^)]*)\)$", text, re.M).group(1)
    flags = re.search(r"^    (--wsl\|--no-wsl\|[^)]*)\)$", text, re.M).group(1)
    return set(value.split("|")), set(flags.split("|")) - {"-h", "--help"}


def _list(name: str) -> set[str]:
    return set(re.search(rf'^{name}=" ([^"]*) "$', _text(), re.M).group(1).split())


def test_pass_through_lists_match_provision():
    """Every provision.sh option is either passed through or owned here,
    so a new one can't be refused (or eat its value) by mistake."""
    value, flags = _provision_opts()
    own = _list("OWN_OPTS")
    assert own == {"--repo-url", "--ref", "--track-branch"}
    # --internet and --timezone are this script's questions, passed on as answers.
    assert _list("PASS_VALUE_OPTS") == value - own - {"--internet", "--timezone"}
    assert _list("PASS_FLAG_OPTS") == flags


def test_provision_runs_only_after_the_checks():
    text = _text()
    main = re.search(r"^main\(\) \{\n(.*?)^\}$", text, re.M | re.S).group(1)
    order = [line.strip() for line in main.splitlines()]
    assert order.index("fetch_and_verify") < order.index("run_provision")
    # The one place provision.sh is started, and only from the checked copy.
    starts = re.findall(r'bash "\$PROVISION"', text)
    assert len(starts) == 2  # with and without a terminal, both in run_provision
    run = re.search(r"^run_provision\(\) \{\n(.*?)^\}$", text, re.M | re.S).group(1)
    assert len(re.findall(r'bash "\$PROVISION"', run)) == 2
    # The last line runs main: a download cut short runs nothing.
    assert text.rstrip("\n").splitlines()[-1] == 'main "$@"'


def test_nothing_remote_is_piped_into_a_shell():
    """The one-liner appears in messages only; in the code, every download
    goes to a file, and nothing is piped into a shell, eval'd or sourced."""
    code = re.sub(r"<<'EOF'\n.*?\nEOF\n", "\n", _text(), flags=re.S)
    code = "\n".join(line for line in code.splitlines() if not line.lstrip().startswith("#"))
    code = re.sub(r"'[^'\n]*'|\"(?:[^\"\\\n]|\\.)*\"", "''", code)
    assert not re.search(r"\|\s*(sudo\s+)?(ba)?sh\b", code)
    assert not re.search(r"\beval\b|\bsource\b|^\s*\.\s", code, re.M)
    curls = re.findall(r"\bcurl -[^\n]*", code)
    assert len(curls) == 2 and all(" -o " in c for c in curls), curls


def test_docs_lead_with_the_one_liner():
    doc = LINUX_HOST.read_text(encoding="utf-8")
    install = doc[doc.index("\n## Install\n"):]
    install = install[: install.index("\n### By hand\n")]
    assert ONE_LINER in install
    assert install.index(ONE_LINER) < install.index("scripts/linux/provision.sh --dry-run")
    assert "curl -fsSL -o install.sh" in install
    readme = README.read_text(encoding="utf-8")
    assert ONE_LINER in readme
    # Owner 2026-11-20: one Install section, the Linux command beside
    # straight downloads of the Windows and Mac installers from the latest
    # release (asset names without a version, so the links never go stale).
    install = readme[readme.index("\n## Install\n"):]
    install = install[: install.index("\n## ", 1)]
    assert ONE_LINER in install
    latest = "https://github.com/coders-farm-official/domovoi/releases/latest/download/"
    assert latest + "DomovoiSetup.exe" in install
    assert latest + "DomovoiSetup-mac.pkg" in install
    assert "Open Anyway" in install


# ─── Running it ──────────────────────────────────────────────────────────


def _bash(cmd: str, cwd: Path, env: dict | None = None) -> str:
    proc = subprocess.run(
        [BASH, "-c", cmd], cwd=cwd, capture_output=True, text=True, env=env or os.environ.copy()
    )
    assert proc.returncode == 0, cmd + "\n" + proc.stdout + proc.stderr
    return proc.stdout


GIT = "git -c commit.gpgsign=false -c tag.gpgsign=false -c init.defaultBranch=main"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "alex",
    "GIT_AUTHOR_EMAIL": "alex@example.com",
    "GIT_COMMITTER_NAME": "alex",
    "GIT_COMMITTER_EMAIL": "alex@example.com",
}


class Lab:
    """An origin repository, its releases and a fake system root."""

    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.env = {**os.environ, **GIT_ENV}
        self.origin = tmp / "origin"
        self.releases = tmp / "releases"
        self.root = tmp / "root"
        self.out = tmp / "out"
        self.out.mkdir()
        (self.root / "etc").mkdir(parents=True)
        self.os_release("26.04")
        (self.root / "etc" / "timezone").write_text("America/New_York\n", encoding="utf-8")
        for z in ("America/New_York", "Europe/Berlin", "Etc/UTC"):
            f = self.root / "usr" / "share" / "zoneinfo" / z
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"TZif")
        self.origin.mkdir()
        stub = self.origin / "scripts" / "linux" / "provision.sh"
        stub.parent.mkdir(parents=True)
        stub.write_bytes(STUB.encode())
        _bash(f"{GIT} init -q . && {GIT} add -A && {GIT} commit -qm release", self.origin, self.env)
        self.release_sha = self.sha("HEAD")
        _bash(f"{GIT} branch stable && {GIT} tag v9.9.9", self.origin, self.env)
        (self.origin / "NEWS").write_text("newer\n", encoding="utf-8")
        _bash(f"{GIT} add -A && {GIT} commit -qm newer", self.origin, self.env)
        self.main_sha = self.sha("main")
        _bash("ssh-keygen -q -t ed25519 -N '' -C test -f key && ssh-keygen -q -t ed25519 -N '' -C other -f other",
              tmp, self.env)
        self.pub = " ".join((tmp / "key.pub").read_text(encoding="utf-8").split()[:2])
        self.publish("v9.9.9", self.release_sha)

    def sha(self, ref: str) -> str:
        return _bash(f"git rev-parse {ref}", self.origin, self.env).strip()

    def os_release(self, version: str, ident: str = "ubuntu", **extra: str):
        lines = f'ID={ident}\nVERSION_ID="{version}"\nPRETTY_NAME="{ident} {version}"\n'
        for k, v in extra.items():
            lines += f'{k.upper()}="{v}"\n'
        (self.root / "etc" / "os-release").write_text(lines, encoding="utf-8")

    def publish(self, tag: str, commit: str, key: str = "key", kind: str = "domovoi-windows-setup",
                manifest_tag: str | None = None):
        d = self.releases / tag
        d.mkdir(parents=True, exist_ok=True)
        (d / "DomovoiSetup.manifest.json").write_bytes(
            (
                "{\n"
                f'  "commit": "{commit}",\n'
                '  "file": "DomovoiSetup.exe",\n'
                f'  "kind": "{kind}",\n'
                '  "schema": 1,\n'
                f'  "tag": "{manifest_tag or tag}",\n'
                '  "version": "9.9.9"\n'
                "}\n"
            ).encode()
        )
        sig = d / "DomovoiSetup.manifest.json.sig"
        if sig.exists():
            sig.unlink()
        _bash(f"ssh-keygen -q -Y sign -f '{(self.tmp / key).as_posix()}' -n domovoi-windows-setup-manifest "
              "DomovoiSetup.manifest.json", d, self.env)

    def run(self, *args: str, answers: str | None = None, extra: dict | None = None):
        tty = "none"
        if answers is not None:
            f = self.tmp / "answers"
            f.write_text(answers, encoding="utf-8")
            tty = f.as_posix()
        for name in ("args", "stdin"):
            (self.out / name).unlink(missing_ok=True)
        env = {
            **self.env,
            "DOMOVOI_INSTALL_TEST_HARNESS": "1",
            "DOMOVOI_INSTALL_REPO_URL": self.origin.as_uri(),
            "DOMOVOI_INSTALL_RELEASES_URL": self.releases.as_uri(),
            "DOMOVOI_INSTALL_RELEASE_KEY": self.pub,
            "DOMOVOI_INSTALL_TTY": tty,
            "DOMOVOI_INSTALL_ROOT": self.root.as_posix(),
            "STUB_OUT": self.out.as_posix(),
            "TMPDIR": self.tmp.as_posix(),
            **(extra or {}),
        }
        proc = subprocess.run(
            [BASH, SCRIPT.as_posix(), *args], capture_output=True, text=True, env=env, timeout=120,
            stdin=subprocess.DEVNULL,
        )
        return proc

    @property
    def ran(self) -> bool:
        return (self.out / "args").exists()

    @property
    def args(self) -> list[str]:
        return (self.out / "args").read_text(encoding="utf-8").splitlines()

    @property
    def stdin(self) -> str:
        return (self.out / "stdin").read_text(encoding="utf-8")


@pytest.fixture
def lab(tmp_path):
    if BASH is None:
        pytest.skip("no usable bash (Git Bash on Windows)")
    probe = subprocess.run([BASH, "-c", "command -v ssh-keygen && command -v curl && command -v git"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("needs ssh-keygen, curl and git in bash")
    return Lab(tmp_path)


def _out(proc) -> str:
    return proc.stdout + proc.stderr


def test_unattended_passes_answers_and_options_through(lab):
    proc = lab.run("--internet", "never", "--timezone", "Europe/Berlin", "--yes",
                   "--no-ai-models", "--media", "music=/srv/music", "--user=alex")
    assert proc.returncode == 0, _out(proc)
    assert lab.args == [
        "--ref", lab.release_sha, "--track-branch", "stable",
        "--internet", "never", "--timezone", "Europe/Berlin",
        "--no-ai-models", "--media", "music=/srv/music", "--user=alex",
    ]
    # No terminal handed on: provision.sh asks nothing.
    assert lab.stdin == ""
    out = _out(proc)
    assert "provision: stub ran" in out
    assert "@@DOMOVOI" not in out
    assert "Checked: v9.9.9" in out
    assert ":6369" in out
    assert "/home/alex/.domovoi/setup-code.txt" in out or "alex/.domovoi/setup-code.txt" in out


def test_answers_given_without_a_terminal_need_no_yes(lab):
    proc = lab.run("--internet=sometimes")
    assert proc.returncode == 0, _out(proc)
    # The time zone defaults to this computer's.
    assert lab.args[:8] == ["--ref", lab.release_sha, "--track-branch", "stable",
                            "--internet", "sometimes", "--timezone", "America/New_York"]


def test_no_terminal_and_no_answers_is_a_clear_error(lab):
    proc = lab.run()
    assert proc.returncode == 2
    assert "no terminal to ask the questions at" in proc.stderr
    assert "--internet always --timezone America/New_York --yes" in proc.stderr
    assert not lab.ran


def test_yes_needs_internet(lab):
    proc = lab.run("--yes", answers="1\n")
    assert proc.returncode == 2
    assert "--yes asks nothing" in proc.stderr
    assert not lab.ran


@pytest.mark.parametrize("opt", ["--ref", "--track-branch", "--repo-url"])
def test_the_commit_is_never_chosen_by_an_option(lab, opt):
    proc = lab.run("--internet", "never", "--yes", opt, "main")
    assert proc.returncode == 2
    assert "picks the release itself" in proc.stderr
    assert not lab.ran


def test_unknown_options_and_bad_answers_are_refused(lab):
    for args in (["--internet", "maybe"], ["--timezone", "Not a zone"], ["--channel", "beta"],
                 ["--frobnicate"], ["--dry-run=1"]):
        proc = lab.run(*args)
        assert proc.returncode == 2, args
        assert not lab.ran


def test_interactive_questions_and_the_terminal_for_provision(lab):
    # A wrong answer is asked again; Enter takes the detected zone; then the
    # summary's Start? and, left for provision.sh, its AI question's answer.
    proc = lab.run(answers="5\n2\nMars/Base\n\n\nAI-ANSWER\n")
    assert proc.returncode == 0, _out(proc)
    err = proc.stderr
    assert "Will this server have internet after setup?" in err
    assert "Please answer 1, 2 or 3." in err
    assert "Time zone [America/New_York]:" in err
    assert "isn't a time zone this computer knows" in err
    assert "Start now? [Y/n]" in err
    assert lab.args[4:8] == ["--internet", "sometimes", "--timezone", "America/New_York"]
    # provision.sh reads the same terminal, so it can ask its own question.
    assert lab.stdin == "AI-ANSWER\n"
    assert "Ready to install Domovoi" in proc.stdout


def test_interactive_no_at_start_runs_nothing(lab):
    proc = lab.run(answers="1\nEurope/Berlin\nn\n")
    assert proc.returncode == 1
    assert "nothing was changed" in proc.stderr
    assert not lab.ran


def test_stable_is_the_latest_release_not_main(lab):
    assert lab.release_sha != lab.main_sha
    proc = lab.run("--internet", "always", "--yes")
    assert proc.returncode == 0, _out(proc)
    assert lab.args[:4] == ["--ref", lab.release_sha, "--track-branch", "stable"]


def test_before_the_first_release_it_installs_main_following_stable(lab):
    _bash(f"{GIT} branch -D stable", lab.origin, lab.env)
    proc = lab.run("--internet", "always", "--yes")
    assert proc.returncode == 0, _out(proc)
    # provision.sh follows main until stable exists, and records stable as
    # the channel asked for (its own fallback, with its own warning).
    assert lab.args[:4] == ["--ref", lab.main_sha, "--track-branch", "stable"]
    assert "no release has been published yet" in proc.stderr
    assert "no release signature to check" in proc.stderr


def test_channel_main(lab):
    proc = lab.run("--internet", "always", "--yes", "--channel", "main")
    assert proc.returncode == 0, _out(proc)
    assert lab.args[:4] == ["--ref", lab.main_sha, "--track-branch", "main"]
    assert "no release signature to check" in proc.stderr


def test_a_release_signed_with_another_key_is_not_run(lab):
    lab.publish("v9.9.9", lab.release_sha, key="other")
    proc = lab.run("--internet", "always", "--yes")
    assert proc.returncode == 1
    assert "not signed with Domovoi's release key" in proc.stderr
    assert "no signed release names commit" in proc.stderr
    assert not lab.ran


def test_a_signed_manifest_for_another_commit_is_not_run(lab):
    lab.publish("v9.9.9", lab.main_sha)
    proc = lab.run("--internet", "always", "--yes")
    assert proc.returncode == 1
    assert "names another release or commit" in proc.stderr
    assert not lab.ran


def test_a_signed_manifest_for_another_tag_or_kind_is_not_run(lab):
    lab.publish("v9.9.9", lab.release_sha, manifest_tag="v9.9.8")
    assert lab.run("--internet", "always", "--yes").returncode == 1
    assert not lab.ran
    lab.publish("v9.9.9", lab.release_sha, kind="something-else")
    assert lab.run("--internet", "always", "--yes").returncode == 1
    assert not lab.ran


def test_a_release_without_its_manifest_is_not_run(lab):
    for f in (lab.releases / "v9.9.9").iterdir():
        f.unlink()
    proc = lab.run("--internet", "always", "--yes")
    assert proc.returncode == 1
    assert "can't download its signed manifest" in proc.stderr
    assert not lab.ran


def test_stable_without_a_release_tag_is_not_run(lab):
    _bash(f"{GIT} tag -d v9.9.9", lab.origin, lab.env)
    proc = lab.run("--internet", "always", "--yes")
    assert proc.returncode == 1
    assert "which no release names" in proc.stderr
    assert not lab.ran


def test_an_annotated_release_tag_is_found(lab):
    _bash(f"{GIT} tag -d v9.9.9 && {GIT} tag -a -m rel v9.9.9 stable", lab.origin, lab.env)
    proc = lab.run("--internet", "always", "--yes")
    assert proc.returncode == 0, _out(proc)
    assert lab.args[1] == lab.release_sha


def test_provision_failing_is_reported_with_its_exit_code(lab):
    proc = lab.run("--internet", "always", "--yes", extra={"STUB_RC": "3"})
    assert proc.returncode == 3
    assert "The install stopped (exit 3)" in proc.stdout
    assert "run" in proc.stdout and "the same command again" in proc.stdout


def test_dry_run_passes_through(lab):
    proc = lab.run("--internet", "always", "--yes", "--dry-run")
    assert proc.returncode == 0, _out(proc)
    assert lab.args[-1] == "--dry-run"
    assert "Dry run finished: nothing was changed." in proc.stdout
    assert ":6369" not in proc.stdout


def test_other_systems(lab):
    lab.os_release("12", ident="debian", version_codename="bookworm")
    proc = lab.run("--internet", "always", "--yes")
    assert proc.returncode == 1
    assert "Debian 13 (trixie) only" in proc.stderr and "#which-distro" in proc.stderr
    assert not lab.ran
    # Another Ubuntu: a clear warning, then on (asked at a terminal).
    lab.os_release("24.04")
    proc = lab.run("--internet", "always", "--yes")
    assert proc.returncode == 0, _out(proc)
    assert "made for Ubuntu 26.04" in proc.stderr
    proc = lab.run(answers="n\n")
    assert proc.returncode == 1
    assert "Continue anyway? [y/N]" in proc.stderr
    assert not lab.ran


# The systems provision.sh installs on (owner decision 2026-11-15): no
# warning, no question.
@pytest.mark.parametrize("version, ident, extra", [
    ("26.04", "ubuntu", {"version_codename": "resolute", "ubuntu_codename": "resolute"}),
    ("13", "debian", {"version_codename": "trixie"}),
    ("22.1", "linuxmint", {"id_like": "ubuntu debian", "ubuntu_codename": "noble", "version_codename": "xia"}),
    ("22", "linuxmint", {"id_like": "ubuntu debian", "ubuntu_codename": "noble", "version_codename": "wilma"}),
    ("24.04", "pop", {"id_like": "ubuntu debian", "ubuntu_codename": "noble", "version_codename": "noble"}),
])
def test_supported_systems_go_on_without_a_word(lab, version, ident, extra):
    lab.os_release(version, ident=ident, **extra)
    proc = lab.run("--internet", "always", "--yes")
    assert proc.returncode == 0, _out(proc)
    assert "WARNING" not in proc.stderr, proc.stderr
    assert lab.ran


def test_an_untried_ubuntu_derivative_is_warned_about(lab):
    lab.os_release("8", ident="elementary", id_like="ubuntu", ubuntu_codename="noble")
    proc = lab.run("--internet", "always", "--yes")
    assert proc.returncode == 0, _out(proc)
    assert "has not been tried on" in proc.stderr and "as Ubuntu noble" in proc.stderr
    proc = lab.run(answers="n\n")
    assert proc.returncode == 1 and not lab.ran


@pytest.mark.parametrize("version, ident, extra, says", [
    # A derivative of an Ubuntu Domovoi has no Docker pins for.
    ("17", "zorin", {"id_like": "ubuntu", "ubuntu_codename": "jammy"}, "on Ubuntu jammy"),
    # One that names no Ubuntu release.
    ("1", "odd", {"id_like": "ubuntu"}, "no UBUNTU_CODENAME"),
    # Not supported yet: the issue tracker is where to ask.
    ("43", "fedora", {}, "/issues"),
    ("", "arch", {}, "/issues"),
    ("15.6", "opensuse-leap", {"id_like": "suse opensuse"}, "Fedora, Arch and others"),
    # Debian's derivatives (LMDE) are not Ubuntu's.
    ("7", "linuxmint", {"id_like": "debian", "debian_codename": "trixie"}, "/issues"),
])
def test_unsupported_systems_stop_before_any_download(lab, version, ident, extra, says):
    lab.os_release(version, ident=ident, **extra)
    proc = lab.run("--internet", "always", "--yes")
    assert proc.returncode == 1, _out(proc)
    assert says in proc.stderr, proc.stderr
    assert "Looking up the latest Domovoi release" not in proc.stdout
    assert not lab.ran


def test_harness_variables_are_refused_outside_the_harness(lab):
    env = {k: v for k, v in os.environ.items() if not k.startswith("DOMOVOI_INSTALL_")}
    env["DOMOVOI_INSTALL_REPO_URL"] = "file:///elsewhere"
    proc = subprocess.run([BASH, SCRIPT.as_posix(), "--internet", "never", "--yes"],
                          capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
    assert proc.returncode == 1
    assert "for this script's tests only" in proc.stderr
